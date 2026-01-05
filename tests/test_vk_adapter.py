"""
Unit tests for VK API adapter.

Tests for:
- Photo upload workflow (getWallUploadServer → saveWallPhoto)
- Video upload workflow (video.save → upload)
- Wall posting with attachments
- Rate limiting and retry logic
- VK API error handling and response parsing
- All major functions with mocked VK API responses
"""

import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.adapters.vk import (
    MediaType,
    PostStatus,
    VKAdapter,
    VKAuthError,
    VKError,
    VKMediaItem,
    VKPost,
    VKPublishResult,
    VKRateLimitError,
    VKUploadError,
    VKUploadResult,
    VKValidationError,
    cleanup_vk_adapter,
    delete_vk_post,
    get_vk_post_info,
    publish_vk_post,
)


class TestVKAdapterInitialization:
    """Test VK adapter initialization and configuration."""

    def test_adapter_initialization_success(self):
        """Test successful adapter initialization."""
        with patch('app.adapters.vk.settings.vk') as mock_settings:
            mock_settings.group_id = 123456789
            mock_settings.access_token.get_secret_value.return_value = "test_token"

            adapter = VKAdapter()

            assert adapter.group_id == 123456789
            assert adapter.access_token == "test_token"
            assert adapter.api_version == "5.131"
            assert adapter.api_base == "https://api.vk.com/method"
            assert adapter.rate_limit_per_second == 3
            assert isinstance(adapter.http_client, httpx.AsyncClient)

    def test_adapter_initialization_missing_token(self):
        """Test adapter initialization with missing access token."""
        with patch('app.adapters.vk.settings.vk') as mock_settings:
            # Remove access_token attribute
            delattr(mock_settings, 'access_token')

            with pytest.raises(VKAuthError) as exc_info:
                VKAdapter()

            assert "access token not configured" in str(exc_info.value)

    def test_get_access_token_variations(self):
        """Test access token retrieval with different configurations."""
        adapter = VKAdapter.__new__(VKAdapter)  # Create without __init__

        # Test with SecretStr-like object
        with patch('app.adapters.vk.settings.vk') as mock_settings:
            mock_token = MagicMock()
            mock_token.get_secret_value.return_value = "secret_token"
            mock_settings.access_token = mock_token

            token = adapter._get_access_token()
            assert token == "secret_token"

        # Test with string token
        with patch('app.adapters.vk.settings.vk') as mock_settings:
            mock_settings.access_token = "plain_string_token"

            token = adapter._get_access_token()
            assert token == "plain_string_token"


