"""
Telegram Bot API adapter for SalesWhisper Crosspost.

This module handles outbound Telegram publishing through the Telegram Bot API,
including sending photos, videos, media groups, and text messages.
"""

import asyncio
import json
import mimetypes
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..core.config import settings
from ..core.logging import get_logger, with_logging_context
from ..observability.metrics import metrics

logger = get_logger("adapters.telegram")


class TelegramError(Exception):
    """Base exception for Telegram Bot API errors."""

    pass


class TelegramRateLimitError(TelegramError):
    """Raised when Telegram Bot API rate limit is exceeded."""

    pass


class TelegramAuthError(TelegramError):
    """Raised when Telegram Bot API authentication fails."""

    pass


class TelegramValidationError(TelegramError):
    """Raised when Telegram Bot API validation fails."""

    pass


class TelegramFileError(TelegramError):
    """Raised when Telegram file operations fail."""

    pass


class MediaType(Enum):
    """Telegram media types."""

    PHOTO = "photo"
    VIDEO = "video"
    ANIMATION = "animation"
    DOCUMENT = "document"
    AUDIO = "audio"


class ParseMode(Enum):
    """Telegram message parse modes."""

    MARKDOWN = "Markdown"
    MARKDOWNV2 = "MarkdownV2"
    HTML = "HTML"


class SendStatus(Enum):
    """Telegram send status."""

    PENDING = "pending"
    SENT = "sent"
    ERROR = "error"


@dataclass
class TelegramMediaItem:
    """Represents a media item for Telegram."""

    file_path: str
    media_type: MediaType
    caption: str | None = None
    parse_mode: ParseMode | None = None
    thumbnail: str | None = None
    width: int | None = None
    height: int | None = None
    duration: int | None = None
    supports_streaming: bool | None = None


@dataclass
class TelegramMessage:
    """Represents a Telegram message."""

    chat_id: int | str
    text: str | None = None
    media_items: list[TelegramMediaItem] = None
    parse_mode: ParseMode | None = None
    disable_web_page_preview: bool = False
    disable_notification: bool = False
    protect_content: bool = False
    reply_to_message_id: int | None = None

    def __post_init__(self):
        if self.media_items is None:
            self.media_items = []


@dataclass
class TelegramSendResult:
    """Result of Telegram message sending."""

    message_id: int | None
    status: SendStatus
    message: str
    chat_id: int | str
    sent_at: datetime | None = None
    message_url: str | None = None
    error_code: int | None = None
    retry_after: int | None = None


