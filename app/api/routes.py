"""
API routes for SalesWhisper Crosspost.

This module provides:
- Health check endpoint
- Telegram webhook intake
- Debug publishing endpoint
- Admin queue monitoring
- Pydantic request/response schemas
"""

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..core.logging import audit_logger, get_logger, with_logging_context
from ..observability.metrics import metrics
from .deps import get_db_session, get_redis_client, get_settings

router = APIRouter()
logger = get_logger("api")


class HealthResponse(BaseModel):
    """Health check response schema."""

    status: str = Field(description="Application status")
    timestamp: datetime = Field(description="Response timestamp")
    version: str = Field(description="Application version")
    environment: str = Field(description="Environment name")
    services: dict[str, str] = Field(description="Service health status")


class TelegramWebhookRequest(BaseModel):
    """Telegram webhook request schema."""

    update_id: int = Field(description="Telegram update ID")
    message: dict[str, Any] | None = Field(default=None, description="Message object")
    channel_post: dict[str, Any] | None = Field(default=None, description="Channel post object")
    edited_message: dict[str, Any] | None = Field(default=None, description="Edited message object")

    class Config:
        extra = "allow"


class TelegramWebhookResponse(BaseModel):
    """Telegram webhook response schema."""

    success: bool = Field(description="Processing success status")
    message: str = Field(description="Response message")
    post_id: str | None = Field(default=None, description="Created post ID")
    processing_time: float = Field(description="Processing time in seconds")


class DebugPublishRequest(BaseModel):
    """Debug publish request schema."""

    post_id: str = Field(description="Post ID to publish")
    platforms: list[str] = Field(description="Target platforms")
    force: bool = Field(default=False, description="Force republish")


class DebugPublishResponse(BaseModel):
    """Debug publish response schema."""

    success: bool = Field(description="Publishing success status")
    message: str = Field(description="Response message")
    results: dict[str, dict[str, Any]] = Field(description="Per-platform results")


class QueueInfo(BaseModel):
    """Queue information schema."""

    name: str = Field(description="Queue name")
    size: int = Field(description="Number of pending tasks")
    active_tasks: int = Field(description="Number of active tasks")
    failed_tasks: int = Field(description="Number of failed tasks")
    last_task_timestamp: datetime | None = Field(default=None, description="Last task timestamp")


class QueuesResponse(BaseModel):
    """Queues status response schema."""

    queues: list[QueueInfo] = Field(description="Queue information list")
    total_pending: int = Field(description="Total pending tasks")
    total_active: int = Field(description="Total active tasks")
    system_health: str = Field(description="Overall system health")


class ReadyResponse(BaseModel):
    """Readiness check response schema."""

    ready: bool = Field(description="Application readiness status")
    timestamp: datetime = Field(description="Response timestamp")
    checks: dict[str, bool] = Field(description="Individual readiness checks")


@router.get("/ready", response_model=ReadyResponse, tags=["Health"])
async def readiness_check(redis=Depends(get_redis_client)):
    """
    Readiness probe endpoint for Kubernetes.

    Returns whether the application is ready to receive traffic.
    Unlike /health, this only checks critical dependencies.
    """
    checks = {}

    # Check Redis (critical for auth and caching)
    try:
        await redis.ping()
        checks["redis"] = True
    except Exception:
        checks["redis"] = False

    # Check database (critical for data operations)
    try:
        from ..models.db import db_manager

        checks["database"] = db_manager.health_check()
    except Exception:
        checks["database"] = False

    ready = all(checks.values())

    return ReadyResponse(ready=ready, timestamp=datetime.now(), checks=checks)


@router.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check(db=Depends(get_db_session), redis=Depends(get_redis_client), settings=Depends(get_settings)):
    """
    Health check endpoint.

    Returns application health status including service dependencies.
    """
    start_time = datetime.now()

    logger.info("Health check requested")

    # Check service health
    services = {}

    # Database health
    try:
        # Simple query to test database connectivity
        from ..models.db import db_manager

        db_healthy = db_manager.health_check()
        services["database"] = "healthy" if db_healthy else "unhealthy"
    except Exception as e:
        logger.error("Database health check failed", error=str(e))
        services["database"] = "unhealthy"

    # Redis health
    try:
        await redis.ping()
        services["redis"] = "healthy"
    except Exception as e:
        logger.error("Redis health check failed", error=str(e))
        services["redis"] = "unhealthy"

    # S3 health (basic connectivity)
    try:
        # This is a placeholder - would normally test S3 connectivity
        services["s3"] = "healthy"
    except Exception as e:
        logger.error("S3 health check failed", error=str(e))
        services["s3"] = "unhealthy"

    # Overall status
    overall_status = "healthy" if all(s == "healthy" for s in services.values()) else "degraded"

    # Track health check metrics
    processing_time = (datetime.now() - start_time).total_seconds()
    metrics.track_request("GET", "/health", 200, processing_time)

    # Log health check result
    logger.info("Health check completed", status=overall_status, services=services, processing_time=processing_time)

    return HealthResponse(
        status=overall_status,
        timestamp=datetime.now(),
        version=settings.app.version,
        environment=settings.app.environment,
        services=services,
    )


