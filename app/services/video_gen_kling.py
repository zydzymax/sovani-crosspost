"""Kling AI video generation service for Crosspost.

Supports Kling 2.0 for text-to-video and image-to-video generation.
"""

import asyncio
import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx
import jwt
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..core.logging import get_logger

logger = get_logger("services.video_gen_kling")


class KlingError(Exception):
    """Base exception for Kling API errors."""
    pass


class KlingRateLimitError(KlingError):
    """Rate limit exceeded."""
    pass


class KlingGenerationError(KlingError):
    """Video generation failed."""
    pass


class KlingAuthError(KlingError):
    """Authentication failed."""
    pass


class VideoStatus(str, Enum):
    """Kling video generation status."""
    PENDING = "submitted"
    PROCESSING = "processing"
    COMPLETED = "succeed"
    FAILED = "failed"


class KlingModel(str, Enum):
    """Kling video models."""
    KLING_V1 = "kling-v1"
    KLING_V1_5 = "kling-v1-5"
    KLING_V2 = "kling-v2"


class AspectRatio(str, Enum):
    """Video aspect ratios."""
    LANDSCAPE = "16:9"
    PORTRAIT = "9:16"
    SQUARE = "1:1"


class VideoDuration(str, Enum):
    """Video duration options."""
    SHORT = "5"   # 5 seconds
    LONG = "10"   # 10 seconds


@dataclass
class KlingVideoResult:
    """Result of Kling video generation."""
    success: bool
    video_url: str | None = None
    thumbnail_url: str | None = None
    task_id: str | None = None
    duration_seconds: int = 0
    error: str | None = None
    cost_estimate: float = 0.25  # ~$0.25 per 5 sec video


