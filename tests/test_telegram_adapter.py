"""
Unit tests for Telegram Bot API adapter.

Tests for:
- Text message sending (sendMessage)
- Single media sending (sendPhoto, sendVideo, etc.)
- Media group sending (sendMediaGroup)
- Rate limiting and retry logic
- Telegram Bot API error handling and response parsing
- All major functions with mocked Telegram API responses
"""

import json
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.adapters.telegram import (
    MediaType,
    ParseMode,
    SendStatus,
    TelegramAdapter,
    TelegramAuthError,
    TelegramError,
    TelegramFileError,
    TelegramMediaItem,
    TelegramMessage,
    TelegramRateLimitError,
    TelegramSendResult,
    TelegramValidationError,
    send_telegram_media_group,
    send_telegram_message,
    send_telegram_photo,
    send_telegram_video,
)


class TestTelegramAdapterInitialization:
    """Test Telegram adapter initialization and configuration."""

    def test_adapter_initialization_success(self):
        """Test successful adapter initialization."""
        with patch('app.adapters.telegram.settings.telegram') as mock_settings:
            mock_settings.bot_token.get_secret_value.return_value = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"

            adapter = TelegramAdapter()

            assert adapter.bot_token == "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
            assert adapter.api_base == "https://api.telegram.org/bot123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
            assert adapter.rate_limit_per_second == 30
            assert adapter.rate_limit_per_minute == 20
            assert isinstance(adapter.http_client, httpx.AsyncClient)

    def test_adapter_initialization_missing_token(self):
        """Test adapter initialization with missing bot token."""
        with patch('app.adapters.telegram.settings.telegram') as mock_settings:
            # Remove bot_token attribute
            delattr(mock_settings, 'bot_token')

            with pytest.raises(TelegramAuthError) as exc_info:
                TelegramAdapter()

            assert "bot token not configured" in str(exc_info.value)

    def test_get_bot_token_variations(self):
        """Test bot token retrieval with different configurations."""
        adapter = TelegramAdapter.__new__(TelegramAdapter)  # Create without __init__

        # Test with SecretStr-like object
        with patch('app.adapters.telegram.settings.telegram') as mock_settings:
            mock_token = MagicMock()
            mock_token.get_secret_value.return_value = "secret_bot_token"
            mock_settings.bot_token = mock_token

            token = adapter._get_bot_token()
            assert token == "secret_bot_token"

        # Test with string token
        with patch('app.adapters.telegram.settings.telegram') as mock_settings:
            mock_settings.bot_token = "plain_string_token"

            token = adapter._get_bot_token()
            assert token == "plain_string_token"


class TestTextMessageSending:
    """Test Telegram text message sending."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mocked adapter for testing."""
        with patch('app.adapters.telegram.settings.telegram') as mock_settings:
            mock_settings.bot_token.get_secret_value.return_value = "123456:test_token"

            adapter = TelegramAdapter()
            return adapter

    @pytest.mark.asyncio
    async def test_send_text_message_success(self, mock_adapter):
        """Test successful text message sending."""
        # Mock API response
        api_response = {
            "ok": True,
            "result": {
                "message_id": 123,
                "from": {
                    "id": 123456,
                    "is_bot": True,
                    "first_name": "TestBot"
                },
                "chat": {
                    "id": -1001234567890,
                    "title": "Test Chat",
                    "type": "supergroup"
                },
                "date": 1640995200,
                "text": "Hello, World!"
            }
        }

        with patch.object(mock_adapter, '_make_api_request', return_value=api_response) as mock_api:
            message = TelegramMessage(
                chat_id=-1001234567890,
                text="Hello, World!",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                disable_notification=True
            )

            result = await mock_adapter._send_text_message(message, "test_correlation_id")

            assert isinstance(result, TelegramSendResult)
            assert result.message_id == 123
            assert result.status == SendStatus.SENT
            assert result.message == "Message sent successfully"
            assert result.chat_id == -1001234567890
            assert result.sent_at is not None

            # Verify API call
            mock_api.assert_called_once_with(
                "sendMessage",
                params={
                    "chat_id": -1001234567890,
                    "text": "Hello, World!",
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                    "disable_notification": True,
                    "protect_content": False
                }
            )

    @pytest.mark.asyncio
    async def test_send_text_message_with_reply(self, mock_adapter):
        """Test text message sending with reply."""
        api_response = {
            "ok": True,
            "result": {
                "message_id": 124,
                "chat": {"id": -1001234567890},
                "date": 1640995200,
                "text": "Reply message",
                "reply_to_message": {"message_id": 100}
            }
        }

        with patch.object(mock_adapter, '_make_api_request', return_value=api_response) as mock_api:
            message = TelegramMessage(
                chat_id=-1001234567890,
                text="Reply message",
                reply_to_message_id=100
            )

            result = await mock_adapter._send_text_message(message)

            assert result.message_id == 124

            # Verify reply parameter
            call_params = mock_api.call_args[1]["params"]
            assert call_params["reply_to_message_id"] == 100

    @pytest.mark.asyncio
    async def test_send_text_message_to_channel_with_url(self, mock_adapter):
        """Test text message to channel with message URL generation."""
        api_response = {
            "ok": True,
            "result": {
                "message_id": 456,
                "chat": {"id": -1001234567890, "username": "testchannel"},
                "date": 1640995200,
                "text": "Channel message"
            }
        }

        with patch.object(mock_adapter, '_make_api_request', return_value=api_response):
            message = TelegramMessage(
                chat_id="@testchannel",
                text="Channel message"
            )

            result = await mock_adapter._send_text_message(message)

            assert result.message_url == "https://t.me/testchannel/456"


