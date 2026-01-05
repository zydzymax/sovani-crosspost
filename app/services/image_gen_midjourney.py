"""Midjourney image generation service for Crosspost.

Uses unofficial API via goapi.ai or similar providers.
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

logger = get_logger("services.image_gen_midjourney")


class MidjourneyError(Exception):
    """Base exception for Midjourney API errors."""
    pass


class MidjourneyRateLimitError(MidjourneyError):
    """Rate limit exceeded."""
    pass


class MidjourneyGenerationError(MidjourneyError):
    """Image generation failed."""
    pass


class TaskStatus(str, Enum):
    """Midjourney task status."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class MidjourneyResult:
    """Result of Midjourney image generation."""
    success: bool
    image_url: str | None = None
    image_urls: list = None  # For grid of 4 images
    task_id: str | None = None
    error: str | None = None
    cost_estimate: float = 0.08  # ~$0.08 per generation

    def __post_init__(self):
        if self.image_urls is None:
            self.image_urls = []


class MidjourneyService:
    """Midjourney image generation service using unofficial API."""

    # API endpoint (using goapi.ai as example, can be changed)
    API_BASE = "https://api.goapi.ai/mj/v2"

    # Cost per image generation (for pricing calculations)
    COST_PER_IMAGE = 0.08

    def __init__(self, api_key: str = None, api_url: str = None):
        """Initialize Midjourney service."""
        self.api_key = api_key or self._get_api_key()
        self.api_base = api_url or self._get_api_url()

        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
        )

        logger.info("Midjourney service initialized")

    def _get_api_key(self) -> str:
        """Get API key from settings or environment."""
        if hasattr(settings, 'midjourney') and hasattr(settings.midjourney, 'api_key'):
            key = settings.midjourney.api_key
            if hasattr(key, 'get_secret_value'):
                return key.get_secret_value()
            return str(key)

        import os
        key = os.getenv('MIDJOURNEY_API_KEY')
        if key:
            return key

        raise MidjourneyError("Midjourney API key not configured.")

    def _get_api_url(self) -> str:
        """Get API URL from settings or environment."""
        if hasattr(settings, 'midjourney') and hasattr(settings.midjourney, 'api_url'):
            return str(settings.midjourney.api_url)

        import os
        return os.getenv('MIDJOURNEY_API_URL', self.API_BASE)

    async def generate_image(
        self,
        prompt: str,
        aspect_ratio: str = "1:1",
        quality: str = "standard",
        style: str = None,
        negative_prompt: str = None,
        seed: int = None
    ) -> MidjourneyResult:
        """
        Generate image using Midjourney.

        Args:
            prompt: Text description for image generation
            aspect_ratio: Image aspect ratio (1:1, 16:9, 9:16, 4:3, 3:4)
            quality: Generation quality (standard, hd)
            style: Style preset (raw, cute, scenic, etc.)
            negative_prompt: What to avoid in the image
            seed: Random seed for reproducibility

        Returns:
            MidjourneyResult with image URLs
        """
        start_time = time.time()

        logger.info(
            "Starting Midjourney generation",
            prompt_length=len(prompt),
            aspect_ratio=aspect_ratio,
            quality=quality
        )

        try:
            # Build the full prompt with parameters
            full_prompt = self._build_prompt(prompt, aspect_ratio, quality, style, negative_prompt, seed)

            # Submit generation task
            task_id = await self._submit_task(full_prompt)

            # Poll for completion
            result = await self._poll_for_result(task_id)

            processing_time = time.time() - start_time

            logger.info(
                "Midjourney generation completed",
                task_id=task_id,
                processing_time=processing_time,
                image_count=len(result.image_urls) if result.image_urls else 0
            )

            return result

        except Exception as e:
            logger.error(f"Midjourney generation failed: {e}", exc_info=True)
            return MidjourneyResult(
                success=False,
                error=str(e)
            )

    def _build_prompt(
        self,
        prompt: str,
        aspect_ratio: str,
        quality: str,
        style: str,
        negative_prompt: str,
        seed: int
    ) -> str:
        """Build full Midjourney prompt with parameters."""
        parts = [prompt]

        # Add aspect ratio
        if aspect_ratio and aspect_ratio != "1:1":
            parts.append(f"--ar {aspect_ratio}")

        # Add quality (v6 default is standard)
        if quality == "hd":
            parts.append("--q 2")

        # Add style
        if style:
            parts.append(f"--style {style}")

        # Add negative prompt
        if negative_prompt:
            parts.append(f"--no {negative_prompt}")

        # Add seed
        if seed is not None:
            parts.append(f"--seed {seed}")

        # Always use v6
        parts.append("--v 6")

        return " ".join(parts)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.RequestError, MidjourneyRateLimitError))
    )
    async def _submit_task(self, prompt: str) -> str:
        """Submit image generation task."""
        response = await self.http_client.post(
            f"{self.api_base}/imagine",
            json={
                "prompt": prompt,
                "process_mode": "fast"  # or "relax"
            }
        )

        if response.status_code == 429:
            raise MidjourneyRateLimitError("Rate limit exceeded")

        if response.status_code != 200:
            error_text = response.text
            raise MidjourneyError(f"Failed to submit task: {response.status_code} - {error_text}")

        data = response.json()
        task_id = data.get("task_id")

        if not task_id:
            raise MidjourneyError(f"No task_id in response: {data}")

        logger.info(f"Task submitted: {task_id}")
        return task_id

    async def _poll_for_result(self, task_id: str, max_wait: int = 300) -> MidjourneyResult:
        """Poll for task completion."""
        start_time = time.time()
        poll_interval = 5  # seconds

        while time.time() - start_time < max_wait:
            status = await self._get_task_status(task_id)

            if status.get("status") == "completed":
                return self._parse_completed_task(status)

            if status.get("status") == "failed":
                error_msg = status.get("error", "Unknown error")
                return MidjourneyResult(
                    success=False,
                    task_id=task_id,
                    error=error_msg
                )

            # Wait before next poll
            await asyncio.sleep(poll_interval)

        # Timeout
        return MidjourneyResult(
            success=False,
            task_id=task_id,
            error=f"Generation timed out after {max_wait} seconds"
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type(httpx.RequestError)
    )
    async def _get_task_status(self, task_id: str) -> dict[str, Any]:
        """Get task status from API."""
        response = await self.http_client.get(
            f"{self.api_base}/task/{task_id}"
        )

        if response.status_code != 200:
            raise MidjourneyError(f"Failed to get task status: {response.status_code}")

        return response.json()

    def _parse_completed_task(self, status: dict[str, Any]) -> MidjourneyResult:
        """Parse completed task response."""
        # Get image URLs from response
        # Structure may vary by API provider
        image_url = status.get("image_url")
        image_urls = status.get("image_urls", [])

        if image_url and not image_urls:
            image_urls = [image_url]

        if not image_urls:
            # Try alternative response structures
            result = status.get("result", {})
            if isinstance(result, dict):
                image_url = result.get("url") or result.get("image_url")
                if image_url:
                    image_urls = [image_url]

        return MidjourneyResult(
            success=len(image_urls) > 0,
            image_url=image_urls[0] if image_urls else None,
            image_urls=image_urls,
            task_id=status.get("task_id"),
            cost_estimate=self.COST_PER_IMAGE
        )

    async def upscale_image(self, task_id: str, index: int = 1) -> MidjourneyResult:
        """
        Upscale a specific image from the grid.

        Args:
            task_id: Original task ID
            index: Image index (1-4)

        Returns:
            MidjourneyResult with upscaled image URL
        """
        try:
            response = await self.http_client.post(
                f"{self.api_base}/upscale",
                json={
                    "task_id": task_id,
                    "index": index
                }
            )

            if response.status_code != 200:
                raise MidjourneyError(f"Upscale failed: {response.status_code}")

            data = response.json()
            upscale_task_id = data.get("task_id")

            # Poll for upscale completion
            return await self._poll_for_result(upscale_task_id)

        except Exception as e:
            logger.error(f"Upscale failed: {e}", exc_info=True)
            return MidjourneyResult(
                success=False,
                error=str(e)
            )

    async def vary_image(self, task_id: str, index: int = 1, variation_type: str = "subtle") -> MidjourneyResult:
        """
        Create variation of a specific image.

        Args:
            task_id: Original task ID
            index: Image index (1-4)
            variation_type: Type of variation (subtle, strong)

        Returns:
            MidjourneyResult with variation image URLs
        """
        try:
            response = await self.http_client.post(
                f"{self.api_base}/variation",
                json={
                    "task_id": task_id,
                    "index": index,
                    "type": variation_type
                }
            )

            if response.status_code != 200:
                raise MidjourneyError(f"Variation failed: {response.status_code}")

            data = response.json()
            vary_task_id = data.get("task_id")

            return await self._poll_for_result(vary_task_id)

        except Exception as e:
            logger.error(f"Variation failed: {e}", exc_info=True)
            return MidjourneyResult(
                success=False,
                error=str(e)
            )

    async def close(self):
        """Close HTTP client."""
        await self.http_client.aclose()
        logger.info("Midjourney service closed")


# Convenience function
async def generate_midjourney_image(
    prompt: str,
    aspect_ratio: str = "1:1",
    quality: str = "standard",
    api_key: str = None
) -> MidjourneyResult:
    """Generate image using Midjourney."""
    service = MidjourneyService(api_key)
    try:
        return await service.generate_image(prompt, aspect_ratio, quality)
    finally:
        await service.close()
