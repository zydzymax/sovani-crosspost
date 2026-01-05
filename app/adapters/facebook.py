"""Facebook/Meta Graph API adapter for Crosspost."""

import asyncio
import json
import mimetypes
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

logger = get_logger("adapters.facebook")


class FacebookError(Exception):
    """Base exception for Facebook API errors."""
    pass


class FacebookRateLimitError(FacebookError):
    """Raised when Facebook API rate limit is exceeded."""
    pass


class FacebookAuthError(FacebookError):
    """Raised when Facebook API authentication fails."""
    pass


class FacebookValidationError(FacebookError):
    """Raised when Facebook API validation fails."""
    pass


class FacebookUploadError(FacebookError):
    """Raised when Facebook media upload fails."""
    pass


class MediaType(Enum):
    """Facebook media types."""
    PHOTO = "photo"
    VIDEO = "video"
    REEL = "reel"


class PostStatus(Enum):
    """Facebook post status."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PUBLISHED = "published"
    SCHEDULED = "scheduled"
    ERROR = "error"


@dataclass
class FacebookMediaItem:
    """Represents a media item for Facebook."""
    file_path: str
    media_type: MediaType
    caption: str | None = None


@dataclass
class FacebookPost:
    """Represents a Facebook post."""
    message: str
    media_items: list[FacebookMediaItem]
    page_id: str | None = None
    scheduled_publish_time: datetime | None = None
    published: bool = True
    link: str | None = None

    def __post_init__(self):
        if self.media_items is None:
            self.media_items = []


@dataclass
class FacebookUploadResult:
    """Result of media upload to Facebook."""
    media_type: MediaType
    media_id: str
    upload_time: float
    error_message: str | None = None


@dataclass
class FacebookPublishResult:
    """Result of Facebook post publishing."""
    post_id: str | None
    status: PostStatus
    message: str
    post_url: str | None = None
    published_at: datetime | None = None
    error_code: str | None = None


class FacebookAdapter:
    """Facebook/Meta Graph API adapter."""

    API_VERSION = "v18.0"
    API_BASE = f"https://graph.facebook.com/{API_VERSION}"

    def __init__(self, page_access_token: str = None, page_id: str = None):
        """Initialize Facebook adapter."""
        self.page_access_token = page_access_token or self._get_access_token()
        self.page_id = page_id or self._get_page_id()

        # HTTP client configuration
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0),  # Facebook uploads can be slow
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            headers={
                "User-Agent": "Crosspost/1.0"
            }
        )

        # Rate limiting
        self.rate_limit_lock = asyncio.Lock()
        self.last_request_time = 0
        self.min_request_interval = 0.2  # 5 requests per second

        logger.info("Facebook adapter initialized", page_id=self.page_id)

    def _get_access_token(self) -> str:
        """Get Facebook page access token from settings."""
        if hasattr(settings, 'facebook') and hasattr(settings.facebook, 'page_access_token'):
            token = settings.facebook.page_access_token
            if hasattr(token, 'get_secret_value'):
                return token.get_secret_value()
            return str(token)

        import os
        token = os.getenv('FACEBOOK_PAGE_ACCESS_TOKEN')
        if token:
            return token

        raise FacebookAuthError("Facebook page access token not configured.")

    def _get_page_id(self) -> str:
        """Get Facebook page ID from settings."""
        if hasattr(settings, 'facebook') and hasattr(settings.facebook, 'page_id'):
            return str(settings.facebook.page_id)

        import os
        page_id = os.getenv('FACEBOOK_PAGE_ID')
        if page_id:
            return page_id

        raise FacebookValidationError("Facebook page ID not configured.")

    async def publish_post(self, post: FacebookPost, correlation_id: str = None) -> FacebookPublishResult:
        """Publish post to Facebook page."""
        start_time = time.time()
        page_id = post.page_id or self.page_id

        with with_logging_context(correlation_id=correlation_id):
            logger.info(
                "Publishing Facebook post",
                message_length=len(post.message),
                media_count=len(post.media_items),
                page_id=page_id,
                is_scheduled=bool(post.scheduled_publish_time)
            )

            try:
                if post.media_items:
                    # Post with media
                    if len(post.media_items) == 1:
                        result = await self._publish_single_media(post, page_id, correlation_id)
                    else:
                        result = await self._publish_multi_media(post, page_id, correlation_id)
                else:
                    # Text-only post
                    result = await self._publish_text_post(post, page_id, correlation_id)

                processing_time = time.time() - start_time

                metrics.track_external_api_call(
                    service="facebook",
                    endpoint="publish",
                    status_code=200,
                    duration=processing_time
                )

                logger.info(
                    "Facebook post published successfully",
                    post_id=result.post_id,
                    processing_time=processing_time,
                    post_url=result.post_url
                )

                return result

            except Exception as e:
                processing_time = time.time() - start_time

                metrics.track_external_api_call(
                    service="facebook",
                    endpoint="publish",
                    status_code=500,
                    duration=processing_time,
                    error=str(e)
                )

                logger.error(
                    "Failed to publish Facebook post",
                    error=str(e),
                    processing_time=processing_time,
                    exc_info=True
                )

                return FacebookPublishResult(
                    post_id=None,
                    status=PostStatus.ERROR,
                    message=str(e),
                    error_code=getattr(e, 'error_code', None)
                )

    async def _publish_text_post(self, post: FacebookPost, page_id: str,
                                  correlation_id: str = None) -> FacebookPublishResult:
        """Publish text-only post."""
        params = {
            "message": post.message,
            "access_token": self.page_access_token
        }

        if post.link:
            params["link"] = post.link

        if post.scheduled_publish_time:
            params["scheduled_publish_time"] = int(post.scheduled_publish_time.timestamp())
            params["published"] = "false"

        response = await self._make_api_request(
            f"/{page_id}/feed",
            method="POST",
            params=params
        )

        post_id = response.get("id")
        post_url = f"https://www.facebook.com/{post_id}" if post_id else None

        return FacebookPublishResult(
            post_id=post_id,
            status=PostStatus.SCHEDULED if post.scheduled_publish_time else PostStatus.PUBLISHED,
            message="Post published successfully",
            post_url=post_url,
            published_at=datetime.now(timezone.utc) if not post.scheduled_publish_time else None
        )

    async def _publish_single_media(self, post: FacebookPost, page_id: str,
                                     correlation_id: str = None) -> FacebookPublishResult:
        """Publish post with single media item."""
        media_item = post.media_items[0]

        if media_item.media_type == MediaType.PHOTO:
            return await self._publish_photo(post, page_id, media_item, correlation_id)
        elif media_item.media_type in [MediaType.VIDEO, MediaType.REEL]:
            return await self._publish_video(post, page_id, media_item, correlation_id)
        else:
            raise FacebookValidationError(f"Unsupported media type: {media_item.media_type}")

    async def _publish_multi_media(self, post: FacebookPost, page_id: str,
                                    correlation_id: str = None) -> FacebookPublishResult:
        """Publish post with multiple media items (carousel)."""
        # Upload all photos first
        photo_ids = []
        for media_item in post.media_items:
            if media_item.media_type == MediaType.PHOTO:
                upload_result = await self._upload_photo_unpublished(media_item, page_id, correlation_id)
                if upload_result.media_id:
                    photo_ids.append(upload_result.media_id)

        if not photo_ids:
            raise FacebookValidationError("No photos uploaded successfully")

        # Create post with attached photos
        params = {
            "message": post.message,
            "access_token": self.page_access_token
        }

        # Attach photos
        for i, photo_id in enumerate(photo_ids):
            params[f"attached_media[{i}]"] = json.dumps({"media_fbid": photo_id})

        if post.scheduled_publish_time:
            params["scheduled_publish_time"] = int(post.scheduled_publish_time.timestamp())
            params["published"] = "false"

        response = await self._make_api_request(
            f"/{page_id}/feed",
            method="POST",
            params=params
        )

        post_id = response.get("id")
        post_url = f"https://www.facebook.com/{post_id}" if post_id else None

        return FacebookPublishResult(
            post_id=post_id,
            status=PostStatus.SCHEDULED if post.scheduled_publish_time else PostStatus.PUBLISHED,
            message="Multi-media post published successfully",
            post_url=post_url,
            published_at=datetime.now(timezone.utc) if not post.scheduled_publish_time else None
        )

    async def _publish_photo(self, post: FacebookPost, page_id: str,
                             media_item: FacebookMediaItem,
                             correlation_id: str = None) -> FacebookPublishResult:
        """Publish single photo post."""
        file_content, file_name, mime_type = await self._prepare_file(media_item.file_path)

        params = {
            "caption": post.message,
            "access_token": self.page_access_token
        }

        if post.scheduled_publish_time:
            params["scheduled_publish_time"] = int(post.scheduled_publish_time.timestamp())
            params["published"] = "false"

        files = {
            "source": (file_name, file_content, mime_type)
        }

        response = await self._make_api_request(
            f"/{page_id}/photos",
            method="POST",
            params=params,
            files=files
        )

        post_id = response.get("post_id") or response.get("id")
        post_url = f"https://www.facebook.com/{post_id}" if post_id else None

        return FacebookPublishResult(
            post_id=post_id,
            status=PostStatus.SCHEDULED if post.scheduled_publish_time else PostStatus.PUBLISHED,
            message="Photo published successfully",
            post_url=post_url,
            published_at=datetime.now(timezone.utc) if not post.scheduled_publish_time else None
        )

    async def _publish_video(self, post: FacebookPost, page_id: str,
                             media_item: FacebookMediaItem,
                             correlation_id: str = None) -> FacebookPublishResult:
        """Publish video post."""
        file_content, file_name, mime_type = await self._prepare_file(media_item.file_path)

        params = {
            "description": post.message,
            "access_token": self.page_access_token
        }

        if post.scheduled_publish_time:
            params["scheduled_publish_time"] = int(post.scheduled_publish_time.timestamp())
            params["published"] = "false"

        # Use resumable upload for large videos
        file_size = len(file_content)

        if file_size > 10 * 1024 * 1024:  # > 10MB
            return await self._resumable_video_upload(
                post, page_id, file_content, file_name, correlation_id
            )

        files = {
            "source": (file_name, file_content, mime_type)
        }

        endpoint = f"/{page_id}/videos"
        if media_item.media_type == MediaType.REEL:
            endpoint = f"/{page_id}/video_reels"

        response = await self._make_api_request(
            endpoint,
            method="POST",
            params=params,
            files=files
        )

        video_id = response.get("id")
        post_url = f"https://www.facebook.com/{video_id}" if video_id else None

        return FacebookPublishResult(
            post_id=video_id,
            status=PostStatus.SCHEDULED if post.scheduled_publish_time else PostStatus.PUBLISHED,
            message="Video published successfully",
            post_url=post_url,
            published_at=datetime.now(timezone.utc) if not post.scheduled_publish_time else None
        )

    async def _upload_photo_unpublished(self, media_item: FacebookMediaItem, page_id: str,
                                         correlation_id: str = None) -> FacebookUploadResult:
        """Upload photo without publishing (for multi-photo posts)."""
        start_time = time.time()

        try:
            file_content, file_name, mime_type = await self._prepare_file(media_item.file_path)

            params = {
                "published": "false",
                "access_token": self.page_access_token
            }

            files = {
                "source": (file_name, file_content, mime_type)
            }

            response = await self._make_api_request(
                f"/{page_id}/photos",
                method="POST",
                params=params,
                files=files
            )

            upload_time = time.time() - start_time

            return FacebookUploadResult(
                media_type=MediaType.PHOTO,
                media_id=response.get("id"),
                upload_time=upload_time
            )

        except Exception as e:
            upload_time = time.time() - start_time
            logger.error(f"Failed to upload photo: {e}", exc_info=True)

            return FacebookUploadResult(
                media_type=MediaType.PHOTO,
                media_id="",
                upload_time=upload_time,
                error_message=str(e)
            )

    async def _resumable_video_upload(self, post: FacebookPost, page_id: str,
                                       file_content: bytes, file_name: str,
                                       correlation_id: str = None) -> FacebookPublishResult:
        """Upload large video using resumable upload."""
        file_size = len(file_content)

        # Step 1: Initialize upload
        init_response = await self._make_api_request(
            f"/{page_id}/videos",
            method="POST",
            params={
                "upload_phase": "start",
                "file_size": file_size,
                "access_token": self.page_access_token
            }
        )

        upload_session_id = init_response.get("upload_session_id")
        video_id = init_response.get("video_id")

        # Step 2: Upload chunks
        chunk_size = 4 * 1024 * 1024  # 4MB chunks
        start_offset = 0

        while start_offset < file_size:
            end_offset = min(start_offset + chunk_size, file_size)
            chunk = file_content[start_offset:end_offset]

            chunk_response = await self._make_api_request(
                f"/{page_id}/videos",
                method="POST",
                params={
                    "upload_phase": "transfer",
                    "upload_session_id": upload_session_id,
                    "start_offset": start_offset,
                    "access_token": self.page_access_token
                },
                files={
                    "video_file_chunk": (file_name, chunk, "application/octet-stream")
                }
            )

            start_offset = int(chunk_response.get("end_offset", end_offset))

        # Step 3: Finish upload
        finish_params = {
            "upload_phase": "finish",
            "upload_session_id": upload_session_id,
            "access_token": self.page_access_token,
            "description": post.message
        }

        if post.scheduled_publish_time:
            finish_params["scheduled_publish_time"] = int(post.scheduled_publish_time.timestamp())
            finish_params["published"] = "false"

        await self._make_api_request(
            f"/{page_id}/videos",
            method="POST",
            params=finish_params
        )

        post_url = f"https://www.facebook.com/{video_id}" if video_id else None

        return FacebookPublishResult(
            post_id=video_id,
            status=PostStatus.SCHEDULED if post.scheduled_publish_time else PostStatus.PUBLISHED,
            message="Video uploaded successfully",
            post_url=post_url,
            published_at=datetime.now(timezone.utc) if not post.scheduled_publish_time else None
        )

    async def _prepare_file(self, file_path: str) -> tuple:
        """Prepare file for upload."""
        if file_path.startswith(('http://', 'https://')):
            async with self.http_client.stream("GET", file_path) as response:
                response.raise_for_status()
                file_content = await response.aread()
                file_name = Path(file_path).name or "media"
        else:
            file_path_obj = Path(file_path)
            if not file_path_obj.exists():
                raise FacebookUploadError(f"File not found: {file_path}")

            file_content = file_path_obj.read_bytes()
            file_name = file_path_obj.name

        mime_type, _ = mimetypes.guess_type(file_name)
        mime_type = mime_type or "application/octet-stream"

        return file_content, file_name, mime_type

    async def _check_rate_limits(self):
        """Check and enforce rate limits."""
        async with self.rate_limit_lock:
            current_time = time.time()
            time_since_last = current_time - self.last_request_time

            if time_since_last < self.min_request_interval:
                await asyncio.sleep(self.min_request_interval - time_since_last)

            self.last_request_time = time.time()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.RequestError, FacebookRateLimitError))
    )
    async def _make_api_request(self, endpoint: str, method: str = "GET",
                                 params: dict[str, Any] = None,
                                 files: dict = None) -> dict[str, Any]:
        """Make API request to Facebook Graph API."""
        await self._check_rate_limits()

        url = f"{self.API_BASE}{endpoint}"

        try:
            if method == "GET":
                response = await self.http_client.get(url, params=params)
            else:
                if files:
                    response = await self.http_client.post(url, data=params, files=files)
                else:
                    response = await self.http_client.post(url, data=params)

            result = response.json()

            if "error" in result:
                await self._handle_api_error(result["error"])

            return result

        except httpx.RequestError as e:
            logger.warning(f"Facebook API request error: {e}")
            raise
        except Exception as e:
            logger.error(f"Facebook API request failed: {e}")
            raise

    async def _handle_api_error(self, error_data: dict[str, Any]):
        """Handle Facebook API errors."""
        error_code = error_data.get("code")
        error_subcode = error_data.get("error_subcode")
        error_msg = error_data.get("message", "Unknown error")

        logger.error(
            "Facebook API error",
            error_code=error_code,
            error_subcode=error_subcode,
            error_message=error_msg
        )

        # Handle specific error codes
        if error_code == 190:  # Invalid OAuth access token
            raise FacebookAuthError(f"Invalid access token: {error_msg}")
        elif error_code == 4:  # Application request limit reached
            raise FacebookRateLimitError(f"Rate limit exceeded: {error_msg}")
        elif error_code == 17:  # User request limit reached
            raise FacebookRateLimitError(f"User rate limit exceeded: {error_msg}")
        elif error_code == 10:  # Permission denied
            raise FacebookAuthError(f"Permission denied: {error_msg}")
        elif error_code == 100:  # Invalid parameter
            raise FacebookValidationError(f"Invalid parameter: {error_msg}")
        elif error_code == 200:  # Permission error
            raise FacebookAuthError(f"Permission error: {error_msg}")
        else:
            raise FacebookError(f"Facebook API Error {error_code}: {error_msg}")

    async def get_page_info(self) -> dict[str, Any]:
        """Get information about the configured page."""
        response = await self._make_api_request(
            f"/{self.page_id}",
            params={
                "fields": "id,name,link,fan_count,followers_count",
                "access_token": self.page_access_token
            }
        )
        return response

    async def delete_post(self, post_id: str) -> bool:
        """Delete a Facebook post."""
        try:
            await self._make_api_request(
                f"/{post_id}",
                method="POST",
                params={
                    "access_token": self.page_access_token,
                    "_method": "delete"
                }
            )
            logger.info("Facebook post deleted", post_id=post_id)
            return True
        except Exception as e:
            logger.error(f"Failed to delete Facebook post: {e}", post_id=post_id)
            return False

    async def close(self):
        """Close HTTP client."""
        await self.http_client.aclose()
        logger.info("Facebook adapter closed")


# Convenience function
async def publish_facebook_post(
    message: str,
    media_files: list[str] = None,
    page_access_token: str = None,
    page_id: str = None,
    scheduled_time: datetime = None,
    correlation_id: str = None
) -> FacebookPublishResult:
    """Publish post to Facebook."""
    adapter = FacebookAdapter(page_access_token, page_id)

    try:
        media_items = []
        if media_files:
            for file_path in media_files:
                file_ext = Path(file_path).suffix.lower()
                if file_ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
                    media_type = MediaType.PHOTO
                elif file_ext in ['.mp4', '.mov', '.avi']:
                    media_type = MediaType.VIDEO
                else:
                    media_type = MediaType.PHOTO

                media_items.append(FacebookMediaItem(
                    file_path=file_path,
                    media_type=media_type
                ))

        post = FacebookPost(
            message=message,
            media_items=media_items,
            page_id=page_id,
            scheduled_publish_time=scheduled_time
        )

        return await adapter.publish_post(post, correlation_id)
    finally:
        await adapter.close()
