"""
Unit tests for Instagram Graph API adapter.

Tests for:
- API response parsing and error handling
- Container creation and publishing workflows
- Rate limiting and retry logic
- Authentication error handling
- Media upload and validation
- Scheduled posting functionality
- All major functions with mocked Instagram API responses
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.adapters.instagram import (
    ContainerResult,
    ContainerType,
    InstagramAdapter,
    InstagramAuthError,
    InstagramError,
    InstagramPost,
    InstagramRateLimitError,
    InstagramValidationError,
    MediaItem,
    PublishResult,
    PublishStatus,
    cleanup_instagram_adapter,
    get_instagram_container_status,
    publish_instagram_post,
)


class TestInstagramAdapterInitialization:
    """Test adapter initialization and configuration."""

    def test_adapter_initialization_success(self):
        """Test successful adapter initialization."""
        with patch('app.adapters.instagram.settings.instagram') as mock_settings:
            mock_settings.page_id = "test_page_id"
            mock_settings.access_token.get_secret_value.return_value = "test_token"

            adapter = InstagramAdapter()

            assert adapter.page_id == "test_page_id"
            assert adapter.access_token == "test_token"
            assert adapter.api_base == "https://graph.facebook.com/v18.0"
            assert adapter.rate_limit_remaining == 200
            assert isinstance(adapter.http_client, httpx.AsyncClient)

    def test_adapter_initialization_missing_token(self):
        """Test adapter initialization with missing access token."""
        with patch('app.adapters.instagram.settings.instagram') as mock_settings:
            # Remove access_token attribute
            delattr(mock_settings, 'access_token')

            with pytest.raises(InstagramAuthError) as exc_info:
                InstagramAdapter()

            assert "access token not configured" in str(exc_info.value)

    def test_get_access_token_variations(self):
        """Test access token retrieval with different configurations."""
        adapter = InstagramAdapter.__new__(InstagramAdapter)  # Create without __init__

        # Test with SecretStr-like object
        with patch('app.adapters.instagram.settings.instagram') as mock_settings:
            mock_token = MagicMock()
            mock_token.get_secret_value.return_value = "secret_token"
            mock_settings.access_token = mock_token

            token = adapter._get_access_token()
            assert token == "secret_token"

        # Test with string token
        with patch('app.adapters.instagram.settings.instagram') as mock_settings:
            mock_settings.access_token = "plain_string_token"

            token = adapter._get_access_token()
            assert token == "plain_string_token"


class TestContainerCreation:
    """Test Instagram container creation."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mocked adapter for testing."""
        with patch('app.adapters.instagram.settings.instagram') as mock_settings:
            mock_settings.page_id = "test_page_id"
            mock_settings.access_token.get_secret_value.return_value = "test_token"

            adapter = InstagramAdapter()
            return adapter

    @pytest.mark.asyncio
    async def test_create_single_image_container_success(self, mock_adapter):
        """Test successful single image container creation."""
        # Mock API response
        mock_response = {
            "id": "container_123"
        }

        with patch.object(mock_adapter, '_make_api_request', return_value=mock_response) as mock_api:
            media_item = MediaItem(
                file_path="https://example.com/image.jpg",
                media_type=ContainerType.IMAGE
            )
            post = InstagramPost(
                caption="Test post",
                media_items=[media_item]
            )

            result = await mock_adapter.create_container(post, "test_correlation_id")

            assert isinstance(result, ContainerResult)
            assert result.container_id == "container_123"
            assert result.status == "created"
            assert result.created_at is not None
            assert result.error_message is None

            # Verify API call
            mock_api.assert_called_once()
            call_args = mock_api.call_args
            assert call_args[0][0] == "POST"  # method
            assert "/media" in call_args[0][1]  # URL
            assert call_args[1]["data"]["image_url"] == "https://example.com/image.jpg"
            assert call_args[1]["data"]["caption"] == "Test post"
            assert call_args[1]["data"]["media_type"] == "IMAGE"

    @pytest.mark.asyncio
    async def test_create_single_video_container_with_thumbnail(self, mock_adapter):
        """Test single video container creation with thumbnail."""
        mock_response = {"id": "video_container_456"}

        with patch.object(mock_adapter, '_make_api_request', return_value=mock_response):
            media_item = MediaItem(
                file_path="https://example.com/video.mp4",
                media_type=ContainerType.VIDEO,
                thumbnail_url="https://example.com/thumb.jpg"
            )
            post = InstagramPost(
                caption="Video post",
                media_items=[media_item]
            )

            result = await mock_adapter.create_container(post)

            assert result.container_id == "video_container_456"

    @pytest.mark.asyncio
    async def test_create_carousel_container_success(self, mock_adapter):
        """Test successful carousel container creation."""
        # Mock responses for individual items and carousel
        individual_responses = [
            {"id": "item_1"},
            {"id": "item_2"},
            {"id": "item_3"}
        ]
        carousel_response = {"id": "carousel_789"}

        responses = individual_responses + [carousel_response]

        with patch.object(mock_adapter, '_make_api_request', side_effect=responses):
            media_items = [
                MediaItem(file_path="https://example.com/img1.jpg", media_type=ContainerType.IMAGE),
                MediaItem(file_path="https://example.com/img2.jpg", media_type=ContainerType.IMAGE),
                MediaItem(file_path="https://example.com/img3.jpg", media_type=ContainerType.IMAGE)
            ]
            post = InstagramPost(
                caption="Carousel post",
                media_items=media_items
            )

            result = await mock_adapter.create_container(post)

            assert result.container_id == "carousel_789"
            assert result.status == "created"

    @pytest.mark.asyncio
    async def test_create_container_with_local_file(self, mock_adapter):
        """Test container creation with local file upload."""
        mock_response = {"id": "local_container_123"}

        with patch.object(mock_adapter, '_make_api_request', return_value=mock_response), \
             patch.object(mock_adapter, '_upload_media_file', return_value="https://cdn.example.com/uploaded.jpg"):

            media_item = MediaItem(
                file_path="/local/path/image.jpg",
                media_type=ContainerType.IMAGE
            )
            post = InstagramPost(
                caption="Local file post",
                media_items=[media_item]
            )

            result = await mock_adapter.create_container(post)

            assert result.container_id == "local_container_123"

    @pytest.mark.asyncio
    async def test_create_container_no_media_error(self, mock_adapter):
        """Test container creation with no media items."""
        post = InstagramPost(
            caption="No media post",
            media_items=[]
        )

        with pytest.raises(InstagramValidationError) as exc_info:
            await mock_adapter.create_container(post)

        assert "No media items provided" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_create_container_api_error(self, mock_adapter):
        """Test container creation with API error."""
        with patch.object(mock_adapter, '_make_api_request', side_effect=InstagramError("API Error")):
            media_item = MediaItem(
                file_path="https://example.com/image.jpg",
                media_type=ContainerType.IMAGE
            )
            post = InstagramPost(
                caption="Test post",
                media_items=[media_item]
            )

            with pytest.raises(InstagramError):
                await mock_adapter.create_container(post)


