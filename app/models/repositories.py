"""
Repository classes for SalesWhisper Crosspost.

This module provides:
- CRUD operations for all entities
- Query builders and filters
- Transaction management
- Async and sync interfaces
"""

import uuid
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Type, TypeVar, Generic
from contextlib import contextmanager

from sqlalchemy import select, update, delete, func, and_, or_
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from .db import Base, db_manager
from .entities import (
    Post, PostStatus, MediaAsset, MediaType,
    SocialAccount, Platform, PublishTask, TaskStage, TaskStatus,
    PublishResult, Schedule, ContentQueue
)


T = TypeVar("T", bound=Base)


class BaseRepository(Generic[T]):
    """Base repository with common CRUD operations."""

    model_class: Type[T]

    def __init__(self, session: Session):
        self.session = session

    def create(self, **kwargs) -> T:
        """Create a new entity."""
        entity = self.model_class(**kwargs)
        self.session.add(entity)
        self.session.flush()
        return entity

    def get_by_id(self, entity_id: uuid.UUID) -> Optional[T]:
        """Get entity by ID."""
        return self.session.query(self.model_class).filter(
            self.model_class.id == entity_id
        ).first()

    def get_all(self, limit: int = 100, offset: int = 0) -> List[T]:
        """Get all entities with pagination."""
        return self.session.query(self.model_class).limit(limit).offset(offset).all()

    def update(self, entity_id: uuid.UUID, **kwargs) -> Optional[T]:
        """Update entity by ID."""
        entity = self.get_by_id(entity_id)
        if entity:
            for key, value in kwargs.items():
                if hasattr(entity, key):
                    setattr(entity, key, value)
            entity.updated_at = datetime.utcnow()
            self.session.flush()
        return entity

    def delete(self, entity_id: uuid.UUID) -> bool:
        """Delete entity by ID."""
        entity = self.get_by_id(entity_id)
        if entity:
            self.session.delete(entity)
            self.session.flush()
            return True
        return False

    def count(self) -> int:
        """Count total entities."""
        return self.session.query(func.count(self.model_class.id)).scalar()


class PostRepository(BaseRepository[Post]):
    """Repository for Post entities."""

    model_class = Post

    def create_post(
        self,
        source_platform: Platform,
        original_text: Optional[str] = None,
        source_message_id: Optional[str] = None,
        source_chat_id: Optional[str] = None,
        source_user_id: Optional[str] = None,
        source_data: Optional[Dict] = None,
    ) -> Post:
        """Create a new post."""
        return self.create(
            source_platform=source_platform,
            original_text=original_text,
            source_message_id=source_message_id,
            source_chat_id=source_chat_id,
            source_user_id=source_user_id,
            source_data=source_data,
            status=PostStatus.INGESTED,
        )

    def get_by_status(self, status: PostStatus, limit: int = 100) -> List[Post]:
        """Get posts by status."""
        return self.session.query(Post).filter(
            Post.status == status
        ).order_by(Post.created_at.desc()).limit(limit).all()

    def get_pending_for_publish(self, limit: int = 10) -> List[Post]:
        """Get posts ready for publishing."""
        return self.session.query(Post).filter(
            Post.status == PostStatus.PREFLIGHT
        ).order_by(Post.created_at.asc()).limit(limit).all()

    def get_scheduled_posts(self, until: datetime = None) -> List[Post]:
        """Get scheduled posts up to given time."""
        query = self.session.query(Post).filter(
            Post.is_scheduled == True,
            Post.status.in_([PostStatus.PREFLIGHT, PostStatus.CAPTIONIZED])
        )
        if until:
            query = query.filter(Post.scheduled_at <= until)
        return query.order_by(Post.scheduled_at.asc()).all()

    def update_status(self, post_id: uuid.UUID, status: PostStatus, error_message: str = None) -> Optional[Post]:
        """Update post status."""
        return self.update(
            post_id,
            status=status,
            error_message=error_message,
            updated_at=datetime.utcnow()
        )

    def update_caption(
        self,
        post_id: uuid.UUID,
        generated_caption: str,
        platform_captions: Dict[str, str] = None,
        hashtags: List[str] = None
    ) -> Optional[Post]:
        """Update post caption."""
        kwargs = {"generated_caption": generated_caption}
        if platform_captions:
            kwargs["platform_captions"] = platform_captions
        if hashtags:
            kwargs["hashtags"] = hashtags
        return self.update(post_id, **kwargs)

    def mark_published(self, post_id: uuid.UUID) -> Optional[Post]:
        """Mark post as published."""
        return self.update(
            post_id,
            status=PostStatus.PUBLISHED,
            published_at=datetime.utcnow()
        )

    def get_with_media(self, post_id: uuid.UUID) -> Optional[Post]:
        """Get post with media assets loaded."""
        return self.session.query(Post).options(
            selectinload(Post.media_assets)
        ).filter(Post.id == post_id).first()

    def get_recent_posts(self, hours: int = 24, limit: int = 50) -> List[Post]:
        """Get recent posts."""
        since = datetime.utcnow() - timedelta(hours=hours)
        return self.session.query(Post).filter(
            Post.created_at >= since
        ).order_by(Post.created_at.desc()).limit(limit).all()

    def search_posts(
        self,
        text_query: Optional[str] = None,
        platform: Optional[Platform] = None,
        status: Optional[PostStatus] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        limit: int = 50
    ) -> List[Post]:
        """Search posts with filters."""
        query = self.session.query(Post)

        if text_query:
            query = query.filter(
                or_(
                    Post.original_text.ilike(f"%{text_query}%"),
                    Post.generated_caption.ilike(f"%{text_query}%")
                )
            )
        if platform:
            query = query.filter(Post.source_platform == platform)
        if status:
            query = query.filter(Post.status == status)
        if from_date:
            query = query.filter(Post.created_at >= from_date)
        if to_date:
            query = query.filter(Post.created_at <= to_date)

        return query.order_by(Post.created_at.desc()).limit(limit).all()


