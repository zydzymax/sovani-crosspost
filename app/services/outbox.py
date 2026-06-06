"""Outbox storage helpers for reliable event processing."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

OUTBOX_STATUSES = ("pending", "processed", "failed")


def _payload_hash(payload: dict[str, Any]) -> str:
    normalized = json.dumps(payload or {}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def ensure_outbox_table(session: Session) -> None:
    """Create outbox table if it does not exist."""
    session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS outbox_events (
                id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                correlation_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                retry_count INTEGER NOT NULL DEFAULT 0,
                max_retries INTEGER NOT NULL DEFAULT 5,
                next_attempt_at TIMESTAMP NOT NULL DEFAULT NOW(),
                last_error TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                processed_at TIMESTAMP
            )
            """
        )
    )
    session.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_outbox_pending
            ON outbox_events (status, next_attempt_at, created_at)
            """
        )
    )
    session.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_outbox_dedupe
            ON outbox_events (event_type, entity_id, payload_hash)
            WHERE status IN ('pending', 'processed')
            """
        )
    )


def find_duplicate_event(session: Session, event_type: str, entity_id: str, payload: dict[str, Any]) -> str | None:
    """Return existing event id if dedupe match exists."""
    dedupe_hash = _payload_hash(payload)
    row = session.execute(
        text(
            """
            SELECT id
            FROM outbox_events
            WHERE event_type = :event_type
              AND entity_id = :entity_id
              AND payload_hash = :payload_hash
              AND status IN ('pending', 'processed')
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"event_type": event_type, "entity_id": entity_id, "payload_hash": dedupe_hash},
    ).fetchone()
    return str(row[0]) if row else None


def insert_outbox_event(
    session: Session,
    event_id: str,
    event_type: str,
    entity_id: str,
    payload: dict[str, Any],
    correlation_id: str | None = None,
    max_retries: int = 5,
) -> None:
    """Insert outbox event in pending status."""
    payload_text = json.dumps(payload or {}, ensure_ascii=False)
    session.execute(
        text(
            """
            INSERT INTO outbox_events (
                id, event_type, entity_id, payload, payload_hash, correlation_id,
                status, retry_count, max_retries, next_attempt_at, created_at, updated_at
            )
            VALUES (
                :id, :event_type, :entity_id, :payload, :payload_hash, :correlation_id,
                'pending', 0, :max_retries, NOW(), NOW(), NOW()
            )
            """
        ),
        {
            "id": event_id,
            "event_type": event_type,
            "entity_id": entity_id,
            "payload": payload_text,
            "payload_hash": _payload_hash(payload),
            "correlation_id": correlation_id,
            "max_retries": max_retries,
        },
    )


def get_pending_events(session: Session, limit: int = 100) -> list[dict[str, Any]]:
    """Load pending outbox events ready for processing."""
    rows = session.execute(
        text(
            """
            SELECT id, event_type, payload, retry_count, max_retries, correlation_id
            FROM outbox_events
            WHERE status = 'pending'
              AND next_attempt_at <= NOW()
            ORDER BY created_at
            LIMIT :limit
            """
        ),
        {"limit": limit},
    ).fetchall()

    events: list[dict[str, Any]] = []
    for row in rows:
        events.append(
            {
                "id": str(row[0]),
                "event_type": str(row[1]),
                "payload": row[2],
                "retry_count": int(row[3] or 0),
                "max_retries": int(row[4] or 5),
                "correlation_id": row[5],
            }
        )
    return events


def mark_processed(session: Session, event_id: str) -> None:
    session.execute(
        text(
            """
            UPDATE outbox_events
            SET status = 'processed',
                processed_at = NOW(),
                updated_at = NOW(),
                last_error = NULL
            WHERE id = :event_id
            """
        ),
        {"event_id": event_id},
    )


def schedule_retry(
    session: Session,
    event_id: str,
    retry_count: int,
    max_retries: int,
    error_message: str | None,
) -> None:
    if retry_count >= max_retries:
        session.execute(
            text(
                """
                UPDATE outbox_events
                SET status = 'failed',
                    retry_count = :retry_count,
                    last_error = :last_error,
                    updated_at = NOW()
                WHERE id = :event_id
                """
            ),
            {"event_id": event_id, "retry_count": retry_count, "last_error": (error_message or "")[:1000]},
        )
        return

    backoff_seconds = min(60 * (2**retry_count), 3600)
    next_attempt = datetime.utcnow() + timedelta(seconds=backoff_seconds)
    session.execute(
        text(
            """
            UPDATE outbox_events
            SET retry_count = :retry_count,
                next_attempt_at = :next_attempt_at,
                last_error = :last_error,
                updated_at = NOW()
            WHERE id = :event_id
            """
        ),
        {
            "event_id": event_id,
            "retry_count": retry_count,
            "next_attempt_at": next_attempt,
            "last_error": (error_message or "")[:1000],
        },
    )
