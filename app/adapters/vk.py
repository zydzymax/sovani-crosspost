"""VK API adapter for SalesWhisper Crosspost."""

import asyncio
import mimetypes
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional, Union
from dataclasses import dataclass
from enum import Enum
import json

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from ..core.config import settings
from ..core.logging import get_logger, with_logging_context
from ..core.security import SecurityUtils
from ..observability.metrics import metrics


logger = get_logger("adapters.vk")


class VKError(Exception):
    """Base exception for VK API errors."""
    pass


class VKRateLimitError(VKError):
    """Raised when VK API rate limit is exceeded."""
    pass


class VKAuthError(VKError):
    """Raised when VK API authentication fails."""
    pass


class VKValidationError(VKError):
    """Raised when VK API validation fails."""
    pass


class VKUploadError(VKError):
    """Raised when VK media upload fails."""
    pass


class MediaType(Enum):
    """VK media types."""
    PHOTO = "photo"
    VIDEO = "video"
    DOCUMENT = "doc"


class PostStatus(Enum):
    """VK post status."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PUBLISHED = "published"
    ERROR = "error"


@dataclass
class VKMediaItem:
    """Represents a media item for VK."""
    file_path: str
    media_type: MediaType
    title: Optional[str] = None
    description: Optional[str] = None
    tags: List[str] = None
    
    def __post_init__(self):
        if self.tags is None:
            self.tags = []


@dataclass
class VKPost:
    """Represents a VK wall post."""
    message: str
    media_items: List[VKMediaItem]
    owner_id: Optional[int] = None
    from_group: bool = True
    signed: bool = False
    mark_as_ads: bool = False
    publish_date: Optional[datetime] = None
    guid: Optional[str] = None
    
    def __post_init__(self):
        if self.media_items is None:
            self.media_items = []


@dataclass
class VKUploadResult:
    """Result of media upload to VK."""
    media_type: MediaType
    attachment_string: str
    upload_time: float
    file_size: int
    vk_id: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class VKPublishResult:
    """Result of VK wall post publishing."""
    post_id: Optional[int]
    status: PostStatus
    message: str
    post_url: Optional[str] = None
    published_at: Optional[datetime] = None
    error_code: Optional[int] = None
    retry_after: Optional[int] = None


class VKAdapter:
    """VK API adapter."""
    
    def __init__(self):
        """Initialize VK adapter."""
        self.access_token = self._get_access_token()
        self.group_id = settings.vk.group_id if hasattr(settings.vk, 'group_id') else None
        self.api_version = "5.131"
        self.api_base = "https://api.vk.com/method"
        
        # HTTP client configuration
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0),  # VK uploads can be slow
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            headers={
                "User-Agent": "SalesWhisper-Crosspost/1.0"
            }
        )
        
        # Rate limiting - VK allows 3 requests per second
        self.rate_limit_per_second = 3
        self.last_request_times = []
        self.rate_limit_lock = asyncio.Lock()
        
        logger.info("VK adapter initialized", group_id=self.group_id, api_version=self.api_version)
    
    def _get_access_token(self) -> str:
        """Get VK access token from settings."""
        # First try direct settings access
        if hasattr(settings, 'vk') and hasattr(settings.vk, 'service_token'):
            token = settings.vk.service_token
            if hasattr(token, 'get_secret_value'):
                return token.get_secret_value()
            return str(token)
        
        # Fallback to environment variable
        import os
        token = os.getenv('VK_SERVICE_TOKEN')
        if token:
            return token
            
        raise VKAuthError("VK service token not configured. Set VK_SERVICE_TOKEN environment variable.")
    
    async def publish_post(self, post: VKPost, correlation_id: str = None) -> VKPublishResult:
        """Publish post to VK wall."""
        start_time = time.time()
        
        with with_logging_context(correlation_id=correlation_id):
            logger.info(
                "Publishing VK post",
                message_length=len(post.message),
                media_count=len(post.media_items),
                owner_id=post.owner_id,
                is_scheduled=bool(post.publish_date)
            )
            
            try:
                # Upload media items first
                attachments = []
                for i, media_item in enumerate(post.media_items):
                    logger.info(f"Uploading media item {i+1}/{len(post.media_items)}")
                    
                    if media_item.media_type == MediaType.PHOTO:
                        upload_result = await self._upload_photo(media_item, correlation_id)
                    elif media_item.media_type == MediaType.VIDEO:
                        upload_result = await self._upload_video(media_item, correlation_id)
                    else:
                        raise VKValidationError(f"Unsupported media type: {media_item.media_type}")
                    
                    if upload_result.attachment_string:
                        attachments.append(upload_result.attachment_string)
                
                # Publish wall post
                wall_result = await self._post_to_wall(post, attachments, correlation_id)
                
                processing_time = time.time() - start_time
                
                # Track metrics
                metrics.track_external_api_call(
                    service="vk",
                    endpoint="wall_post",
                    status_code=200,
                    duration=processing_time
                )
                
                logger.info(
                    "VK post published successfully",
                    post_id=wall_result.post_id,
                    attachments_count=len(attachments),
                    processing_time=processing_time,
                    post_url=wall_result.post_url
                )
                
                return wall_result
                
            except Exception as e:
                processing_time = time.time() - start_time
                
                # Track failure metrics
                metrics.track_external_api_call(
                    service="vk",
                    endpoint="wall_post",
                    status_code=getattr(e, 'error_code', 500),
                    duration=processing_time,
                    error=str(e)
                )
                
                logger.error(
                    "Failed to publish VK post",
                    error=str(e),
                    processing_time=processing_time,
                    exc_info=True
                )
                
                return VKPublishResult(
                    post_id=None,
                    status=PostStatus.ERROR,
                    message=str(e),
                    error_code=getattr(e, 'error_code', None),
                    retry_after=getattr(e, 'retry_after', None)
                )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        retry=retry_if_exception_type((httpx.RequestError, VKRateLimitError))
    )
    async def _upload_photo(self, media_item: VKMediaItem, correlation_id: str = None) -> VKUploadResult:
        """Upload photo to VK using getWallUploadServer � saveWallPhoto workflow."""
        start_time = time.time()
        
        with with_logging_context(correlation_id=correlation_id, media_type="photo"):
            logger.info("Starting VK photo upload", file_path=media_item.file_path)
            
            try:
                # Step 1: Get upload server URL
                upload_server_response = await self._make_api_request(
                    "photos.getWallUploadServer",
                    params={
                        "group_id": self.group_id
                    }
                )
                
                upload_url = upload_server_response["response"]["upload_url"]
                
                # Step 2: Upload photo to server
                upload_response = await self._upload_file_to_server(
                    upload_url,
                    media_item.file_path,
                    "photo"
                )
                
                # Step 3: Save uploaded photo
                save_response = await self._make_api_request(
                    "photos.saveWallPhoto",
                    params={
                        "group_id": self.group_id,
                        "photo": upload_response.get("photo"),
                        "server": upload_response.get("server"),
                        "hash": upload_response.get("hash")
                    }
                )
                
                photo_data = save_response["response"][0]
                attachment_string = f"photo{photo_data['owner_id']}_{photo_data['id']}"
                
                if photo_data.get("access_key"):
                    attachment_string += f"_{photo_data['access_key']}"
                
                upload_time = time.time() - start_time
                
                logger.info(
                    "VK photo uploaded successfully",
                    attachment_string=attachment_string,
                    upload_time=upload_time,
                    photo_id=photo_data.get('id')
                )
                
                return VKUploadResult(
                    media_type=MediaType.PHOTO,
                    attachment_string=attachment_string,
                    upload_time=upload_time,
                    file_size=photo_data.get('sizes', [{}])[-1].get('height', 0) * photo_data.get('sizes', [{}])[-1].get('width', 0),
                    vk_id=str(photo_data.get('id'))
                )
                
            except Exception as e:
                upload_time = time.time() - start_time
                
                logger.error(
                    "Failed to upload VK photo",
                    file_path=media_item.file_path,
                    error=str(e),
                    upload_time=upload_time,
                    exc_info=True
                )
                
                return VKUploadResult(
                    media_type=MediaType.PHOTO,
                    attachment_string="",
                    upload_time=upload_time,
                    file_size=0,
                    error_message=str(e)
                )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=12),
        retry=retry_if_exception_type((httpx.RequestError, VKRateLimitError))
    )
    async def _upload_video(self, media_item: VKMediaItem, correlation_id: str = None) -> VKUploadResult:
        """Upload video to VK using video.save workflow."""
        start_time = time.time()
        
        with with_logging_context(correlation_id=correlation_id, media_type="video"):
            logger.info("Starting VK video upload", file_path=media_item.file_path)
            
            try:
                # Get file info
                file_path = Path(media_item.file_path)
                file_size = file_path.stat().st_size if file_path.exists() else 0
                
                # Step 1: Create video entry
                video_save_response = await self._make_api_request(
                    "video.save",
                    params={
                        "group_id": self.group_id,
                        "name": media_item.title or file_path.stem,
                        "description": media_item.description or "",
                        "is_private": 0,
                        "wallpost": 1
                    }
                )
                
                video_data = video_save_response["response"]
                upload_url = video_data["upload_url"]
                video_id = video_data["video_id"]
                owner_id = video_data["owner_id"]
                
                # Step 2: Upload video file
                upload_response = await self._upload_file_to_server(
                    upload_url,
                    media_item.file_path,
                    "video_file"
                )
                
                # VK video upload returns different structure
                if "error" in upload_response:
                    raise VKUploadError(f"Video upload failed: {upload_response['error']}")
                
                # Create attachment string
                attachment_string = f"video{owner_id}_{video_id}"
                
                if video_data.get("access_key"):
                    attachment_string += f"_{video_data['access_key']}"
                
                upload_time = time.time() - start_time
                
                logger.info(
                    "VK video uploaded successfully",
                    attachment_string=attachment_string,
                    upload_time=upload_time,
                    video_id=video_id,
                    file_size=file_size
                )
                
                return VKUploadResult(
                    media_type=MediaType.VIDEO,
                    attachment_string=attachment_string,
                    upload_time=upload_time,
                    file_size=file_size,
                    vk_id=str(video_id)
                )
                
            except Exception as e:
                upload_time = time.time() - start_time
                
                logger.error(
                    "Failed to upload VK video",
                    file_path=media_item.file_path,
                    error=str(e),
                    upload_time=upload_time,
                    exc_info=True
                )
                
                return VKUploadResult(
                    media_type=MediaType.VIDEO,
                    attachment_string="",
                    upload_time=upload_time,
                    file_size=file_size,
                    error_message=str(e)
                )

    async def _post_to_wall(self, post: VKPost, attachments: List[str], 
                          correlation_id: str = None) -> VKPublishResult:
        """Post to VK wall with attachments using wall.post."""
        with with_logging_context(correlation_id=correlation_id):
            try:
                params = {
                    "owner_id": f"-{self.group_id}" if self.group_id else None,
                    "from_group": 1 if post.from_group else 0,
                    "message": post.message,
                    "attachments": ",".join(attachments) if attachments else None,
                    "signed": 1 if post.signed else 0,
                    "mark_as_ads": 1 if post.mark_as_ads else 0,
                    "guid": post.guid
                }
                
                # Remove None values
                params = {k: v for k, v in params.items() if v is not None}
                
                # Handle scheduled posting
                if post.publish_date:
                    publish_timestamp = int(post.publish_date.timestamp())
                    params["publish_date"] = publish_timestamp
                
                response = await self._make_api_request("wall.post", params=params)
                
                post_data = response["response"]
                post_id = post_data["post_id"]
                
                # Construct post URL
                owner_id = post.owner_id or f"-{self.group_id}"
                post_url = f"https://vk.com/wall{owner_id}_{post_id}"
                
                return VKPublishResult(
                    post_id=post_id,
                    status=PostStatus.PUBLISHED if not post.publish_date else PostStatus.PENDING,
                    message="Post published successfully",
                    post_url=post_url,
                    published_at=datetime.now(timezone.utc) if not post.publish_date else post.publish_date
                )
                
            except Exception as e:
                logger.error(
                    "Failed to post to VK wall",
                    error=str(e),
                    attachments_count=len(attachments),
                    exc_info=True
                )
                raise

    async def _upload_file_to_server(self, upload_url: str, file_path: str, 
                                   field_name: str) -> Dict[str, Any]:
        """Upload file to VK upload server."""
        try:
            # Handle both URL and local file paths
            if file_path.startswith(('http://', 'https://')):
                # Download file from URL first
                async with self.http_client.get(file_path) as response:
                    response.raise_for_status()
                    file_content = await response.aread()
                    file_name = Path(file_path).name
            else:
                # Read local file
                file_path_obj = Path(file_path)
                if not file_path_obj.exists():
                    raise VKUploadError(f"File not found: {file_path}")
                
                file_content = file_path_obj.read_bytes()
                file_name = file_path_obj.name
            
            # Determine MIME type
            mime_type, _ = mimetypes.guess_type(file_name)
            mime_type = mime_type or "application/octet-stream"
            
            # Upload file
            files = {
                field_name: (file_name, file_content, mime_type)
            }
            
            async with self.http_client.post(upload_url, files=files) as response:
                response.raise_for_status()
                
                result = await response.aread()
                
                # VK returns JSON response
                try:
                    return json.loads(result.decode('utf-8'))
                except json.JSONDecodeError as e:
                    logger.error("Failed to parse upload response", response_text=result.decode('utf-8', errors='ignore'))
                    raise VKUploadError(f"Invalid upload response: {e}")
                
        except Exception as e:
            logger.error(f"File upload failed: {e}", file_path=file_path, upload_url=upload_url)
            raise VKUploadError(f"File upload failed: {e}")

    async def _check_rate_limits(self):
        """Check and enforce VK API rate limits (3 requests per second)."""
        async with self.rate_limit_lock:
            current_time = time.time()
            
            # Remove requests older than 1 second
            self.last_request_times = [
                t for t in self.last_request_times 
                if current_time - t < 1.0
            ]
            
            # Check if we can make a request
            if len(self.last_request_times) >= self.rate_limit_per_second:
                # Need to wait
                oldest_request = min(self.last_request_times)
                wait_time = 1.0 - (current_time - oldest_request)
                
                if wait_time > 0:
                    logger.warning(f"VK rate limit reached. Waiting {wait_time:.2f} seconds")
                    await asyncio.sleep(wait_time)
            
            # Record this request
            self.last_request_times.append(time.time())

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        retry=retry_if_exception_type((httpx.RequestError, VKRateLimitError))
    )
    async def _make_api_request(self, method: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Make authenticated API request to VK."""
        await self._check_rate_limits()
        
        # Prepare parameters
        request_params = {
            "access_token": self.access_token,
            "v": self.api_version
        }
        
        if params:
            request_params.update(params)
        
        # Make request
        url = f"{self.api_base}/{method}"
        
        try:
            response = await self.http_client.post(url, data=request_params)
            response.raise_for_status()
            
            result = response.json()
            
            # Handle VK API errors
            if "error" in result:
                await self._handle_api_error(result["error"])
            
            return result
            
        except httpx.RequestError as e:
            logger.warning(f"VK API request error, will retry: {e}")
            raise
        except Exception as e:
            logger.error(f"VK API request failed: {e}")
            raise

    async def _handle_api_error(self, error_data: Dict[str, Any]):
        """Handle VK API errors."""
        error_code = error_data.get("error_code")
        error_msg = error_data.get("error_msg", "Unknown error")
        
        logger.error(
            "VK API error",
            error_code=error_code,
            error_message=error_msg,
            request_params=error_data.get("request_params", {})
        )
        
        # Handle specific error codes
        if error_code == 5:  # User authorization failed
            raise VKAuthError(f"Authorization failed: {error_msg}")
        elif error_code == 6:  # Too many requests per second
            raise VKRateLimitError(f"Rate limit exceeded: {error_msg}")
        elif error_code == 9:  # Flood control
            raise VKRateLimitError(f"Flood control: {error_msg}")
        elif error_code == 10:  # Internal server error
            raise VKError(f"VK server error: {error_msg}")
        elif error_code == 14:  # Captcha needed
            raise VKError(f"Captcha required: {error_msg}")
        elif error_code == 15:  # Access denied
            raise VKAuthError(f"Access denied: {error_msg}")
        elif error_code == 17:  # Validation required
            raise VKAuthError(f"Validation required: {error_msg}")
        elif error_code == 18:  # Page deleted or blocked
            raise VKValidationError(f"Page deleted or blocked: {error_msg}")
        elif error_code == 100:  # One of the parameters specified was missing or invalid
            raise VKValidationError(f"Invalid parameters: {error_msg}")
        elif error_code == 113:  # Invalid user id
            raise VKValidationError(f"Invalid user ID: {error_msg}")
        elif error_code == 214:  # Access to adding post denied
            raise VKAuthError(f"Access to posting denied: {error_msg}")
        else:
            raise VKError(f"VK API Error {error_code}: {error_msg}")

    async def get_post_info(self, owner_id: int, post_id: int) -> Dict[str, Any]:
        """Get information about a VK post."""
        try:
            response = await self._make_api_request(
                "wall.getById",
                params={
                    "posts": f"{owner_id}_{post_id}",
                    "extended": 1,
                    "fields": "id,date,text,attachments,post_type"
                }
            )
            
            posts = response.get("response", {}).get("items", [])
            if not posts:
                return {}
            
            post = posts[0]
            return {
                "id": post.get("id"),
                "owner_id": post.get("owner_id"),
                "date": post.get("date"),
                "text": post.get("text"),
                "attachments": post.get("attachments", []),
                "post_type": post.get("post_type"),
                "url": f"https://vk.com/wall{post.get('owner_id')}_{post.get('id')}"
            }
            
        except Exception as e:
            logger.error(f"Failed to get VK post info: {e}", owner_id=owner_id, post_id=post_id)
            return {}

    async def delete_post(self, owner_id: int, post_id: int) -> bool:
        """Delete a VK post."""
        try:
            await self._make_api_request(
                "wall.delete",
                params={
                    "owner_id": owner_id,
                    "post_id": post_id
                }
            )
            
            logger.info("VK post deleted successfully", owner_id=owner_id, post_id=post_id)
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete VK post: {e}", owner_id=owner_id, post_id=post_id)
            return False

    async def update_post_status(self, post_id: str, status: str, result_data: Dict[str, Any] = None):
        """Update post status in database."""
        logger.info(
            "Updating VK post status",
            post_id=post_id,
            status=status,
            result_data=result_data
        )

    async def close(self):
        """Close HTTP client and cleanup resources."""
        await self.http_client.aclose()
        logger.info("VK adapter closed")


