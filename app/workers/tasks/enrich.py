"""
Enrich stage tasks for SalesWhisper Crosspost.

This module handles:
- Content enrichment with metadata
- Brand context addition
- Platform-specific content adaptation
"""

import time
from typing import Any

from ...core.logging import get_logger, with_logging_context
from ..celery_app import celery

logger = get_logger("tasks.enrich")


@celery.task(bind=True, name="app.workers.tasks.enrich.enrich_post_content")
def enrich_post_content(self, stage_data: dict[str, Any]) -> dict[str, Any]:
    """Enrich post content with metadata and brand context."""
    task_start_time = time.time()
    post_id = stage_data["post_id"]

    with with_logging_context(task_id=self.request.id, post_id=post_id):
        logger.info("Starting content enrichment", post_id=post_id)

        try:
            # Placeholder enrichment logic
            enriched_data = {
                "original_text": stage_data.get("text_content", ""),
                "brand_context": {"brand": "SalesWhisper", "voice": "elegant"},
                "hashtags": ["#SalesWhisper", "#Style"],
                "platform_adaptations": {
                    "instagram": {"text": "Adapted for IG"},
                    "vk": {"text": "Adapted for VK"}
                }
            }

            processing_time = time.time() - task_start_time

            # Trigger next stage
            from .captionize import generate_captions
            next_task = generate_captions.delay({**stage_data, **enriched_data})

            logger.info("Content enrichment completed",
                       post_id=post_id,
                       processing_time=processing_time,
                       next_task_id=next_task.id)

            return {
                "success": True,
                "post_id": post_id,
                "processing_time": processing_time,
                "next_stage": "captionize",
                "next_task_id": next_task.id
            }

        except Exception as e:
            processing_time = time.time() - task_start_time
            logger.error("Content enrichment failed", post_id=post_id, error=str(e))

            if self.request.retries < self.max_retries:
                raise self.retry(countdown=60 * (self.request.retries + 1))
            raise
