"""Publish stage tasks for SalesWhisper Crosspost."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from collections import defaultdict
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import text

from ...core.logging import audit_logger, get_logger, with_logging_context
from ...core.security import decrypt_data
from ...models.db import db_manager
from ...observability.metrics import metrics
from ..celery_app import celery

logger = get_logger("tasks.publish")

DEFAULT_PUBLISH_PLATFORMS = ("instagram", "vk", "tiktok", "youtube", "telegram", "dzen")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv")
PLATFORMS_WITH_ACCOUNT_OWNERSHIP = {"telegram", "vk", "instagram", "tiktok", "dzen"}
GLOBAL_ACCOUNT_FALLBACK_ENV = "PUBLISH_ALLOW_GLOBAL_ACCOUNTS_FALLBACK"


def _epoch_time() -> float:
    return time.time()


def _run_async(coro):
    """Run async coroutine in sync Celery context."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _normalize_platform(value: Any) -> str:
    if value is None:
        return ""
    text_value = str(value).strip().lower()
    if not text_value:
        return ""
    if "." in text_value:
        text_value = text_value.split(".")[-1]
    return text_value


def _extract_platform_targets(stage_data: dict[str, Any]) -> list[str]:
    targets: list[str] = []
    for source in (
        stage_data.get("platform_posts"),
        stage_data.get("captions"),
        stage_data.get("preflight_results", {}).get("validation_results"),
    ):
        if isinstance(source, dict):
            targets.extend(_normalize_platform(k) for k in source.keys())

    raw_platforms = stage_data.get("platforms")
    if isinstance(raw_platforms, list):
        targets.extend(_normalize_platform(p) for p in raw_platforms)

    deduped = []
    seen = set()
    for platform in targets:
        if platform and platform not in seen:
            deduped.append(platform)
            seen.add(platform)

    return deduped or list(DEFAULT_PUBLISH_PLATFORMS)


def _extract_requested_account_ids(stage_data: dict[str, Any]) -> dict[str, set[str]]:
    """
    Extract requested account IDs from stage_data for explicit per-platform routing.
    Supports multiple payload shapes for compatibility.
    """
    requested: dict[str, set[str]] = defaultdict(set)

    def _add(platform: Any, account_value: Any):
        platform_name = _normalize_platform(platform)
        if not platform_name:
            return
        if isinstance(account_value, list):
            for value in account_value:
                if value is not None and str(value).strip():
                    requested[platform_name].add(str(value).strip())
        elif account_value is not None and str(account_value).strip():
            requested[platform_name].add(str(account_value).strip())

    for key in ("platform_account_ids", "selected_accounts", "account_ids"):
        payload = stage_data.get(key)
        if isinstance(payload, dict):
            for platform, account_value in payload.items():
                _add(platform, account_value)

    platform_posts = stage_data.get("platform_posts")
    if isinstance(platform_posts, dict):
        for platform, payload in platform_posts.items():
            if not isinstance(payload, dict):
                continue
            if "account_id" in payload:
                _add(platform, payload.get("account_id"))
            if "account_ids" in payload:
                _add(platform, payload.get("account_ids"))

    return dict(requested)


def _extract_caption(stage_data: dict[str, Any], platform: str) -> str:
    platform_posts = stage_data.get("platform_posts")
    if isinstance(platform_posts, dict):
        payload = platform_posts.get(platform)
        if isinstance(payload, dict) and payload.get("caption"):
            return str(payload["caption"]).strip()

    captions = stage_data.get("captions")
    if isinstance(captions, dict):
        value = captions.get(platform)
        if value:
            return str(value).strip()

    generated_caption = stage_data.get("generated_caption")
    if generated_caption:
        return str(generated_caption).strip()

    text_content = stage_data.get("text_content") or stage_data.get("original_text")
    return str(text_content or "").strip()


