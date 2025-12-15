"""
SQLAlchemy models for SoVAni Crosspost.

This module defines all database entities:
- Post: Main content entity
- MediaAsset: Attached media files
- SocialAccount: Connected social media accounts
- PublishTask: Publishing task tracking
- Schedule: Scheduled publishing rules
- PublishResult: Results of publishing attempts
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any

from sqlalchemy import (
    Column, String, Integer, BigInteger, Text, Boolean, DateTime,
    ForeignKey, JSON, Enum as SQLEnum, Float, Index, UniqueConstraint
)
from sqlalchemy.orm import relationship, Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY

from .db import Base


class PostStatus(str, Enum):
    """Post processing status."""
    DRAFT = "draft"
    INGESTED = "ingested"
    ENRICHED = "enriched"
    CAPTIONIZED = "captionized"
    TRANSCODED = "transcoded"
    PREFLIGHT = "preflight"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    ARCHIVED = "archived"


class MediaType(str, Enum):
    """Media file types."""
    IMAGE = "image"
    VIDEO = "video"
    ANIMATION = "animation"
    AUDIO = "audio"
    DOCUMENT = "document"


class Platform(str, Enum):
    """Supported social media platforms."""
    TELEGRAM = "telegram"
    VK = "vk"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"
    YOUTUBE = "youtube"
    FACEBOOK = "facebook"
    RUTUBE = "rutube"


class TaskStage(str, Enum):
    """Celery task stages."""
    INGEST = "ingest"
    ENRICH = "enrich"
    CAPTIONIZE = "captionize"
    TRANSCODE = "transcode"
    PREFLIGHT = "preflight"
    PUBLISH = "publish"
    FINALIZE = "finalize"


class TaskStatus(str, Enum):
    """Task execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    SKIPPED = "skipped"


class Post(Base):
    """Main content post entity."""

    __tablename__ = "posts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Source information
    source_platform = Column(SQLEnum(Platform), nullable=False, index=True)
    source_message_id = Column(String(255), nullable=True)
    source_chat_id = Column(String(255), nullable=True)
    source_user_id = Column(String(255), nullable=True)

    # Content
    original_text = Column(Text, nullable=True)
    generated_caption = Column(Text, nullable=True)
    hashtags = Column(ARRAY(String), nullable=True)

    # AI-generated content per platform
    platform_captions = Column(JSONB, nullable=True, default=dict)
    # Format: {"instagram": "caption", "vk": "caption", ...}

    # Processing status
    status = Column(SQLEnum(PostStatus), default=PostStatus.DRAFT, index=True)
    current_stage = Column(SQLEnum(TaskStage), nullable=True)

    # Metadata from enrichment
    enrichment_data = Column(JSONB, nullable=True, default=dict)
    # Contains: sentiment, keywords, topics, etc.

    # Original Telegram update for reference
    source_data = Column(JSONB, nullable=True)

    # Scheduling
    scheduled_at = Column(DateTime, nullable=True, index=True)
    is_scheduled = Column(Boolean, default=False)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    published_at = Column(DateTime, nullable=True)

    # Error tracking
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)

    # Relationships
    media_assets = relationship("MediaAsset", back_populates="post", cascade="all, delete-orphan")
    publish_tasks = relationship("PublishTask", back_populates="post", cascade="all, delete-orphan")
    publish_results = relationship("PublishResult", back_populates="post", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_posts_status_scheduled", "status", "scheduled_at"),
        Index("ix_posts_source", "source_platform", "source_chat_id"),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "source_platform": self.source_platform.value if self.source_platform else None,
            "original_text": self.original_text,
            "generated_caption": self.generated_caption,
            "hashtags": self.hashtags,
            "status": self.status.value if self.status else None,
            "scheduled_at": self.scheduled_at.isoformat() if self.scheduled_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "media_count": len(self.media_assets) if self.media_assets else 0,
        }