class TestContainerPublishing:
    """Test container publishing functionality."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mocked adapter for testing."""
        with patch('app.adapters.instagram.settings.instagram') as mock_settings:
            mock_settings.page_id = "test_page_id"
            mock_settings.access_token.get_secret_value.return_value = "test_token"

            adapter = InstagramAdapter()
            return adapter

    @pytest.mark.asyncio
    async def test_publish_container_success(self, mock_adapter):
        """Test successful container publishing."""
        publish_response = {"id": "published_post_123"}
        post_details = {
            "permalink": "https://instagram.com/p/ABC123/",
            "media_type": "IMAGE",
            "timestamp": "2024-01-15T10:00:00+0000"
        }

        with patch.object(mock_adapter, '_make_api_request', side_effect=[publish_response, post_details]) as mock_api, \
             patch.object(mock_adapter, '_get_post_details', return_value=post_details):

            result = await mock_adapter.publish_container("container_123", "test_correlation_id")

            assert isinstance(result, PublishResult)
            assert result.post_id == "published_post_123"
            assert result.status == PublishStatus.FINISHED
            assert result.message == "Post published successfully"
            assert result.container_id == "container_123"
            assert result.permalink == "https://instagram.com/p/ABC123/"
            assert result.published_at is not None

            # Verify API call
            assert mock_api.call_count >= 1
            publish_call = mock_api.call_args_list[0]
            assert publish_call[0][0] == "POST"
            assert "/media_publish" in publish_call[0][1]
            assert publish_call[1]["data"]["creation_id"] == "container_123"

    @pytest.mark.asyncio
    async def test_publish_container_api_error(self, mock_adapter):
        """Test container publishing with API error."""
        error = InstagramError("Publishing failed")
        error.error_code = "CONTENT_NOT_READY"
        error.retry_after = 300

        with patch.object(mock_adapter, '_make_api_request', side_effect=error):
            result = await mock_adapter.publish_container("container_123")

            assert isinstance(result, PublishResult)
            assert result.post_id is None
            assert result.status == PublishStatus.ERROR
            assert "Publishing failed" in result.message
            assert result.container_id == "container_123"
            assert result.error_code == "CONTENT_NOT_READY"
            assert result.retry_after == 300

    @pytest.mark.asyncio
    async def test_publish_container_auth_error(self, mock_adapter):
        """Test container publishing with authentication error."""
        with patch.object(mock_adapter, '_make_api_request', side_effect=InstagramAuthError("Invalid token")):
            result = await mock_adapter.publish_container("container_123")

            assert result.status == PublishStatus.ERROR
            assert result.error_code is None  # Auth errors don't have specific error codes
            assert "Invalid token" in result.message