class TestPhotoUpload:
    """Test VK photo upload workflow."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mocked adapter for testing."""
        with patch('app.adapters.vk.settings.vk') as mock_settings:
            mock_settings.group_id = 123456789
            mock_settings.access_token.get_secret_value.return_value = "test_token"

            adapter = VKAdapter()
            return adapter

    @pytest.mark.asyncio
    async def test_upload_photo_success(self, mock_adapter):
        """Test successful photo upload workflow."""
        # Mock API responses
        upload_server_response = {
            "response": {
                "upload_url": "https://pu.vk.com/c123456/upload.php"
            }
        }

        upload_response = {
            "server": 123456,
            "photo": "[{\"photo\":\"encoded_photo_data\"}]",
            "hash": "abc123hash"
        }

        save_response = {
            "response": [{
                "id": 456789123,
                "owner_id": -123456789,
                "access_key": "def456key",
                "sizes": [{"width": 1080, "height": 1080}]
            }]
        }

        mock_responses = [upload_server_response, save_response]

        with patch.object(mock_adapter, '_make_api_request', side_effect=mock_responses) as mock_api, \
             patch.object(mock_adapter, '_upload_file_to_server', return_value=upload_response) as mock_upload:

            media_item = VKMediaItem(
                file_path="/test/photo.jpg",
                media_type=MediaType.PHOTO
            )

            result = await mock_adapter._upload_photo(media_item, "test_correlation_id")

            assert isinstance(result, VKUploadResult)
            assert result.media_type == MediaType.PHOTO
            assert result.attachment_string == "photo-123456789_456789123_def456key"
            assert result.vk_id == "456789123"
            assert result.upload_time > 0
            assert result.error_message is None

            # Verify API calls
            assert mock_api.call_count == 2

            # Verify getWallUploadServer call
            upload_server_call = mock_api.call_args_list[0]
            assert upload_server_call[0][0] == "photos.getWallUploadServer"
            assert upload_server_call[1]["params"]["group_id"] == 123456789

            # Verify saveWallPhoto call
            save_call = mock_api.call_args_list[1]
            assert save_call[0][0] == "photos.saveWallPhoto"
            assert save_call[1]["params"]["group_id"] == 123456789
            assert save_call[1]["params"]["photo"] == "[{\"photo\":\"encoded_photo_data\"}]"
            assert save_call[1]["params"]["server"] == 123456
            assert save_call[1]["params"]["hash"] == "abc123hash"

            # Verify file upload
            mock_upload.assert_called_once_with(
                "https://pu.vk.com/c123456/upload.php",
                "/test/photo.jpg",
                "photo"
            )

    @pytest.mark.asyncio
    async def test_upload_photo_without_access_key(self, mock_adapter):
        """Test photo upload without access key."""
        upload_server_response = {
            "response": {"upload_url": "https://pu.vk.com/upload.php"}
        }

        upload_response = {
            "server": 123456,
            "photo": "[{\"photo\":\"data\"}]",
            "hash": "hash123"
        }

        save_response = {
            "response": [{
                "id": 456789123,
                "owner_id": -123456789,
                # No access_key
                "sizes": [{"width": 640, "height": 480}]
            }]
        }

        with patch.object(mock_adapter, '_make_api_request', side_effect=[upload_server_response, save_response]), \
             patch.object(mock_adapter, '_upload_file_to_server', return_value=upload_response):

            media_item = VKMediaItem(
                file_path="/test/photo.jpg",
                media_type=MediaType.PHOTO
            )

            result = await mock_adapter._upload_photo(media_item)

            # Should not include access key in attachment string
            assert result.attachment_string == "photo-123456789_456789123"

    @pytest.mark.asyncio
    async def test_upload_photo_api_error(self, mock_adapter):
        """Test photo upload with API error."""
        with patch.object(mock_adapter, '_make_api_request', side_effect=VKError("API Error")):
            media_item = VKMediaItem(
                file_path="/test/photo.jpg",
                media_type=MediaType.PHOTO
            )

            result = await mock_adapter._upload_photo(media_item)

            assert result.media_type == MediaType.PHOTO
            assert result.attachment_string == ""
            assert result.error_message == "API Error"
            assert result.upload_time > 0

    @pytest.mark.asyncio
    async def test_upload_photo_file_upload_error(self, mock_adapter):
        """Test photo upload with file upload error."""
        upload_server_response = {
            "response": {"upload_url": "https://pu.vk.com/upload.php"}
        }

        with patch.object(mock_adapter, '_make_api_request', return_value=upload_server_response), \
             patch.object(mock_adapter, '_upload_file_to_server', side_effect=VKUploadError("Upload failed")):

            media_item = VKMediaItem(
                file_path="/test/photo.jpg",
                media_type=MediaType.PHOTO
            )

            result = await mock_adapter._upload_photo(media_item)

            assert result.attachment_string == ""
            assert "Upload failed" in result.error_message


