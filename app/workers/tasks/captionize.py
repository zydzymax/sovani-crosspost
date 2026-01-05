"""Captionize stage tasks for SalesWhisper Crosspost."""

import time
from typing import Any

from ...core.logging import get_logger, with_logging_context
from ..celery_app import celery

logger = get_logger("tasks.captionize")


@celery.task(bind=True, name="app.workers.tasks.captionize.generate_captions")
def generate_captions(self, stage_data: dict[str, Any]) -> dict[str, Any]:
    """Generate AI-powered captions for different platforms."""
    task_start_time = time.time()
    post_id = stage_data["post_id"]

    with with_logging_context(task_id=self.request.id, post_id=post_id):
        logger.info("Starting caption generation", post_id=post_id)

        try:
            # Placeholder caption generation
            captions = {
                "instagram": "( >20O :>;;5:F8O SalesWhisper C65 745AL! #SalesWhisper #Style",
                "vk": "@54AB02;O5< =>2CN :>;;5:F8N AB8;L=>9 >4564K >B SalesWhisper",
                "tiktok": "SalesWhisper style ( #fashion #SalesWhisper",
                "youtube": "17>@ =>2>9 :>;;5:F88 SalesWhisper - AB8;L 8 M;530=B=>ABL",
            }

            processing_time = time.time() - task_start_time

            # Trigger next stage
            from .transcode import process_media

            next_task = process_media.delay({**stage_data, "captions": captions})

            logger.info("Caption generation completed", post_id=post_id, processing_time=processing_time)

            return {
                "success": True,
                "post_id": post_id,
                "processing_time": processing_time,
                "next_stage": "transcode",
                "next_task_id": next_task.id,
            }

        except Exception as e:
            logger.error("Caption generation failed", post_id=post_id, error=str(e))
            if self.request.retries < self.max_retries:
                raise self.retry(countdown=60 * (self.request.retries + 1))
            raise