class TelegramAdapter:
    """Telegram Bot API adapter."""

    def __init__(self, use_publishing_bot: bool = False):
        """Initialize Telegram adapter.

        Args:
            use_publishing_bot: Use separate bot for publishing if available
        """
        self.bot_token = self._get_bot_token(use_publishing_bot)
        self.api_base = f"https://api.telegram.org/bot{self.bot_token}"
        self.is_publishing_bot = use_publishing_bot

        # HTTP client configuration
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0),  # Telegram uploads can be slow
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            headers={"User-Agent": "SalesWhisper-Crosspost/1.0"},
        )

        # Rate limiting - Telegram Bot API has flexible limits
        self.rate_limit_per_second = 30  # 30 messages per second
        self.rate_limit_per_minute = 20  # 20 messages per minute to same chat
        self.last_request_times = []
        self.chat_request_times = {}
        self.rate_limit_lock = asyncio.Lock()

        bot_type = "publishing" if use_publishing_bot else "main"
        logger.info("Telegram adapter initialized", bot_type=bot_type, is_publishing_bot=use_publishing_bot)

    def _get_bot_token(self, use_publishing_bot: bool = False) -> str:
        """Get Telegram bot token from settings."""
        import os

        if use_publishing_bot:
            # Try to get publishing bot token first
            if hasattr(settings, "telegram") and hasattr(settings.telegram, "publishing_bot_token"):
                token = settings.telegram.publishing_bot_token
                if hasattr(token, "get_secret_value"):
                    return token.get_secret_value()
                return str(token)

            # Fallback to environment variable for publishing bot
            token = os.getenv("TG_PUBLISHING_BOT_TOKEN")
            if token:
                return token

        # Default bot token
        if hasattr(settings, "telegram") and hasattr(settings.telegram, "bot_token"):
            token = settings.telegram.bot_token
            if hasattr(token, "get_secret_value"):
                return token.get_secret_value()
            return str(token)

        # Fallback to environment variable for main bot
        token = os.getenv("TG_BOT_TOKEN")
        if token:
            return token

        raise TelegramAuthError("Telegram bot token not configured. Set TG_BOT_TOKEN environment variable.")

    async def send_message(self, message: TelegramMessage, correlation_id: str = None) -> TelegramSendResult:
        """
        Send message to Telegram.

        Args:
            message: Telegram message data
            correlation_id: Request correlation ID

        Returns:
            Send result
        """
        start_time = time.time()

        with with_logging_context(correlation_id=correlation_id):
            logger.info(
                "Sending Telegram message",
                chat_id=message.chat_id,
                has_text=bool(message.text),
                media_count=len(message.media_items),
                parse_mode=message.parse_mode.value if message.parse_mode else None,
            )

            try:
                # Determine sending strategy based on content
                if message.media_items and len(message.media_items) > 1:
                    # Multiple media items - use sendMediaGroup
                    result = await self._send_media_group(message, correlation_id)
                elif message.media_items and len(message.media_items) == 1:
                    # Single media item - use specific method
                    result = await self._send_single_media(message, correlation_id)
                elif message.text:
                    # Text only - use sendMessage
                    result = await self._send_text_message(message, correlation_id)
                else:
                    raise TelegramValidationError("Message must contain text or media")

                processing_time = time.time() - start_time

                # Track metrics
                metrics.track_external_api_call(
                    service="telegram", endpoint="send_message", status_code=200, duration=processing_time
                )

                logger.info(
                    "Telegram message sent successfully",
                    message_id=result.message_id,
                    chat_id=message.chat_id,
                    processing_time=processing_time,
                    message_url=result.message_url,
                )

                return result

            except Exception as e:
                processing_time = time.time() - start_time

                # Track failure metrics
                metrics.track_external_api_call(
                    service="telegram",
                    endpoint="send_message",
                    status_code=getattr(e, "error_code", 500),
                    duration=processing_time,
                    error=str(e),
                )

                logger.error(
                    "Failed to send Telegram message",
                    chat_id=message.chat_id,
                    error=str(e),
                    processing_time=processing_time,
                    exc_info=True,
                )

                return TelegramSendResult(
                    message_id=None,
                    status=SendStatus.ERROR,
                    message=str(e),
                    chat_id=message.chat_id,
                    error_code=getattr(e, "error_code", None),
                    retry_after=getattr(e, "retry_after", None),
                )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        retry=retry_if_exception_type((httpx.RequestError, TelegramRateLimitError)),
    )
    async def _send_text_message(self, message: TelegramMessage, correlation_id: str = None) -> TelegramSendResult:
        """Send text message using sendMessage method."""
        with with_logging_context(correlation_id=correlation_id, method="sendMessage"):
            logger.info("Sending text message", chat_id=message.chat_id, text_length=len(message.text))

            params = {
                "chat_id": message.chat_id,
                "text": message.text,
                "disable_web_page_preview": message.disable_web_page_preview,
                "disable_notification": message.disable_notification,
                "protect_content": message.protect_content,
            }

            if message.parse_mode:
                params["parse_mode"] = message.parse_mode.value

            if message.reply_to_message_id:
                params["reply_to_message_id"] = message.reply_to_message_id

            response = await self._make_api_request("sendMessage", params=params)

            message_data = response["result"]
            message_id = message_data["message_id"]

            # Generate message URL if possible
            message_url = self._generate_message_url(message.chat_id, message_id)

            return TelegramSendResult(
                message_id=message_id,
                status=SendStatus.SENT,
                message="Message sent successfully",
                chat_id=message.chat_id,
                sent_at=datetime.now(timezone.utc),
                message_url=message_url,
            )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        retry=retry_if_exception_type((httpx.RequestError, TelegramRateLimitError)),
    )
    async def _send_single_media(self, message: TelegramMessage, correlation_id: str = None) -> TelegramSendResult:
        """Send single media item using specific method (sendPhoto, sendVideo, etc.)."""
        media_item = message.media_items[0]

        with with_logging_context(correlation_id=correlation_id, media_type=media_item.media_type.value):
            logger.info(
                "Sending single media",
                chat_id=message.chat_id,
                media_type=media_item.media_type.value,
                file_path=media_item.file_path,
            )

            # Determine method based on media type
            if media_item.media_type == MediaType.PHOTO:
                method = "sendPhoto"
                file_field = "photo"
            elif media_item.media_type == MediaType.VIDEO:
                method = "sendVideo"
                file_field = "video"
            elif media_item.media_type == MediaType.ANIMATION:
                method = "sendAnimation"
                file_field = "animation"
            elif media_item.media_type == MediaType.DOCUMENT:
                method = "sendDocument"
                file_field = "document"
            elif media_item.media_type == MediaType.AUDIO:
                method = "sendAudio"
                file_field = "audio"
            else:
                raise TelegramValidationError(f"Unsupported media type: {media_item.media_type}")

            # Prepare parameters
            params = {
                "chat_id": message.chat_id,
                "disable_notification": message.disable_notification,
                "protect_content": message.protect_content,
            }

            # Add caption
            caption = media_item.caption or message.text
            if caption:
                params["caption"] = caption
                if media_item.parse_mode:
                    params["parse_mode"] = media_item.parse_mode.value
                elif message.parse_mode:
                    params["parse_mode"] = message.parse_mode.value

            if message.reply_to_message_id:
                params["reply_to_message_id"] = message.reply_to_message_id

            # Add media-specific parameters
            if media_item.media_type == MediaType.VIDEO:
                if media_item.width:
                    params["width"] = media_item.width
                if media_item.height:
                    params["height"] = media_item.height
                if media_item.duration:
                    params["duration"] = media_item.duration
                if media_item.supports_streaming is not None:
                    params["supports_streaming"] = media_item.supports_streaming

            # Send file
            files = None
            if media_item.file_path.startswith(("http://", "https://")):
                # Use URL
                params[file_field] = media_item.file_path
            else:
                # Upload file
                file_content = await self._read_file(media_item.file_path)
                file_name = Path(media_item.file_path).name
                mime_type, _ = mimetypes.guess_type(file_name)

                files = {file_field: (file_name, file_content, mime_type or "application/octet-stream")}

                # Add thumbnail if provided for video
                if media_item.media_type == MediaType.VIDEO and media_item.thumbnail:
                    if media_item.thumbnail.startswith(("http://", "https://")):
                        params["thumb"] = media_item.thumbnail
                    else:
                        thumb_content = await self._read_file(media_item.thumbnail)
                        thumb_name = Path(media_item.thumbnail).name
                        thumb_mime, _ = mimetypes.guess_type(thumb_name)

                        files["thumb"] = (thumb_name, thumb_content, thumb_mime or "image/jpeg")

            response = await self._make_api_request(method, params=params, files=files)

            message_data = response["result"]
            message_id = message_data["message_id"]

            # Generate message URL
            message_url = self._generate_message_url(message.chat_id, message_id)

            return TelegramSendResult(
                message_id=message_id,
                status=SendStatus.SENT,
                message="Media sent successfully",
                chat_id=message.chat_id,
                sent_at=datetime.now(timezone.utc),
                message_url=message_url,
            )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        retry=retry_if_exception_type((httpx.RequestError, TelegramRateLimitError)),
    )
    async def _send_media_group(self, message: TelegramMessage, correlation_id: str = None) -> TelegramSendResult:
        """Send multiple media items using sendMediaGroup method."""
        with with_logging_context(correlation_id=correlation_id, method="sendMediaGroup"):
            logger.info("Sending media group", chat_id=message.chat_id, media_count=len(message.media_items))

            media_array = []
            files = {}
            file_counter = 0

            for i, media_item in enumerate(message.media_items):
                # Prepare media object
                media_obj = {"type": media_item.media_type.value}

                # Handle file/URL
                if media_item.file_path.startswith(("http://", "https://")):
                    media_obj["media"] = media_item.file_path
                else:
                    # Use attach:// prefix for file uploads
                    file_key = f"file_{file_counter}"
                    media_obj["media"] = f"attach://{file_key}"

                    # Read file content
                    file_content = await self._read_file(media_item.file_path)
                    file_name = Path(media_item.file_path).name
                    mime_type, _ = mimetypes.guess_type(file_name)

                    files[file_key] = (file_name, file_content, mime_type or "application/octet-stream")
                    file_counter += 1

                # Add caption to first item or specific item
                if i == 0 and (media_item.caption or message.text):
                    media_obj["caption"] = media_item.caption or message.text
                    if media_item.parse_mode:
                        media_obj["parse_mode"] = media_item.parse_mode.value
                    elif message.parse_mode:
                        media_obj["parse_mode"] = message.parse_mode.value
                elif media_item.caption:
                    media_obj["caption"] = media_item.caption
                    if media_item.parse_mode:
                        media_obj["parse_mode"] = media_item.parse_mode.value

                # Add media-specific parameters
                if media_item.media_type == MediaType.VIDEO:
                    if media_item.width:
                        media_obj["width"] = media_item.width
                    if media_item.height:
                        media_obj["height"] = media_item.height
                    if media_item.duration:
                        media_obj["duration"] = media_item.duration
                    if media_item.supports_streaming is not None:
                        media_obj["supports_streaming"] = media_item.supports_streaming

                    # Handle thumbnail
                    if media_item.thumbnail:
                        if media_item.thumbnail.startswith(("http://", "https://")):
                            media_obj["thumb"] = media_item.thumbnail
                        else:
                            thumb_key = f"thumb_{file_counter}"
                            media_obj["thumb"] = f"attach://{thumb_key}"

                            thumb_content = await self._read_file(media_item.thumbnail)
                            thumb_name = Path(media_item.thumbnail).name
                            thumb_mime, _ = mimetypes.guess_type(thumb_name)

                            files[thumb_key] = (thumb_name, thumb_content, thumb_mime or "image/jpeg")
                            file_counter += 1

                media_array.append(media_obj)

            # Prepare parameters
            params = {
                "chat_id": message.chat_id,
                "media": json.dumps(media_array),
                "disable_notification": message.disable_notification,
                "protect_content": message.protect_content,
            }

            if message.reply_to_message_id:
                params["reply_to_message_id"] = message.reply_to_message_id

            response = await self._make_api_request("sendMediaGroup", params=params, files=files or None)

            # sendMediaGroup returns array of messages
            messages = response["result"]
            first_message = messages[0]
            message_id = first_message["message_id"]

            # Generate message URL for first message
            message_url = self._generate_message_url(message.chat_id, message_id)

            return TelegramSendResult(
                message_id=message_id,
                status=SendStatus.SENT,
                message=f"Media group with {len(messages)} items sent successfully",
                chat_id=message.chat_id,
                sent_at=datetime.now(timezone.utc),
                message_url=message_url,
            )

    async def _read_file(self, file_path: str) -> bytes:
        """Read file content from local path or URL."""
        if file_path.startswith(("http://", "https://")):
            # Download from URL
            response = await self.http_client.get(file_path)
            response.raise_for_status()
            return response.content
        else:
            # Read local file
            file_path_obj = Path(file_path)
            if not file_path_obj.exists():
                raise TelegramFileError(f"File not found: {file_path}")

            return file_path_obj.read_bytes()

    def _generate_message_url(self, chat_id: int | str, message_id: int) -> str | None:
        """Generate message URL if possible."""
        # Only generate URLs for channels/public groups with username
        if isinstance(chat_id, str) and chat_id.startswith("@"):
            username = chat_id[1:]  # Remove @
            return f"https://t.me/{username}/{message_id}"

        # For private chats and groups, we can't generate public URLs
        return None

    async def _check_rate_limits(self, chat_id: int | str):
        """Check and enforce Telegram Bot API rate limits."""
        async with self.rate_limit_lock:
            current_time = time.time()

            # Global rate limit (30 requests per second)
            self.last_request_times = [t for t in self.last_request_times if current_time - t < 1.0]

            if len(self.last_request_times) >= self.rate_limit_per_second:
                oldest_request = min(self.last_request_times)
                wait_time = 1.0 - (current_time - oldest_request)

                if wait_time > 0:
                    logger.warning(f"Telegram global rate limit reached. Waiting {wait_time:.2f} seconds")
                    await asyncio.sleep(wait_time)

            # Per-chat rate limit (20 requests per minute)
            chat_key = str(chat_id)
            if chat_key not in self.chat_request_times:
                self.chat_request_times[chat_key] = []

            chat_times = self.chat_request_times[chat_key]
            chat_times[:] = [t for t in chat_times if current_time - t < 60.0]  # Remove old requests

            if len(chat_times) >= self.rate_limit_per_minute:
                oldest_chat_request = min(chat_times)
                wait_time = 60.0 - (current_time - oldest_chat_request)

                if wait_time > 0:
                    logger.warning(f"Telegram chat rate limit reached for {chat_id}. Waiting {wait_time:.2f} seconds")
                    await asyncio.sleep(wait_time)

            # Record requests
            self.last_request_times.append(current_time)
            self.chat_request_times[chat_key].append(current_time)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        retry=retry_if_exception_type((httpx.RequestError, TelegramRateLimitError)),
    )
    async def _make_api_request(
        self, method: str, params: dict[str, Any] = None, files: dict[str, Any] = None
    ) -> dict[str, Any]:
        """Make authenticated API request to Telegram."""
        # Extract chat_id for rate limiting
        chat_id = params.get("chat_id") if params else None
        if chat_id:
            await self._check_rate_limits(chat_id)

        url = f"{self.api_base}/{method}"

        try:
            if files:
                # Multipart form data for file uploads
                response = await self.http_client.post(url, data=params or {}, files=files)
            else:
                # JSON data for simple requests
                response = await self.http_client.post(url, json=params or {})

            response.raise_for_status()
            result = response.json()

            # Handle Telegram API errors
            if not result.get("ok", False):
                await self._handle_api_error(result)

            return result

        except httpx.RequestError as e:
            logger.warning(f"Telegram API request error, will retry: {e}")
            raise
        except Exception as e:
            logger.error(f"Telegram API request failed: {e}")
            raise

    async def _handle_api_error(self, response_data: dict[str, Any]):
        """Handle Telegram Bot API errors."""
        error_code = response_data.get("error_code")
        description = response_data.get("description", "Unknown error")

        logger.error(
            "Telegram Bot API error", error_code=error_code, description=description, response_data=response_data
        )

        # Handle specific error codes
        if error_code == 400:
            if "Bad Request" in description:
                raise TelegramValidationError(f"Bad Request: {description}")
            else:
                raise TelegramError(f"Client Error: {description}")
        elif error_code == 401:
            raise TelegramAuthError(f"Unauthorized: {description}")
        elif error_code == 403:
            raise TelegramAuthError(f"Forbidden: {description}")
        elif error_code == 404:
            raise TelegramError(f"Not Found: {description}")
        elif error_code == 413:
            raise TelegramFileError(f"File too large: {description}")
        elif error_code == 429:
            # Rate limiting
            retry_after = response_data.get("parameters", {}).get("retry_after", 60)
            error = TelegramRateLimitError(f"Rate limit exceeded: {description}")
            error.retry_after = retry_after
            raise error
        elif error_code == 500:
            raise TelegramError(f"Internal Server Error: {description}")
        else:
            raise TelegramError(f"Telegram API Error {error_code}: {description}")

    async def get_me(self) -> dict[str, Any]:
        """Get bot information."""
        try:
            response = await self._make_api_request("getMe")
            return response["result"]
        except Exception as e:
            logger.error(f"Failed to get bot info: {e}")
            return {}

    async def get_chat(self, chat_id: int | str) -> dict[str, Any]:
        """Get chat information."""
        try:
            response = await self._make_api_request("getChat", params={"chat_id": chat_id})
            return response["result"]
        except Exception as e:
            logger.error(f"Failed to get chat info: {e}", chat_id=chat_id)
            return {}

    async def delete_message(self, chat_id: int | str, message_id: int) -> bool:
        """Delete a message."""
        try:
            await self._make_api_request("deleteMessage", params={"chat_id": chat_id, "message_id": message_id})

            logger.info("Telegram message deleted successfully", chat_id=chat_id, message_id=message_id)
            return True

        except Exception as e:
            logger.error(f"Failed to delete Telegram message: {e}", chat_id=chat_id, message_id=message_id)
            return False

    async def edit_message_text(
        self, chat_id: int | str, message_id: int, text: str, parse_mode: ParseMode = None
    ) -> bool:
        """Edit message text."""
        try:
            params = {"chat_id": chat_id, "message_id": message_id, "text": text}

            if parse_mode:
                params["parse_mode"] = parse_mode.value

            await self._make_api_request("editMessageText", params=params)

            logger.info("Telegram message edited successfully", chat_id=chat_id, message_id=message_id)
            return True

        except Exception as e:
            logger.error(f"Failed to edit Telegram message: {e}", chat_id=chat_id, message_id=message_id)
            return False

    async def update_message_status(self, post_id: str, status: str, result_data: dict[str, Any] = None):
        """Update message status in database."""
        logger.info("Updating Telegram message status", post_id=post_id, status=status, result_data=result_data)

    async def close(self):
        """Close HTTP client and cleanup resources."""
        await self.http_client.aclose()
        logger.info("Telegram adapter closed")