class KlingService:
    """Kling AI video generation service."""

    API_BASE = "https://api.klingai.com/v1"

    # Cost per video (approximate)
    COST_PER_5_SEC = 0.25
    COST_PER_10_SEC = 0.50

    def __init__(self, access_key: str = None, secret_key: str = None):
        """Initialize Kling service."""
        self.access_key = access_key or self._get_access_key()
        self.secret_key = secret_key or self._get_secret_key()

        self.http_client = None
        logger.info("Kling AI service initialized")

    def _get_access_key(self) -> str:
        """Get Kling access key from settings or environment."""
        return os.getenv('KLING_ACCESS_KEY', '')

    def _get_secret_key(self) -> str:
        """Get Kling secret key from settings or environment."""
        return os.getenv('KLING_SECRET_KEY', '')

    def _generate_jwt_token(self) -> str:
        """Generate JWT token for Kling API authentication."""
        now = int(time.time())
        payload = {
            "iss": self.access_key,
            "exp": now + 1800,  # 30 minutes
            "nbf": now - 5
        }
        return jwt.encode(payload, self.secret_key, algorithm="HS256")

    async def _get_client(self) -> httpx.AsyncClient:
        """Get HTTP client with fresh JWT token."""
        token = self._generate_jwt_token()
        return httpx.AsyncClient(
            timeout=httpx.Timeout(300.0),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=60),
        retry=retry_if_exception_type((httpx.TimeoutException, KlingRateLimitError))
    )
    async def generate_from_text(
        self,
        prompt: str,
        negative_prompt: str = "",
        model: KlingModel = KlingModel.KLING_V2,
        duration: VideoDuration = VideoDuration.SHORT,
        aspect_ratio: AspectRatio = AspectRatio.LANDSCAPE,
        cfg_scale: float = 0.5
    ) -> KlingVideoResult:
        """Generate video from text prompt."""
        logger.info("Starting Kling text-to-video generation", prompt=prompt[:50])

        async with await self._get_client() as client:
            payload = {
                "model_name": model.value,
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "cfg_scale": cfg_scale,
                "duration": duration.value,
                "aspect_ratio": aspect_ratio.value
            }

            try:
                response = await client.post(
                    f"{self.API_BASE}/videos/text2video",
                    json=payload
                )

                if response.status_code == 401:
                    raise KlingAuthError("Invalid API credentials")
                elif response.status_code == 429:
                    raise KlingRateLimitError("Rate limit exceeded")
                elif response.status_code != 200:
                    raise KlingGenerationError(f"API error: {response.status_code}")

                data = response.json()

                if data.get("code") != 0:
                    raise KlingGenerationError(data.get("message", "Unknown error"))

                task_id = data.get("data", {}).get("task_id")

                if not task_id:
                    raise KlingGenerationError("No task_id returned")

                # Poll for result
                return await self._poll_for_result(task_id, int(duration.value))

            except httpx.TimeoutException:
                logger.error("Kling API timeout")
                raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=60),
        retry=retry_if_exception_type((httpx.TimeoutException, KlingRateLimitError))
    )
    async def generate_from_image(
        self,
        image_url: str,
        prompt: str = "",
        negative_prompt: str = "",
        model: KlingModel = KlingModel.KLING_V2,
        duration: VideoDuration = VideoDuration.SHORT,
        cfg_scale: float = 0.5
    ) -> KlingVideoResult:
        """Generate video from image."""
        logger.info("Starting Kling image-to-video generation")

        async with await self._get_client() as client:
            payload = {
                "model_name": model.value,
                "image": image_url,
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "cfg_scale": cfg_scale,
                "duration": duration.value
            }

            try:
                response = await client.post(
                    f"{self.API_BASE}/videos/image2video",
                    json=payload
                )

                if response.status_code == 401:
                    raise KlingAuthError("Invalid API credentials")
                elif response.status_code == 429:
                    raise KlingRateLimitError("Rate limit exceeded")
                elif response.status_code != 200:
                    raise KlingGenerationError(f"API error: {response.status_code}")

                data = response.json()

                if data.get("code") != 0:
                    raise KlingGenerationError(data.get("message", "Unknown error"))

                task_id = data.get("data", {}).get("task_id")

                if not task_id:
                    raise KlingGenerationError("No task_id returned")

                return await self._poll_for_result(task_id, int(duration.value))

            except httpx.TimeoutException:
                logger.error("Kling API timeout")
                raise

    async def _poll_for_result(
        self,
        task_id: str,
        duration: int,
        max_attempts: int = 120,
        poll_interval: int = 5
    ) -> KlingVideoResult:
        """Poll for video generation result."""
        logger.info("Polling for Kling result", task_id=task_id)

        for _attempt in range(max_attempts):
            async with await self._get_client() as client:
                response = await client.get(
                    f"{self.API_BASE}/videos/text2video/{task_id}"
                )

                if response.status_code != 200:
                    logger.warning(f"Poll request failed: {response.status_code}")
                    await asyncio.sleep(poll_interval)
                    continue

                data = response.json()
                task_data = data.get("data", {})
                status = task_data.get("task_status")

                if status == VideoStatus.COMPLETED.value:
                    videos = task_data.get("task_result", {}).get("videos", [])
                    if videos:
                        video = videos[0]
                        cost = self.COST_PER_5_SEC if duration <= 5 else self.COST_PER_10_SEC
                        return KlingVideoResult(
                            success=True,
                            video_url=video.get("url"),
                            thumbnail_url=video.get("cover_url"),
                            task_id=task_id,
                            duration_seconds=video.get("duration", duration),
                            cost_estimate=cost
                        )

                elif status == VideoStatus.FAILED.value:
                    error_msg = task_data.get("task_status_msg", "Generation failed")
                    return KlingVideoResult(
                        success=False,
                        task_id=task_id,
                        error=error_msg
                    )

                await asyncio.sleep(poll_interval)

        return KlingVideoResult(
            success=False,
            task_id=task_id,
            error="Timeout waiting for video generation"
        )

    async def get_task_status(self, task_id: str) -> dict[str, Any]:
        """Get status of a video generation task."""
        async with await self._get_client() as client:
            response = await client.get(
                f"{self.API_BASE}/videos/text2video/{task_id}"
            )
            return response.json()

    async def close(self):
        """Close HTTP client."""
        if self.http_client:
            await self.http_client.aclose()


# Singleton instance
_kling_service: KlingService | None = None


def get_kling_service() -> KlingService:
    """Get or create Kling service instance."""
    global _kling_service
    if _kling_service is None:
        _kling_service = KlingService()
    return _kling_service