class MediaAssetRepository(BaseRepository[MediaAsset]):
    """Repository for MediaAsset entities."""

    model_class = MediaAsset

    def create_media(
        self,
        post_id: uuid.UUID,
        media_type: MediaType,
        original_file_id: Optional[str] = None,
        file_name: Optional[str] = None,
        file_size: Optional[int] = None,
        mime_type: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        duration: Optional[float] = None,
        original_path: Optional[str] = None,
    ) -> MediaAsset:
        """Create a new media asset."""
        return self.create(
            post_id=post_id,
            media_type=media_type,
            original_file_id=original_file_id,
            file_name=file_name,
            file_size=file_size,
            mime_type=mime_type,
            width=width,
            height=height,
            duration=duration,
            original_path=original_path,
        )

    def get_by_post(self, post_id: uuid.UUID) -> List[MediaAsset]:
        """Get all media assets for a post."""
        return self.session.query(MediaAsset).filter(
            MediaAsset.post_id == post_id
        ).all()

    def update_transcode_path(
        self,
        media_id: uuid.UUID,
        platform: Platform,
        transcoded_path: str
    ) -> Optional[MediaAsset]:
        """Update transcoded path for a platform."""
        media = self.get_by_id(media_id)
        if media:
            if media.transcoded_paths is None:
                media.transcoded_paths = {}
            media.transcoded_paths[platform.value] = transcoded_path
            self.session.flush()
        return media

    def update_transcode_status(
        self,
        media_id: uuid.UUID,
        platform: Platform,
        status: str
    ) -> Optional[MediaAsset]:
        """Update transcode status for a platform."""
        media = self.get_by_id(media_id)
        if media:
            if media.transcode_status is None:
                media.transcode_status = {}
            media.transcode_status[platform.value] = status
            self.session.flush()
        return media