# Global adapter instances
telegram_adapter = TelegramAdapter(use_publishing_bot=False)  # Main bot for intake/admin
telegram_publishing_adapter = TelegramAdapter(use_publishing_bot=True)  # Publishing bot for crossposting


# Convenience functions
async def send_telegram_message(
    chat_id: int | str,
    text: str = None,
    media_files: list[str] = None,
    parse_mode: ParseMode = None,
    disable_notification: bool = False,
    correlation_id: str = None,
) -> TelegramSendResult:
    """
    Send message to Telegram.

    Args:
        chat_id: Chat ID or username
        text: Message text
        media_files: List of media file paths
        parse_mode: Parse mode for text formatting
        disable_notification: Disable notification
        correlation_id: Request correlation ID

    Returns:
        Send result
    """
    # Convert file paths to TelegramMediaItem objects
    media_items = []
    if media_files:
        for file_path in media_files:
            # Detect media type based on file extension
            file_ext = Path(file_path).suffix.lower()
            if file_ext in [".jpg", ".jpeg", ".png", ".webp"]:
                media_type = MediaType.PHOTO
            elif file_ext in [".mp4", ".mov", ".avi", ".mkv"]:
                media_type = MediaType.VIDEO
            elif file_ext in [".gif"]:
                media_type = MediaType.ANIMATION
            elif file_ext in [".mp3", ".wav", ".ogg"]:
                media_type = MediaType.AUDIO
            else:
                # Default to document for other file types
                media_type = MediaType.DOCUMENT

            media_items.append(TelegramMediaItem(file_path=file_path, media_type=media_type))

    message = TelegramMessage(
        chat_id=chat_id,
        text=text,
        media_items=media_items,
        parse_mode=parse_mode,
        disable_notification=disable_notification,
    )

    return await telegram_adapter.send_message(message, correlation_id)


