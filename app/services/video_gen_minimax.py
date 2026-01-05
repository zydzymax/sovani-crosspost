"""MiniMax (Hailuo) video generation service for Crosspost.

Supports Hailuo Video-01 for text-to-video and image-to-video generation.
"""

import asyncio
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..core.logging import get_logger

logger = get_logger("services.video_gen_minimax")


class MinimaxError(Exception):
    """Base exception for Minimax API errors."""

    pass


class MinimaxRateLimitError(MinimaxError):
    """Rate limit exceeded."""

    pass


class MinimaxGenerationError(MinimaxError):
    """Video generation failed."""

    pass


class MinimaxAuthError(MinimaxError):
    """Authentication failed."""

    pass


class VideoStatus(str, Enum):
    """Minimax video generation status."""

    PENDING = "Queueing"
    PROCESSING = "Processing"
    COMPLETED = "Success"
    FAILED = "Fail"


class MinimaxModel(str, Enum):
    """Minimax video models."""

    VIDEO_01 = "video-01"  # Standard quality
    VIDEO_01_LIVE = "video-01-live"  # Faster, slightly lower quality
    S2V_01 = "S2V-01"  # Subject-to-video


@dataclass
class MinimaxVideoResult:
    """Result of Minimax video generation."""

    success: bool
    video_url: str | None = None
    thumbnail_url: str | None = None
    task_id: str | None = None
    duration_seconds: int = 0
    error: str | None = None
    cost_estimate: float = 0.28  # ~$0.28 per video


class MinimaxService:
    """MiniMax (Hailuo) video generation service."""

    API_BASE = "https://api.minimax.chat/v1"

    # Cost per video (approximate)
    COST_PER_VIDEO = 0.28

    def __init__(self, api_key: str = None):
        """Initialize Minimax service."""
        self.api_key = api_key or self._get_api_key()

        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(300.0),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )

        logger.info("MiniMax (Hailuo) service initialized")

    def _get_api_key(self) -> str:
        """Get Minimax API key from settings or environment."""
        return os.getenv("MINIMAX_API_KEY", "")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=60),
        retry=retry_if_exception_type((httpx.TimeoutException, MinimaxRateLimitError)),
    )
    async def generate_from_text(self, prompt: str, model: MinimaxModel = MinimaxModel.VIDEO_01) -> MinimaxVideoResult:
        """Generate video from text prompt."""
        logger.info("Starting Minimax text-to-video generation", prompt=prompt[:50])

        payload = {"model": model.value, "prompt": prompt}

        try:
            response = await self.http_client.post(f"{self.API_BASE}/video_generation", json=payload)

            if response.status_code == 401:
                raise MinimaxAuthError("Invalid API credentials")
            elif response.status_code == 429:
                raise MinimaxRateLimitError("Rate limit exceeded")
            elif response.status_code != 200:
                raise MinimaxGenerationError(f"API error: {response.status_code}")

            data = response.json()

            if data.get("base_resp", {}).get("status_code") != 0:
                error_msg = data.get("base_resp", {}).get("status_msg", "Unknown error")
                raise MinimaxGenerationError(error_msg)

            task_id = data.get("task_id")

            if not task_id:
                raise MinimaxGenerationError("No task_id returned")

            # Poll for result
            return await self._poll_for_result(task_id)

        except httpx.TimeoutException:
            logger.error("Minimax API timeout")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=60),
        retry=retry_if_exception_type((httpx.TimeoutException, MinimaxRateLimitError)),
    )
    async def generate_from_image(
        self, image_url: str, prompt: str = "", model: MinimaxModel = MinimaxModel.VIDEO_01
    ) -> MinimaxVideoResult:
        """Generate video from image."""
        logger.info("Starting Minimax image-to-video generation")

        payload = {"model": model.value, "first_frame_image": image_url, "prompt": prompt}

        try:
            response = await self.http_client.post(f"{self.API_BASE}/video_generation", json=payload)

            if response.status_code == 401:
                raise MinimaxAuthError("Invalid API credentials")
            elif response.status_code == 429:
                raise MinimaxRateLimitError("Rate limit exceeded")
            elif response.status_code != 200:
                raise MinimaxGenerationError(f"API error: {response.status_code}")

            data = response.json()

            if data.get("base_resp", {}).get("status_code") != 0:
                error_msg = data.get("base_resp", {}).get("status_msg", "Unknown error")
                raise MinimaxGenerationError(error_msg)

            task_id = data.get("task_id")

            if not task_id:
                raise MinimaxGenerationError("No task_id returned")

            return await self._poll_for_result(task_id)

        except httpx.TimeoutException:
            logger.error("Minimax API timeout")
            raise

    async def _poll_for_result(
        self, task_id: str, max_attempts: int = 180, poll_interval: int = 5  # Up to 15 minutes
    ) -> MinimaxVideoResult:
        """Poll for video generation result."""
        logger.info("Polling for Minimax result", task_id=task_id)

        for _attempt in range(max_attempts):
            response = await self.http_client.get(
                f"{self.API_BASE}/query/video_generation", params={"task_id": task_id}
            )

            if response.status_code != 200:
                logger.warning(f"Poll request failed: {response.status_code}")
                await asyncio.sleep(poll_interval)
                continue

            data = response.json()
            status = data.get("status")

            if status == VideoStatus.COMPLETED.value:
                file_id = data.get("file_id")

                # Get download URL
                download_response = await self.http_client.get(
                    f"{self.API_BASE}/files/retrieve", params={"file_id": file_id}
                )

                if download_response.status_code == 200:
                    download_data = download_response.json()
                    video_url = download_data.get("file", {}).get("download_url")

                    return MinimaxVideoResult(
                        success=True,
                        video_url=video_url,
                        task_id=task_id,
                        duration_seconds=6,  # Default Hailuo duration
                        cost_estimate=self.COST_PER_VIDEO,
                    )
                else:
                    return MinimaxVideoResult(success=False, task_id=task_id, error="Failed to get download URL")

            elif status == VideoStatus.FAILED.value:
                error_msg = data.get("base_resp", {}).get("status_msg", "Generation failed")
                return MinimaxVideoResult(success=False, task_id=task_id, error=error_msg)

            await asyncio.sleep(poll_interval)

        return MinimaxVideoResult(success=False, task_id=task_id, error="Timeout waiting for video generation")

    async def get_task_status(self, task_id: str) -> dict[str, Any]:
        """Get status of a video generation task."""
        response = await self.http_client.get(f"{self.API_BASE}/query/video_generation", params={"task_id": task_id})
        return response.json()

    async def close(self):
        """Close HTTP client."""
        if self.http_client:
            await self.http_client.aclose()


# Singleton instance
_minimax_service: MinimaxService | None = None


def get_minimax_service() -> MinimaxService:
    """Get or create Minimax service instance."""
    global _minimax_service
    if _minimax_service is None:
        _minimax_service = MinimaxService()
    return _minimax_service
