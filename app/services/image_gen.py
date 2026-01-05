"""
Image generation service for SalesWhisper Crosspost.
Supports multiple providers: OpenAI DALL-E, Stability AI, Flux, Nanobana.
"""

import asyncio
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from ..core.config import settings
from ..core.logging import get_logger

logger = get_logger("services.image_gen")


@dataclass
class ImageResult:
    """Result of image generation."""
    success: bool
    image_url: str | None = None
    image_base64: str | None = None
    error: str | None = None
    provider: str | None = None
    cost_estimate: float = 0.0


class ImageProvider(ABC):
    """Abstract base class for image generation providers."""

    @abstractmethod
    async def generate(self, prompt: str, size: str = "1024x1024") -> ImageResult:
        pass

    @abstractmethod
    def get_name(self) -> str:
        pass


class OpenAIProvider(ImageProvider):
    """OpenAI DALL-E 3 provider."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.AsyncClient(
            timeout=60.0,
            headers={"Authorization": f"Bearer {api_key}"}
        )

    async def generate(self, prompt: str, size: str = "1024x1024") -> ImageResult:
        try:
            response = await self.client.post(
                "https://api.openai.com/v1/images/generations",
                json={
                    "model": "dall-e-3",
                    "prompt": prompt,
                    "n": 1,
                    "size": size,
                    "quality": "standard",
                }
            )
            response.raise_for_status()
            data = response.json()

            return ImageResult(
                success=True,
                image_url=data["data"][0]["url"],
                provider="openai",
                cost_estimate=0.04 if size == "1024x1024" else 0.08,
            )
        except Exception as e:
            logger.error(f"OpenAI image generation failed: {e}")
            return ImageResult(success=False, error=str(e), provider="openai")

    def get_name(self) -> str:
        return "openai"


class StabilityProvider(ImageProvider):
    """Stability AI provider."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.AsyncClient(
            timeout=60.0,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            }
        )

    async def generate(self, prompt: str, size: str = "1024x1024") -> ImageResult:
        try:
            width, height = map(int, size.split("x"))

            response = await self.client.post(
                "https://api.stability.ai/v1/generation/stable-diffusion-xl-1024-v1-0/text-to-image",
                json={
                    "text_prompts": [{"text": prompt}],
                    "cfg_scale": 7,
                    "width": width,
                    "height": height,
                    "samples": 1,
                    "steps": 30,
                }
            )
            response.raise_for_status()
            data = response.json()

            image_base64 = data["artifacts"][0]["base64"]

            return ImageResult(
                success=True,
                image_base64=image_base64,
                provider="stability",
                cost_estimate=0.02,
            )
        except Exception as e:
            logger.error(f"Stability AI image generation failed: {e}")
            return ImageResult(success=False, error=str(e), provider="stability")

    def get_name(self) -> str:
        return "stability"


class FluxProvider(ImageProvider):
    """Flux image generation provider."""

    def __init__(self, api_key: str, api_url: str = None):
        self.api_key = api_key
        self.api_url = api_url or "https://api.flux.ai/v1/generate"
        self.client = httpx.AsyncClient(
            timeout=60.0,
            headers={"Authorization": f"Bearer {api_key}"}
        )

    async def generate(self, prompt: str, size: str = "1024x1024") -> ImageResult:
        try:
            response = await self.client.post(
                self.api_url,
                json={
                    "prompt": prompt,
                    "size": size,
                }
            )
            response.raise_for_status()
            data = response.json()

            return ImageResult(
                success=True,
                image_url=data.get("url"),
                image_base64=data.get("base64"),
                provider="flux",
                cost_estimate=0.01,
            )
        except Exception as e:
            logger.error(f"Flux image generation failed: {e}")
            return ImageResult(success=False, error=str(e), provider="flux")

    def get_name(self) -> str:
        return "flux"