async def send_telegram_photo(
    chat_id: int | str, photo_path: str, caption: str = None, parse_mode: ParseMode = None, correlation_id: str = None
) -> TelegramSendResult:
    """Send photo to Telegram."""
    media_item = TelegramMediaItem(
        file_path=photo_path, media_type=MediaType.PHOTO, caption=caption, parse_mode=parse_mode
    )

    message = TelegramMessage(chat_id=chat_id, media_items=[media_item])

    return await telegram_adapter.send_message(message, correlation_id)


async def send_telegram_video(
    chat_id: int | str,
    video_path: str,
    caption: str = None,
    thumbnail: str = None,
    width: int = None,
    height: int = None,
    duration: int = None,
    correlation_id: str = None,
) -> TelegramSendResult:
    """Send video to Telegram."""
    media_item = TelegramMediaItem(
        file_path=video_path,
        media_type=MediaType.VIDEO,
        caption=caption,
        thumbnail=thumbnail,
        width=width,
        height=height,
        duration=duration,
    )

    message = TelegramMessage(chat_id=chat_id, media_items=[media_item])

    return await telegram_adapter.send_message(message, correlation_id)


async def send_telegram_media_group(
    chat_id: int | str,
    media_files: list[str],
    caption: str = None,
    parse_mode: ParseMode = None,
    correlation_id: str = None,
) -> TelegramSendResult:
    """Send media group to Telegram."""
    media_items = []
    for i, file_path in enumerate(media_files):
        # Detect media type
        file_ext = Path(file_path).suffix.lower()
        if file_ext in [".jpg", ".jpeg", ".png", ".webp"]:
            media_type = MediaType.PHOTO
        elif file_ext in [".mp4", ".mov", ".avi"]:
            media_type = MediaType.VIDEO
        else:
            continue  # Skip unsupported files for media group

        # Add caption only to first item
        item_caption = caption if i == 0 else None

        media_items.append(
            TelegramMediaItem(file_path=file_path, media_type=media_type, caption=item_caption, parse_mode=parse_mode)
        )

    message = TelegramMessage(chat_id=chat_id, media_items=media_items)

    return await telegram_adapter.send_message(message, correlation_id)


