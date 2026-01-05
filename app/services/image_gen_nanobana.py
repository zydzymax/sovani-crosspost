"""Nanobana image generation service for Crosspost.

Uses nanobananaapi.ai - third-party API for Google Gemini Image models.
Nano Banana = Gemini 2.5 Flash Image (fast, cheap)
Nano Banana Pro = Gemini 3 Pro Image (high quality)
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

logger = get_logger("services.image_gen_nanobana")


class NanobanaError(Exception):
    """Base exception for Nanobana API errors."""

    pass


class NanobanaRateLimitError(NanobanaError):
    """Rate limit exceeded."""

    pass


class NanobanaGenerationError(NanobanaError):
    """Image generation failed."""

    pass


class ModelType(str, Enum):
    """Nanobana model types."""

    FLASH = "flash"  # Nano Banana (Gemini 2.5 Flash) - fast & cheap
    PRO = "pro"  # Nano Banana Pro (Gemini 3 Pro) - high quality


class Resolution(str, Enum):
    """Nanobana Pro resolutions."""

    RES_1K = "1K"
    RES_2K = "2K"
    RES_4K = "4K"


class AspectRatio(str, Enum):
    """Nanobana aspect ratios."""

    SQUARE = "1:1"
    LANDSCAPE = "16:9"
    PORTRAIT = "9:16"
    WIDE = "4:3"
    TALL = "3:4"
    PHOTO = "3:2"
    PHOTO_PORTRAIT = "2:3"
    SOCIAL = "4:5"
    SOCIAL_WIDE = "5:4"
    CINEMA = "21:9"
    AUTO = "auto"


@dataclass
class NanobanaResult:
    """Result of Nanobana image generation."""

    success: bool
    image_url: str | None = None
    image_base64: str | None = None
    task_id: str | None = None
    error: str | None = None
    cost_estimate: float = 0.02  # default for Flash


class NanobanaService:
    """Nanobana image generation service via nanobananaapi.ai."""

    # API base URL (note: api. subdomain)
    API_BASE = "https://api.nanobananaapi.ai"

    # Cost per image
    COST_FLASH = 0.02  # ~$0.02 per image
    COST_PRO_1K = 0.12  # ~$0.12 per 1K/2K image
    COST_PRO_4K = 0.24  # ~$0.24 per 4K image

    def __init__(self, api_key: str = None):
        """Initialize Nanobana service."""
        self.api_key = api_key or self._get_api_key()

        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(180.0),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )

        logger.info("Nanobana service initialized (api.nanobananaapi.ai)")

    def _get_api_key(self) -> str:
        """Get API key from settings or environment."""
        if hasattr(settings, "nanobana") and hasattr(settings.nanobana, "api_key"):
            key = settings.nanobana.api_key
            if hasattr(key, "get_secret_value"):
                return key.get_secret_value()
            return str(key)

        import os

        key = os.getenv("NANOBANA_API_KEY")
        if key:
            return key

        raise NanobanaError("Nanobana API key not configured. Set NANOBANA_API_KEY.")

    async def generate_image(
        self,
        prompt: str,
        model: ModelType = ModelType.PRO,
        resolution: Resolution = Resolution.RES_1K,
        aspect_ratio: AspectRatio = AspectRatio.SQUARE,
        reference_image_urls: list[str] = None,
    ) -> NanobanaResult:
        """
        Generate image using Nanobana.

        Args:
            prompt: Text description for image generation
            model: FLASH (fast/cheap) or PRO (high quality)
            resolution: 1K, 2K, or 4K (Pro only)
            aspect_ratio: Output aspect ratio
            reference_image_urls: Optional reference images (max 8)

        Returns:
            NanobanaResult with image URL
        """
        start_time = time.time()

        logger.info(
            "Starting Nanobana generation", prompt_length=len(prompt), model=model.value, resolution=resolution.value
        )

        try:
            if model == ModelType.PRO:
                result = await self._generate_pro(prompt, resolution, aspect_ratio, reference_image_urls)
            else:
                result = await self._generate_flash(prompt, aspect_ratio)

            processing_time = time.time() - start_time

            logger.info(
                "Nanobana generation completed",
                processing_time=processing_time,
                success=result.success,
                model=model.value,
            )

            return result

        except Exception as e:
            logger.error(f"Nanobana generation failed: {e}", exc_info=True)
            return NanobanaResult(success=False, error=str(e))

    async def _generate_pro(
        self, prompt: str, resolution: Resolution, aspect_ratio: AspectRatio, reference_image_urls: list[str] = None
    ) -> NanobanaResult:
        """Generate using Nano Banana Pro (Gemini 3 Pro Image)."""
        endpoint = f"{self.API_BASE}/api/v1/nanobanana/generate-pro"

        # Calculate cost
        cost = self.COST_PRO_4K if resolution == Resolution.RES_4K else self.COST_PRO_1K

        request_data = {"prompt": prompt, "resolution": resolution.value, "aspectRatio": aspect_ratio.value}

        if reference_image_urls:
            request_data["imageUrls"] = reference_image_urls[:8]

        return await self._make_request(endpoint, request_data, cost)

    async def _generate_flash(self, prompt: str, aspect_ratio: AspectRatio) -> NanobanaResult:
        """Generate using Nano Banana Flash (Gemini 2.5 Flash Image)."""
        endpoint = f"{self.API_BASE}/api/v1/nanobanana/generate"

        # Flash requires type and callBackUrl
        request_data = {
            "prompt": prompt,
            "type": "TEXTTOIMAGE",
            "image_size": aspect_ratio.value,
            "callBackUrl": "https://crosspost.saleswhisper.pro/webhooks/nanobana",
        }

        return await self._make_request(endpoint, request_data, self.COST_FLASH)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.RequestError, NanobanaRateLimitError)),
    )
    async def _make_request(self, endpoint: str, request_data: dict[str, Any], cost: float) -> NanobanaResult:
        """Make generation request to Nanobana API."""
        response = await self.http_client.post(endpoint, json=request_data)

        if response.status_code == 429:
            raise NanobanaRateLimitError("Rate limit exceeded")

        if response.status_code == 401:
            raise NanobanaError("Invalid API key")

        if response.status_code == 402:
            raise NanobanaError("Insufficient credits")

        data = response.json()

        if data.get("code") != 200:
            error_msg = data.get("msg") or data.get("message") or "Unknown error"
            raise NanobanaError(f"Generation failed: {error_msg}")

        # Get task ID and poll for result
        task_id = data.get("data", {}).get("taskId")
        if task_id:
            return await self._poll_for_result(task_id, cost)

        return NanobanaResult(success=False, error="No taskId in response")

    async def _poll_for_result(self, task_id: str, cost: float, max_wait: int = 180) -> NanobanaResult:
        """Poll for async task completion."""
        start_time = time.time()
        poll_interval = 3  # seconds

        while time.time() - start_time < max_wait:
            await asyncio.sleep(poll_interval)

            status = await self._get_task_status(task_id)

            if status.get("code") != 200:
                continue

            task_data = status.get("data", {})
            success_flag = task_data.get("successFlag")

            if success_flag == 1:
                # Success - extract image URL
                response_data = task_data.get("response", {})
                image_url = response_data.get("resultImageUrl")

                if image_url:
                    return NanobanaResult(success=True, image_url=image_url, task_id=task_id, cost_estimate=cost)

            if success_flag == 0 or task_data.get("errorCode"):
                error_msg = task_data.get("errorMessage") or "Generation failed"
                return NanobanaResult(success=False, task_id=task_id, error=error_msg)

            # Still processing, continue polling
            poll_interval = min(poll_interval + 1, 10)

        return NanobanaResult(success=False, task_id=task_id, error=f"Generation timed out after {max_wait} seconds")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type(httpx.RequestError),
    )
    async def _get_task_status(self, task_id: str) -> dict[str, Any]:
        """Get task status from API."""
        response = await self.http_client.get(
            f"{self.API_BASE}/api/v1/nanobanana/record-info", params={"taskId": task_id}
        )

        return response.json()

    async def get_credits(self) -> dict[str, Any]:
        """Get account credits balance."""
        try:
            response = await self.http_client.get(f"{self.API_BASE}/api/v1/common/credit")

            if response.status_code == 200:
                return response.json()

            return {}

        except Exception as e:
            logger.warning(f"Failed to get credits: {e}")
            return {}

    async def close(self):
        """Close HTTP client."""
        await self.http_client.aclose()
        logger.info("Nanobana service closed")


# Convenience function
async def generate_nanobana_image(
    prompt: str, model: str = "pro", resolution: str = "1K", aspect_ratio: str = "1:1", api_key: str = None
) -> NanobanaResult:
    """Generate image using Nanobana.

    Args:
        prompt: Image description
        model: "flash" (fast/cheap) or "pro" (high quality)
        resolution: "1K", "2K", or "4K" (Pro only)
        aspect_ratio: "1:1", "16:9", "9:16", "4:3", "3:4", etc.
        api_key: Optional API key override
    """
    service = NanobanaService(api_key)
    try:
        model_type = ModelType.PRO if model == "pro" else ModelType.FLASH
        res = Resolution(resolution) if resolution in [r.value for r in Resolution] else Resolution.RES_1K
        ratio = AspectRatio(aspect_ratio) if aspect_ratio in [r.value for r in AspectRatio] else AspectRatio.SQUARE

        return await service.generate_image(prompt, model_type, res, ratio)
    finally:
        await service.close()
