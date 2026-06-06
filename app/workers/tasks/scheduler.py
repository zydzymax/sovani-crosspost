"""
Scheduled posts processor for SalesWhisper Crosspost.
"""

import asyncio
import os
import tempfile
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ...core.config import settings
from ...core.logging import get_logger
from ...core.security import decrypt_data
from ...models.entities import SocialAccount, UserSocialAccount
from ..celery_app import celery

logger = get_logger("tasks.scheduler")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv")


def get_async_session():
    """Create new async session with new engine for each task."""
    db_url = settings.get_database_url(async_driver=True)
    engine = create_async_engine(db_url, pool_pre_ping=True, pool_size=5)
    return async_sessionmaker(engine, expire_on_commit=False)()


def _normalize_platform(value: Any) -> str:
    if value is None:
        return ""
    text_value = str(value).strip().lower()
    if "." in text_value:
        text_value = text_value.split(".")[-1]
    return text_value


def _decrypt_token(value: str | None) -> str:
    if not value:
        return ""
    try:
        return decrypt_data(value)
    except Exception:
        return value


def _resolve_instagram_page_id(account: SocialAccount) -> str:
    extra = account.extra_credentials if isinstance(account.extra_credentials, dict) else {}
    for key in ("page_id", "instagram_business_id", "ig_user_id", "business_account_id"):
        value = extra.get(key)
        if value:
            return str(value)
    return str(account.platform_user_id or "")


@celery.task(bind=True, name="app.workers.tasks.scheduler.process_scheduled_posts")
def process_scheduled_posts(self) -> dict[str, Any]:
    """Main scheduler task - runs every minute."""
    logger.info("Starting scheduled posts processing")

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_process_scheduled_posts_async())
        finally:
            loop.close()
        logger.info("Scheduled posts processing completed", result=result)
        return result
    except Exception as e:
        logger.exception("Scheduled posts processing failed")
        return {"success": False, "error": str(e)}


async def _process_scheduled_posts_async() -> dict[str, Any]:
    """Async implementation of scheduled posts processing."""
    processed = 0
    failed = 0

    async with get_async_session() as db:
        now = datetime.utcnow()

        # Use raw SQL due to model/table mismatch
        result = await db.execute(
            text(
                """
                SELECT id, original_text, generated_caption, hashtags, enrichment_data
                     , media_asset_ids, user_id
                FROM posts
                WHERE status = 'draft'
                AND is_scheduled = true
                AND scheduled_at <= :now
                LIMIT 50
            """
            ),
            {"now": now},
        )
        posts = result.fetchall()

        logger.info("Found posts due for processing", count=len(posts))

        for post in posts:
            post_id = post[0]
            try:
                success = await _process_single_post(db, post)
                if success:
                    processed += 1
                else:
                    failed += 1
            except Exception as e:
                logger.exception("Failed to process post %s", post_id)
                await db.execute(
                    text("UPDATE posts SET status = 'failed', error_message = :error WHERE id = :id"),
                    {"id": post_id, "error": str(e)[:500]},
                )
                failed += 1

        await db.commit()

    return {"success": True, "processed": processed, "failed": failed, "timestamp": datetime.utcnow().isoformat()}


