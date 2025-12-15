"""
Unit tests for TikTok adapter.
"""

import asyncio
import json
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch, MagicMock
from typing import Dict, Any

import httpx
from tenacity import RetryError

from app.adapters.tiktok import (
    TikTokAdapter,
    TikTokVideoItem,
    TikTokPost,
    TikTokPublishResult,
    TikTokUploadResult,
    WebhookEvent,
    PostStatus,
    WebhookEventType,
    TikTokError,
    TikTokAuthError,
    TikTokRateLimitError,
    TikTokValidationError,
    TikTokUploadError,
    publish_tiktok_video,
    validate_tiktok_webhook,
    handle_tiktok_webhook
)


@pytest.fixture
def mock_settings():
    """Mock settings for TikTok adapter."""
    settings_mock = Mock()
    settings_mock.tiktok.client_key.get_secret_value.return_value = "test_client_key"
    settings_mock.tiktok.client_secret.get_secret_value.return_value = "test_client_secret"
    settings_mock.tiktok.access_token.get_secret_value.return_value = "test_access_token"
    settings_mock.tiktok.webhook_secret.get_secret_value.return_value = "test_webhook_secret"
    return settings_mock


@pytest.fixture
def video_item():
    """Test TikTok video item."""
    return TikTokVideoItem(
        file_path="/test/video.mp4",
        title="Test Video",
        description="Test video description",
        tags=["test", "video"],
        privacy_level="PUBLIC_TO_EVERYONE"
    )


@pytest.fixture
def tiktok_post(video_item):
    """Test TikTok post."""
    return TikTokPost(
        video_item=video_item,
        is_app_approved=True
    )


@pytest.fixture
def tiktok_adapter(mock_settings):
    """TikTok adapter instance with mocked settings."""
    with patch('app.adapters.tiktok.settings', mock_settings):
        adapter = TikTokAdapter()
        # Mock HTTP client
        adapter.http_client = AsyncMock(spec=httpx.AsyncClient)
        return adapter


