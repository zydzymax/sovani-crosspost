"""
Admin API for Fraud Monitoring.

Provides endpoints for:
- Viewing fraud events
- Managing blocked IPs
- Rate limit overrides
- Dashboard statistics
"""

from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from ..models.entities import BlockedIP, FraudEvent, FraudEventType, FraudRiskLevel, RateLimitOverride, User
from .deps import get_db_async_session, require_admin

logger = get_logger("api.admin_fraud")

router = APIRouter(prefix="/admin/fraud", tags=["Admin - Fraud"])


# ============ Pydantic Schemas ============

class FraudEventResponse(BaseModel):
    id: UUID
    event_type: str
    risk_level: str
    score: float
    user_id: UUID | None
    ip_address: str | None
    device_fingerprint: str | None
    details: dict | None
    created_at: datetime

    class Config:
        from_attributes = True


class FraudStatsResponse(BaseModel):
    total_events_24h: int
    high_risk_events_24h: int
    blocked_ips_count: int
    events_by_type: dict
    events_by_risk: dict
    top_ips: list[dict]
    hourly_trend: list[dict]


class BlockedIPCreate(BaseModel):
    ip_address: str
    reason: str
    expires_at: datetime | None = None


class BlockedIPResponse(BaseModel):
    id: UUID
    ip_address: str
    reason: str
    blocked_by: UUID | None
    blocked_at: datetime
    expires_at: datetime | None
    is_active: bool

    class Config:
        from_attributes = True


class RateLimitOverrideCreate(BaseModel):
    identifier: str
    identifier_type: str  # user_id, ip, api_key
    limit_per_minute: int
    limit_per_hour: int
    reason: str
    expires_at: datetime | None = None


class RateLimitOverrideResponse(BaseModel):
    id: UUID
    identifier: str
    identifier_type: str
    limit_per_minute: int
    limit_per_hour: int
    reason: str
    created_by: UUID | None
    created_at: datetime
    expires_at: datetime | None
    is_active: bool

    class Config:
        from_attributes = True


# ============ Fraud Events ============

@router.get("/events", response_model=list[FraudEventResponse])
async def list_fraud_events(
    event_type: FraudEventType | None = None,
    risk_level: FraudRiskLevel | None = None,
    ip_address: str | None = None,
    user_id: UUID | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db_async_session),
    admin: User = Depends(require_admin)
):
    """
    List fraud events with filtering.

    Admin only.
    """
    query = select(FraudEvent)

    # Apply filters
    conditions = []

    if event_type:
        conditions.append(FraudEvent.event_type == event_type)

    if risk_level:
        conditions.append(FraudEvent.risk_level == risk_level)

    if ip_address:
        conditions.append(FraudEvent.ip_address == ip_address)

    if user_id:
        conditions.append(FraudEvent.user_id == user_id)

    if start_date:
        conditions.append(FraudEvent.created_at >= start_date)

    if end_date:
        conditions.append(FraudEvent.created_at <= end_date)

    if conditions:
        query = query.where(and_(*conditions))

    # Order by newest first
    query = query.order_by(desc(FraudEvent.created_at))

    # Pagination
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    events = result.scalars().all()

    return [
        FraudEventResponse(
            id=e.id,
            event_type=e.event_type.value if hasattr(e.event_type, 'value') else str(e.event_type),
            risk_level=e.risk_level.value if hasattr(e.risk_level, 'value') else str(e.risk_level),
            score=e.score,
            user_id=e.user_id,
            ip_address=e.ip_address,
            device_fingerprint=e.device_fingerprint,
            details=e.details,
            created_at=e.created_at
        )
        for e in events
    ]