class TestVideoUpload:
    """Test VK video upload workflow."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mocked adapter for testing."""
        with patch('app.adapters.vk.settings.vk') as mock_settings:
            mock_settings.group_id = 123456789
            mock_settings.access_token.get_secret_value.return_value = "test_token"

            adapter = VKAdapter()
            return adapter

    @pytest.mark.asyncio
    async def test_upload_video_success(self, mock_adapter):
        """Test successful video upload workflow."""
        # Mock video.save response
        video_save_response = {
            "response": {
                "video_id": 987654321,
                "owner_id": -123456789,
                "upload_url": "https://vu.vk.com/upload_video.php",
                "access_key": "video_access_key"
            }
        }

        # Mock upload response
        upload_response = {
            "size": 10485760,
            "video_id": 987654321
        }

        with patch.object(mock_adapter, '_make_api_request', return_value=video_save_response) as mock_api, \
             patch.object(mock_adapter, '_upload_file_to_server', return_value=upload_response) as mock_upload, \
             patch('pathlib.Path.exists', return_value=True), \
             patch('pathlib.Path.stat') as mock_stat:

            # Mock file size
            mock_stat.return_value.st_size = 10485760

            media_item = VKMediaItem(
                file_path="/test/video.mp4",
                media_type=MediaType.VIDEO,
                title="Test Video",
                description="A test video"
            )

            result = await mock_adapter._upload_video(media_item, "test_correlation_id")

            assert isinstance(result, VKUploadResult)
            assert result.media_type == MediaType.VIDEO
            assert result.attachment_string == "video-123456789_987654321_video_access_key"
            assert result.vk_id == "987654321"
            assert result.file_size == 10485760
            assert result.upload_time > 0
            assert result.error_message is None

            # Verify video.save API call
            mock_api.assert_called_once_with(
                "video.save",
                params={
                    "group_id": 123456789,
                    "name": "Test Video",
                    "description": "A test video",
                    "is_private": 0,
                    "wallpost": 1
                }
            )

            # Verify file upload
            mock_upload.assert_called_once_with(
                "https://vu.vk.com/upload_video.php",
                "/test/video.mp4",
                "video_file"
            )

    @pytest.mark.asyncio
    async def test_upload_video_without_access_key(self, mock_adapter):
        """Test video upload without access key."""
        video_save_response = {
            "response": {
                "video_id": 987654321,
                "owner_id": -123456789,
                "upload_url": "https://vu.vk.com/upload.php"
                # No access_key
            }
        }

        upload_response = {"video_id": 987654321}

        with patch.object(mock_adapter, '_make_api_request', return_value=video_save_response), \
             patch.object(mock_adapter, '_upload_file_to_server', return_value=upload_response), \
             patch('pathlib.Path.exists', return_value=True), \
             patch('pathlib.Path.stat') as mock_stat:

            mock_stat.return_value.st_size = 5242880

            media_item = VKMediaItem(
                file_path="/test/video.mp4",
                media_type=MediaType.VIDEO,
                title="Test Video"
            )

            result = await mock_adapter._upload_video(media_item)

            # Should not include access key in attachment string
            assert result.attachment_string == "video-123456789_987654321"

    @pytest.mark.asyncio
    async def test_upload_video_with_upload_error(self, mock_adapter):
        """Test video upload with upload server error."""
        video_save_response = {
            "response": {
                "video_id": 987654321,
                "owner_id": -123456789,
                "upload_url": "https://vu.vk.com/upload.php"
            }
        }

        # Upload response with error
        upload_response = {
            "error": "File too large"
        }

        with patch.object(mock_adapter, '_make_api_request', return_value=video_save_response), \
             patch.object(mock_adapter, '_upload_file_to_server', return_value=upload_response), \
             patch('pathlib.Path.exists', return_value=True), \
             patch('pathlib.Path.stat') as mock_stat:

            mock_stat.return_value.st_size = 104857600

            media_item = VKMediaItem(
                file_path="/test/large_video.mp4",
                media_type=MediaType.VIDEO
            )

            result = await mock_adapter._upload_video(media_item)

            assert result.attachment_string == ""
            assert "File too large" in result.error_message

    @pytest.mark.asyncio
    async def test_upload_video_file_not_found(self, mock_adapter):
        """Test video upload with missing file."""
        with patch('pathlib.Path.exists', return_value=False):
            media_item = VKMediaItem(
                file_path="/test/nonexistent.mp4",
                media_type=MediaType.VIDEO
            )

            result = await mock_adapter._upload_video(media_item)

            assert result.attachment_string == ""
            assert result.error_message is not None


class TestWallPosting:
    """Test VK wall posting functionality."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mocked adapter for testing."""
        with patch('app.adapters.vk.settings.vk') as mock_settings:
            mock_settings.group_id = 123456789
            mock_settings.access_token.get_secret_value.return_value = "test_token"

            adapter = VKAdapter()
            return adapter

    @pytest.mark.asyncio
    async def test_post_to_wall_success(self, mock_adapter):
        """Test successful wall posting."""
        wall_response = {
            "response": {
                "post_id": 456
            }
        }

        with patch.object(mock_adapter, '_make_api_request', return_value=wall_response) as mock_api:
            post = VKPost(
                message="Test wall post",
                media_items=[],
                from_group=True,
                signed=False
            )

            attachments = ["photo-123456789_456789123", "video-123456789_987654321"]

            result = await mock_adapter._post_to_wall(post, attachments, "test_correlation_id")

            assert isinstance(result, VKPublishResult)
            assert result.post_id == 456
            assert result.status == PostStatus.PUBLISHED
            assert result.message == "Post published successfully"
            assert result.post_url == "https://vk.com/wall-123456789_456"
            assert result.published_at is not None

            # Verify API call
            mock_api.assert_called_once_with(
                "wall.post",
                params={
                    "owner_id": "-123456789",
                    "from_group": 1,
                    "message": "Test wall post",
                    "attachments": "photo-123456789_456789123,video-123456789_987654321",
                    "signed": 0,
                    "mark_as_ads": 0
                }
            )

    @pytest.mark.asyncio
    async def test_post_to_wall_scheduled(self, mock_adapter):
        """Test scheduled wall posting."""
        wall_response = {
            "response": {
                "post_id": 789
            }
        }

        schedule_time = datetime.now(timezone.utc) + timedelta(hours=2)

        with patch.object(mock_adapter, '_make_api_request', return_value=wall_response) as mock_api:
            post = VKPost(
                message="Scheduled post",
                media_items=[],
                publish_date=schedule_time,
                signed=True,
                mark_as_ads=True
            )

            result = await mock_adapter._post_to_wall(post, [])

            assert result.status == PostStatus.PENDING
            assert result.published_at == schedule_time

            # Verify scheduled parameters
            call_params = mock_api.call_args[1]["params"]
            assert call_params["publish_date"] == int(schedule_time.timestamp())
            assert call_params["signed"] == 1
            assert call_params["mark_as_ads"] == 1

    @pytest.mark.asyncio
    async def test_post_to_wall_with_guid(self, mock_adapter):
        """Test wall posting with GUID for idempotency."""
        wall_response = {
            "response": {
                "post_id": 999
            }
        }

        with patch.object(mock_adapter, '_make_api_request', return_value=wall_response) as mock_api:
            post = VKPost(
                message="Post with GUID",
                media_items=[],
                guid="unique-guid-123"
            )

            await mock_adapter._post_to_wall(post, [])

            # Verify GUID parameter
            call_params = mock_api.call_args[1]["params"]
            assert call_params["guid"] == "unique-guid-123"

    @pytest.mark.asyncio
    async def test_post_to_wall_error(self, mock_adapter):
        """Test wall posting with error."""
        with patch.object(mock_adapter, '_make_api_request', side_effect=VKError("Access denied")):
            post = VKPost(
                message="Failed post",
                media_items=[]
            )

            with pytest.raises(VKError):
                await mock_adapter._post_to_wall(post, [])


