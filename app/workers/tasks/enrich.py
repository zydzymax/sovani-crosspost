"""
Enrich stage tasks for SalesWhisper Crosspost.

This module handles:
- Content enrichment with metadata
- Brand context addition
- Platform-specific content adaptation
- AI-powered image/video prompt generation
"""

import time
from typing import Any

import openai

from ...core.config import settings
from ...core.logging import get_logger, with_logging_context
from ..celery_app import celery

logger = get_logger("tasks.enrich")


def generate_media_prompts(text_content: str) -> dict[str, Any]:
    """Generate image and video prompts using OpenAI based on text content."""
    if not text_content or len(text_content.strip()) < 10:
        logger.warning("Text content too short for prompt generation")
        return {}

    try:
        client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)

        # Generate prompts using GPT
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": """You are an AI assistant that generates image and video prompts for social media posts.
Given a text post, create:
1. image_prompt: A detailed visual description for generating an image (2-3 sentences, descriptive, cinematic)
2. video_prompt: A short concept for a video if applicable (1-2 sentences, action-oriented)
3. media_needed: boolean - whether visual content would enhance this post

Return JSON format:
{
  "image_prompt": "detailed visual description...",
  "video_prompt": "video concept..." or null,
  "media_needed": true/false,
  "reasoning": "brief explanation"
}""",
                },
                {"role": "user", "content": f"Generate image and video prompts for this post:\n\n{text_content}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
            max_tokens=500,
        )

        import json

        result = json.loads(response.choices[0].message.content)
        logger.info("Generated media prompts", media_needed=result.get("media_needed"))
        return result

    except Exception:
        logger.exception("Failed to generate media prompts")
        return {}


@celery.task(bind=True, name="app.workers.tasks.enrich.enrich_post_content")
def enrich_post_content(self, stage_data: dict[str, Any]) -> dict[str, Any]:
    """Enrich post content with metadata and brand context."""
    task_start_time = time.time()
    post_id = stage_data["post_id"]

    with with_logging_context(task_id=self.request.id, post_id=post_id):
        logger.info("Starting content enrichment", post_id=post_id)

        try:
            text_content = stage_data.get("text_content", "")

            # Generate AI-powered media prompts
            media_prompts = generate_media_prompts(text_content)

            # Build enrichment data
            enriched_data = {
                "original_text": text_content,
                "brand_context": {"brand": "SalesWhisper", "voice": "elegant"},
                "hashtags": ["#SalesWhisper", "#Style"],
                "platform_adaptations": {"instagram": {"text": "Adapted for IG"}, "vk": {"text": "Adapted for VK"}},
            }

            # Add media prompts if generated
            if media_prompts.get("media_needed") and media_prompts.get("image_prompt"):
                enriched_data["image_prompt"] = media_prompts["image_prompt"]
                logger.info("Added image_prompt to enrichment_data for post %s", post_id)

            if media_prompts.get("video_prompt"):
                enriched_data["video_prompt"] = media_prompts["video_prompt"]
                logger.info("Added video_prompt to enrichment_data for post %s", post_id)

            processing_time = time.time() - task_start_time

            # Trigger next stage
            from .captionize import generate_captions
            from .image_generate import generate_post_image

            next_task = generate_captions.delay({**stage_data, **enriched_data})

            # Async image generation — fire-and-forget alongside the main pipeline
            if enriched_data.get("image_prompt"):
                generate_post_image.apply_async(
                    args=[post_id],
                    queue="default",
                    countdown=5,  # slight delay so post record is committed
                )
                logger.info("Queued image generation for post %s", post_id)

            logger.info(
                "Content enrichment completed",
                post_id=post_id,
                processing_time=processing_time,
                next_task_id=next_task.id,
                has_image_prompt=bool(enriched_data.get("image_prompt")),
                has_video_prompt=bool(enriched_data.get("video_prompt")),
            )

            return {
                "success": True,
                "post_id": post_id,
                "processing_time": processing_time,
                "next_stage": "captionize",
                "next_task_id": next_task.id,
            }

        except Exception:
            processing_time = time.time() - task_start_time
            logger.exception("Content enrichment failed", post_id=post_id, processing_time=processing_time)

            if self.request.retries < self.max_retries:
                raise self.retry(countdown=60 * (self.request.retries + 1))
            raise


def delay(*args, **kwargs):
    """Compatibility proxy for task.delay used by legacy tests."""
    return enrich_post_content.delay(*args, **kwargs)