class MediaAsset(Base):
    """Media file attached to a post."""

    __tablename__ = "media_assets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id = Column(UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)

    # File identification
    original_file_id = Column(String(255), nullable=True)  # Telegram file_id
    file_name = Column(String(500), nullable=True)

    # File type
    media_type = Column(SQLEnum(MediaType), nullable=False)
    mime_type = Column(String(100), nullable=True)

    # File size and dimensions
    file_size = Column(BigInteger, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    duration = Column(Float, nullable=True)  # For video/audio in seconds

    # Storage paths
    original_path = Column(String(1000), nullable=True)  # S3 path to original
    transcoded_paths = Column(JSONB, nullable=True, default=dict)
    # Format: {"instagram": "s3://...", "vk": "s3://...", "tiktok": "s3://..."}

    thumbnail_path = Column(String(1000), nullable=True)

    # Processing metadata
    processing_metadata = Column(JSONB, nullable=True, default=dict)
    # Contains: codec info, bitrate, color profile, etc.

    # Transcoding status per platform
    transcode_status = Column(JSONB, nullable=True, default=dict)
    # Format: {"instagram": "completed", "vk": "pending", ...}

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    post = relationship("Post", back_populates="media_assets")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "media_type": self.media_type.value if self.media_type else None,
            "file_name": self.file_name,
            "file_size": self.file_size,
            "width": self.width,
            "height": self.height,
            "duration": self.duration,
            "mime_type": self.mime_type,
        }