def _extract_hashtags(stage_data: dict[str, Any], platform: str) -> list[str]:
    platform_posts = stage_data.get("platform_posts")
    if isinstance(platform_posts, dict):
        payload = platform_posts.get(platform)
        if isinstance(payload, dict) and isinstance(payload.get("hashtags"), list):
            return [str(tag).strip().lstrip("#") for tag in payload["hashtags"] if str(tag).strip()]

    hashtags = stage_data.get("hashtags")
    if isinstance(hashtags, list):
        return [str(tag).strip().lstrip("#") for tag in hashtags if str(tag).strip()]
    return []


def _collect_media_candidates(stage_data: dict[str, Any], platform: str) -> list[str]:
    media: list[str] = []

    # 1) platform_posts[platform].media
    platform_posts = stage_data.get("platform_posts")
    if isinstance(platform_posts, dict):
        payload = platform_posts.get(platform)
        if isinstance(payload, dict):
            for item in payload.get("media", []) or []:
                if isinstance(item, dict):
                    for key in ("url", "file_path", "local_path", "path"):
                        value = item.get(key)
                        if isinstance(value, str) and value.strip():
                            media.append(value.strip())
                            break
                elif isinstance(item, str) and item.strip():
                    media.append(item.strip())

    # 2) processed_media[asset][platform]
    processed_media = stage_data.get("processed_media")
    if isinstance(processed_media, dict):
        for _asset_id, per_platform in processed_media.items():
            if not isinstance(per_platform, dict):
                continue
            payload = per_platform.get(platform)
            if isinstance(payload, dict):
                for key in ("url", "local_path", "path"):
                    value = payload.get(key)
                    if isinstance(value, str) and value.strip():
                        media.append(value.strip())
            elif isinstance(payload, str) and payload.strip():
                media.append(payload.strip())

    # 3) explicit media_urls
    for value in stage_data.get("media_urls", []) or []:
        if isinstance(value, str) and value.strip():
            media.append(value.strip())

    # Deduplicate while preserving order
    deduped: list[str] = []
    seen = set()
    for value in media:
        if value not in seen:
            deduped.append(value)
            seen.add(value)
    return deduped


def _is_video(media_path: str) -> bool:
    parsed = urlparse(media_path)
    path = parsed.path or media_path
    lower_path = path.lower()
    return lower_path.endswith(VIDEO_EXTENSIONS)


def _first_image(media: list[str]) -> str | None:
    for item in media:
        if not _is_video(item):
            return item
    return None


def _first_video(media: list[str]) -> str | None:
    for item in media:
        if _is_video(item):
            return item
    return None


def _download_to_temp(url: str) -> str:
    parsed = urlparse(url)
    suffix = os.path.splitext(parsed.path)[1] or ".mp4"
    fd, tmp_path = tempfile.mkstemp(prefix="crosspost_media_", suffix=suffix)
    os.close(fd)

    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            with open(tmp_path, "wb") as f:
                for chunk in response.iter_bytes():
                    f.write(chunk)
    return tmp_path


def _resolve_video_path(media: list[str]) -> tuple[str | None, str | None]:
    """Return video path and temporary file path for cleanup."""
    video = _first_video(media)
    if not video:
        return None, None
    if os.path.exists(video):
        return video, None
    if video.startswith(("http://", "https://")):
        try:
            tmp_file = _download_to_temp(video)
            return tmp_file, tmp_file
        except Exception as e:
            logger.warning("video_download_failed", url=video, error=str(e))
            return None, None
    return None, None


def _resolve_owner_user_id(post_id: str, stage_data: dict[str, Any]) -> str | None:
    for key in ("user_id", "owner_user_id"):
        value = stage_data.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()

    with db_manager.get_sync_session() as session:
        row = session.execute(
            text(
                """
                SELECT user_id
                FROM posts
                WHERE id = :post_id
                """
            ),
            {"post_id": post_id},
        ).fetchone()
    if not row or row[0] is None:
        return None
    return str(row[0])