class TestScheduledPosting:
    """Test scheduled posting functionality."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mocked adapter for testing."""
        with patch('app.adapters.instagram.settings.instagram') as mock_settings:
            mock_settings.page_id = "test_page_id"
            mock_settings.access_token.get_secret_value.return_value = "test_token"

            adapter = InstagramAdapter()
            return adapter

    @pytest.mark.asyncio
    async def test_schedule_immediate_publishing(self, mock_adapter):
        """Test immediate publishing when no schedule time is provided."""
        publish_result = PublishResult(
            post_id="immediate_post_123",
            status=PublishStatus.FINISHED,
            message="Published immediately",
            container_id="container_123"
        )

        with patch.object(mock_adapter, 'publish_container', return_value=publish_result):
            post = InstagramPost(
                caption="Immediate post",
                media_items=[MediaItem(file_path="test.jpg", media_type=ContainerType.IMAGE)]
            )

            result = await mock_adapter.schedule_if_needed(post, "container_123")

            assert result.post_id == "immediate_post_123"
            assert result.status == PublishStatus.FINISHED

    @pytest.mark.asyncio
    async def test_schedule_future_post_success(self, mock_adapter):
        """Test scheduling post for future."""
        future_time = datetime.now(timezone.utc) + timedelta(hours=2)

        with patch.object(mock_adapter, '_make_api_request', return_value={"config": "ok"}):
            post = InstagramPost(
                caption="Scheduled post",
                media_items=[MediaItem(file_path="test.jpg", media_type=ContainerType.IMAGE)],
                schedule_time=future_time
            )

            result = await mock_adapter.schedule_if_needed(post, "container_123")

            assert result.status == PublishStatus.PENDING
            assert f"scheduled for {future_time.isoformat()}" in result.message
            assert result.container_id == "container_123"

    @pytest.mark.asyncio
    async def test_schedule_past_time_immediate_publish(self, mock_adapter):
        """Test scheduling with past time results in immediate publishing."""
        past_time = datetime.now(timezone.utc) - timedelta(hours=1)

        publish_result = PublishResult(
            post_id="past_time_post_123",
            status=PublishStatus.FINISHED,
            message="Published immediately",
            container_id="container_123"
        )

        with patch.object(mock_adapter, 'publish_container', return_value=publish_result):
            post = InstagramPost(
                caption="Past time post",
                media_items=[MediaItem(file_path="test.jpg", media_type=ContainerType.IMAGE)],
                schedule_time=past_time
            )

            result = await mock_adapter.schedule_if_needed(post, "container_123")

            assert result.status == PublishStatus.FINISHED

    @pytest.mark.asyncio
    async def test_schedule_too_far_future_error(self, mock_adapter):
        """Test scheduling too far in the future raises error."""
        too_far_future = datetime.now(timezone.utc) + timedelta(days=100)  # Beyond 75 days

        post = InstagramPost(
            caption="Too far future post",
            media_items=[MediaItem(file_path="test.jpg", media_type=ContainerType.IMAGE)],
            schedule_time=too_far_future
        )

        with pytest.raises(InstagramValidationError) as exc_info:
            await mock_adapter.schedule_if_needed(post, "container_123")

        assert "too far in future" in str(exc_info.value)


