"""
Scheduler service for SalesWhisper Crosspost.

This module provides:
- Content scheduling and queue management
- Celery Beat integration for periodic tasks
- Publishing window optimization
- Rate limiting and throttling
"""

import logging
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytz
import yaml
from celery import Celery
from celery.schedules import crontab

from ..core.config import settings
from ..models.entities import ContentQueue, Platform, Post, PostStatus, Schedule
from ..models.repositories import UnitOfWork

logger = logging.getLogger(__name__)


class PublishingWindowManager:
    """Manage optimal publishing windows based on platform rules."""

    def __init__(self, rules_path: str = "config/publishing_rules.yml"):
        self.rules = self._load_rules(rules_path)
        self.timezone = pytz.timezone(settings.app.brand_timezone)

    def _load_rules(self, path: str) -> dict[str, Any]:
        """Load publishing rules from YAML."""
        try:
            rules_file = Path(path)
            if rules_file.exists():
                with open(rules_file, encoding="utf-8") as f:
                    return yaml.safe_load(f)
        except Exception as e:
            logger.warning(f"Failed to load publishing rules: {e}")
        return {}

    def get_optimal_hours(self, platform: Platform) -> list[int]:
        """Get optimal posting hours for a platform."""
        business = self.rules.get("business", {})
        windows = business.get("posting_windows", {})
        platform_windows = windows.get(platform.value, {})
        return platform_windows.get("optimal_hours", [9, 12, 17, 20])

    def get_avoid_hours(self, platform: Platform) -> list[int]:
        """Get hours to avoid for a platform."""
        business = self.rules.get("business", {})
        windows = business.get("posting_windows", {})
        platform_windows = windows.get(platform.value, {})
        return platform_windows.get("avoid_hours", [1, 2, 3, 4, 5])

    def get_next_optimal_slot(
        self,
        platform: Platform,
        after: datetime = None,
        exclude_slots: list[datetime] = None
    ) -> datetime:
        """Get next optimal publishing slot for a platform."""
        if after is None:
            after = datetime.now(self.timezone)
        if exclude_slots is None:
            exclude_slots = []

        optimal_hours = self.get_optimal_hours(platform)
        avoid_hours = self.get_avoid_hours(platform)

        # Start from current time
        candidate = after.replace(minute=0, second=0, microsecond=0)

        # Search for next optimal slot
        max_search_days = 7
        for _ in range(max_search_days * 24):
            candidate = candidate + timedelta(hours=1)
            hour = candidate.hour

            # Check if hour is optimal
            if hour in optimal_hours and hour not in avoid_hours:
                # Add some randomness to minutes (0-30)
                candidate = candidate.replace(minute=random.randint(0, 30))

                # Check if slot is excluded
                if not any(
                    abs((candidate - exc).total_seconds()) < 3600
                    for exc in exclude_slots
                ):
                    return candidate

        # Fallback: next hour that's not in avoid list
        candidate = after + timedelta(hours=1)
        while candidate.hour in avoid_hours:
            candidate = candidate + timedelta(hours=1)
        return candidate.replace(minute=random.randint(0, 30))

    def is_within_posting_window(self, platform: Platform, dt: datetime = None) -> bool:
        """Check if given time is within optimal posting window."""
        if dt is None:
            dt = datetime.now(self.timezone)

        avoid_hours = self.get_avoid_hours(platform)
        return dt.hour not in avoid_hours


