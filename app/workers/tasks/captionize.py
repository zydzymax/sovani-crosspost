"""Captionize stage tasks for SalesWhisper Crosspost."""

import time
from typing import Any

from ...core.config import settings
from ...core.logging import get_logger, with_logging_context
from ..celery_app import celery

logger = get_logger("tasks.captionize")

# All target platforms for cross-posting
TARGET_PLATFORMS = ["vk", "telegram", "instagram", "tiktok", "youtube", "dzen"]

# Platform-specific character limits
PLATFORM_LIMITS = {
    "vk": 16000,
    "telegram": 4096,
    "instagram": 2200,
    "tiktok": 2200,
    "youtube": 5000,
    "dzen": 10000,
}

PLATFORM_PROMPTS = {
    "vk": "для ВКонтакте: деловой но живой стиль, можно длиннее, упомяни ссылку",
    "telegram": "для Telegram-канала: короткий, ёмкий, с эмодзи, призыв перейти",
    "instagram": "для Instagram: вдохновляющий, с хэштегами, до 2200 символов",
    "tiktok": "для TikTok: дерзкий, молодёжный, с трендовыми хэштегами",
    "youtube": "для YouTube: описание видео/поста с ключевыми словами для SEO",
    "dzen": "для Дзена: информационный, структурированный, как статья",
}


def _generate_caption_llm(text: str, platform: str) -> str:
    """Generate caption using OpenAI API."""
    import httpx
    api_key = settings.app.__dict__.get("openai_api_key") or __import__("os").getenv("OPENAI_API_KEY", "")
    if not api_key:
        return _fallback_caption(text, platform)
    try:
        prompt = (
            f"Ты копирайтер бренда SalesWhisper (AI автоматизация продаж).\n"
            f"Адаптируй следующий текст {PLATFORM_PROMPTS[platform]}.\n"
            f"Лимит символов: {PLATFORM_LIMITS[platform]}.\n"
            f"Исходный текст:\n{text}\n\n"
            f"Верни только готовый текст поста без пояснений."
        )
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 800,
                    "temperature": 0.7,
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning("LLM caption failed, using fallback", platform=platform, error=str(e))
        return _fallback_caption(text, platform)


def _fallback_caption(text: str, platform: str) -> str:
    """Simple fallback caption without LLM."""
    tags = {
        "vk": "#SalesWhisper #продажи #автоматизация",
        "telegram": "📣 SalesWhisper",
        "instagram": "#SalesWhisper #sales #AI #автоматизация",
        "tiktok": "#SalesWhisper #продажи #AI",
        "youtube": "SalesWhisper — автоматизация продаж",
        "dzen": "",
    }
    base = text[:PLATFORM_LIMITS.get(platform, 2000)]
    tag = tags.get(platform, "")
    return f"{base}\n\n{tag}".strip() if tag else base


@celery.task(bind=True, name="app.workers.tasks.captionize.generate_captions")
def generate_captions(self, stage_data: dict[str, Any]) -> dict[str, Any]:
    """Generate AI-powered captions for all target platforms."""
    task_start_time = time.time()
    post_id = stage_data["post_id"]
    source_text = stage_data.get("text_content") or stage_data.get("original_text", "")

    with with_logging_context(task_id=self.request.id, post_id=post_id):
        logger.info("Starting caption generation", post_id=post_id, platforms=TARGET_PLATFORMS)

        try:
            captions = {}
            for platform in TARGET_PLATFORMS:
                captions[platform] = _generate_caption_llm(source_text, platform)
                logger.info("Caption generated", platform=platform, length=len(captions[platform]))

            processing_time = time.time() - task_start_time

            from .transcode import process_media
            next_task = process_media.delay({**stage_data, "captions": captions})

            logger.info(
                "Caption generation completed",
                post_id=post_id,
                platforms=list(captions.keys()),
                processing_time=processing_time,
            )

            return {
                "success": True,
                "post_id": post_id,
                "processing_time": processing_time,
                "platforms": list(captions.keys()),
                "next_stage": "transcode",
                "next_task_id": next_task.id,
            }

        except Exception as e:
            logger.exception("Caption generation failed", post_id=post_id, error=str(e))
            if self.request.retries < self.max_retries:
                raise self.retry(countdown=60 * (self.request.retries + 1))
            raise


def delay(*args, **kwargs):
    return generate_captions.delay(*args, **kwargs)
