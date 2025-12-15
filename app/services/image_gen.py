"""
Image generation service for SoVAni Crosspost.
Supports multiple providers: OpenAI DALL-E, Stability AI, Flux.
"""

import asyncio
import base64
import httpx
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
from enum import Enum

from ..core.config import settings
from ..core.logging import get_logger

logger = get_logger("services.image_gen")


@dataclass
class ImageResult:
    """Result of image generation."""
    success: bool
    image_url: Optional[str] = None
    image_base64: Optional[str] = None
    error: Optional[str] = None
    provider: Optional[str] = None
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
            
            # Stability returns base64 encoded images
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
    """Flux image generation provider (placeholder for custom API)."""
    
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


class ImageGenerationService:
    """Main service for image generation with provider selection."""
    
    def __init__(self):
        self.providers = {}
        
        # Initialize available providers
        if hasattr(settings, 'OPENAI_API_KEY') and settings.OPENAI_API_KEY:
            self.providers["openai"] = OpenAIProvider(settings.OPENAI_API_KEY)
        
        if hasattr(settings, 'STABILITY_API_KEY') and settings.STABILITY_API_KEY:
            self.providers["stability"] = StabilityProvider(settings.STABILITY_API_KEY)
        
        if hasattr(settings, 'FLUX_API_KEY') and settings.FLUX_API_KEY:
            self.providers["flux"] = FluxProvider(
                settings.FLUX_API_KEY,
                getattr(settings, 'FLUX_API_URL', None)
            )
    
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
            provider: Provider name (openai, stability, flux)
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