class TestSingleMediaSending:
    """Test single media item sending."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mocked adapter for testing."""
        with patch('app.adapters.telegram.settings.telegram') as mock_settings:
            mock_settings.bot_token.get_secret_value.return_value = "123456:test_token"

            adapter = TelegramAdapter()
            return adapter

    @pytest.mark.asyncio
    async def test_send_photo_with_url(self, mock_adapter):
        """Test sending photo from URL."""
        api_response = {
            "ok": True,
            "result": {
                "message_id": 789,
                "chat": {"id": -1001234567890},
                "date": 1640995200,
                "photo": [
                    {"file_id": "photo_id", "width": 1280, "height": 720}
                ],
                "caption": "Test photo"
            }
        }

        with patch.object(mock_adapter, '_make_api_request', return_value=api_response) as mock_api:
            media_item = TelegramMediaItem(
                file_path="https://example.com/photo.jpg",
                media_type=MediaType.PHOTO,
                caption="Test photo",
                parse_mode=ParseMode.MARKDOWN
            )

            message = TelegramMessage(
                chat_id=-1001234567890,
                media_items=[media_item]
            )

            result = await mock_adapter._send_single_media(message, "test_correlation_id")

            assert result.message_id == 789
            assert result.status == SendStatus.SENT
            assert result.message == "Media sent successfully"

            # Verify API call
            mock_api.assert_called_once_with(
                "sendPhoto",
                params={
                    "chat_id": -1001234567890,
                    "photo": "https://example.com/photo.jpg",
                    "caption": "Test photo",
                    "parse_mode": "Markdown",
                    "disable_notification": False,
                    "protect_content": False
                },
                files=None
            )

    @pytest.mark.asyncio
    async def test_send_video_with_local_file(self, mock_adapter):
        """Test sending video from local file."""
        api_response = {
            "ok": True,
            "result": {
                "message_id": 790,
                "chat": {"id": -1001234567890},
                "date": 1640995200,
                "video": {
                    "file_id": "video_id",
                    "width": 1920,
                    "height": 1080,
                    "duration": 60
                }
            }
        }

        mock_file_content = b"fake_video_data"

        with patch.object(mock_adapter, '_make_api_request', return_value=api_response) as mock_api, \
             patch.object(mock_adapter, '_read_file', return_value=mock_file_content) as mock_read:

            media_item = TelegramMediaItem(
                file_path="/local/video.mp4",
                media_type=MediaType.VIDEO,
                width=1920,
                height=1080,
                duration=60,
                supports_streaming=True
            )

            message = TelegramMessage(
                chat_id=-1001234567890,
                media_items=[media_item],
                text="Video caption"
            )

            result = await mock_adapter._send_single_media(message)

            assert result.message_id == 790

            # Verify file reading
            mock_read.assert_called_once_with("/local/video.mp4")

            # Verify API call with files
            mock_api.assert_called_once()
            call_args = mock_api.call_args
            assert call_args[0][0] == "sendVideo"
            assert call_args[1]["params"]["width"] == 1920
            assert call_args[1]["params"]["height"] == 1080
            assert call_args[1]["params"]["duration"] == 60
            assert call_args[1]["params"]["supports_streaming"] is True
            assert call_args[1]["params"]["caption"] == "Video caption"
            assert "video" in call_args[1]["files"]

    @pytest.mark.asyncio
    async def test_send_video_with_thumbnail(self, mock_adapter):
        """Test sending video with thumbnail."""
        api_response = {
            "ok": True,
            "result": {
                "message_id": 791,
                "chat": {"id": -1001234567890},
                "video": {"file_id": "video_with_thumb"}
            }
        }

        mock_video_content = b"fake_video_data"
        mock_thumb_content = b"fake_thumb_data"

        with patch.object(mock_adapter, '_make_api_request', return_value=api_response) as mock_api, \
             patch.object(mock_adapter, '_read_file', side_effect=[mock_video_content, mock_thumb_content]):

            media_item = TelegramMediaItem(
                file_path="/local/video.mp4",
                media_type=MediaType.VIDEO,
                thumbnail="/local/thumb.jpg"
            )

            message = TelegramMessage(
                chat_id=-1001234567890,
                media_items=[media_item]
            )

            result = await mock_adapter._send_single_media(message)

            assert result.message_id == 791

            # Verify both video and thumbnail in files
            call_args = mock_api.call_args
            assert "video" in call_args[1]["files"]
            assert "thumb" in call_args[1]["files"]

    @pytest.mark.asyncio
    async def test_send_unsupported_media_type(self, mock_adapter):
        """Test sending unsupported media type."""
        media_item = TelegramMediaItem(
            file_path="/test/file.unknown",
            media_type="unsupported"  # Invalid enum value
        )

        message = TelegramMessage(
            chat_id=-1001234567890,
            media_items=[media_item]
        )

        # This should be caught during MediaType enum validation
        # But if it somehow gets through, it should raise validation error
        with pytest.raises((ValueError, TelegramValidationError)):
            await mock_adapter._send_single_media(message)


