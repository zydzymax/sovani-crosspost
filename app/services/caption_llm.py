"""Caption LLM service for SoVAni Crosspost."""

import asyncio
import json
import re
import time
from typing import Dict, Any, List, Optional
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

import httpx

from ..core.config import settings
from ..core.logging import get_logger, with_logging_context
from ..core.security import SecurityUtils
from ..observability.metrics import metrics

logger = get_logger("services.caption_llm")

@dataclass
class PlatformInput:
    """Input data for caption generation for a specific platform."""
    platform: str
    content_text: str
    product_context: Optional[str] = None
    media_type: Optional[str] = None
    media_count: int = 0
    hashtags: List[str] = None
    call_to_action: Optional[str] = None
    
    def __post_init__(self):
        if self.hashtags is None:
            self.hashtags = []

@dataclass
class CaptionOutput:
    """Generated caption output for a platform."""
    platform: str
    caption: str
    hashtags: List[str]
    character_count: int
    is_truncated: bool = False
    confidence_score: float = 1.0
    generation_time: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "platform": self.platform,
            "caption": self.caption,
            "hashtags": self.hashtags,
            "character_count": self.character_count,
            "is_truncated": self.is_truncated,
            "confidence_score": self.confidence_score,
            "generation_time": self.generation_time
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
            timeout=httpx.Timeout(30.0),
            headers={"Authorization": f"Bearer {api_key}"}
        )
    
    async def generate_text(self, prompt: str, max_tokens: int = 500) -> str:
        try:
            response = await self.http_client.post(
                f"{self.api_base}/chat/completions",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": ""K ?@>D5AA8>=0;L=K9 :>?8@09B5@ 4;O 1@5=40 SoVAni."},
                        {"role": "user", "content": prompt}
                    ],
                    "max_tokens": max_tokens,
                    "temperature": 0.7
                }
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
            "instagram": "{content} New collection SoVAni is here! #SoVAni #Fashion #Style",
            "vk": "{content} Presenting the new SoVAni collection. #SoVAni #Fashion",
            "tiktok": "{content} SoVAni style #sovani #fashion #style",
            "youtube": "{content} More about SoVAni in our video!",
            "telegram": "{content} SoVAni - style for you."
        } >20O :>;;5:F8O SoVAni C65 745AL! =� #SoVAni #Fashion #Style",
            "vk": "{content} @54AB02;O5< =>2CN :>;;5:F8N SoVAni. #SoVAni #>40",
            "tiktok": "{content} SoVAni style ( #sovani #fashion #style",
            "youtube": "{content} >4@>1=55 > SoVAni A<>B@8B5 2 =0H5< 2845>!",
            "telegram": "{content} < SoVAni - AB8;L 4;O 20A."
        }
    
    async def generate_text(self, prompt: str, max_tokens: int = 500) -> str:
        await asyncio.sleep(0.1)
        
        platform = "instagram"
        if "for VK" in prompt or "vk" in prompt.lower():
            platform = "vk"
        elif "for TikTok" in prompt or "tiktok" in prompt.lower():
            platform = "tiktok"
        elif "for YouTube" in prompt or "youtube" in prompt.lower():
            platform = "youtube"
        elif "for Telegram" in prompt or "telegram" in prompt.lower():
            platform = "telegram"
        
        content_match = re.search(r'!%+ "!": "(.*?)"', prompt)
        content = content_match.group(1) if content_match else ">20O :>;;5:F8O"
        
        if len(content) > 50:
            content = content[:50] + "..."
        
        template = self.response_templates.get(platform, self.response_templates["instagram"])
        return template.format(content=content)
    
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
            "telegram": {"max_length": 4096, "hashtag_limit": 10}
        }
        
        self.brand_guidelines = {
            "brand_name": "SoVAni",
            "brand_voice": "M;530=B=K9, AB8;L=K9",
            "target_audience": "65=I8=K 25-45 ;5B",
            "prohibited_words": ["45H52K9", "1N465B=K9"]
        }
    
    def _create_provider(self) -> LLMProviderBase:
        provider_name = getattr(settings, 'llm_provider', 'mock')
        
        if provider_name == "openai":
            api_key = getattr(settings, 'openai_api_key', '')
            if hasattr(api_key, 'get_secret_value'):
                api_key = api_key.get_secret_value()
            if api_key:
                return OpenAIProvider(api_key)
        
        return MockProvider()
    
    async def generate_all(self, platform_inputs: Dict[str, PlatformInput]) -> Dict[str, CaptionOutput]:
        start_time = time.time()
        
        logger.info("Starting caption generation", platforms=list(platform_inputs.keys()))
        
        results = {}
        
        for platform, platform_input in platform_inputs.items():
            try:
                result = await self._generate_single_caption(platform, platform_input)
                results[platform] = result
            except Exception as e:
                logger.error(f"Caption generation failed for {platform}", error=str(e))
                results[platform] = CaptionOutput(
                    platform=platform,
                    caption=self._create_fallback_caption(platform, platform_input),
                    hashtags=platform_input.hashtags[:5],
                    character_count=0,
                    confidence_score=0.3
                )
        
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
            hashtags=hashtags[:config["hashtag_limit"]],
            character_count=len(validated_caption),
            is_truncated=is_truncated,
            confidence_score=0.9,
            generation_time=generation_time
        )
    
    def _build_prompt(self, platform: str, platform_input: PlatformInput, config: Dict[str, Any]) -> str:
        prompt_parts = [
            f"!>7409 ?@82;5:0B5;L=CN ?>4?8AL 4;O {platform.upper()}.",
            "",
            f" : {self.brand_guidelines['brand_name']}",
            f"!  : {self.brand_guidelines['brand_voice']}",
            f"#" /: {self.brand_guidelines['target_audience']}",
            "",
            f'!%+ "!": "{platform_input.content_text}"'
        ]
        
        if platform_input.product_context:
            prompt_parts.extend(["", ""!" " :", platform_input.product_context])
        
        platform_guidelines = {
            "instagram": [
                "- A?>;L7C9 M<>468 C<5@5==>",
                "- >102L ?@87K2 : 459AB28N",
                f"- 0:A8<C< {config['max_length']} A8<2>;>2"
            ],
            "vk": [
                "- 8=8<C< M<>468",
                "- @O<>9 ?@87K2 : 459AB28N",
                f"- 0:A8<C< {config['max_length']} A8<2>;>2"
            ],
            "tiktok": [
                "- =>3> M<>468 8 B@5=4>2KE EMHB53>2",
                "- -=5@38G=K9 B>=",
                f"- 0:A8<C< {config['max_length']} A8<2>;>2"
            ]
        }
        
        guidelines = platform_guidelines.get(platform, platform_guidelines["instagram"])
        prompt_parts.extend(["", f"" /:", *guidelines])
        
        if platform_input.hashtags:
            prompt_parts.extend(["", f"/",+ %-(": {' '.join(platform_input.hashtags)}"])
        
        return "\n".join(prompt_parts)
    
    def _parse_llm_response(self, generated_text: str, existing_hashtags: List[str]) -> tuple[str, List[str]]:
        hashtags = set(existing_hashtags)
        hashtags.update(re.findall(r'#[\w0-O-O]+', generated_text))
        return generated_text.strip(), list(hashtags)
    
    def _validate_caption_length(self, caption: str, max_length: int) -> tuple[str, bool]:
        if len(caption) <= max_length:
            return caption, False
        
        truncated = caption[:max_length]
        last_space = truncated.rfind(' ')
        if last_space > max_length * 0.9:
            truncated = truncated[:last_space] + "..."
        
        return truncated, True
    
    def _create_fallback_caption(self, platform: str, platform_input: PlatformInput) -> str:
        fallback_templates = {
            "instagram": "( {content}\n\n#SoVAni #Fashion",
            "vk": "{content}\n\n#SoVAni #>40",
            "tiktok": "{content} ( #SoVAni",
            "youtube": "{content}\n\n>4?8AK209B5AL =0 :0=0;!",
            "telegram": "< {content}\n\nSoVAni - 20H AB8;L!"
        }
        
        template = fallback_templates.get(platform, fallback_templates["instagram"])
        content = platform_input.content_text[:100]
        return template.format(content=content)

# Global service instance
caption_service = CaptionLLMService()

# Convenience functions
async def generate_all_captions(platform_inputs: Dict[str, PlatformInput]) -> Dict[str, CaptionOutput]:
    return await caption_service.generate_all(platform_inputs)