class TestFileUpload:
    """Test file upload functionality."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mocked adapter for testing."""
        with patch('app.adapters.vk.settings.vk') as mock_settings:
            mock_settings.group_id = 123456789
            mock_settings.access_token.get_secret_value.return_value = "test_token"

            adapter = VKAdapter()
            return adapter

    @pytest.mark.asyncio
    async def test_upload_file_local_path(self, mock_adapter):
        """Test uploading local file."""
        mock_response = {
            "server": 123,
            "photo": "encoded_data",
            "hash": "abc123"
        }

        mock_http_response = MagicMock()
        mock_http_response.raise_for_status.return_value = None
        mock_http_response.aread.return_value = json.dumps(mock_response).encode('utf-8')

        file_content = b"fake_image_data"

        with patch('pathlib.Path.exists', return_value=True), \
             patch('pathlib.Path.read_bytes', return_value=file_content), \
             patch.object(mock_adapter.http_client, 'post') as mock_post:

            mock_post.return_value.__aenter__.return_value = mock_http_response

            result = await mock_adapter._upload_file_to_server(
                "https://upload.vk.com/test",
                "/local/image.jpg",
                "photo"
            )

            assert result == mock_response

            # Verify file upload call
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert call_args[0][0] == "https://upload.vk.com/test"
            assert "photo" in call_args[1]["files"]

    @pytest.mark.asyncio
    async def test_upload_file_url(self, mock_adapter):
        """Test uploading file from URL."""
        mock_upload_response = {
            "server": 456,
            "video": "video_data"
        }

        mock_download_response = MagicMock()
        mock_download_response.raise_for_status.return_value = None
        mock_download_response.aread.return_value = b"fake_video_data"

        mock_upload_http_response = MagicMock()
        mock_upload_http_response.raise_for_status.return_value = None
        mock_upload_http_response.aread.return_value = json.dumps(mock_upload_response).encode('utf-8')

        with patch.object(mock_adapter.http_client, 'get') as mock_get, \
             patch.object(mock_adapter.http_client, 'post') as mock_post:

            mock_get.return_value.__aenter__.return_value = mock_download_response
            mock_post.return_value.__aenter__.return_value = mock_upload_http_response

            result = await mock_adapter._upload_file_to_server(
                "https://upload.vk.com/test",
                "https://example.com/video.mp4",
                "video_file"
            )

            assert result == mock_upload_response

            # Verify download call
            mock_get.assert_called_once_with("https://example.com/video.mp4")

    @pytest.mark.asyncio
    async def test_upload_file_not_found(self, mock_adapter):
        """Test uploading non-existent local file."""
        with patch('pathlib.Path.exists', return_value=False):
            with pytest.raises(VKUploadError) as exc_info:
                await mock_adapter._upload_file_to_server(
                    "https://upload.vk.com/test",
                    "/nonexistent/file.jpg",
                    "photo"
                )

            assert "File not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_upload_file_invalid_json_response(self, mock_adapter):
        """Test upload with invalid JSON response."""
        mock_http_response = MagicMock()
        mock_http_response.raise_for_status.return_value = None
        mock_http_response.aread.return_value = b"invalid json {"

        file_content = b"fake_data"

        with patch('pathlib.Path.exists', return_value=True), \
             patch('pathlib.Path.read_bytes', return_value=file_content), \
             patch.object(mock_adapter.http_client, 'post') as mock_post:

            mock_post.return_value.__aenter__.return_value = mock_http_response

            with pytest.raises(VKUploadError) as exc_info:
                await mock_adapter._upload_file_to_server(
                    "https://upload.vk.com/test",
                    "/local/file.jpg",
                    "photo"
                )

            assert "Invalid upload response" in str(exc_info.value)


