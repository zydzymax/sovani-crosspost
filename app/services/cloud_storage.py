"""
Unified Cloud Storage Service for SalesWhisper Crosspost.

Provides a unified interface for working with different cloud storage providers:
- Google Drive
- Yandex Disk

Users share folders with specific structure (videos/, photos/) and the service
syncs media files automatically.
"""

import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from ..adapters.google_drive import (
    CloudFile,
    GoogleDriveAdapter,
    MediaType,
    SyncResult,
    exchange_google_code,
    get_google_oauth_url,
    google_drive,
    refresh_google_token,
)
from ..adapters.yandex_disk import (
    YandexDiskAdapter,
    exchange_yandex_code,
    get_yandex_oauth_url,
    refresh_yandex_token,
    yandex_disk,
)
from ..core.logging import get_logger

logger = get_logger("services.cloud_storage")


class CloudProvider(str, Enum):
    """Supported cloud storage providers."""

    GOOGLE_DRIVE = "google_drive"
    YANDEX_DISK = "yandex_disk"


class ConnectionStatus(str, Enum):
    """Cloud connection status."""

    PENDING = "pending"  # Waiting for OAuth
    ACTIVE = "active"  # Connected and working
    ERROR = "error"  # Connection error
    EXPIRED = "expired"  # Token expired
    DISCONNECTED = "disconnected"  # User disconnected


@dataclass
class CloudConnection:
    """Represents a user's cloud storage connection."""

    id: str
    user_id: str
    provider: CloudProvider
    folder_id: str  # Google Drive folder ID or Yandex Disk path
    folder_name: str
    status: ConnectionStatus
    access_token: str | None = None
    refresh_token: str | None = None
    token_expires_at: datetime | None = None
    public_url: str | None = None  # For public folders (Yandex)
    last_sync_at: datetime | None = None
    files_synced: int = 0
    error_message: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class CloudFolderInfo:
    """Information about a cloud folder."""

    provider: CloudProvider
    folder_id: str
    folder_name: str
    has_videos_folder: bool
    has_photos_folder: bool
    total_files: int
    video_files: int
    photo_files: int
    total_size_bytes: int


