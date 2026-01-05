"""
Telegram Bot API intake adapter for SalesWhisper Crosspost.

This module handles:
- Parsing Telegram Bot API updates (photos, videos, captions)
- Downloading media files via Telegram Bot API
- Uploading to S3/MinIO storage with metadata
- Creating posts records for all target platforms
- Publishing outbox events and triggering ingest tasks
- Comprehensive error handling and logging
"""

import hashlib
import mimetypes
import os
import tempfile
import time
import uuid
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone
from pathlib import Path

import httpx
from PIL import Image
import ffmpeg

from ..core.config import settings
from ..core.logging import get_logger, with_logging_context, audit_logger
from ..core.security import generate_idempotency_key
from ..models.db import db_manager
from ..workers.tasks.outbox import publish_outbox_event
from ..workers.tasks.ingest import process_telegram_update
from ..observability.metrics import metrics
from .storage_s3 import S3StorageAdapter


logger = get_logger("adapters.telegram_intake")


class TelegramIntakeError(Exception):
    """Custom exception for Telegram intake processing errors."""
    pass


class TelegramBotAPIError(Exception):
    """Custom exception for Telegram Bot API errors."""
    pass


class TelegramIntakeAdapter:
    """Adapter for processing Telegram Bot API updates."""
    
    def __init__(self):
        """Initialize Telegram intake adapter."""
        self.bot_token = settings.telegram.bot_token.get_secret_value()
        self.bot_api_base = f"https://api.telegram.org/bot{self.bot_token}"
        self.storage = S3StorageAdapter()
        
        # HTTP client for Telegram API calls
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5)
        )
        
        # Target platforms for cross-posting
        self.target_platforms = ["instagram", "vk", "tiktok", "youtube", "telegram"]
        
        logger.info("TelegramIntakeAdapter initialized")
    
    async def process_update(self, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process incoming Telegram Bot API update.
        
        Args:
            update_data: Telegram update data from webhook
            
        Returns:
            Processing result with post IDs and status
        """
        update_id = update_data.get("update_id")
        correlation_id = str(uuid.uuid4())
        
        with with_logging_context(correlation_id=correlation_id):
            logger.info(
                "Processing Telegram update",
                update_id=update_id,
                correlation_id=correlation_id
            )
            
            start_time = time.time()
            
            try:
                # Extract content from update
                content = self._extract_content(update_data)
                if not content:
                    raise TelegramIntakeError("No processable content in update")
                
                # Generate unique post ID
                post_id = str(uuid.uuid4())
                
                # Process media files if present
                media_assets = await self._process_media_files(content, post_id)
                
                # Extract text content and metadata
                text_content = self._extract_text_content(content)
                metadata = self._extract_metadata(content, update_data)
                
                # Create posts for all target platforms
                posts_created = await self._create_posts_for_platforms(
                    post_id, text_content, media_assets, metadata, correlation_id
                )
                
                # Publish outbox event
                outbox_event_id = publish_outbox_event(
                    event_type="post_created",
                    payload={
                        "post_id": post_id,
                        "update_data": update_data,
                        "content": content,
                        "media_assets": [asset.dict() if hasattr(asset, 'dict') else asset for asset in media_assets],
                        "text_content": text_content,
                        "metadata": metadata,
                        "platforms": self.target_platforms
                    },
                    entity_id=post_id,
                    correlation_id=correlation_id
                )
                
                # Trigger ingest task
                ingest_task = process_telegram_update.delay(update_data, post_id)
                
                processing_time = time.time() - start_time
                
                # Track metrics
                metrics.track_post_created("telegram", "webhook")
                if media_assets:
                    metrics.track_media_processed(
                        media_type="mixed" if len(media_assets) > 1 else media_assets[0].get("media_type", "unknown"),
                        platform="telegram",
                        success=True,
                        duration=processing_time,
                        file_size=sum(asset.get("file_size", 0) for asset in media_assets)
                    )
                
                # Audit log
                audit_logger.log_post_created(
                    post_id=post_id,
                    platform="telegram",
                    user_id=str(metadata.get("from_user_id", "unknown")),
                    product_id="telegram_intake",
                    update_id=update_id,
                    processing_time=processing_time,
                    media_count=len(media_assets)
                )
                
                logger.info(
                    "Telegram update processed successfully",
                    post_id=post_id,
                    update_id=update_id,
                    processing_time=processing_time,
                    media_assets_count=len(media_assets),
                    platforms_created=len(posts_created),
                    outbox_event_id=outbox_event_id,
                    ingest_task_id=ingest_task.id
                )
                
                return {
                    "success": True,
                    "post_id": post_id,
                    "update_id": update_id,
                    "processing_time": processing_time,
                    "media_assets_count": len(media_assets),
                    "platforms_created": len(posts_created),
                    "posts_created": posts_created,
                    "outbox_event_id": outbox_event_id,
                    "ingest_task_id": ingest_task.id,
                    "correlation_id": correlation_id
                }
                
            except Exception as e:
                processing_time = time.time() - start_time
                
                logger.error(
                    "Telegram update processing failed",
                    update_id=update_id,
                    correlation_id=correlation_id,
                    error=str(e),
                    processing_time=processing_time,
                    exc_info=True
                )
                
                # Track failure metrics
                metrics.track_post_failed("telegram", "intake_error")
                
                raise TelegramIntakeError(f"Failed to process update {update_id}: {str(e)}")
    
    def _extract_content(self, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract processable content from Telegram update."""
        content_sources = ["message", "channel_post", "edited_message", "edited_channel_post"]
        
        for source in content_sources:
            if source in update_data and update_data[source]:
                content = update_data[source]
                logger.debug(f"Extracted content from {source}", message_id=content.get("message_id"))
                return content
        
        logger.warning("No processable content found in update")
        return None
    
    async def _process_media_files(self, content: Dict[str, Any], post_id: str) -> List[Dict[str, Any]]:
        """
        Process and download media files from Telegram content.
        
        Args:
            content: Telegram message content
            post_id: Unique post identifier
            
        Returns:
            List of processed media asset information
        """
        media_assets = []
        
        # Media fields to check in order of priority
        media_fields = [
            ("photo", "photo"),
            ("video", "video"), 
            ("animation", "animation"),
            ("document", "document"),
            ("audio", "audio"),
            ("voice", "voice"),
            ("video_note", "video_note"),
            ("sticker", "sticker")
        ]
        
        for field_name, media_type in media_fields:
            if field_name in content:
                media_data = content[field_name]
                
                try:
                    # For photo arrays, get the largest size
                    if field_name == "photo" and isinstance(media_data, list):
                        media_data = max(media_data, key=lambda x: x.get("file_size", 0))
                    
                    if media_data:
                        asset = await self._download_and_process_media(media_data, media_type, post_id)
                        if asset:
                            media_assets.append(asset)
                            
                except Exception as e:
                    logger.error(
                        "Failed to process media file",
                        post_id=post_id,
                        field_name=field_name,
                        file_id=media_data.get("file_id") if media_data else None,
                        error=str(e)
                    )
                    # Continue processing other media files
        
        logger.info(f"Processed {len(media_assets)} media assets", post_id=post_id)
        return media_assets
    
    async def _download_and_process_media(self, media_data: Dict[str, Any], 
                                        media_type: str, post_id: str) -> Optional[Dict[str, Any]]:
        """
        Download media file from Telegram and upload to S3 with metadata.
        
        Args:
            media_data: Telegram media object data
            media_type: Type of media (photo, video, etc.)
            post_id: Post identifier
            
        Returns:
            Media asset information with S3 path and metadata
        """
        file_id = media_data.get("file_id")
        if not file_id:
            return None
            
        logger.info(
            "Downloading media file",
            post_id=post_id,
            file_id=file_id,
            media_type=media_type
        )
        
        try:
            # Get file info from Telegram API
            file_info = await self._get_file_info(file_id)
            file_path = file_info.get("file_path")
            
            if not file_path:
                raise TelegramBotAPIError(f"No file_path in response for file_id {file_id}")
            
            # Download file to temporary location
            temp_file_path = await self._download_file(file_path, file_id)
            
            try:
                # Generate file hash
                file_hash = self._calculate_file_hash(temp_file_path)
                
                # Extract media metadata
                metadata = await self._extract_media_metadata(temp_file_path, media_type)
                
                # Generate S3 path
                file_extension = Path(file_path).suffix or self._guess_extension(media_type, metadata)
                s3_key = f"media/{post_id}/{file_id}{file_extension}"
                
                # Upload to S3
                s3_url = await self.storage.upload_file(temp_file_path, s3_key)
                
                # Create media asset record
                media_asset = {
                    "id": str(uuid.uuid4()),
                    "post_id": post_id,
                    "file_id": file_id,
                    "media_type": media_type,
                    "file_hash": file_hash,
                    "file_size": os.path.getsize(temp_file_path),
                    "mime_type": metadata.get("mime_type", self._guess_mime_type(file_extension)),
                    "file_name": media_data.get("file_name", f"{file_id}{file_extension}"),
                    "s3_key": s3_key,
                    "s3_url": s3_url,
                    "width": metadata.get("width"),
                    "height": metadata.get("height"),
                    "duration": metadata.get("duration"),
                    "aspect_ratio": self._calculate_aspect_ratio(metadata.get("width"), metadata.get("height")),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "metadata": {
                        "telegram_data": media_data,
                        "extracted_metadata": metadata
                    }
                }
                
                # Save media asset to database
                await self._save_media_asset(media_asset)
                
                logger.info(
                    "Media file processed successfully",
                    post_id=post_id,
                    file_id=file_id,
                    file_hash=file_hash,
                    file_size=media_asset["file_size"],
                    s3_key=s3_key
                )
                
                return media_asset
                
            finally:
                # Clean up temporary file
                try:
                    os.unlink(temp_file_path)
                except Exception as e:
                    logger.warning(f"Failed to delete temp file {temp_file_path}: {e}")
                    
        except Exception as e:
            logger.error(
                "Failed to download and process media",
                post_id=post_id,
                file_id=file_id,
                error=str(e),
                exc_info=True
            )
            raise
    
    async def _get_file_info(self, file_id: str) -> Dict[str, Any]:
        """Get file information from Telegram Bot API."""
        try:
            response = await self.http_client.get(
                f"{self.bot_api_base}/getFile",
                params={"file_id": file_id}
            )
            response.raise_for_status()
            
            data = response.json()
            if not data.get("ok"):
                raise TelegramBotAPIError(f"API error: {data.get('description', 'Unknown error')}")
            
            return data["result"]
            
        except httpx.HTTPError as e:
            raise TelegramBotAPIError(f"HTTP error getting file info: {e}")
        except Exception as e:
            raise TelegramBotAPIError(f"Error getting file info: {e}")
    
    async def _download_file(self, file_path: str, file_id: str) -> str:
        """Download file from Telegram servers to temporary location."""
        file_url = f"https://api.telegram.org/file/bot{self.bot_token}/{file_path}"
        
        try:
            # Create temporary file
            temp_dir = tempfile.gettempdir()
            temp_file_path = os.path.join(temp_dir, f"tg_{file_id}_{int(time.time())}")
            
            # Download file
            async with self.http_client.stream("GET", file_url) as response:
                response.raise_for_status()
                
                with open(temp_file_path, "wb") as f:
                    async for chunk in response.aiter_bytes(8192):
                        f.write(chunk)
            
            logger.debug(f"Downloaded file to {temp_file_path}", file_id=file_id)
            return temp_file_path
            
        except httpx.HTTPError as e:
            raise TelegramBotAPIError(f"HTTP error downloading file: {e}")
        except Exception as e:
            raise TelegramBotAPIError(f"Error downloading file: {e}")
    
    def _calculate_file_hash(self, file_path: str) -> str:
        """Calculate SHA-256 hash of file."""
        hash_sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha256.update(chunk)
        return hash_sha256.hexdigest()
    
    async def _extract_media_metadata(self, file_path: str, media_type: str) -> Dict[str, Any]:
        """Extract metadata from media file."""
        metadata = {
            "file_size": os.path.getsize(file_path),
            "mime_type": mimetypes.guess_type(file_path)[0]
        }
        
        try:
            if media_type in ["photo", "sticker"] or file_path.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif')):
                # Extract image metadata
                with Image.open(file_path) as img:
                    metadata.update({
                        "width": img.width,
                        "height": img.height,
                        "format": img.format,
                        "mode": img.mode
                    })
                    
            elif media_type in ["video", "animation", "video_note"] or file_path.lower().endswith(('.mp4', '.avi', '.mov', '.webm')):
                # Extract video metadata using ffmpeg
                try:
                    probe = ffmpeg.probe(file_path)
                    video_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'video'), None)
                    
                    if video_stream:
                        metadata.update({
                            "width": int(video_stream.get('width', 0)),
                            "height": int(video_stream.get('height', 0)),
                            "duration": float(video_stream.get('duration', 0)),
                            "codec": video_stream.get('codec_name'),
                            "fps": eval(video_stream.get('r_frame_rate', '0/1'))
                        })
                        
                except Exception as e:
                    logger.warning(f"Failed to extract video metadata with ffmpeg: {e}")
                    
            elif media_type in ["audio", "voice"]:
                # Extract audio metadata
                try:
                    probe = ffmpeg.probe(file_path)
                    audio_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'audio'), None)
                    
                    if audio_stream:
                        metadata.update({
                            "duration": float(audio_stream.get('duration', 0)),
                            "codec": audio_stream.get('codec_name'),
                            "sample_rate": int(audio_stream.get('sample_rate', 0)),
                            "channels": int(audio_stream.get('channels', 0))
                        })
                        
                except Exception as e:
                    logger.warning(f"Failed to extract audio metadata with ffmpeg: {e}")
                    
        except Exception as e:
            logger.warning(f"Failed to extract metadata for {media_type}: {e}")
        
        return metadata
    
    def _guess_extension(self, media_type: str, metadata: Dict[str, Any]) -> str:
        """Guess file extension based on media type and metadata."""
        extensions = {
            "photo": ".jpg",
            "video": ".mp4", 
            "animation": ".gif",
            "document": ".bin",
            "audio": ".mp3",
            "voice": ".ogg",
            "video_note": ".mp4",
            "sticker": ".webp"
        }
        return extensions.get(media_type, ".bin")
    
    def _guess_mime_type(self, file_extension: str) -> str:
        """Guess MIME type from file extension."""
        return mimetypes.guess_type(f"file{file_extension}")[0] or "application/octet-stream"
    
    def _calculate_aspect_ratio(self, width: Optional[int], height: Optional[int]) -> Optional[str]:
        """Calculate aspect ratio string from width and height."""
        if not width or not height:
            return None
            
        # Calculate GCD for simplification
        def gcd(a, b):
            while b:
                a, b = b, a % b
            return a
        
        divisor = gcd(width, height)
        ratio_w = width // divisor
        ratio_h = height // divisor
        
        return f"{ratio_w}:{ratio_h}"
    
    async def _save_media_asset(self, media_asset: Dict[str, Any]):
        """Save media asset record to database."""
        try:
            db_session = db_manager.get_session()
            
            # This is a placeholder - would save to actual database
            # In real implementation:
            # asset_model = MediaAssetModel(**media_asset)
            # db_session.add(asset_model)
            # db_session.commit()
            
            logger.debug("Media asset saved to database", asset_id=media_asset["id"])
            
        except Exception as e:
            logger.error(f"Failed to save media asset to database: {e}")
            raise
        finally:
            if 'db_session' in locals():
                db_session.close()
    
    def _extract_text_content(self, content: Dict[str, Any]) -> str:
        """Extract text content from Telegram message."""
        # Check for text content in order of priority
        text_fields = ["text", "caption"]
        
        for field in text_fields:
            if field in content and content[field]:
                return content[field].strip()
        
        return ""
    
    def _extract_metadata(self, content: Dict[str, Any], update_data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract metadata from Telegram content and update."""
        metadata = {
            "message_id": content.get("message_id"),
            "from_user_id": content.get("from", {}).get("id"),
            "from_username": content.get("from", {}).get("username"),
            "from_first_name": content.get("from", {}).get("first_name"),
            "chat_id": content.get("chat", {}).get("id"),
            "chat_type": content.get("chat", {}).get("type"),
            "chat_title": content.get("chat", {}).get("title"),
            "message_date": content.get("date"),
            "update_id": update_data.get("update_id"),
            "content_type": self._determine_content_type(content),
            "has_media": self._has_media(content),
            "extracted_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Add edit information if present
        if content.get("edit_date"):
            metadata["edit_date"] = content["edit_date"]
            metadata["is_edited"] = True
        
        return metadata
    
    def _determine_content_type(self, content: Dict[str, Any]) -> str:
        """Determine the type of content in the message."""
        if "photo" in content:
            return "photo"
        elif "video" in content:
            return "video"
        elif "animation" in content:
            return "animation"
        elif "document" in content:
            return "document"
        elif "audio" in content:
            return "audio"
        elif "voice" in content:
            return "voice"
        elif "video_note" in content:
            return "video_note"
        elif "sticker" in content:
            return "sticker"
        elif "text" in content:
            return "text"
        else:
            return "unknown"
    
    def _has_media(self, content: Dict[str, Any]) -> bool:
        """Check if content contains media files."""
        media_fields = ["photo", "video", "animation", "document", "audio", "voice", "video_note", "sticker"]
        return any(field in content for field in media_fields)
    
    async def _create_posts_for_platforms(self, post_id: str, text_content: str, 
                                        media_assets: List[Dict[str, Any]], metadata: Dict[str, Any],
                                        correlation_id: str) -> List[Dict[str, Any]]:
        """
        Create post records for all target platforms.
        
        Args:
            post_id: Main post identifier
            text_content: Extracted text content
            media_assets: List of processed media assets
            metadata: Extracted metadata
            correlation_id: Request correlation ID
            
        Returns:
            List of created post records
        """
        posts_created = []
        
        try:
            db_session = db_manager.get_session()
            
            for platform in self.target_platforms:
                try:
                    # Generate platform-specific post ID and idempotency key
                    platform_post_id = f"{post_id}_{platform}"
                    idempotency_key = generate_idempotency_key(f"post_{platform}_{post_id}")
                    
                    # Create post record
                    post_record = {
                        "id": platform_post_id,
                        "main_post_id": post_id,
                        "platform": platform,
                        "status": "queued",
                        "idempotency_key": idempotency_key,
                        "text_content": text_content,
                        "media_assets": [asset["id"] for asset in media_assets],
                        "metadata": {
                            **metadata,
                            "platform": platform,
                            "correlation_id": correlation_id
                        },
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }
                    
                    # Save post record to database
                    # In real implementation:
                    # post_model = PostModel(**post_record)
                    # db_session.add(post_model)
                    
                    posts_created.append(post_record)
                    
                    logger.debug(
                        "Created post record for platform",
                        platform_post_id=platform_post_id,
                        platform=platform,
                        idempotency_key=idempotency_key
                    )
                    
                except Exception as e:
                    logger.error(
                        "Failed to create post record for platform",
                        platform=platform,
                        post_id=post_id,
                        error=str(e)
                    )
                    # Continue with other platforms
            
            # Commit all post records
            # db_session.commit()
            
            logger.info(
                "Created post records for platforms",
                post_id=post_id,
                platforms_created=len(posts_created),
                platforms=self.target_platforms
            )
            
        except Exception as e:
            logger.error(f"Failed to create posts for platforms: {e}")
            raise
        finally:
            if 'db_session' in locals():
                db_session.close()
        
        return posts_created
    
    async def close(self):
        """Close HTTP client and cleanup resources."""
        await self.http_client.aclose()
        logger.info("TelegramIntakeAdapter closed")


# Singleton instance
telegram_intake = TelegramIntakeAdapter()


# Convenience functions
async def process_telegram_update(update_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process Telegram Bot API update.
    
    Args:
        update_data: Telegram update data from webhook
        
    Returns:
        Processing result
    """
    return await telegram_intake.process_update(update_data)


async def cleanup_telegram_intake():
    """Cleanup Telegram intake resources."""
    await telegram_intake.close()


# Example usage and testing
if __name__ == "__main__":
    import asyncio
    import json
    
    # Example Telegram update with photo
    example_update = {
        "update_id": 123456789,
        "message": {
            "message_id": 1001,
            "from": {
                "id": 987654321,
                "is_bot": False,
                "first_name": "Test",
                "username": "testuser",
                "language_code": "ru"
            },
            "chat": {
                "id": -1001234567890,
                "title": "SalesWhisper Test Channel",
                "username": "saleswhisper_test",
                "type": "channel"
            },
            "date": int(time.time()),
            "photo": [
                {
                    "file_id": "AgACAgIAAxkDAAIB",
                    "file_unique_id": "AQADGAADr7cxG3I",
                    "file_size": 1234567,
                    "width": 1280,
                    "height": 960
                }
            ],
            "caption": "( >20O :>;;5:F8O SalesWhisper C65 2 ?@>4065! !B8;L=K5 ?;0BLO 4;O A>2@5<5==KE 65=I8=. #SalesWhisper #Fashion #Style"
        }
    }
    
    async def test_telegram_intake():
        """Test Telegram intake processing."""
        print("=== Testing Telegram Intake Adapter ===")
        
        try:
            result = await process_telegram_update(example_update)
            print(f" Success: {json.dumps(result, indent=2, ensure_ascii=False)}")
        except Exception as e:
            print(f"L Error: {e}")
        finally:
            await cleanup_telegram_intake()
    
    # Run test
    asyncio.run(test_telegram_intake())