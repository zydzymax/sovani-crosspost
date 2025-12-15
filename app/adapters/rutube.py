"""RuTube API adapter for Crosspost."""

import asyncio
import mimetypes
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from enum import Enum
import hashlib

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from ..core.config import settings
from ..core.logging import get_logger, with_logging_context
from ..observability.metrics import metrics


logger = get_logger("adapters.rutube")


class RuTubeError(Exception):
    """Base exception for RuTube API errors."""
    pass


class RuTubeRateLimitError(RuTubeError):
    """Raised when RuTube API rate limit is exceeded."""
    pass


class RuTubeAuthError(RuTubeError):
    """Raised when RuTube API authentication fails."""
    pass


class RuTubeValidationError(RuTubeError):
    """Raised when RuTube API validation fails."""
    pass


class RuTubeUploadError(RuTubeError):
    """Raised when RuTube upload fails."""
    pass


class VideoStatus(Enum):
    """RuTube video status."""
    PENDING = "pending"
    UPLOADING = "uploading"
    PROCESSING = "processing"
    PUBLISHED = "published"
    SCHEDULED = "scheduled"
    ERROR = "error"


class VideoCategory(Enum):
    """RuTube video categories."""
    AUTO = 1
    GAMES = 2
    HUMOR = 3
    MUSIC = 4
    NEWS = 5
    SCIENCE = 6
    SPORTS = 7
    TRAVEL = 8
    ANIMALS = 9
    FOOD = 10
    BEAUTY = 11
    LIFESTYLE = 12
    EDUCATION = 13
    BUSINESS = 14
    ENTERTAINMENT = 15


@dataclass
class RuTubeVideo:
    """Represents a video for RuTube."""
    file_path: str
    title: str
    description: str = ""
    category: VideoCategory = VideoCategory.ENTERTAINMENT
    is_hidden: bool = False
    is_adult: bool = False
    scheduled_publish_time: Optional[datetime] = None
    tags: List[str] = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


@dataclass
class RuTubePublishResult:
    """Result of RuTube video publishing."""
    video_id: Optional[str]
    status: VideoStatus
    message: str
    video_url: Optional[str] = None
    embed_url: Optional[str] = None
    published_at: Optional[datetime] = None
    error_code: Optional[str] = None