def _load_active_accounts(owner_user_id: str | None, allow_global_fallback: bool = False) -> dict[str, list[SimpleNamespace]]:
    grouped: dict[str, list[SimpleNamespace]] = defaultdict(list)
    with db_manager.get_sync_session() as session:
        if owner_user_id:
            rows = session.execute(
                text(
                    """
                    SELECT sa.id, sa.platform, sa.access_token, sa.platform_user_id, sa.extra_credentials,
                           sa.publish_priority, usa.is_primary
                    FROM social_accounts sa
                    JOIN user_social_accounts usa ON usa.account_id = sa.id
                    WHERE sa.is_active = true
                      AND sa.publish_enabled = true
                      AND usa.can_publish = true
                      AND usa.user_id = :owner_user_id
                    ORDER BY sa.platform, usa.is_primary DESC, sa.publish_priority DESC, sa.created_at ASC
                    """
                ),
                {"owner_user_id": owner_user_id},
            ).fetchall()
        elif allow_global_fallback:
            rows = session.execute(
                text(
                    """
                    SELECT id, platform, access_token, platform_user_id, extra_credentials,
                           publish_priority, false as is_primary
                    FROM social_accounts
                    WHERE is_active = true
                      AND publish_enabled = true
                    ORDER BY platform, publish_priority DESC, created_at ASC
                    """
                )
            ).fetchall()
        else:
            rows = []

    def _decrypt_access_token(raw_value: Any) -> str:
        token = (raw_value or "").strip() if isinstance(raw_value, str) else ""
        if not token:
            return ""
        try:
            return decrypt_data(token)
        except Exception:
            return token

    for row in rows:
        extra = row[4] or {}
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except Exception:
                extra = {}
        grouped[_normalize_platform(row[1])].append(
            SimpleNamespace(
                id=str(row[0]),
                platform=_normalize_platform(row[1]),
                access_token=_decrypt_access_token(row[2]),
                platform_user_id=str(row[3]) if row[3] else "",
                extra_credentials=extra if isinstance(extra, dict) else {},
                publish_priority=int(row[5]) if row[5] is not None else 0,
                is_primary=bool(row[6]) if len(row) > 6 else False,
            )
        )
    return grouped


def _pick_accounts_for_platform(
    platform: str,
    available_accounts: list[SimpleNamespace],
    requested_ids: set[str] | None,
) -> list[SimpleNamespace]:
    if requested_ids:
        return [account for account in available_accounts if account.id in requested_ids]

    # For IG/TikTok we default to a single best account to avoid accidental cross-account posting.
    if platform in {"instagram", "tiktok"}:
        return available_accounts[:1]

    return available_accounts


def _is_force_republish(stage_data: dict[str, Any]) -> bool:
    for key in ("force_republish", "force", "republish"):
        value = stage_data.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}:
            return True
    return False


def _load_existing_successful_results(post_id: str) -> dict[str, dict[str, Any]]:
    with db_manager.get_sync_session() as session:
        rows = session.execute(
            text(
                """
                SELECT platform, account_id, platform_post_id, platform_post_url, published_at
                FROM publish_results
                WHERE post_id = :post_id
                  AND success = true
                """
            ),
            {"post_id": post_id},
        ).fetchall()

    existing: dict[str, dict[str, Any]] = {}
    for row in rows:
        platform = _normalize_platform(row[0])
        if not platform:
            continue
        existing[platform] = {
            "success": True,
            "account_id": str(row[1]) if row[1] else None,
            "platform_post_id": row[2],
            "platform_url": row[3],
            "published_at": row[4].timestamp() if row[4] else None,
            "skipped": True,
            "reason": "already_published",
        }
    return existing


def _map_publish_result(
    success: bool,
    platform_post_id: str | None = None,
    platform_url: str | None = None,
    error: str | None = None,
    account_id: str | None = None,
) -> dict[str, Any]:
    payload = {
        "success": success,
        "platform_post_id": platform_post_id,
        "platform_url": platform_url,
        "published_at": _epoch_time() if success else None,
        "error": error,
    }
    if account_id:
        payload["account_id"] = account_id
    return payload


async def _publish_telegram(account: SimpleNamespace, caption: str, hashtags: list[str], media: list[str]) -> dict[str, Any]:
    from ...services.publishers.telegram import TelegramPublisher

    publisher = TelegramPublisher(account)
    result = await publisher.publish(
        text=caption,
        image_url=_first_image(media),
        video_url=_first_video(media),
        media_urls=media,
        hashtags=hashtags,
    )
    return _map_publish_result(
        success=bool(result.success),
        platform_post_id=result.platform_post_id,
        platform_url=result.platform_url,
        error=result.error,
        account_id=account.id,
    )


