"""YouTube API adapter for SalesWhisper Crosspost."""

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..core.logging import get_logger, with_logging_context
from ..observability.metrics import metrics

logger = get_logger("adapters.youtube")


# YouTube API constants
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
YOUTUBE_UPLOAD_BASE = "https://www.googleapis.com/upload/youtube/v3/videos"
YOUTUBE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
YOUTUBE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Required OAuth scopes
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


class YouTubeError(Exception):
    """Base exception for YouTube API errors."""
    pass


class YouTubeAuthError(YouTubeError):
    """Raised when YouTube API authentication fails."""
    pass


class YouTubeRateLimitError(YouTubeError):
    """Raised when YouTube API rate limit is exceeded."""
    pass


class YouTubeQuotaError(YouTubeError):
    """Raised when YouTube API quota is exceeded."""
    pass


class YouTubeUploadError(YouTubeError):
    """Raised when YouTube video upload fails."""
    pass


class YouTubeValidationError(YouTubeError):
    """Raised when YouTube API validation fails."""
    pass


class PrivacyStatus(Enum):
    """YouTube video privacy status."""
    PUBLIC = "public"
    UNLISTED = "unlisted"
    PRIVATE = "private"


class VideoCategory(Enum):
    """YouTube video categories."""
    FILM_ANIMATION = "1"
    AUTOS_VEHICLES = "2"
    MUSIC = "10"
    PETS_ANIMALS = "15"
    SPORTS = "17"
    TRAVEL_EVENTS = "19"
    GAMING = "20"
    PEOPLE_BLOGS = "22"
    COMEDY = "23"
    ENTERTAINMENT = "24"
    NEWS_POLITICS = "25"
    HOWTO_STYLE = "26"
    EDUCATION = "27"
    SCIENCE_TECH = "28"
    NONPROFITS = "29"


class UploadStatus(Enum):
    """YouTube upload status."""
    PENDING = "pending"
    UPLOADING = "uploading"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class YouTubeCredentials:
    """YouTube OAuth credentials."""
    access_token: str
    refresh_token: str | None = None
    token_expires_at: datetime | None = None
    client_id: str | None = None
    client_secret: str | None = None

    def is_expired(self) -> bool:
        """Check if access token is expired."""
        if not self.token_expires_at:
            return False
        return datetime.now(timezone.utc) >= self.token_expires_at


@dataclass
class YouTubeVideo:
    """Represents a YouTube video for upload."""
    file_path: str
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    category_id: str = VideoCategory.ENTERTAINMENT.value
    privacy_status: PrivacyStatus = PrivacyStatus.PUBLIC
    thumbnail_path: str | None = None
    publish_at: datetime | None = None
    is_shorts: bool = False
    playlist_id: str | None = None

    def __post_init__(self):
        # Validate title length
        if len(self.title) > 100:
            self.title = self.title[:97] + "..."

        # Validate description length
        if len(self.description) > 5000:
            self.description = self.description[:4997] + "..."

        # Limit tags
        if len(self.tags) > 500:
            self.tags = self.tags[:500]


@dataclass
class YouTubeUploadResult:
    """Result of YouTube video upload."""
    video_id: str | None
    status: UploadStatus
    message: str
    video_url: str | None = None
    thumbnail_url: str | None = None
    upload_time: float = 0.0
    processing_progress: int = 0
    error_code: str | None = None

    @property
    def watch_url(self) -> str | None:
        """Get YouTube watch URL."""
        if self.video_id:
            return f"https://www.youtube.com/watch?v={self.video_id}"
        return None

    @property
    def shorts_url(self) -> str | None:
        """Get YouTube Shorts URL."""
        if self.video_id:
            return f"https://www.youtube.com/shorts/{self.video_id}"
        return None


@dataclass
class YouTubeChannel:
    """YouTube channel information."""
    channel_id: str
    title: str
    description: str = ""
    subscriber_count: int = 0
    video_count: int = 0
    thumbnail_url: str | None = None


