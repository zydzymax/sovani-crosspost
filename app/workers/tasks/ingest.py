"""
Ingest stage tasks for SoVAni Crosspost.

This module handles:
- Processing incoming Telegram webhook data
- Media file downloading and validation
- Initial post creation in database
- Triggering next stage (enrich)
"""

import time
import json
from typing import Dict, Any, Optional
from datetime import datetime

from celery import current_task
from sqlalchemy.orm import Session

from ..celery_app import celery
from ...core.logging import get_logger, with_logging_context, audit_logger
from ...core.config import settings
from ...models.db import db_manager
from ...observability.metrics import metrics


logger = get_logger("tasks.ingest")


@celery.task(bind=True, name="app.workers.tasks.ingest.process_telegram_update")
def process_telegram_update(self, update_data: Dict[str, Any], post_id: str) -> Dict[str, Any]:
    """
    Process incoming Telegram update and create initial post record.
    
    Args:
        update_data: Telegram webhook update data
        post_id: Generated post ID
        
    Returns:
        Processing result with next stage info
    """
    task_start_time = time.time()
    
    with with_logging_context(task_id=self.request.id, post_id=post_id):
        logger.info(
            "Starting Telegram update processing",
            post_id=post_id,
            update_id=update_data.get("update_id"),
            has_message=bool(update_data.get("message")),
            has_channel_post=bool(update_data.get("channel_post"))
        )
        
        try:
            # Get database session
            db_session = db_manager.get_session()
            
            try:
                # Extract relevant content
                content = _extract_content_from_update(update_data)
                
                if not content:
                    raise ValueError("No processable content in update")
                
                # Create post record
                post_record = _create_post_record(
                    db_session, 
                    post_id, 
                    content, 
                    update_data
                )
                
                # Download and validate media files
                media_info = _process_media_files(content, post_id)
                
                # Update post with media info
                if media_info:
                    _update_post_with_media(db_session, post_id, media_info)
                
                # Commit transaction
                db_session.commit()
                
                # Calculate processing time
                processing_time = time.time() - task_start_time
                
                # Update task status
                _update_task_status(
                    db_session, 
                    post_id, 
                    "ingest", 
                    "completed", 
                    processing_time
                )
                
                # Track metrics
                metrics.track_post_created("telegram", "webhook")
                metrics.track_media_processed(
                    media_type="mixed" if media_info else "text",
                    platform="telegram",
                    success=True,
                    duration=processing_time,
                    file_size=sum(m.get("file_size", 0) for m in media_info) if media_info else 0
                )
                
                # Audit log
                audit_logger.log_post_created(
                    post_id=post_id,
                    platform="telegram",
                    user_id=str(content.get("from", {}).get("id", "unknown")),
                    product_id="telegram_ingest",
                    processing_time=processing_time
                )
                
                # Prepare next stage
                next_stage_data = {
                    "post_id": post_id,
                    "has_media": bool(media_info),
                    "media_count": len(media_info) if media_info else 0,
                    "text_content": content.get("text") or content.get("caption", ""),
                    "source": "telegram",
                    "original_update": update_data
                }
                
                # Trigger next stage (enrich)
                from .enrich import enrich_post_content
                enrich_task = enrich_post_content.delay(next_stage_data)
                
                logger.info(
                    "Telegram update processed successfully",
                    post_id=post_id,
                    processing_time=processing_time,
                    next_task_id=enrich_task.id,
                    media_files_count=len(media_info) if media_info else 0
                )
                
                return {
                    "success": True,
                    "post_id": post_id,
                    "processing_time": processing_time,
                    "media_processed": len(media_info) if media_info else 0,
                    "next_stage": "enrich",
                    "next_task_id": enrich_task.id
                }
                
            finally:
                db_session.close()
                
        except Exception as e:
            processing_time = time.time() - task_start_time
            
            logger.error(
                "Telegram update processing failed",
                post_id=post_id,
                error=str(e),
                processing_time=processing_time,
                exc_info=True
            )
            
            # Update task status
            try:
                db_session = db_manager.get_session()
                _update_task_status(
                    db_session,
                    post_id,
                    "ingest", 
                    "failed",
                    processing_time,
                    error_message=str(e)
                )
                db_session.commit()
                db_session.close()
            except:
                pass  # Don't fail on status update errors
            
            # Track failure metrics
            metrics.track_post_failed("telegram", "ingest_error")
            
            # Retry logic
            if self.request.retries < self.max_retries:
                logger.warning(
                    "Retrying telegram update processing",
                    post_id=post_id,
                    retry_count=self.request.retries + 1,
                    max_retries=self.max_retries
                )
                raise self.retry(countdown=60 * (self.request.retries + 1))
            
            raise