class NanobanaProvider(ImageProvider):
    """Nanobana (Google Gemini Image) provider via api.nanobananaapi.ai.

    Models:
    - flash: Gemini 2.5 Flash Image (fast, ~$0.02/image) - requires callback
    - pro: Gemini 3 Pro Image (high quality, ~$0.12/image) - recommended
    """

    API_BASE = "https://api.nanobananaapi.ai"

    def __init__(self, api_key: str, model: str = "pro"):
        self.api_key = api_key
        self.model = model  # "flash" or "pro"
        self.client = httpx.AsyncClient(
            timeout=180.0,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
        )

    def _size_to_aspect(self, size: str) -> str:
        """Convert size like 1024x1024 to aspect ratio."""
        try:
            w, h = map(int, size.split("x"))
            ratio = w / h
            if ratio > 1.7:
                return "16:9"
            elif ratio < 0.6:
                return "9:16"
            elif ratio > 1.2:
                return "4:3"
            elif ratio < 0.83:
                return "3:4"
            else:
                return "1:1"
        except:
            return "1:1"

    async def generate(self, prompt: str, size: str = "1024x1024") -> ImageResult:
        try:
            # Pro endpoint (recommended - no callback needed)
            endpoint = f"{self.API_BASE}/api/v1/nanobanana/generate-pro"
            aspect_ratio = self._size_to_aspect(size)

            response = await self.client.post(
                endpoint,
                json={
                    "prompt": prompt,
                    "resolution": "1K",
                    "aspectRatio": aspect_ratio
                }
            )

            data = response.json()

            if data.get("code") == 429:
                return ImageResult(success=False, error="Rate limit exceeded", provider="nanobana")

            if data.get("code") == 401:
                return ImageResult(success=False, error="Invalid API key", provider="nanobana")

            if data.get("code") == 402:
                return ImageResult(success=False, error="Insufficient credits", provider="nanobana")

            if data.get("code") != 200:
                error_msg = data.get("msg") or "Unknown error"
                return ImageResult(success=False, error=error_msg, provider="nanobana")

            # Get task ID and poll for result
            task_id = data.get("data", {}).get("taskId")
            if task_id:
                return await self._poll_for_result(task_id)

            return ImageResult(success=False, error="No taskId in response", provider="nanobana")

        except Exception as e:
            logger.error(f"Nanobana image generation failed: {e}")
            return ImageResult(success=False, error=str(e), provider="nanobana")

    async def _poll_for_result(self, task_id: str, max_wait: int = 180) -> ImageResult:
        """Poll for async task completion."""
        import time
        start_time = time.time()
        poll_interval = 3

        while time.time() - start_time < max_wait:
            await asyncio.sleep(poll_interval)

            try:
                response = await self.client.get(
                    f"{self.API_BASE}/api/v1/nanobanana/record-info",
                    params={"taskId": task_id}
                )

                data = response.json()

                if data.get("code") != 200:
                    continue

                task_data = data.get("data", {})
                success_flag = task_data.get("successFlag")

                if success_flag == 1:
                    response_data = task_data.get("response", {})
                    image_url = response_data.get("resultImageUrl")
                    if image_url:
                        return ImageResult(
                            success=True,
                            image_url=image_url,
                            provider="nanobana",
                            cost_estimate=0.12,
                        )

                if success_flag == 0 or task_data.get("errorCode"):
                    return ImageResult(
                        success=False,
                        error=task_data.get("errorMessage", "Generation failed"),
                        provider="nanobana"
                    )

            except Exception as e:
                logger.warning(f"Poll error: {e}")

            poll_interval = min(poll_interval + 1, 10)

        return ImageResult(success=False, error="Generation timed out", provider="nanobana")

    def get_name(self) -> str:
        return "nanobana"


class ImageGenerationService:
    """Main service for image generation with provider selection."""

    def __init__(self):
        self.providers = {}

        # Initialize available providers
        openai_key = getattr(settings, 'OPENAI_API_KEY', None) or os.getenv('OPENAI_API_KEY')
        if openai_key:
            self.providers["openai"] = OpenAIProvider(openai_key)

        stability_key = getattr(settings, 'STABILITY_API_KEY', None) or os.getenv('STABILITY_API_KEY')
        if stability_key:
            self.providers["stability"] = StabilityProvider(stability_key)

        flux_key = getattr(settings, 'FLUX_API_KEY', None) or os.getenv('FLUX_API_KEY')
        if flux_key:
            self.providers["flux"] = FluxProvider(
                flux_key,
                getattr(settings, 'FLUX_API_URL', None)
            )

        # Nanobana (Google Gemini Image via api.nanobananaapi.ai)
        nanobana_key = getattr(settings, 'NANOBANA_API_KEY', None) or os.getenv('NANOBANA_API_KEY')
        if nanobana_key:
            self.providers["nanobana"] = NanobanaProvider(nanobana_key, model="pro")
            self.providers["nanobana-pro"] = NanobanaProvider(nanobana_key, model="pro")
            logger.info("Nanobana provider initialized (Pro model)")

    async def generate(
        self,
        prompt: str,
        provider: str = "openai",
        size: str = "1024x1024"
    ) -> ImageResult:
        """
        Generate an image using the specified provider.

        Args:
            prompt: Text description of the image
            provider: Provider name (openai, stability, flux, nanobana)
            size: Image size (e.g., "1024x1024")

        Returns:
            ImageResult with the generated image or error
        """
        if provider not in self.providers:
            # Fallback to first available provider
            if self.providers:
                provider = next(iter(self.providers))
                logger.warning(f"Requested provider not available, using {provider}")
            else:
                return ImageResult(
                    success=False,
                    error="No image generation providers configured"
                )

        logger.info(f"Generating image with {provider}: {prompt[:50]}...")
        return await self.providers[provider].generate(prompt, size)

    def get_available_providers(self) -> list:
        """Get list of available provider names."""
        return list(self.providers.keys())


# Singleton instance
image_service = ImageGenerationService()
