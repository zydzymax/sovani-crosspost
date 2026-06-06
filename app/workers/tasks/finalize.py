"""Finalize stage tasks for SalesWhisper Crosspost."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text

from ...core.logging import get_logger, with_logging_context
from ...models.db import db_manager
from ..celery_app import celery

logger = get_logger("tasks.finalize")


def _elapsed(start_time: float) -> float:
    return time.monotonic() - start_time


def _cleanup_temp_files_from_stage(stage_data: dict[str, Any]) -> int:
    removed = 0
    for file_path in stage_data.get("temp_files", []) or []:
        if not isinstance(file_path, str):
            continue
        try:
            if os.path.exists(file_path):
                os.unlink(file_path)
                removed += 1
        except Exception as e:
            logger.warning("temp_file_cleanup_failed", path=file_path, error=str(e))
    return removed


def _persist_publish_outcome(post_id: str, publish_results: dict[str, Any], final_status: str, error_message: str | None):
    published_at = datetime.utcnow() if final_status == "published" else None
    now = datetime.utcnow()

    with db_manager.get_sync_session() as session:
        session.execute(
            text(
                """
                UPDATE posts
                SET status = :status,
                    published_at = :published_at,
                    platform_captions = :platform_results,
                    error_message = :error_message,
                    updated_at = :updated_at
                WHERE id = :post_id
                """
            ),
            {
                "post_id": post_id,
                "status": final_status,
                "published_at": published_at,
                "platform_results": json.dumps(publish_results),
                "error_message": error_message,
                "updated_at": now,
            },
        )

        for platform, result in publish_results.items():
            session.execute(
                text(
                    """
                    INSERT INTO publish_results (
                        post_id, platform, account_id, success, platform_post_id, platform_post_url,
                        error_code, error_message, platform_response, published_at, created_at, updated_at
                    )
                    VALUES (
                        :post_id, :platform, :account_id, :success, :platform_post_id, :platform_post_url,
                        :error_code, :error_message, :platform_response, :published_at, :created_at, :updated_at
                    )
                    ON CONFLICT (post_id, platform)
                    DO UPDATE SET
                        account_id = EXCLUDED.account_id,
                        success = EXCLUDED.success,
                        platform_post_id = EXCLUDED.platform_post_id,
                        platform_post_url = EXCLUDED.platform_post_url,
                        error_code = EXCLUDED.error_code,
                        error_message = EXCLUDED.error_message,
                        platform_response = EXCLUDED.platform_response,
                        published_at = EXCLUDED.published_at,
                        updated_at = EXCLUDED.updated_at
                    """
                ),
                {
                    "post_id": post_id,
                    "platform": platform,
                    "account_id": result.get("account_id"),
                    "success": bool(result.get("success")),
                    "platform_post_id": result.get("platform_post_id"),
                    "platform_post_url": result.get("platform_url"),
                    "error_code": result.get("error_code"),
                    "error_message": result.get("error"),
                    "platform_response": json.dumps(result),
                    "published_at": datetime.utcfromtimestamp(result.get("published_at"))
                    if result.get("published_at")
                    else None,
                    "created_at": now,
                    "updated_at": now,
                },
            )


@celery.task(bind=True, name="app.workers.tasks.finalize.finalize_post")
def finalize_post(self, stage_data: dict[str, Any]) -> dict[str, Any]:
    """Finalize post processing, persist status and cleanup artifacts."""
    task_start_time = time.monotonic()
    post_id = stage_data["post_id"]

    with with_logging_context(task_id=self.request.id, post_id=post_id):
        logger.info("Starting post finalization", post_id=post_id)

        try:
            publish_results = stage_data.get("publish_results", {}) or {}
            successful_platforms = [p for p, r in publish_results.items() if r.get("success")]
            failed_platforms = [p for p, r in publish_results.items() if not r.get("success")]

            final_status = "published" if successful_platforms else "failed"
            error_message = None
            if not successful_platforms and failed_platforms:
                error_message = "; ".join(
                    f"{platform}: {publish_results[platform].get('error', 'publish_failed')}" for platform in failed_platforms
                )[:500]

            _persist_publish_outcome(post_id, publish_results, final_status, error_message)
            temp_files_cleaned = _cleanup_temp_files_from_stage(stage_data)

            processing_time = _elapsed(task_start_time)
            analytics_summary = {
                "total_processing_time": processing_time,
                "platforms_successful": len(successful_platforms),
                "platforms_failed": len(failed_platforms),
                "files_processed": stage_data.get("media_count", 0),
                "temp_files_cleaned": temp_files_cleaned,
                "failed_platforms": failed_platforms,
            }

            logger.info(
                "Post finalization completed",
                post_id=post_id,
                processing_time=processing_time,
                final_status=final_status,
                successful_platforms=len(successful_platforms),
                failed_platforms=len(failed_platforms),
            )

            return {
                "success": bool(successful_platforms),
                "post_id": post_id,
                "processing_time": processing_time,
                "final_status": final_status,
                "analytics_summary": analytics_summary,
                "stage": "completed",
            }

        except Exception as e:
            logger.exception("Post finalization failed", post_id=post_id, error=str(e))
            if self.request.retries < self.max_retries:
                raise self.retry(countdown=60 * (self.request.retries + 1))
            raise


@celery.task(bind=True, name="app.workers.tasks.finalize.cleanup_completed_tasks")
def cleanup_completed_tasks(self) -> dict[str, Any]:
    """Cleanup old temp media artifacts and report stale posts statistics."""
    task_start_time = time.monotonic()

    with with_logging_context(task_id=self.request.id):
        logger.info("Starting cleanup of completed tasks")

        try:
            threshold = datetime.utcnow() - timedelta(days=7)
            stale_posts = 0

            with db_manager.get_sync_session() as session:
                stale_posts = (
                    session.execute(
                        text(
                            """
                            SELECT COUNT(*)
                            FROM posts
                            WHERE status IN ('published', 'failed')
                              AND updated_at < :threshold
                            """
                        ),
                        {"threshold": threshold},
                    ).scalar()
                    or 0
                )

            cleaned_files = 0
            for file_path in Path("/tmp").glob("crosspost_media_*"):
                try:
                    if file_path.is_file() and file_path.stat().st_mtime < (time.time() - 3600):
                        file_path.unlink()
                        cleaned_files += 1
                except Exception:
                    logger.warning("tmp_file_cleanup_failed", path=str(file_path))

            processing_time = _elapsed(task_start_time)
            logger.info(
                "Cleanup completed",
                stale_posts=stale_posts,
                cleaned_files=cleaned_files,
                processing_time=processing_time,
            )

            return {
                "success": True,
                "processing_time": processing_time,
                "stale_posts": stale_posts,
                "cleaned_files": cleaned_files,
            }

        except Exception as e:
            logger.exception("Cleanup failed", error=str(e))
            raise


def delay(*args, **kwargs):
    """Compatibility proxy for task.delay used by legacy tests."""
    return finalize_post.delay(*args, **kwargs)