class TestAPIRequestHandling:
    """Test API request handling, rate limiting, and error responses."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mocked adapter for testing."""
        with patch('app.adapters.instagram.settings.instagram') as mock_settings:
            mock_settings.page_id = "test_page_id"
            mock_settings.access_token.get_secret_value.return_value = "test_token"

            adapter = InstagramAdapter()
            return adapter

    @pytest.mark.asyncio
    async def test_make_api_request_success(self, mock_adapter):
        """Test successful API request."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.json.return_value = {"success": True, "id": "123"}

        with patch.object(mock_adapter.http_client, 'post', return_value=mock_response), \
             patch.object(mock_adapter, '_check_rate_limits', return_value=None), \
             patch.object(mock_adapter, '_update_rate_limits', return_value=None):

            result = await mock_adapter._make_api_request(
                "POST",
                "https://graph.facebook.com/v18.0/test",
                data={"test": "data"}
            )

            assert result == {"success": True, "id": "123"}

    @pytest.mark.asyncio
    async def test_make_api_request_rate_limit_error(self, mock_adapter):
        """Test API request with rate limit error."""
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {"Retry-After": "60"}

        with patch.object(mock_adapter.http_client, 'post', return_value=mock_response), \
             patch.object(mock_adapter, '_check_rate_limits', return_value=None):

            with pytest.raises(InstagramRateLimitError) as exc_info:
                await mock_adapter._make_api_request("POST", "https://test.com")

            assert "Rate limit exceeded" in str(exc_info.value)
            assert "60 seconds" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_make_api_request_auth_error(self, mock_adapter):
        """Test API request with authentication error."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized access"

        with patch.object(mock_adapter.http_client, 'post', return_value=mock_response), \
             patch.object(mock_adapter, '_check_rate_limits', return_value=None):

            with pytest.raises(InstagramAuthError) as exc_info:
                await mock_adapter._make_api_request("POST", "https://test.com")

            assert "Authentication failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_make_api_request_with_error_response(self, mock_adapter):
        """Test API request with error in response body."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.json.return_value = {
            "error": {
                "code": 190,
                "message": "Invalid access token",
                "error_subcode": None
            }
        }

        with patch.object(mock_adapter.http_client, 'post', return_value=mock_response), \
             patch.object(mock_adapter, '_check_rate_limits', return_value=None), \
             patch.object(mock_adapter, '_handle_api_error', side_effect=InstagramAuthError("Invalid token")):

            with pytest.raises(InstagramAuthError):
                await mock_adapter._make_api_request("POST", "https://test.com")

    @pytest.mark.asyncio
    async def test_make_api_request_network_error_retry(self, mock_adapter):
        """Test API request with network error and retry."""
        with patch.object(mock_adapter.http_client, 'post', side_effect=httpx.RequestError("Network error")), \
             patch.object(mock_adapter, '_check_rate_limits', return_value=None):

            with pytest.raises(httpx.RequestError):
                await mock_adapter._make_api_request("POST", "https://test.com")

    def test_rate_limit_checking(self, mock_adapter):
        """Test rate limit checking logic."""
        # Test normal rate limit
        mock_adapter.rate_limit_remaining = 100
        mock_adapter.rate_limit_reset_time = time.time() + 1800

        # Should not raise or wait
        asyncio.run(mock_adapter._check_rate_limits())

        assert mock_adapter.rate_limit_remaining == 100

    @pytest.mark.asyncio
    async def test_rate_limit_reset(self, mock_adapter):
        """Test rate limit reset logic."""
        # Set expired rate limit
        mock_adapter.rate_limit_remaining = 0
        mock_adapter.rate_limit_reset_time = time.time() - 100  # Past time

        await mock_adapter._check_rate_limits()

        # Should reset
        assert mock_adapter.rate_limit_remaining == 200
        assert mock_adapter.rate_limit_reset_time > time.time()

    @pytest.mark.asyncio
    async def test_rate_limit_near_exhaustion(self, mock_adapter):
        """Test behavior when rate limit is nearly exhausted."""
        mock_adapter.rate_limit_remaining = 3  # Below threshold of 5
        mock_adapter.rate_limit_reset_time = time.time() + 10  # 10 seconds in future

        with patch('asyncio.sleep') as mock_sleep:
            await mock_adapter._check_rate_limits()

            mock_sleep.assert_called_once()
            # Should reset after wait
            assert mock_adapter.rate_limit_remaining == 200

    def test_update_rate_limits_from_headers(self, mock_adapter):
        """Test rate limit update from response headers."""
        headers = {
            "X-Business-Use-Case-Usage": '{"123456": {"call_count": 50, "total_time": 300}}'
        }

        mock_adapter._update_rate_limits(headers)

        assert mock_adapter.rate_limit_remaining == 150  # 200 - 50

    def test_update_rate_limits_invalid_headers(self, mock_adapter):
        """Test rate limit update with invalid headers."""
        original_remaining = mock_adapter.rate_limit_remaining

        # Invalid JSON
        headers = {"X-Business-Use-Case-Usage": "invalid json"}
        mock_adapter._update_rate_limits(headers)

        # Should not change
        assert mock_adapter.rate_limit_remaining == original_remaining

        # Missing headers
        headers = {}
        mock_adapter._update_rate_limits(headers)

        # Should not change
        assert mock_adapter.rate_limit_remaining == original_remaining


class TestAPIErrorHandling:
    """Test Instagram API error handling."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mocked adapter for testing."""
        with patch('app.adapters.instagram.settings.instagram') as mock_settings:
            mock_settings.page_id = "test_page_id"
            mock_settings.access_token.get_secret_value.return_value = "test_token"

            adapter = InstagramAdapter()
            return adapter

    @pytest.mark.asyncio
    async def test_handle_invalid_access_token_error(self, mock_adapter):
        """Test handling of invalid access token error."""
        error_data = {
            "code": 190,
            "message": "Invalid OAuth access token",
            "error_subcode": None
        }

        with pytest.raises(InstagramAuthError) as exc_info:
            await mock_adapter._handle_api_error(error_data)

        assert "Invalid access token" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_handle_content_not_ready_error(self, mock_adapter):
        """Test handling of content not ready error."""
        error_data = {
            "code": 100,
            "message": "Content is not ready for publishing",
            "error_subcode": 2207006
        }

        with pytest.raises(InstagramValidationError) as exc_info:
            await mock_adapter._handle_api_error(error_data)

        assert "not ready for publishing" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_handle_posting_too_frequently_error(self, mock_adapter):
        """Test handling of posting too frequently error."""
        error_data = {
            "code": 100,
            "message": "Media posted too frequently. Please try again later.",
            "error_subcode": None
        }

        with pytest.raises(InstagramRateLimitError) as exc_info:
            await mock_adapter._handle_api_error(error_data)

        assert "posted too frequently" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_handle_temporarily_blocked_error(self, mock_adapter):
        """Test handling of temporarily blocked errors."""
        error_codes = [368, 9007]  # Different blocked error codes

        for error_code in error_codes:
            error_data = {
                "code": error_code,
                "message": "You are temporarily blocked from performing this action",
                "error_subcode": None
            }

            with pytest.raises(InstagramRateLimitError) as exc_info:
                await mock_adapter._handle_api_error(error_data)

            assert "Temporarily blocked" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_handle_generic_api_error(self, mock_adapter):
        """Test handling of generic API errors."""
        error_data = {
            "code": 999,
            "message": "Unknown error occurred",
            "error_subcode": None
        }

        with pytest.raises(InstagramError) as exc_info:
            await mock_adapter._handle_api_error(error_data)

        assert "API Error 999" in str(exc_info.value)
        assert "Unknown error occurred" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_handle_malformed_error_data(self, mock_adapter):
        """Test handling of malformed error data."""
        error_data = {}  # Missing required fields

        with pytest.raises(InstagramError) as exc_info:
            await mock_adapter._handle_api_error(error_data)

        assert "API Error None" in str(exc_info.value)
        assert "Unknown error" in str(exc_info.value)