class SocialAccountRepository(BaseRepository[SocialAccount]):
    """Repository for SocialAccount entities."""

    model_class = SocialAccount

    def create_account(
        self,
        platform: Platform,
        platform_user_id: str,
        platform_username: Optional[str] = None,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        token_expires_at: Optional[datetime] = None,
        extra_credentials: Optional[Dict] = None,
    ) -> SocialAccount:
        """Create a new social account."""
        return self.create(
            platform=platform,
            platform_user_id=platform_user_id,
            platform_username=platform_username,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=token_expires_at,
            extra_credentials=extra_credentials or {},
        )

    def get_by_platform(self, platform: Platform, active_only: bool = True) -> List[SocialAccount]:
        """Get accounts by platform."""
        query = self.session.query(SocialAccount).filter(
            SocialAccount.platform == platform
        )
        if active_only:
            query = query.filter(SocialAccount.is_active == True)
        return query.all()

    def get_active_for_publishing(self, platform: Platform) -> Optional[SocialAccount]:
        """Get active account for publishing."""
        return self.session.query(SocialAccount).filter(
            SocialAccount.platform == platform,
            SocialAccount.is_active == True,
            SocialAccount.publish_enabled == True
        ).order_by(SocialAccount.publish_priority.desc()).first()

    def get_by_platform_user(self, platform: Platform, platform_user_id: str) -> Optional[SocialAccount]:
        """Get account by platform and user ID."""
        return self.session.query(SocialAccount).filter(
            SocialAccount.platform == platform,
            SocialAccount.platform_user_id == platform_user_id
        ).first()

    def update_tokens(
        self,
        account_id: uuid.UUID,
        access_token: str,
        refresh_token: Optional[str] = None,
        token_expires_at: Optional[datetime] = None
    ) -> Optional[SocialAccount]:
        """Update OAuth tokens."""
        return self.update(
            account_id,
            access_token=access_token,
            refresh_token=refresh_token or None,
            token_expires_at=token_expires_at
        )

    def get_expiring_tokens(self, hours: int = 24) -> List[SocialAccount]:
        """Get accounts with tokens expiring soon."""
        expires_before = datetime.utcnow() + timedelta(hours=hours)
        return self.session.query(SocialAccount).filter(
            SocialAccount.is_active == True,
            SocialAccount.token_expires_at != None,
            SocialAccount.token_expires_at <= expires_before
        ).all()


class PublishTaskRepository(BaseRepository[PublishTask]):
    """Repository for PublishTask entities."""

    model_class = PublishTask

    def create_task(
        self,
        post_id: uuid.UUID,
        stage: TaskStage,
        celery_task_id: Optional[str] = None,
        input_data: Optional[Dict] = None,
    ) -> PublishTask:
        """Create a new publish task."""
        return self.create(
            post_id=post_id,
            stage=stage,
            celery_task_id=celery_task_id,
            input_data=input_data,
            status=TaskStatus.PENDING,
        )

    def get_by_post(self, post_id: uuid.UUID) -> List[PublishTask]:
        """Get all tasks for a post."""
        return self.session.query(PublishTask).filter(
            PublishTask.post_id == post_id
        ).order_by(PublishTask.created_at.asc()).all()

    def get_by_celery_id(self, celery_task_id: str) -> Optional[PublishTask]:
        """Get task by Celery task ID."""
        return self.session.query(PublishTask).filter(
            PublishTask.celery_task_id == celery_task_id
        ).first()

    def start_task(self, task_id: uuid.UUID, celery_task_id: str = None) -> Optional[PublishTask]:
        """Mark task as started."""
        kwargs = {
            "status": TaskStatus.RUNNING,
            "started_at": datetime.utcnow()
        }
        if celery_task_id:
            kwargs["celery_task_id"] = celery_task_id
        return self.update(task_id, **kwargs)

    def complete_task(
        self,
        task_id: uuid.UUID,
        output_data: Optional[Dict] = None,
        processing_time: Optional[float] = None
    ) -> Optional[PublishTask]:
        """Mark task as completed."""
        return self.update(
            task_id,
            status=TaskStatus.COMPLETED,
            completed_at=datetime.utcnow(),
            output_data=output_data,
            processing_time=processing_time
        )

    def fail_task(
        self,
        task_id: uuid.UUID,
        error_message: str,
        error_traceback: Optional[str] = None
    ) -> Optional[PublishTask]:
        """Mark task as failed."""
        task = self.get_by_id(task_id)
        if task:
            task.status = TaskStatus.FAILED
            task.completed_at = datetime.utcnow()
            task.error_message = error_message
            task.error_traceback = error_traceback
            task.retry_count += 1
            self.session.flush()
        return task

    def get_failed_tasks(self, limit: int = 50) -> List[PublishTask]:
        """Get failed tasks for retry."""
        return self.session.query(PublishTask).filter(
            PublishTask.status == TaskStatus.FAILED,
            PublishTask.retry_count < PublishTask.max_retries
        ).order_by(PublishTask.updated_at.asc()).limit(limit).all()