class SocialAccount(Base):
    """Connected social media account."""

    __tablename__ = "social_accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Platform info
    platform = Column(SQLEnum(Platform), nullable=False, index=True)
    platform_user_id = Column(String(255), nullable=False)
    platform_username = Column(String(255), nullable=True)
    platform_display_name = Column(String(500), nullable=True)

    # OAuth tokens (encrypted)
    access_token = Column(Text, nullable=True)  # Encrypted
    refresh_token = Column(Text, nullable=True)  # Encrypted
    token_expires_at = Column(DateTime, nullable=True)

    # Additional credentials
    extra_credentials = Column(JSONB, nullable=True, default=dict)
    # For platform-specific: page_id, group_id, etc.

    # Account status
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    last_verified_at = Column(DateTime, nullable=True)

    # Configuration
    publish_enabled = Column(Boolean, default=True)
    publish_priority = Column(Integer, default=0)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("platform", "platform_user_id", name="uq_social_account_platform_user"),
        Index("ix_social_accounts_active", "platform", "is_active"),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary (without sensitive data)."""
        return {
            "id": str(self.id),
            "platform": self.platform.value if self.platform else None,
            "platform_username": self.platform_username,
            "platform_display_name": self.platform_display_name,
            "is_active": self.is_active,
            "publish_enabled": self.publish_enabled,
            "token_expires_at": self.token_expires_at.isoformat() if self.token_expires_at else None,
        }


class PublishTask(Base):
    """Track publishing task through pipeline stages."""

    __tablename__ = "publish_tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id = Column(UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)

    # Task tracking
    celery_task_id = Column(String(255), nullable=True, index=True)
    stage = Column(SQLEnum(TaskStage), nullable=False)
    status = Column(SQLEnum(TaskStatus), default=TaskStatus.PENDING)

    # Timing
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    processing_time = Column(Float, nullable=True)  # in seconds

    # Retry tracking
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    next_retry_at = Column(DateTime, nullable=True)

    # Result data
    input_data = Column(JSONB, nullable=True)
    output_data = Column(JSONB, nullable=True)
    error_message = Column(Text, nullable=True)
    error_traceback = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    post = relationship("Post", back_populates="publish_tasks")

    __table_args__ = (
        Index("ix_publish_tasks_status_stage", "status", "stage"),
        Index("ix_publish_tasks_celery", "celery_task_id"),
    )


class PublishResult(Base):
    """Result of publishing to a specific platform."""

    __tablename__ = "publish_results"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id = Column(UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)

    # Platform info
    platform = Column(SQLEnum(Platform), nullable=False, index=True)
    account_id = Column(UUID(as_uuid=True), ForeignKey("social_accounts.id"), nullable=True)

    # Result
    success = Column(Boolean, default=False)
    platform_post_id = Column(String(255), nullable=True)
    platform_post_url = Column(String(1000), nullable=True)

    # Error handling
    error_code = Column(String(50), nullable=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)

    # Platform response
    platform_response = Column(JSONB, nullable=True)

    # Timestamps
    published_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    post = relationship("Post", back_populates="publish_results")
    account = relationship("SocialAccount")

    __table_args__ = (
        UniqueConstraint("post_id", "platform", name="uq_publish_result_post_platform"),
        Index("ix_publish_results_platform_success", "platform", "success"),
    )


class Schedule(Base):
    """Publishing schedule configuration."""

    __tablename__ = "schedules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Schedule name
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    # Target platforms
    platforms = Column(ARRAY(String), nullable=False)

    # Cron expression
    cron_expression = Column(String(100), nullable=True)
    # Alternative: specific times
    publish_times = Column(ARRAY(String), nullable=True)  # ["09:00", "12:00", "18:00"]

    # Days of week (0=Monday, 6=Sunday)
    days_of_week = Column(ARRAY(Integer), nullable=True)

    # Timezone
    timezone = Column(String(50), default="Europe/Moscow")

    # Queue settings
    max_posts_per_day = Column(Integer, default=10)
    min_interval_minutes = Column(Integer, default=60)

    # Status
    is_active = Column(Boolean, default=True)

    # Content filter
    content_filter = Column(JSONB, nullable=True, default=dict)
    # Format: {"hashtags": ["promo"], "exclude_hashtags": ["draft"], "media_types": ["video"]}

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "name": self.name,
            "platforms": self.platforms,
            "cron_expression": self.cron_expression,
            "publish_times": self.publish_times,
            "timezone": self.timezone,
            "is_active": self.is_active,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
        }


class ContentQueue(Base):
    """Queue for scheduled content."""

    __tablename__ = "content_queue"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id = Column(UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    schedule_id = Column(UUID(as_uuid=True), ForeignKey("schedules.id", ondelete="SET NULL"), nullable=True)

    # Target
    platform = Column(SQLEnum(Platform), nullable=False)

    # Scheduling
    scheduled_for = Column(DateTime, nullable=False, index=True)
    priority = Column(Integer, default=0)  # Higher = more priority

    # Status
    status = Column(String(50), default="pending")  # pending, processing, published, failed, cancelled

    # Processing
    attempts = Column(Integer, default=0)
    last_attempt_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    published_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_content_queue_scheduled", "scheduled_for", "status"),
        Index("ix_content_queue_platform", "platform", "status"),
    )


# Export all models
__all__ = [
    "Post",
    "PostStatus",
    "MediaAsset",
    "MediaType",
    "SocialAccount",
    "Platform",
    "PublishTask",
    "TaskStage",
    "TaskStatus",
    "PublishResult",
    "Schedule",
    "ContentQueue",
]


# ==================== USER & SUBSCRIPTION MODELS ====================

class SubscriptionPlan(str, Enum):
    """Subscription plan types."""
    DEMO = "demo"
    PRO = "pro"
    BUSINESS = "business"


class ImageGenProvider(str, Enum):
    """Image generation providers."""
    OPENAI = "openai"      # DALL-E 3
    STABILITY = "stability"  # Stability AI
    FLUX = "flux"          # Flux
    MIDJOURNEY = "midjourney"  # Midjourney (via API)
    NANOBANA = "nanobana"  # Nanobana (Russian service)


class VideoGenProvider(str, Enum):
    """Video generation providers."""
    RUNWAY = "runway"  # Runway ML Gen-3


class User(Base):
    """User account entity."""

    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Telegram auth
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    telegram_username = Column(String(255), nullable=True)
    telegram_first_name = Column(String(255), nullable=True)
    telegram_last_name = Column(String(255), nullable=True)
    telegram_photo_url = Column(Text, nullable=True)

    # Subscription
    subscription_plan = Column(SQLEnum(SubscriptionPlan), default=SubscriptionPlan.DEMO)
    subscription_expires_at = Column(DateTime, nullable=True)
    demo_started_at = Column(DateTime, default=datetime.utcnow)

    # Settings
    image_gen_provider = Column(SQLEnum(ImageGenProvider), default=ImageGenProvider.OPENAI)
    video_gen_provider = Column(SQLEnum(VideoGenProvider), default=VideoGenProvider.RUNWAY)

    # Usage tracking
    posts_count_this_month = Column(Integer, default=0)
    images_generated_this_month = Column(Integer, default=0)
    usage_reset_at = Column(DateTime, default=datetime.utcnow)

    # Status
    is_active = Column(Boolean, default=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    social_accounts = relationship("UserSocialAccount", back_populates="user", cascade="all, delete-orphan")
    topics = relationship("Topic", back_populates="user", cascade="all, delete-orphan")


class UserSocialAccount(Base):
    """Link between User and SocialAccount."""

    __tablename__ = "user_social_accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id = Column(UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)

    # Permissions
    can_publish = Column(Boolean, default=True)
    is_primary = Column(Boolean, default=False)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="social_accounts")
    account = relationship("SocialAccount")

    __table_args__ = (
        UniqueConstraint("user_id", "account_id", name="uq_user_social_account"),
    )


class Topic(Base):
    """Content topic/category for organizing posts."""

    __tablename__ = "topics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Topic info
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    color = Column(String(7), default="#6366f1")  # Hex color

    # AI settings for this topic
    tone = Column(String(100), nullable=True)  # e.g., "professional", "casual", "humorous"
    hashtags = Column(ARRAY(String), nullable=True, default=list)
    call_to_action = Column(Text, nullable=True)

    # Status
    is_active = Column(Boolean, default=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="topics")


class ContentPlanStatus(str, Enum):
    """Content plan status."""
    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class VideoGenStatus(str, Enum):
    """Video generation task status."""
    PENDING = "pending"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


class ContentPlan(Base):
    """AI-generated content plan for a niche."""

    __tablename__ = "content_plans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Plan parameters
    niche = Column(String(100), nullable=False)
    duration_days = Column(Integer, nullable=False)
    posts_per_day = Column(Integer, default=1)
    tone = Column(String(50), nullable=True)  # professional, casual, humorous

    # Target platforms
    platforms = Column(ARRAY(String), nullable=False)

    # Generated plan data
    plan_data = Column(JSONB, nullable=False, default=list)
    # Format: [{"date": "2024-01-15", "topic": "...", "caption_draft": "...", "platforms": [...], "media_type": "image"}]

    # Status
    status = Column(SQLEnum(ContentPlanStatus), default=ContentPlanStatus.DRAFT)

    # Statistics
    posts_created = Column(Integer, default=0)
    posts_published = Column(Integer, default=0)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    activated_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "niche": self.niche,
            "duration_days": self.duration_days,
            "posts_per_day": self.posts_per_day,
            "tone": self.tone,
            "platforms": self.platforms,
            "plan_data": self.plan_data,
            "status": self.status.value if self.status else None,
            "posts_created": self.posts_created,
            "posts_published": self.posts_published,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class VideoGenTask(Base):
    """Video generation task."""

    __tablename__ = "video_gen_tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Provider info
    provider = Column(SQLEnum(VideoGenProvider), default=VideoGenProvider.RUNWAY)

    # Input
    prompt = Column(Text, nullable=False)
    source_image_url = Column(String(1000), nullable=True)  # For image-to-video
    duration_seconds = Column(Integer, default=5)

    # Provider task tracking
    provider_task_id = Column(String(255), nullable=True)  # Runway task ID

    # Result
    result_url = Column(String(1000), nullable=True)
    result_thumbnail_url = Column(String(1000), nullable=True)

    # Status
    status = Column(SQLEnum(VideoGenStatus), default=VideoGenStatus.PENDING)
    error_message = Column(Text, nullable=True)

    # Cost tracking
    cost_estimate = Column(Float, default=0.0)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "provider": self.provider.value if self.provider else None,
            "prompt": self.prompt,
            "duration_seconds": self.duration_seconds,
            "result_url": self.result_url,
            "status": self.status.value if self.status else None,
            "cost_estimate": self.cost_estimate,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ==================== CLOUD STORAGE MODELS ====================

class CloudProvider(str, Enum):
    """Supported cloud storage providers."""
    GOOGLE_DRIVE = "google_drive"
    YANDEX_DISK = "yandex_disk"


class CloudConnectionStatus(str, Enum):
    """Cloud connection status."""
    PENDING = "pending"      # Waiting for OAuth
    ACTIVE = "active"        # Connected and working
    ERROR = "error"          # Connection error
    EXPIRED = "expired"      # Token expired
    DISCONNECTED = "disconnected"  # User disconnected


class CloudStorageConnection(Base):
    """
    User's cloud storage connection.

    Allows users to connect Google Drive or Yandex Disk folders
    for automatic media sync.
    """

    __tablename__ = "cloud_storage_connections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Provider info
    provider = Column(SQLEnum(CloudProvider), nullable=False)

    # Folder identification
    folder_id = Column(String(500), nullable=False)  # Google Drive folder ID or Yandex path
    folder_name = Column(String(500), nullable=True)
    folder_url = Column(String(1000), nullable=True)  # Original sharing URL

    # OAuth tokens (encrypted in production)
    access_token = Column(Text, nullable=True)
    refresh_token = Column(Text, nullable=True)
    token_expires_at = Column(DateTime, nullable=True)

    # For public folders (Yandex Disk)
    public_url = Column(String(1000), nullable=True)
    is_public = Column(Boolean, default=False)

    # Connection status
    status = Column(SQLEnum(CloudConnectionStatus), default=CloudConnectionStatus.PENDING)
    error_message = Column(Text, nullable=True)

    # Sync settings
    sync_enabled = Column(Boolean, default=True)
    sync_interval_minutes = Column(Integer, default=60)  # How often to sync
    sync_videos = Column(Boolean, default=True)
    sync_photos = Column(Boolean, default=True)

    # Sync statistics
    last_sync_at = Column(DateTime, nullable=True)
    last_sync_status = Column(String(50), nullable=True)  # success, partial, failed
    files_synced_total = Column(Integer, default=0)
    last_sync_files_count = Column(Integer, default=0)
    last_sync_errors = Column(JSONB, nullable=True, default=list)

    # Local storage path for synced files
    local_sync_path = Column(String(1000), nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_cloud_connections_user_provider", "user_id", "provider"),
        Index("ix_cloud_connections_status", "status", "sync_enabled"),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary (without sensitive tokens)."""
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "provider": self.provider.value if self.provider else None,
            "folder_id": self.folder_id,
            "folder_name": self.folder_name,
            "folder_url": self.folder_url,
            "is_public": self.is_public,
            "status": self.status.value if self.status else None,
            "error_message": self.error_message,
            "sync_enabled": self.sync_enabled,
            "sync_interval_minutes": self.sync_interval_minutes,
            "sync_videos": self.sync_videos,
            "sync_photos": self.sync_photos,
            "last_sync_at": self.last_sync_at.isoformat() if self.last_sync_at else None,
            "last_sync_status": self.last_sync_status,
            "files_synced_total": self.files_synced_total,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class CloudSyncedFile(Base):
    """
    Track individual files synced from cloud storage.

    Links cloud files to local media assets and tracks sync status.
    """

    __tablename__ = "cloud_synced_files"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connection_id = Column(UUID(as_uuid=True), ForeignKey("cloud_storage_connections.id", ondelete="CASCADE"), nullable=False, index=True)

    # Cloud file info
    cloud_file_id = Column(String(500), nullable=False)  # File ID in cloud
    cloud_file_name = Column(String(500), nullable=False)
    cloud_file_path = Column(String(1000), nullable=True)  # Path in cloud folder
    cloud_mime_type = Column(String(100), nullable=True)
    cloud_file_size = Column(BigInteger, nullable=True)
    cloud_modified_at = Column(DateTime, nullable=True)

    # Media type
    media_type = Column(SQLEnum(MediaType), nullable=False)

    # Local file info
    local_path = Column(String(1000), nullable=True)
    local_file_size = Column(BigInteger, nullable=True)

    # Link to MediaAsset if used in a post
    media_asset_id = Column(UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True)

    # Sync status
    is_synced = Column(Boolean, default=False)
    sync_error = Column(Text, nullable=True)

    # Timestamps
    first_synced_at = Column(DateTime, nullable=True)
    last_synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("connection_id", "cloud_file_id", name="uq_cloud_synced_file"),
        Index("ix_cloud_synced_files_media_type", "connection_id", "media_type"),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "cloud_file_name": self.cloud_file_name,
            "cloud_file_path": self.cloud_file_path,
            "media_type": self.media_type.value if self.media_type else None,
            "cloud_file_size": self.cloud_file_size,
            "is_synced": self.is_synced,
            "last_synced_at": self.last_synced_at.isoformat() if self.last_synced_at else None,
        }


