"""Outbox processing tasks for SoVAni Crosspost.

This module handles:
- Outbox event publishing to ensure reliable message delivery
- Processing outbox events into queue tasks
- Deduplication and retry logic
- Beat scheduler tasks for monitoring
"""

import time
import json
import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

from celery import current_task
from sqlalchemy.orm import Session

from ..celery_app import celery
from ...core.logging import get_logger, with_logging_context
from ...core.config import settings
from ...models.db import db_manager
from ...observability.metrics import metrics


logger = get_logger("tasks.outbox")


def publish_outbox_event(event_type: str, payload: Dict[str, Any], 
                        entity_id: str, correlation_id: Optional[str] = None) -> str:
    """Publish an event to the outbox for reliable processing."""
    event_id = str(uuid.uuid4())
    correlation_id = correlation_id or str(uuid.uuid4())
    
    logger.info(
        "Publishing outbox event",
        event_id=event_id,
        event_type=event_type,
        entity_id=entity_id,
        correlation_id=correlation_id
    )
    
    try:
        db_session = db_manager.get_session()
        
        # Check for deduplication
        existing_event = _check_duplicate_event(db_session, event_type, entity_id, payload)
        if existing_event:
            logger.warning(
                "Duplicate event detected, skipping",
                event_id=event_id,
                existing_event_id=existing_event
            )
            db_session.close()
            return existing_event
        
        # Create outbox event record
        outbox_event = {
            "id": event_id,
            "event_type": event_type,
            "entity_id": entity_id,
            "payload": json.dumps(payload),
            "correlation_id": correlation_id,
            "status": "pending",
            "created_at": datetime.utcnow(),
            "retry_count": 0,
            "next_retry_at": datetime.utcnow()
        }
        
        # In real implementation: insert into outbox_events table
        logger.debug(
            "Outbox event created",
            event_id=event_id,
            event_type=event_type
        )
        
        db_session.commit()
        db_session.close()
        
        return event_id
        
    except Exception as e:
        logger.error(
            "Failed to publish outbox event",
            event_type=event_type,
            entity_id=entity_id,
            error=str(e),
            exc_info=True
        )
        raise


@celery.task(bind=True, name="app.workers.tasks.outbox.process_outbox_events")
def process_outbox_events(self) -> Dict[str, Any]:
    """Process pending outbox events and dispatch to appropriate queues."""
    task_start_time = time.time()
    
    with with_logging_context(task_id=self.request.id):
        logger.info("Starting outbox event processing")
        
        try:
            db_session = db_manager.get_session()
            
            try:
                # Get pending outbox events
                pending_events = _get_pending_outbox_events(db_session)
                
                logger.info(f"Found {len(pending_events)} pending outbox events")
                
                processed_events = 0
                failed_events = 0
                
                for event in pending_events:
                    try:
                        success = _process_single_outbox_event(event)
                        if success:
                            _mark_event_processed(db_session, event["id"])
                            processed_events += 1
                        else:
                            _handle_event_retry(db_session, event)
                            failed_events += 1
                            
                    except Exception as e:
                        logger.error(
                            "Failed to process outbox event",
                            event_id=event["id"],
                            error=str(e)
                        )
                        _handle_event_retry(db_session, event, str(e))
                        failed_events += 1
                
                db_session.commit()
                
                processing_time = time.time() - task_start_time
                
                # Track metrics
                metrics.track_celery_task(
                    "process_outbox_events", 
                    "outbox", 
                    "success", 
                    processing_time
                )
                
                logger.info(
                    "Outbox event processing completed",
                    total_events=len(pending_events),
                    processed_events=processed_events,
                    failed_events=failed_events,
                    processing_time=processing_time
                )
                
                return {
                    "success": True,
                    "total_events": len(pending_events),
                    "processed_events": processed_events,
                    "failed_events": failed_events,
                    "processing_time": processing_time
                }
                
            finally:
                db_session.close()
                
        except Exception as e:
            processing_time = time.time() - task_start_time
            
            logger.error(
                "Outbox event processing failed",
                error=str(e),
                processing_time=processing_time,
                exc_info=True
            )
            
            metrics.track_celery_task(
                "process_outbox_events", 
                "outbox", 
                "failure", 
                processing_time
            )
            
            raise


@celery.task(bind=True, name="app.workers.tasks.outbox.update_queue_metrics")
def update_queue_metrics(self) -> Dict[str, Any]:
    """Update queue size metrics for monitoring."""
    task_start_time = time.time()
    
    with with_logging_context(task_id=self.request.id):
        logger.debug("Updating queue metrics")
        
        try:
            # This is a placeholder - would query actual queue sizes
            queue_metrics = {
                "ingest": 5,
                "enrich": 3,
                "captionize": 2,
                "transcode": 1,
                "preflight": 2,
                "publish": 4,
                "finalize": 1,
                "outbox": 0
            }
            
            for queue_name, size in queue_metrics.items():
                metrics.update_celery_queue_size(queue_name, size)
            
            processing_time = time.time() - task_start_time
            
            logger.debug(
                "Queue metrics updated",
                total_queues=len(queue_metrics),
                processing_time=processing_time
            )
            
            return {
                "success": True,
                "queue_metrics": queue_metrics,
                "processing_time": processing_time
            }
            
        except Exception as e:
            logger.error("Queue metrics update failed", error=str(e))
            raise