class PublishResultRepository(BaseRepository[PublishResult]):
    """Repository for PublishResult entities."""

    model_class = PublishResult

    def create_result(
        self,
        post_id: uuid.UUID,
        platform: Platform,
        success: bool,
        account_id: Optional[uuid.UUID] = None,
        platform_post_id: Optional[str] = None,
        platform_post_url: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        platform_response: Optional[Dict] = None,
    ) -> PublishResult:
        """Create a publish result."""
        return self.create(
            post_id=post_id,
            platform=platform,
            success=success,
            account_id=account_id,
            platform_post_id=platform_post_id,
            platform_post_url=platform_post_url,
            error_code=error_code,
            error_message=error_message,
            platform_response=platform_response,
            published_at=datetime.utcnow() if success else None,
        )

    def get_by_post(self, post_id: uuid.UUID) -> List[PublishResult]:
        """Get all results for a post."""
        return self.session.query(PublishResult).filter(
            PublishResult.post_id == post_id
        ).all()

    def get_by_post_and_platform(self, post_id: uuid.UUID, platform: Platform) -> Optional[PublishResult]:
        """Get result for specific post and platform."""
        return self.session.query(PublishResult).filter(
            PublishResult.post_id == post_id,
            PublishResult.platform == platform
        ).first()

    def get_recent_failures(self, platform: Platform = None, hours: int = 24) -> List[PublishResult]:
        """Get recent failures."""
        since = datetime.utcnow() - timedelta(hours=hours)
        query = self.session.query(PublishResult).filter(
            PublishResult.success == False,
            PublishResult.created_at >= since
        )
        if platform:
            query = query.filter(PublishResult.platform == platform)
        return query.order_by(PublishResult.created_at.desc()).all()

    def get_success_rate(self, platform: Platform, days: int = 7) -> float:
        """Calculate success rate for platform."""
        since = datetime.utcnow() - timedelta(days=days)
        total = self.session.query(func.count(PublishResult.id)).filter(
            PublishResult.platform == platform,
            PublishResult.created_at >= since
        ).scalar()
        if total == 0:
            return 0.0
        success = self.session.query(func.count(PublishResult.id)).filter(
            PublishResult.platform == platform,
            PublishResult.success == True,
            PublishResult.created_at >= since
        ).scalar()
        return success / total


class ScheduleRepository(BaseRepository[Schedule]):
    """Repository for Schedule entities."""

    model_class = Schedule

    def create_schedule(
        self,
        name: str,
        platforms: List[str],
        cron_expression: Optional[str] = None,
        publish_times: Optional[List[str]] = None,
        days_of_week: Optional[List[int]] = None,
        timezone: str = "Europe/Moscow",
        max_posts_per_day: int = 10,
    ) -> Schedule:
        """Create a new schedule."""
        return self.create(
            name=name,
            platforms=platforms,
            cron_expression=cron_expression,
            publish_times=publish_times,
            days_of_week=days_of_week,
            timezone=timezone,
            max_posts_per_day=max_posts_per_day,
        )

    def get_active_schedules(self) -> List[Schedule]:
        """Get all active schedules."""
        return self.session.query(Schedule).filter(
            Schedule.is_active == True
        ).all()

    def get_due_schedules(self, now: datetime = None) -> List[Schedule]:
        """Get schedules due to run."""
        if now is None:
            now = datetime.utcnow()
        return self.session.query(Schedule).filter(
            Schedule.is_active == True,
            or_(
                Schedule.next_run_at == None,
                Schedule.next_run_at <= now
            )
        ).all()

    def update_last_run(self, schedule_id: uuid.UUID, next_run_at: datetime = None) -> Optional[Schedule]:
        """Update last run time."""
        return self.update(
            schedule_id,
            last_run_at=datetime.utcnow(),
            next_run_at=next_run_at
        )