class TestRateLimiting:
    """Test VK API rate limiting."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mocked adapter for testing."""
        with patch('app.adapters.vk.settings.vk') as mock_settings:
            mock_settings.group_id = 123456789
            mock_settings.access_token.get_secret_value.return_value = "test_token"

            adapter = VKAdapter()
            return adapter

    @pytest.mark.asyncio
    async def test_rate_limit_normal_operation(self, mock_adapter):
        """Test normal operation within rate limits."""
        # Should allow 3 requests without waiting
        for _i in range(3):
            await mock_adapter._check_rate_limits()

        assert len(mock_adapter.last_request_times) == 3

    @pytest.mark.asyncio
    async def test_rate_limit_exceeded(self, mock_adapter):
        """Test rate limit enforcement."""
        # Fill up the rate limit
        current_time = time.time()
        mock_adapter.last_request_times = [current_time, current_time, current_time]

        with patch('asyncio.sleep') as mock_sleep:
            await mock_adapter._check_rate_limits()

            # Should wait before making request
            mock_sleep.assert_called_once()

    @pytest.mark.asyncio
    async def test_rate_limit_reset_old_requests(self, mock_adapter):
        """Test that old requests are removed from rate limit tracking."""
        # Add old request times (more than 1 second ago)
        old_time = time.time() - 2.0
        mock_adapter.last_request_times = [old_time, old_time, old_time]

        await mock_adapter._check_rate_limits()

        # Old requests should be removed, new one added
        assert len(mock_adapter.last_request_times) == 1
        assert mock_adapter.last_request_times[0] > old_time


class TestAPIErrorHandling:
    """Test VK API error handling."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mocked adapter for testing."""
        with patch('app.adapters.vk.settings.vk') as mock_settings:
            mock_settings.group_id = 123456789
            mock_settings.access_token.get_secret_value.return_value = "test_token"

            adapter = VKAdapter()
            return adapter

    @pytest.mark.asyncio
    async def test_handle_auth_error(self, mock_adapter):
        """Test handling of authentication errors."""
        error_data = {
            "error_code": 5,
            "error_msg": "User authorization failed: access_token has expired."
        }

        with pytest.raises(VKAuthError) as exc_info:
            await mock_adapter._handle_api_error(error_data)

        assert "Authorization failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_handle_rate_limit_errors(self, mock_adapter):
        """Test handling of rate limit errors."""
        rate_limit_errors = [
            {"error_code": 6, "error_msg": "Too many requests per second"},
            {"error_code": 9, "error_msg": "Flood control"}
        ]

        for error_data in rate_limit_errors:
            with pytest.raises(VKRateLimitError):
                await mock_adapter._handle_api_error(error_data)

    @pytest.mark.asyncio
    async def test_handle_validation_errors(self, mock_adapter):
        """Test handling of validation errors."""
        validation_errors = [
            {"error_code": 100, "error_msg": "One of the parameters specified was missing or invalid"},
            {"error_code": 113, "error_msg": "Invalid user id"},
            {"error_code": 18, "error_msg": "Page deleted or blocked"}
        ]

        for error_data in validation_errors:
            with pytest.raises(VKValidationError):
                await mock_adapter._handle_api_error(error_data)

    @pytest.mark.asyncio
    async def test_handle_access_denied_errors(self, mock_adapter):
        """Test handling of access denied errors."""
        access_errors = [
            {"error_code": 15, "error_msg": "Access denied"},
            {"error_code": 17, "error_msg": "Validation required"},
            {"error_code": 214, "error_msg": "Access to adding post denied"}
        ]

        for error_data in access_errors:
            with pytest.raises(VKAuthError):
                await mock_adapter._handle_api_error(error_data)

    @pytest.mark.asyncio
    async def test_handle_server_error(self, mock_adapter):
        """Test handling of server errors."""
        error_data = {
            "error_code": 10,
            "error_msg": "Internal server error"
        }

        with pytest.raises(VKError) as exc_info:
            await mock_adapter._handle_api_error(error_data)

        assert "VK server error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_handle_captcha_error(self, mock_adapter):
        """Test handling of captcha required error."""
        error_data = {
            "error_code": 14,
            "error_msg": "Captcha needed"
        }

        with pytest.raises(VKError) as exc_info:
            await mock_adapter._handle_api_error(error_data)

        assert "Captcha required" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_handle_unknown_error(self, mock_adapter):
        """Test handling of unknown errors."""
        error_data = {
            "error_code": 9999,
            "error_msg": "Unknown error"
        }

        with pytest.raises(VKError) as exc_info:
            await mock_adapter._handle_api_error(error_data)

        assert "VK API Error 9999" in str(exc_info.value)
        assert "Unknown error" in str(exc_info.value)