class YouTubeAdapter:
    """YouTube API adapter for video uploading and management."""

    def __init__(self, credentials: YouTubeCredentials = None):
        """Initialize YouTube adapter."""
        self.credentials = credentials or self._load_credentials()

        # HTTP client configuration
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(300.0),  # 5 minutes for uploads
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
            headers={
                "User-Agent": "SalesWhisper-Crosspost/1.0"
            }
        )

        # Rate limiting - YouTube allows ~10,000 quota units per day
        self.daily_quota_used = 0
        self.quota_reset_time = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ) + timedelta(days=1)

        # Upload costs ~1600 quota units
        self.upload_quota_cost = 1600

        logger.info("YouTube adapter initialized")

    def _load_credentials(self) -> YouTubeCredentials:
        """Load YouTube credentials from settings or environment."""
        client_id = os.getenv("YOUTUBE_CLIENT_ID", "")
        client_secret = os.getenv("YOUTUBE_CLIENT_SECRET", "")
        access_token = os.getenv("YOUTUBE_ACCESS_TOKEN", "")
        refresh_token = os.getenv("YOUTUBE_REFRESH_TOKEN", "")

        if not access_token and not refresh_token:
            logger.warning("YouTube credentials not configured")
            return YouTubeCredentials(access_token="")

        return YouTubeCredentials(
            access_token=access_token,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret
        )

    async def _ensure_valid_token(self):
        """Ensure access token is valid, refresh if needed."""
        if not self.credentials.access_token:
            raise YouTubeAuthError("No access token configured")

        if self.credentials.is_expired() and self.credentials.refresh_token:
            await self._refresh_access_token()

    async def _refresh_access_token(self):
        """Refresh the access token using refresh token."""
        if not self.credentials.refresh_token:
            raise YouTubeAuthError("No refresh token available")

        if not self.credentials.client_id or not self.credentials.client_secret:
            raise YouTubeAuthError("Client ID and secret required for token refresh")

        logger.info("Refreshing YouTube access token")

        try:
            response = await self.http_client.post(
                YOUTUBE_TOKEN_URL,
                data={
                    "client_id": self.credentials.client_id,
                    "client_secret": self.credentials.client_secret,
                    "refresh_token": self.credentials.refresh_token,
                    "grant_type": "refresh_token"
                }
            )

            if response.status_code != 200:
                raise YouTubeAuthError(f"Token refresh failed: {response.text}")

            data = response.json()
            self.credentials.access_token = data["access_token"]

            expires_in = data.get("expires_in", 3600)
            self.credentials.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

            logger.info("YouTube access token refreshed successfully")

        except httpx.RequestError as e:
            raise YouTubeAuthError(f"Token refresh request failed: {e}")

    def _get_auth_headers(self) -> dict[str, str]:
        """Get authorization headers."""
        return {
            "Authorization": f"Bearer {self.credentials.access_token}",
            "Accept": "application/json"
        }

    async def _check_quota(self):
        """Check if we have enough quota for an upload."""
        now = datetime.now(timezone.utc)

        # Reset quota if day has passed
        if now >= self.quota_reset_time:
            self.daily_quota_used = 0
            self.quota_reset_time = now.replace(
                hour=0, minute=0, second=0, microsecond=0
            ) + timedelta(days=1)

        # Check if we have enough quota (assuming 10,000 daily limit)
        if self.daily_quota_used + self.upload_quota_cost > 10000:
            raise YouTubeQuotaError(
                f"Daily quota exceeded. Used: {self.daily_quota_used}, "
                f"Resets at: {self.quota_reset_time}"
            )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=60),
        retry=retry_if_exception_type((httpx.RequestError, YouTubeRateLimitError))
    )
    async def upload_video(
        self,
        video: YouTubeVideo,
        correlation_id: str = None
    ) -> YouTubeUploadResult:
        """Upload a video to YouTube."""
        start_time = time.time()

        with with_logging_context(correlation_id=correlation_id):
            logger.info(
                "Starting YouTube video upload",
                title=video.title,
                file_path=video.file_path,
                privacy=video.privacy_status.value,
                is_shorts=video.is_shorts
            )

            try:
                await self._ensure_valid_token()
                await self._check_quota()

                # Validate file exists
                file_path = Path(video.file_path)
                if not file_path.exists():
                    raise YouTubeValidationError(f"Video file not found: {video.file_path}")

                file_size = file_path.stat().st_size

                # Build video metadata
                metadata = self._build_video_metadata(video)

                # Start resumable upload session
                upload_url = await self._init_resumable_upload(metadata, file_size)

                # Upload video file
                video_id = await self._upload_video_file(upload_url, file_path, file_size)

                # Set thumbnail if provided
                if video.thumbnail_path and Path(video.thumbnail_path).exists():
                    await self._set_thumbnail(video_id, video.thumbnail_path)

                # Add to playlist if specified
                if video.playlist_id:
                    await self._add_to_playlist(video_id, video.playlist_id)

                processing_time = time.time() - start_time
                self.daily_quota_used += self.upload_quota_cost

                # Track metrics
                metrics.track_external_api_call(
                    service="youtube",
                    endpoint="upload",
                    status_code=200,
                    duration=processing_time
                )
                metrics.track_post_published("youtube")

                logger.info(
                    "YouTube video uploaded successfully",
                    video_id=video_id,
                    processing_time=processing_time
                )

                return YouTubeUploadResult(
                    video_id=video_id,
                    status=UploadStatus.COMPLETED,
                    message="Video uploaded successfully",
                    video_url=f"https://www.youtube.com/watch?v={video_id}",
                    upload_time=processing_time
                )

            except YouTubeError as e:
                logger.error("YouTube upload failed", error=str(e))
                metrics.track_post_failed("youtube", str(type(e).__name__))
                return YouTubeUploadResult(
                    video_id=None,
                    status=UploadStatus.FAILED,
                    message=str(e),
                    error_code=type(e).__name__,
                    upload_time=time.time() - start_time
                )
            except Exception as e:
                logger.error("Unexpected error during YouTube upload", error=str(e), exc_info=True)
                return YouTubeUploadResult(
                    video_id=None,
                    status=UploadStatus.FAILED,
                    message=f"Unexpected error: {str(e)}",
                    error_code="UnexpectedError",
                    upload_time=time.time() - start_time
                )

    def _build_video_metadata(self, video: YouTubeVideo) -> dict[str, Any]:
        """Build video metadata for YouTube API."""
        metadata = {
            "snippet": {
                "title": video.title,
                "description": video.description,
                "tags": video.tags,
                "categoryId": video.category_id,
                "defaultLanguage": "ru",
                "defaultAudioLanguage": "ru"
            },
            "status": {
                "privacyStatus": video.privacy_status.value,
                "selfDeclaredMadeForKids": False,
                "embeddable": True,
                "publicStatsViewable": True
            }
        }

        # Add scheduled publish time
        if video.publish_at and video.privacy_status == PrivacyStatus.PRIVATE:
            metadata["status"]["publishAt"] = video.publish_at.isoformat()

        # Add Shorts-specific metadata
        if video.is_shorts:
            # YouTube Shorts are identified automatically, but we can add hints
            if "#shorts" not in video.description.lower():
                metadata["snippet"]["description"] += "\n\n#Shorts"

        return metadata

    async def _init_resumable_upload(self, metadata: dict[str, Any], file_size: int) -> str:
        """Initialize a resumable upload session."""
        headers = self._get_auth_headers()
        headers.update({
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(file_size),
            "X-Upload-Content-Type": "video/*"
        })

        params = {
            "uploadType": "resumable",
            "part": "snippet,status"
        }

        response = await self.http_client.post(
            YOUTUBE_UPLOAD_BASE,
            params=params,
            headers=headers,
            json=metadata
        )

        if response.status_code == 401:
            raise YouTubeAuthError("Authentication failed")
        elif response.status_code == 403:
            error_data = response.json()
            if "quotaExceeded" in str(error_data):
                raise YouTubeQuotaError("Daily quota exceeded")
            raise YouTubeAuthError(f"Access denied: {error_data}")
        elif response.status_code != 200:
            raise YouTubeUploadError(f"Failed to init upload: {response.text}")

        upload_url = response.headers.get("Location")
        if not upload_url:
            raise YouTubeUploadError("No upload URL in response")

        return upload_url

    async def _upload_video_file(
        self,
        upload_url: str,
        file_path: Path,
        file_size: int
    ) -> str:
        """Upload video file using resumable upload."""
        chunk_size = 10 * 1024 * 1024  # 10MB chunks

        with open(file_path, "rb") as f:
            uploaded = 0

            while uploaded < file_size:
                # Read chunk
                chunk = f.read(chunk_size)
                if not chunk:
                    break

                chunk_end = min(uploaded + len(chunk), file_size)

                headers = {
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {uploaded}-{chunk_end - 1}/{file_size}"
                }

                response = await self.http_client.put(
                    upload_url,
                    headers=headers,
                    content=chunk
                )

                if response.status_code == 308:
                    # Upload in progress
                    uploaded = chunk_end
                    progress = int(uploaded / file_size * 100)
                    logger.debug(f"Upload progress: {progress}%")

                elif response.status_code in (200, 201):
                    # Upload complete
                    data = response.json()
                    return data["id"]

                elif response.status_code == 401:
                    raise YouTubeAuthError("Authentication failed during upload")

                elif response.status_code == 403:
                    raise YouTubeQuotaError("Quota exceeded during upload")

                else:
                    raise YouTubeUploadError(f"Upload failed: {response.text}")

        raise YouTubeUploadError("Upload completed without video ID")

    async def _set_thumbnail(self, video_id: str, thumbnail_path: str):
        """Set custom thumbnail for video."""
        logger.info(f"Setting thumbnail for video {video_id}")

        headers = self._get_auth_headers()

        with open(thumbnail_path, "rb") as f:
            thumbnail_data = f.read()

        response = await self.http_client.post(
            f"{YOUTUBE_API_BASE}/thumbnails/set",
            params={"videoId": video_id},
            headers=headers,
            content=thumbnail_data,
            headers_update={"Content-Type": "image/png"}
        )

        if response.status_code not in (200, 201):
            logger.warning(f"Failed to set thumbnail: {response.text}")

    async def _add_to_playlist(self, video_id: str, playlist_id: str):
        """Add video to a playlist."""
        logger.info(f"Adding video {video_id} to playlist {playlist_id}")

        headers = self._get_auth_headers()
        headers["Content-Type"] = "application/json"

        body = {
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {
                    "kind": "youtube#video",
                    "videoId": video_id
                }
            }
        }

        response = await self.http_client.post(
            f"{YOUTUBE_API_BASE}/playlistItems",
            params={"part": "snippet"},
            headers=headers,
            json=body
        )

        if response.status_code not in (200, 201):
            logger.warning(f"Failed to add to playlist: {response.text}")

    async def get_channel_info(self) -> YouTubeChannel | None:
        """Get authenticated channel information."""
        await self._ensure_valid_token()

        response = await self.http_client.get(
            f"{YOUTUBE_API_BASE}/channels",
            params={
                "part": "snippet,statistics",
                "mine": "true"
            },
            headers=self._get_auth_headers()
        )

        if response.status_code != 200:
            logger.error(f"Failed to get channel info: {response.text}")
            return None

        data = response.json()
        items = data.get("items", [])

        if not items:
            return None

        channel = items[0]
        snippet = channel.get("snippet", {})
        stats = channel.get("statistics", {})

        return YouTubeChannel(
            channel_id=channel["id"],
            title=snippet.get("title", ""),
            description=snippet.get("description", ""),
            subscriber_count=int(stats.get("subscriberCount", 0)),
            video_count=int(stats.get("videoCount", 0)),
            thumbnail_url=snippet.get("thumbnails", {}).get("default", {}).get("url")
        )

    async def get_video_status(self, video_id: str) -> dict[str, Any]:
        """Get video processing status."""
        await self._ensure_valid_token()

        response = await self.http_client.get(
            f"{YOUTUBE_API_BASE}/videos",
            params={
                "part": "status,processingDetails",
                "id": video_id
            },
            headers=self._get_auth_headers()
        )

        if response.status_code != 200:
            return {"error": response.text}

        data = response.json()
        items = data.get("items", [])

        if not items:
            return {"error": "Video not found"}

        video = items[0]
        return {
            "upload_status": video.get("status", {}).get("uploadStatus"),
            "privacy_status": video.get("status", {}).get("privacyStatus"),
            "processing_status": video.get("processingDetails", {}).get("processingStatus"),
            "processing_progress": video.get("processingDetails", {}).get("processingProgress", {})
        }

    async def delete_video(self, video_id: str) -> bool:
        """Delete a video."""
        await self._ensure_valid_token()

        response = await self.http_client.delete(
            f"{YOUTUBE_API_BASE}/videos",
            params={"id": video_id},
            headers=self._get_auth_headers()
        )

        if response.status_code == 204:
            logger.info(f"Video {video_id} deleted successfully")
            return True

        logger.error(f"Failed to delete video: {response.text}")
        return False

    @staticmethod
    def get_oauth_url(client_id: str, redirect_uri: str, state: str = "") -> str:
        """Generate OAuth authorization URL."""
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(YOUTUBE_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{YOUTUBE_AUTH_URL}?{query}"

    async def exchange_code_for_tokens(
        self,
        code: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str
    ) -> YouTubeCredentials:
        """Exchange authorization code for access tokens."""
        response = await self.http_client.post(
            YOUTUBE_TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code"
            }
        )

        if response.status_code != 200:
            raise YouTubeAuthError(f"Token exchange failed: {response.text}")

        data = response.json()

        expires_in = data.get("expires_in", 3600)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        return YouTubeCredentials(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            token_expires_at=expires_at,
            client_id=client_id,
            client_secret=client_secret
        )

    async def close(self):
        """Close HTTP client."""
        await self.http_client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


# Factory function for creating adapter
def create_youtube_adapter(credentials: YouTubeCredentials = None) -> YouTubeAdapter:
    """Create YouTube adapter instance."""
    return YouTubeAdapter(credentials)


__all__ = [
    "YouTubeAdapter",
    "YouTubeVideo",
    "YouTubeUploadResult",
    "YouTubeCredentials",
    "YouTubeChannel",
    "YouTubeError",
    "YouTubeAuthError",
    "YouTubeQuotaError",
    "YouTubeUploadError",
    "YouTubeValidationError",
    "PrivacyStatus",
    "VideoCategory",
    "UploadStatus",
    "create_youtube_adapter",
]
