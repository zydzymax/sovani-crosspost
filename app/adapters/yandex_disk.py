"""
Yandex Disk Adapter for SalesWhisper Crosspost.

Provides integration with Yandex Disk for fetching user media files.
Users share a folder with the service and media is automatically synced.
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from ..core.logging import get_logger

logger = get_logger("adapters.yandex_disk")


# Import shared types from google_drive
from .google_drive import CloudFile, MediaType, SyncResult


class YandexDiskAdapter:
    """
    Yandex Disk integration adapter.

    Uses Yandex Disk REST API for:
    - Listing files in shared folders
    - Downloading media files
    - Public folder access via sharing links
    """

    API_BASE = "https://cloud-api.yandex.net/v1/disk"
    OAUTH_URL = "https://oauth.yandex.ru/authorize"
    TOKEN_URL = "https://oauth.yandex.ru/token"

    # Supported extensions
    VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".wmv"}
    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"}

    def __init__(self):
        """Initialize Yandex Disk adapter."""
        self.client_id = os.getenv("YANDEX_CLIENT_ID")
        self.client_secret = os.getenv("YANDEX_CLIENT_SECRET")
        logger.info("YandexDiskAdapter initialized")

    def _classify_file(self, filename: str, mime_type: str = None) -> MediaType:
        """Classify file as video, image, or unknown."""
        ext = Path(filename).suffix.lower()

        if ext in self.VIDEO_EXTENSIONS:
            return MediaType.VIDEO
        if ext in self.IMAGE_EXTENSIONS:
            return MediaType.IMAGE

        # Fallback to MIME type
        if mime_type:
            if mime_type.startswith("video/"):
                return MediaType.VIDEO
            elif mime_type.startswith("image/"):
                return MediaType.IMAGE

        return MediaType.UNKNOWN

    @staticmethod
    def _target_dir_for_media_type(media_type: MediaType, videos_dir: str, photos_dir: str) -> str | None:
        if media_type == MediaType.VIDEO:
            return videos_dir
        if media_type == MediaType.IMAGE:
            return photos_dir
        return None

    @staticmethod
    def _should_include_media_type(media_type: MediaType, media_types: list[str] | None) -> bool:
        if media_type == MediaType.UNKNOWN:
            return False
        if not media_types:
            return True
        if media_type == MediaType.VIDEO:
            return "video" in media_types
        if media_type == MediaType.IMAGE:
            return "image" in media_types
        return False

    async def _sync_files(
        self,
        files: list[CloudFile],
        output_dir: str,
        download_func,
    ) -> SyncResult:
        errors = []
        downloaded = []

        if not files:
            return SyncResult(
                success=True,
                files_found=0,
                files_downloaded=0,
                files_failed=0,
                errors=[],
                downloaded_files=[],
            )

        videos_dir = os.path.join(output_dir, "videos")
        photos_dir = os.path.join(output_dir, "photos")
        os.makedirs(videos_dir, exist_ok=True)
        os.makedirs(photos_dir, exist_ok=True)

        files_downloaded = 0
        files_failed = 0

        for cloud_file in files:
            try:
                target_dir = self._target_dir_for_media_type(cloud_file.media_type, videos_dir, photos_dir)
                if target_dir is None:
                    continue

                output_path = os.path.join(target_dir, cloud_file.name)

                if os.path.exists(output_path) and os.path.getsize(output_path) == cloud_file.size:
                    logger.debug("Skipping existing file with matching size", file_name=cloud_file.name)
                    continue

                success = await download_func(cloud_file, output_path)
                if success:
                    files_downloaded += 1
                    downloaded.append(output_path)
                else:
                    files_failed += 1
                    errors.append(f"Failed to download: {cloud_file.name}")
            except Exception as e:
                files_failed += 1
                errors.append(f"Error processing {cloud_file.name}: {str(e)}")

        return SyncResult(
            success=files_failed == 0,
            files_found=len(files),
            files_downloaded=files_downloaded,
            files_failed=files_failed,
            errors=errors,
            downloaded_files=downloaded,
        )

    async def list_folder_contents(
        self, folder_path: str, access_token: str, media_types: list[str] = None
    ) -> list[CloudFile]:
        """
        List all media files in a Yandex Disk folder.

        Args:
            folder_path: Path to folder (e.g., "/Crosspost/videos")
            access_token: Yandex OAuth token
            media_types: Filter by media types ['video', 'image']

        Returns:
            List of CloudFile objects
        """
        files = []

        try:
            async with httpx.AsyncClient() as client:
                # Get folder contents
                offset = 0
                limit = 100

                while True:
                    response = await client.get(
                        f"{self.API_BASE}/resources",
                        params={
                            "path": folder_path,
                            "limit": limit,
                            "offset": offset,
                            "fields": "_embedded.items.name,_embedded.items.path,_embedded.items.size,_embedded.items.mime_type,_embedded.items.modified,_embedded.items.preview,_embedded.items.type",
                        },
                        headers={"Authorization": f"OAuth {access_token}"},
                        timeout=30,
                    )

                    if response.status_code != 200:
                        logger.error(
                            "Failed to list folder",
                            folder_path=folder_path,
                            status_code=response.status_code,
                            response_text=response.text,
                        )
                        break

                    data = response.json()
                    items = data.get("_embedded", {}).get("items", [])

                    if not items:
                        break

                    for item in items:
                        # Skip folders
                        if item.get("type") == "dir":
                            continue

                        media_type = self._classify_file(item.get("name", ""), item.get("mime_type"))

                        if not self._should_include_media_type(media_type, media_types):
                            continue

                        # Parse modified time
                        modified_str = item.get("modified", "")
                        try:
                            modified_at = datetime.fromisoformat(modified_str.replace("Z", "+00:00"))
                        except ValueError:
                            modified_at = datetime.utcnow()

                        cloud_file = CloudFile(
                            id=item.get("path", ""),
                            name=item.get("name", ""),
                            path=item.get("path", ""),
                            size=item.get("size", 0),
                            mime_type=item.get("mime_type", ""),
                            media_type=media_type,
                            modified_at=modified_at,
                            thumbnail_url=item.get("preview"),
                        )
                        files.append(cloud_file)

                    offset += limit

                    # Check if there are more items
                    total = data.get("_embedded", {}).get("total", 0)
                    if offset >= total:
                        break

            logger.info("Found media files in folder", files_count=len(files), folder_path=folder_path)
            return files

        except Exception:
            logger.exception("Failed to list folder", folder_path=folder_path)
            return []

    async def list_public_folder(
        self, public_key: str, path: str = "/", media_types: list[str] = None
    ) -> list[CloudFile]:
        """
        List contents of a public shared folder.

        Args:
            public_key: Public sharing key (from URL)
            path: Path within the shared folder
            media_types: Filter by media types

        Returns:
            List of CloudFile objects
        """
        files = []

        try:
            async with httpx.AsyncClient() as client:
                offset = 0
                limit = 100

                while True:
                    response = await client.get(
                        f"{self.API_BASE}/public/resources",
                        params={
                            "public_key": public_key,
                            "path": path,
                            "limit": limit,
                            "offset": offset,
                        },
                        timeout=30,
                    )

                    if response.status_code != 200:
                        logger.error(
                            "Failed to list public folder",
                            status_code=response.status_code,
                            response_text=response.text,
                        )
                        break

                    data = response.json()
                    items = data.get("_embedded", {}).get("items", [])

                    if not items:
                        break

                    for item in items:
                        if item.get("type") == "dir":
                            # Recursively list subdirectories
                            subpath = item.get("path", "")
                            subfiles = await self.list_public_folder(public_key, subpath, media_types)
                            files.extend(subfiles)
                            continue

                        media_type = self._classify_file(item.get("name", ""), item.get("mime_type"))

                        if not self._should_include_media_type(media_type, media_types):
                            continue

                        modified_str = item.get("modified", "")
                        try:
                            modified_at = datetime.fromisoformat(modified_str.replace("Z", "+00:00"))
                        except ValueError:
                            modified_at = datetime.utcnow()

                        cloud_file = CloudFile(
                            id=item.get("path", ""),
                            name=item.get("name", ""),
                            path=item.get("path", ""),
                            size=item.get("size", 0),
                            mime_type=item.get("mime_type", ""),
                            media_type=media_type,
                            modified_at=modified_at,
                            download_url=item.get("file"),
                            thumbnail_url=item.get("preview"),
                        )
                        files.append(cloud_file)

                    offset += limit
                    total = data.get("_embedded", {}).get("total", 0)
                    if offset >= total:
                        break

            logger.info("Found media files in public folder", files_count=len(files))
            return files

        except Exception:
            logger.exception("Failed to list public folder")
            return []

    async def download_file(self, file_path: str, access_token: str, output_path: str) -> bool:
        """
        Download a file from Yandex Disk.

        Args:
            file_path: Yandex Disk file path
            access_token: OAuth token
            output_path: Local path to save file

        Returns:
            True if successful
        """
        try:
            async with httpx.AsyncClient() as client:
                # Get download URL
                response = await client.get(
                    f"{self.API_BASE}/resources/download",
                    params={"path": file_path},
                    headers={"Authorization": f"OAuth {access_token}"},
                    timeout=30,
                )

                if response.status_code != 200:
                    logger.error(
                        "Failed to get download URL",
                        file_path=file_path,
                        status_code=response.status_code,
                        response_text=response.text,
                    )
                    return False

                download_url = response.json().get("href")
                if not download_url:
                    logger.error("No download URL in response")
                    return False

                # Download file
                os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

                async with client.stream("GET", download_url, timeout=300) as resp:
                    if resp.status_code != 200:
                        logger.error("Download failed", file_path=file_path, status_code=resp.status_code)
                        return False

                    with open(output_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=8192):
                            f.write(chunk)

                logger.info("Downloaded file from Yandex Disk", file_path=file_path, output_path=output_path)
                return True

        except Exception:
            logger.exception("Failed to download file", file_path=file_path, output_path=output_path)
            return False

    async def download_public_file(self, public_key: str, file_path: str, output_path: str) -> bool:
        """
        Download a file from a public shared folder.

        Args:
            public_key: Public sharing key
            file_path: Path within shared folder
            output_path: Local path to save file

        Returns:
            True if successful
        """
        try:
            async with httpx.AsyncClient() as client:
                # Get download URL
                response = await client.get(
                    f"{self.API_BASE}/public/resources/download",
                    params={"public_key": public_key, "path": file_path},
                    timeout=30,
                )

                if response.status_code != 200:
                    logger.error(
                        "Failed to get public download URL",
                        file_path=file_path,
                        status_code=response.status_code,
                        response_text=response.text,
                    )
                    return False

                download_url = response.json().get("href")
                if not download_url:
                    logger.error("No download URL in response")
                    return False

                # Download file
                os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

                async with client.stream("GET", download_url, timeout=300) as resp:
                    if resp.status_code != 200:
                        logger.error("Public download failed", file_path=file_path, status_code=resp.status_code)
                        return False

                    with open(output_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=8192):
                            f.write(chunk)

                logger.info("Downloaded public file from Yandex Disk", file_path=file_path, output_path=output_path)
                return True

        except Exception:
            logger.exception("Failed to download public file", file_path=file_path, output_path=output_path)
            return False

    async def sync_folder(
        self, folder_path: str, access_token: str, output_dir: str, media_types: list[str] = None
    ) -> SyncResult:
        """
        Sync all media from a Yandex Disk folder.

        Args:
            folder_path: Yandex Disk folder path
            access_token: OAuth token
            output_dir: Local directory for downloaded files
            media_types: Filter by media types

        Returns:
            SyncResult with statistics
        """
        files = await self.list_folder_contents(folder_path, access_token, media_types)
        return await self._sync_files(
            files,
            output_dir,
            download_func=lambda cloud_file, out: self.download_file(cloud_file.path, access_token, out),
        )

    async def sync_public_folder(self, public_url: str, output_dir: str, media_types: list[str] = None) -> SyncResult:
        """
        Sync all media from a public shared folder.

        Args:
            public_url: Yandex Disk sharing URL
            output_dir: Local directory for downloaded files
            media_types: Filter by media types

        Returns:
            SyncResult with statistics
        """
        public_key = self.extract_public_key(public_url)
        if not public_key:
            return SyncResult(
                success=False,
                files_found=0,
                files_downloaded=0,
                files_failed=0,
                errors=["Invalid Yandex Disk sharing URL"],
                downloaded_files=[],
            )

        files = await self.list_public_folder(public_key, "/", media_types)
        return await self._sync_files(
            files,
            output_dir,
            download_func=lambda cloud_file, out: self.download_public_file(public_key, cloud_file.path, out),
        )

    @staticmethod
    def extract_public_key(url: str) -> str | None:
        """
        Extract public key from Yandex Disk sharing URL.

        Supported formats:
        - https://disk.yandex.ru/d/XXXXX
        - https://yadi.sk/d/XXXXX
        - https://disk.yandex.com/d/XXXXX
        """
        import re

        patterns = [
            r"(?:disk\.yandex\.(?:ru|com)|yadi\.sk)/d/([a-zA-Z0-9_-]+)",
            r"(?:disk\.yandex\.(?:ru|com)|yadi\.sk)/i/([a-zA-Z0-9_-]+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return url  # Return full URL as public_key for Yandex API

        return None


# OAuth helper functions
def get_yandex_oauth_url(redirect_uri: str, state: str = None) -> str:
    """Generate Yandex OAuth authorization URL."""
    client_id = os.getenv("YANDEX_CLIENT_ID")

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "force_confirm": "yes",
    }

    if state:
        params["state"] = state

    return f"https://oauth.yandex.ru/authorize?{urlencode(params)}"


async def exchange_yandex_code(code: str) -> dict[str, Any] | None:
    """Exchange authorization code for tokens."""
    client_id = os.getenv("YANDEX_CLIENT_ID")
    client_secret = os.getenv("YANDEX_CLIENT_SECRET")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://oauth.yandex.ru/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                timeout=30,
            )

            if response.status_code == 200:
                return response.json()
            else:
                logger.error(
                    "Yandex token exchange failed",
                    status_code=response.status_code,
                    response_text=response.text,
                )
                return None

    except Exception:
        logger.exception("Yandex token exchange error")
        return None


async def refresh_yandex_token(refresh_token: str) -> dict[str, Any] | None:
    """Refresh expired access token."""
    client_id = os.getenv("YANDEX_CLIENT_ID")
    client_secret = os.getenv("YANDEX_CLIENT_SECRET")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://oauth.yandex.ru/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                timeout=30,
            )

            if response.status_code == 200:
                return response.json()
            else:
                logger.error(
                    "Yandex token refresh failed",
                    status_code=response.status_code,
                    response_text=response.text,
                )
                return None

    except Exception:
        logger.exception("Yandex token refresh error")
        return None


# Global instance
yandex_disk = YandexDiskAdapter()