async def _publish_vk(account: SimpleNamespace, caption: str, hashtags: list[str], media: list[str]) -> dict[str, Any]:
    from ...services.publishers.vk import VKPublisher

    publisher = VKPublisher(account)
    result = await publisher.publish(
        text=caption,
        image_url=_first_image(media),
        hashtags=hashtags,
    )
    return _map_publish_result(
        success=bool(result.success),
        platform_post_id=result.platform_post_id,
        platform_url=result.platform_url,
        error=result.error,
        account_id=account.id,
    )


def _resolve_instagram_page_id(account: SimpleNamespace | None) -> str:
    if account is None:
        return ""
    extra = account.extra_credentials if isinstance(account.extra_credentials, dict) else {}
    for key in ("page_id", "instagram_business_id", "ig_user_id", "business_account_id"):
        value = extra.get(key)
        if value:
            return str(value)
    if account.platform_user_id:
        return str(account.platform_user_id)
    return ""


async def _publish_instagram(
    account: SimpleNamespace | None, caption: str, media: list[str], correlation_id: str
) -> dict[str, Any]:
    if not media:
        return _map_publish_result(False, error="No media provided for Instagram")

    try:
        from ...adapters.instagram import PublishStatus, publish_instagram_post
    except Exception as e:
        return _map_publish_result(False, error=f"Instagram adapter unavailable: {e}")

    access_token = account.access_token if account else ""
    page_id = _resolve_instagram_page_id(account)

    if account and not access_token:
        return _map_publish_result(False, error="Instagram account token missing", account_id=account.id)
    if account and not page_id:
        return _map_publish_result(False, error="Instagram account page_id missing", account_id=account.id)

    try:
        result = await publish_instagram_post(
            caption=caption,
            media_files=media,
            correlation_id=correlation_id,
            access_token=access_token or None,
            page_id=page_id or None,
        )
        success = result.status in {PublishStatus.FINISHED, PublishStatus.PENDING}
        return _map_publish_result(
            success=success,
            platform_post_id=result.post_id or result.container_id,
            platform_url=result.permalink,
            error=None if success else result.message,
            account_id=account.id if account else None,
        )
    except Exception as e:
        return _map_publish_result(False, error=str(e), account_id=account.id if account else None)