@router.post("/intake/telegram", response_model=TelegramWebhookResponse, tags=["Telegram"])
async def telegram_webhook(
    request: Request,
    webhook_data: TelegramWebhookRequest,
    db=Depends(get_db_session),
    redis=Depends(get_redis_client),
    settings=Depends(get_settings),
):
    """
    Telegram webhook endpoint.

    Receives and processes updates from Telegram bot.
    """
    start_time = datetime.now()

    with with_logging_context(request_id=getattr(request.state, "request_id", None)):
        logger.info(
            "Telegram webhook received",
            update_id=webhook_data.update_id,
            has_message=webhook_data.message is not None,
            has_channel_post=webhook_data.channel_post is not None,
        )

        try:
            # Validate webhook signature (placeholder)
            # In production, this would verify the webhook signature

            # Extract relevant content
            content = None
            if webhook_data.message:
                content = webhook_data.message
            elif webhook_data.channel_post:
                content = webhook_data.channel_post
            elif webhook_data.edited_message:
                content = webhook_data.edited_message

            if not content:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No processable content in webhook")

            # Generate post ID (placeholder)
            import uuid

            post_id = str(uuid.uuid4())

            # Store in database (placeholder)
            logger.info("Storing webhook content in database", post_id=post_id)

            # Queue for processing (placeholder)
            processing_task = {
                "post_id": post_id,
                "content": content,
                "update_id": webhook_data.update_id,
                "created_at": datetime.now().isoformat(),
            }

            # Add to Redis queue (placeholder)
            await redis.lpush("ingest_queue", json.dumps(processing_task))

            # Track metrics
            processing_time = (datetime.now() - start_time).total_seconds()
            metrics.track_request("POST", "/intake/telegram", 200, processing_time)
            metrics.track_post_created("telegram", "webhook")

            # Audit log
            audit_logger.log_post_created(
                post_id=post_id,
                platform="telegram",
                user_id=str(content.get("from", {}).get("id", "unknown")),
                product_id="webhook_intake",
                update_id=webhook_data.update_id,
            )

            logger.info("Telegram webhook processed successfully", post_id=post_id, processing_time=processing_time)

            return TelegramWebhookResponse(
                success=True, message="Webhook processed successfully", post_id=post_id, processing_time=processing_time
            )

        except HTTPException:
            raise
        except Exception as e:
            processing_time = (datetime.now() - start_time).total_seconds()

            logger.error(
                "Telegram webhook processing failed", error=str(e), processing_time=processing_time, exc_info=True
            )

            # Track error metrics
            metrics.track_request("POST", "/intake/telegram", 500, processing_time)

            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to process webhook")