class TestMediaGroupSending:
    """Test media group sending."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mocked adapter for testing."""
        with patch('app.adapters.telegram.settings.telegram') as mock_settings:
            mock_settings.bot_token.get_secret_value.return_value = "123456:test_token"

            adapter = TelegramAdapter()
            return adapter

    @pytest.mark.asyncio
    async def test_send_media_group_with_urls(self, mock_adapter):
        """Test sending media group with URLs."""
        api_response = {
            "ok": True,
            "result": [
                {
                    "message_id": 800,
                    "chat": {"id": -1001234567890},
                    "photo": [{"file_id": "photo1"}],
                    "caption": "Group caption"
                },
                {
                    "message_id": 801,
                    "chat": {"id": -1001234567890},
                    "photo": [{"file_id": "photo2"}]
                }
            ]
        }

        with patch.object(mock_adapter, '_make_api_request', return_value=api_response) as mock_api:
            media_items = [
                TelegramMediaItem(
                    file_path="https://example.com/photo1.jpg",
                    media_type=MediaType.PHOTO,
                    caption="Group caption"
                ),
                TelegramMediaItem(
                    file_path="https://example.com/photo2.jpg",
                    media_type=MediaType.PHOTO
                )
            ]

            message = TelegramMessage(
                chat_id=-1001234567890,
                media_items=media_items
            )

            result = await mock_adapter._send_media_group(message, "test_correlation_id")

            assert result.message_id == 800  # First message ID
            assert result.status == SendStatus.SENT
            assert "2 items" in result.message

            # Verify API call
            mock_api.assert_called_once_with(
                "sendMediaGroup",
                params={
                    "chat_id": -1001234567890,
                    "media": json.dumps([
                        {
                            "type": "photo",
                            "media": "https://example.com/photo1.jpg",
                            "caption": "Group caption"
                        },
                        {
                            "type": "photo",
                            "media": "https://example.com/photo2.jpg"
                        }
                    ]),
                    "disable_notification": False,
                    "protect_content": False
                },
                files=None
            )

    @pytest.mark.asyncio
    async def test_send_media_group_with_local_files(self, mock_adapter):
        """Test sending media group with local files."""
        api_response = {
            "ok": True,
            "result": [
                {"message_id": 802, "chat": {"id": -1001234567890}},
                {"message_id": 803, "chat": {"id": -1001234567890}}
            ]
        }

        mock_file_contents = [b"fake_photo1_data", b"fake_video_data"]

        with patch.object(mock_adapter, '_make_api_request', return_value=api_response) as mock_api, \
             patch.object(mock_adapter, '_read_file', side_effect=mock_file_contents):

            media_items = [
                TelegramMediaItem(
                    file_path="/local/photo.jpg",
                    media_type=MediaType.PHOTO
                ),
                TelegramMediaItem(
                    file_path="/local/video.mp4",
                    media_type=MediaType.VIDEO,
                    width=1280,
                    height=720,
                    duration=30
                )
            ]

            message = TelegramMessage(
                chat_id=-1001234567890,
                media_items=media_items,
                text="Media group caption"
            )

            result = await mock_adapter._send_media_group(message)

            assert result.message_id == 802

            # Verify API call with files
            call_args = mock_api.call_args
            assert "files" in call_args[1]
            files = call_args[1]["files"]
            assert "file_0" in files  # Photo file
            assert "file_1" in files  # Video file

            # Verify media array
            media_json = call_args[1]["params"]["media"]
            media_array = json.loads(media_json)
            assert len(media_array) == 2
            assert media_array[0]["media"] == "attach://file_0"
            assert media_array[1]["media"] == "attach://file_1"
            assert media_array[0]["caption"] == "Media group caption"  # Caption on first item
            assert media_array[1]["width"] == 1280
            assert media_array[1]["height"] == 720
            assert media_array[1]["duration"] == 30

    @pytest.mark.asyncio
    async def test_send_media_group_with_video_thumbnail(self, mock_adapter):
        """Test sending media group with video thumbnail."""
        api_response = {
            "ok": True,
            "result": [{"message_id": 804, "chat": {"id": -1001234567890}}]
        }

        mock_contents = [b"video_data", b"thumb_data"]

        with patch.object(mock_adapter, '_make_api_request', return_value=api_response) as mock_api, \
             patch.object(mock_adapter, '_read_file', side_effect=mock_contents):

            media_items = [
                TelegramMediaItem(
                    file_path="/local/video.mp4",
                    media_type=MediaType.VIDEO,
                    thumbnail="/local/thumb.jpg"
                )
            ]

            message = TelegramMessage(
                chat_id=-1001234567890,
                media_items=media_items
            )

            await mock_adapter._send_media_group(message)

            # Verify thumbnail file upload
            call_args = mock_api.call_args
            files = call_args[1]["files"]
            assert "file_0" in files  # Video file
            assert "thumb_1" in files  # Thumbnail file

            # Verify media array references thumbnail
            media_json = call_args[1]["params"]["media"]
            media_array = json.loads(media_json)
            assert media_array[0]["thumb"] == "attach://thumb_1"


