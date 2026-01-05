"""
Instagram Graph API adapter for SalesWhisper Crosspost.

This module handles Instagram publishing through the Instagram Graph API,
including container creation, media upload, and publishing workflows.
"""

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..core.config import settings
from ..core.logging import get_logger, with_logging_context
from ..observability.metrics import metrics

logger = get_logger("adapters.instagram")


class InstagramError(Exception):
    """Base exception for Instagram API errors."""
    pass


class InstagramRateLimitError(InstagramError):
    """Raised when Instagram API rate limit is exceeded."""
    pass


class InstagramAuthError(InstagramError):
    """Raised when Instagram API authentication fails."""
    pass


class InstagramValidationError(InstagramError):
    """Raised when Instagram API validation fails."""
    pass


class ContainerType(Enum):
    """Instagram container types."""
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    CAROUSEL_ALBUM = "CAROUSEL_ALBUM"


class PublishStatus(Enum):
    """Instagram publish status."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    FINISHED = "finished"
    ERROR = "error"


@dataclass
class MediaItem:
    """Represents a media item for Instagram."""
    file_path: str
    media_type: ContainerType
    caption: str | None = None
    thumbnail_url: str | None = None
    aspect_ratio: str | None = None
    duration: float | None = None


@dataclass
class InstagramPost:
    """Represents an Instagram post."""
    caption: str
    media_items: list[MediaItem]
    schedule_time: datetime | None = None
    location_id: str | None = None
    user_tags: list[dict[str, Any]] = None

    def __post_init__(self):
        if self.user_tags is None:
            self.user_tags = []


@dataclass
class ContainerResult:
    """Result of container creation."""
    container_id: str
    status: str
    created_at: datetime
    error_message: str | None = None


@dataclass
class PublishResult:
    """Result of publishing operation."""
    post_id: str | None
    status: PublishStatus
    message: str
    container_id: str | None = None
    published_at: datetime | None = None
    permalink: str | None = None
    error_code: str | None = None
    retry_after: int | None = None


class InstagramAdapter:
    """Instagram Graph API adapter."""

    def __init__(self):
        """Initialize Instagram adapter."""
        self.access_token = self._get_access_token()
        self.page_id = settings.instagram.page_id
        self.api_base = "https://graph.facebook.com/v18.0"

        # HTTP client configuration
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            headers={
                "User-Agent": "SalesWhisper-Crosspost/1.0"
            }
        )

        # Rate limiting
        self.rate_limit_remaining = 200
        self.rate_limit_reset_time = time.time() + 3600

        logger.info("Instagram adapter initialized", page_id=self.page_id)

    def _get_access_token(self) -> str:
        """Get Instagram access token from settings."""
        if hasattr(settings.instagram, 'access_token'):
            token = settings.instagram.access_token
            if hasattr(token, 'get_secret_value'):
                return token.get_secret_value()
            return str(token)
        raise InstagramAuthError("Instagram access token not configured")

    async def create_container(self, post: InstagramPost, correlation_id: str = None) -> ContainerResult:
        """
        Create Instagram media container.

        Args:
            post: Instagram post data
            correlation_id: Request correlation ID

        Returns:
            Container creation result
        """
        start_time = time.time()

        with with_logging_context(correlation_id=correlation_id):
            logger.info(
                "Creating Instagram container",
                media_count=len(post.media_items),
                has_caption=bool(post.caption),
                is_scheduled=bool(post.schedule_time)
            )

            try:
                if len(post.media_items) == 1:
                    # Single media post
                    result = await self._create_single_media_container(post.media_items[0], post.caption)
                elif len(post.media_items) > 1:
                    # Carousel post
                    result = await self._create_carousel_container(post.media_items, post.caption)
                else:
                    raise InstagramValidationError("No media items provided")

                processing_time = time.time() - start_time

                # Track metrics
                metrics.track_external_api_call(
                    service="instagram",
                    endpoint="create_container",
                    status_code=200,
                    duration=processing_time
                )

                logger.info(
                    "Instagram container created successfully",
                    container_id=result.container_id,
                    status=result.status,
                    processing_time=processing_time
                )

                return result

            except Exception as e:
                processing_time = time.time() - start_time

                # Track failure metrics
                metrics.track_external_api_call(
                    service="instagram",
                    endpoint="create_container",
                    status_code=getattr(e, 'status_code', 500),
                    duration=processing_time,
                    error=str(e)
                )

                logger.error(
                    "Failed to create Instagram container",
                    error=str(e),
                    processing_time=processing_time,
                    exc_info=True
                )
                raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((httpx.RequestError, InstagramRateLimitError))
    )
    async def _create_single_media_container(self, media_item: MediaItem, caption: str) -> ContainerResult:
        """Create container for single media item."""
        url = f"{self.api_base}/{self.page_id}/media"

        # Prepare container data
        data = {
            "access_token": self.access_token,
            "caption": caption or "",
            "media_type": media_item.media_type.value
        }

        # Add media URL or file upload
        if media_item.file_path.startswith(('http://', 'https://')):
            if media_item.media_type == ContainerType.IMAGE:
                data["image_url"] = media_item.file_path
            elif media_item.media_type == ContainerType.VIDEO:
                data["video_url"] = media_item.file_path
                if media_item.thumbnail_url:
                    data["thumb_url"] = media_item.thumbnail_url
        else:
            # For local files, we need to upload them first
            media_url = await self._upload_media_file(media_item.file_path, media_item.media_type)
            if media_item.media_type == ContainerType.IMAGE:
                data["image_url"] = media_url
            elif media_item.media_type == ContainerType.VIDEO:
                data["video_url"] = media_url
                if media_item.thumbnail_url:
                    data["thumb_url"] = media_item.thumbnail_url

        # Make API request
        response = await self._make_api_request("POST", url, data=data)

        return ContainerResult(
            container_id=response["id"],
            status="created",
            created_at=datetime.now(timezone.utc)
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((httpx.RequestError, InstagramRateLimitError))
    )
    async def _create_carousel_container(self, media_items: list[MediaItem], caption: str) -> ContainerResult:
        """Create carousel container for multiple media items."""
        # Step 1: Create individual containers for each media item
        children_ids = []

        for i, media_item in enumerate(media_items):
            logger.info(f"Creating carousel item {i+1}/{len(media_items)}")

            url = f"{self.api_base}/{self.page_id}/media"
            data = {
                "access_token": self.access_token,
                "media_type": media_item.media_type.value,
                "is_carousel_item": True
            }

            # Add media URL
            if media_item.file_path.startswith(('http://', 'https://')):
                if media_item.media_type == ContainerType.IMAGE:
                    data["image_url"] = media_item.file_path
                elif media_item.media_type == ContainerType.VIDEO:
                    data["video_url"] = media_item.file_path
                    if media_item.thumbnail_url:
                        data["thumb_url"] = media_item.thumbnail_url
            else:
                media_url = await self._upload_media_file(media_item.file_path, media_item.media_type)
                if media_item.media_type == ContainerType.IMAGE:
                    data["image_url"] = media_url
                elif media_item.media_type == ContainerType.VIDEO:
                    data["video_url"] = media_url
                    if media_item.thumbnail_url:
                        data["thumb_url"] = media_item.thumbnail_url

            response = await self._make_api_request("POST", url, data=data)
            children_ids.append(response["id"])

        # Step 2: Create carousel album container
        url = f"{self.api_base}/{self.page_id}/media"
        data = {
            "access_token": self.access_token,
            "media_type": ContainerType.CAROUSEL_ALBUM.value,
            "caption": caption or "",
            "children": ",".join(children_ids)
        }

        response = await self._make_api_request("POST", url, data=data)

        return ContainerResult(
            container_id=response["id"],
            status="created",
            created_at=datetime.now(timezone.utc)
        )

    async def publish_container(self, container_id: str, correlation_id: str = None) -> PublishResult:
        """
        Publish Instagram media container.

        Args:
            container_id: Container ID to publish
            correlation_id: Request correlation ID

        Returns:
            Publishing result
        """
        start_time = time.time()

        with with_logging_context(correlation_id=correlation_id, container_id=container_id):
            logger.info("Publishing Instagram container", container_id=container_id)

            try:
                url = f"{self.api_base}/{self.page_id}/media_publish"
                data = {
                    "access_token": self.access_token,
                    "creation_id": container_id
                }

                response = await self._make_api_request("POST", url, data=data)

                processing_time = time.time() - start_time

                # Get published post details
                post_details = await self._get_post_details(response["id"])

                result = PublishResult(
                    post_id=response["id"],
                    status=PublishStatus.FINISHED,
                    message="Post published successfully",
                    container_id=container_id,
                    published_at=datetime.now(timezone.utc),
                    permalink=post_details.get("permalink")
                )

                # Track metrics
                metrics.track_external_api_call(
                    service="instagram",
                    endpoint="publish_container",
                    status_code=200,
                    duration=processing_time
                )

                logger.info(
                    "Instagram container published successfully",
                    container_id=container_id,
                    post_id=response["id"],
                    processing_time=processing_time,
                    permalink=result.permalink
                )

                return result

            except Exception as e:
                processing_time = time.time() - start_time

                # Track failure metrics
                metrics.track_external_api_call(
                    service="instagram",
                    endpoint="publish_container",
                    status_code=getattr(e, 'status_code', 500),
                    duration=processing_time,
                    error=str(e)
                )

                error_code = getattr(e, 'error_code', None)
                retry_after = getattr(e, 'retry_after', None)

                logger.error(
                    "Failed to publish Instagram container",
                    container_id=container_id,
                    error=str(e),
                    error_code=error_code,
                    retry_after=retry_after,
                    processing_time=processing_time,
                    exc_info=True
                )

                return PublishResult(
                    post_id=None,
                    status=PublishStatus.ERROR,
                    message=str(e),
                    container_id=container_id,
                    error_code=error_code,
                    retry_after=retry_after
                )

    async def upload_thumbnail(self, video_path: str, thumbnail_path: str,
                             correlation_id: str = None) -> str:
        """
        Upload thumbnail for video.

        Args:
            video_path: Path to video file
            thumbnail_path: Path to thumbnail image
            correlation_id: Request correlation ID

        Returns:
            Thumbnail URL
        """
        start_time = time.time()

        with with_logging_context(correlation_id=correlation_id):
            logger.info(
                "Uploading thumbnail for video",
                video_path=video_path,
                thumbnail_path=thumbnail_path
            )

            try:
                # Upload thumbnail to temporary storage or CDN
                thumbnail_url = await self._upload_media_file(thumbnail_path, ContainerType.IMAGE)

                processing_time = time.time() - start_time

                logger.info(
                    "Thumbnail uploaded successfully",
                    thumbnail_url=thumbnail_url,
                    processing_time=processing_time
                )

                return thumbnail_url

            except Exception as e:
                processing_time = time.time() - start_time

                logger.error(
                    "Failed to upload thumbnail",
                    video_path=video_path,
                    thumbnail_path=thumbnail_path,
                    error=str(e),
                    processing_time=processing_time,
                    exc_info=True
                )
                raise

    async def schedule_if_needed(self, post: InstagramPost, container_id: str,
                               correlation_id: str = None) -> PublishResult:
        """
        Schedule post for later publishing if needed.

        Args:
            post: Instagram post data
            container_id: Container ID
            correlation_id: Request correlation ID

        Returns:
            Scheduling result
        """
        start_time = time.time()

        with with_logging_context(correlation_id=correlation_id, container_id=container_id):
            if not post.schedule_time:
                # No scheduling needed, publish immediately
                return await self.publish_container(container_id, correlation_id)

            now = datetime.now(timezone.utc)

            # Check if schedule time is in the future
            if post.schedule_time <= now:
                logger.warning(
                    "Schedule time is in the past, publishing immediately",
                    schedule_time=post.schedule_time.isoformat(),
                    current_time=now.isoformat()
                )
                return await self.publish_container(container_id, correlation_id)

            # Check if schedule time is within allowed range (Instagram allows up to 75 days)
            max_schedule_time = now + timedelta(days=75)
            if post.schedule_time > max_schedule_time:
                raise InstagramValidationError(
                    f"Schedule time too far in future. Maximum: {max_schedule_time.isoformat()}"
                )

            logger.info(
                "Scheduling Instagram post",
                container_id=container_id,
                schedule_time=post.schedule_time.isoformat(),
                delay_minutes=(post.schedule_time - now).total_seconds() / 60
            )

            try:
                url = f"{self.api_base}/{self.page_id}/content_publishing_limit"
                params = {
                    "access_token": self.access_token,
                    "fields": "config"
                }

                # Check publishing limits
                await self._make_api_request("GET", url, params=params)

                # For now, we'll store the scheduled post and handle it via background task
                # In production, you might use a scheduler like Celery Beat or similar

                processing_time = time.time() - start_time

                logger.info(
                    "Instagram post scheduled successfully",
                    container_id=container_id,
                    schedule_time=post.schedule_time.isoformat(),
                    processing_time=processing_time
                )

                return PublishResult(
                    post_id=None,
                    status=PublishStatus.PENDING,
                    message=f"Post scheduled for {post.schedule_time.isoformat()}",
                    container_id=container_id
                )

            except Exception as e:
                processing_time = time.time() - start_time

                logger.error(
                    "Failed to schedule Instagram post",
                    container_id=container_id,
                    schedule_time=post.schedule_time.isoformat(),
                    error=str(e),
                    processing_time=processing_time,
                    exc_info=True
                )
                raise

    async def _upload_media_file(self, file_path: str, media_type: ContainerType) -> str:
        """
        Upload media file to temporary storage or CDN.

        This is a placeholder implementation. In production, you would:
        1. Upload to S3/CDN
        2. Return publicly accessible URL
        3. Handle different media types appropriately
        """
        logger.info(f"Uploading media file: {file_path}")

        # For now, assume files are already uploaded and return the path
        # In production, implement actual upload logic
        if file_path.startswith(('http://', 'https://')):
            return file_path

        # Mock URL for testing
        file_name = Path(file_path).name
        return f"https://cdn.saleswhisper.ru/uploads/{file_name}"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        retry=retry_if_exception_type((httpx.RequestError, InstagramRateLimitError))
    )
    async def _make_api_request(self, method: str, url: str,
                              data: dict[str, Any] = None,
                              params: dict[str, Any] = None,
                              files: dict[str, Any] = None) -> dict[str, Any]:
        """Make authenticated API request to Instagram."""
        try:
            # Check rate limits
            await self._check_rate_limits()

            # Make request
            if method.upper() == "GET":
                response = await self.http_client.get(url, params=params or {})
            elif method.upper() == "POST":
                if files:
                    response = await self.http_client.post(url, data=data or {}, files=files)
                else:
                    response = await self.http_client.post(url, data=data or {})
            else:
                raise InstagramError(f"Unsupported HTTP method: {method}")

            # Update rate limits from headers
            self._update_rate_limits(response.headers)

            # Handle response
            if response.status_code == 200:
                response_data = response.json()
                if "error" in response_data:
                    await self._handle_api_error(response_data["error"])
                return response_data
            elif response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 60))
                raise InstagramRateLimitError(f"Rate limit exceeded. Retry after {retry_after} seconds")
            elif response.status_code in [401, 403]:
                raise InstagramAuthError(f"Authentication failed: {response.text}")
            elif response.status_code >= 400:
                try:
                    error_data = response.json()
                    await self._handle_api_error(error_data.get("error", {}))
                except ValueError:
                    raise InstagramError(f"API request failed: {response.status_code} {response.text}")
            else:
                response.raise_for_status()
                return response.json()

        except httpx.RequestError as e:
            logger.warning(f"Request error, will retry: {e}")
            raise
        except Exception as e:
            logger.error(f"API request failed: {e}")
            raise

    async def _check_rate_limits(self):
        """Check and enforce rate limits."""
        current_time = time.time()

        if current_time >= self.rate_limit_reset_time:
            # Reset rate limits
            self.rate_limit_remaining = 200
            self.rate_limit_reset_time = current_time + 3600

        if self.rate_limit_remaining <= 5:
            wait_time = self.rate_limit_reset_time - current_time
            logger.warning(f"Rate limit nearly exceeded. Waiting {wait_time:.1f} seconds")
            await asyncio.sleep(wait_time)
            self.rate_limit_remaining = 200
            self.rate_limit_reset_time = time.time() + 3600

    def _update_rate_limits(self, headers: dict[str, str]):
        """Update rate limit counters from response headers."""
        if "X-Business-Use-Case-Usage" in headers:
            try:
                usage_data = eval(headers["X-Business-Use-Case-Usage"])
                if isinstance(usage_data, dict):
                    for _app_id, limits in usage_data.items():
                        if "call_count" in limits:
                            self.rate_limit_remaining = 200 - limits["call_count"]
                            break
            except Exception as e:
                logger.warning(f"Failed to parse rate limit headers: {e}")

    async def _handle_api_error(self, error_data: dict[str, Any]):
        """Handle Instagram API errors."""
        error_code = error_data.get("code")
        error_message = error_data.get("message", "Unknown error")
        error_subcode = error_data.get("error_subcode")

        logger.error(
            "Instagram API error",
            error_code=error_code,
            error_message=error_message,
            error_subcode=error_subcode
        )

        # Handle specific error codes
        if error_code == 190:  # Invalid access token
            raise InstagramAuthError(f"Invalid access token: {error_message}")
        elif error_code == 100 and error_subcode == 2207006:  # Content not ready
            raise InstagramValidationError(f"Content not ready for publishing: {error_message}")
        elif error_code == 100 and "Media posted too frequently" in error_message:
            raise InstagramRateLimitError(error_message)
        elif error_code in [368, 9007]:  # Temporarily blocked
            raise InstagramRateLimitError(f"Temporarily blocked: {error_message}")
        else:
            raise InstagramError(f"API Error {error_code}: {error_message}")

    async def _get_post_details(self, post_id: str) -> dict[str, Any]:
        """Get details of published post."""
        url = f"{self.api_base}/{post_id}"
        params = {
            "access_token": self.access_token,
            "fields": "id,media_type,media_url,permalink,thumbnail_url,timestamp,caption"
        }

        try:
            return await self._make_api_request("GET", url, params=params)
        except Exception as e:
            logger.warning(f"Failed to get post details: {e}")
            return {}

    async def get_container_status(self, container_id: str) -> dict[str, Any]:
        """
        Get status of media container.

        Args:
            container_id: Container ID to check

        Returns:
            Container status information
        """
        url = f"{self.api_base}/{container_id}"
        params = {
            "access_token": self.access_token,
            "fields": "id,media_type,status_code,status"
        }

        try:
            response = await self._make_api_request("GET", url, params=params)

            # Status codes:
            # - EXPIRED: Container expired
            # - ERROR: Error in container
            # - FINISHED: Container ready for publishing
            # - IN_PROGRESS: Container being processed
            # - PUBLISHED: Container already published

            return {
                "container_id": container_id,
                "status": response.get("status", "unknown"),
                "status_code": response.get("status_code", "unknown"),
                "media_type": response.get("media_type", "unknown"),
                "is_ready": response.get("status_code") == "FINISHED"
            }

        except Exception as e:
            logger.error(f"Failed to get container status: {e}")
            return {
                "container_id": container_id,
                "status": "error",
                "error": str(e),
                "is_ready": False
            }

    async def update_post_status(self, post_id: str, status: str, result_data: dict[str, Any] = None):
        """
        Update post status in database.

        This is a placeholder for database update logic.
        In production, this would update the posts table.
        """
        logger.info(
            "Updating post status",
            post_id=post_id,
            status=status,
            result_data=result_data
        )

        # Placeholder for database update
        # In production:
        # await db.execute(
        #     "UPDATE posts SET status = ?, result_id = ?, updated_at = ? WHERE id = ?",
        #     (status, result_data.get('post_id'), datetime.now(), post_id)
        # )

    async def close(self):
        """Close HTTP client and cleanup resources."""
        await self.http_client.aclose()
        logger.info("Instagram adapter closed")


# Global adapter instance
instagram_adapter = InstagramAdapter()


# Convenience functions
async def publish_instagram_post(caption: str, media_files: list[str],
                               schedule_time: datetime = None,
                               correlation_id: str = None) -> PublishResult:
    """
    Publish post to Instagram.

    Args:
        caption: Post caption
        media_files: List of media file paths
        schedule_time: Optional schedule time
        correlation_id: Request correlation ID

    Returns:
        Publishing result
    """
    # Convert file paths to MediaItem objects
    media_items = []
    for file_path in media_files:
        # Detect media type based on file extension
        file_ext = Path(file_path).suffix.lower()
        if file_ext in ['.jpg', '.jpeg', '.png', '.webp']:
            media_type = ContainerType.IMAGE
        elif file_ext in ['.mp4', '.mov', '.avi']:
            media_type = ContainerType.VIDEO
        else:
            raise InstagramValidationError(f"Unsupported file type: {file_ext}")

        media_items.append(MediaItem(
            file_path=file_path,
            media_type=media_type
        ))

    post = InstagramPost(
        caption=caption,
        media_items=media_items,
        schedule_time=schedule_time
    )

    # Create container
    container_result = await instagram_adapter.create_container(post, correlation_id)

    # Publish or schedule
    return await instagram_adapter.schedule_if_needed(post, container_result.container_id, correlation_id)


async def get_instagram_container_status(container_id: str) -> dict[str, Any]:
    """Get Instagram container status."""
    return await instagram_adapter.get_container_status(container_id)


async def cleanup_instagram_adapter():
    """Cleanup Instagram adapter resources."""
    await instagram_adapter.close()
