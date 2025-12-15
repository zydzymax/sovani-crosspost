"""Publish stage tasks for SoVAni Crosspost."""

import time
from typing import Dict, Any

from ..celery_app import celery
from ...core.logging import get_logger, with_logging_context, audit_logger
from ...observability.metrics import metrics

logger = get_logger("tasks.publish")

@celery.task(bind=True, name="app.workers.tasks.publish.publish_to_platforms")
def publish_to_platforms(self, stage_data: Dict[str, Any]) -> Dict[str, Any]:
    """Publish content to social media platforms."""
    task_start_time = time.time()
    post_id = stage_data["post_id"]
    
    with with_logging_context(task_id=self.request.id, post_id=post_id):
        logger.info("Starting platform publishing", post_id=post_id)
        
        try:
            # Placeholder publishing logic
            platforms = ["instagram", "vk", "tiktok", "youtube"]
            publish_results = {}
            
            for platform in platforms:
                try:
                    # Simulate platform publishing
                    publish_results[platform] = {
                        "success": True,
                        "platform_post_id": f"{platform}_{post_id}_{int(time.time())}",
                        "platform_url": f"https://{platform}.com/post/{post_id}",
                        "published_at": time.time()
                    }
                    
                    # Log successful publication
                    audit_logger.log_post_published(
                        post_id=post_id,
                        platform=platform,
                        platform_post_id=publish_results[platform]["platform_post_id"],
                        platform_url=publish_results[platform]["platform_url"]
                    )
                    
                    # Track metrics
                    metrics.track_post_published(platform)
                    
                except Exception as e:
                    publish_results[platform] = {
                        "success": False,
                        "error": str(e),
                        "failed_at": time.time()
                    }
                    
                    # Track failure
                    metrics.track_post_failed(platform, "publish_error")
                    audit_logger.log_post_failed(post_id, platform, str(e))
            
            processing_time = time.time() - task_start_time
            successful_platforms = [p for p, r in publish_results.items() if r.get("success")]
            
            # Trigger next stage
            from .finalize import finalize_post
            next_task = finalize_post.delay({**stage_data, "publish_results": publish_results})
            
            logger.info("Platform publishing completed", 
                       post_id=post_id, 
                       processing_time=processing_time,
                       successful_platforms=len(successful_platforms),
                       total_platforms=len(platforms))
            
            return {
                "success": True,
                "post_id": post_id,
                "processing_time": processing_time,
                "platforms_published": len(successful_platforms),
                "total_platforms": len(platforms),
                "publish_results": publish_results,
                "next_stage": "finalize",
                "next_task_id": next_task.id
            }
            
        except Exception as e:
            logger.error("Platform publishing failed", post_id=post_id, error=str(e))
            if self.request.retries < self.max_retries:
                raise self.retry(countdown=60 * (self.request.retries + 1))
            raise