async def _publish_tiktok(
    account: SimpleNamespace | None, caption: str, hashtags: list[str], media: list[str], correlation_id: str
) -> dict[str, Any]:
    try:
        from ...adapters.tiktok import PostStatus, publish_tiktok_video
    except Exception as e:
        return _map_publish_result(False, error=f"TikTok adapter unavailable: {e}")

    access_token = account.access_token if account else ""
    if account and not access_token:
        return _map_publish_result(False, error="TikTok account token missing", account_id=account.id)

    video_path, tmp_file = _resolve_video_path(media)
    if not video_path:
        return _map_publish_result(
            False, error="No usable local/remote video for TikTok", account_id=account.id if account else None
        )

    try:
        is_app_approved = os.getenv("TIKTOK_DIRECT_POST_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
        title = (caption or "SalesWhisper").strip()[:90]
        description = (caption or "").strip()[:2200]
        result = await publish_tiktok_video(
            video_path=video_path,
            title=title,
            description=description,
            tags=hashtags,
            is_app_approved=is_app_approved,
            correlation_id=correlation_id,
            access_token=access_token or None,
        )
        success = result.status in {PostStatus.PUBLISHED, PostStatus.PENDING, PostStatus.DRAFT}
        return _map_publish_result(
            success=success,
            platform_post_id=result.share_id,
            platform_url=result.post_url,
            error=None if success else result.message,
            account_id=account.id if account else None,
        )
    except Exception as e:
        return _map_publish_result(False, error=str(e), account_id=account.id if account else None)
    finally:
        if tmp_file and os.path.exists(tmp_file):
            try:
                os.unlink(tmp_file)
            except Exception:
                logger.warning("failed_to_cleanup_temp_video", path=tmp_file)


async def _publish_youtube(caption: str, hashtags: list[str], media: list[str], correlation_id: str) -> dict[str, Any]:
    try:
        from ...adapters.youtube import UploadStatus, YouTubeVideo, create_youtube_adapter
    except Exception as e:
        return _map_publish_result(False, error=f"YouTube adapter unavailable: {e}")

    video_path, tmp_file = _resolve_video_path(media)
    if not video_path:
        return _map_publish_result(False, error="No usable local/remote video for YouTube")

    adapter = create_youtube_adapter()
    try:
        title = (caption or "SalesWhisper").strip()[:100]
        video = YouTubeVideo(
            file_path=video_path,
            title=title,
            description=(caption or "").strip(),
            tags=hashtags,
        )
        result = await adapter.upload_video(video, correlation_id=correlation_id)
        success = result.status == UploadStatus.COMPLETED
        return _map_publish_result(
            success=success,
            platform_post_id=result.video_id,
            platform_url=result.video_url or result.watch_url,
            error=None if success else result.message,
        )
    except Exception as e:
        return _map_publish_result(False, error=str(e))
    finally:
        await adapter.close()
        if tmp_file and os.path.exists(tmp_file):
            try:
                os.unlink(tmp_file)
            except Exception:
                logger.warning("failed_to_cleanup_temp_video", path=tmp_file)


async def _publish_dzen(account: SimpleNamespace, caption: str, hashtags: list[str], media: list[str]) -> dict[str, Any]:
    try:
        from ...adapters.dzen import publish_dzen_article
    except Exception as e:
        return _map_publish_result(False, error=f"Dzen adapter unavailable: {e}")

    try:
        access_token = decrypt_data(account.access_token) if account.access_token else None
        if not access_token:
            return _map_publish_result(False, error="No Dzen access token configured")

        title = (caption or "SalesWhisper").strip()[:100]
        body = caption or ""

        # Pick first image/video URL for Dzen article cover
        image_url = media[0] if media else None

        result = await publish_dzen_article(
            title=title,
            body=body,
            access_token=access_token,
            image_url=image_url,
            tags=hashtags,
        )
        return _map_publish_result(
            success=result.success,
            platform_post_id=result.post_id,
            platform_url=result.post_url,
            error=None if result.success else result.status,
        )
    except Exception as e:
        return _map_publish_result(False, error=str(e))



def _merge_attempts(platform: str, attempts: list[dict[str, Any]]) -> dict[str, Any]:
    if not attempts:
        return _map_publish_result(False, error=f"No publish attempts for {platform}")

    for item in attempts:
        if item.get("success"):
            merged = dict(item)
            merged["attempts"] = attempts
            return merged

    merged = dict(attempts[-1])
    merged["attempts"] = attempts
    return merged


@celery.task(bind=True, name="app.workers.tasks.publish.publish_to_platforms")
def publish_to_platforms(self, stage_data: dict[str, Any]) -> dict[str, Any]:
    """Publish content to configured social media platforms."""
    task_start_time = time.monotonic()
    post_id = stage_data["post_id"]

    with with_logging_context(task_id=self.request.id, post_id=post_id):
        logger.info("Starting platform publishing", post_id=post_id)

        try:
            target_platforms = _extract_platform_targets(stage_data)
            requested_account_ids = _extract_requested_account_ids(stage_data)
            force_republish = _is_force_republish(stage_data)
            owner_user_id = _resolve_owner_user_id(post_id, stage_data)
            allow_global_fallback = os.getenv(GLOBAL_ACCOUNT_FALLBACK_ENV, "false").lower() in {"1", "true", "yes", "on"}
            accounts_by_platform = _load_active_accounts(owner_user_id, allow_global_fallback=allow_global_fallback)
            existing_successful_results = _load_existing_successful_results(post_id)

            if not owner_user_id and not allow_global_fallback:
                logger.warning(
                    "post_owner_not_resolved_accounts_blocked",
                    post_id=post_id,
                    target_platforms=target_platforms,
                )

            publish_results: dict[str, dict[str, Any]] = {}

            for platform in target_platforms:
                if not force_republish and platform in existing_successful_results:
                    publish_results[platform] = existing_successful_results[platform]
                    logger.info("publish_skipped_already_published", post_id=post_id, platform=platform)
                    continue

                caption = _extract_caption(stage_data, platform)
                hashtags = _extract_hashtags(stage_data, platform)
                media = _collect_media_candidates(stage_data, platform)
                correlation_id = f"{post_id}:{platform}:{int(_epoch_time())}"
                attempts: list[dict[str, Any]] = []
                requested_for_platform = requested_account_ids.get(platform)
                available_accounts = accounts_by_platform.get(platform, [])
                selected_accounts = _pick_accounts_for_platform(platform, available_accounts, requested_for_platform)

                try:
                    if platform == "telegram":
                        for account in selected_accounts:
                            attempts.append(_run_async(_publish_telegram(account, caption, hashtags, media)))

                    elif platform == "vk":
                        for account in selected_accounts:
                            attempts.append(_run_async(_publish_vk(account, caption, hashtags, media)))

                    elif platform == "instagram":
                        for account in selected_accounts:
                            attempts.append(_run_async(_publish_instagram(account, caption, media, correlation_id)))

                    elif platform == "tiktok":
                        for account in selected_accounts:
                            attempts.append(_run_async(_publish_tiktok(account, caption, hashtags, media, correlation_id)))

                    elif platform == "youtube":
                        attempts.append(_run_async(_publish_youtube(caption, hashtags, media, correlation_id)))

                    elif platform == "dzen":
                        for account in selected_accounts:
                            attempts.append(_run_async(_publish_dzen(account, caption, hashtags, media)))

                    else:
                        attempts.append(_map_publish_result(False, error=f"Unsupported platform: {platform}"))

                    if platform in PLATFORMS_WITH_ACCOUNT_OWNERSHIP and not attempts:
                        if requested_for_platform:
                            attempts.append(
                                _map_publish_result(
                                    False,
                                    error=f"Requested account_id not found or not allowed for platform: {platform}",
                                )
                            )
                        else:
                            attempts.append(
                                _map_publish_result(False, error=f"No active accounts connected for platform: {platform}")
                            )

                except Exception as e:
                    logger.exception("Publishing failed for platform %s", platform)
                    attempts.append(_map_publish_result(False, error=str(e)))

                merged = _merge_attempts(platform, attempts)
                publish_results[platform] = merged

                if merged.get("success"):
                    metrics.track_post_published(platform)
                    audit_logger.log_post_published(
                        post_id=post_id,
                        platform=platform,
                        platform_post_id=merged.get("platform_post_id"),
                        platform_url=merged.get("platform_url"),
                    )
                else:
                    metrics.track_post_failed(platform, "publish_error")
                    audit_logger.log_post_failed(post_id, platform, merged.get("error", "unknown_error"))

            processing_time = time.monotonic() - task_start_time
            successful_platforms = [p for p, r in publish_results.items() if r.get("success")]

            # Trigger next stage
            from .finalize import finalize_post

            next_task = finalize_post.delay({**stage_data, "publish_results": publish_results})

            logger.info(
                "Platform publishing completed",
                post_id=post_id,
                processing_time=processing_time,
                successful_platforms=len(successful_platforms),
                total_platforms=len(target_platforms),
            )

            return {
                "success": bool(successful_platforms),
                "post_id": post_id,
                "processing_time": processing_time,
                "platforms_published": len(successful_platforms),
                "total_platforms": len(target_platforms),
                "publish_results": publish_results,
                "owner_user_id": owner_user_id,
                "next_stage": "finalize",
                "next_task_id": next_task.id,
            }

        except Exception as e:
            logger.exception("Platform publishing failed", post_id=post_id, error=str(e))
            if self.request.retries < self.max_retries:
                raise self.retry(countdown=60 * (self.request.retries + 1))
            raise


def delay(*args, **kwargs):
    """Compatibility proxy for task.delay used by legacy tests."""
    return publish_to_platforms.delay(*args, **kwargs)
