"""
Analytics API - Content performance tracking and AI recommendations.
"""
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.entities import (
    AnalyticsSettings,
    ContentInsight,
    InsightStatus,
    OptimizationMode,
    PostMetrics,
    User,
)
from ..services.content_analytics import get_analytics_service
from .deps import get_current_user, get_db_async_session

router = APIRouter(prefix="/analytics", tags=["analytics"])


# === Request/Response Models ===

class MetricsInput(BaseModel):
    publish_result_id: str
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    ctr: float = 0
    platform_specific: dict = Field(default_factory=dict)
    audience: dict = Field(default_factory=dict)
    followers_before: int | None = None
    followers_after: int | None = None
    followers_gained: int = 0


class MetricsResponse(BaseModel):
    id: str
    post_id: str
    platform: str
    views: int
    likes: int
    comments: int
    shares: int
    engagement_rate: float
    followers_gained: int
    measured_at: str


class InsightResponse(BaseModel):
    id: str
    post_id: str | None
    platform: str | None
    insight_type: str
    priority: str
    status: str
    title: str
    summary: str
    detailed_analysis: str | None
    recommendations: list
    confidence_score: float
    created_at: str
    expires_at: str | None


class SettingsInput(BaseModel):
    optimization_mode: str = "hints_only"
    collect_metrics: bool = True
    auto_adjust_timing: bool = False
    auto_optimize_hashtags: bool = False
    auto_suggest_topics: bool = True
    notify_on_viral: bool = True
    notify_weekly_report: bool = True


class SettingsResponse(BaseModel):
    optimization_mode: str
    collect_metrics: bool
    auto_adjust_timing: bool
    auto_optimize_hashtags: bool
    auto_suggest_topics: bool
    notify_on_viral: bool
    notify_weekly_report: bool


class DashboardStats(BaseModel):
    total_posts: int
    total_views: int
    total_likes: int
    avg_engagement_rate: float
    best_platform: str | None
    best_posting_time: str | None
    pending_insights: int
    weekly_growth: float


# === Endpoints ===

