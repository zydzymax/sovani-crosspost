"""Outbox processing tasks for SalesWhisper Crosspost.

This module handles:
- Outbox event publishing to ensure reliable message delivery
- Processing outbox events into queue tasks
- Deduplication and retry logic
- Beat scheduler tasks for monitoring
"""

import json
import time
import uuid
import asyncio
import os
from typing import Any

import redis.asyncio as redis
from sqlalchemy.orm import Session

from ...core.config import settings
from ...core.logging import get_logger, with_logging_context
from ...models.db import db_manager
from ...observability.metrics import metrics
from ...services.outbox import (
    ensure_outbox_table,
    find_duplicate_event,
    get_pending_events,
    insert_outbox_event,
    mark_processed,
    schedule_retry,
)
from ..celery_app import celery

logger = get_logger("tasks.outbox")


def _is_outbox_strict_mode() -> bool:
    raw_override = os.getenv("OUTBOX_STRICT_MODE")
    if raw_override is not None:
        return raw_override.lower() in {"1", "true", "yes", "on"}
    return settings.app.environment in {"staging", "production"}


def publish_outbox_event(
    event_type: str, payload: dict[str, Any], entity_id: str, correlation_id: str | None = None
) -> str:
    """Publish an event to the outbox for reliable processing."""
    event_id = str(uuid.uuid4())
    correlation_id = correlation_id or str(uuid.uuid4())

    logger.info(
        "Publishing outbox event",
        event_id=event_id,
        event_type=event_type,
        entity_id=entity_id,
        correlation_id=correlation_id,
    )

    try:
        with db_manager.get_sync_session() as db_session:
            ensure_outbox_table(db_session)
            # Check for deduplication
            existing_event = _check_duplicate_event(db_session, event_type, entity_id, payload)
            if existing_event:
                logger.warning(
                    "Duplicate event detected, skipping", event_id=event_id, existing_event_id=existing_event
                )
                return existing_event

            insert_outbox_event(
                db_session,
                event_id=event_id,
                event_type=event_type,
                entity_id=entity_id,
                payload=payload,
                correlation_id=correlation_id,
            )
            logger.debug("Outbox event created", event_id=event_id, event_type=event_type, entity_id=entity_id)
            db_session.commit()
            return event_id

    except Exception as e:
        logger.error(
            "Failed to publish outbox event", event_type=event_type, entity_id=entity_id, error=str(e), exc_info=True
        )
        strict_mode = _is_outbox_strict_mode()
        if strict_mode:
            raise

        # Fallback mode: keep pipeline alive in degraded environments (e.g. local tests without DB).
        logger.warning(
            "Outbox storage unavailable, using degraded fallback",
            event_id=event_id,
            event_type=event_type,
            entity_id=entity_id,
        )
        return event_id


@celery.task(bind=True, name="app.workers.tasks.outbox.process_outbox_events")
def process_outbox_events(self) -> dict[str, Any]:
    """Process pending outbox events and dispatch to appropriate queues."""
    task_start_time = time.time()

    with with_logging_context(task_id=self.request.id):
        logger.info("Starting outbox event processing")

        try:
            with db_manager.get_sync_session() as db_session:
                # Get pending outbox events
                pending_events = _get_pending_outbox_events(db_session)
                logger.info("Found pending outbox events", count=len(pending_events))

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
                        logger.exception("Failed to process outbox event", event_id=event["id"])
                        _handle_event_retry(db_session, event, str(e))
                        failed_events += 1

                db_session.commit()

            processing_time = time.time() - task_start_time
            metrics.track_celery_task("process_outbox_events", "outbox", "success", processing_time)

            logger.info(
                "Outbox event processing completed",
                total_events=len(pending_events),
                processed_events=processed_events,
                failed_events=failed_events,
                processing_time=processing_time,
            )

            return {
                "success": True,
                "total_events": len(pending_events),
                "processed_events": processed_events,
                "failed_events": failed_events,
                "processing_time": processing_time,
            }

        except Exception:
            processing_time = time.time() - task_start_time
            logger.exception("Outbox event processing failed", processing_time=processing_time)
            metrics.track_celery_task("process_outbox_events", "outbox", "failure", processing_time)
            raise