@router.get("/stats", response_model=FraudStatsResponse)
async def get_fraud_stats(
    db: AsyncSession = Depends(get_db_async_session),
    admin: User = Depends(require_admin)
):
    """
    Get fraud statistics for dashboard.

    Admin only.
    """
    now = datetime.utcnow()
    day_ago = now - timedelta(days=1)

    # Total events in 24h
    total_query = select(func.count()).select_from(FraudEvent).where(
        FraudEvent.created_at >= day_ago
    )
    total_result = await db.execute(total_query)
    total_events_24h = total_result.scalar() or 0

    # High risk events in 24h
    high_risk_query = select(func.count()).select_from(FraudEvent).where(
        and_(
            FraudEvent.created_at >= day_ago,
            FraudEvent.risk_level.in_([FraudRiskLevel.HIGH, FraudRiskLevel.CRITICAL])
        )
    )
    high_risk_result = await db.execute(high_risk_query)
    high_risk_events_24h = high_risk_result.scalar() or 0

    # Blocked IPs count
    blocked_query = select(func.count()).select_from(BlockedIP).where(
        BlockedIP.is_active
    )
    blocked_result = await db.execute(blocked_query)
    blocked_ips_count = blocked_result.scalar() or 0

    # Events by type (24h)
    type_query = select(
        FraudEvent.event_type,
        func.count().label('count')
    ).where(
        FraudEvent.created_at >= day_ago
    ).group_by(FraudEvent.event_type)

    type_result = await db.execute(type_query)
    events_by_type = {
        row.event_type.value if hasattr(row.event_type, 'value') else str(row.event_type): row.count
        for row in type_result
    }

    # Events by risk level (24h)
    risk_query = select(
        FraudEvent.risk_level,
        func.count().label('count')
    ).where(
        FraudEvent.created_at >= day_ago
    ).group_by(FraudEvent.risk_level)

    risk_result = await db.execute(risk_query)
    events_by_risk = {
        row.risk_level.value if hasattr(row.risk_level, 'value') else str(row.risk_level): row.count
        for row in risk_result
    }

    # Top IPs by fraud events (24h)
    top_ip_query = select(
        FraudEvent.ip_address,
        func.count().label('count'),
        func.max(FraudEvent.score).label('max_score')
    ).where(
        and_(
            FraudEvent.created_at >= day_ago,
            FraudEvent.ip_address.isnot(None)
        )
    ).group_by(FraudEvent.ip_address).order_by(
        desc(func.count())
    ).limit(10)

    top_ip_result = await db.execute(top_ip_query)
    top_ips = [
        {"ip": row.ip_address[:8] + "..." if row.ip_address else None, "count": row.count, "max_score": row.max_score}
        for row in top_ip_result
    ]

    # Hourly trend (24h)
    hourly_trend = []
    for i in range(24):
        hour_start = day_ago + timedelta(hours=i)
        hour_end = hour_start + timedelta(hours=1)

        hour_query = select(func.count()).select_from(FraudEvent).where(
            and_(
                FraudEvent.created_at >= hour_start,
                FraudEvent.created_at < hour_end
            )
        )
        hour_result = await db.execute(hour_query)
        count = hour_result.scalar() or 0

        hourly_trend.append({
            "hour": hour_start.strftime("%H:00"),
            "count": count
        })

    return FraudStatsResponse(
        total_events_24h=total_events_24h,
        high_risk_events_24h=high_risk_events_24h,
        blocked_ips_count=blocked_ips_count,
        events_by_type=events_by_type,
        events_by_risk=events_by_risk,
        top_ips=top_ips,
        hourly_trend=hourly_trend
    )


# ============ Blocked IPs ============

@router.get("/blocked-ips", response_model=list[BlockedIPResponse])
async def list_blocked_ips(
    active_only: bool = True,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db_async_session),
    admin: User = Depends(require_admin)
):
    """
    List blocked IPs.

    Admin only.
    """
    query = select(BlockedIP)

    if active_only:
        query = query.where(BlockedIP.is_active)

    query = query.order_by(desc(BlockedIP.blocked_at)).offset(offset).limit(limit)

    result = await db.execute(query)
    return result.scalars().all()


@router.post("/blocked-ips", response_model=BlockedIPResponse)
async def block_ip(
    data: BlockedIPCreate,
    db: AsyncSession = Depends(get_db_async_session),
    admin: User = Depends(require_admin)
):
    """
    Block an IP address.

    Admin only.
    """
    # Check if already blocked
    existing = await db.execute(
        select(BlockedIP).where(
            and_(
                BlockedIP.ip_address == data.ip_address,
                BlockedIP.is_active
            )
        )
    )

    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="IP is already blocked"
        )

    blocked_ip = BlockedIP(
        ip_address=data.ip_address,
        reason=data.reason,
        blocked_by=admin.id,
        expires_at=data.expires_at,
        is_active=True
    )

    db.add(blocked_ip)
    await db.commit()
    await db.refresh(blocked_ip)

    logger.info(
        "IP blocked by admin",
        ip=data.ip_address[:8] + "...",
        admin_id=str(admin.id),
        reason=data.reason
    )

    return blocked_ip