def _extract_content_from_update(update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract processable content from Telegram update."""
    content_sources = ["message", "channel_post", "edited_message"]
    
    for source in content_sources:
        if source in update_data and update_data[source]:
            return update_data[source]
    
    return None


def _create_post_record(db_session: Session, post_id: str, content: Dict[str, Any], 
                       update_data: Dict[str, Any]) -> Dict[str, Any]:
    """Create initial post record in database."""
    logger.info("Creating post record", post_id=post_id)
    
    # This is a placeholder - would create actual database record
    post_data = {
        "id": post_id,
        "source_platform": "telegram",
        "source_data": content,
        "original_update": update_data,
        "status": "ingested",
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    
    # In real implementation:
    # post = PostModel(**post_data)
    # db_session.add(post)
    
    logger.info("Post record created", post_id=post_id)
    return post_data


def _process_media_files(content: Dict[str, Any], post_id: str) -> Optional[list]:
    """Download and process media files from content."""
    media_info = []
    
    # Check for different media types
    media_fields = ["photo", "video", "animation", "document", "audio", "voice"]
    
    for field in media_fields:
        if field in content:
            media_data = content[field]
            
            # For photos, get largest size
            if field == "photo" and isinstance(media_data, list):
                media_data = max(media_data, key=lambda x: x.get("file_size", 0))
            
            if media_data:
                media_file_info = _download_media_file(media_data, field, post_id)
                if media_file_info:
                    media_info.append(media_file_info)
    
    return media_info if media_info else None


def _download_media_file(media_data: Dict[str, Any], media_type: str, post_id: str) -> Optional[Dict[str, Any]]:
    """Download media file from Telegram."""
    file_id = media_data.get("file_id")
    if not file_id:
        return None
    
    logger.info(
        "Processing media file",
        post_id=post_id,
        file_id=file_id,
        media_type=media_type
    )
    
    # This is a placeholder - would actually download file
    # In real implementation:
    # 1. Get file info from Telegram API
    # 2. Download file to temp location
    # 3. Upload to S3/MinIO
    # 4. Return S3 path and metadata
    
    media_info = {
        "file_id": file_id,
        "media_type": media_type,
        "file_size": media_data.get("file_size", 0),
        "mime_type": media_data.get("mime_type"),
        "file_name": media_data.get("file_name"),
        "duration": media_data.get("duration"),
        "width": media_data.get("width"),
        "height": media_data.get("height"),
        "local_path": f"/tmp/{post_id}_{file_id}",  # Placeholder
        "s3_path": f"media/{post_id}/{file_id}",    # Placeholder
        "downloaded_at": datetime.utcnow().isoformat()
    }
    
    logger.info(
        "Media file processed",
        post_id=post_id,
        file_id=file_id,
        file_size=media_info["file_size"]
    )
    
    return media_info


def _update_post_with_media(db_session: Session, post_id: str, media_info: list):
    """Update post record with media information."""
    logger.info("Updating post with media info", post_id=post_id, media_count=len(media_info))
    
    # This is a placeholder - would update database record
    # In real implementation:
    # post = db_session.query(PostModel).filter_by(id=post_id).first()
    # post.media_assets = media_info
    # post.updated_at = datetime.utcnow()
    
    logger.info("Post updated with media info", post_id=post_id)


def _update_task_status(db_session: Session, post_id: str, stage: str, status: str, 
                       processing_time: float, error_message: str = None):
    """Update task status in database."""
    logger.debug(
        "Updating task status",
        post_id=post_id,
        stage=stage,
        status=status,
        processing_time=processing_time
    )
    
    # This is a placeholder - would update task status table
    # In real implementation:
    # task_status = TaskStatusModel(
    #     post_id=post_id,
    #     stage=stage,
    #     status=status,
    #     processing_time=processing_time,
    #     error_message=error_message,
    #     completed_at=datetime.utcnow()
    # )
    # db_session.add(task_status)
    
    logger.debug("Task status updated", post_id=post_id, stage=stage, status=status)