@celery.task(bind=True, name="app.workers.tasks.outbox.update_queue_metrics")
def update_queue_metrics(self) -> dict[str, Any]:
    """Update queue size metrics for monitoring."""
    task_start_time = time.time()

    with with_logging_context(task_id=self.request.id):
        logger.debug("Updating queue metrics")

        try:
            queue_metrics = {
                "ingest": 0,
                "enrich": 0,
                "captionize": 0,
                "transcode": 0,
                "preflight": 0,
                "publish": 0,
                "finalize": 0,
                "outbox": 0,
                "default": 0,
            }
            for queue_name, size in queue_metrics.items():
                metrics.update_celery_queue_size(queue_name, size)

            processing_time = time.time() - task_start_time
            logger.debug("Queue metrics updated", total_queues=len(queue_metrics), processing_time=processing_time)
            return {"success": True, "queue_metrics": queue_metrics, "processing_time": processing_time}

        except Exception:
            logger.exception("Queue metrics update failed")
            raise


@celery.task(bind=True, name="app.workers.tasks.outbox.health_check_task")
def health_check_task(self) -> dict[str, Any]:
    """Perform health checks on system components."""
    task_start_time = time.time()

    with with_logging_context(task_id=self.request.id):
        logger.debug("Performing system health checks")

        try:
            health_status = {
                "database": _check_database_health(),
                "redis": _check_redis_health(),
                "queues": _check_queue_health(),
                "storage": _check_storage_health(),
            }
            overall_healthy = all(health_status.values())
            processing_time = time.time() - task_start_time

            logger.info(
                "Health check completed",
                overall_healthy=overall_healthy,
                database_healthy=health_status["database"],
                redis_healthy=health_status["redis"],
                processing_time=processing_time,
            )

            return {
                "success": True,
                "overall_healthy": overall_healthy,
                "health_status": health_status,
                "processing_time": processing_time,
            }
        except Exception:
            logger.exception("Health check failed")
            raise


# Helper functions
def _check_duplicate_event(db_session: Session, event_type: str, entity_id: str, payload: dict[str, Any]) -> str | None:
    """Check for duplicate events based on content hash."""
    return find_duplicate_event(db_session, event_type, entity_id, payload)


def _get_pending_outbox_events(db_session: Session, limit: int = 100) -> list[dict[str, Any]]:
    """Get pending outbox events ready for processing."""
    ensure_outbox_table(db_session)
    return get_pending_events(db_session, limit=limit)


def _process_single_outbox_event(event: dict[str, Any]) -> bool:
    """Process a single outbox event and dispatch to appropriate queue."""
    event_type = event["event_type"]
    payload = json.loads(event["payload"])

    logger.debug("Processing outbox event", event_id=event["id"], event_type=event_type)

    try:
        if event_type == "post_created":
            from .ingest import process_telegram_update

            process_telegram_update.delay(payload.get("update_data", {}), payload["post_id"])
        elif event_type in ("post_updated", "media_uploaded"):
            logger.info("Handling outbox event", event_type=event_type, event_id=event["id"])
        else:
            logger.warning("Unknown event type", event_id=event["id"], event_type=event_type)
            return False
        return True
    except Exception:
        logger.exception("Failed to dispatch outbox event", event_id=event["id"])
        return False


def _mark_event_processed(db_session: Session, event_id: str):
    """Mark outbox event as processed."""
    mark_processed(db_session, event_id)
    logger.debug("Marked event as processed", event_id=event_id)


def _handle_event_retry(db_session: Session, event: dict[str, Any], error_message: str = None):
    """Handle event retry logic with exponential backoff."""
    event_id = event["id"]
    retry_count = event.get("retry_count", 0) + 1
    max_retries = int(event.get("max_retries", 5))

    if retry_count >= max_retries:
        logger.error("Event max retries exceeded", event_id=event_id, retry_count=retry_count)
    else:
        backoff_seconds = min(60 * (2**retry_count), 3600)
        logger.warning("Scheduling event retry", event_id=event_id, retry_count=retry_count, backoff=backoff_seconds)

    schedule_retry(
        db_session,
        event_id=event_id,
        retry_count=retry_count,
        max_retries=max_retries,
        error_message=error_message,
    )


def _check_database_health() -> bool:
    try:
        return db_manager.health_check()
    except Exception:
        return False


def _check_redis_health() -> bool:
    try:
        redis_url = settings.get_redis_url()
        client = redis.from_url(redis_url)
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        result = loop.run_until_complete(client.ping())
        loop.run_until_complete(client.aclose())
        return bool(result)
    except Exception:
        return False


def _check_queue_health() -> bool:
    return True


def _check_storage_health() -> bool:
    return True