class TestFileHandling:
    """Test file reading and handling."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mocked adapter for testing."""
        with patch('app.adapters.telegram.settings.telegram') as mock_settings:
            mock_settings.bot_token.get_secret_value.return_value = "123456:test_token"

            adapter = TelegramAdapter()
            return adapter

    @pytest.mark.asyncio
    async def test_read_local_file_success(self, mock_adapter):
        """Test reading local file successfully."""
        mock_content = b"file_content"

        with patch('pathlib.Path.exists', return_value=True), \
             patch('pathlib.Path.read_bytes', return_value=mock_content):

            content = await mock_adapter._read_file("/local/file.jpg")
            assert content == mock_content

    @pytest.mark.asyncio
    async def test_read_local_file_not_found(self, mock_adapter):
        """Test reading non-existent local file."""
        with patch('pathlib.Path.exists', return_value=False):
            with pytest.raises(TelegramFileError) as exc_info:
                await mock_adapter._read_file("/nonexistent/file.jpg")

            assert "File not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_read_file_from_url(self, mock_adapter):
        """Test reading file from URL."""
        mock_content = b"url_file_content"

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.content = mock_content

        with patch.object(mock_adapter.http_client, 'get', return_value=mock_response):
            content = await mock_adapter._read_file("https://example.com/file.jpg")
            assert content == mock_content

    @pytest.mark.asyncio
    async def test_read_file_from_url_error(self, mock_adapter):
        """Test reading file from URL with error."""
        with patch.object(mock_adapter.http_client, 'get', side_effect=httpx.RequestError("Network error")):
            with pytest.raises(httpx.RequestError):
                await mock_adapter._read_file("https://example.com/file.jpg")


class TestRateLimiting:
    """Test Telegram Bot API rate limiting."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mocked adapter for testing."""
        with patch('app.adapters.telegram.settings.telegram') as mock_settings:
            mock_settings.bot_token.get_secret_value.return_value = "123456:test_token"

            adapter = TelegramAdapter()
            return adapter

    @pytest.mark.asyncio
    async def test_global_rate_limit_normal_operation(self, mock_adapter):
        """Test normal operation within global rate limits."""
        # Should allow up to 30 requests without waiting
        for _i in range(30):
            await mock_adapter._check_rate_limits(-1001234567890)

        assert len(mock_adapter.last_request_times) == 30

    @pytest.mark.asyncio
    async def test_global_rate_limit_exceeded(self, mock_adapter):
        """Test global rate limit enforcement."""
        # Fill up the global rate limit
        current_time = time.time()
        mock_adapter.last_request_times = [current_time] * 30

        with patch('asyncio.sleep') as mock_sleep:
            await mock_adapter._check_rate_limits(-1001234567890)

            # Should wait before making request
            mock_sleep.assert_called_once()

    @pytest.mark.asyncio
    async def test_per_chat_rate_limit_exceeded(self, mock_adapter):
        """Test per-chat rate limit enforcement."""
        chat_id = -1001234567890
        current_time = time.time()

        # Fill up the per-chat rate limit
        mock_adapter.chat_request_times[str(chat_id)] = [current_time] * 20

        with patch('asyncio.sleep') as mock_sleep:
            await mock_adapter._check_rate_limits(chat_id)

            # Should wait before making request
            mock_sleep.assert_called_once()

    @pytest.mark.asyncio
    async def test_rate_limit_cleanup_old_requests(self, mock_adapter):
        """Test that old requests are removed from rate limit tracking."""
        # Add old request times
        old_time = time.time() - 120.0  # 2 minutes ago
        mock_adapter.last_request_times = [old_time] * 30
        mock_adapter.chat_request_times["123"] = [old_time] * 20

        await mock_adapter._check_rate_limits(123)

        # Old requests should be removed
        assert len(mock_adapter.last_request_times) == 1  # New request added
        assert len(mock_adapter.chat_request_times["123"]) == 1


