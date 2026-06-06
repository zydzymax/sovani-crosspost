"""Image generation task for content plan posts.

Generates images using Nanobana (Gemini) for posts with image_prompt.
"""

import time
from typing import Any
from uuid import uuid4

from ...core.logging import get_logger, with_logging_context
from ...models.db import db_manager
from ...services.image_gen_nanobana import AspectRatio, ModelType, NanobanaService
from ..celery_app import celery

logger = get_logger("tasks.image_generate")


@celery.task(bind=True, name="app.workers.tasks.image_generate.generate_post_image")
def generate_post_image(self, post_id: str) -> dict[str, Any]:
    """Generate image for a post using its image_prompt.

    Args:
        post_id: UUID of the post to generate image for

    Returns:
        dict with success status and image_url if generated
    """
    task_start_time = time.time()

    with with_logging_context(task_id=self.request.id, post_id=post_id):
        logger.info("Starting image generation for post", post_id=post_id)

        try:
            with db_manager.get_sync_session() as db:
                from sqlalchemy import text

                # Get post with enrichment data
                result = db.execute(
                    text(
                        """
                        SELECT id, enrichment_data, user_id
                        FROM posts
                        WHERE id = :post_id
                    """
                    ),
                    {"post_id": post_id},
                )
                post = result.fetchone()

                if not post:
                    logger.warning("Post not found", post_id=post_id)
                    return {"success": False, "error": "Post not found"}

                # Check if image already exists
                media_check = db.execute(
                    text("SELECT id FROM media_assets WHERE post_id = :post_id LIMIT 1"), {"post_id": post_id}
                )
                if media_check.fetchone():
                    logger.info("Post already has media, skipping generation", post_id=post_id)
                    return {"success": True, "skipped": True, "reason": "already_has_media"}

                # Get image_prompt from enrichment_data
                enrichment = post.enrichment_data or {}
                image_prompt = enrichment.get("image_prompt")

                if not image_prompt:
                    logger.info("No image_prompt in post, skipping", post_id=post_id)
                    return {"success": True, "skipped": True, "reason": "no_image_prompt"}

                # Generate image using Nanobana
                import asyncio

                async def _generate():
                    service = NanobanaService()
                    try:
                        result = await service.generate_image(
                            prompt=image_prompt, model=ModelType.PRO, aspect_ratio=AspectRatio.SQUARE
                        )
                        return result
                    finally:
                        await service.close()

                gen_result = asyncio.run(_generate())

                if not gen_result.success:
                    logger.error("Image generation failed", post_id=post_id, error=gen_result.error)
                    return {"success": False, "error": gen_result.error}

                image_url = gen_result.image_url
                logger.info("Image generated successfully", post_id=post_id, image_url=image_url)

                # Create MediaAsset record
                media_id = uuid4()
                db.execute(
                    text(
                        """
                        INSERT INTO media_assets (
                            id, post_id, media_type, original_path,
                            file_size, created_at, updated_at
                        ) VALUES (
                            :id, :post_id, 'IMAGE', :url,
                            0, NOW(), NOW()
                        )
                    """
                    ),
                    {"id": media_id, "post_id": post_id, "url": image_url},
                )

                # Update post enrichment_data with generated image
                import json

                enrichment["generated_image_url"] = image_url
                enrichment["image_generated_at"] = time.time()

                db.execute(
                    text(
                        """
                        UPDATE posts
                        SET enrichment_data = :enrichment,
                            updated_at = NOW()
                        WHERE id = :post_id
                    """
                    ),
                    {"post_id": post_id, "enrichment": json.dumps(enrichment)},
                )

                db.commit()

                processing_time = time.time() - task_start_time
                logger.info(
                    "Image generation completed", post_id=post_id, processing_time=processing_time, image_url=image_url
                )

                return {
                    "success": True,
                    "image_url": image_url,
                    "media_id": str(media_id),
                    "processing_time": processing_time,
                }

        except Exception as e:
            logger.exception("Image generation task failed", post_id=post_id, error=str(e))

            if self.request.retries < 3:
                raise self.retry(countdown=60 * (self.request.retries + 1))

            return {"success": False, "error": str(e)}


@celery.task(bind=True, name="app.workers.tasks.image_generate.batch_generate_images")
def batch_generate_images(self, post_ids: list[str]) -> dict[str, Any]:
    """Generate images for multiple posts in batch.

    Args:
        post_ids: List of post UUIDs

    Returns:
        dict with batch generation results
    """
    task_start_time = time.time()

    with with_logging_context(task_id=self.request.id):
        logger.info("Starting batch image generation", post_count=len(post_ids))

        results = {"total": len(post_ids), "generated": 0, "skipped": 0, "failed": 0, "details": []}

        for post_id in post_ids:
            try:
                result = generate_post_image.delay(post_id)
                # Don't wait for result, just queue them
                results["details"].append({"post_id": post_id, "task_id": result.id, "status": "queued"})
            except Exception as e:
                logger.exception("Failed to queue image generation", post_id=post_id)
                results["failed"] += 1
                results["details"].append({"post_id": post_id, "status": "queue_failed", "error": str(e)})

        processing_time = time.time() - task_start_time
        logger.info("Batch image generation queued", total=results["total"], processing_time=processing_time)

        return {"success": True, **results, "processing_time": processing_time}
