"""
S3 Storage Adapter for SalesWhisper Crosspost.

Provides file upload/download functionality to S3-compatible storage (MinIO).
"""

import asyncio
import os

from ..core.logging import get_logger

logger = get_logger("adapters.storage_s3")


class S3Storage:
    """S3-compatible storage adapter using MinIO."""

    def __init__(self):
        """Initialize S3 storage adapter."""
        self.endpoint = os.getenv("S3_ENDPOINT", "http://minio:9000")
        self.access_key = os.getenv("S3_ACCESS_KEY", "minioadmin")
        self.secret_key = os.getenv("S3_SECRET_KEY", "minioadmin123")
        self.bucket = os.getenv("S3_BUCKET", "crosspost-media")
        self._client = None
        logger.info("S3Storage initialized", endpoint=self.endpoint)

    def _get_client(self):
        """Get or create MinIO client."""
        if self._client is None:
            try:
                from urllib.parse import urlparse

                from minio import Minio

                parsed = urlparse(self.endpoint)
                secure = parsed.scheme == "https"
                endpoint = parsed.netloc or parsed.path

                self._client = Minio(endpoint, access_key=self.access_key, secret_key=self.secret_key, secure=secure)

                # Ensure bucket exists
                if not self._client.bucket_exists(self.bucket):
                    self._client.make_bucket(self.bucket)
                    logger.info(f"Created bucket: {self.bucket}")

            except ImportError:
                logger.warning("minio package not installed, S3 operations will fail")
                return None
            except Exception as e:
                logger.error(f"Failed to initialize MinIO client: {e}")
                return None

        return self._client

    async def upload_file(self, local_path: str, s3_key: str) -> str:
        """
        Upload file to S3.

        Args:
            local_path: Path to local file
            s3_key: S3 object key

        Returns:
            S3 URL of uploaded file
        """
        client = self._get_client()
        if not client:
            logger.warning("S3 client not available, returning local path")
            return local_path

        try:
            # Upload file
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: client.fput_object(self.bucket, s3_key, local_path)
            )

            url = f"{self.endpoint}/{self.bucket}/{s3_key}"
            logger.info(f"Uploaded {local_path} to {url}")
            return url

        except Exception as e:
            logger.error(f"Failed to upload {local_path}: {e}")
            return local_path

    async def download_file(self, s3_key: str, local_path: str) -> bool:
        """
        Download file from S3.

        Args:
            s3_key: S3 object key or full URL
            local_path: Path to save file locally

        Returns:
            True if successful, False otherwise
        """
        client = self._get_client()
        if not client:
            logger.warning("S3 client not available")
            return False

        try:
            # Handle full URLs
            if s3_key.startswith(self.endpoint):
                s3_key = s3_key.replace(f"{self.endpoint}/{self.bucket}/", "")

            # Download file
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: client.fget_object(self.bucket, s3_key, local_path)
            )

            logger.info(f"Downloaded {s3_key} to {local_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to download {s3_key}: {e}")
            return False

    async def delete_file(self, s3_key: str) -> bool:
        """Delete file from S3."""
        client = self._get_client()
        if not client:
            return False

        try:
            await asyncio.get_event_loop().run_in_executor(None, lambda: client.remove_object(self.bucket, s3_key))
            logger.info(f"Deleted {s3_key}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete {s3_key}: {e}")
            return False

    async def get_presigned_url(self, s3_key: str, expires_hours: int = 1) -> str | None:
        """Get presigned URL for file download."""
        client = self._get_client()
        if not client:
            return None

        try:
            from datetime import timedelta

            url = await asyncio.get_event_loop().run_in_executor(
                None, lambda: client.presigned_get_object(self.bucket, s3_key, expires=timedelta(hours=expires_hours))
            )
            return url
        except Exception as e:
            logger.error(f"Failed to get presigned URL for {s3_key}: {e}")
            return None


# Global instance
s3_storage = S3Storage()