class TestAPIErrorHandling:
    """Test Telegram Bot API error handling."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mocked adapter for testing."""
        with patch('app.adapters.telegram.settings.telegram') as mock_settings:
            mock_settings.bot_token.get_secret_value.return_value = "123456:test_token"

            adapter = TelegramAdapter()
            return adapter

    @pytest.mark.asyncio
    async def test_handle_bad_request_error(self, mock_adapter):
        """Test handling of bad request errors."""
        error_response = {
            "ok": False,
            "error_code": 400,
            "description": "Bad Request: chat not found"
        }

        with pytest.raises(TelegramValidationError) as exc_info:
            await mock_adapter._handle_api_error(error_response)

        assert "Bad Request" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_handle_unauthorized_error(self, mock_adapter):
        """Test handling of unauthorized errors."""
        error_response = {
            "ok": False,
            "error_code": 401,
            "description": "Unauthorized"
        }

        with pytest.raises(TelegramAuthError) as exc_info:
            await mock_adapter._handle_api_error(error_response)

        assert "Unauthorized" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_handle_forbidden_error(self, mock_adapter):
        """Test handling of forbidden errors."""
        error_response = {
            "ok": False,
            "error_code": 403,
            "description": "Forbidden: bot was blocked by the user"
        }

        with pytest.raises(TelegramAuthError) as exc_info:
            await mock_adapter._handle_api_error(error_response)

        assert "Forbidden" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_handle_not_found_error(self, mock_adapter):
        """Test handling of not found errors."""
        error_response = {
            "ok": False,
            "error_code": 404,
            "description": "Not Found"
        }

        with pytest.raises(TelegramError) as exc_info:
            await mock_adapter._handle_api_error(error_response)

        assert "Not Found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_handle_file_too_large_error(self, mock_adapter):
        """Test handling of file too large errors."""
        error_response = {
            "ok": False,
            "error_code": 413,
            "description": "Request Entity Too Large: file too large"
        }

        with pytest.raises(TelegramFileError) as exc_info:
            await mock_adapter._handle_api_error(error_response)

        assert "File too large" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_handle_rate_limit_error(self, mock_adapter):
        """Test handling of rate limit errors."""
        error_response = {
            "ok": False,
            "error_code": 429,
            "description": "Too Many Requests: retry after 60",
            "parameters": {
                "retry_after": 60
            }
        }

        with pytest.raises(TelegramRateLimitError) as exc_info:
            await mock_adapter._handle_api_error(error_response)

        assert exc_info.value.retry_after == 60
        assert "Rate limit exceeded" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_handle_server_error(self, mock_adapter):
        """Test handling of server errors."""
        error_response = {
            "ok": False,
            "error_code": 500,
            "description": "Internal Server Error"
        }

        with pytest.raises(TelegramError) as exc_info:
            await mock_adapter._handle_api_error(error_response)

        assert "Internal Server Error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_handle_unknown_error(self, mock_adapter):
        """Test handling of unknown errors."""
        error_response = {
            "ok": False,
            "error_code": 999,
            "description": "Unknown error"
        }

        with pytest.raises(TelegramError) as exc_info:
            await mock_adapter._handle_api_error(error_response)

        assert "Telegram API Error 999" in str(exc_info.value)