@router.delete("/blocked-ips/{blocked_id}")
async def unblock_ip(
    blocked_id: UUID,
    db: AsyncSession = Depends(get_db_async_session),
    admin: User = Depends(require_admin)
):
    """
    Unblock an IP address.

    Admin only.
    """
    result = await db.execute(
        select(BlockedIP).where(BlockedIP.id == blocked_id)
    )
    blocked_ip = result.scalar_one_or_none()

    if not blocked_ip:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Blocked IP not found"
        )

    blocked_ip.is_active = False
    await db.commit()

    logger.info(
        "IP unblocked by admin",
        ip=blocked_ip.ip_address[:8] + "...",
        admin_id=str(admin.id)
    )

    return {"status": "success", "message": "IP unblocked"}


# ============ Rate Limit Overrides ============

@router.get("/rate-limits", response_model=list[RateLimitOverrideResponse])
async def list_rate_limit_overrides(
    active_only: bool = True,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db_async_session),
    admin: User = Depends(require_admin)
):
    """
    List rate limit overrides.

    Admin only.
    """
    query = select(RateLimitOverride)

    if active_only:
        query = query.where(RateLimitOverride.is_active)

    query = query.order_by(desc(RateLimitOverride.created_at)).offset(offset).limit(limit)

    result = await db.execute(query)
    return result.scalars().all()


@router.post("/rate-limits", response_model=RateLimitOverrideResponse)
async def create_rate_limit_override(
    data: RateLimitOverrideCreate,
    db: AsyncSession = Depends(get_db_async_session),
    admin: User = Depends(require_admin)
):
    """
    Create a rate limit override.

    Use to increase or decrease limits for specific users/IPs.
    Admin only.
    """
    # Check if override already exists
    existing = await db.execute(
        select(RateLimitOverride).where(
            and_(
                RateLimitOverride.identifier == data.identifier,
                RateLimitOverride.identifier_type == data.identifier_type,
                RateLimitOverride.is_active
            )
        )
    )

    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Active rate limit override already exists for this identifier"
        )

    override = RateLimitOverride(
        identifier=data.identifier,
        identifier_type=data.identifier_type,
        limit_per_minute=data.limit_per_minute,
        limit_per_hour=data.limit_per_hour,
        reason=data.reason,
        created_by=admin.id,
        expires_at=data.expires_at,
        is_active=True
    )

    db.add(override)
    await db.commit()
    await db.refresh(override)

    logger.info(
        "Rate limit override created",
        identifier=data.identifier[:16] + "..." if len(data.identifier) > 16 else data.identifier,
        type=data.identifier_type,
        admin_id=str(admin.id)
    )

    return override


@router.delete("/rate-limits/{override_id}")
async def delete_rate_limit_override(
    override_id: UUID,
    db: AsyncSession = Depends(get_db_async_session),
    admin: User = Depends(require_admin)
):
    """
    Delete a rate limit override.

    Admin only.
    """
    result = await db.execute(
        select(RateLimitOverride).where(RateLimitOverride.id == override_id)
    )
    override = result.scalar_one_or_none()

    if not override:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rate limit override not found"
        )

    override.is_active = False
    await db.commit()

    logger.info(
        "Rate limit override deleted",
        override_id=str(override_id),
        admin_id=str(admin.id)
    )

    return {"status": "success", "message": "Rate limit override deleted"}


# ============ Manual Actions ============

@router.post("/check-user/{user_id}")
async def check_user_fraud_risk(
    user_id: UUID,
    db: AsyncSession = Depends(get_db_async_session),
    admin: User = Depends(require_admin)
):
    """
    Manually check fraud risk for a user.

    Admin only.
    """
    # Get user
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    # Get fraud history
    events_result = await db.execute(
        select(FraudEvent)
        .where(FraudEvent.user_id == user_id)
        .order_by(desc(FraudEvent.created_at))
        .limit(20)
    )
    events = events_result.scalars().all()

    # Calculate risk metrics
    total_events = len(events)
    high_risk_events = sum(1 for e in events if e.risk_level in [FraudRiskLevel.HIGH, FraudRiskLevel.CRITICAL])
    avg_score = sum(e.score for e in events) / total_events if total_events > 0 else 0

    return {
        "user_id": str(user_id),
        "total_fraud_events": total_events,
        "high_risk_events": high_risk_events,
        "average_score": round(avg_score, 2),
        "recent_events": [
            {
                "type": e.event_type.value if hasattr(e.event_type, 'value') else str(e.event_type),
                "risk": e.risk_level.value if hasattr(e.risk_level, 'value') else str(e.risk_level),
                "score": e.score,
                "created_at": e.created_at.isoformat()
            }
            for e in events[:10]
        ],
        "recommendation": (
            "block" if high_risk_events > 3 or avg_score > 0.7
            else "monitor" if high_risk_events > 0 or avg_score > 0.4
            else "ok"
        )
    }