class ContentScheduler:
    """Main scheduler for content publishing."""

    def __init__(self):
        self.window_manager = PublishingWindowManager()
        self.max_posts_per_hour = 1
        self.max_posts_per_day = 10
        self.min_interval_minutes = 60

    def schedule_post(
        self,
        post_id: str,
        platforms: list[Platform],
        scheduled_at: datetime = None,
        use_optimal_window: bool = True
    ) -> dict[str, datetime]:
        """Schedule a post for publishing to specified platforms."""
        scheduled_slots = {}

        with UnitOfWork() as uow:
            # Get existing scheduled slots to avoid conflicts
            existing_scheduled = self._get_scheduled_slots(uow, platforms)

            for platform in platforms:
                if scheduled_at and not use_optimal_window:
                    slot = scheduled_at
                else:
                    # Find optimal slot
                    after = scheduled_at or datetime.now(self.window_manager.timezone)
                    slot = self.window_manager.get_next_optimal_slot(
                        platform=platform,
                        after=after,
                        exclude_slots=existing_scheduled.get(platform, [])
                    )

                # Add to queue
                uow.queue.enqueue(
                    post_id=post_id,
                    platform=platform,
                    scheduled_for=slot
                )
                scheduled_slots[platform.value] = slot

                # Update existing scheduled for next iteration
                if platform not in existing_scheduled:
                    existing_scheduled[platform] = []
                existing_scheduled[platform].append(slot)

            uow.commit()

        logger.info(f"Scheduled post {post_id} for {len(platforms)} platforms")
        return scheduled_slots

    def _get_scheduled_slots(
        self,
        uow: UnitOfWork,
        platforms: list[Platform]
    ) -> dict[Platform, list[datetime]]:
        """Get already scheduled slots for platforms."""
        result = {}
        for platform in platforms:
            items = uow.queue.session.query(ContentQueue).filter(
                ContentQueue.platform == platform,
                ContentQueue.status == "pending",
                ContentQueue.scheduled_for >= datetime.utcnow()
            ).all()
            result[platform] = [item.scheduled_for for item in items]
        return result

    def get_next_posts_to_publish(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get posts due for publishing."""
        with UnitOfWork() as uow:
            due_items = uow.queue.get_due_items(limit=limit)
            return [
                {
                    "queue_id": str(item.id),
                    "post_id": str(item.post_id),
                    "platform": item.platform.value,
                    "scheduled_for": item.scheduled_for,
                }
                for item in due_items
            ]

    def process_scheduled_queue(self) -> dict[str, int]:
        """Process scheduled queue and trigger publishing."""
        from ..workers.tasks.publish import publish_to_platforms

        stats = {"processed": 0, "failed": 0, "skipped": 0}

        with UnitOfWork() as uow:
            due_items = uow.queue.get_due_items(limit=self.max_posts_per_hour)

            for item in due_items:
                try:
                    # Check rate limits
                    if not self._check_rate_limits(uow, item.platform):
                        stats["skipped"] += 1
                        continue

                    # Mark as processing
                    uow.queue.mark_processing(item.id)
                    uow.commit()

                    # Get post data
                    post = uow.posts.get_with_media(item.post_id)
                    if not post:
                        logger.warning(f"Post {item.post_id} not found for queue item {item.id}")
                        uow.queue.mark_failed(item.id, "Post not found")
                        stats["failed"] += 1
                        continue

                    # Trigger publishing task
                    stage_data = {
                        "post_id": str(post.id),
                        "queue_id": str(item.id),
                        "platform": item.platform.value,
                        "caption": post.platform_captions.get(item.platform.value, post.generated_caption),
                        "media_paths": [
                            m.transcoded_paths.get(item.platform.value, m.original_path)
                            for m in post.media_assets
                        ],
                    }
                    publish_to_platforms.delay(stage_data)
                    stats["processed"] += 1

                except Exception as e:
                    logger.error(f"Failed to process queue item {item.id}: {e}")
                    uow.queue.mark_failed(item.id, str(e))
                    stats["failed"] += 1

            uow.commit()

        logger.info(f"Processed queue: {stats}")
        return stats

    def _check_rate_limits(self, uow: UnitOfWork, platform: Platform) -> bool:
        """Check if we're within rate limits for platform."""
        now = datetime.utcnow()
        hour_ago = now - timedelta(hours=1)
        day_ago = now - timedelta(hours=24)

        # Count posts in last hour
        hourly_count = uow.queue.session.query(ContentQueue).filter(
            ContentQueue.platform == platform,
            ContentQueue.status == "published",
            ContentQueue.published_at >= hour_ago
        ).count()

        if hourly_count >= self.max_posts_per_hour:
            logger.debug(f"Rate limit: {platform.value} hourly limit reached")
            return False

        # Count posts in last day
        daily_count = uow.queue.session.query(ContentQueue).filter(
            ContentQueue.platform == platform,
            ContentQueue.status == "published",
            ContentQueue.published_at >= day_ago
        ).count()

        if daily_count >= self.max_posts_per_day:
            logger.debug(f"Rate limit: {platform.value} daily limit reached")
            return False

        return True

    def reschedule_failed(self, max_items: int = 10) -> int:
        """Reschedule failed queue items."""
        rescheduled = 0

        with UnitOfWork() as uow:
            failed_items = uow.queue.session.query(ContentQueue).filter(
                ContentQueue.status == "failed",
                ContentQueue.attempts < 3
            ).limit(max_items).all()

            for item in failed_items:
                # Calculate next attempt time with exponential backoff
                backoff = timedelta(minutes=30 * (2 ** item.attempts))
                new_scheduled = datetime.utcnow() + backoff

                item.status = "pending"
                item.scheduled_for = new_scheduled
                item.error_message = None
                rescheduled += 1

            uow.commit()

        logger.info(f"Rescheduled {rescheduled} failed items")
        return rescheduled

    def get_queue_stats(self) -> dict[str, Any]:
        """Get current queue statistics."""
        with UnitOfWork() as uow:
            stats = uow.queue.get_queue_stats()

            # Add per-platform breakdown
            platforms_stats = {}
            for platform in Platform:
                count = uow.queue.session.query(ContentQueue).filter(
                    ContentQueue.platform == platform,
                    ContentQueue.status == "pending"
                ).count()
                platforms_stats[platform.value] = count

            stats["by_platform"] = platforms_stats
            return stats


class ScheduleRunner:
    """Run configured schedules."""

    def __init__(self):
        self.scheduler = ContentScheduler()

    def check_and_run_schedules(self) -> dict[str, int]:
        """Check all active schedules and queue content."""
        stats = {"checked": 0, "queued": 0}

        with UnitOfWork() as uow:
            due_schedules = uow.schedules.get_due_schedules()
            stats["checked"] = len(due_schedules)

            for schedule in due_schedules:
                try:
                    queued = self._process_schedule(uow, schedule)
                    stats["queued"] += queued

                    # Calculate next run time
                    next_run = self._calculate_next_run(schedule)
                    uow.schedules.update_last_run(schedule.id, next_run)

                except Exception as e:
                    logger.error(f"Failed to process schedule {schedule.id}: {e}")

            uow.commit()

        return stats

    def _process_schedule(self, uow: UnitOfWork, schedule: Schedule) -> int:
        """Process a single schedule."""
        # Get posts matching schedule filters
        query = uow.posts.session.query(Post).filter(
            Post.status.in_([PostStatus.PREFLIGHT, PostStatus.CAPTIONIZED]),
            not Post.is_scheduled
        )

        # Apply content filters
        if schedule.content_filter:
            filters = schedule.content_filter
            if "hashtags" in filters:
                query = query.filter(
                    Post.hashtags.overlap(filters["hashtags"])
                )
            if "media_types" in filters:
                # Filter by media type if needed
                pass

        posts = query.limit(schedule.max_posts_per_day).all()
        queued = 0

        for post in posts:
            platforms = [Platform(p) for p in schedule.platforms]

            # Get next publish time based on schedule
            scheduled_for = self._get_schedule_time(schedule)

            for platform in platforms:
                uow.queue.enqueue(
                    post_id=post.id,
                    platform=platform,
                    scheduled_for=scheduled_for,
                    schedule_id=schedule.id
                )
                queued += 1

            # Mark post as scheduled
            post.is_scheduled = True

        return queued

    def _get_schedule_time(self, schedule: Schedule) -> datetime:
        """Get next scheduled time based on schedule config."""
        tz = pytz.timezone(schedule.timezone)
        now = datetime.now(tz)

        if schedule.publish_times:
            # Find next publish time from list
            for time_str in sorted(schedule.publish_times):
                hour, minute = map(int, time_str.split(":"))
                scheduled = now.replace(hour=hour, minute=minute, second=0)
                if scheduled > now:
                    return scheduled

            # All times passed today, use first time tomorrow
            hour, minute = map(int, schedule.publish_times[0].split(":"))
            return (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0)

        # Default: next hour
        return now + timedelta(hours=1)

    def _calculate_next_run(self, schedule: Schedule) -> datetime:
        """Calculate next run time for schedule."""
        tz = pytz.timezone(schedule.timezone)
        now = datetime.now(tz)

        if schedule.cron_expression:
            # Parse cron expression (simplified)
            # Format: minute hour day_of_month month day_of_week
            parts = schedule.cron_expression.split()
            if len(parts) >= 2:
                minute = int(parts[0]) if parts[0] != "*" else 0
                hour = int(parts[1]) if parts[1] != "*" else now.hour + 1
                return now.replace(hour=hour, minute=minute, second=0) + timedelta(days=1)

        # Default: run every hour
        return now + timedelta(hours=1)


# Celery Beat schedule configuration
def get_celery_beat_schedule() -> dict[str, dict[str, Any]]:
    """Generate Celery Beat schedule from configuration."""
    return {
        "process-scheduled-queue": {
            "task": "app.workers.tasks.scheduler.process_scheduled_queue",
            "schedule": crontab(minute="*/5"),  # Every 5 minutes
            "options": {"queue": "publish"},
        },
        "check-schedules": {
            "task": "app.workers.tasks.scheduler.check_schedules",
            "schedule": crontab(minute="*/15"),  # Every 15 minutes
            "options": {"queue": "publish"},
        },
        "reschedule-failed": {
            "task": "app.workers.tasks.scheduler.reschedule_failed",
            "schedule": crontab(minute="0", hour="*/6"),  # Every 6 hours
            "options": {"queue": "finalize"},
        },
        "cleanup-old-queue-items": {
            "task": "app.workers.tasks.scheduler.cleanup_queue",
            "schedule": crontab(minute="0", hour="3"),  # 3 AM daily
            "options": {"queue": "finalize"},
        },
    }


# Celery tasks for scheduler
def register_scheduler_tasks(celery_app: Celery):
    """Register scheduler tasks with Celery."""

    @celery_app.task(name="app.workers.tasks.scheduler.process_scheduled_queue")
    def process_scheduled_queue():
        """Process scheduled content queue."""
        scheduler = ContentScheduler()
        return scheduler.process_scheduled_queue()

    @celery_app.task(name="app.workers.tasks.scheduler.check_schedules")
    def check_schedules():
        """Check and run due schedules."""
        runner = ScheduleRunner()
        return runner.check_and_run_schedules()

    @celery_app.task(name="app.workers.tasks.scheduler.reschedule_failed")
    def reschedule_failed():
        """Reschedule failed queue items."""
        scheduler = ContentScheduler()
        return scheduler.reschedule_failed()

    @celery_app.task(name="app.workers.tasks.scheduler.cleanup_queue")
    def cleanup_queue():
        """Clean up old queue items."""
        with UnitOfWork() as uow:
            cutoff = datetime.utcnow() - timedelta(days=30)
            deleted = uow.queue.session.query(ContentQueue).filter(
                ContentQueue.status.in_(["published", "cancelled"]),
                ContentQueue.created_at < cutoff
            ).delete()
            uow.commit()
            return {"deleted": deleted}


# Global scheduler instance
content_scheduler = ContentScheduler()
schedule_runner = ScheduleRunner()


__all__ = [
    "PublishingWindowManager",
    "ContentScheduler",
    "ScheduleRunner",
    "get_celery_beat_schedule",
    "register_scheduler_tasks",
    "content_scheduler",
    "schedule_runner",
]