class RuTubeAdapter:
    """RuTube API adapter."""

    API_BASE = "https://rutube.ru/api"

    def __init__(self, api_key: str = None):
        """Initialize RuTube adapter."""
        self.api_key = api_key or self._get_api_key()

        # HTTP client configuration
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(300.0),  # Video uploads can be very slow
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=3),
            headers={
                "User-Agent": "Crosspost/1.0",
                "Authorization": f"Token {self.api_key}"
            }
        )

        # Rate limiting
        self.rate_limit_lock = asyncio.Lock()
        self.last_request_time = 0
        self.min_request_interval = 1.0  # 1 request per second

        logger.info("RuTube adapter initialized")

    def _get_api_key(self) -> str:
        """Get RuTube API key from settings."""
        if hasattr(settings, 'rutube') and hasattr(settings.rutube, 'api_key'):
            token = settings.rutube.api_key
            if hasattr(token, 'get_secret_value'):
                return token.get_secret_value()
            return str(token)

        import os
        token = os.getenv('RUTUBE_API_KEY')
        if token:
            return token

        raise RuTubeAuthError("RuTube API key not configured. Set RUTUBE_API_KEY environment variable.")

    async def upload_video(self, video: RuTubeVideo, correlation_id: str = None) -> RuTubePublishResult:
        """Upload video to RuTube."""
        start_time = time.time()

        with with_logging_context(correlation_id=correlation_id):
            logger.info(
                "Uploading video to RuTube",
                title=video.title,
                file_path=video.file_path,
                category=video.category.name
            )

            try:
                # Step 1: Get upload URL
                upload_info = await self._init_upload(video)

                # Step 2: Upload video file
                await self._upload_file(upload_info, video.file_path)

                # Step 3: Create video entry
                result = await self._create_video(upload_info, video)

                processing_time = time.time() - start_time

                metrics.track_external_api_call(
                    service="rutube",
                    endpoint="upload_video",
                    status_code=200,
                    duration=processing_time
                )

                logger.info(
                    "RuTube video uploaded successfully",
                    video_id=result.video_id,
                    processing_time=processing_time,
                    video_url=result.video_url
                )

                return result

            except Exception as e:
                processing_time = time.time() - start_time

                metrics.track_external_api_call(
                    service="rutube",
                    endpoint="upload_video",
                    status_code=500,
                    duration=processing_time,
                    error=str(e)
                )

                logger.error(
                    "Failed to upload RuTube video",
                    error=str(e),
                    processing_time=processing_time,
                    exc_info=True
                )

                return RuTubePublishResult(
                    video_id=None,
                    status=VideoStatus.ERROR,
                    message=str(e),
                    error_code=getattr(e, 'error_code', None)
                )

    async def _init_upload(self, video: RuTubeVideo) -> Dict[str, Any]:
        """Initialize upload session and get upload URL."""
        file_path = Path(video.file_path)

        if video.file_path.startswith(('http://', 'https://')):
            # Download file first to get size and checksum
            async with self.http_client.stream("GET", video.file_path) as response:
                response.raise_for_status()
                file_content = await response.aread()
                file_size = len(file_content)
                file_name = file_path.name or "video.mp4"
        else:
            if not file_path.exists():
                raise RuTubeUploadError(f"File not found: {video.file_path}")
            file_size = file_path.stat().st_size
            file_name = file_path.name
            file_content = None

        # Calculate MD5 hash for the file
        if file_content:
            file_hash = hashlib.md5(file_content).hexdigest()
        else:
            file_hash = await self._calculate_file_hash(video.file_path)

        # Initialize upload
        response = await self._make_api_request(
            "/video/upload/",
            method="POST",
            json_data={
                "filename": file_name,
                "filesize": file_size,
                "md5": file_hash
            }
        )

        return {
            "upload_url": response.get("upload_url"),
            "video_id": response.get("video_id"),
            "file_size": file_size,
            "file_name": file_name,
            "file_content": file_content
        }

    async def _calculate_file_hash(self, file_path: str) -> str:
        """Calculate MD5 hash of a file."""
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()

    async def _upload_file(self, upload_info: Dict[str, Any], file_path: str):
        """Upload file to RuTube upload server."""
        upload_url = upload_info["upload_url"]
        file_content = upload_info.get("file_content")
        file_name = upload_info["file_name"]

        if file_content is None:
            file_content = Path(file_path).read_bytes()

        mime_type, _ = mimetypes.guess_type(file_name)
        mime_type = mime_type or "video/mp4"

        # Upload using multipart form
        files = {
            "file": (file_name, file_content, mime_type)
        }

        response = await self.http_client.post(
            upload_url,
            files=files,
            timeout=httpx.Timeout(600.0)  # 10 minutes for large files
        )

        if response.status_code not in [200, 201, 204]:
            raise RuTubeUploadError(f"Upload failed with status {response.status_code}: {response.text}")

        logger.info("File uploaded to RuTube successfully")

    async def _create_video(self, upload_info: Dict[str, Any], video: RuTubeVideo) -> RuTubePublishResult:
        """Create video entry with metadata."""
        video_id = upload_info["video_id"]

        # Update video metadata
        metadata = {
            "title": video.title[:100],  # Max 100 chars
            "description": video.description[:5000],  # Max 5000 chars
            "category": video.category.value,
            "is_hidden": video.is_hidden,
            "is_adult": video.is_adult
        }

        if video.tags:
            metadata["tags"] = ",".join(video.tags[:20])  # Max 20 tags

        if video.scheduled_publish_time:
            metadata["publication_ts"] = int(video.scheduled_publish_time.timestamp())

        response = await self._make_api_request(
            f"/video/{video_id}/",
            method="PATCH",
            json_data=metadata
        )

        video_url = f"https://rutube.ru/video/{video_id}/"
        embed_url = f"https://rutube.ru/play/embed/{video_id}/"

        status = VideoStatus.SCHEDULED if video.scheduled_publish_time else VideoStatus.PROCESSING

        return RuTubePublishResult(
            video_id=video_id,
            status=status,
            message="Video uploaded and processing",
            video_url=video_url,
            embed_url=embed_url,
            published_at=None  # Will be set when processing completes
        )

    async def get_video_status(self, video_id: str) -> Dict[str, Any]:
        """Get video processing status."""
        response = await self._make_api_request(
            f"/video/{video_id}/",
            method="GET"
        )

        return {
            "id": response.get("id"),
            "title": response.get("title"),
            "status": response.get("publication_status"),
            "is_hidden": response.get("is_hidden"),
            "created_ts": response.get("created_ts"),
            "publication_ts": response.get("publication_ts"),
            "duration": response.get("duration"),
            "hits": response.get("hits"),
            "video_url": response.get("video_url"),
            "embed_url": response.get("embed_url"),
            "thumbnail_url": response.get("thumbnail_url")
        }

    async def delete_video(self, video_id: str) -> bool:
        """Delete a video from RuTube."""
        try:
            await self._make_api_request(
                f"/video/{video_id}/",
                method="DELETE"
            )
            logger.info("RuTube video deleted", video_id=video_id)
            return True
        except Exception as e:
            logger.error(f"Failed to delete RuTube video: {e}", video_id=video_id)
            return False

    async def get_channel_videos(self, limit: int = 20, offset: int = 0) -> Dict[str, Any]:
        """Get list of channel videos."""
        response = await self._make_api_request(
            "/video/person/",
            method="GET",
            params={
                "limit": limit,
                "offset": offset
            }
        )

        return {
            "count": response.get("count", 0),
            "results": [
                {
                    "id": v.get("id"),
                    "title": v.get("title"),
                    "status": v.get("publication_status"),
                    "created_ts": v.get("created_ts"),
                    "hits": v.get("hits"),
                    "thumbnail_url": v.get("thumbnail_url")
                }
                for v in response.get("results", [])
            ]
        }

    async def update_video(self, video_id: str, title: str = None, description: str = None,
                           category: VideoCategory = None, is_hidden: bool = None) -> bool:
        """Update video metadata."""
        try:
            metadata = {}
            if title is not None:
                metadata["title"] = title[:100]
            if description is not None:
                metadata["description"] = description[:5000]
            if category is not None:
                metadata["category"] = category.value
            if is_hidden is not None:
                metadata["is_hidden"] = is_hidden

            if not metadata:
                return True

            await self._make_api_request(
                f"/video/{video_id}/",
                method="PATCH",
                json_data=metadata
            )
            logger.info("RuTube video updated", video_id=video_id)
            return True
        except Exception as e:
            logger.error(f"Failed to update RuTube video: {e}", video_id=video_id)
            return False

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
        retry=retry_if_exception_type((httpx.RequestError, RuTubeRateLimitError))
    )
    async def _make_api_request(self, endpoint: str, method: str = "GET",
                                 params: Dict[str, Any] = None,
                                 json_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Make API request to RuTube."""
        await self._check_rate_limits()

        url = f"{self.API_BASE}{endpoint}"

        try:
            if method == "GET":
                response = await self.http_client.get(url, params=params)
            elif method == "POST":
                response = await self.http_client.post(url, json=json_data)
            elif method == "PATCH":
                response = await self.http_client.patch(url, json=json_data)
            elif method == "DELETE":
                response = await self.http_client.delete(url)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            # Handle errors
            if response.status_code == 401:
                raise RuTubeAuthError("Authentication failed")
            elif response.status_code == 429:
                raise RuTubeRateLimitError("Rate limit exceeded")
            elif response.status_code >= 400:
                error_msg = response.text
                try:
                    error_data = response.json()
                    error_msg = error_data.get("detail", error_data.get("error", str(error_data)))
                except:
                    pass
                raise RuTubeError(f"API Error {response.status_code}: {error_msg}")

            if response.status_code == 204:
                return {}

            return response.json()

        except httpx.RequestError as e:
            logger.warning(f"RuTube API request error: {e}")
            raise
        except Exception as e:
            logger.error(f"RuTube API request failed: {e}")
            raise

    async def close(self):
        """Close HTTP client."""
        await self.http_client.aclose()
        logger.info("RuTube adapter closed")


# Convenience function
async def upload_rutube_video(
    file_path: str,
    title: str,
    description: str = "",
    category: VideoCategory = VideoCategory.ENTERTAINMENT,
    tags: List[str] = None,
    is_hidden: bool = False,
    scheduled_time: datetime = None,
    api_key: str = None,
    correlation_id: str = None
) -> RuTubePublishResult:
    """Upload video to RuTube."""
    adapter = RuTubeAdapter(api_key)

    try:
        video = RuTubeVideo(
            file_path=file_path,
            title=title,
            description=description,
            category=category,
            tags=tags or [],
            is_hidden=is_hidden,
            scheduled_publish_time=scheduled_time
        )

        return await adapter.upload_video(video, correlation_id)
    finally:
        await adapter.close()