@router.post("/metrics", response_model=MetricsResponse)
async def submit_metrics(
    data: MetricsInput,
    db: AsyncSession = Depends(get_db_async_session),
    current_user: User = Depends(get_current_user)
):
    """Submit metrics for a published post."""
    try:
        service = get_analytics_service()
        metrics = await service.collect_metrics_for_post(
            db,
            uuid.UUID(data.publish_result_id),
            data.dict()
        )
        return MetricsResponse(
            id=str(metrics.id),
            post_id=str(metrics.post_id),
            platform=metrics.platform,
            views=metrics.views,
            likes=metrics.likes,
            comments=metrics.comments,
            shares=metrics.shares,
            engagement_rate=metrics.engagement_rate,
            followers_gained=metrics.followers_gained,
            measured_at=metrics.measured_at.isoformat()
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/metrics/{post_id}", response_model=list[MetricsResponse])
async def get_post_metrics(
    post_id: str,
    db: AsyncSession = Depends(get_db_async_session),
    current_user: User = Depends(get_current_user)
):
    """Get all metrics snapshots for a post."""
    result = await db.execute(
        select(PostMetrics)
        .where(PostMetrics.post_id == uuid.UUID(post_id))
        .order_by(desc(PostMetrics.measured_at))
    )
    metrics_list = result.scalars().all()

    return [
        MetricsResponse(
            id=str(m.id),
            post_id=str(m.post_id),
            platform=m.platform,
            views=m.views,
            likes=m.likes,
            comments=m.comments,
            shares=m.shares,
            engagement_rate=m.engagement_rate or 0,
            followers_gained=m.followers_gained or 0,
            measured_at=m.measured_at.isoformat() if m.measured_at else ""
        )
        for m in metrics_list
    ]


@router.post("/analyze/{post_id}", response_model=InsightResponse)
async def analyze_post(
    post_id: str,
    db: AsyncSession = Depends(get_db_async_session),
    current_user: User = Depends(get_current_user)
):
    """Trigger AI analysis for a specific post."""
    service = get_analytics_service()
    insight = await service.analyze_post_performance(
        db,
        uuid.UUID(post_id),
        current_user.id
    )

    if not insight:
        raise HTTPException(status_code=404, detail="No metrics found for post")

    return InsightResponse(
        id=str(insight.id),
        post_id=str(insight.post_id) if insight.post_id else None,
        platform=insight.platform,
        insight_type=insight.insight_type.value,
        priority=insight.priority.value,
        status=insight.status.value,
        title=insight.title,
        summary=insight.summary,
        detailed_analysis=insight.detailed_analysis,
        recommendations=insight.recommendations or [],
        confidence_score=insight.confidence_score or 0.8,
        created_at=insight.created_at.isoformat() if insight.created_at else "",
        expires_at=insight.expires_at.isoformat() if insight.expires_at else None
    )


@router.get("/insights", response_model=list[InsightResponse])
async def get_insights(
    status: str | None = Query(None, description="Filter by status"),
    platform: str | None = Query(None, description="Filter by platform"),
    limit: int = Query(20, le=100),
    db: AsyncSession = Depends(get_db_async_session),
    current_user: User = Depends(get_current_user)
):
    """Get all insights for current user."""
    query = select(ContentInsight).where(ContentInsight.user_id == current_user.id)

    if status:
        query = query.where(ContentInsight.status == InsightStatus(status))
    if platform:
        query = query.where(ContentInsight.platform == platform)

    query = query.order_by(
        desc(ContentInsight.priority),
        desc(ContentInsight.created_at)
    ).limit(limit)

    result = await db.execute(query)
    insights = result.scalars().all()

    return [
        InsightResponse(
            id=str(i.id),
            post_id=str(i.post_id) if i.post_id else None,
            platform=i.platform,
            insight_type=i.insight_type.value,
            priority=i.priority.value,
            status=i.status.value,
            title=i.title,
            summary=i.summary,
            detailed_analysis=i.detailed_analysis,
            recommendations=i.recommendations or [],
            confidence_score=i.confidence_score or 0.8,
            created_at=i.created_at.isoformat() if i.created_at else "",
            expires_at=i.expires_at.isoformat() if i.expires_at else None
        )
        for i in insights
    ]


@router.post("/insights/{insight_id}/apply")
async def apply_insight(
    insight_id: str,
    db: AsyncSession = Depends(get_db_async_session),
    current_user: User = Depends(get_current_user)
):
    """Apply an insight recommendation."""
    service = get_analytics_service()
    result = await service.apply_optimization(
        db,
        uuid.UUID(insight_id),
        current_user.id,
        auto=False
    )
    return result


@router.post("/insights/{insight_id}/dismiss")
async def dismiss_insight(
    insight_id: str,
    db: AsyncSession = Depends(get_db_async_session),
    current_user: User = Depends(get_current_user)
):
    """Dismiss an insight."""
    insight = await db.get(ContentInsight, uuid.UUID(insight_id))
    if not insight or insight.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Insight not found")

    insight.status = InsightStatus.DISMISSED
    await db.commit()

    return {"status": "dismissed"}


@router.post("/insights/{insight_id}/feedback")
async def submit_feedback(
    insight_id: str,
    feedback: str = Query(..., description="helpful or not_helpful"),
    db: AsyncSession = Depends(get_db_async_session),
    current_user: User = Depends(get_current_user)
):
    """Submit feedback for an insight."""
    insight = await db.get(ContentInsight, uuid.UUID(insight_id))
    if not insight or insight.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Insight not found")

    insight.user_feedback = feedback
    await db.commit()

    return {"status": "feedback_recorded", "feedback": feedback}


@router.get("/settings", response_model=SettingsResponse)
async def get_settings(
    db: AsyncSession = Depends(get_db_async_session),
    current_user: User = Depends(get_current_user)
):
    """Get user analytics settings."""
    result = await db.execute(
        select(AnalyticsSettings).where(AnalyticsSettings.user_id == current_user.id)
    )
    settings = result.scalars().first()

    if not settings:
        # Return defaults
        return SettingsResponse(
            optimization_mode="hints_only",
            collect_metrics=True,
            auto_adjust_timing=False,
            auto_optimize_hashtags=False,
            auto_suggest_topics=True,
            notify_on_viral=True,
            notify_weekly_report=True
        )

    return SettingsResponse(
        optimization_mode=settings.optimization_mode.value,
        collect_metrics=settings.collect_metrics,
        auto_adjust_timing=settings.auto_adjust_timing,
        auto_optimize_hashtags=settings.auto_optimize_hashtags,
        auto_suggest_topics=settings.auto_suggest_topics,
        notify_on_viral=settings.notify_on_viral,
        notify_weekly_report=settings.notify_weekly_report
    )


@router.put("/settings", response_model=SettingsResponse)
async def update_settings(
    data: SettingsInput,
    db: AsyncSession = Depends(get_db_async_session),
    current_user: User = Depends(get_current_user)
):
    """Update user analytics settings."""
    result = await db.execute(
        select(AnalyticsSettings).where(AnalyticsSettings.user_id == current_user.id)
    )
    settings = result.scalars().first()

    if not settings:
        settings = AnalyticsSettings(user_id=current_user.id)
        db.add(settings)

    settings.optimization_mode = OptimizationMode(data.optimization_mode)
    settings.collect_metrics = data.collect_metrics
    settings.auto_adjust_timing = data.auto_adjust_timing
    settings.auto_optimize_hashtags = data.auto_optimize_hashtags
    settings.auto_suggest_topics = data.auto_suggest_topics
    settings.notify_on_viral = data.notify_on_viral
    settings.notify_weekly_report = data.notify_weekly_report

    await db.commit()
    await db.refresh(settings)

    return SettingsResponse(
        optimization_mode=settings.optimization_mode.value,
        collect_metrics=settings.collect_metrics,
        auto_adjust_timing=settings.auto_adjust_timing,
        auto_optimize_hashtags=settings.auto_optimize_hashtags,
        auto_suggest_topics=settings.auto_suggest_topics,
        notify_on_viral=settings.notify_on_viral,
        notify_weekly_report=settings.notify_weekly_report
    )


@router.get("/dashboard", response_model=DashboardStats)
async def get_dashboard_stats(
    days: int = Query(30, le=90, description="Period in days"),
    db: AsyncSession = Depends(get_db_async_session),
    current_user: User = Depends(get_current_user)
):
    """Get dashboard statistics for the analytics page."""
    since = datetime.utcnow() - timedelta(days=days)

    # Get metrics aggregates
    metrics_result = await db.execute(
        select(
            func.count(PostMetrics.id).label("total_posts"),
            func.sum(PostMetrics.views).label("total_views"),
            func.sum(PostMetrics.likes).label("total_likes"),
            func.avg(PostMetrics.engagement_rate).label("avg_engagement")
        )
        .where(PostMetrics.created_at >= since)
    )
    row = metrics_result.fetchone()

    # Get best platform
    platform_result = await db.execute(
        select(
            PostMetrics.platform,
            func.avg(PostMetrics.engagement_rate).label("avg_eng")
        )
        .where(PostMetrics.created_at >= since)
        .group_by(PostMetrics.platform)
        .order_by(desc("avg_eng"))
        .limit(1)
    )
    best_platform_row = platform_result.fetchone()

    # Get pending insights count
    insights_result = await db.execute(
        select(func.count(ContentInsight.id))
        .where(ContentInsight.user_id == current_user.id)
        .where(ContentInsight.status == InsightStatus.PENDING)
    )
    pending_count = insights_result.scalar() or 0

    # Calculate weekly growth (simplified)
    week_ago = datetime.utcnow() - timedelta(days=7)
    two_weeks_ago = datetime.utcnow() - timedelta(days=14)

    this_week_result = await db.execute(
        select(func.sum(PostMetrics.views))
        .where(PostMetrics.created_at >= week_ago)
    )
    last_week_result = await db.execute(
        select(func.sum(PostMetrics.views))
        .where(PostMetrics.created_at >= two_weeks_ago)
        .where(PostMetrics.created_at < week_ago)
    )

    this_week = this_week_result.scalar() or 0
    last_week = last_week_result.scalar() or 1
    weekly_growth = ((this_week - last_week) / last_week) * 100 if last_week > 0 else 0

    return DashboardStats(
        total_posts=row.total_posts or 0 if row else 0,
        total_views=int(row.total_views or 0) if row else 0,
        total_likes=int(row.total_likes or 0) if row else 0,
        avg_engagement_rate=float(row.avg_engagement or 0) if row else 0,
        best_platform=best_platform_row[0] if best_platform_row else None,
        best_posting_time=None,  # TODO: Calculate from metrics
        pending_insights=pending_count,
        weekly_growth=round(weekly_growth, 1)
    )


@router.post("/generate-weekly-report")
async def generate_weekly_report(
    db: AsyncSession = Depends(get_db_async_session),
    current_user: User = Depends(get_current_user)
):
    """Generate weekly insights report."""
    service = get_analytics_service()
    insights = await service.generate_weekly_insights(db, current_user.id)

    return {
        "status": "generated",
        "insights_count": len(insights),
        "insight_ids": [str(i.id) for i in insights]
    }


@router.get("/suggestions")
async def get_optimization_suggestions(
    platform: str | None = None,
    db: AsyncSession = Depends(get_db_async_session),
    current_user: User = Depends(get_current_user)
):
    """Get actionable optimization suggestions."""
    service = get_analytics_service()
    suggestions = await service.get_optimization_suggestions(
        db, current_user.id, platform
    )
    return {"suggestions": suggestions}