class TestMediaHandling:
    """Test media upload and thumbnail handling."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mocked adapter for testing."""
        with patch('app.adapters.instagram.settings.instagram') as mock_settings:
            mock_settings.page_id = "test_page_id"
            mock_settings.access_token.get_secret_value.return_value = "test_token"

            adapter = InstagramAdapter()
            return adapter

    @pytest.mark.asyncio
    async def test_upload_media_file_url(self, mock_adapter):
        """Test media file upload with URL."""
        url = await mock_adapter._upload_media_file("https://example.com/image.jpg", ContainerType.IMAGE)

        assert url == "https://example.com/image.jpg"

    @pytest.mark.asyncio
    async def test_upload_media_file_local_path(self, mock_adapter):
        """Test media file upload with local path."""
        url = await mock_adapter._upload_media_file("/local/image.jpg", ContainerType.IMAGE)

        assert url.startswith("https://cdn.saleswhisper.ru/uploads/")
        assert url.endswith("image.jpg")

    @pytest.mark.asyncio
    async def test_upload_thumbnail_success(self, mock_adapter):
        """Test successful thumbnail upload."""
        with patch.object(mock_adapter, '_upload_media_file', return_value="https://cdn.example.com/thumb.jpg"):
            thumbnail_url = await mock_adapter.upload_thumbnail(
                "/path/to/video.mp4",
                "/path/to/thumbnail.jpg",
                "test_correlation_id"
            )

            assert thumbnail_url == "https://cdn.example.com/thumb.jpg"

    @pytest.mark.asyncio
    async def test_upload_thumbnail_failure(self, mock_adapter):
        """Test thumbnail upload failure."""
        with patch.object(mock_adapter, '_upload_media_file', side_effect=Exception("Upload failed")):
            with pytest.raises(Exception) as exc_info:
                await mock_adapter.upload_thumbnail(
                    "/path/to/video.mp4",
                    "/path/to/thumbnail.jpg"
                )

            assert "Upload failed" in str(exc_info.value)