class TestAPIRequests:
    """Test Telegram Bot API request handling."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mocked adapter for testing."""
        with patch('app.adapters.telegram.settings.telegram') as mock_settings:
            mock_settings.bot_token.get_secret_value.return_value = "123456:test_token"

            adapter = TelegramAdapter()
            return adapter

    @pytest.mark.asyncio
    async def test_make_api_request_json_success(self, mock_adapter):
        """Test successful JSON API request."""
        mock_response_data = {
            "ok": True,
            "result": {"message_id": 123}
        }

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = mock_response_data

        with patch.object(mock_adapter.http_client, 'post', return_value=mock_response), \
             patch.object(mock_adapter, '_check_rate_limits', return_value=None):

            result = await mock_adapter._make_api_request(
                "sendMessage",
                params={"chat_id": 123, "text": "test"}
            )

            assert result == mock_response_data

    @pytest.mark.asyncio
    async def test_make_api_request_with_files(self, mock_adapter):
        """Test API request with file upload."""
        mock_response_data = {
            "ok": True,
            "result": {"message_id": 124}
        }

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = mock_response_data

        with patch.object(mock_adapter.http_client, 'post', return_value=mock_response), \
             patch.object(mock_adapter, '_check_rate_limits', return_value=None):

            result = await mock_adapter._make_api_request(
                "sendPhoto",
                params={"chat_id": 123},
                files={"photo": ("test.jpg", b"data", "image/jpeg")}
            )

            assert result == mock_response_data

    @pytest.mark.asyncio
    async def test_make_api_request_with_error_response(self, mock_adapter):
        """Test API request with error in response."""
        error_response = {
            "ok": False,
            "error_code": 400,
            "description": "Bad Request"
        }

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = error_response

        with patch.object(mock_adapter.http_client, 'post', return_value=mock_response), \
             patch.object(mock_adapter, '_check_rate_limits', return_value=None), \
             patch.object(mock_adapter, '_handle_api_error', side_effect=TelegramValidationError("Bad Request")):

            with pytest.raises(TelegramValidationError):
                await mock_adapter._make_api_request("sendMessage")

    @pytest.mark.asyncio
    async def test_make_api_request_network_error(self, mock_adapter):
        """Test API request with network error."""
        with patch.object(mock_adapter.http_client, 'post', side_effect=httpx.RequestError("Network error")), \
             patch.object(mock_adapter, '_check_rate_limits', return_value=None):

            with pytest.raises(httpx.RequestError):
                await mock_adapter._make_api_request("sendMessage")


class TestUtilityMethods:
    """Test utility methods."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mocked adapter for testing."""
        with patch('app.adapters.telegram.settings.telegram') as mock_settings:
            mock_settings.bot_token.get_secret_value.return_value = "123456:test_token"

            adapter = TelegramAdapter()
            return adapter

    def test_generate_message_url_for_channel(self, mock_adapter):
        """Test message URL generation for channel."""
        url = mock_adapter._generate_message_url("@testchannel", 123)
        assert url == "https://t.me/testchannel/123"

    def test_generate_message_url_for_private_chat(self, mock_adapter):
        """Test message URL generation for private chat."""
        url = mock_adapter._generate_message_url(-1001234567890, 123)
        assert url is None  # Can't generate URLs for private chats

        url = mock_adapter._generate_message_url(123456, 123)
        assert url is None  # Can't generate URLs for private chats

    @pytest.mark.asyncio
    async def test_get_me_success(self, mock_adapter):
        """Test getting bot information."""
        bot_info = {
            "ok": True,
            "result": {
                "id": 123456,
                "is_bot": True,
                "first_name": "TestBot",
                "username": "testbot"
            }
        }

        with patch.object(mock_adapter, '_make_api_request', return_value=bot_info):
            result = await mock_adapter.get_me()

            assert result["id"] == 123456
            assert result["username"] == "testbot"

    @pytest.mark.asyncio
    async def test_get_me_error(self, mock_adapter):
        """Test getting bot information with error."""
        with patch.object(mock_adapter, '_make_api_request', side_effect=TelegramError("API Error")):
            result = await mock_adapter.get_me()

            assert result == {}

    @pytest.mark.asyncio
    async def test_get_chat_success(self, mock_adapter):
        """Test getting chat information."""
        chat_info = {
            "ok": True,
            "result": {
                "id": -1001234567890,
                "title": "Test Chat",
                "type": "supergroup"
            }
        }

        with patch.object(mock_adapter, '_make_api_request', return_value=chat_info):
            result = await mock_adapter.get_chat(-1001234567890)

            assert result["title"] == "Test Chat"
            assert result["type"] == "supergroup"

    @pytest.mark.asyncio
    async def test_delete_message_success(self, mock_adapter):
        """Test successful message deletion."""
        delete_response = {
            "ok": True,
            "result": True
        }

        with patch.object(mock_adapter, '_make_api_request', return_value=delete_response):
            result = await mock_adapter.delete_message(-1001234567890, 123)

            assert result is True

    @pytest.mark.asyncio
    async def test_delete_message_error(self, mock_adapter):
        """Test message deletion with error."""
        with patch.object(mock_adapter, '_make_api_request', side_effect=TelegramError("Can't delete")):
            result = await mock_adapter.delete_message(-1001234567890, 123)

            assert result is False

    @pytest.mark.asyncio
    async def test_edit_message_text_success(self, mock_adapter):
        """Test successful message text editing."""
        edit_response = {
            "ok": True,
            "result": {
                "message_id": 123,
                "text": "Edited message"
            }
        }

        with patch.object(mock_adapter, '_make_api_request', return_value=edit_response):
            result = await mock_adapter.edit_message_text(
                -1001234567890, 123, "Edited message", ParseMode.HTML
            )

            assert result is True


