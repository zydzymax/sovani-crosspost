"""Token maintenance tasks for connected social accounts."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text

from ...core.config import settings
from ...core.logging import get_logger, with_logging_context
from ...core.security import decrypt_data, encrypt_data
from ...models.db import db_manager
from ..celery_app import celery

logger = get_logger("tasks.token_maintenance")

TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"


def _normalize_platform(value: Any) -> str:
    if value is None:
        return ""
    text_value = str(value).strip().lower()
    if "." in text_value:
        text_value = text_value.split(".")[-1]
    return text_value


def _decrypt_refresh_token(value: str | None) -> str:
    if not value:
        return ""
    try:
        return decrypt_data(value)
    except Exception:
        return value


def _refresh_tiktok_tokens(refresh_token: str) -> tuple[str, str | None, int]:
    response = httpx.post(
        TIKTOK_TOKEN_URL,
        data={
            "client_key": settings.tiktok_client_key,
            "client_secret": settings.tiktok_client_secret.get_secret_value(),
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30.0,
    )
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict) and payload.get("error"):
        message = payload.get("error_description") or payload.get("error") or "unknown_token_error"
        raise RuntimeError(f"TikTok token refresh failed: {message}")

    access_token = payload.get("access_token")
    if not access_token:
        raise RuntimeError("TikTok token refresh failed: access_token missing")
    expires_in = int(payload.get("expires_in") or 86400)
    return access_token, payload.get("refresh_token"), expires_in


@celery.task(bind=True, name="app.workers.tasks.token_maintenance.refresh_expiring_social_tokens")
def refresh_expiring_social_tokens(self, lookahead_hours: int = 24) -> dict[str, Any]:
    """
    Refresh expiring social tokens.

    Current automated refresh support:
    - TikTok: refresh_token flow
    """
    task_started_at = time.monotonic()
    expires_before = datetime.utcnow() + timedelta(hours=max(1, int(lookahead_hours)))

    with with_logging_context(task_id=self.request.id):
        logger.info("Starting social token maintenance", expires_before=expires_before.isoformat())

        refreshed = 0
        failed = 0
        skipped = 0
        warnings: list[str] = []

        with db_manager.get_sync_session() as session:
            rows = session.execute(
                text(
                    """
                    SELECT id, platform, refresh_token, token_expires_at
                    FROM social_accounts
                    WHERE is_active = true
                      AND token_expires_at IS NOT NULL
                      AND token_expires_at <= :expires_before
                    ORDER BY token_expires_at ASC
                    """
                ),
                {"expires_before": expires_before},
            ).fetchall()

            for row in rows:
                account_id = str(row[0])
                platform = _normalize_platform(row[1])
                refresh_token_raw = row[2]
                expires_at = row[3]

                if platform == "tiktok":
                    if not refresh_token_raw:
                        failed += 1
                        warnings.append(f"{platform}:{account_id}:missing_refresh_token")
                        logger.warning(
                            "token_refresh_skipped_missing_refresh_token",
                            platform=platform,
                            account_id=account_id,
                            token_expires_at=str(expires_at),
                        )
                        continue

                    try:
                        refresh_token = _decrypt_refresh_token(refresh_token_raw)
                        access_token, next_refresh_token, expires_in = _refresh_tiktok_tokens(refresh_token)

                        session.execute(
                            text(
                                """
                                UPDATE social_accounts
                                SET access_token = :access_token,
                                    refresh_token = :refresh_token,
                                    token_expires_at = :token_expires_at,
                                    updated_at = :updated_at
                                WHERE id = :account_id
                                """
                            ),
                            {
                                "account_id": account_id,
                                "access_token": encrypt_data(access_token),
                                "refresh_token": encrypt_data(next_refresh_token or refresh_token),
                                "token_expires_at": datetime.utcnow() + timedelta(seconds=expires_in),
                                "updated_at": datetime.utcnow(),
                            },
                        )
                        refreshed += 1
                        logger.info(
                            "token_refreshed",
                            platform=platform,
                            account_id=account_id,
                            expires_in=expires_in,
                        )
                    except Exception as e:
                        failed += 1
                        warnings.append(f"{platform}:{account_id}:{str(e)[:120]}")
                        logger.exception(
                            "token_refresh_failed",
                            platform=platform,
                            account_id=account_id,
                            error=str(e),
                        )
                    continue

                # Other platforms: currently monitor-only.
                skipped += 1
                warnings.append(f"{platform}:{account_id}:auto_refresh_not_implemented")
                logger.warning(
                    "token_refresh_not_implemented_for_platform",
                    platform=platform,
                    account_id=account_id,
                    token_expires_at=str(expires_at),
                )

        processing_time = time.monotonic() - task_started_at
        logger.info(
            "Social token maintenance completed",
            refreshed=refreshed,
            failed=failed,
            skipped=skipped,
            processing_time=processing_time,
        )
        return {
            "success": failed == 0,
            "refreshed": refreshed,
            "failed": failed,
            "skipped": skipped,
            "warnings": warnings,
            "processing_time": processing_time,
        }


def delay(*args, **kwargs):
    """Compatibility proxy for task.delay used by legacy tests."""
    return refresh_expiring_social_tokens.delay(*args, **kwargs)