class TestContainerStatus:
    """Test container status checking."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mocked adapter for testing."""
        with patch('app.adapters.instagram.settings.instagram') as mock_settings:
            mock_settings.page_id = "test_page_id"
            mock_settings.access_token.get_secret_value.return_value = "test_token"

            adapter = InstagramAdapter()
            return adapter

    @pytest.mark.asyncio
    async def test_get_container_status_finished(self, mock_adapter):
        """Test getting container status when finished."""
        mock_response = {
            "id": "container_123",
            "media_type": "IMAGE",
            "status_code": "FINISHED",
            "status": "ready"
        }

        with patch.object(mock_adapter, '_make_api_request', return_value=mock_response):
            status = await mock_adapter.get_container_status("container_123")

            assert status["container_id"] == "container_123"
            assert status["status"] == "ready"
            assert status["status_code"] == "FINISHED"
            assert status["media_type"] == "IMAGE"
            assert status["is_ready"] is True

    @pytest.mark.asyncio
    async def test_get_container_status_in_progress(self, mock_adapter):
        """Test getting container status when in progress."""
        mock_response = {
            "id": "container_123",
            "media_type": "VIDEO",
            "status_code": "IN_PROGRESS",
            "status": "processing"
        }

        with patch.object(mock_adapter, '_make_api_request', return_value=mock_response):
            status = await mock_adapter.get_container_status("container_123")

            assert status["status_code"] == "IN_PROGRESS"
            assert status["is_ready"] is False

    @pytest.mark.asyncio
    async def test_get_container_status_error(self, mock_adapter):
        """Test getting container status with error."""
        with patch.object(mock_adapter, '_make_api_request', side_effect=InstagramError("API Error")):
            status = await mock_adapter.get_container_status("container_123")

            assert status["container_id"] == "container_123"
            assert status["status"] == "error"
            assert "API Error" in status["error"]
            assert status["is_ready"] is False

    @pytest.mark.asyncio
    async def test_get_post_details_success(self, mock_adapter):
        """Test getting post details after publishing."""
        mock_response = {
            "id": "post_123",
            "media_type": "IMAGE",
            "permalink": "https://instagram.com/p/ABC123/",
            "timestamp": "2024-01-15T10:00:00+0000",
            "caption": "Test caption"
        }

        with patch.object(mock_adapter, '_make_api_request', return_value=mock_response):
            details = await mock_adapter._get_post_details("post_123")

            assert details["permalink"] == "https://instagram.com/p/ABC123/"
            assert details["media_type"] == "IMAGE"

    @pytest.mark.asyncio
    async def test_get_post_details_failure(self, mock_adapter):
        """Test getting post details with failure."""
        with patch.object(mock_adapter, '_make_api_request', side_effect=Exception("Network error")):
            details = await mock_adapter._get_post_details("post_123")

            assert details == {}  # Should return empty dict on failure


class TestUpdatePostStatus:
    """Test post status update functionality."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mocked adapter for testing."""
        with patch('app.adapters.instagram.settings.instagram') as mock_settings:
            mock_settings.page_id = "test_page_id"
            mock_settings.access_token.get_secret_value.return_value = "test_token"

            adapter = InstagramAdapter()
            return adapter

    @pytest.mark.asyncio
    async def test_update_post_status_success(self, mock_adapter):
        """Test successful post status update."""
        # This is currently a placeholder, but test that it doesn't raise
        await mock_adapter.update_post_status(
            "post_123",
            "published",
            {"post_id": "instagram_456", "permalink": "https://instagram.com/p/ABC123/"}
        )

        # Should complete without error
        assert True

    @pytest.mark.asyncio
    async def test_update_post_status_minimal_data(self, mock_adapter):
        """Test post status update with minimal data."""
        await mock_adapter.update_post_status("post_123", "failed")

        # Should complete without error
        assert True