class TestFullSendWorkflow:
    """Test complete send workflow."""

    @pytest.fixture
    def mock_adapter(self):
        """Create mocked adapter for testing."""
        with patch('app.adapters.telegram.settings.telegram') as mock_settings:
            mock_settings.bot_token.get_secret_value.return_value = "123456:test_token"

            adapter = TelegramAdapter()
            return adapter

    @pytest.mark.asyncio
    async def test_send_message_text_only(self, mock_adapter):
        """Test sending text-only message."""
        text_result = TelegramSendResult(
            message_id=123,
            status=SendStatus.SENT,
            message="Message sent successfully",
            chat_id=-1001234567890,
            sent_at=datetime.now(timezone.utc)
        )

        with patch.object(mock_adapter, '_send_text_message', return_value=text_result) as mock_text:
            message = TelegramMessage(
                chat_id=-1001234567890,
                text="Hello, World!"
            )

            result = await mock_adapter.send_message(message, "test_correlation_id")

            assert result.message_id == 123
            assert result.status == SendStatus.SENT

            mock_text.assert_called_once_with(message, "test_correlation_id")

    @pytest.mark.asyncio
    async def test_send_message_single_media(self, mock_adapter):
        """Test sending single media message."""
        media_result = TelegramSendResult(
            message_id=456,
            status=SendStatus.SENT,
            message="Media sent successfully",
            chat_id=-1001234567890
        )

        with patch.object(mock_adapter, '_send_single_media', return_value=media_result) as mock_media:
            media_item = TelegramMediaItem(
                file_path="/test/photo.jpg",
                media_type=MediaType.PHOTO
            )

            message = TelegramMessage(
                chat_id=-1001234567890,
                media_items=[media_item]
            )

            result = await mock_adapter.send_message(message)

            assert result.message_id == 456

            mock_media.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_message_media_group(self, mock_adapter):
        """Test sending media group message."""
        group_result = TelegramSendResult(
            message_id=789,
            status=SendStatus.SENT,
            message="Media group with 2 items sent successfully",
            chat_id=-1001234567890
        )

        with patch.object(mock_adapter, '_send_media_group', return_value=group_result) as mock_group:
            media_items = [
                TelegramMediaItem(file_path="/test/photo1.jpg", media_type=MediaType.PHOTO),
                TelegramMediaItem(file_path="/test/photo2.jpg", media_type=MediaType.PHOTO)
            ]

            message = TelegramMessage(
                chat_id=-1001234567890,
                media_items=media_items
            )

            result = await mock_adapter.send_message(message)

            assert result.message_id == 789
            assert "2 items" in result.message

            mock_group.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_message_empty_content(self, mock_adapter):
        """Test sending message with no content."""
        message = TelegramMessage(
            chat_id=-1001234567890
            # No text or media
        )

        result = await mock_adapter.send_message(message)

        assert result.status == SendStatus.ERROR
        assert "must contain text or media" in result.message