class CloudStorageService:
    """
    Unified cloud storage service.

    Provides single interface for:
    - Connecting cloud storage accounts
    - Listing media in folders
    - Syncing files locally
    - Managing OAuth tokens
    """

    def __init__(self):
        """Initialize cloud storage service."""
        self.google_drive = google_drive
        self.yandex_disk = yandex_disk
        self._local_storage_path = os.getenv("CLOUD_SYNC_PATH", "/app/data/cloud_media")
        logger.info("CloudStorageService initialized")

    @staticmethod
    def _require_provider(provider: CloudProvider, supported: tuple[CloudProvider, ...]) -> None:
        if provider not in supported:
            raise ValueError(f"Unsupported provider: {provider}")

    @staticmethod
    def _get_access_token(credentials: dict[str, Any] | None) -> str | None:
        if not credentials:
            return None
        token = credentials.get("access_token")
        if not token:
            return None
        return str(token)

    @staticmethod
    def _sync_error_result(error: str, files_failed: int = 0) -> SyncResult:
        return SyncResult(
            success=False,
            files_found=0,
            files_downloaded=0,
            files_failed=files_failed,
            errors=[error],
            downloaded_files=[],
        )

    # ==================== OAuth URLs ====================

    def get_oauth_url(self, provider: CloudProvider, redirect_uri: str, state: str | None = None) -> str:
        """
        Get OAuth authorization URL for provider.

        Args:
            provider: Cloud provider
            redirect_uri: OAuth redirect URI
            state: Optional state parameter

        Returns:
            OAuth authorization URL
        """
        oauth_url_handlers = {
            CloudProvider.GOOGLE_DRIVE: get_google_oauth_url,
            CloudProvider.YANDEX_DISK: get_yandex_oauth_url,
        }
        handler = oauth_url_handlers.get(provider)
        if handler is None:
            raise ValueError(f"Unsupported provider: {provider}")
        return handler(redirect_uri, state)

    async def exchange_oauth_code(self, provider: CloudProvider, code: str, redirect_uri: str) -> dict[str, Any] | None:
        """
        Exchange OAuth code for tokens.

        Args:
            provider: Cloud provider
            code: Authorization code
            redirect_uri: OAuth redirect URI

        Returns:
            Token data or None
        """
        exchange_handlers = {
            CloudProvider.GOOGLE_DRIVE: exchange_google_code,
            CloudProvider.YANDEX_DISK: exchange_yandex_code,
        }
        handler = exchange_handlers.get(provider)
        if handler is None:
            raise ValueError(f"Unsupported provider: {provider}")
        return await handler(code, redirect_uri)

    async def refresh_token(self, provider: CloudProvider, refresh_token: str) -> dict[str, Any] | None:
        """
        Refresh expired access token.

        Args:
            provider: Cloud provider
            refresh_token: Refresh token

        Returns:
            New token data or None
        """
        refresh_handlers = {
            CloudProvider.GOOGLE_DRIVE: refresh_google_token,
            CloudProvider.YANDEX_DISK: refresh_yandex_token,
        }
        handler = refresh_handlers.get(provider)
        if handler is None:
            raise ValueError(f"Unsupported provider: {provider}")
        return await handler(refresh_token)

    # ==================== Folder Operations ====================

    def extract_folder_id(self, provider: CloudProvider, url_or_id: str) -> str | None:
        """
        Extract folder ID from sharing URL or return ID as-is.

        Args:
            provider: Cloud provider
            url_or_id: Sharing URL or folder ID

        Returns:
            Folder ID or None
        """
        extractors = {
            CloudProvider.GOOGLE_DRIVE: (
                "drive.google.com",
                GoogleDriveAdapter.extract_folder_id_from_url,
            ),
            CloudProvider.YANDEX_DISK: (
                "disk.yandex",
                YandexDiskAdapter.extract_public_key_from_url,
            ),
        }
        extractor_data = extractors.get(provider)
        if extractor_data is None:
            return None
        host_marker, extractor = extractor_data
        if host_marker in url_or_id:
            return extractor(url_or_id)
        return url_or_id

    async def get_folder_info(
        self,
        provider: CloudProvider,
        folder_id: str,
        credentials: dict[str, Any] | None = None,
        public_url: str | None = None,
    ) -> CloudFolderInfo | None:
        """
        Get information about a cloud folder.

        Args:
            provider: Cloud provider
            folder_id: Folder ID or path
            credentials: OAuth credentials (for private folders)
            public_url: Public sharing URL (for Yandex public folders)

        Returns:
            CloudFolderInfo or None
        """
        try:
            if provider == CloudProvider.GOOGLE_DRIVE:
                if not credentials:
                    logger.error("Google Drive requires credentials")
                    return None

                # Get folder info
                folder_data = await self.google_drive.get_folder_info(folder_id, credentials)
                if not folder_data:
                    return None

                # List contents
                all_files = await self.google_drive.list_folder_contents(folder_id, credentials)

                video_count = sum(1 for f in all_files if f.media_type == MediaType.VIDEO)
                photo_count = sum(1 for f in all_files if f.media_type == MediaType.IMAGE)
                total_size = sum(f.size for f in all_files)

                return CloudFolderInfo(
                    provider=provider,
                    folder_id=folder_id,
                    folder_name=folder_data.get("name", "Unknown"),
                    has_videos_folder=True,  # Google Drive - flat structure OK
                    has_photos_folder=True,
                    total_files=len(all_files),
                    video_files=video_count,
                    photo_files=photo_count,
                    total_size_bytes=total_size,
                )

            elif provider == CloudProvider.YANDEX_DISK:
                if public_url:
                    # Public folder
                    files = await self.yandex_disk.list_public_folder(public_url, "")
                    folder_name = "Public Folder"
                elif credentials:
                    # Private folder
                    access_token = self._get_access_token(credentials)
                    if not access_token:
                        logger.error("Yandex Disk credentials missing access_token")
                        return None
                    files = await self.yandex_disk.list_folder_contents(folder_id, access_token)
                    folder_name = Path(folder_id).name or folder_id
                else:
                    logger.error("Yandex Disk requires credentials or public URL")
                    return None

                video_count = sum(1 for f in files if f.media_type == MediaType.VIDEO)
                photo_count = sum(1 for f in files if f.media_type == MediaType.IMAGE)
                total_size = sum(f.size for f in files)

                # Check for standard folder structure
                folder_names = {Path(f.path).parts[1] if len(Path(f.path).parts) > 1 else "" for f in files}
                has_videos = "videos" in folder_names or "video" in folder_names
                has_photos = "photos" in folder_names or "photo" in folder_names

                return CloudFolderInfo(
                    provider=provider,
                    folder_id=folder_id,
                    folder_name=folder_name,
                    has_videos_folder=has_videos or video_count > 0,
                    has_photos_folder=has_photos or photo_count > 0,
                    total_files=len(files),
                    video_files=video_count,
                    photo_files=photo_count,
                    total_size_bytes=total_size,
                )

            return None

        except Exception:
            logger.exception("Failed to get folder info")
            return None

    async def list_media_files(
        self,
        provider: CloudProvider,
        folder_id: str,
        credentials: dict[str, Any] | None = None,
        public_url: str | None = None,
        media_types: list[str] | None = None,
    ) -> list[CloudFile]:
        """
        List all media files in a cloud folder.

        Args:
            provider: Cloud provider
            folder_id: Folder ID or path
            credentials: OAuth credentials
            public_url: Public sharing URL
            media_types: Filter by types ['video', 'image']

        Returns:
            List of CloudFile objects
        """
        try:
            if provider == CloudProvider.GOOGLE_DRIVE:
                if not credentials:
                    return []
                return await self.google_drive.list_folder_contents(folder_id, credentials, media_types)

            elif provider == CloudProvider.YANDEX_DISK:
                if public_url:
                    return await self.yandex_disk.list_public_folder(public_url, "", media_types)
                elif credentials:
                    access_token = self._get_access_token(credentials)
                    if not access_token:
                        logger.error("Yandex Disk credentials missing access_token")
                        return []
                    return await self.yandex_disk.list_folder_contents(folder_id, access_token, media_types)

            return []

        except Exception:
            logger.exception("Failed to list media files")
            return []

    # ==================== Sync Operations ====================

    async def sync_folder(
        self,
        provider: CloudProvider,
        folder_id: str,
        user_id: str,
        credentials: dict[str, Any] | None = None,
        public_url: str | None = None,
        media_types: list[str] | None = None,
    ) -> SyncResult:
        """
        Sync all media from a cloud folder to local storage.

        Args:
            provider: Cloud provider
            folder_id: Folder ID or path
            user_id: User ID for local storage path
            credentials: OAuth credentials
            public_url: Public sharing URL
            media_types: Filter by types

        Returns:
            SyncResult with statistics
        """
        # Create user's local storage directory
        output_dir = os.path.join(self._local_storage_path, user_id)
        os.makedirs(output_dir, exist_ok=True)

        try:
            if provider == CloudProvider.GOOGLE_DRIVE:
                if not credentials:
                    return self._sync_error_result("Google Drive requires credentials")
                return await self.google_drive.sync_folder(folder_id, credentials, output_dir, media_types)

            elif provider == CloudProvider.YANDEX_DISK:
                if public_url:
                    return await self.yandex_disk.sync_public_folder(public_url, output_dir, media_types)
                elif credentials:
                    access_token = self._get_access_token(credentials)
                    if not access_token:
                        return self._sync_error_result("Yandex Disk credentials missing access_token")
                    return await self.yandex_disk.sync_folder(folder_id, access_token, output_dir, media_types)

            return self._sync_error_result(f"Unsupported provider: {provider}")

        except Exception as e:
            logger.exception("Cloud folder sync failed")
            return self._sync_error_result(str(e), files_failed=1)

    async def download_file(
        self,
        provider: CloudProvider,
        file_id: str,
        output_path: str,
        credentials: dict[str, Any] | None = None,
        download_url: str | None = None,
    ) -> bool:
        """
        Download a single file from cloud storage.

        Args:
            provider: Cloud provider
            file_id: File ID or path
            output_path: Local output path
            credentials: OAuth credentials
            download_url: Direct download URL

        Returns:
            True if successful
        """
        try:
            if provider == CloudProvider.GOOGLE_DRIVE:
                if not credentials:
                    return False
                return await self.google_drive.download_file(file_id, credentials, output_path)

            elif provider == CloudProvider.YANDEX_DISK:
                if download_url:
                    return await self.yandex_disk.download_public_file(download_url, output_path)
                elif credentials:
                    access_token = self._get_access_token(credentials)
                    if not access_token:
                        logger.error("Yandex Disk credentials missing access_token")
                        return False
                    return await self.yandex_disk.download_file(file_id, access_token, output_path)

            return False

        except Exception:
            logger.exception("Cloud file download failed")
            return False

    # ==================== Validation ====================

    async def validate_connection(
        self,
        provider: CloudProvider,
        folder_id: str,
        credentials: dict[str, Any] | None = None,
        public_url: str | None = None,
    ) -> tuple[bool, str | None]:
        """
        Validate that a cloud connection is working.

        Args:
            provider: Cloud provider
            folder_id: Folder ID or path
            credentials: OAuth credentials
            public_url: Public sharing URL

        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            info = await self.get_folder_info(provider, folder_id, credentials, public_url)

            if not info:
                return False, "Cannot access folder"

            if info.total_files == 0:
                return True, "Folder is empty (no media files found)"

            if not info.has_videos_folder and not info.has_photos_folder:
                return True, "No videos/ or photos/ subfolders (all files will be synced)"

            return True, None

        except Exception as e:
            logger.exception("Cloud connection validation failed")
            return False, str(e)

    def get_expected_folder_structure(self) -> dict[str, Any]:
        """Get expected folder structure for user guidance."""
        return {
            "description": "Create folders with this structure in your cloud storage",
            "structure": {
                "your_folder/": {
                    "videos/": "Place your video files here (.mp4, .mov, .avi)",
                    "photos/": "Place your image files here (.jpg, .png, .webp)",
                }
            },
            "supported_video_formats": [".mp4", ".mov", ".avi", ".mkv", ".webm"],
            "supported_image_formats": [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"],
            "tips": [
                "Files will be automatically adapted for each social platform",
                "Original files are never modified",
                "Sync happens automatically every hour (or on-demand)",
                "Files are processed with smart cropping to avoid black bars",
            ],
        }


# Global service instance
cloud_storage_service = CloudStorageService()