async def _process_single_post(db, post_row) -> bool:
    """Process a single scheduled post."""
    from ...services.publishers.telegram import TelegramPublisher
    from ...services.publishers.vk import VKPublisher

    post_id = post_row[0]
    original_text = post_row[1]
    generated_caption = post_row[2]
    hashtags = post_row[3] or []
    enrichment_data = post_row[4] or {}
    media_asset_ids = post_row[5] or []
    owner_user_id = post_row[6]

    logger.info("Processing post %s", post_id)

    # Mark as publishing
    await db.execute(text("UPDATE posts SET status = 'publishing' WHERE id = :id"), {"id": post_id})
    await db.flush()

    # Get active accounts for this post owner only.
    stmt = (
        select(SocialAccount)
        .join(UserSocialAccount, UserSocialAccount.account_id == SocialAccount.id)
        .where(
            UserSocialAccount.user_id == owner_user_id,
            UserSocialAccount.can_publish.is_(True),
            SocialAccount.is_active.is_(True),
            SocialAccount.publish_enabled.is_(True),
        )
        .order_by(UserSocialAccount.is_primary.desc(), SocialAccount.publish_priority.desc(), SocialAccount.created_at.asc())
    )
    result = await db.execute(stmt)
    accounts = result.scalars().all()

    if not accounts:
        logger.warning("No connected accounts for post %s", post_id)
        await db.execute(
            text("UPDATE posts SET status = 'failed', error_message = 'No connected accounts' WHERE id = :id"),
            {"id": post_id},
        )
        return False

    # Collect media from DB/enrichment
    media_urls = await _collect_post_media_urls(db, media_asset_ids, enrichment_data)
    image_url = _pick_first_image(media_urls)

    # Generate image if we still have no media and there is a prompt
    image_prompt = enrichment_data.get("image_prompt") if isinstance(enrichment_data, dict) else None

    if image_prompt and not media_urls:
        try:
            from ...services.image_gen import ImageGenerationService

            image_service = ImageGenerationService()
            image_result = await image_service.generate(prompt=image_prompt, provider="openai")
            if image_result.success:
                image_url = image_result.image_url
                media_urls = [image_url]
                logger.info("Generated image for post %s", post_id)
        except Exception:
            logger.exception("Image generation failed for post %s", post_id)

    # Publish to one selected account per platform (highest priority first).
    selected_accounts_by_platform: dict[str, SocialAccount] = {}
    for account in accounts:
        platform_name = _normalize_platform(account.platform)
        if platform_name and platform_name not in selected_accounts_by_platform:
            selected_accounts_by_platform[platform_name] = account

    # Publish to each target platform
    publish_results = {}
    text_content = generated_caption or original_text or ""

    for platform_name, account in selected_accounts_by_platform.items():
        platform = platform_name.upper()

        try:
            if platform == "TELEGRAM":
                publisher = TelegramPublisher(account)
                result = await publisher.publish(
                    text=text_content,
                    image_url=image_url,
                    media_urls=media_urls,
                    hashtags=hashtags,
                )
                publish_results["telegram"] = {
                    "success": result.success,
                    "account_id": str(account.id),
                    "post_id": result.platform_post_id,
                    "url": result.platform_url,
                    "error": result.error,
                }

            elif platform == "VK":
                publisher = VKPublisher(account)
                result = await publisher.publish(text=text_content, image_url=image_url, hashtags=hashtags)
                publish_results["vk"] = {
                    "success": result.success,
                    "account_id": str(account.id),
                    "post_id": result.platform_post_id,
                    "url": result.platform_url,
                    "error": result.error,
                }

            elif platform == "INSTAGRAM":
                from ...adapters.instagram import PublishStatus, publish_instagram_post

                ig_media = media_urls[:] if media_urls else ([image_url] if image_url else [])
                if not ig_media:
                    raise ValueError("No media available for Instagram publishing")

                access_token = _decrypt_token(account.access_token)
                page_id = _resolve_instagram_page_id(account)
                if not access_token:
                    raise ValueError("Instagram account token missing")
                if not page_id:
                    raise ValueError("Instagram account page_id missing")

                result = await publish_instagram_post(
                    caption=text_content,
                    media_files=ig_media,
                    correlation_id=f"{post_id}:instagram",
                    access_token=access_token,
                    page_id=page_id,
                )
                success = result.status in {PublishStatus.FINISHED, PublishStatus.PENDING}
                publish_results["instagram"] = {
                    "success": success,
                    "account_id": str(account.id),
                    "post_id": result.post_id or result.container_id,
                    "url": result.permalink,
                    "error": None if success else result.message,
                }

            elif platform == "TIKTOK":
                from ...adapters.tiktok import PostStatus, publish_tiktok_video

                video_path, temp_file = await _resolve_local_video_path_async(media_urls)
                if not video_path:
                    raise ValueError("No video found for TikTok publishing")

                access_token = _decrypt_token(account.access_token)
                if not access_token:
                    raise ValueError("TikTok account token missing")

                try:
                    result = await publish_tiktok_video(
                        video_path=video_path,
                        title=(text_content or "SalesWhisper").strip()[:90],
                        description=(text_content or "").strip()[:2200],
                        tags=hashtags,
                        is_app_approved=os.getenv("TIKTOK_DIRECT_POST_ENABLED", "false").lower()
                        in {"1", "true", "yes", "on"},
                        correlation_id=f"{post_id}:tiktok",
                        access_token=access_token,
                    )
                    success = result.status in {PostStatus.PUBLISHED, PostStatus.PENDING, PostStatus.DRAFT}
                    publish_results["tiktok"] = {
                        "success": success,
                        "account_id": str(account.id),
                        "post_id": result.share_id,
                        "url": result.post_url,
                        "error": None if success else result.message,
                    }
                finally:
                    if temp_file and os.path.exists(temp_file):
                        try:
                            os.unlink(temp_file)
                        except Exception:
                            logger.warning("Failed to cleanup temp TikTok video", path=temp_file)

            elif platform == "YOUTUBE":
                from ...adapters.youtube import UploadStatus, YouTubeVideo, create_youtube_adapter

                video_path, temp_file = await _resolve_local_video_path_async(media_urls)
                if not video_path:
                    raise ValueError("No video found for YouTube publishing")

                adapter = create_youtube_adapter()
                try:
                    video = YouTubeVideo(
                        file_path=video_path,
                        title=(text_content or "SalesWhisper").strip()[:100],
                        description=(text_content or "").strip(),
                        tags=hashtags,
                    )
                    result = await adapter.upload_video(video, correlation_id=f"{post_id}:youtube")
                    success = result.status == UploadStatus.COMPLETED
                    publish_results["youtube"] = {
                        "success": success,
                        "account_id": str(account.id),
                        "post_id": result.video_id,
                        "url": result.video_url or result.watch_url,
                        "error": None if success else result.message,
                    }
                finally:
                    await adapter.close()
                    if temp_file and os.path.exists(temp_file):
                        try:
                            os.unlink(temp_file)
                        except Exception:
                            logger.warning("Failed to cleanup temp YouTube video", path=temp_file)

        except Exception as e:
            logger.exception("Publishing to %s failed", platform)
            publish_results[platform.lower()] = {"success": False, "error": str(e)}

    # Update post status
    successful = [k for k, v in publish_results.items() if v.get("success")]

    import json

    if successful:
        await db.execute(
            text(
                """
                UPDATE posts
                SET status = 'published',
                    published_at = :published_at,
                    platform_captions = :results
                WHERE id = :id
            """
            ),
            {"id": post_id, "published_at": datetime.utcnow(), "results": json.dumps(publish_results)},
        )
        logger.info("Post %s published", post_id=post_id, successful=successful)
        return True
    else:
        errors = [f"{k}: {v.get('error', 'unknown')}" for k, v in publish_results.items()]
        await db.execute(
            text(
                """
                UPDATE posts
                SET status = 'failed',
                    error_message = :error,
                    platform_captions = :results
                WHERE id = :id
            """
            ),
            {"id": post_id, "error": "; ".join(errors)[:500], "results": json.dumps(publish_results)},
        )
        return False