@celery.task(bind=True, name="app.workers.tasks.outbox.health_check_task")
def health_check_task(self) -> Dict[str, Any]:
    """Perform health checks on system components."""
    task_start_time = time.time()
    
    with with_logging_context(task_id=self.request.id):
        logger.debug("Performing system health checks")
        
        try:
            health_status = {
                "database": _check_database_health(),
                "redis": _check_redis_health(),
                "queues": _check_queue_health(),
                "storage": _check_storage_health()
            }
            
            overall_healthy = all(health_status.values())
            processing_time = time.time() - task_start_time
            
            logger.info(
                "Health check completed",
                overall_healthy=overall_healthy,
                database_healthy=health_status["database"],
                redis_healthy=health_status["redis"],
                processing_time=processing_time
            )
            
            return {
                "success": True,
                "overall_healthy": overall_healthy,
                "health_status": health_status,
                "processing_time": processing_time
            }
            
        except Exception as e:
            logger.error("Health check failed", error=str(e))
            raise


# Helper functions
def _check_duplicate_event(db_session: Session, event_type: str, 
                          entity_id: str, payload: Dict[str, Any]) -> Optional[str]:
    """Check for duplicate events based on content hash."""
    # This is a placeholder - would check deduplication table
    # In real implementation:
    # 1. Generate content hash of event_type + entity_id + payload
    # 2. Query deduplication table for existing hash
    # 3. Return existing event ID if found
    return None


def _get_pending_outbox_events(db_session: Session, limit: int = 100) -> List[Dict[str, Any]]:
    """Get pending outbox events ready for processing."""
    # This is a placeholder - would query outbox_events table
    # In real implementation:
    # SELECT * FROM outbox_events 
    # WHERE status = 'pending' AND next_retry_at <= NOW()
    # ORDER BY created_at ASC LIMIT limit
    
    # Placeholder data
    return [
        {
            "id": "event_1",
            "event_type": "post_created",
            "entity_id": "post_123",
            "payload": '{"post_id": "post_123", "source": "telegram"}',
            "correlation_id": "corr_1",
            "status": "pending",
            "created_at": datetime.utcnow(),
            "retry_count": 0
        }
    ]


def _process_single_outbox_event(event: Dict[str, Any]) -> bool:
    """Process a single outbox event and dispatch to appropriate queue."""
    event_type = event["event_type"]
    payload = json.loads(event["payload"])
    
    logger.debug(
        "Processing outbox event",
        event_id=event["id"],
        event_type=event_type
    )
    
    try:
        # Route event to appropriate task based on event type
        if event_type == "post_created":
            from .ingest import process_telegram_update
            process_telegram_update.delay(
                payload.get("update_data", {}),
                payload["post_id"]
            )
            
        elif event_type == "post_updated":
            # Handle post update event
            logger.info("Handling post update event", event_id=event["id"])
            
        elif event_type == "media_uploaded":
            # Handle media upload event
            logger.info("Handling media upload event", event_id=event["id"])
            
        else:
            logger.warning(
                "Unknown event type",
                event_id=event["id"],
                event_type=event_type
            )
            return False
        
        return True
        
    except Exception as e:
        logger.error(
            "Failed to dispatch outbox event",
            event_id=event["id"],
            event_type=event_type,
            error=str(e)
        )
        return False


def _mark_event_processed(db_session: Session, event_id: str):
    """Mark outbox event as processed."""
    # This is a placeholder - would update outbox_events table
    # UPDATE outbox_events SET status = 'processed', processed_at = NOW()
    # WHERE id = event_id
    logger.debug("Marked event as processed", event_id=event_id)


def _handle_event_retry(db_session: Session, event: Dict[str, Any], error_message: str = None):
    """Handle event retry logic with exponential backoff."""
    event_id = event["id"]
    retry_count = event.get("retry_count", 0) + 1
    max_retries = 5
    
    if retry_count >= max_retries:
        # Mark as failed permanently
        logger.error(
            "Event max retries exceeded, marking as failed",
            event_id=event_id,
            retry_count=retry_count,
            error_message=error_message
        )
        # UPDATE outbox_events SET status = 'failed' WHERE id = event_id
    else:
        # Schedule retry with exponential backoff
        backoff_seconds = min(60 * (2 ** retry_count), 3600)  # Max 1 hour
        next_retry_at = datetime.utcnow() + timedelta(seconds=backoff_seconds)
        
        logger.warning(
            "Scheduling event retry",
            event_id=event_id,
            retry_count=retry_count,
            next_retry_in_seconds=backoff_seconds
        )
        
        # UPDATE outbox_events SET retry_count = retry_count, 
        # next_retry_at = next_retry_at WHERE id = event_id


def _check_database_health() -> bool:
    """Check database connectivity and health."""
    try:
        return db_manager.health_check()
    except Exception:
        return False


def _check_redis_health() -> bool:
    """Check Redis connectivity and health."""
    try:
        # This is a placeholder - would actually ping Redis
        return True
    except Exception:
        return False


def _check_queue_health() -> bool:
    """Check Celery queue health."""
    try:
        # This is a placeholder - would check queue status
        return True
    except Exception:
        return False


def _check_storage_health() -> bool:
    """Check S3/MinIO storage health."""
    try:
        # This is a placeholder - would test storage connectivity
        return True
    except Exception:
        return False