@router.post("/debug/publish", response_model=DebugPublishResponse, tags=["Debug"])
async def debug_publish(
    request: Request,
    publish_request: DebugPublishRequest,
    db=Depends(get_db_session),
    redis=Depends(get_redis_client),
    settings=Depends(get_settings),
):
    """
    Debug publish endpoint.

    Manually trigger publishing of a post to specified platforms.
    Only available in development/testing environments.
    """
    start_time = datetime.now()

    # Security check - only allow in non-production
    if settings.app.is_production:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Debug endpoints not available in production")

    with with_logging_context(request_id=getattr(request.state, "request_id", None)):
        logger.info(
            "Debug publish requested",
            post_id=publish_request.post_id,
            platforms=publish_request.platforms,
            force=publish_request.force,
        )

        try:
            # Validate post exists (placeholder)
            post_exists = True  # Would check database

            if not post_exists:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail=f"Post {publish_request.post_id} not found"
                )

            # Validate platforms
            supported_platforms = ["instagram", "vk", "tiktok", "youtube"]
            invalid_platforms = [p for p in publish_request.platforms if p not in supported_platforms]

            if invalid_platforms:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsupported platforms: {invalid_platforms}"
                )

            # Queue publishing tasks
            results = {}
            for platform in publish_request.platforms:
                try:
                    # Create publishing task (placeholder)
                    task_data = {
                        "post_id": publish_request.post_id,
                        "platform": platform,
                        "force": publish_request.force,
                        "debug": True,
                        "created_at": datetime.now().isoformat(),
                    }

                    # Add to platform-specific queue
                    queue_name = f"publish_{platform}"
                    await redis.lpush(queue_name, json.dumps(task_data))

                    results[platform] = {
                        "status": "queued",
                        "message": f"Publishing task queued for {platform}",
                        "queue": queue_name,
                    }

                    logger.info(f"Queued publishing task for {platform}", post_id=publish_request.post_id)

                except Exception as e:
                    results[platform] = {
                        "status": "error",
                        "message": f"Failed to queue task: {str(e)}",
                        "error": str(e),
                    }

                    logger.error(f"Failed to queue {platform} task", error=str(e))

            # Track metrics
            processing_time = (datetime.now() - start_time).total_seconds()
            metrics.track_request("POST", "/debug/publish", 200, processing_time)

            # Audit log
            audit_logger.log_api_access(
                method="POST",
                path="/debug/publish",
                status_code=200,
                response_time=processing_time,
                post_id=publish_request.post_id,
                platforms=publish_request.platforms,
            )

            success = all(r["status"] == "queued" for r in results.values())

            logger.info(
                "Debug publish completed",
                post_id=publish_request.post_id,
                success=success,
                processing_time=processing_time,
            )

            return DebugPublishResponse(
                success=success,
                message="Publishing tasks queued" if success else "Some tasks failed to queue",
                results=results,
            )

        except HTTPException:
            raise
        except Exception as e:
            processing_time = (datetime.now() - start_time).total_seconds()

            logger.error(
                "Debug publish failed",
                post_id=publish_request.post_id,
                error=str(e),
                processing_time=processing_time,
                exc_info=True,
            )

            metrics.track_request("POST", "/debug/publish", 500, processing_time)

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to process publish request"
            )


@router.get("/admin/queues", response_model=QueuesResponse, tags=["Admin"])
async def get_queue_status(request: Request, redis=Depends(get_redis_client), settings=Depends(get_settings)):
    """
    Queue monitoring endpoint.

    Returns status of all Celery queues for admin monitoring.
    """
    start_time = datetime.now()

    with with_logging_context(request_id=getattr(request.state, "request_id", None)):
        logger.info("Queue status requested")

        try:
            # Define queue names
            queue_names = ["ingest", "enrich", "captionize", "transcode", "preflight", "publish", "finalize"]

            queues = []
            total_pending = 0
            total_active = 0

            for queue_name in queue_names:
                try:
                    # Get queue length (pending tasks)
                    queue_size = await redis.llen(f"{queue_name}_queue")

                    # Get active tasks (placeholder - would query Celery)
                    active_tasks = 0  # Would get from Celery inspect

                    # Get failed tasks (placeholder)
                    failed_tasks = 0  # Would get from Celery inspect

                    # Get last task timestamp (placeholder)
                    last_task_timestamp = None

                    queues.append(
                        QueueInfo(
                            name=queue_name,
                            size=queue_size,
                            active_tasks=active_tasks,
                            failed_tasks=failed_tasks,
                            last_task_timestamp=last_task_timestamp,
                        )
                    )

                    total_pending += queue_size
                    total_active += active_tasks

                    # Update metrics
                    metrics.update_celery_queue_size(queue_name, queue_size)
                    metrics.update_active_celery_tasks(queue_name, active_tasks)

                except Exception as e:
                    logger.warning(f"Failed to get status for queue {queue_name}", error=str(e))

                    queues.append(
                        QueueInfo(
                            name=queue_name,
                            size=-1,  # Indicates error
                            active_tasks=-1,
                            failed_tasks=-1,
                            last_task_timestamp=None,
                        )
                    )

            # Determine system health
            if any(q.size == -1 for q in queues):
                system_health = "degraded"
            elif total_pending > 1000:  # Arbitrary threshold
                system_health = "overloaded"
            else:
                system_health = "healthy"

            # Track metrics
            processing_time = (datetime.now() - start_time).total_seconds()
            metrics.track_request("GET", "/admin/queues", 200, processing_time)

            logger.info(
                "Queue status retrieved",
                total_pending=total_pending,
                total_active=total_active,
                system_health=system_health,
                processing_time=processing_time,
            )

            return QueuesResponse(
                queues=queues, total_pending=total_pending, total_active=total_active, system_health=system_health
            )

        except Exception as e:
            processing_time = (datetime.now() - start_time).total_seconds()

            logger.error("Queue status retrieval failed", error=str(e), processing_time=processing_time, exc_info=True)

            metrics.track_request("GET", "/admin/queues", 500, processing_time)

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve queue status"
            )
