"""
Google Drive Adapter for SalesWhisper Crosspost.

Provides integration with Google Drive for fetching user media files.
Users share a folder with the service and media is automatically synced.
"""

import os
import io
import asyncio
import tempfile
from typing import Optional, List, Dict, Any
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass
from enum import Enum

from ..core.logging import get_logger
from ..core.config import settings

logger = get_logger("adapters.google_drive")


class MediaType(Enum):
    """Media file types."""
    VIDEO = "video"
    IMAGE = "image"
    UNKNOWN = "unknown"


@dataclass
class CloudFile:
    """Represents a file in cloud storage."""
    id: str
    name: str
    path: str
    size: int
    mime_type: str
    media_type: MediaType
    modified_at: datetime
    download_url: Optional[str] = None
    thumbnail_url: Optional[str] = None


@dataclass
class SyncResult:
    """Result of sync operation."""
    success: bool
    files_found: int
    files_downloaded: int
    files_failed: int
    errors: List[str]
    downloaded_files: List[str]


class GoogleDriveAdapter:
    """
    Google Drive integration adapter.

    Uses Google Drive API v3 for:
    - Listing files in shared folders
    - Downloading media files
    - Watching for changes (webhooks)
    """

    # Supported MIME types
    VIDEO_MIME_TYPES = [
        'video/mp4', 'video/quicktime', 'video/x-msvideo',
        'video/x-matroska', 'video/webm', 'video/mpeg'
    ]
    IMAGE_MIME_TYPES = [
        'image/jpeg', 'image/png', 'image/gif', 'image/webp',
        'image/bmp', 'image/tiff'
    ]

    def __init__(self):
        """Initialize Google Drive adapter."""
        self.client_id = os.getenv("GOOGLE_CLIENT_ID")
        self.client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
        self.api_key = os.getenv("GOOGLE_API_KEY")
        self._service = None
        logger.info("GoogleDriveAdapter initialized")

    async def _get_service(self, credentials: Dict[str, Any] = None):
        """Get or create Google Drive API service."""
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            if credentials:
                creds = Credentials(
                    token=credentials.get('access_token'),
                    refresh_token=credentials.get('refresh_token'),
                    token_uri='https://oauth2.googleapis.com/token',
                    client_id=self.client_id,
                    client_secret=self.client_secret
                )
                return build('drive', 'v3', credentials=creds)

            return None

        except ImportError:
            logger.error("google-api-python-client not installed")
            return None
        except Exception as e:
            logger.error(f"Failed to create Drive service: {e}")
            return None

    def _classify_mime_type(self, mime_type: str) -> MediaType:
        """Classify MIME type as video, image, or unknown."""
        if mime_type in self.VIDEO_MIME_TYPES:
            return MediaType.VIDEO
        elif mime_type in self.IMAGE_MIME_TYPES:
            return MediaType.IMAGE
        return MediaType.UNKNOWN

    async def list_folder_contents(
        self,
        folder_id: str,
        credentials: Dict[str, Any],
        media_types: List[str] = None
    ) -> List[CloudFile]:
        """
        List all media files in a Google Drive folder.

        Args:
            folder_id: Google Drive folder ID
            credentials: OAuth credentials dict
            media_types: Filter by media types ['video', 'image']

        Returns:
            List of CloudFile objects
        """
        service = await self._get_service(credentials)
        if not service:
            logger.error("Failed to get Drive service")
            return []

        try:
            files = []
            page_token = None

            # Build MIME type query
            mime_queries = []
            if media_types is None or 'video' in media_types:
                mime_queries.extend([f"mimeType='{mt}'" for mt in self.VIDEO_MIME_TYPES])
            if media_types is None or 'image' in media_types:
                mime_queries.extend([f"mimeType='{mt}'" for mt in self.IMAGE_MIME_TYPES])

            mime_query = ' or '.join(mime_queries)
            query = f"'{folder_id}' in parents and ({mime_query}) and trashed=false"

            while True:
                # Fetch files
                response = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: service.files().list(
                        q=query,
                        spaces='drive',
                        fields='nextPageToken, files(id, name, mimeType, size, modifiedTime, thumbnailLink)',
                        pageToken=page_token,
                        pageSize=100
                    ).execute()
                )

                for file in response.get('files', []):
                    media_type = self._classify_mime_type(file.get('mimeType', ''))

                    cloud_file = CloudFile(
                        id=file['id'],
                        name=file['name'],
                        path=f"/{file['name']}",
                        size=int(file.get('size', 0)),
                        mime_type=file.get('mimeType', ''),
                        media_type=media_type,
                        modified_at=datetime.fromisoformat(
                            file.get('modifiedTime', '').replace('Z', '+00:00')
                        ),
                        thumbnail_url=file.get('thumbnailLink')
                    )
                    files.append(cloud_file)

                page_token = response.get('nextPageToken')
                if not page_token:
                    break

            logger.info(f"Found {len(files)} media files in folder {folder_id}")
            return files

        except Exception as e:
            logger.error(f"Failed to list folder {folder_id}: {e}")
            return []

    async def download_file(
        self,
        file_id: str,
        credentials: Dict[str, Any],
        output_path: str
    ) -> bool:
        """
        Download a file from Google Drive.

        Args:
            file_id: Google Drive file ID
            credentials: OAuth credentials dict
            output_path: Local path to save file

        Returns:
            True if successful
        """
        service = await self._get_service(credentials)
        if not service:
            return False

        try:
            from googleapiclient.http import MediaIoBaseDownload

            # Create request
            request = service.files().get_media(fileId=file_id)

            # Download file
            os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)

            with open(output_path, 'wb') as f:
                downloader = MediaIoBaseDownload(f, request)
                done = False
                while not done:
                    status, done = await asyncio.get_event_loop().run_in_executor(
                        None, downloader.next_chunk
                    )
                    if status:
                        logger.debug(f"Download progress: {int(status.progress() * 100)}%")

            logger.info(f"Downloaded file {file_id} to {output_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to download file {file_id}: {e}")
            return False

    async def sync_folder(
        self,
        folder_id: str,
        credentials: Dict[str, Any],
        output_dir: str,
        media_types: List[str] = None
    ) -> SyncResult:
        """
        Sync all media from a Google Drive folder.

        Args:
            folder_id: Google Drive folder ID
            credentials: OAuth credentials
            output_dir: Local directory for downloaded files
            media_types: Filter by media types

        Returns:
            SyncResult with statistics
        """
        errors = []
        downloaded = []

        # List files
        files = await self.list_folder_contents(folder_id, credentials, media_types)

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
                # Determine output directory
                if cloud_file.media_type == MediaType.VIDEO:
                    target_dir = videos_dir
                elif cloud_file.media_type == MediaType.IMAGE:
                    target_dir = photos_dir
                else:
                    continue  # Skip unknown types

                output_path = os.path.join(target_dir, cloud_file.name)

                # Skip if already exists and same size
                if os.path.exists(output_path):
                    if os.path.getsize(output_path) == cloud_file.size:
                        logger.debug(f"Skipping {cloud_file.name} - already exists")
                        continue

                # Download file
                success = await self.download_file(cloud_file.id, credentials, output_path)

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

    async def get_folder_info(
        self,
        folder_id: str,
        credentials: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Get information about a folder."""
        service = await self._get_service(credentials)
        if not service:
            return None

        try:
            folder = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: service.files().get(
                    fileId=folder_id,
                    fields='id, name, mimeType, owners, permissions'
                ).execute()
            )

            return {
                'id': folder['id'],
                'name': folder['name'],
                'is_folder': folder.get('mimeType') == 'application/vnd.google-apps.folder',
                'owners': folder.get('owners', []),
                'permissions': folder.get('permissions', [])
            }

        except Exception as e:
            logger.error(f"Failed to get folder info: {e}")
            return None

    @staticmethod
    def extract_folder_id_from_url(url: str) -> Optional[str]:
        """
        Extract folder ID from Google Drive sharing URL.

        Supported formats:
        - https://drive.google.com/drive/folders/FOLDER_ID
        - https://drive.google.com/drive/u/0/folders/FOLDER_ID
        - https://drive.google.com/open?id=FOLDER_ID
        """
        import re

        patterns = [
            r'drive\.google\.com/drive/(?:u/\d+/)?folders/([a-zA-Z0-9_-]+)',
            r'drive\.google\.com/open\?id=([a-zA-Z0-9_-]+)',
            r'drive\.google\.com/folderview\?id=([a-zA-Z0-9_-]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)

        return None


# OAuth helper functions
def get_google_oauth_url(redirect_uri: str, state: str = None) -> str:
    """Generate Google OAuth authorization URL."""
    from urllib.parse import urlencode

    client_id = os.getenv("GOOGLE_CLIENT_ID")

    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': 'https://www.googleapis.com/auth/drive.readonly',
        'access_type': 'offline',
        'prompt': 'consent',
    }

    if state:
        params['state'] = state

    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


async def exchange_google_code(code: str, redirect_uri: str) -> Optional[Dict[str, Any]]:
    """Exchange authorization code for tokens."""
    import httpx

    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                'https://oauth2.googleapis.com/token',
                data={
                    'code': code,
                    'client_id': client_id,
                    'client_secret': client_secret,
                    'redirect_uri': redirect_uri,
                    'grant_type': 'authorization_code'
                }
            )

            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Token exchange failed: {response.text}")
                return None

    except Exception as e:
        logger.error(f"Token exchange error: {e}")
        return None


async def refresh_google_token(refresh_token: str) -> Optional[Dict[str, Any]]:
    """Refresh expired access token."""
    import httpx

    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                'https://oauth2.googleapis.com/token',
                data={
                    'refresh_token': refresh_token,
                    'client_id': client_id,
                    'client_secret': client_secret,
                    'grant_type': 'refresh_token'
                }
            )

            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Token refresh failed: {response.text}")
                return None

    except Exception as e:
        logger.error(f"Token refresh error: {e}")
        return None


# Global instance
google_drive = GoogleDriveAdapter()
