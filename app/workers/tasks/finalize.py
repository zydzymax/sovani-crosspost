"""Finalize stage tasks for SalesWhisper Crosspost."""

import time
from typing import Dict, Any

from ..celery_app import celery
from ...core.logging import get_logger, with_logging_context
from ...observability.metrics import metrics

logger = get_logger("tasks.finalize")

@celery.task(bind=True, name="app.workers.tasks.finalize.finalize_post")
def finalize_post(self, stage_data: Dict[str, Any]) -> Dict[str, Any]:
    """Finalize post processing and cleanup."""
    task_start_time = time.time()
    post_id = stage_data["post_id"]
    
    with with_logging_context(task_id=self.request.id, post_id=post_id):
        logger.info("Starting post finalization", post_id=post_id)
        
        try:
            # Placeholder finalization logic
            publish_results = stage_data.get("publish_results", {})
            successful_platforms = [p for p, r in publish_results.items() if r.get("success")]
            
            # Update final post status
            final_status = "completed" if successful_platforms else "failed"
            
            # Cleanup temporary files
            temp_files_cleaned = 3  # Placeholder
            
            # Generate analytics summary
            analytics_summary = {
                "total_processing_time": time.time() - task_start_time,
                "platforms_successful": len(successful_platforms),
                "platforms_failed": len(publish_results) - len(successful_platforms),
                "files_processed": stage_data.get("media_count", 0),
                "temp_files_cleaned": temp_files_cleaned
            }
            
            processing_time = time.time() - task_start_time
            
            logger.info("Post finalization completed", 
                       post_id=post_id, 
                       processing_time=processing_time,
                       final_status=final_status,
                       successful_platforms=len(successful_platforms))
            
            return {
                "success": True,
                "post_id": post_id,
                "processing_time": processing_time,
                "final_status": final_status,
                "analytics_summary": analytics_summary,
                "stage": "completed"
            }
            
        except Exception as e:
            logger.error("Post finalization failed", post_id=post_id, error=str(e))
            if self.request.retries < self.max_retries:
                raise self.retry(countdown=60 * (self.request.retries + 1))
            raise

@celery.task(bind=True, name="app.workers.tasks.finalize.cleanup_completed_tasks")
def cleanup_completed_tasks(self) -> Dict[str, Any]:
    """Cleanup completed tasks and temporary files."""
    task_start_time = time.time()
    
    with with_logging_context(task_id=self.request.id):
        logger.info("Starting cleanup of completed tasks")
        
        try:
            # Placeholder cleanup logic
            cleaned_posts = 5  # Would query and clean old completed posts
            cleaned_files = 15  # Would clean temp files
            freed_space_mb = 250  # Space freed
            
            processing_time = time.time() - task_start_time
            
            logger.info("Cleanup completed", 
                       cleaned_posts=cleaned_posts,
                       cleaned_files=cleaned_files,
                       freed_space_mb=freed_space_mb)
            
            return {
                "success": True,
                "processing_time": processing_time,
                "cleaned_posts": cleaned_posts,
                "cleaned_files": cleaned_files,
                "freed_space_mb": freed_space_mb
            }
            
        except Exception as e:
            logger.error("Cleanup failed", error=str(e))
            raise