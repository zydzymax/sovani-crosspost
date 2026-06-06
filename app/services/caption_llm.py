"""Caption LLM service for SalesWhisper Crosspost."""

import asyncio
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx

from ..core.config import settings
from ..core.logging import get_logger

logger = get_logger("services.caption_llm")


@dataclass
class PlatformInput:
    """Input data for caption generation for a specific platform."""

    platform: str
    content_text: str
    product_context: str | None = None
    media_type: str | None = None
    media_count: int = 0
    hashtags: list[str] = None
    call_to_action: str | None = None

    def __post_init__(self):
        if self.hashtags is None:
            self.hashtags = []


@dataclass
class CaptionOutput:
    """Generated caption output for a platform."""

    platform: str
    caption: str
    hashtags: list[str]
    character_count: int
    is_truncated: bool = False
    confidence_score: float = 1.0
    generation_time: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "caption": self.caption,
            "hashtags": self.hashtags,
            "character_count": self.character_count,
            "is_truncated": self.is_truncated,
            "confidence_score": self.confidence_score,
            "generation_time": self.generation_time,
        }


class LLMError(Exception):
    pass


class LLMProviderBase(ABC):
    @abstractmethod
    async def generate_text(self, prompt: str, max_tokens: int = 500) -> str:
        pass

    @abstractmethod
    def get_provider_name(self) -> str:
        pass


class OpenAIProvider(LLMProviderBase):
    def __init__(self, api_key: str, model: str = "gpt-4"):
        self.api_key = api_key
        self.model = model
        self.api_base = "https://api.openai.com/v1"
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0), headers={"Authorization": f"Bearer {api_key}"}
        )

    async def generate_text(self, prompt: str, max_tokens: int = 500) -> str:
        try:
            response = await self.http_client.post(
                f"{self.api_base}/chat/completions",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": "You are a professional copywriter for the SalesWhisper brand."},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": 0.7,
                },
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            raise LLMError(f"OpenAI generation failed: {e}")

    def get_provider_name(self) -> str:
        return "openai"


class MockProvider(LLMProviderBase):
    def __init__(self):
        self.response_templates = {
            "instagram": "{content} Новая коллекция SalesWhisper уже здесь! #SalesWhisper #Fashion #Style",
            "vk": "{content} Представляем новую коллекцию SalesWhisper. #SalesWhisper #Мода",
            "tiktok": "{content} Стиль SalesWhisper #saleswhisper #fashion #style",
            "youtube": "{content} Больше о SalesWhisper в нашем видео!",
            "telegram": "{content} SalesWhisper - стиль для тебя.",
        }

    async def generate_text(self, prompt: str, max_tokens: int = 500) -> str:
        await asyncio.sleep(0.1)
        platform = self._detect_platform(prompt)

        content_match = re.search(r'CONTENT TEXT: "(.*?)"', prompt, re.DOTALL) or re.search(
            r'ИСХОДНЫЙ ТЕКСТ:\s*"(.*?)"', prompt, re.DOTALL
        )
        content = content_match.group(1) if content_match else "New collection"

        if len(content) > 50:
            content = content[:50] + "..."

        template = self.response_templates.get(platform, self.response_templates["instagram"])
        return template.format(content=content)

    def _detect_platform(self, prompt: str) -> str:
        prompt_lower = prompt.lower()
        if "for vk" in prompt_lower or "vk" in prompt_lower:
            return "vk"
        if "for tiktok" in prompt_lower or "tiktok" in prompt_lower:
            return "tiktok"
        if "for youtube" in prompt_lower or "youtube" in prompt_lower:
            return "youtube"
        if "for telegram" in prompt_lower or "telegram" in prompt_lower:
            return "telegram"
        return "instagram"

    def get_provider_name(self) -> str:
        return "mock"


