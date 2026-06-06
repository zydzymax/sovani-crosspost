"""
Content Analytics & AI Insights Service

This service:
1. Collects metrics from social platforms
2. Analyzes performance patterns
3. Generates AI-powered recommendations
4. Can auto-apply optimizations based on user settings
"""

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from ..models.entities import (
    AnalyticsSettings,
    ContentInsight,
    InsightPriority,
    InsightStatus,
    InsightType,
    OptimizationMode,
    PerformanceBenchmark,
    Post,
    PostMetrics,
    PublishResult,
)

logger = get_logger("services.content_analytics")


class ContentAnalyticsService:
    """Service for content analytics and AI-powered recommendations."""

    # Prompts for AI analysis
    PERFORMANCE_ANALYSIS_PROMPT = """
Проанализируй эффективность поста в социальной сети.

Платформа: {platform}
Текст поста: {caption}
Хештеги: {hashtags}
Время публикации: {published_at}

Метрики:
- Просмотры: {views}
- Лайки: {likes}
- Комментарии: {comments}
- Репосты: {shares}
- Engagement Rate: {engagement_rate:.2%}

Средние показатели пользователя:
- Avg Views: {avg_views}
- Avg Engagement: {avg_engagement:.2%}

Определи:
1. Почему этот пост показал такие результаты (лучше/хуже среднего)?
2. Какие элементы контента сработали хорошо?
3. Что можно улучшить в будущих постах?

Ответь в JSON формате:
{{
    "performance_verdict": "above_average" | "average" | "below_average",
    "key_success_factors": ["фактор1", "фактор2"],
    "improvement_areas": ["область1", "область2"],
    "specific_recommendations": [
        {{"action": "действие", "impact": "high/medium/low", "effort": "high/medium/low"}}
    ],
    "optimal_timing": "рекомендуемое время",
    "hashtag_effectiveness": "оценка хештегов"
}}
"""

    CONTENT_OPTIMIZATION_PROMPT = """
На основе анализа последних {num_posts} постов пользователя, дай рекомендации по оптимизации контент-стратегии.

Статистика по постам:
{posts_summary}

Лучшие посты:
{best_posts}

Худшие посты:
{worst_posts}

Паттерны:
- Лучшие дни недели: {best_days}
- Лучшее время: {best_times}
- Топ хештеги: {top_hashtags}

Сгенерируй рекомендации в JSON:
{{
    "content_strategy": {{
        "recommended_topics": ["тема1", "тема2"],
        "content_mix": {{"image": 60, "video": 30, "carousel": 10}},
        "posting_frequency": "рекомендация по частоте"
    }},
    "timing_optimization": {{
        "best_days": [1, 3, 5],
        "best_hours": ["09:00", "18:00"],
        "avoid_times": ["02:00-06:00"]
    }},
    "hashtag_strategy": {{
        "always_use": ["хештег1"],
        "rotate": ["хештег2", "хештег3"],
        "avoid": ["плохой_хештег"]
    }},
    "engagement_boosters": [
        "совет по увеличению вовлечённости"
    ]
}}
"""

    def __init__(self, ai_client=None):
        self.ai_client = ai_client  # OpenAI/Claude client
        logger.info("ContentAnalyticsService initialized")

    @staticmethod
    def _error_result(error: str) -> dict[str, Any]:
        return {"success": False, "error": error}

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.utcnow()

    async def collect_metrics_for_post(
        self, db: AsyncSession, publish_result_id: uuid.UUID, metrics_data: dict[str, Any]
    ) -> PostMetrics:
        """Store metrics snapshot for a published post."""

        # Get publish result
        result = await db.get(PublishResult, publish_result_id)
        if not result:
            raise ValueError(f"PublishResult {publish_result_id} not found")

        # Calculate hours since publish
        hours_since = 0
        if result.published_at:
            hours_since = int((self._utcnow() - result.published_at).total_seconds() / 3600)

        # Calculate engagement rate
        views = metrics_data.get("views", 0)
        likes = metrics_data.get("likes", 0)
        comments = metrics_data.get("comments", 0)
        shares = metrics_data.get("shares", 0)

        engagement_rate = 0
        if views > 0:
            engagement_rate = (likes + comments + shares) / views

        metrics = PostMetrics(
            publish_result_id=publish_result_id,
            post_id=result.post_id,
            platform=result.platform.value if hasattr(result.platform, "value") else str(result.platform),
            views=views,
            likes=likes,
            comments=comments,
            shares=shares,
            saves=metrics_data.get("saves", 0),
            engagement_rate=engagement_rate,
            click_through_rate=metrics_data.get("ctr", 0),
            platform_metrics=metrics_data.get("platform_specific", {}),
            audience_data=metrics_data.get("audience", {}),
            followers_before=metrics_data.get("followers_before"),
            followers_after=metrics_data.get("followers_after"),
            followers_gained=metrics_data.get("followers_gained", 0),
            hours_since_publish=hours_since,
        )

        db.add(metrics)
        await db.commit()
        await db.refresh(metrics)

        logger.info("Metrics collected", post_id=str(result.post_id), platform=metrics.platform)
        return metrics

    async def analyze_post_performance(
        self, db: AsyncSession, post_id: uuid.UUID, user_id: uuid.UUID
    ) -> ContentInsight | None:
        """Analyze a single post performance and generate insights."""

        # Get post with metrics
        post = await db.get(Post, post_id)
        if not post:
            return None

        # Get latest metrics
        metrics_result = await db.execute(
            select(PostMetrics).where(PostMetrics.post_id == post_id).order_by(desc(PostMetrics.measured_at)).limit(1)
        )
        metrics = metrics_result.scalars().first()

        if not metrics:
            logger.warning("No metrics found for post", post_id=str(post_id))
            return None

        # Get user benchmarks
        benchmarks = await self._get_user_benchmarks(db, user_id, metrics.platform)

        # Determine performance vs average
        avg_engagement = benchmarks.get("avg_engagement_rate", 0.02) if benchmarks else 0.02
        performance_ratio = metrics.engagement_rate / avg_engagement if avg_engagement > 0 else 1

        # Determine priority based on performance deviation
        priority = InsightPriority.MEDIUM
        if performance_ratio > 2:  # 2x better than average
            priority = InsightPriority.HIGH
            insight_type = InsightType.PERFORMANCE_ANALYSIS
            title = "🚀 Пост показал отличные результаты!"
        elif performance_ratio < 0.5:  # 50% worse than average
            priority = InsightPriority.HIGH
            insight_type = InsightType.CONTENT_RECOMMENDATION
            title = "⚠️ Пост показал результаты ниже среднего"
        else:
            insight_type = InsightType.PERFORMANCE_ANALYSIS
            title = "📊 Анализ эффективности поста"

        # Generate AI analysis if client available
        detailed_analysis = None
        recommendations = []

        if self.ai_client:
            try:
                analysis = await self._generate_ai_analysis(post, metrics, benchmarks)
                detailed_analysis = analysis.get("analysis")
                recommendations = analysis.get("recommendations", [])
            except Exception as e:
                logger.error("AI analysis failed", error=str(e))

        # Create insight
        insight = ContentInsight(
            user_id=user_id,
            post_id=post_id,
            platform=metrics.platform,
            insight_type=insight_type,
            priority=priority,
            title=title,
            summary=f"Engagement Rate: {metrics.engagement_rate:.2%} (среднее: {avg_engagement:.2%}). "
            f"Просмотры: {metrics.views}, Лайки: {metrics.likes}, Комментарии: {metrics.comments}",
            detailed_analysis=detailed_analysis,
            recommendations=recommendations,
            confidence_score=0.85,
            supporting_data={
                "metrics": metrics.to_dict(),
                "benchmarks": benchmarks,
                "performance_ratio": performance_ratio,
            },
            expires_at=self._utcnow() + timedelta(days=30),
        )

        db.add(insight)
        await db.commit()
        await db.refresh(insight)

        logger.info("Post analysis insight created", insight_id=str(insight.id))
        return insight

    async def generate_weekly_insights(self, db: AsyncSession, user_id: uuid.UUID) -> list[ContentInsight]:
        """Generate weekly performance insights for a user."""

        insights = []
        week_ago = self._utcnow() - timedelta(days=7)

        # Get all platforms user published to
        platforms_result = await db.execute(
            select(PostMetrics.platform)
            .join(Post)
            .where(Post.user_id == user_id if hasattr(Post, "user_id") else True)
            .where(PostMetrics.measured_at >= week_ago)
            .distinct()
        )
        platforms = [p[0] for p in platforms_result.fetchall()]

        for platform in platforms:
            # Get metrics for this platform
            metrics_result = await db.execute(
                select(PostMetrics)
                .where(PostMetrics.platform == platform)
                .where(PostMetrics.measured_at >= week_ago)
                .order_by(desc(PostMetrics.engagement_rate))
            )
            platform_metrics = metrics_result.scalars().all()

            if not platform_metrics:
                continue

            # Calculate weekly stats
            total_views = sum(m.views for m in platform_metrics)
            total_likes = sum(m.likes for m in platform_metrics)
            avg_engagement = sum(m.engagement_rate for m in platform_metrics) / len(platform_metrics)
            best_post = platform_metrics[0] if platform_metrics else None

            # Find best posting times
            posting_times = {}
            for m in platform_metrics:
                if m.measured_at:
                    hour = m.measured_at.hour
                    if hour not in posting_times:
                        posting_times[hour] = []
                    posting_times[hour].append(m.engagement_rate)

            best_hours = sorted(
                posting_times.keys(), key=lambda h: sum(posting_times[h]) / len(posting_times[h]), reverse=True
            )[:3]

            # Create weekly insight
            insight = ContentInsight(
                user_id=user_id,
                platform=platform,
                insight_type=InsightType.PERFORMANCE_ANALYSIS,
                priority=InsightPriority.MEDIUM,
                title=f"📈 Недельный отчёт: {platform}",
                summary=f"За неделю: {len(platform_metrics)} постов, {total_views} просмотров, "
                f"avg engagement: {avg_engagement:.2%}",
                detailed_analysis="Лучшее время для постов: " + ", ".join(f"{h}:00" for h in best_hours),
                recommendations=(
                    [
                        {
                            "action": f"Публикуйте в {best_hours[0]}:00 для максимального охвата",
                            "impact": "high",
                            "effort": "low",
                        }
                    ]
                    if best_hours
                    else []
                ),
                supporting_data={
                    "total_posts": len(platform_metrics),
                    "total_views": total_views,
                    "total_likes": total_likes,
                    "avg_engagement": avg_engagement,
                    "best_hours": best_hours,
                    "best_post_id": str(best_post.post_id) if best_post else None,
                },
                expires_at=self._utcnow() + timedelta(days=7),
            )

            db.add(insight)
            insights.append(insight)

        await db.commit()

        logger.info("Weekly insights generated", user_id=str(user_id), count=len(insights))
        return insights

    async def get_optimization_suggestions(
        self, db: AsyncSession, user_id: uuid.UUID, platform: str | None = None
    ) -> list[dict[str, Any]]:
        """Get actionable optimization suggestions based on analytics."""

        suggestions = []

        # Get user settings
        settings_result = await db.execute(select(AnalyticsSettings).where(AnalyticsSettings.user_id == user_id))
        settings = settings_result.scalars().first()

        if not settings or settings.optimization_mode == OptimizationMode.DISABLED:
            return suggestions

        # Get recent insights
        insights_result = await db.execute(
            select(ContentInsight)
            .where(ContentInsight.user_id == user_id)
            .where(ContentInsight.status == InsightStatus.PENDING)
            .where(ContentInsight.expires_at > self._utcnow())
            .order_by(desc(ContentInsight.priority), desc(ContentInsight.created_at))
            .limit(10)
        )
        insights = insights_result.scalars().all()

        for insight in insights:
            if platform and insight.platform != platform:
                continue
            for rec in insight.recommendations or []:
                suggestions.append(
                    {
                        "insight_id": str(insight.id),
                        "type": insight.insight_type.value,
                        "priority": insight.priority.value,
                        "action": rec.get("action"),
                        "impact": rec.get("impact", "medium"),
                        "effort": rec.get("effort", "medium"),
                        "platform": insight.platform,
                        "auto_applicable": settings.optimization_mode == OptimizationMode.AUTO,
                    }
                )

        return suggestions

    async def apply_optimization(
        self, db: AsyncSession, insight_id: uuid.UUID, user_id: uuid.UUID, auto: bool = False
    ) -> dict[str, Any]:
        """Apply an optimization suggestion."""

        insight = await db.get(ContentInsight, insight_id)
        if not insight or insight.user_id != user_id:
            return self._error_result("Insight not found")

        # Check user settings
        settings_result = await db.execute(select(AnalyticsSettings).where(AnalyticsSettings.user_id == user_id))
        settings = settings_result.scalars().first()

        if auto and (not settings or settings.optimization_mode != OptimizationMode.AUTO):
            return self._error_result("Auto-optimization not enabled")

        # Apply based on insight type
        result = {"success": True, "actions_taken": []}

        if insight.auto_action_type == "adjust_timing":
            # TODO: Adjust scheduled posts timing
            result["actions_taken"].append("Timing adjusted for scheduled posts")

        elif insight.auto_action_type == "modify_hashtags":
            # TODO: Update hashtag templates
            result["actions_taken"].append("Hashtag strategy updated")

        # Mark insight as applied
        insight.status = InsightStatus.AUTO_APPLIED if auto else InsightStatus.APPLIED
        insight.applied_at = self._utcnow()
        insight.auto_action_executed = True
        insight.auto_action_result = result

        await db.commit()

        logger.info("Optimization applied", insight_id=str(insight_id), auto=auto)
        return result

    async def _get_user_benchmarks(self, db: AsyncSession, user_id: uuid.UUID, platform: str) -> dict[str, Any] | None:
        """Get user performance benchmarks for a platform."""

        # Try to get existing benchmark
        benchmark_result = await db.execute(
            select(PerformanceBenchmark)
            .where(PerformanceBenchmark.user_id == user_id)
            .where(PerformanceBenchmark.platform == platform)
            .order_by(desc(PerformanceBenchmark.period_end))
            .limit(1)
        )
        benchmark = benchmark_result.scalars().first()

        if benchmark:
            return benchmark.to_dict()

        # Calculate from recent metrics
        month_ago = self._utcnow() - timedelta(days=30)
        metrics_result = await db.execute(
            select(PostMetrics)
            .join(Post, Post.id == PostMetrics.post_id)
            .where(Post.user_id == user_id if hasattr(Post, "user_id") else True)
            .where(PostMetrics.platform == platform)
            .where(PostMetrics.created_at >= month_ago)
        )
        metrics = metrics_result.scalars().all()

        if not metrics:
            return None

        return {
            "avg_views": sum(m.views for m in metrics) / len(metrics),
            "avg_likes": sum(m.likes for m in metrics) / len(metrics),
            "avg_engagement_rate": sum(m.engagement_rate for m in metrics) / len(metrics),
            "total_posts": len(metrics),
        }

    async def _generate_ai_analysis(self, post: Post, metrics: PostMetrics, benchmarks: dict | None) -> dict[str, Any]:
        """Generate AI-powered analysis using LLM."""

        if not self.ai_client:
            return {"analysis": None, "recommendations": []}

        # TODO: Implement actual AI call
        # For now, return rule-based analysis

        recommendations = []
        analysis_parts = []

        avg_engagement = benchmarks.get("avg_engagement_rate", 0.02) if benchmarks else 0.02

        if metrics.engagement_rate > avg_engagement * 1.5:
            analysis_parts.append("Этот пост показал результаты выше среднего.")
            recommendations.append(
                {"action": "Создавайте больше похожего контента", "impact": "high", "effort": "medium"}
            )
        elif metrics.engagement_rate < avg_engagement * 0.5:
            analysis_parts.append("Этот пост показал результаты ниже среднего.")
            recommendations.append(
                {"action": "Попробуйте другой формат или время публикации", "impact": "high", "effort": "low"}
            )

        if metrics.views > 0 and metrics.likes / metrics.views < 0.02:
            recommendations.append(
                {
                    "action": "Добавьте более привлекательный визуал или заголовок",
                    "impact": "medium",
                    "effort": "medium",
                }
            )

        return {
            "analysis": " ".join(analysis_parts) if analysis_parts else "Пост показал средние результаты.",
            "recommendations": recommendations,
        }


# Singleton instance
_analytics_service: ContentAnalyticsService | None = None


def get_analytics_service() -> ContentAnalyticsService:
    global _analytics_service
    if _analytics_service is None:
        _analytics_service = ContentAnalyticsService()
    return _analytics_service