# ==================== ANTIFRAUD MODELS ====================

class FraudEventType(str, Enum):
    """Types of fraud events."""
    DEMO_ABUSE = "demo_abuse"
    MULTIPLE_ACCOUNTS = "multiple_accounts"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    PAYMENT_FRAUD = "payment_fraud"
    BOT_DETECTED = "bot_detected"
    IP_BLOCKED = "ip_blocked"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"


class FraudRiskLevel(str, Enum):
    """Risk levels for fraud events."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FraudEvent(Base):
    """
    Log of detected fraud events.

    Used for monitoring and analysis.
    """

    __tablename__ = "fraud_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Event type and risk
    event_type = Column(SQLEnum(FraudEventType), nullable=False, index=True)
    risk_level = Column(SQLEnum(FraudRiskLevel), nullable=False, index=True)
    score = Column(Float, nullable=False)

    # Target information
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    ip_address = Column(String(45), nullable=True, index=True)  # IPv6 max length
    ip_hash = Column(String(64), nullable=True, index=True)
    device_fingerprint = Column(String(64), nullable=True)
    telegram_id = Column(BigInteger, nullable=True, index=True)

    # Event details
    description = Column(Text, nullable=False)
    details = Column(JSONB, nullable=True, default=dict)

    # Action taken
    action_taken = Column(String(50), nullable=True)  # allow, challenge, block
    was_blocked = Column(Boolean, default=False)

    # Request context
    endpoint = Column(String(255), nullable=True)
    user_agent = Column(Text, nullable=True)
    request_id = Column(String(100), nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index("ix_fraud_events_type_created", "event_type", "created_at"),
        Index("ix_fraud_events_risk_created", "risk_level", "created_at"),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "event_type": self.event_type.value,
            "risk_level": self.risk_level.value,
            "score": self.score,
            "user_id": str(self.user_id) if self.user_id else None,
            "ip_hash": self.ip_hash[:8] + "..." if self.ip_hash else None,
            "description": self.description,
            "action_taken": self.action_taken,
            "was_blocked": self.was_blocked,
            "endpoint": self.endpoint,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class BlockedIP(Base):
    """
    Blocked IP addresses.

    Supports both manual and automatic blocks.
    """

    __tablename__ = "blocked_ips"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # IP identification
    ip_address = Column(String(45), nullable=False, unique=True, index=True)
    ip_hash = Column(String(64), nullable=True)

    # Block reason
    reason = Column(Text, nullable=False)
    auto_blocked = Column(Boolean, default=False)  # True if blocked by system
    blocked_by_user_id = Column(UUID(as_uuid=True), nullable=True)  # Admin who blocked

    # Related fraud events
    fraud_event_ids = Column(ARRAY(String), nullable=True, default=list)

    # Block duration
    permanent = Column(Boolean, default=False)
    expires_at = Column(DateTime, nullable=True)

    # Statistics
    block_count = Column(Integer, default=1)  # Times this IP was blocked
    last_attempt_at = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_blocked_ips_expires", "expires_at"),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "ip_address": self.ip_address[:8] + "...",  # Partial for security
            "reason": self.reason,
            "auto_blocked": self.auto_blocked,
            "permanent": self.permanent,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "block_count": self.block_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class RateLimitOverride(Base):
    """
    Custom rate limit overrides for specific users or IPs.

    Allows whitelisting or custom limits.
    """

    __tablename__ = "rate_limit_overrides"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Target
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    ip_address = Column(String(45), nullable=True, index=True)
    api_key = Column(String(255), nullable=True, index=True)

    # Override settings
    requests_per_minute = Column(Integer, nullable=True)
    requests_per_hour = Column(Integer, nullable=True)
    requests_per_day = Column(Integer, nullable=True)
    burst_limit = Column(Integer, nullable=True)

    # Whitelist (bypass all limits)
    is_whitelisted = Column(Boolean, default=False)

    # Metadata
    reason = Column(Text, nullable=True)
    created_by = Column(UUID(as_uuid=True), nullable=True)

    # Validity
    expires_at = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# Update exports
__all__.extend([
    "User",
    "UserSocialAccount",
    "Topic",
    "SubscriptionPlan",
    "ImageGenProvider",
    "VideoGenProvider",
    "ContentPlan",
    "ContentPlanStatus",
    "VideoGenTask",
    "VideoGenStatus",
    "CloudProvider",
    "CloudConnectionStatus",
    "CloudStorageConnection",
    "CloudSyncedFile",
    "FraudEventType",
    "FraudRiskLevel",
    "FraudEvent",
    "BlockedIP",
    "RateLimitOverride",
])