# Global adapter instance
vk_adapter = VKAdapter()


# Convenience functions
async def publish_vk_post(message: str, media_files: List[str] = None,
                         owner_id: int = None, from_group: bool = True,
                         publish_date: datetime = None,
                         correlation_id: str = None) -> VKPublishResult:
    """Publish post to VK."""
    # Convert file paths to VKMediaItem objects
    media_items = []
    if media_files:
        for file_path in media_files:
            # Detect media type based on file extension
            file_ext = Path(file_path).suffix.lower()
            if file_ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
                media_type = MediaType.PHOTO
            elif file_ext in ['.mp4', '.mov', '.avi', '.wmv', '.mkv']:
                media_type = MediaType.VIDEO
            else:
                # Default to photo for unknown extensions
                media_type = MediaType.PHOTO
            
            media_items.append(VKMediaItem(
                file_path=file_path,
                media_type=media_type,
                title=Path(file_path).stem
            ))
    
    post = VKPost(
        message=message,
        media_items=media_items,
        owner_id=owner_id,
        from_group=from_group,
        publish_date=publish_date
    )
    
    return await vk_adapter.publish_post(post, correlation_id)


async def get_vk_post_info(owner_id: int, post_id: int) -> Dict[str, Any]:
    """Get VK post information."""
    return await vk_adapter.get_post_info(owner_id, post_id)


async def delete_vk_post(owner_id: int, post_id: int) -> bool:
    """Delete VK post."""
    return await vk_adapter.delete_post(owner_id, post_id)


async def cleanup_vk_adapter():
    """Cleanup VK adapter resources."""
    await vk_adapter.close()