class TestCleanupAndUtilities:
    """Test cleanup and utility functions."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mocked adapter for testing."""
        with patch('app.adapters.instagram.settings.instagram') as mock_settings:
            mock_settings.page_id = "test_page_id"
            mock_settings.access_token.get_secret_value.return_value = "test_token"

            adapter = InstagramAdapter()
            return adapter

    @pytest.mark.asyncio
    async def test_adapter_close(self, mock_adapter):
        """Test adapter cleanup."""
        with patch.object(mock_adapter.http_client, 'aclose', new_callable=AsyncMock) as mock_close:
            await mock_adapter.close()
            mock_close.assert_called_once()


class TestConvenienceFunctions:
    """Test convenience functions."""

    @pytest.mark.asyncio
    async def test_publish_instagram_post_single_image(self):
        """Test publish_instagram_post convenience function with single image."""
        mock_container_result = ContainerResult(
            container_id="container_123",
            status="created",
            created_at=datetime.now(timezone.utc)
        )

        mock_publish_result = PublishResult(
            post_id="post_123",
            status=PublishStatus.FINISHED,
            message="Published successfully",
            container_id="container_123"
        )

        with patch('app.adapters.instagram.instagram_adapter') as mock_adapter:
            mock_adapter.create_container.return_value = mock_container_result
            mock_adapter.schedule_if_needed.return_value = mock_publish_result

            result = await publish_instagram_post(
                caption="Test post",
                media_files=["/path/to/image.jpg"],
                correlation_id="test_correlation"
            )

            assert result.post_id == "post_123"
            assert result.status == PublishStatus.FINISHED

    @pytest.mark.asyncio
    async def test_publish_instagram_post_multiple_images(self):
        """Test publish_instagram_post convenience function with multiple images."""
        mock_container_result = ContainerResult(
            container_id="carousel_123",
            status="created",
            created_at=datetime.now(timezone.utc)
        )

        mock_publish_result = PublishResult(
            post_id="post_456",
            status=PublishStatus.FINISHED,
            message="Carousel published",
            container_id="carousel_123"
        )

        with patch('app.adapters.instagram.instagram_adapter') as mock_adapter:
            mock_adapter.create_container.return_value = mock_container_result
            mock_adapter.schedule_if_needed.return_value = mock_publish_result

            result = await publish_instagram_post(
                caption="Carousel post",
                media_files=["/path/to/image1.jpg", "/path/to/image2.jpg", "/path/to/video.mp4"]
            )

            assert result.post_id == "post_456"

    @pytest.mark.asyncio
    async def test_publish_instagram_post_unsupported_format(self):
        """Test publish_instagram_post with unsupported file format."""
        with pytest.raises(InstagramValidationError) as exc_info:
            await publish_instagram_post(
                caption="Test post",
                media_files=["/path/to/document.pdf"]  # Unsupported format
            )

        assert "Unsupported file type" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_publish_instagram_post_with_schedule(self):
        """Test publish_instagram_post with scheduled time."""
        schedule_time = datetime.now(timezone.utc) + timedelta(hours=2)

        mock_container_result = ContainerResult(
            container_id="scheduled_123",
            status="created",
            created_at=datetime.now(timezone.utc)
        )

        mock_publish_result = PublishResult(
            post_id=None,
            status=PublishStatus.PENDING,
            message=f"Scheduled for {schedule_time.isoformat()}",
            container_id="scheduled_123"
        )

        with patch('app.adapters.instagram.instagram_adapter') as mock_adapter:
            mock_adapter.create_container.return_value = mock_container_result
            mock_adapter.schedule_if_needed.return_value = mock_publish_result

            result = await publish_instagram_post(
                caption="Scheduled post",
                media_files=["/path/to/image.jpg"],
                schedule_time=schedule_time
            )

            assert result.status == PublishStatus.PENDING

    @pytest.mark.asyncio
    async def test_get_instagram_container_status(self):
        """Test get_instagram_container_status convenience function."""
        expected_status = {
            "container_id": "container_123",
            "status": "finished",
            "is_ready": True
        }

        with patch('app.adapters.instagram.instagram_adapter') as mock_adapter:
            mock_adapter.get_container_status.return_value = expected_status

            result = await get_instagram_container_status("container_123")

            assert result == expected_status

    @pytest.mark.asyncio
    async def test_cleanup_instagram_adapter(self):
        """Test cleanup_instagram_adapter convenience function."""
        with patch('app.adapters.instagram.instagram_adapter') as mock_adapter:
            mock_adapter.close = AsyncMock()

            await cleanup_instagram_adapter()

            mock_adapter.close.assert_called_once()