class TestAPIRequests:
    """Test VK API request handling."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mocked adapter for testing."""
        with patch('app.adapters.vk.settings.vk') as mock_settings:
            mock_settings.group_id = 123456789
            mock_settings.access_token.get_secret_value.return_value = "test_token"

            adapter = VKAdapter()
            return adapter

    @pytest.mark.asyncio
    async def test_make_api_request_success(self, mock_adapter):
        """Test successful API request."""
        mock_response_data = {
            "response": {
                "items": [{"id": 1, "text": "test"}]
            }
        }

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = mock_response_data

        with patch.object(mock_adapter.http_client, 'post', return_value=mock_response), \
             patch.object(mock_adapter, '_check_rate_limits', return_value=None):

            result = await mock_adapter._make_api_request(
                "wall.get",
                params={"count": 10}
            )

            assert result == mock_response_data

    @pytest.mark.asyncio
    async def test_make_api_request_with_error_response(self, mock_adapter):
        """Test API request with error in response."""
        error_response = {
            "error": {
                "error_code": 100,
                "error_msg": "Invalid parameters"
            }
        }

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = error_response

        with patch.object(mock_adapter.http_client, 'post', return_value=mock_response), \
             patch.object(mock_adapter, '_check_rate_limits', return_value=None), \
             patch.object(mock_adapter, '_handle_api_error', side_effect=VKValidationError("Invalid parameters")):

            with pytest.raises(VKValidationError):
                await mock_adapter._make_api_request("wall.get")

    @pytest.mark.asyncio
    async def test_make_api_request_network_error(self, mock_adapter):
        """Test API request with network error."""
        with patch.object(mock_adapter.http_client, 'post', side_effect=httpx.RequestError("Network error")), \
             patch.object(mock_adapter, '_check_rate_limits', return_value=None):

            with pytest.raises(httpx.RequestError):
                await mock_adapter._make_api_request("wall.get")


class TestPostManagement:
    """Test post management functions."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mocked adapter for testing."""
        with patch('app.adapters.vk.settings.vk') as mock_settings:
            mock_settings.group_id = 123456789
            mock_settings.access_token.get_secret_value.return_value = "test_token"

            adapter = VKAdapter()
            return adapter

    @pytest.mark.asyncio
    async def test_get_post_info_success(self, mock_adapter):
        """Test successful post info retrieval."""
        post_response = {
            "response": {
                "items": [{
                    "id": 456,
                    "owner_id": -123456789,
                    "date": 1640995200,
                    "text": "Test post",
                    "attachments": [
                        {"type": "photo", "photo": {"id": 789}}
                    ],
                    "post_type": "post"
                }]
            }
        }

        with patch.object(mock_adapter, '_make_api_request', return_value=post_response):
            result = await mock_adapter.get_post_info(-123456789, 456)

            assert result["id"] == 456
            assert result["owner_id"] == -123456789
            assert result["text"] == "Test post"
            assert result["url"] == "https://vk.com/wall-123456789_456"
            assert len(result["attachments"]) == 1

    @pytest.mark.asyncio
    async def test_get_post_info_not_found(self, mock_adapter):
        """Test post info retrieval when post not found."""
        empty_response = {
            "response": {
                "items": []
            }
        }

        with patch.object(mock_adapter, '_make_api_request', return_value=empty_response):
            result = await mock_adapter.get_post_info(-123456789, 999)

            assert result == {}

    @pytest.mark.asyncio
    async def test_get_post_info_error(self, mock_adapter):
        """Test post info retrieval with error."""
        with patch.object(mock_adapter, '_make_api_request', side_effect=VKError("Access denied")):
            result = await mock_adapter.get_post_info(-123456789, 456)

            assert result == {}

    @pytest.mark.asyncio
    async def test_delete_post_success(self, mock_adapter):
        """Test successful post deletion."""
        delete_response = {
            "response": 1
        }

        with patch.object(mock_adapter, '_make_api_request', return_value=delete_response):
            result = await mock_adapter.delete_post(-123456789, 456)

            assert result is True

    @pytest.mark.asyncio
    async def test_delete_post_error(self, mock_adapter):
        """Test post deletion with error."""
        with patch.object(mock_adapter, '_make_api_request', side_effect=VKError("Access denied")):
            result = await mock_adapter.delete_post(-123456789, 456)

            assert result is False