class TestTikTokAdapter:
    """Test TikTok adapter functionality."""
    
    def test_init_with_valid_settings(self, mock_settings):
        """Test TikTok adapter initialization with valid settings."""
        with patch('app.adapters.tiktok.settings', mock_settings):
            adapter = TikTokAdapter()
            
            assert adapter.client_key == "test_client_key"
            assert adapter.client_secret == "test_client_secret"
            assert adapter.access_token == "test_access_token"
            assert adapter.webhook_secret == "test_webhook_secret"
            assert adapter.api_base == "https://open.tiktokapis.com/v2"
            assert adapter.daily_limit == 1000
            assert adapter.minute_limit == 20
    
    def test_init_missing_credentials(self):
        """Test TikTok adapter initialization with missing credentials."""
        mock_settings = Mock()
        del mock_settings.tiktok  # Remove tiktok settings
        
        with patch('app.adapters.tiktok.settings', mock_settings):
            with pytest.raises(TikTokAuthError, match="client key not configured"):
                TikTokAdapter()
    
    @pytest.mark.asyncio
    async def test_publish_post_approved_app_success(self, tiktok_adapter, tiktok_post):
        """Test successful post publishing for approved app."""
        # Mock successful upload
        upload_response = {
            "data": {
                "upload_id": "test_upload_123",
                "upload_url": "https://upload.tiktok.com/test"
            }
        }
        
        # Mock successful publish
        publish_response = {
            "data": {
                "share_id": "test_share_123"
            }
        }
        
        # Mock file operations
        mock_path = Mock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.stat.return_value.st_size = 1024000
        mock_path.name = "test_video.mp4"
        mock_path.read_bytes.return_value = b"fake_video_content"
        
        with patch('app.adapters.tiktok.Path', return_value=mock_path), \
             patch.object(tiktok_adapter, '_make_api_request') as mock_api_request:
            
            # Setup API request responses
            mock_api_request.side_effect = [upload_response, publish_response]
            
            # Mock chunked upload
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_response.raise_for_status = Mock()
            tiktok_adapter.http_client.put.return_value.__aenter__.return_value = mock_response
            
            result = await tiktok_adapter.publish_post(tiktok_post, "test_correlation_id")
            
            assert result.share_id == "test_share_123"
            assert result.status == PostStatus.PUBLISHED
            assert result.message == "Post published successfully"
            assert result.post_url == "https://www.tiktok.com/@test_share_123"
            assert result.published_at is not None
    
    @pytest.mark.asyncio
    async def test_publish_post_non_approved_app(self, tiktok_adapter, tiktok_post):
        """Test post publishing for non-approved app (draft creation)."""
        tiktok_post.is_app_approved = False
        
        upload_response = {
            "data": {
                "upload_id": "test_upload_123",
                "upload_url": "https://upload.tiktok.com/test"
            }
        }
        
        draft_response = {
            "data": {
                "draft_id": "test_draft_123"
            }
        }
        
        mock_path = Mock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.stat.return_value.st_size = 1024000
        mock_path.name = "test_video.mp4"
        mock_path.read_bytes.return_value = b"fake_video_content"
        
        with patch('app.adapters.tiktok.Path', return_value=mock_path), \
             patch.object(tiktok_adapter, '_make_api_request') as mock_api_request:
            
            mock_api_request.side_effect = [upload_response, draft_response]
            
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_response.raise_for_status = Mock()
            tiktok_adapter.http_client.put.return_value.__aenter__.return_value = mock_response
            
            result = await tiktok_adapter.publish_post(tiktok_post, "test_correlation_id")
            
            assert result.share_id == "test_draft_123"
            assert result.status == PostStatus.DRAFT
            assert "Manual approval required" in result.message
            assert result.published_at is None
    
    @pytest.mark.asyncio
    async def test_publish_post_upload_failure(self, tiktok_adapter, tiktok_post):
        """Test post publishing with upload failure."""
        mock_path = Mock(spec=Path)
        mock_path.exists.return_value = False
        
        with patch('app.adapters.tiktok.Path', return_value=mock_path):
            result = await tiktok_adapter.publish_post(tiktok_post, "test_correlation_id")
            
            assert result.share_id is None
            assert result.status == PostStatus.ERROR
            assert "not found" in result.message
    
    @pytest.mark.asyncio
    async def test_upload_video_success(self, tiktok_adapter, video_item):
        """Test successful video upload."""
        upload_response = {
            "data": {
                "upload_id": "test_upload_123",
                "upload_url": "https://upload.tiktok.com/test"
            }
        }
        
        mock_path = Mock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.stat.return_value.st_size = 1024000
        mock_path.name = "test_video.mp4"
        mock_path.read_bytes.return_value = b"fake_video_content"
        
        with patch('app.adapters.tiktok.Path', return_value=mock_path), \
             patch.object(tiktok_adapter, '_make_api_request', return_value=upload_response):
            
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_response.raise_for_status = Mock()
            tiktok_adapter.http_client.put.return_value.__aenter__.return_value = mock_response
            
            result = await tiktok_adapter._upload_video(video_item, "test_correlation_id")
            
            assert result.upload_id == "test_upload_123"
            assert result.status == PostStatus.IN_PROGRESS
            assert result.file_size == 1024000
            assert result.upload_time > 0
    
    @pytest.mark.asyncio
    async def test_upload_video_file_not_found(self, tiktok_adapter, video_item):
        """Test video upload with missing file."""
        mock_path = Mock(spec=Path)
        mock_path.exists.return_value = False
        
        with patch('app.adapters.tiktok.Path', return_value=mock_path):
            result = await tiktok_adapter._upload_video(video_item, "test_correlation_id")
            
            assert result.upload_id is None
            assert result.status == PostStatus.ERROR
            assert "not found" in result.error_message
    
    @pytest.mark.asyncio
    async def test_rate_limiting_daily_limit(self, tiktok_adapter):
        """Test daily rate limit enforcement."""
        tiktok_adapter.daily_requests = 1000
        
        with pytest.raises(TikTokRateLimitError, match="Daily rate limit exceeded"):
            await tiktok_adapter._check_rate_limits()
    
    @pytest.mark.asyncio
    async def test_rate_limiting_minute_limit(self, tiktok_adapter):
        """Test minute rate limit enforcement."""
        import time
        current_time = time.time()
        tiktok_adapter.minute_requests = [current_time - 30] * 20
        
        with patch('time.time', return_value=current_time), \
             patch('asyncio.sleep') as mock_sleep:
            
            await tiktok_adapter._check_rate_limits()
            mock_sleep.assert_called_once()
            assert mock_sleep.call_args[0][0] > 0  # Should wait some time
    
    @pytest.mark.asyncio
    async def test_api_request_success(self, tiktok_adapter):
        """Test successful API request."""
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"result": "success"}}
        mock_response.raise_for_status = Mock()
        
        tiktok_adapter.http_client.request.return_value = mock_response
        
        result = await tiktok_adapter._make_api_request(
            "POST",
            "https://api.tiktok.com/test",
            json_data={"test": "data"}
        )
        
        assert result["data"]["result"] == "success"
        tiktok_adapter.http_client.request.assert_called_once_with(
            method="POST",
            url="https://api.tiktok.com/test",
            headers={
                "Authorization": "Bearer test_access_token",
                "Content-Type": "application/json; charset=UTF-8"
            },
            params=None,
            json={"test": "data"}
        )
    
    @pytest.mark.asyncio
    async def test_api_request_error_handling(self, tiktok_adapter):
        """Test API request error handling."""
        mock_response = AsyncMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {
            "error": {
                "code": "invalid_request",
                "message": "Bad request"
            }
        }
        mock_response.raise_for_status = Mock()
        
        tiktok_adapter.http_client.request.return_value = mock_response
        
        with pytest.raises(TikTokValidationError, match="Bad request"):
            await tiktok_adapter._make_api_request("POST", "https://api.tiktok.com/test")
    
    @pytest.mark.asyncio
    async def test_api_request_rate_limit_error(self, tiktok_adapter):
        """Test API request rate limit error."""
        mock_response = AsyncMock()
        mock_response.json.return_value = {
            "error": {
                "code": "rate_limit_exceeded",
                "message": "Too many requests"
            }
        }
        mock_response.raise_for_status = Mock()
        
        tiktok_adapter.http_client.request.return_value = mock_response
        
        with pytest.raises(TikTokRateLimitError, match="Too many requests"):
            await tiktok_adapter._make_api_request("POST", "https://api.tiktok.com/test")
    
    @pytest.mark.asyncio
    async def test_api_request_auth_error(self, tiktok_adapter):
        """Test API request authentication error."""
        mock_response = AsyncMock()
        mock_response.json.return_value = {
            "error": {
                "code": "invalid_token",
                "message": "Invalid access token"
            }
        }
        mock_response.raise_for_status = Mock()
        
        tiktok_adapter.http_client.request.return_value = mock_response
        
        with pytest.raises(TikTokAuthError, match="Invalid access token"):
            await tiktok_adapter._make_api_request("POST", "https://api.tiktok.com/test")
    
    def test_webhook_signature_validation_success(self, tiktok_adapter):
        """Test successful webhook signature validation."""
        payload = '{"event_type":"video.publish","data":{"share_id":"123"}}'
        timestamp = "1234567890"
        
        # Create expected signature
        import hmac
        import hashlib
        message = f"{timestamp}|{payload}"
        expected_signature = hmac.new(
            "test_webhook_secret".encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        result = tiktok_adapter.validate_webhook_signature(payload, expected_signature, timestamp)
        assert result is True
    
    def test_webhook_signature_validation_failure(self, tiktok_adapter):
        """Test failed webhook signature validation."""
        payload = '{"event_type":"video.publish","data":{"share_id":"123"}}'
        timestamp = "1234567890"
        invalid_signature = "invalid_signature"
        
        result = tiktok_adapter.validate_webhook_signature(payload, invalid_signature, timestamp)
        assert result is False
    
    @pytest.mark.asyncio
    async def test_handle_webhook_event_publish_success(self, tiktok_adapter):
        """Test handling successful publish webhook event."""
        payload = {
            "event_type": "video.publish",
            "timestamp": 1234567890,
            "data": {
                "share_id": "test_share_123",
                "status": "SUCCESS"
            }
        }
        
        with patch.object(tiktok_adapter, '_update_post_from_webhook') as mock_update:
            event = await tiktok_adapter.handle_webhook_event(payload, "test_correlation_id")
            
            assert event.event_type == WebhookEventType.VIDEO_PUBLISH
            assert event.share_id == "test_share_123"
            assert event.status == "SUCCESS"
            assert event.timestamp == 1234567890
            assert event.error_code is None
            
            mock_update.assert_called_once_with(
                "test_share_123", "SUCCESS", None, None, "test_correlation_id"
            )
    
    @pytest.mark.asyncio
    async def test_handle_webhook_event_failure(self, tiktok_adapter):
        """Test handling failed webhook event."""
        payload = {
            "event_type": "video.publish",
            "timestamp": 1234567890,
            "data": {
                "share_id": "test_share_123",
                "status": "FAILED",
                "error_code": 4001,
                "error_message": "Content violates community guidelines"
            }
        }
        
        with patch.object(tiktok_adapter, '_update_post_from_webhook') as mock_update:
            event = await tiktok_adapter.handle_webhook_event(payload, "test_correlation_id")
            
            assert event.event_type == WebhookEventType.VIDEO_PUBLISH
            assert event.share_id == "test_share_123"
            assert event.status == "FAILED"
            assert event.error_code == 4001
            assert event.error_message == "Content violates community guidelines"
            
            mock_update.assert_called_once_with(
                "test_share_123", "FAILED", 4001, "Content violates community guidelines", "test_correlation_id"
            )
    
    @pytest.mark.asyncio
    async def test_handle_webhook_event_missing_event_type(self, tiktok_adapter):
        """Test handling webhook event with missing event type."""
        payload = {
            "timestamp": 1234567890,
            "data": {
                "share_id": "test_share_123",
                "status": "SUCCESS"
            }
        }
        
        with pytest.raises(TikTokValidationError, match="Missing event_type"):
            await tiktok_adapter.handle_webhook_event(payload, "test_correlation_id")
    
    @pytest.mark.asyncio
    async def test_get_video_info_success(self, tiktok_adapter):
        """Test successful video info retrieval."""
        video_list_response = {
            "data": {
                "videos": [
                    {
                        "id": "test_share_123",
                        "title": "Test Video",
                        "video_description": "Test description",
                        "duration": 30,
                        "cover_image_url": "https://example.com/cover.jpg",
                        "share_url": "https://tiktok.com/@user/video/123",
                        "view_count": 1000,
                        "like_count": 50,
                        "comment_count": 10,
                        "share_count": 5
                    }
                ]
            }
        }
        
        with patch.object(tiktok_adapter, '_make_api_request', return_value=video_list_response):
            result = await tiktok_adapter.get_video_info("test_share_123")
            
            assert result["id"] == "test_share_123"
            assert result["title"] == "Test Video"
            assert result["description"] == "Test description"
            assert result["view_count"] == 1000
            assert result["like_count"] == 50
    
    @pytest.mark.asyncio
    async def test_get_video_info_not_found(self, tiktok_adapter):
        """Test video info retrieval for non-existent video."""
        video_list_response = {
            "data": {
                "videos": []
            }
        }
        
        with patch.object(tiktok_adapter, '_make_api_request', return_value=video_list_response):
            result = await tiktok_adapter.get_video_info("nonexistent_share_id")
            
            assert result == {}
    
    @pytest.mark.asyncio
    async def test_get_video_info_api_error(self, tiktok_adapter):
        """Test video info retrieval with API error."""
        with patch.object(tiktok_adapter, '_make_api_request', side_effect=TikTokError("API Error")):
            result = await tiktok_adapter.get_video_info("test_share_123")
            
            assert result == {}
    
    @pytest.mark.asyncio
    async def test_close(self, tiktok_adapter):
        """Test adapter cleanup."""
        await tiktok_adapter.close()
        tiktok_adapter.http_client.aclose.assert_called_once()


class TestTikTokConvenienceFunctions:
    """Test TikTok convenience functions."""
    
    @pytest.mark.asyncio
    async def test_publish_tiktok_video(self):
        """Test publish TikTok video convenience function."""
        with patch('app.adapters.tiktok.tiktok_adapter') as mock_adapter:
            mock_result = TikTokPublishResult(
                share_id="test_share_123",
                status=PostStatus.PUBLISHED,
                message="Success",
                published_at=datetime.now(timezone.utc)
            )
            mock_adapter.publish_post.return_value = mock_result
            
            result = await publish_tiktok_video(
                video_path="/test/video.mp4",
                title="Test Video",
                description="Test description",
                tags=["test", "video"],
                is_app_approved=True,
                correlation_id="test_correlation"
            )
            
            assert result.share_id == "test_share_123"
            assert result.status == PostStatus.PUBLISHED
            
            # Verify the post was created correctly
            call_args = mock_adapter.publish_post.call_args
            post = call_args[0][0]
            assert post.video_item.file_path == "/test/video.mp4"
            assert post.video_item.title == "Test Video"
            assert post.video_item.description == "Test description"
            assert post.video_item.tags == ["test", "video"]
            assert post.is_app_approved is True
    
    @pytest.mark.asyncio
    async def test_validate_tiktok_webhook(self):
        """Test validate TikTok webhook convenience function."""
        with patch('app.adapters.tiktok.tiktok_adapter') as mock_adapter:
            mock_adapter.validate_webhook_signature.return_value = True
            
            result = await validate_tiktok_webhook(
                payload='{"test": "data"}',
                signature="test_signature",
                timestamp="1234567890"
            )
            
            assert result is True
            mock_adapter.validate_webhook_signature.assert_called_once_with(
                '{"test": "data"}', "test_signature", "1234567890"
            )
    
    @pytest.mark.asyncio
    async def test_handle_tiktok_webhook(self):
        """Test handle TikTok webhook convenience function."""
        payload = {"event_type": "video.publish", "data": {"share_id": "123"}}
        mock_event = WebhookEvent(
            event_type=WebhookEventType.VIDEO_PUBLISH,
            share_id="123",
            status="SUCCESS",
            timestamp=1234567890
        )
        
        with patch('app.adapters.tiktok.tiktok_adapter') as mock_adapter:
            mock_adapter.handle_webhook_event.return_value = mock_event
            
            result = await handle_tiktok_webhook(payload, "test_correlation")
            
            assert result.event_type == WebhookEventType.VIDEO_PUBLISH
            assert result.share_id == "123"
            mock_adapter.handle_webhook_event.assert_called_once_with(payload, "test_correlation")


class TestTikTokDataClasses:
    """Test TikTok data classes."""
    
    def test_tiktok_video_item_creation(self):
        """Test TikTokVideoItem creation."""
        video_item = TikTokVideoItem(
            file_path="/test/video.mp4",
            title="Test Video",
            description="Test description",
            tags=["test", "video"]
        )
        
        assert video_item.file_path == "/test/video.mp4"
        assert video_item.title == "Test Video"
        assert video_item.description == "Test description"
        assert video_item.tags == ["test", "video"]
        assert video_item.privacy_level == "PUBLIC_TO_EVERYONE"
        assert video_item.disable_duet is False
    
    def test_tiktok_video_item_default_tags(self):
        """Test TikTokVideoItem with default tags."""
        video_item = TikTokVideoItem(
            file_path="/test/video.mp4",
            title="Test Video"
        )
        
        assert video_item.tags == []
    
    def test_tiktok_post_creation(self):
        """Test TikTokPost creation."""
        video_item = TikTokVideoItem(
            file_path="/test/video.mp4",
            title="Test Video"
        )
        
        post = TikTokPost(
            video_item=video_item,
            is_app_approved=True,
            schedule_time=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        )
        
        assert post.video_item == video_item
        assert post.is_app_approved is True
        assert post.schedule_time.year == 2024
        assert post.auto_add_music is True
    
    def test_tiktok_upload_result_creation(self):
        """Test TikTokUploadResult creation."""
        result = TikTokUploadResult(
            upload_id="test_upload_123",
            status=PostStatus.IN_PROGRESS,
            upload_time=1.5,
            file_size=1024000
        )
        
        assert result.upload_id == "test_upload_123"
        assert result.status == PostStatus.IN_PROGRESS
        assert result.upload_time == 1.5
        assert result.file_size == 1024000
        assert result.error_message is None
    
    def test_tiktok_publish_result_creation(self):
        """Test TikTokPublishResult creation."""
        published_at = datetime.now(timezone.utc)
        
        result = TikTokPublishResult(
            share_id="test_share_123",
            status=PostStatus.PUBLISHED,
            message="Success",
            post_url="https://tiktok.com/@user/video/123",
            published_at=published_at
        )
        
        assert result.share_id == "test_share_123"
        assert result.status == PostStatus.PUBLISHED
        assert result.message == "Success"
        assert result.post_url == "https://tiktok.com/@user/video/123"
        assert result.published_at == published_at
    
    def test_webhook_event_creation(self):
        """Test WebhookEvent creation."""
        event = WebhookEvent(
            event_type=WebhookEventType.VIDEO_PUBLISH,
            share_id="test_share_123",
            status="SUCCESS",
            timestamp=1234567890,
            error_code=None,
            error_message=None
        )
        
        assert event.event_type == WebhookEventType.VIDEO_PUBLISH
        assert event.share_id == "test_share_123"
        assert event.status == "SUCCESS"
        assert event.timestamp == 1234567890
        assert event.error_code is None
        assert event.error_message is None


class TestTikTokErrorHandling:
    """Test TikTok error handling scenarios."""
    
    @pytest.mark.asyncio
    async def test_upload_retry_on_network_error(self, tiktok_adapter, video_item):
        """Test upload retry on network errors."""
        mock_path = Mock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.stat.return_value.st_size = 1024000
        
        with patch('app.adapters.tiktok.Path', return_value=mock_path), \
             patch.object(tiktok_adapter, '_make_api_request', side_effect=httpx.RequestError("Network error")):
            
            # Should retry and eventually fail
            with pytest.raises(RetryError):
                await tiktok_adapter._upload_video(video_item, "test_correlation_id")
    
    @pytest.mark.asyncio
    async def test_rate_limit_retry(self, tiktok_adapter):
        """Test rate limit retry behavior."""
        with patch.object(tiktok_adapter, '_check_rate_limits', side_effect=TikTokRateLimitError("Rate limit")), \
             patch('asyncio.sleep'):
            
            with pytest.raises(RetryError):
                await tiktok_adapter._make_api_request("GET", "https://api.tiktok.com/test")
    
    def test_error_classification(self, tiktok_adapter):
        """Test proper error classification."""
        test_cases = [
            ({"code": "invalid_token", "message": "Invalid token"}, TikTokAuthError),
            ({"code": "rate_limit_exceeded", "message": "Rate limit"}, TikTokRateLimitError),
            ({"code": "invalid_request", "message": "Bad request"}, TikTokValidationError),
            ({"code": "upload_failed", "message": "Upload failed"}, TikTokUploadError),
            ({"code": "content_rejected", "message": "Content rejected"}, TikTokValidationError),
            ({"code": "unknown_error", "message": "Unknown"}, TikTokError),
        ]
        
        for error_data, expected_exception in test_cases:
            with pytest.raises(expected_exception):
                asyncio.run(tiktok_adapter._handle_api_error(error_data))


if __name__ == "__main__":
    pytest.main([__file__])