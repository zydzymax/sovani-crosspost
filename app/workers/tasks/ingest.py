"""
Ingest stage tasks for SalesWhisper Crosspost.

Processes incoming Telegram webhook data:
- Downloads media from Telegram Bot API
- Creates Post + MediaAsset records in DB
- Uploads media to MinIO
- Triggers enrich stage
"""

import io
import time
import uuid
from datetime import datetime
from typing import Any

import httpx
from minio import Minio
from urllib.parse import urlparse

from ...core.config import settings
from ...core.logging import audit_logger, get_logger, with_logging_context
from ...models.db import db_manager
from ...models.entities import MediaAsset, MediaType, Platform, Post, PostStatus, TaskStage
from ...observability.metrics import metrics
from ..celery_app import celery

logger = get_logger("tasks.ingest")

MEDIA_FIELDS = ("photo", "video", "animation", "document", "audio", "voice")
MEDIA_TYPE_MAP = {
    "photo": MediaType.IMAGE,
    "video": MediaType.VIDEO,
    "animation": MediaType.ANIMATION,
    "document": MediaType.DOCUMENT,
    "audio": MediaType.AUDIO,
    "voice": MediaType.AUDIO,
}


def _utcnow() -> datetime:
    return datetime.utcnow()


def _get_minio_client() -> Minio:
    parsed = urlparse(settings.s3.endpoint)
    secure = parsed.scheme == "https"
    endpoint_host = parsed.netloc or parsed.path
    return Minio(
        endpoint_host,
        access_key=settings.s3.access_key,
        secret_key=settings.s3.secret_key.get_secret_value(),
        secure=secure,
    )


def _ensure_bucket(client: Minio, bucket: str) -> None:
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)


def _download_telegram_file(bot_token: str, file_id: str) -> tuple[bytes, str] | None:
    """Download file from Telegram, return (bytes, file_path)."""
    try:
        with httpx.Client(timeout=60) as http:
            resp = http.get(f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}")
            resp.raise_for_status()
            file_path = resp.json()["result"]["file_path"]
            data = http.get(f"https://api.telegram.org/file/bot{bot_token}/{file_path}")
            data.raise_for_status()
            return data.content, file_path
    except Exception as e:
        logger.warning("Failed to download Telegram file", file_id=file_id, error=str(e))
        return None


def _upload_to_minio(client: Minio, bucket: str, object_name: str, data: bytes, content_type: str) -> str:
    client.put_object(
        bucket, object_name,
        io.BytesIO(data), length=len(data),
        content_type=content_type,
    )
    return f"s3://{bucket}/{object_name}"


@celery.task(bind=True, name="app.workers.tasks.ingest.process_telegram_update", max_retries=3)
def process_telegram_update(self, update_data: dict[str, Any], post_id: str) -> dict[str, Any]:
    """Process incoming Telegram update: create DB record, download media, trigger enrich."""
    task_start = time.monotonic()

    with with_logging_context(task_id=self.request.id, post_id=post_id):
        logger.info("Starting ingest", post_id=post_id, update_id=update_data.get("update_id"))

        try:
            content = _extract_content_from_update(update_data)
            if not content:
                raise ValueError("No processable content in update")

            bot_token = settings.telegram.publishing_bot_token.get_secret_value()
            minio = _get_minio_client()
            bucket = settings.s3.bucket_name
            _ensure_bucket(minio, bucket)

            session = db_manager.get_session()
            try:
                # Create Post record
                chat = content.get("chat", {})
                sender = content.get("from", {})
                post = Post(
                    id=uuid.UUID(post_id),
                    source_platform=Platform.TELEGRAM,
                    source_message_id=str(content.get("message_id", "")),
                    source_chat_id=str(chat.get("id", "")),
                    source_user_id=str(sender.get("id", "")) if sender else None,
                    original_text=content.get("text") or content.get("caption") or "",
                    source_data=content,
                    status=PostStatus.INGESTED,
                    current_stage=TaskStage.INGEST,
                )
                session.add(post)
                session.flush()

                # Download and store media
                media_count = 0
                for field in MEDIA_FIELDS:
                    if field not in content:
                        continue
                    raw = content[field]
                    if field == "photo" and isinstance(raw, list):
                        raw = max(raw, key=lambda x: x.get("file_size", 0))
                    if not isinstance(raw, dict):
                        continue

                    file_id = raw.get("file_id")
                    if not file_id:
                        continue

                    result = _download_telegram_file(bot_token, file_id)
                    if not result:
                        continue
                    file_bytes, tg_path = result
                    ext = tg_path.rsplit(".", 1)[-1] if "." in tg_path else "bin"
                    object_name = f"telegram/{post_id}/{field}_{file_id}.{ext}"
                    mime = raw.get("mime_type", f"application/{ext}")
                    s3_path = _upload_to_minio(minio, bucket, object_name, file_bytes, mime)

                    asset = MediaAsset(
                        post_id=uuid.UUID(post_id),
                        original_file_id=file_id,
                        file_name=raw.get("file_name"),
                        media_type=MEDIA_TYPE_MAP.get(field, MediaType.DOCUMENT),
                        mime_type=mime,
                        file_size=len(file_bytes),
                        width=raw.get("width"),
                        height=raw.get("height"),
                        duration=raw.get("duration"),
                        original_path=s3_path,
                    )
                    session.add(asset)
                    media_count += 1

                session.commit()
            finally:
                session.close()

            elapsed = time.monotonic() - task_start
            metrics.track_post_created("telegram", "webhook")

            audit_logger.log_post_created(
                post_id=post_id,
                platform="telegram",
                user_id=str(content.get("from", {}).get("id", "unknown")),
                product_id="telegram_ingest",
                processing_time=elapsed,
            )

            next_stage = {
                "post_id": post_id,
                "has_media": media_count > 0,
                "media_count": media_count,
                "text_content": content.get("text") or content.get("caption", ""),
                "source": "telegram",
                "original_update": update_data,
            }

            from .enrich import enrich_post_content
            enrich_task = enrich_post_content.apply_async(args=[next_stage], queue="enrich")

            logger.info(
                "Ingest done", post_id=post_id, media=media_count,
                next_task=enrich_task.id, elapsed=elapsed,
            )
            return {
                "success": True, "post_id": post_id,
                "media_processed": media_count,
                "next_stage": "enrich", "next_task_id": enrich_task.id,
            }

        except Exception as e:
            elapsed = time.monotonic() - task_start
            logger.exception("Ingest failed", post_id=post_id, error=str(e), elapsed=elapsed)
            metrics.track_post_failed("telegram", "ingest_error")
            if self.request.retries < self.max_retries:
                raise self.retry(countdown=60 * (self.request.retries + 1))
            raise


def _extract_content_from_update(update_data: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("message", "channel_post", "edited_message"):
        if update_data.get(key):
            return update_data[key]
    return None


def delay(*args, **kwargs):
    return process_telegram_update.delay(*args, **kwargs)