class TestFullPublishWorkflow:
    """Test complete publish workflow."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mocked adapter for testing."""
        with patch('app.adapters.vk.settings.vk') as mock_settings:
            mock_settings.group_id = 123456789
            mock_settings.access_token.get_secret_value.return_value = "test_token"

            adapter = VKAdapter()
            return adapter

    @pytest.mark.asyncio
    async def test_publish_post_with_media_success(self, mock_adapter):
        """Test complete publish workflow with media."""
        # Mock photo upload
        photo_upload_result = VKUploadResult(
            media_type=MediaType.PHOTO,
            attachment_string="photo-123456789_456789123",
            upload_time=1.5,
            file_size=1024000,
            vk_id="456789123"
        )

        # Mock video upload
        video_upload_result = VKUploadResult(
            media_type=MediaType.VIDEO,
            attachment_string="video-123456789_987654321",
            upload_time=5.2,
            file_size=10485760,
            vk_id="987654321"
        )

        # Mock wall post result
        wall_result = VKPublishResult(
            post_id=789,
            status=PostStatus.PUBLISHED,
            message="Post published successfully",
            post_url="https://vk.com/wall-123456789_789",
            published_at=datetime.now(timezone.utc)
        )

        media_items = [
            VKMediaItem(file_path="/test/photo.jpg", media_type=MediaType.PHOTO),
            VKMediaItem(file_path="/test/video.mp4", media_type=MediaType.VIDEO)
        ]

        post = VKPost(
            message="Test post with media",
            media_items=media_items
        )

        with patch.object(mock_adapter, '_upload_photo', return_value=photo_upload_result) as mock_photo, \
             patch.object(mock_adapter, '_upload_video', return_value=video_upload_result) as mock_video, \
             patch.object(mock_adapter, '_post_to_wall', return_value=wall_result) as mock_wall:

            result = await mock_adapter.publish_post(post, "test_correlation_id")

            assert result.post_id == 789
            assert result.status == PostStatus.PUBLISHED
            assert result.post_url == "https://vk.com/wall-123456789_789"

            # Verify upload calls
            mock_photo.assert_called_once_with(media_items[0], "test_correlation_id")
            mock_video.assert_called_once_with(media_items[1], "test_correlation_id")

            # Verify wall post call
            mock_wall.assert_called_once()
            wall_call_args = mock_wall.call_args
            attachments = wall_call_args[0][1]  # Second positional argument
            assert "photo-123456789_456789123" in attachments
            assert "video-123456789_987654321" in attachments

    @pytest.mark.asyncio
    async def test_publish_post_text_only(self, mock_adapter):
        """Test publishing text-only post."""
        wall_result = VKPublishResult(
            post_id=123,
            status=PostStatus.PUBLISHED,
            message="Post published successfully",
            post_url="https://vk.com/wall-123456789_123",
            published_at=datetime.now(timezone.utc)
        )

        post = VKPost(
            message="Text only post",
            media_items=[]
        )

        with patch.object(mock_adapter, '_post_to_wall', return_value=wall_result) as mock_wall:
            result = await mock_adapter.publish_post(post)

            assert result.post_id == 123
            assert result.status == PostStatus.PUBLISHED

            # Verify wall post with empty attachments
            mock_wall.assert_called_once()
            attachments = mock_wall.call_args[0][1]
            assert attachments == []

    @pytest.mark.asyncio
    async def test_publish_post_upload_failure(self, mock_adapter):
        """Test publish workflow with upload failure."""
        # Mock failed photo upload
        photo_upload_result = VKUploadResult(
            media_type=MediaType.PHOTO,
            attachment_string="",  # Empty indicates failure
            upload_time=1.0,
            file_size=0,
            error_message="Upload failed"
        )

        media_items = [
            VKMediaItem(file_path="/test/photo.jpg", media_type=MediaType.PHOTO)
        ]

        post = VKPost(
            message="Post with failed upload",
            media_items=media_items
        )

        # Mock wall post result for text-only fallback
        wall_result = VKPublishResult(
            post_id=456,
            status=PostStatus.PUBLISHED,
            message="Post published successfully",
            post_url="https://vk.com/wall-123456789_456"
        )

        with patch.object(mock_adapter, '_upload_photo', return_value=photo_upload_result), \
             patch.object(mock_adapter, '_post_to_wall', return_value=wall_result) as mock_wall:

            result = await mock_adapter.publish_post(post)

            assert result.post_id == 456

            # Should post without attachments since upload failed
            attachments = mock_wall.call_args[0][1]
            assert attachments == []