class ContentQueueRepository(BaseRepository[ContentQueue]):
    """Repository for ContentQueue entities."""

    model_class = ContentQueue

    def enqueue(
        self,
        post_id: uuid.UUID,
        platform: Platform,
        scheduled_for: datetime,
        schedule_id: Optional[uuid.UUID] = None,
        priority: int = 0,
    ) -> ContentQueue:
        """Add content to queue."""
        return self.create(
            post_id=post_id,
            platform=platform,
            scheduled_for=scheduled_for,
            schedule_id=schedule_id,
            priority=priority,
            status="pending",
        )

    def get_due_items(self, platform: Platform = None, limit: int = 10) -> List[ContentQueue]:
        """Get items due for publishing."""
        now = datetime.utcnow()
        query = self.session.query(ContentQueue).filter(
            ContentQueue.status == "pending",
            ContentQueue.scheduled_for <= now
        )
        if platform:
            query = query.filter(ContentQueue.platform == platform)
        return query.order_by(
            ContentQueue.priority.desc(),
            ContentQueue.scheduled_for.asc()
        ).limit(limit).all()

    def mark_processing(self, queue_id: uuid.UUID) -> Optional[ContentQueue]:
        """Mark item as processing."""
        item = self.get_by_id(queue_id)
        if item:
            item.status = "processing"
            item.attempts += 1
            item.last_attempt_at = datetime.utcnow()
            self.session.flush()
        return item

    def mark_published(self, queue_id: uuid.UUID) -> Optional[ContentQueue]:
        """Mark item as published."""
        return self.update(
            queue_id,
            status="published",
            published_at=datetime.utcnow()
        )

    def mark_failed(self, queue_id: uuid.UUID, error_message: str) -> Optional[ContentQueue]:
        """Mark item as failed."""
        return self.update(
            queue_id,
            status="failed",
            error_message=error_message
        )

    def get_queue_stats(self) -> Dict[str, Any]:
        """Get queue statistics."""
        pending = self.session.query(func.count(ContentQueue.id)).filter(
            ContentQueue.status == "pending"
        ).scalar()
        processing = self.session.query(func.count(ContentQueue.id)).filter(
            ContentQueue.status == "processing"
        ).scalar()
        failed = self.session.query(func.count(ContentQueue.id)).filter(
            ContentQueue.status == "failed"
        ).scalar()
        return {
            "pending": pending,
            "processing": processing,
            "failed": failed,
            "total": pending + processing + failed
        }


# Unit of Work pattern
class UnitOfWork:
    """Unit of work for managing transactions."""

    def __init__(self):
        self._session: Optional[Session] = None

    def __enter__(self):
        self._session = db_manager.sync_session_factory()
        self.posts = PostRepository(self._session)
        self.media = MediaAssetRepository(self._session)
        self.accounts = SocialAccountRepository(self._session)
        self.tasks = PublishTaskRepository(self._session)
        self.results = PublishResultRepository(self._session)
        self.schedules = ScheduleRepository(self._session)
        self.queue = ContentQueueRepository(self._session)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self._session.rollback()
        else:
            self._session.commit()
        self._session.close()

    def commit(self):
        """Commit current transaction."""
        self._session.commit()

    def rollback(self):
        """Rollback current transaction."""
        self._session.rollback()


# Export
__all__ = [
    "BaseRepository",
    "PostRepository",
    "MediaAssetRepository",
    "SocialAccountRepository",
    "PublishTaskRepository",
    "PublishResultRepository",
    "ScheduleRepository",
    "ContentQueueRepository",
    "UnitOfWork",
]
