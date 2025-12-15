"""
Yandex Disk Adapter for SoVAni Crosspost.

Provides integration with Yandex Disk for fetching user media files.
Users share a folder with the service and media is automatically synced.
"""

import os
import asyncio
from typing import Optional, List, Dict, Any
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass
from urllib.parse import urlencode, quote

import httpx

from ..core.logging import get_logger
from ..core.config import settings

logger = get_logger("adapters.yandex_disk")


# Import shared types from google_drive
from .google_drive import MediaType, CloudFile, SyncResult


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
    VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v', '.wmv'}
    IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff'}

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
        elif ext in self.IMAGE_EXTENSIONS:
            return MediaType.IMAGE

        # Fallback to MIME type
        if mime_type:
            if mime_type.startswith('video/'):
                return MediaType.VIDEO
            elif mime_type.startswith('image/'):
                return MediaType.IMAGE

        return MediaType.UNKNOWN

    async def list_folder_contents(
        self,
        folder_path: str,
        access_token: str,
        media_types: List[str] = None
    ) -> List[CloudFile]:
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
                            'path': folder_path,
                            'limit': limit,
                            'offset': offset,
                            'fields': '_embedded.items.name,_embedded.items.path,_embedded.items.size,_embedded.items.mime_type,_embedded.items.modified,_embedded.items.preview,_embedded.items.type'
                        },
                        headers={'Authorization': f'OAuth {access_token}'},
                        timeout=30
                    )

                    if response.status_code != 200:
                        logger.error(f"Failed to list folder: {response.text}")
                        break

                    data = response.json()
                    items = data.get('_embedded', {}).get('items', [])

                    if not items:
                        break

                    for item in items:
                        # Skip folders
                        if item.get('type') == 'dir':
                            continue

                        media_type = self._classify_file(
                            item.get('name', ''),
                            item.get('mime_type')
                        )

                        # Filter by media type
                        if media_types:
                            if media_type == MediaType.VIDEO and 'video' not in media_types:
                                continue
                            if media_type == MediaType.IMAGE and 'image' not in media_types:
                                continue

                        if media_type == MediaType.UNKNOWN:
                            continue

                        # Parse modified time
                        modified_str = item.get('modified', '')
                        try:
                            modified_at = datetime.fromisoformat(
                                modified_str.replace('Z', '+00:00')
                            )
                        except:
                            modified_at = datetime.utcnow()

                        cloud_file = CloudFile(
                            id=item.get('path', ''),
                            name=item.get('name', ''),
                            path=item.get('path', ''),
                            size=item.get('size', 0),
                            mime_type=item.get('mime_type', ''),
                            media_type=media_type,
                            modified_at=modified_at,
                            thumbnail_url=item.get('preview')
                        )
                        files.append(cloud_file)

                    offset += limit

                    # Check if there are more items
                    total = data.get('_embedded', {}).get('total', 0)
                    if offset >= total:
                        break

            logger.info(f"Found {len(files)} media files in {folder_path}")
            return files

        except Exception as e:
            logger.error(f"Failed to list folder {folder_path}: {e}")
            return []

    async def list_public_folder(
        self,
        public_key: str,
        path: str = "/",
        media_types: List[str] = None
    ) -> List[CloudFile]:
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
                            'public_key': public_key,
                            'path': path,
                            'limit': limit,
                            'offset': offset,
                        },
                        timeout=30
                    )

                    if response.status_code != 200:
                        logger.error(f"Failed to list public folder: {response.text}")
                        break

                    data = response.json()
                    items = data.get('_embedded', {}).get('items', [])

                    if not items:
                        break

                    for item in items:
                        if item.get('type') == 'dir':
                            # Recursively list subdirectories
                            subpath = item.get('path', '')
                            subfiles = await self.list_public_folder(
                                public_key, subpath, media_types
                            )
                            files.extend(subfiles)
                            continue

                        media_type = self._classify_file(
                            item.get('name', ''),
                            item.get('mime_type')
                        )

                        if media_types:
                            if media_type == MediaType.VIDEO and 'video' not in media_types:
                                continue
                            if media_type == MediaType.IMAGE and 'image' not in media_types:
                                continue

                        if media_type == MediaType.UNKNOWN:
                            continue

                        modified_str = item.get('modified', '')
                        try:
                            modified_at = datetime.fromisoformat(
                                modified_str.replace('Z', '+00:00')
                            )
                        except:
                            modified_at = datetime.utcnow()

                        cloud_file = CloudFile(
                            id=item.get('path', ''),
                            name=item.get('name', ''),
                            path=item.get('path', ''),
                            size=item.get('size', 0),
                            mime_type=item.get('mime_type', ''),
                            media_type=media_type,
                            modified_at=modified_at,
                            download_url=item.get('file'),
                            thumbnail_url=item.get('preview')
                        )
                        files.append(cloud_file)

                    offset += limit
                    total = data.get('_embedded', {}).get('total', 0)
                    if offset >= total:
                        break

            logger.info(f"Found {len(files)} media files in public folder")
            return files

        except Exception as e:
            logger.error(f"Failed to list public folder: {e}")
            return []

    async def download_file(
        self,
        file_path: str,
        access_token: str,
        output_path: str
    ) -> bool:
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
                    params={'path': file_path},
                    headers={'Authorization': f'OAuth {access_token}'},
                    timeout=30
                )

                if response.status_code != 200:
                    logger.error(f"Failed to get download URL: {response.text}")
                    return False

                download_url = response.json().get('href')
                if not download_url:
                    logger.error("No download URL in response")
                    return False

                # Download file
                os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)

                async with client.stream('GET', download_url, timeout=300) as resp:
                    if resp.status_code != 200:
                        logger.error(f"Download failed: {resp.status_code}")
                        return False

                    with open(output_path, 'wb') as f:
                        async for chunk in resp.aiter_bytes(chunk_size=8192):
                            f.write(chunk)

                logger.info(f"Downloaded {file_path} to {output_path}")
                return True

        except Exception as e:
            logger.error(f"Failed to download file {file_path}: {e}")
            return False

    async def download_public_file(
        self,
        public_key: str,
        file_path: str,
        output_path: str
    ) -> bool:
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
                    params={
                        'public_key': public_key,
                        'path': file_path
                    },
                    timeout=30
                )

                if response.status_code != 200:
                    logger.error(f"Failed to get public download URL: {response.text}")
                    return False

                download_url = response.json().get('href')
                if not download_url:
                    logger.error("No download URL in response")
                    return False

                # Download file
                os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)

                async with client.stream('GET', download_url, timeout=300) as resp:
                    if resp.status_code != 200:
                        logger.error(f"Download failed: {resp.status_code}")
                        return False

                    with open(output_path, 'wb') as f:
                        async for chunk in resp.aiter_bytes(chunk_size=8192):
                            f.write(chunk)

                logger.info(f"Downloaded public file {file_path} to {output_path}")
                return True

        except Exception as e:
            logger.error(f"Failed to download public file {file_path}: {e}")
            return False

    async def sync_folder(
        self,
        folder_path: str,
        access_token: str,
        output_dir: str,
        media_types: List[str] = None
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
        errors = []
        downloaded = []

        # List files
        files = await self.list_folder_contents(folder_path, access_token, media_types)

        if not files:
            return SyncResult(
                success=True,
                files_found=0,
                files_downloaded=0,
                files_failed=0,
                errors=[],
                downloaded_files=[]
            )

        # Create output directories
        videos_dir = os.path.join(output_dir, 'videos')
        photos_dir = os.path.join(output_dir, 'photos')
        os.makedirs(videos_dir, exist_ok=True)
        os.makedirs(photos_dir, exist_ok=True)

        files_downloaded = 0
        files_failed = 0

        for cloud_file in files:
            try:
                if cloud_file.media_type == MediaType.VIDEO:
                    target_dir = videos_dir
                elif cloud_file.media_type == MediaType.IMAGE:
                    target_dir = photos_dir
                else:
                    continue

                output_path = os.path.join(target_dir, cloud_file.name)

                # Skip if already exists and same size
                if os.path.exists(output_path):
                    if os.path.getsize(output_path) == cloud_file.size:
                        logger.debug(f"Skipping {cloud_file.name} - already exists")
                        continue

                success = await self.download_file(
                    cloud_file.path, access_token, output_path
                )

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
            downloaded_files=downloaded
        )

    async def sync_public_folder(
        self,
        public_url: str,
        output_dir: str,
        media_types: List[str] = None
    ) -> SyncResult:
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
                downloaded_files=[]
            )

        errors = []
        downloaded = []

        files = await self.list_public_folder(public_key, "/", media_types)

        if not files:
            return SyncResult(
                success=True,
                files_found=0,
                files_downloaded=0,
                files_failed=0,
                errors=[],
                downloaded_files=[]
            )

        videos_dir = os.path.join(output_dir, 'videos')
        photos_dir = os.path.join(output_dir, 'photos')
        os.makedirs(videos_dir, exist_ok=True)
        os.makedirs(photos_dir, exist_ok=True)

        files_downloaded = 0
        files_failed = 0

        for cloud_file in files:
            try:
                if cloud_file.media_type == MediaType.VIDEO:
                    target_dir = videos_dir
                elif cloud_file.media_type == MediaType.IMAGE:
                    target_dir = photos_dir
                else:
                    continue

                output_path = os.path.join(target_dir, cloud_file.name)

                if os.path.exists(output_path):
                    if os.path.getsize(output_path) == cloud_file.size:
                        continue

                success = await self.download_public_file(
                    public_key, cloud_file.path, output_path
                )

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
            downloaded_files=downloaded
        )

    @staticmethod
    def extract_public_key(url: str) -> Optional[str]:
        """
        Extract public key from Yandex Disk sharing URL.

        Supported formats:
        - https://disk.yandex.ru/d/XXXXX
        - https://yadi.sk/d/XXXXX
        - https://disk.yandex.com/d/XXXXX
        """
        import re

        patterns = [
            r'(?:disk\.yandex\.(?:ru|com)|yadi\.sk)/d/([a-zA-Z0-9_-]+)',
            r'(?:disk\.yandex\.(?:ru|com)|yadi\.sk)/i/([a-zA-Z0-9_-]+)',
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
        'response_type': 'code',
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'force_confirm': 'yes',
    }

    if state:
        params['state'] = state

    return f"https://oauth.yandex.ru/authorize?{urlencode(params)}"