class TestDataclassValidation:
    """Test dataclass validation and behavior."""

    def test_media_item_creation(self):
        """Test MediaItem creation and defaults."""
        media_item = MediaItem(
            file_path="/test/image.jpg",
            media_type=ContainerType.IMAGE
        )

        assert media_item.file_path == "/test/image.jpg"
        assert media_item.media_type == ContainerType.IMAGE
        assert media_item.caption is None
        assert media_item.thumbnail_url is None
        assert media_item.aspect_ratio is None
        assert media_item.duration is None

    def test_media_item_with_all_fields(self):
        """Test MediaItem with all fields populated."""
        media_item = MediaItem(
            file_path="/test/video.mp4",
            media_type=ContainerType.VIDEO,
            caption="Video caption",
            thumbnail_url="https://example.com/thumb.jpg",
            aspect_ratio="16:9",
            duration=30.5
        )

        assert media_item.caption == "Video caption"
        assert media_item.thumbnail_url == "https://example.com/thumb.jpg"
        assert media_item.aspect_ratio == "16:9"
        assert media_item.duration == 30.5

    def test_instagram_post_creation(self):
        """Test InstagramPost creation and defaults."""
        media_items = [
            MediaItem(file_path="/test/image.jpg", media_type=ContainerType.IMAGE)
        ]

        post = InstagramPost(
            caption="Test caption",
            media_items=media_items
        )

        assert post.caption == "Test caption"
        assert len(post.media_items) == 1
        assert post.schedule_time is None
        assert post.location_id is None
        assert post.user_tags == []  # Should be initialized by __post_init__

    def test_instagram_post_with_optional_fields(self):
        """Test InstagramPost with optional fields."""
        schedule_time = datetime.now(timezone.utc) + timedelta(hours=1)
        media_items = [MediaItem(file_path="/test/image.jpg", media_type=ContainerType.IMAGE)]
        user_tags = [{"username": "testuser", "x": 0.5, "y": 0.5}]

        post = InstagramPost(
            caption="Test caption",
            media_items=media_items,
            schedule_time=schedule_time,
            location_id="123456789",
            user_tags=user_tags
        )

        assert post.schedule_time == schedule_time
        assert post.location_id == "123456789"
        assert post.user_tags == user_tags

    def test_container_result_creation(self):
        """Test ContainerResult creation."""
        created_at = datetime.now(timezone.utc)

        result = ContainerResult(
            container_id="container_123",
            status="created",
            created_at=created_at
        )

        assert result.container_id == "container_123"
        assert result.status == "created"
        assert result.created_at == created_at
        assert result.error_message is None

    def test_publish_result_success(self):
        """Test PublishResult for successful publishing."""
        published_at = datetime.now(timezone.utc)

        result = PublishResult(
            post_id="post_123",
            status=PublishStatus.FINISHED,
            message="Success",
            container_id="container_123",
            published_at=published_at,
            permalink="https://instagram.com/p/ABC123/"
        )

        assert result.post_id == "post_123"
        assert result.status == PublishStatus.FINISHED
        assert result.message == "Success"
        assert result.published_at == published_at
        assert result.permalink == "https://instagram.com/p/ABC123/"
        assert result.error_code is None
        assert result.retry_after is None

    def test_publish_result_error(self):
        """Test PublishResult for failed publishing."""
        result = PublishResult(
            post_id=None,
            status=PublishStatus.ERROR,
            message="Publishing failed",
            error_code="CONTENT_NOT_READY",
            retry_after=300
        )

        assert result.post_id is None
        assert result.status == PublishStatus.ERROR
        assert result.error_code == "CONTENT_NOT_READY"
        assert result.retry_after == 300
        assert result.published_at is None
        assert result.permalink is None


if __name__ == "__main__":
    # Run specific tests
    pytest.main([__file__, "-v"])
