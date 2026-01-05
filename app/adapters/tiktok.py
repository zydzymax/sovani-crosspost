"""
TikTok API adapter for SalesWhisper Crosspost.

This module handles TikTok video publishing through the TikTok for Business API,
including direct posting for approved apps and draft creation with webhook callbacks.
"""

import asyncio
import hashlib
import hmac
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..core.config import settings
from ..core.logging import get_logger, with_logging_context
from ..observability.metrics import metrics

logger = get_logger("adapters.tiktok")


class TikTokError(Exception):
    """Base exception for TikTok API errors."""

    pass


class TikTokRateLimitError(TikTokError):
    """Raised when TikTok API rate limit is exceeded."""

    pass


class TikTokAuthError(TikTokError):
    """Raised when TikTok API authentication fails."""

    pass


class TikTokValidationError(TikTokError):
    """Raised when TikTok API validation fails."""

    pass


class TikTokUploadError(TikTokError):
    """Raised when TikTok video upload fails."""

    pass


class PostStatus(Enum):
    """TikTok post status."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PUBLISHED = "published"
    DRAFT = "draft"
    REJECTED = "rejected"
    ERROR = "error"


class WebhookEventType(Enum):
    """TikTok webhook event types."""

    VIDEO_PUBLISH = "video.publish"
    VIDEO_UPLOAD = "video.upload"
    VIDEO_DELETE = "video.delete"


@dataclass
class TikTokVideoItem:
    """Represents a video item for TikTok."""

    file_path: str
    title: str
    description: str = ""
    tags: list[str] = None
    privacy_level: str = "PUBLIC_TO_EVERYONE"  # SELF_ONLY, MUTUAL_FOLLOW_FRIENDS, PUBLIC_TO_EVERYONE
    disable_duet: bool = False
    disable_comment: bool = False
    disable_stitch: bool = False
    brand_content_toggle: bool = False
    brand_organic_toggle: bool = False

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


@dataclass
class TikTokPost:
    """Represents a TikTok post."""

    video_item: TikTokVideoItem
    is_app_approved: bool = False  # Whether the app can direct post
    schedule_time: datetime | None = None
    auto_add_music: bool = True


@dataclass
class TikTokUploadResult:
    """Result of video upload to TikTok."""

    upload_id: str | None
    status: PostStatus
    upload_url: str | None = None
    upload_time: float | None = None
    file_size: int | None = None
    error_message: str | None = None


@dataclass
class TikTokPublishResult:
    """Result of TikTok post publishing."""

    share_id: str | None
    status: PostStatus
    message: str
    post_url: str | None = None
    published_at: datetime | None = None
    error_code: int | None = None
    retry_after: int | None = None


@dataclass
class WebhookEvent:
    """TikTok webhook event."""

    event_type: WebhookEventType
    share_id: str
    status: str
    timestamp: int
    error_code: int | None = None
    error_message: str | None = None


class TikTokAdapter:
    """TikTok API adapter."""

    def __init__(self):
        """Initialize TikTok adapter."""
        self.client_key = self._get_client_key()
        self.client_secret = self._get_client_secret()
        self.access_token = self._get_access_token()
        self.webhook_secret = self._get_webhook_secret()

        self.api_base = "https://open.tiktokapis.com/v2"
        self.upload_api_base = "https://open.tiktokapis.com/v2/post"

        # HTTP client configuration
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(300.0),  # TikTok uploads can take a long time
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
            headers={"User-Agent": "SalesWhisper-Crosspost/1.0"},
        )

        # Rate limiting - TikTok allows 1000 requests per day, 20 per minute
        self.daily_limit = 1000
        self.minute_limit = 20
        self.daily_requests = 0
        self.minute_requests = []
        self.rate_limit_lock = asyncio.Lock()
        self.last_reset = datetime.now(timezone.utc).date()

        logger.info("TikTok adapter initialized")

    def _get_client_key(self) -> str:
        """Get TikTok client key from settings."""
        if hasattr(settings.tiktok, "client_key"):
            key = settings.tiktok.client_key
            if hasattr(key, "get_secret_value"):
                return key.get_secret_value()
            return str(key)
        raise TikTokAuthError("TikTok client key not configured")

    def _get_client_secret(self) -> str:
        """Get TikTok client secret from settings."""
        if hasattr(settings.tiktok, "client_secret"):
            secret = settings.tiktok.client_secret
            if hasattr(secret, "get_secret_value"):
                return secret.get_secret_value()
            return str(secret)
        raise TikTokAuthError("TikTok client secret not configured")

    def _get_access_token(self) -> str:
        """Get TikTok access token from settings."""
        if hasattr(settings.tiktok, "access_token"):
            token = settings.tiktok.access_token
            if hasattr(token, "get_secret_value"):
                return token.get_secret_value()
            return str(token)
        raise TikTokAuthError("TikTok access token not configured")

    def _get_webhook_secret(self) -> str:
        """Get TikTok webhook secret from settings."""
        if hasattr(settings.tiktok, "webhook_secret"):
            secret = settings.tiktok.webhook_secret
            if hasattr(secret, "get_secret_value"):
                return secret.get_secret_value()
            return str(secret)
        raise TikTokAuthError("TikTok webhook secret not configured")

    async def publish_post(self, post: TikTokPost, correlation_id: str = None) -> TikTokPublishResult:
        """Publish post to TikTok."""
        start_time = time.time()

        with with_logging_context(correlation_id=correlation_id):
            logger.info(
                "Publishing TikTok post",
                title=post.video_item.title,
                file_path=post.video_item.file_path,
                is_approved_app=post.is_app_approved,
                is_scheduled=bool(post.schedule_time),
            )

            try:
                # Step 1: Upload video first
                upload_result = await self._upload_video(post.video_item, correlation_id)

                if upload_result.status != PostStatus.IN_PROGRESS:
                    return TikTokPublishResult(
                        share_id=None,
                        status=PostStatus.ERROR,
                        message=upload_result.error_message or "Video upload failed",
                    )

                # Step 2: Create post (direct publish or draft)
                if post.is_app_approved:
                    # Direct publish for approved apps
                    publish_result = await self._direct_publish(post, upload_result.upload_id, correlation_id)
                else:
                    # Create draft for non-approved apps
                    publish_result = await self._create_draft(post, upload_result.upload_id, correlation_id)

                processing_time = time.time() - start_time

                # Track metrics
                metrics.track_external_api_call(
                    service="tiktok", endpoint="publish_post", status_code=200, duration=processing_time
                )

                logger.info(
                    "TikTok post processing completed",
                    share_id=publish_result.share_id,
                    status=publish_result.status.value,
                    processing_time=processing_time,
                    post_url=publish_result.post_url,
                )

                return publish_result

            except Exception as e:
                processing_time = time.time() - start_time

                # Track failure metrics
                metrics.track_external_api_call(
                    service="tiktok",
                    endpoint="publish_post",
                    status_code=getattr(e, "error_code", 500),
                    duration=processing_time,
                    error=str(e),
                )

                logger.error(
                    "Failed to publish TikTok post", error=str(e), processing_time=processing_time, exc_info=True
                )

                return TikTokPublishResult(
                    share_id=None,
                    status=PostStatus.ERROR,
                    message=str(e),
                    error_code=getattr(e, "error_code", None),
                    retry_after=getattr(e, "retry_after", None),
                )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=16),
        retry=retry_if_exception_type((httpx.RequestError, TikTokRateLimitError)),
    )
    async def _upload_video(self, video_item: TikTokVideoItem, correlation_id: str = None) -> TikTokUploadResult:
        """Upload video to TikTok using chunked upload."""
        start_time = time.time()

        with with_logging_context(correlation_id=correlation_id, action="upload_video"):
            logger.info("Starting TikTok video upload", file_path=video_item.file_path)

            try:
                # Get file info
                file_path = Path(video_item.file_path)
                if not file_path.exists():
                    raise TikTokUploadError(f"Video file not found: {video_item.file_path}")

                file_size = file_path.stat().st_size

                # Step 1: Initialize upload
                init_response = await self._init_video_upload(file_size, correlation_id)
                upload_id = init_response["upload_id"]
                upload_url = init_response["upload_url"]

                # Step 2: Upload video chunks
                await self._upload_video_chunks(upload_url, video_item.file_path, correlation_id)

                upload_time = time.time() - start_time

                logger.info(
                    "TikTok video uploaded successfully",
                    upload_id=upload_id,
                    file_size=file_size,
                    upload_time=upload_time,
                )

                return TikTokUploadResult(
                    upload_id=upload_id,
                    status=PostStatus.IN_PROGRESS,
                    upload_url=upload_url,
                    upload_time=upload_time,
                    file_size=file_size,
                )

            except Exception as e:
                upload_time = time.time() - start_time

                logger.error(
                    "Failed to upload TikTok video",
                    file_path=video_item.file_path,
                    error=str(e),
                    upload_time=upload_time,
                    exc_info=True,
                )

                return TikTokUploadResult(
                    upload_id=None, status=PostStatus.ERROR, upload_time=upload_time, error_message=str(e)
                )

    async def _init_video_upload(self, file_size: int, correlation_id: str = None) -> dict[str, Any]:
        """Initialize video upload session."""
        with with_logging_context(correlation_id=correlation_id, action="init_upload"):
            payload = {"source_info": {"source": "FILE_UPLOAD", "file_size": file_size}}

            response = await self._make_api_request("POST", f"{self.upload_api_base}/video/init", json_data=payload)

            return response["data"]

    async def _upload_video_chunks(self, upload_url: str, file_path: str, correlation_id: str = None):
        """Upload video file in chunks."""
        with with_logging_context(correlation_id=correlation_id, action="upload_chunks"):
            # Read and upload file
            file_content = Path(file_path).read_bytes()

            # TikTok expects multipart upload
            files = {"video": (Path(file_path).name, file_content, "video/mp4")}

            async with self.http_client.put(upload_url, files=files) as response:
                response.raise_for_status()

                logger.info(
                    "Video chunks uploaded successfully", file_size=len(file_content), status_code=response.status_code
                )

    async def _direct_publish(
        self, post: TikTokPost, upload_id: str, correlation_id: str = None
    ) -> TikTokPublishResult:
        """Directly publish post for approved apps."""
        with with_logging_context(correlation_id=correlation_id, action="direct_publish"):
            try:
                payload = {
                    "post_info": {
                        "title": post.video_item.title,
                        "description": post.video_item.description,
                        "privacy_level": post.video_item.privacy_level,
                        "disable_duet": post.video_item.disable_duet,
                        "disable_comment": post.video_item.disable_comment,
                        "disable_stitch": post.video_item.disable_stitch,
                        "brand_content_toggle": post.video_item.brand_content_toggle,
                        "brand_organic_toggle": post.video_item.brand_organic_toggle,
                        "auto_add_music": post.auto_add_music,
                    },
                    "source_info": {"source": "FILE_UPLOAD", "video_id": upload_id},
                }

                # Add scheduling if specified
                if post.schedule_time:
                    payload["post_info"]["schedule_time"] = int(post.schedule_time.timestamp())

                # Add tags
                if post.video_item.tags:
                    payload["post_info"]["tag"] = post.video_item.tags

                response = await self._make_api_request(
                    "POST", f"{self.upload_api_base}/video/publish", json_data=payload
                )

                data = response["data"]
                share_id = data.get("share_id")

                # Generate post URL (TikTok doesn't provide direct URLs in API response)
                post_url = f"https://www.tiktok.com/@{share_id}" if share_id else None

                return TikTokPublishResult(
                    share_id=share_id,
                    status=PostStatus.PUBLISHED if not post.schedule_time else PostStatus.PENDING,
                    message="Post published successfully",
                    post_url=post_url,
                    published_at=datetime.now(timezone.utc) if not post.schedule_time else post.schedule_time,
                )

            except Exception as e:
                logger.error(f"Direct publish failed: {e}", exc_info=True)
                raise

    async def _create_draft(self, post: TikTokPost, upload_id: str, correlation_id: str = None) -> TikTokPublishResult:
        """Create draft for non-approved apps."""
        with with_logging_context(correlation_id=correlation_id, action="create_draft"):
            try:
                payload = {
                    "post_info": {
                        "title": post.video_item.title,
                        "description": post.video_item.description,
                        "privacy_level": post.video_item.privacy_level,
                        "disable_duet": post.video_item.disable_duet,
                        "disable_comment": post.video_item.disable_comment,
                        "disable_stitch": post.video_item.disable_stitch,
                        "auto_add_music": post.auto_add_music,
                    },
                    "source_info": {"source": "FILE_UPLOAD", "video_id": upload_id},
                }

                # Add tags
                if post.video_item.tags:
                    payload["post_info"]["tag"] = post.video_item.tags

                response = await self._make_api_request(
                    "POST", f"{self.upload_api_base}/video/draft", json_data=payload
                )

                data = response["data"]
                draft_id = data.get("draft_id")

                return TikTokPublishResult(
                    share_id=draft_id,
                    status=PostStatus.DRAFT,
                    message="Draft created successfully. Manual approval required.",
                    published_at=None,
                )

            except Exception as e:
                logger.error(f"Create draft failed: {e}", exc_info=True)
                raise

    def validate_webhook_signature(self, payload: str, signature: str, timestamp: str) -> bool:
        """Validate TikTok webhook signature using HMAC-SHA256."""
        try:
            # TikTok webhook signature format: timestamp|payload
            message = f"{timestamp}|{payload}"

            expected_signature = hmac.new(
                self.webhook_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
            ).hexdigest()

            return hmac.compare_digest(signature, expected_signature)

        except Exception as e:
            logger.error(f"Webhook signature validation failed: {e}")
            return False

    async def handle_webhook_event(self, payload: dict[str, Any], correlation_id: str = None) -> WebhookEvent:
        """Handle incoming TikTok webhook event."""
        with with_logging_context(correlation_id=correlation_id, action="handle_webhook"):
            try:
                event_type_str = payload.get("event_type")
                if not event_type_str:
                    raise TikTokValidationError("Missing event_type in webhook payload")

                # Parse event type
                try:
                    event_type = WebhookEventType(event_type_str)
                except ValueError:
                    logger.warning(f"Unknown webhook event type: {event_type_str}")
                    event_type = None

                event_data = payload.get("data", {})
                share_id = event_data.get("share_id")
                status = event_data.get("status")
                timestamp = payload.get("timestamp", int(time.time()))

                error_code = event_data.get("error_code")
                error_message = event_data.get("error_message")

                logger.info(
                    "Processing TikTok webhook event",
                    event_type=event_type_str,
                    share_id=share_id,
                    status=status,
                    error_code=error_code,
                )

                # Update post status based on webhook
                await self._update_post_from_webhook(share_id, status, error_code, error_message, correlation_id)

                return WebhookEvent(
                    event_type=event_type,
                    share_id=share_id,
                    status=status,
                    timestamp=timestamp,
                    error_code=error_code,
                    error_message=error_message,
                )

            except Exception as e:
                logger.error(f"Webhook event handling failed: {e}", payload=payload, exc_info=True)
                raise

    async def _update_post_from_webhook(
        self, share_id: str, status: str, error_code: int | None, error_message: str | None, correlation_id: str = None
    ):
        """Update post status from webhook event."""
        with with_logging_context(correlation_id=correlation_id):
            # Map TikTok status to our PostStatus
            status_mapping = {
                "PROCESSING": PostStatus.IN_PROGRESS,
                "SUCCESS": PostStatus.PUBLISHED,
                "FAILED": PostStatus.ERROR,
                "REJECTED": PostStatus.REJECTED,
            }

            post_status = status_mapping.get(status, PostStatus.PENDING)

            # Generate post URL for published posts
            post_url = None
            if post_status == PostStatus.PUBLISHED and share_id:
                post_url = f"https://www.tiktok.com/@{share_id}"

            update_data = {
                "status": post_status.value,
                "share_id": share_id,
                "post_url": post_url,
                "error_code": error_code,
                "error_message": error_message,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

            logger.info(
                "Updating post status from webhook",
                share_id=share_id,
                status=post_status.value,
                post_url=post_url,
                update_data=update_data,
            )

    async def _check_rate_limits(self):
        """Check and enforce TikTok API rate limits."""
        async with self.rate_limit_lock:
            current_time = time.time()
            current_date = datetime.now(timezone.utc).date()

            # Reset daily counter if new day
            if current_date > self.last_reset:
                self.daily_requests = 0
                self.last_reset = current_date
                logger.info("Daily TikTok rate limit counter reset")

            # Check daily limit
            if self.daily_requests >= self.daily_limit:
                raise TikTokRateLimitError(f"Daily rate limit exceeded: {self.daily_requests}/{self.daily_limit}")

            # Clean up old minute requests
            self.minute_requests = [req_time for req_time in self.minute_requests if current_time - req_time < 60.0]

            # Check minute limit
            if len(self.minute_requests) >= self.minute_limit:
                oldest_request = min(self.minute_requests)
                wait_time = 60.0 - (current_time - oldest_request)

                if wait_time > 0:
                    logger.warning(f"TikTok minute rate limit reached. Waiting {wait_time:.2f} seconds")
                    await asyncio.sleep(wait_time)

            # Record request
            self.daily_requests += 1
            self.minute_requests.append(current_time)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        retry=retry_if_exception_type((httpx.RequestError, TikTokRateLimitError)),
    )
    async def _make_api_request(
        self, method: str, url: str, params: dict[str, Any] = None, json_data: dict[str, Any] = None
    ) -> dict[str, Any]:
        """Make authenticated API request to TikTok."""
        await self._check_rate_limits()

        headers = {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json; charset=UTF-8"}

        try:
            response = await self.http_client.request(
                method=method, url=url, headers=headers, params=params, json=json_data
            )

            response.raise_for_status()
            result = response.json()

            # Handle TikTok API errors
            if result.get("error"):
                await self._handle_api_error(result["error"])

            return result

        except httpx.RequestError as e:
            logger.warning(f"TikTok API request error, will retry: {e}")
            raise
        except Exception as e:
            logger.error(f"TikTok API request failed: {e}")
            raise

    async def _handle_api_error(self, error_data: dict[str, Any]):
        """Handle TikTok API errors."""
        error_code = error_data.get("code")
        error_message = error_data.get("message", "Unknown error")

        logger.error("TikTok API error", error_code=error_code, error_message=error_message, error_data=error_data)

        # Handle specific error codes
        if error_code == "invalid_token":
            raise TikTokAuthError(f"Invalid access token: {error_message}")
        elif error_code == "rate_limit_exceeded":
            raise TikTokRateLimitError(f"Rate limit exceeded: {error_message}")
        elif error_code == "invalid_request":
            raise TikTokValidationError(f"Invalid request: {error_message}")
        elif error_code == "upload_failed":
            raise TikTokUploadError(f"Upload failed: {error_message}")
        elif error_code == "content_rejected":
            raise TikTokValidationError(f"Content rejected: {error_message}")
        else:
            raise TikTokError(f"TikTok API Error {error_code}: {error_message}")

    async def get_video_info(self, share_id: str) -> dict[str, Any]:
        """Get information about a TikTok video."""
        try:
            response = await self._make_api_request(
                "GET",
                f"{self.api_base}/video/list",
                params={
                    "fields": "id,title,video_description,duration,cover_image_url,share_url,view_count,like_count,comment_count,share_count",
                    "max_count": 20,
                },
            )

            videos = response.get("data", {}).get("videos", [])
            for video in videos:
                if video.get("id") == share_id:
                    return {
                        "id": video.get("id"),
                        "title": video.get("title"),
                        "description": video.get("video_description"),
                        "duration": video.get("duration"),
                        "cover_image_url": video.get("cover_image_url"),
                        "share_url": video.get("share_url"),
                        "view_count": video.get("view_count"),
                        "like_count": video.get("like_count"),
                        "comment_count": video.get("comment_count"),
                        "share_count": video.get("share_count"),
                    }

            return {}

        except Exception as e:
            logger.error(f"Failed to get TikTok video info: {e}", share_id=share_id)
            return {}

    async def update_post_status(self, post_id: str, status: str, result_data: dict[str, Any] = None):
        """Update post status in database."""
        logger.info("Updating TikTok post status", post_id=post_id, status=status, result_data=result_data)

    async def close(self):
        """Close HTTP client and cleanup resources."""
        await self.http_client.aclose()
        logger.info("TikTok adapter closed")


# Global adapter instance
tiktok_adapter = TikTokAdapter()


# Convenience functions
async def publish_tiktok_video(
    video_path: str,
    title: str,
    description: str = "",
    tags: list[str] = None,
    privacy_level: str = "PUBLIC_TO_EVERYONE",
    is_app_approved: bool = False,
    correlation_id: str = None,
) -> TikTokPublishResult:
    """Publish video to TikTok."""
    video_item = TikTokVideoItem(
        file_path=video_path, title=title, description=description, tags=tags or [], privacy_level=privacy_level
    )

    post = TikTokPost(video_item=video_item, is_app_approved=is_app_approved)

    return await tiktok_adapter.publish_post(post, correlation_id)


async def get_tiktok_video_info(share_id: str) -> dict[str, Any]:
    """Get TikTok video information."""
    return await tiktok_adapter.get_video_info(share_id)


async def validate_tiktok_webhook(payload: str, signature: str, timestamp: str) -> bool:
    """Validate TikTok webhook signature."""
    return tiktok_adapter.validate_webhook_signature(payload, signature, timestamp)


async def handle_tiktok_webhook(payload: dict[str, Any], correlation_id: str = None) -> WebhookEvent:
    """Handle TikTok webhook event."""
    return await tiktok_adapter.handle_webhook_event(payload, correlation_id)


async def cleanup_tiktok_adapter():
    """Cleanup TikTok adapter resources."""
    await tiktok_adapter.close()