async def _collect_post_media_urls(db, media_asset_ids: list[str], enrichment_data: dict[str, Any] | None) -> list[str]:
    """Collect publishable media URLs for a post."""
    urls: list[str] = []

    for media_id in media_asset_ids:
        result = await db.execute(
            text(
                """
                SELECT storage_path
                FROM media_assets
                WHERE id = :media_id
                """
            ),
            {"media_id": str(media_id)},
        )
        row = result.fetchone()
        if not row:
            continue

        media_url = row[0]
        if isinstance(media_url, str) and media_url.startswith(("http://", "https://")):
            urls.append(media_url)

    if isinstance(enrichment_data, dict):
        for key in ("generated_image_url", "image_url", "generated_video_url", "video_url"):
            media_url = enrichment_data.get(key)
            if isinstance(media_url, str) and media_url.startswith(("http://", "https://")) and media_url not in urls:
                urls.append(media_url)

    return urls


def _pick_first_image(media_urls: list[str]) -> str | None:
    """Pick first image-like URL for publishers that support one image only."""
    video_ext = (".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv")
    for media_url in media_urls:
        clean_url = media_url.lower().split("?", 1)[0]
        if not clean_url.endswith(video_ext):
            return media_url
    return None


def _pick_first_video(media_urls: list[str]) -> str | None:
    for media_url in media_urls:
        clean_url = media_url.lower().split("?", 1)[0]
        if clean_url.endswith(VIDEO_EXTENSIONS):
            return media_url
    return None


async def _download_to_temp_async(url: str) -> str:
    parsed = urlparse(url)
    suffix = os.path.splitext(parsed.path)[1] or ".mp4"
    fd, tmp_path = tempfile.mkstemp(prefix="crosspost_scheduler_", suffix=suffix)
    os.close(fd)

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            with open(tmp_path, "wb") as f:
                async for chunk in response.aiter_bytes():
                    f.write(chunk)
    return tmp_path


async def _resolve_local_video_path_async(media_urls: list[str]) -> tuple[str | None, str | None]:
    """Return local video path and temp file path for cleanup."""
    video_url = _pick_first_video(media_urls)
    if not video_url:
        return None, None

    if os.path.exists(video_url):
        return video_url, None

    if video_url.startswith(("http://", "https://")):
        try:
            tmp_file = await _download_to_temp_async(video_url)
            return tmp_file, tmp_file
        except Exception:
            logger.exception("Failed to download remote video for publishing")
            return None, None

    return None, None