async def exchange_yandex_code(code: str) -> Optional[Dict[str, Any]]:
    """Exchange authorization code for tokens."""
    client_id = os.getenv("YANDEX_CLIENT_ID")
    client_secret = os.getenv("YANDEX_CLIENT_SECRET")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                'https://oauth.yandex.ru/token',
                data={
                    'grant_type': 'authorization_code',
                    'code': code,
                    'client_id': client_id,
                    'client_secret': client_secret
                },
                timeout=30
            )

            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Yandex token exchange failed: {response.text}")
                return None

    except Exception as e:
        logger.error(f"Yandex token exchange error: {e}")
        return None


async def refresh_yandex_token(refresh_token: str) -> Optional[Dict[str, Any]]:
    """Refresh expired access token."""
    client_id = os.getenv("YANDEX_CLIENT_ID")
    client_secret = os.getenv("YANDEX_CLIENT_SECRET")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                'https://oauth.yandex.ru/token',
                data={
                    'grant_type': 'refresh_token',
                    'refresh_token': refresh_token,
                    'client_id': client_id,
                    'client_secret': client_secret
                },
                timeout=30
            )

            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Yandex token refresh failed: {response.text}")
                return None

    except Exception as e:
        logger.error(f"Yandex token refresh error: {e}")
        return None


# Global instance
yandex_disk = YandexDiskAdapter()