class TestConvenienceFunctions:
    """Test convenience functions."""

    @pytest.mark.asyncio
    async def test_send_telegram_message_text_only(self):
        """Test send_telegram_message convenience function for text."""
        mock_result = TelegramSendResult(
            message_id=123,
            status=SendStatus.SENT,
            message="Sent successfully",
            chat_id=-1001234567890
        )

        with patch('app.adapters.telegram.telegram_adapter') as mock_adapter:
            mock_adapter.send_message.return_value = mock_result

            result = await send_telegram_message(
                chat_id=-1001234567890,
                text="Hello, World!",
                parse_mode=ParseMode.HTML,
                correlation_id="test_correlation"
            )

            assert result.message_id == 123

            # Verify adapter call
            mock_adapter.send_message.assert_called_once()
            call_args = mock_adapter.send_message.call_args
            message_arg = call_args[0][0]
            assert message_arg.chat_id == -1001234567890
            assert message_arg.text == "Hello, World!"
            assert message_arg.parse_mode == ParseMode.HTML

    @pytest.mark.asyncio
    async def test_send_telegram_message_with_media(self):
        """Test send_telegram_message convenience function with media."""
        mock_result = TelegramSendResult(
            message_id=456,
            status=SendStatus.SENT,
            message="Media sent",
            chat_id=-1001234567890
        )

        with patch('app.adapters.telegram.telegram_adapter') as mock_adapter:
            mock_adapter.send_message.return_value = mock_result

            result = await send_telegram_message(
                chat_id=-1001234567890,
                text="Media message",
                media_files=["/path/to/photo.jpg", "/path/to/video.mp4", "/path/to/audio.mp3"]
            )

            assert result.message_id == 456

            # Verify media types detection
            call_args = mock_adapter.send_message.call_args
            message_arg = call_args[0][0]
            assert len(message_arg.media_items) == 3
            assert message_arg.media_items[0].media_type == MediaType.PHOTO
            assert message_arg.media_items[1].media_type == MediaType.VIDEO
            assert message_arg.media_items[2].media_type == MediaType.AUDIO

    @pytest.mark.asyncio
    async def test_send_telegram_photo(self):
        """Test send_telegram_photo convenience function."""
        mock_result = TelegramSendResult(
            message_id=789,
            status=SendStatus.SENT,
            message="Photo sent",
            chat_id=-1001234567890
        )

        with patch('app.adapters.telegram.telegram_adapter') as mock_adapter:
            mock_adapter.send_message.return_value = mock_result

            result = await send_telegram_photo(
                chat_id=-1001234567890,
                photo_path="https://example.com/photo.jpg",
                caption="Test photo",
                parse_mode=ParseMode.MARKDOWN
            )

            assert result.message_id == 789

            # Verify photo parameters
            call_args = mock_adapter.send_message.call_args
            message_arg = call_args[0][0]
            assert len(message_arg.media_items) == 1
            media_item = message_arg.media_items[0]
            assert media_item.media_type == MediaType.PHOTO
            assert media_item.file_path == "https://example.com/photo.jpg"
            assert media_item.caption == "Test photo"
            assert media_item.parse_mode == ParseMode.MARKDOWN

    @pytest.mark.asyncio
    async def test_send_telegram_video(self):
        """Test send_telegram_video convenience function."""
        mock_result = TelegramSendResult(
            message_id=999,
            status=SendStatus.SENT,
            message="Video sent",
            chat_id=-1001234567890
        )

        with patch('app.adapters.telegram.telegram_adapter') as mock_adapter:
            mock_adapter.send_message.return_value = mock_result

            result = await send_telegram_video(
                chat_id=-1001234567890,
                video_path="/local/video.mp4",
                caption="Test video",
                thumbnail="/local/thumb.jpg",
                width=1920,
                height=1080,
                duration=60
            )

            assert result.message_id == 999

            # Verify video parameters
            call_args = mock_adapter.send_message.call_args
            message_arg = call_args[0][0]
            media_item = message_arg.media_items[0]
            assert media_item.media_type == MediaType.VIDEO
            assert media_item.thumbnail == "/local/thumb.jpg"
            assert media_item.width == 1920
            assert media_item.height == 1080
            assert media_item.duration == 60

    @pytest.mark.asyncio
    async def test_send_telegram_media_group(self):
        """Test send_telegram_media_group convenience function."""
        mock_result = TelegramSendResult(
            message_id=111,
            status=SendStatus.SENT,
            message="Media group sent",
            chat_id=-1001234567890
        )

        with patch('app.adapters.telegram.telegram_adapter') as mock_adapter:
            mock_adapter.send_message.return_value = mock_result

            result = await send_telegram_media_group(
                chat_id=-1001234567890,
                media_files=["/path/photo1.jpg", "/path/photo2.jpg", "/path/video.mp4"],
                caption="Media group caption",
                parse_mode=ParseMode.HTML
            )

            assert result.message_id == 111

            # Verify media group parameters
            call_args = mock_adapter.send_message.call_args
            message_arg = call_args[0][0]
            assert len(message_arg.media_items) == 3
            assert message_arg.media_items[0].caption == "Media group caption"  # Caption on first item
            assert message_arg.media_items[0].parse_mode == ParseMode.HTML


class TestDataclassValidation:
    """Test dataclass validation and behavior."""

    def test_telegram_media_item_creation(self):
        """Test TelegramMediaItem creation and defaults."""
        media_item = TelegramMediaItem(
            file_path="/test/photo.jpg",
            media_type=MediaType.PHOTO
        )

        assert media_item.file_path == "/test/photo.jpg"
        assert media_item.media_type == MediaType.PHOTO
        assert media_item.caption is None
        assert media_item.parse_mode is None
        assert media_item.thumbnail is None
        assert media_item.width is None
        assert media_item.height is None
        assert media_item.duration is None
        assert media_item.supports_streaming is None

    def test_telegram_message_creation(self):
        """Test TelegramMessage creation and defaults."""
        message = TelegramMessage(
            chat_id=-1001234567890,
            text="Test message"
        )

        assert message.chat_id == -1001234567890
        assert message.text == "Test message"
        assert message.media_items == []  # Should be initialized by __post_init__
        assert message.parse_mode is None
        assert message.disable_web_page_preview is False
        assert message.disable_notification is False
        assert message.protect_content is False
        assert message.reply_to_message_id is None


if __name__ == "__main__":
    # Run specific tests
    pytest.main([__file__, "-v"])
