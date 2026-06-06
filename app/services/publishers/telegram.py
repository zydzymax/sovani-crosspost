"""
Telegram publisher for SalesWhisper Crosspost.
"""

from dataclasses import dataclass

import httpx

from ...core.logging import get_logger
from ...core.security import decrypt_data

logger = get_logger("publishers.telegram")


@dataclass
class PublishResult:
    success: bool
    platform_post_id: str | None = None
    platform_url: str | None = None
    error: str | None = None


class TelegramPublisher:
    """Publish content to Telegram channels/groups."""

    BASE_URL = "https://api.telegram.org/bot"

    def __init__(self, account):
        """Initialize with SocialAccount."""
        self.account = account
        extra = account.extra_credentials or {}

        # Decrypt access_token if it looks encrypted
        # Valid Telegram bot token format: NUMBER:ALPHANUMERIC (e.g., 7936274857:AAGRxz...)
        # Encrypted tokens don't have ':' character
        raw_token = account.access_token or extra.get("bot_token", "")
        if raw_token:
            if ":" in raw_token:
                # Already looks like a valid bot token
                self.bot_token = raw_token
            else:
                # Try to decrypt
                try:
                    self.bot_token = decrypt_data(raw_token)
                    logger.info("Decrypted Telegram token successfully")
                except Exception as e:
                    logger.warning("Failed to decrypt Telegram token, using as-is", error=str(e))
                    self.bot_token = raw_token
        else:
            self.bot_token = ""

        # Get chat_id from extra_credentials
        self.chat_id = extra.get("chat_id", "")

        if not self.bot_token or not self.chat_id:
            raise ValueError(
                f"Telegram credentials missing: bot_token={bool(self.bot_token)}, chat_id={bool(self.chat_id)}"
            )

    async def publish(
        self,
        text: str,
        image_url: str | None = None,
        video_url: str | None = None,
        media_urls: list[str] | None = None,
        hashtags: list[str] | None = None,
        parse_mode: str = "HTML",
    ) -> PublishResult:
        """Publish post to Telegram."""
        message = self._format_message(text, hashtags)
        media_candidates = [url for url in (media_urls or []) if isinstance(url, str) and url.strip()]

        if not media_candidates:
            if video_url:
                media_candidates.append(video_url)
            if image_url:
                media_candidates.append(image_url)

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                result: PublishResult | None = None

                if len(media_candidates) > 1:
                    result = await self._send_media_group(client, message, media_candidates, parse_mode)
                    if not result.success:
                        logger.warning("Telegram media group failed, falling back to text", error=result.error)
                        result = await self._send_message(client, message, parse_mode)
                elif len(media_candidates) == 1:
                    media_url = media_candidates[0]
                    media_type = self._detect_media_type(media_url)
                    if media_type == "video":
                        result = await self._send_video(client, message, media_url, parse_mode)
                    else:
                        result = await self._send_photo(client, message, media_url, parse_mode)

                    if not result.success:
                        logger.warning("Telegram media publish failed, falling back to text", error=result.error)
                        result = await self._send_message(client, message, parse_mode)
                else:
                    result = await self._send_message(client, message, parse_mode)

                return result
            except Exception as e:
                logger.exception("Telegram publish failed")
                return PublishResult(success=False, error=str(e))

    async def _send_message(self, client: httpx.AsyncClient, text: str, parse_mode: str) -> PublishResult:
        """Send text message."""
        url = f"{self.BASE_URL}{self.bot_token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": parse_mode}

        response = await client.post(url, json=payload)
        return self._parse_telegram_result(response.json())

    async def _send_photo(
        self, client: httpx.AsyncClient, caption: str, image_url: str, parse_mode: str
    ) -> PublishResult:
        """Send photo with caption."""
        url = f"{self.BASE_URL}{self.bot_token}/sendPhoto"
        payload = {"chat_id": self.chat_id, "photo": image_url, "caption": caption[:1024], "parse_mode": parse_mode}

        response = await client.post(url, json=payload)
        return self._parse_telegram_result(response.json())

    async def _send_video(
        self, client: httpx.AsyncClient, caption: str, video_url: str, parse_mode: str
    ) -> PublishResult:
        """Send video with caption."""
        url = f"{self.BASE_URL}{self.bot_token}/sendVideo"
        payload = {"chat_id": self.chat_id, "video": video_url, "caption": caption[:1024], "parse_mode": parse_mode}

        response = await client.post(url, json=payload)
        return self._parse_telegram_result(response.json())

    async def _send_media_group(
        self, client: httpx.AsyncClient, caption: str, media_urls: list[str], parse_mode: str
    ) -> PublishResult:
        """Send media group (album) with mixed photos/videos by URL."""
        url = f"{self.BASE_URL}{self.bot_token}/sendMediaGroup"
        media = []

        for idx, media_url in enumerate(media_urls[:10]):
            item = {"type": self._detect_media_type(media_url), "media": media_url}
            if idx == 0 and caption:
                item["caption"] = caption[:1024]
                item["parse_mode"] = parse_mode
            media.append(item)

        payload = {"chat_id": self.chat_id, "media": media}
        response = await client.post(url, json=payload)
        data = response.json()

        if data.get("ok") and data.get("result"):
            first_message = data["result"][0]
            message_id = first_message["message_id"]
            return self._success_result(message_id)
        else:
            return self._error_result(data.get("description", "Unknown error"))

    def _detect_media_type(self, media_url: str) -> str:
        """Detect Telegram media type by URL extension."""
        url = media_url.lower().split("?", 1)[0]
        video_ext = (".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv")
        if url.endswith(video_ext):
            return "video"
        return "photo"

    def _format_message(self, text: str, hashtags: list[str] | None) -> str:
        """Format message with hashtags."""
        message = text or ""
        if hashtags:
            tags = " ".join(f"#{tag.replace('#', '')}" for tag in hashtags)
            message = f"{message}\n\n{tags}"
        return message.strip()

    def _success_result(self, message_id: int | str) -> PublishResult:
        chat_id = str(self.chat_id).replace("-100", "")
        return PublishResult(
            success=True,
            platform_post_id=str(message_id),
            platform_url=f"https://t.me/c/{chat_id}/{message_id}",
        )

    def _error_result(self, error: str) -> PublishResult:
        logger.error("Telegram API error", error=error)
        return PublishResult(success=False, error=error)

    def _parse_telegram_result(self, data: dict) -> PublishResult:
        if data.get("ok"):
            message_id = data["result"]["message_id"]
            return self._success_result(message_id)
        return self._error_result(data.get("description", "Unknown error"))