class CaptionLLMService:
    def __init__(self):
        self.provider = self._create_provider()

        self.platform_configs = {
            "instagram": {"max_length": 2200, "hashtag_limit": 30},
            "vk": {"max_length": 15000, "hashtag_limit": 10},
            "tiktok": {"max_length": 150, "hashtag_limit": 5},
            "youtube": {"max_length": 5000, "hashtag_limit": 15},
            "telegram": {"max_length": 4096, "hashtag_limit": 10},
        }

        self.brand_guidelines = {
            "brand_name": "SalesWhisper",
            "brand_voice": "Elegant, stylish",
            "target_audience": "Women 25-45 years old",
            "prohibited_words": ["cheap", "budget"],
        }

    def _create_provider(self) -> LLMProviderBase:
        provider_name = getattr(settings, "llm_provider", "mock")

        if provider_name == "openai":
            api_key = getattr(settings, "openai_api_key", "")
            if hasattr(api_key, "get_secret_value"):
                api_key = api_key.get_secret_value()
            if api_key:
                return OpenAIProvider(api_key)

        return MockProvider()

    async def generate_all(self, platform_inputs: dict[str, PlatformInput]) -> dict[str, CaptionOutput]:
        start_time = time.time()

        logger.info("Starting caption generation", platforms=list(platform_inputs.keys()))

        results = {}

        for platform, platform_input in platform_inputs.items():
            try:
                result = await self._generate_single_caption(platform, platform_input)
                results[platform] = result
            except Exception as e:
                logger.error("Caption generation failed", platform=platform, error=str(e))
                results[platform] = self._create_fallback_output(platform, platform_input)

        total_time = time.time() - start_time
        logger.info("Caption generation completed", total_time=total_time)

        return results

    async def _generate_single_caption(self, platform: str, platform_input: PlatformInput) -> CaptionOutput:
        start_time = time.time()

        config = self.platform_configs.get(platform, self.platform_configs["instagram"])
        prompt = self._build_prompt(platform, platform_input, config)
        max_tokens = min(500, config["max_length"] // 2)

        generated_text = await self.provider.generate_text(prompt, max_tokens)
        caption, hashtags = self._parse_llm_response(generated_text, platform_input.hashtags)
        validated_caption, is_truncated = self._validate_caption_length(caption, config["max_length"])

        generation_time = time.time() - start_time

        return CaptionOutput(
            platform=platform,
            caption=validated_caption,
            hashtags=hashtags[: config["hashtag_limit"]],
            character_count=len(validated_caption),
            is_truncated=is_truncated,
            confidence_score=0.9,
            generation_time=generation_time,
        )

    def _build_prompt(self, platform: str, platform_input: PlatformInput, config: dict[str, Any]) -> str:
        prompt_parts = [
            f"Create an engaging caption for {platform.upper()}.",
            "",
            f"Brand: {self.brand_guidelines['brand_name']}",
            f"Brand voice: {self.brand_guidelines['brand_voice']}",
            f"Target audience: {self.brand_guidelines['target_audience']}",
            "",
            f'CONTENT TEXT: "{platform_input.content_text}"',
        ]

        if platform_input.product_context:
            prompt_parts.extend(["", "Product context:", platform_input.product_context])

        platform_guidelines = {
            "instagram": [
                "- Use emojis moderately",
                "- Add a call to action",
                f"- Максимум {config['max_length']} символов",
            ],
            "vk": ["- Minimal emojis", "- Direct call to action", f"- Максимум {config['max_length']} символов"],
            "tiktok": [
                "- Use trending hashtags and emojis",
                "- Energetic tone",
                f"- Максимум {config['max_length']} символов",
            ],
        }

        guidelines = platform_guidelines.get(platform, platform_guidelines["instagram"])
        prompt_parts.extend(["", "Platform guidelines:", *guidelines])

        if platform_input.hashtags:
            prompt_parts.extend(["", f"Include hashtags: {' '.join(platform_input.hashtags)}"])

        return "\n".join(prompt_parts)

    def _parse_llm_response(self, generated_text: str, existing_hashtags: list[str]) -> tuple[str, list[str]]:
        hashtags = set(existing_hashtags)
        hashtags.update(re.findall(r"#[\w]+", generated_text))
        return generated_text.strip(), list(hashtags)

    def _validate_caption_length(self, caption: str, max_length: int) -> tuple[str, bool]:
        if len(caption) <= max_length:
            return caption, False

        if max_length <= 3:
            return caption[:max_length], True

        truncated = caption[: max_length - 3]
        last_space = truncated.rfind(" ")
        if last_space > max_length * 0.6:
            truncated = truncated[:last_space]
        truncated = truncated.rstrip() + "..."

        return truncated, True

    def _create_fallback_caption(self, platform: str, platform_input: PlatformInput) -> str:
        fallback_templates = {
            "instagram": "{content}\n\n#SalesWhisper #Fashion",
            "vk": "{content}\n\n#SalesWhisper #Fashion",
            "tiktok": "{content} #SalesWhisper",
            "youtube": "{content}\n\nSubscribe to the channel!",
            "telegram": "{content}\n\nSalesWhisper - your style!",
        }

        template = fallback_templates.get(platform, fallback_templates["instagram"])
        content = platform_input.content_text[:100]
        return template.format(content=content)

    def _create_fallback_output(self, platform: str, platform_input: PlatformInput) -> CaptionOutput:
        return CaptionOutput(
            platform=platform,
            caption=self._create_fallback_caption(platform, platform_input),
            hashtags=platform_input.hashtags[:5],
            character_count=0,
            confidence_score=0.3,
        )


# Global service instance
caption_service = CaptionLLMService()


# Convenience functions
async def generate_all_captions(platform_inputs: dict[str, PlatformInput]) -> dict[str, CaptionOutput]:
    result = caption_service.generate_all(platform_inputs)
    if asyncio.iscoroutine(result):
        return await result
    return result
