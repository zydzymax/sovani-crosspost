"""Runway ML video generation service for Crosspost.

Supports Gen-3 Alpha for text-to-video and image-to-video generation.
"""

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..core.config import settings
from ..core.logging import get_logger

logger = get_logger("services.video_gen_runway")


class RunwayError(Exception):
    """Base exception for Runway ML API errors."""

    pass


class RunwayRateLimitError(RunwayError):
    """Rate limit exceeded."""

    pass


class RunwayGenerationError(RunwayError):
    """Video generation failed."""

    pass


class RunwayAuthError(RunwayError):
    """Authentication failed."""

    pass


class VideoStatus(str, Enum):
    """Runway video generation status."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class VideoModel(str, Enum):
    """Runway video models."""

    GEN3_ALPHA = "gen3a_turbo"  # Gen-3 Alpha Turbo (faster)
    GEN3_ALPHA_FULL = "gen3a"  # Gen-3 Alpha (higher quality)


class AspectRatio(str, Enum):
    """Video aspect ratios."""

    LANDSCAPE = "16:9"
    PORTRAIT = "9:16"
    SQUARE = "1:1"


@dataclass
class RunwayVideoResult:
    """Result of Runway video generation."""

    success: bool
    video_url: str | None = None
    thumbnail_url: str | None = None
    task_id: str | None = None
    duration_seconds: int = 0
    error: str | None = None
    cost_estimate: float = 0.05  # ~$0.05 per second


class RunwayService:
    """Runway ML video generation service."""

    API_BASE = "https://api.runwayml.com/v1"

    # Cost per second of video (approximate)
    COST_PER_SECOND = 0.05

    def __init__(self, api_key: str = None, api_secret: str = None):
        """Initialize Runway service."""
        self.api_key = api_key or self._get_api_key()
        self.api_secret = api_secret or self._get_api_secret()

        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(300.0),  # Video generation is slow
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "X-Runway-Version": "2024-11-01",
                "Content-Type": "application/json",
            },
        )

        logger.info("Runway ML service initialized")

    def _get_api_key(self) -> str:
        """Get API key from settings or environment."""
        if hasattr(settings, "runway") and hasattr(settings.runway, "api_key"):
            key = settings.runway.api_key
            if hasattr(key, "get_secret_value"):
                return key.get_secret_value()
            return str(key)

        import os

        key = os.getenv("RUNWAY_API_KEY")
        if key:
            return key

        raise RunwayError("Runway API key not configured.")

    def _get_api_secret(self) -> str | None:
        """Get API secret from settings or environment."""
        if hasattr(settings, "runway") and hasattr(settings.runway, "api_secret"):
            secret = settings.runway.api_secret
            if hasattr(secret, "get_secret_value"):
                return secret.get_secret_value()
            return str(secret) if secret else None

        import os

        return os.getenv("RUNWAY_API_SECRET")

    async def generate_video_from_text(
        self,
        prompt: str,
        duration: int = 5,
        model: VideoModel = VideoModel.GEN3_ALPHA,
        aspect_ratio: AspectRatio = AspectRatio.LANDSCAPE,
        seed: int = None,
    ) -> RunwayVideoResult:
        """
        Generate video from text prompt.

        Args:
            prompt: Text description for video
            duration: Video duration in seconds (5 or 10)
            model: Which model to use
            aspect_ratio: Video aspect ratio
            seed: Random seed for reproducibility

        Returns:
            RunwayVideoResult with video URL
        """
        start_time = time.time()

        logger.info(
            "Starting Runway text-to-video generation",
            prompt_length=len(prompt),
            duration=duration,
            model=model.value,
            aspect_ratio=aspect_ratio.value,
        )

        try:
            # Submit generation task
            task_id = await self._submit_text_to_video(
                prompt=prompt, duration=duration, model=model, aspect_ratio=aspect_ratio, seed=seed
            )

            # Poll for completion
            result = await self._poll_for_result(task_id)

            processing_time = time.time() - start_time

            logger.info(
                "Runway video generation completed",
                task_id=task_id,
                processing_time=processing_time,
                success=result.success,
            )

            return result

        except Exception as e:
            logger.error(f"Runway video generation failed: {e}", exc_info=True)
            return RunwayVideoResult(success=False, error=str(e))

    async def generate_video_from_image(
        self,
        image_url: str,
        prompt: str = "",
        duration: int = 5,
        model: VideoModel = VideoModel.GEN3_ALPHA,
        seed: int = None,
    ) -> RunwayVideoResult:
        """
        Generate video from image (image-to-video).

        Args:
            image_url: URL of source image
            prompt: Optional text to guide motion
            duration: Video duration in seconds (5 or 10)
            model: Which model to use
            seed: Random seed for reproducibility

        Returns:
            RunwayVideoResult with video URL
        """
        start_time = time.time()

        logger.info(
            "Starting Runway image-to-video generation",
            image_url=image_url[:50] + "...",
            duration=duration,
            model=model.value,
        )

        try:
            # Submit generation task
            task_id = await self._submit_image_to_video(
                image_url=image_url, prompt=prompt, duration=duration, model=model, seed=seed
            )

            # Poll for completion
            result = await self._poll_for_result(task_id)

            processing_time = time.time() - start_time

            logger.info(
                "Runway image-to-video completed",
                task_id=task_id,
                processing_time=processing_time,
                success=result.success,
            )

            return result

        except Exception as e:
            logger.error(f"Runway image-to-video failed: {e}", exc_info=True)
            return RunwayVideoResult(success=False, error=str(e))

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.RequestError, RunwayRateLimitError)),
    )
    async def _submit_text_to_video(
        self, prompt: str, duration: int, model: VideoModel, aspect_ratio: AspectRatio, seed: int = None
    ) -> str:
        """Submit text-to-video generation task."""
        request_data = {
            "model": model.value,
            "promptText": prompt,
            "duration": duration,
            "ratio": aspect_ratio.value,
            "watermark": False,
        }

        if seed is not None:
            request_data["seed"] = seed

        response = await self.http_client.post(f"{self.API_BASE}/text-to-video", json=request_data)

        await self._handle_response_errors(response)

        data = response.json()
        task_id = data.get("id")

        if not task_id:
            raise RunwayError(f"No task ID in response: {data}")

        logger.info(f"Text-to-video task submitted: {task_id}")
        return task_id

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.RequestError, RunwayRateLimitError)),
    )
    async def _submit_image_to_video(
        self, image_url: str, prompt: str, duration: int, model: VideoModel, seed: int = None
    ) -> str:
        """Submit image-to-video generation task."""
        request_data = {"model": model.value, "promptImage": image_url, "duration": duration, "watermark": False}

        if prompt:
            request_data["promptText"] = prompt

        if seed is not None:
            request_data["seed"] = seed

        response = await self.http_client.post(f"{self.API_BASE}/image-to-video", json=request_data)

        await self._handle_response_errors(response)

        data = response.json()
        task_id = data.get("id")

        if not task_id:
            raise RunwayError(f"No task ID in response: {data}")

        logger.info(f"Image-to-video task submitted: {task_id}")
        return task_id

    async def _poll_for_result(self, task_id: str, max_wait: int = 600) -> RunwayVideoResult:
        """Poll for task completion."""
        start_time = time.time()
        poll_interval = 10  # seconds (video gen is slow)

        while time.time() - start_time < max_wait:
            status = await self._get_task_status(task_id)

            task_status = status.get("status", "").lower()

            if task_status in ["succeeded", "completed"]:
                return self._parse_completed_task(status)

            if task_status in ["failed", "error"]:
                error_msg = status.get("error", status.get("message", "Unknown error"))
                return RunwayVideoResult(success=False, task_id=task_id, error=error_msg)

            if task_status == "cancelled":
                return RunwayVideoResult(success=False, task_id=task_id, error="Task was cancelled")

            # Log progress
            progress = status.get("progress", 0)
            if progress:
                logger.info(f"Generation progress: {progress}%", task_id=task_id)

            await asyncio.sleep(poll_interval)

        return RunwayVideoResult(success=False, task_id=task_id, error=f"Generation timed out after {max_wait} seconds")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        retry=retry_if_exception_type(httpx.RequestError),
    )
    async def _get_task_status(self, task_id: str) -> dict[str, Any]:
        """Get task status from API."""
        response = await self.http_client.get(f"{self.API_BASE}/tasks/{task_id}")

        await self._handle_response_errors(response)

        return response.json()

    def _parse_completed_task(self, status: dict[str, Any]) -> RunwayVideoResult:
        """Parse completed task response."""
        # Get video URL
        video_url = status.get("output", [None])[0]
        if not video_url:
            output = status.get("output")
            if isinstance(output, str):
                video_url = output
            elif isinstance(output, dict):
                video_url = output.get("url") or output.get("video_url")

        # Get thumbnail
        thumbnail_url = status.get("thumbnail")

        # Get duration
        duration = status.get("duration", 5)

        # Calculate cost
        cost = duration * self.COST_PER_SECOND

        return RunwayVideoResult(
            success=video_url is not None,
            video_url=video_url,
            thumbnail_url=thumbnail_url,
            task_id=status.get("id"),
            duration_seconds=duration,
            cost_estimate=cost,
        )

    async def _handle_response_errors(self, response: httpx.Response):
        """Handle API response errors."""
        if response.status_code == 401:
            raise RunwayAuthError("Invalid API key")

        if response.status_code == 429:
            raise RunwayRateLimitError("Rate limit exceeded")

        if response.status_code == 402:
            raise RunwayError("Insufficient credits")

        if response.status_code >= 400:
            error_text = response.text
            try:
                error_data = response.json()
                error_text = error_data.get("error", error_data.get("message", error_text))
            except:
                pass
            raise RunwayError(f"API Error {response.status_code}: {error_text}")

    async def get_account_info(self) -> dict[str, Any]:
        """Get account information including credits."""
        try:
            response = await self.http_client.get(f"{self.API_BASE}/account")

            if response.status_code == 200:
                return response.json()

            return {}

        except Exception as e:
            logger.warning(f"Failed to get account info: {e}")
            return {}

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a pending or processing task."""
        try:
            response = await self.http_client.post(f"{self.API_BASE}/tasks/{task_id}/cancel")

            if response.status_code in [200, 204]:
                logger.info(f"Task cancelled: {task_id}")
                return True

            return False

        except Exception as e:
            logger.error(f"Failed to cancel task: {e}", task_id=task_id)
            return False

    async def close(self):
        """Close HTTP client."""
        await self.http_client.aclose()
        logger.info("Runway service closed")


# Convenience functions
async def generate_runway_video(
    prompt: str, duration: int = 5, aspect_ratio: str = "16:9", api_key: str = None
) -> RunwayVideoResult:
    """Generate video from text prompt using Runway."""
    service = RunwayService(api_key)
    try:
        ar = AspectRatio(aspect_ratio) if aspect_ratio in [a.value for a in AspectRatio] else AspectRatio.LANDSCAPE
        return await service.generate_video_from_text(prompt, duration, aspect_ratio=ar)
    finally:
        await service.close()


async def generate_runway_video_from_image(
    image_url: str, prompt: str = "", duration: int = 5, api_key: str = None
) -> RunwayVideoResult:
    """Generate video from image using Runway."""
    service = RunwayService(api_key)
    try:
        return await service.generate_video_from_image(image_url, prompt, duration)
    finally:
        await service.close()