async def get_telegram_bot_info() -> dict[str, Any]:
    """Get Telegram bot information."""
    return await telegram_adapter.get_me()


async def get_telegram_chat_info(chat_id: int | str) -> dict[str, Any]:
    """Get Telegram chat information."""
    return await telegram_adapter.get_chat(chat_id)


async def delete_telegram_message(chat_id: int | str, message_id: int) -> bool:
    """Delete Telegram message."""
    return await telegram_adapter.delete_message(chat_id, message_id)


async def cleanup_telegram_adapter():
    """Cleanup Telegram adapter resources."""
    await telegram_adapter.close()
    await telegram_publishing_adapter.close()


# Publishing-specific convenience functions
async def publish_telegram_message(
    chat_id: int | str,
    text: str = None,
    media_files: list[str] = None,
    parse_mode: ParseMode = None,
    disable_notification: bool = False,
    correlation_id: str = None,
) -> TelegramSendResult:
    """
    Publish message to Telegram using publishing bot.

    Args:
        chat_id: Chat ID or username
        text: Message text
        media_files: List of media file paths
        parse_mode: Parse mode for text formatting
        disable_notification: Disable notification
        correlation_id: Request correlation ID

    Returns:
        Send result
    """
    # Convert file paths to TelegramMediaItem objects
    media_items = []
    if media_files:
        for file_path in media_files:
            # Detect media type based on file extension
            file_ext = Path(file_path).suffix.lower()
            if file_ext in [".jpg", ".jpeg", ".png", ".webp"]:
                media_type = MediaType.PHOTO
            elif file_ext in [".mp4", ".mov", ".avi", ".mkv"]:
                media_type = MediaType.VIDEO
            elif file_ext in [".gif"]:
                media_type = MediaType.ANIMATION
            elif file_ext in [".mp3", ".wav", ".ogg"]:
                media_type = MediaType.AUDIO
            else:
                # Default to document for other file types
                media_type = MediaType.DOCUMENT

            media_items.append(TelegramMediaItem(file_path=file_path, media_type=media_type))

    message = TelegramMessage(
        chat_id=chat_id,
        text=text,
        media_items=media_items,
        parse_mode=parse_mode,
        disable_notification=disable_notification,
    )

    return await telegram_publishing_adapter.send_message(message, correlation_id)


