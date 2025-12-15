"""Nanobana image generation service for Crosspost.

Nanobana is a Russian AI image generation service.
"""

import asyncio
import time
from typing import Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

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
    STANDARD = "standard"
    ARTISTIC = "artistic"
    REALISTIC = "realistic"
    ANIME = "anime"


class ImageSize(str, Enum):
    """Nanobana image sizes."""
    SMALL = "512x512"
    MEDIUM = "768x768"
    LARGE = "1024x1024"
    WIDE = "1024x576"
    TALL = "576x1024"


@dataclass
class NanobanaResult:
    """Result of Nanobana image generation."""
    success: bool
    image_url: Optional[str] = None
    image_base64: Optional[str] = None
    task_id: Optional[str] = None
    error: Optional[str] = None
    cost_estimate: float = 0.01  # ~$0.01 per generation (budget option)


class NanobanaService:
    """Nanobana image generation service."""

    # API endpoint
    API_BASE = "https://api.nanobana.ru/v1"

    # Cost per image generation (budget provider)
    COST_PER_IMAGE = 0.01

    def __init__(self, api_key: str = None, api_url: str = None):
        """Initialize Nanobana service."""
        self.api_key = api_key or self._get_api_key()
        self.api_base = api_url or self._get_api_url()

        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
        )

        logger.info("Nanobana service initialized")

    def _get_api_key(self) -> str:
        """Get API key from settings or environment."""
        if hasattr(settings, 'nanobana') and hasattr(settings.nanobana, 'api_key'):
            key = settings.nanobana.api_key
            if hasattr(key, 'get_secret_value'):
                return key.get_secret_value()
            return str(key)

        import os
        key = os.getenv('NANOBANA_API_KEY')
        if key:
            return key

        raise NanobanaError("Nanobana API key not configured.")

    def _get_api_url(self) -> str:
        """Get API URL from settings or environment."""
        if hasattr(settings, 'nanobana') and hasattr(settings.nanobana, 'api_url'):
            return str(settings.nanobana.api_url)

        import os
        return os.getenv('NANOBANA_API_URL', self.API_BASE)

    async def generate_image(
        self,
        prompt: str,
        model: ModelType = ModelType.STANDARD,
        size: ImageSize = ImageSize.MEDIUM,
        negative_prompt: str = None,
        num_images: int = 1,
        guidance_scale: float = 7.5,
        seed: int = None
    ) -> NanobanaResult:
        """
        Generate image using Nanobana.

        Args:
            prompt: Text description for image generation (supports Russian)
            model: Model type to use
            size: Output image size
            negative_prompt: What to avoid in the image
            num_images: Number of images to generate (1-4)
            guidance_scale: How closely to follow the prompt (1-20)
            seed: Random seed for reproducibility

        Returns:
            NanobanaResult with image URL or base64
        """
        start_time = time.time()

        logger.info(
            "Starting Nanobana generation",
            prompt_length=len(prompt),
            model=model.value,
            size=size.value
        )

        try:
            # Parse size
            width, height = map(int, size.value.split('x'))

            # Submit generation request
            request_data = {
                "prompt": prompt,
                "model": model.value,
                "width": width,
                "height": height,
                "num_images": min(num_images, 4),
                "guidance_scale": guidance_scale
            }

            if negative_prompt:
                request_data["negative_prompt"] = negative_prompt

            if seed is not None:
                request_data["seed"] = seed

            # Make generation request
            result = await self._generate(request_data)

            processing_time = time.time() - start_time

            logger.info(
                "Nanobana generation completed",
                processing_time=processing_time,
                success=result.success
            )

            return result

        except Exception as e:
            logger.error(f"Nanobana generation failed: {e}", exc_info=True)
            return NanobanaResult(
                success=False,
                error=str(e)
            )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.RequestError, NanobanaRateLimitError))
    )
    async def _generate(self, request_data: Dict[str, Any]) -> NanobanaResult:
        """Make generation request to Nanobana API."""
        response = await self.http_client.post(
            f"{self.api_base}/generate",
            json=request_data
        )

        if response.status_code == 429:
            raise NanobanaRateLimitError("Rate limit exceeded")

        if response.status_code == 401:
            raise NanobanaError("Invalid API key")

        if response.status_code != 200:
            error_text = response.text
            try:
                error_data = response.json()
                error_text = error_data.get("error", {}).get("message", error_text)
            except:
                pass
            raise NanobanaError(f"Generation failed: {response.status_code} - {error_text}")

        data = response.json()

        # Handle async generation (polling)
        if data.get("status") == "processing":
            task_id = data.get("task_id")
            return await self._poll_for_result(task_id)

        # Handle sync response
        return self._parse_response(data)

    async def _poll_for_result(self, task_id: str, max_wait: int = 120) -> NanobanaResult:
        """Poll for async task completion."""
        start_time = time.time()
        poll_interval = 2  # seconds

        while time.time() - start_time < max_wait:
            status = await self._get_task_status(task_id)

            if status.get("status") == "completed":
                return self._parse_response(status)

            if status.get("status") == "failed":
                error_msg = status.get("error", "Unknown error")
                return NanobanaResult(
                    success=False,
                    task_id=task_id,
                    error=error_msg
                )

            await asyncio.sleep(poll_interval)

        return NanobanaResult(
            success=False,
            task_id=task_id,
            error=f"Generation timed out after {max_wait} seconds"
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type(httpx.RequestError)
    )
    async def _get_task_status(self, task_id: str) -> Dict[str, Any]:
        """Get task status from API."""
        response = await self.http_client.get(
            f"{self.api_base}/task/{task_id}"
        )

        if response.status_code != 200:
            raise NanobanaError(f"Failed to get task status: {response.status_code}")

        return response.json()

    def _parse_response(self, data: Dict[str, Any]) -> NanobanaResult:
        """Parse generation response."""
        # Try to get image URL
        image_url = data.get("image_url") or data.get("url")

        # Try to get images array
        images = data.get("images", [])
        if images and isinstance(images, list):
            first_image = images[0]
            if isinstance(first_image, dict):
                image_url = first_image.get("url")
            elif isinstance(first_image, str):
                if first_image.startswith("http"):
                    image_url = first_image
                else:
                    # Assume base64
                    return NanobanaResult(
                        success=True,
                        image_base64=first_image,
                        task_id=data.get("task_id"),
                        cost_estimate=self.COST_PER_IMAGE
                    )

        # Try to get base64 data
        image_base64 = data.get("image_base64") or data.get("base64")

        if image_url or image_base64:
            return NanobanaResult(
                success=True,
                image_url=image_url,
                image_base64=image_base64,
                task_id=data.get("task_id"),
                cost_estimate=self.COST_PER_IMAGE
            )

        return NanobanaResult(
            success=False,
            error="No image in response"
        )

    async def translate_prompt(self, prompt: str) -> str:
        """
        Translate Russian prompt to English for better results.

        Nanobana works best with English prompts, but accepts Russian.
        This optional method can improve results.
        """
        try:
            response = await self.http_client.post(
                f"{self.api_base}/translate",
                json={"text": prompt, "target": "en"}
            )

            if response.status_code == 200:
                data = response.json()
                return data.get("translated", prompt)

            return prompt

        except Exception as e:
            logger.warning(f"Translation failed, using original prompt: {e}")
            return prompt

    async def get_available_models(self) -> list:
        """Get list of available models."""
        try:
            response = await self.http_client.get(f"{self.api_base}/models")

            if response.status_code == 200:
                data = response.json()
                return data.get("models", [])

            return []

        except Exception as e:
            logger.warning(f"Failed to get models: {e}")
            return []

    async def get_account_info(self) -> Dict[str, Any]:
        """Get account information including balance."""
        try:
            response = await self.http_client.get(f"{self.api_base}/account")

            if response.status_code == 200:
                return response.json()

            return {}

        except Exception as e:
            logger.warning(f"Failed to get account info: {e}")
            return {}

    async def close(self):
        """Close HTTP client."""
        await self.http_client.aclose()
        logger.info("Nanobana service closed")


# Convenience function
async def generate_nanobana_image(
    prompt: str,
    model: str = "standard",
    size: str = "768x768",
    api_key: str = None
) -> NanobanaResult:
    """Generate image using Nanobana."""
    service = NanobanaService(api_key)
    try:
        model_type = ModelType(model) if model in [m.value for m in ModelType] else ModelType.STANDARD
        image_size = ImageSize(size) if size in [s.value for s in ImageSize] else ImageSize.MEDIUM

        return await service.generate_image(prompt, model_type, image_size)
    finally:
        await service.close()