class TestConvenienceFunctions:
    """Test convenience functions."""

    @pytest.mark.asyncio
    async def test_publish_vk_post_with_media(self):
        """Test publish_vk_post convenience function."""
        mock_result = VKPublishResult(
            post_id=123,
            status=PostStatus.PUBLISHED,
            message="Published successfully",
            post_url="https://vk.com/wall-123456789_123"
        )

        with patch('app.adapters.vk.vk_adapter') as mock_adapter:
            mock_adapter.publish_post.return_value = mock_result

            result = await publish_vk_post(
                message="Test post",
                media_files=["/path/to/image.jpg", "/path/to/video.mp4"],
                correlation_id="test_correlation"
            )

            assert result.post_id == 123
            assert result.status == PostStatus.PUBLISHED

            # Verify adapter call
            mock_adapter.publish_post.assert_called_once()
            call_args = mock_adapter.publish_post.call_args
            post_arg = call_args[0][0]
            assert post_arg.message == "Test post"
            assert len(post_arg.media_items) == 2
            assert post_arg.media_items[0].media_type == MediaType.PHOTO
            assert post_arg.media_items[1].media_type == MediaType.VIDEO

    @pytest.mark.asyncio
    async def test_publish_vk_post_text_only(self):
        """Test publish_vk_post without media."""
        mock_result = VKPublishResult(
            post_id=456,
            status=PostStatus.PUBLISHED,
            message="Published successfully"
        )

        with patch('app.adapters.vk.vk_adapter') as mock_adapter:
            mock_adapter.publish_post.return_value = mock_result

            result = await publish_vk_post(
                message="Text only post",
                from_group=False,
                publish_date=datetime.now(timezone.utc) + timedelta(hours=1)
            )

            assert result.post_id == 456

            # Verify post configuration
            call_args = mock_adapter.publish_post.call_args
            post_arg = call_args[0][0]
            assert post_arg.from_group is False
            assert post_arg.publish_date is not None
            assert len(post_arg.media_items) == 0

    @pytest.mark.asyncio
    async def test_get_vk_post_info(self):
        """Test get_vk_post_info convenience function."""
        expected_info = {
            "id": 123,
            "owner_id": -123456789,
            "text": "Test post",
            "url": "https://vk.com/wall-123456789_123"
        }

        with patch('app.adapters.vk.vk_adapter') as mock_adapter:
            mock_adapter.get_post_info.return_value = expected_info

            result = await get_vk_post_info(-123456789, 123)

            assert result == expected_info
            mock_adapter.get_post_info.assert_called_once_with(-123456789, 123)

    @pytest.mark.asyncio
    async def test_delete_vk_post(self):
        """Test delete_vk_post convenience function."""
        with patch('app.adapters.vk.vk_adapter') as mock_adapter:
            mock_adapter.delete_post.return_value = True

            result = await delete_vk_post(-123456789, 123)

            assert result is True
            mock_adapter.delete_post.assert_called_once_with(-123456789, 123)

    @pytest.mark.asyncio
    async def test_cleanup_vk_adapter(self):
        """Test cleanup_vk_adapter convenience function."""
        with patch('app.adapters.vk.vk_adapter') as mock_adapter:
            mock_adapter.close = AsyncMock()

            await cleanup_vk_adapter()

            mock_adapter.close.assert_called_once()


class TestDataclassValidation:
    """Test dataclass validation and behavior."""

    def test_vk_media_item_creation(self):
        """Test VKMediaItem creation and defaults."""
        media_item = VKMediaItem(
            file_path="/test/photo.jpg",
            media_type=MediaType.PHOTO
        )

        assert media_item.file_path == "/test/photo.jpg"
        assert media_item.media_type == MediaType.PHOTO
        assert media_item.title is None
        assert media_item.description is None
        assert media_item.tags == []  # Should be initialized by __post_init__

    def test_vk_media_item_with_all_fields(self):
        """Test VKMediaItem with all fields populated."""
        media_item = VKMediaItem(
            file_path="/test/video.mp4",
            media_type=MediaType.VIDEO,
            title="Test Video",
            description="A test video",
            tags=["test", "video"]
        )

        assert media_item.title == "Test Video"
        assert media_item.description == "A test video"
        assert media_item.tags == ["test", "video"]

    def test_vk_post_creation(self):
        """Test VKPost creation and defaults."""
        media_items = [
            VKMediaItem(file_path="/test/photo.jpg", media_type=MediaType.PHOTO)
        ]

        post = VKPost(
            message="Test post",
            media_items=media_items
        )

        assert post.message == "Test post"
        assert len(post.media_items) == 1
        assert post.owner_id is None
        assert post.from_group is True
        assert post.signed is False
        assert post.mark_as_ads is False
        assert post.publish_date is None
        assert post.guid is None

    def test_vk_post_with_optional_fields(self):
        """Test VKPost with optional fields."""
        publish_date = datetime.now(timezone.utc) + timedelta(hours=1)

        post = VKPost(
            message="Test post",
            media_items=[],
            owner_id=-123456789,
            from_group=False,
            signed=True,
            mark_as_ads=True,
            publish_date=publish_date,
            guid="unique-guid-123"
        )

        assert post.owner_id == -123456789
        assert post.from_group is False
        assert post.signed is True
        assert post.mark_as_ads is True
        assert post.publish_date == publish_date
        assert post.guid == "unique-guid-123"


if __name__ == "__main__":
    # Run specific tests
    pytest.main([__file__, "-v"])