async def publish_telegram_photo(
    chat_id: int | str, photo_path: str, caption: str = None, parse_mode: ParseMode = None, correlation_id: str = None
) -> TelegramSendResult:
    """Publish photo to Telegram using publishing bot."""
    media_item = TelegramMediaItem(
        file_path=photo_path, media_type=MediaType.PHOTO, caption=caption, parse_mode=parse_mode
    )

    message = TelegramMessage(chat_id=chat_id, media_items=[media_item])

    return await telegram_publishing_adapter.send_message(message, correlation_id)


async def publish_telegram_video(
    chat_id: int | str,
    video_path: str,
    caption: str = None,
    thumbnail: str = None,
    width: int = None,
    height: int = None,
    duration: int = None,
    correlation_id: str = None,
) -> TelegramSendResult:
    """Publish video to Telegram using publishing bot."""
    media_item = TelegramMediaItem(
        file_path=video_path,
        media_type=MediaType.VIDEO,
        caption=caption,
        thumbnail=thumbnail,
        width=width,
        height=height,
        duration=duration,
    )

    message = TelegramMessage(chat_id=chat_id, media_items=[media_item])

    return await telegram_publishing_adapter.send_message(message, correlation_id)


async def publish_telegram_media_group(
    chat_id: int | str,
    media_files: list[str],
    caption: str = None,
    parse_mode: ParseMode = None,
    correlation_id: str = None,
) -> TelegramSendResult:
    """Publish media group to Telegram using publishing bot."""
    media_items = []
    for i, file_path in enumerate(media_files):
        # Detect media type
        file_ext = Path(file_path).suffix.lower()
        if file_ext in [".jpg", ".jpeg", ".png", ".webp"]:
            media_type = MediaType.PHOTO
        elif file_ext in [".mp4", ".mov", ".avi"]:
            media_type = MediaType.VIDEO
        else:
            continue  # Skip unsupported files for media group

        # Add caption only to first item
        item_caption = caption if i == 0 else None

        media_items.append(
            TelegramMediaItem(file_path=file_path, media_type=media_type, caption=item_caption, parse_mode=parse_mode)
        )

    message = TelegramMessage(chat_id=chat_id, media_items=media_items)

    return await telegram_publishing_adapter.send_message(message, correlation_id)
