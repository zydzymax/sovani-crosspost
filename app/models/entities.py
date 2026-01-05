"""
SQLAlchemy models for SalesWhisper Crosspost.

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
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import relationship

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
    DZEN = "dzen"


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

    def to_dict(self) -> dict[str, Any]:
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

    def to_dict(self) -> dict[str, Any]:
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

    def to_dict(self) -> dict[str, Any]:
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

    def to_dict(self) -> dict[str, Any]:
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
    RUNWAY = "runway"        # Runway ML Gen-3
    KLING = "kling"          # Kling AI
    MINIMAX = "minimax"      # MiniMax Hailuo
    LUMA = "luma"            # Luma Dream Machine
    REPLICATE = "replicate"  # Replicate (Stable Video Diffusion)


class User(Base):
    """User account entity."""

    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Email auth (primary for new users)
    email = Column(String(255), unique=True, nullable=True, index=True)
    email_verified = Column(Boolean, default=False)
    email_verified_at = Column(DateTime, nullable=True)

    # Telegram auth (legacy, optional)
    telegram_id = Column(BigInteger, unique=True, nullable=True, index=True)
    telegram_username = Column(String(255), nullable=True)
    telegram_first_name = Column(String(255), nullable=True)
    telegram_last_name = Column(String(255), nullable=True)
    telegram_photo_url = Column(Text, nullable=True)

    # Profile
    first_name = Column(String(255), nullable=True)
    last_name = Column(String(255), nullable=True)
    company_name = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)

    # Subscription (legacy - use user_subscriptions for new system)
    subscription_plan = Column(SQLEnum(SubscriptionPlan, name="subscription_plan", create_type=False), default=SubscriptionPlan.DEMO)
    subscription_expires_at = Column(DateTime, nullable=True)
    demo_started_at = Column(DateTime, default=datetime.utcnow)

    # Settings
    image_gen_provider = Column(SQLEnum(ImageGenProvider, name="image_gen_provider", create_type=False), default=ImageGenProvider.OPENAI)
    video_gen_provider = Column(SQLEnum(VideoGenProvider, name="video_gen_provider", create_type=False), default=VideoGenProvider.RUNWAY)

    # Usage tracking
    posts_count_this_month = Column(Integer, default=0)
    images_generated_this_month = Column(Integer, default=0)
    usage_reset_at = Column(DateTime, default=datetime.utcnow)

    # Status
    is_active = Column(Boolean, default=True)
    last_login_at = Column(DateTime, nullable=True)

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
    account_id = Column(UUID(as_uuid=True), ForeignKey("social_accounts.id", ondelete="CASCADE"), nullable=False, index=True)

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


class MarketplacePlatform(str, Enum):
    """Supported marketplace platforms."""
    WILDBERRIES = "wildberries"
    OZON = "ozon"
    YANDEX_MARKET = "yandex_market"
    ALIEXPRESS = "aliexpress"


class MarketplaceCredential(Base):
    """User's marketplace API credentials for enrichment."""

    __tablename__ = "marketplace_credentials"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Marketplace info
    platform = Column(SQLEnum(MarketplacePlatform), nullable=False)

    # Credentials (encrypted)
    api_key = Column(Text, nullable=True)  # Encrypted - main API token
    client_id = Column(Text, nullable=True)  # Encrypted - for Ozon

    # Optional seller info
    seller_id = Column(String(255), nullable=True)
    campaign_id = Column(String(255), nullable=True)  # For Yandex Market

    # Status
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    last_verified_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "platform", name="uq_user_marketplace"),
        Index("ix_marketplace_user_platform", "user_id", "platform"),
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

    def to_dict(self) -> dict[str, Any]:
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




class GenerationStepStatus(str, Enum):
    """Generation step status."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class GenerationStep(str, Enum):
    """Steps in content generation process."""
    # Plan creation
    PLAN_CREATED = "plan_created"
    # Per-post steps
    CAPTION_GENERATED = "caption_generated"
    HASHTAGS_GENERATED = "hashtags_generated"
    IMAGE_PROMPT_GENERATED = "image_prompt_generated"
    IMAGE_GENERATED = "image_generated"
    VIDEO_GENERATED = "video_generated"
    AUDIO_GENERATED = "audio_generated"
    # Publishing
    POST_SCHEDULED = "post_scheduled"
    POST_PUBLISHED = "post_published"
    # Quality checks
    CONTENT_REVIEWED = "content_reviewed"
    COMPLIANCE_CHECKED = "compliance_checked"


class PostGenerationProgress(Base):
    """Tracks generation progress for each post in a content plan."""

    __tablename__ = "post_generation_progress"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_plan_id = Column(UUID(as_uuid=True), ForeignKey("content_plans.id", ondelete="CASCADE"), nullable=False, index=True)
    post_index = Column(Integer, nullable=False)

    post_date = Column(Date, nullable=False)
    post_topic = Column(String(500), nullable=True)

    steps = Column(JSONB, nullable=False, default=dict)
    overall_status = Column(SQLEnum(GenerationStepStatus), default=GenerationStepStatus.PENDING)
    progress_percent = Column(Integer, default=0)

    last_error = Column(Text, nullable=True)
    error_count = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "content_plan_id": str(self.content_plan_id),
            "post_index": self.post_index,
            "post_date": self.post_date.isoformat() if self.post_date else None,
            "post_topic": self.post_topic,
            "steps": self.steps,
            "overall_status": self.overall_status.value if self.overall_status else None,
            "progress_percent": self.progress_percent,
            "last_error": self.last_error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None
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

    def to_dict(self) -> dict[str, Any]:
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

    def to_dict(self) -> dict[str, Any]:
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

    def to_dict(self) -> dict[str, Any]:
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

    def to_dict(self) -> dict[str, Any]:
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

    def to_dict(self) -> dict[str, Any]:
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


# ============================================
# UNIFIED AUTH AND CART SYSTEM MODELS
# ============================================

class UserSubscriptionStatus(str, Enum):
    """User subscription status."""
    ACTIVE = "active"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    PAUSED = "paused"
    TRIAL = "trial"


class OrderStatus(str, Enum):
    """Order status."""
    PENDING = "pending"
    AWAITING_PAYMENT = "awaiting_payment"
    PAID = "paid"
    FAILED = "failed"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"


class PaymentStatus(str, Enum):
    """Payment status."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"


class BillingPeriod(str, Enum):
    """Billing period types."""
    MONTHLY = "monthly"
    YEARLY = "yearly"
    LIFETIME = "lifetime"


class SaaSProduct(Base):
    """SaaS Product catalog (Crosspost, HeadOfSales, etc.)."""

    __tablename__ = "saas_products"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(50), unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    plans = relationship("SaaSProductPlan", back_populates="product", cascade="all, delete-orphan")


class SaaSProductPlan(Base):
    """SaaS Product pricing plans (Demo, Starter, Pro, Business)."""

    __tablename__ = "saas_product_plans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id = Column(UUID(as_uuid=True), ForeignKey("saas_products.id", ondelete="CASCADE"), nullable=False, index=True)
    code = Column(String(50), nullable=False)
    name = Column(String(255), nullable=False)
    price_rub = Column(Float, nullable=False)
    billing_period = Column(SQLEnum(BillingPeriod, name="billing_period", create_type=False), default=BillingPeriod.MONTHLY)
    limits = Column(JSONB, default=dict)
    features = Column(JSONB, default=list)
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    product = relationship("SaaSProduct", back_populates="plans")

    __table_args__ = (
        UniqueConstraint("product_id", "code", name="uq_saas_product_plan"),
    )


class UserSubscription(Base):
    """User subscriptions to SaaS products."""

    __tablename__ = "user_subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(UUID(as_uuid=True), ForeignKey("saas_products.id", ondelete="RESTRICT"), nullable=False)
    plan_id = Column(UUID(as_uuid=True), ForeignKey("saas_product_plans.id", ondelete="RESTRICT"), nullable=False)

    status = Column(SQLEnum(UserSubscriptionStatus, name="subscription_status", create_type=False), default=UserSubscriptionStatus.ACTIVE)

    started_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)

    current_period_start = Column(DateTime, default=datetime.utcnow)
    current_period_end = Column(DateTime, nullable=True)

    payment_provider = Column(String(50), nullable=True)
    external_subscription_id = Column(String(255), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    product = relationship("SaaSProduct")
    plan = relationship("SaaSProductPlan")

    __table_args__ = (
        UniqueConstraint("user_id", "product_id", name="uq_user_product_subscription"),
        Index("idx_subscriptions_expires", "expires_at"),
        Index("idx_subscriptions_status", "status"),
    )


class Cart(Base):
    """Shopping cart for users."""

    __tablename__ = "carts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)

    items = Column(JSONB, default=list)
    # Format: [{"product_id": "...", "plan_id": "...", "product_code": "...", "plan_code": "...", "price_rub": 990}]

    subtotal_rub = Column(Float, default=0)
    discount_rub = Column(Float, default=0)
    total_rub = Column(Float, default=0)

    promo_code = Column(String(50), nullable=True)
    promo_discount_percent = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Order(Base):
    """Orders for product purchases."""

    __tablename__ = "orders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    order_number = Column(String(50), unique=True, nullable=False)
    status = Column(SQLEnum(OrderStatus, name="order_status", create_type=False), default=OrderStatus.PENDING)

    items = Column(JSONB, nullable=False)

    subtotal_rub = Column(Float, nullable=False)
    discount_rub = Column(Float, default=0)
    total_rub = Column(Float, nullable=False)

    promo_code = Column(String(50), nullable=True)

    customer_email = Column(String(255), nullable=False)
    customer_name = Column(String(255), nullable=True)
    customer_company = Column(String(255), nullable=True)
    customer_phone = Column(String(50), nullable=True)

    payment_provider = Column(String(50), nullable=True)
    payment_method = Column(String(50), nullable=True)

    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    paid_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)

    # Relationships
    payments = relationship("Payment", back_populates="order", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_orders_status", "status"),
        Index("idx_orders_created", "created_at"),
    )


class Payment(Base):
    """Payment records for orders."""

    __tablename__ = "payments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)

    amount_rub = Column(Float, nullable=False)
    currency = Column(String(3), default="RUB")

    status = Column(SQLEnum(PaymentStatus, name="payment_status", create_type=False), default=PaymentStatus.PENDING)

    provider = Column(String(50), nullable=False)
    provider_payment_id = Column(String(255), nullable=True)
    provider_response = Column(JSONB, nullable=True)

    invoice_number = Column(String(50), nullable=True)
    invoice_pdf_url = Column(Text, nullable=True)

    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    # Relationships
    order = relationship("Order", back_populates="payments")

    __table_args__ = (
        Index("idx_payments_provider", "provider", "provider_payment_id"),
        Index("idx_payments_status", "status"),
    )


class PromoCode(Base):
    """Promotional codes for discounts."""

    __tablename__ = "promo_codes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(50), unique=True, nullable=False)

    discount_percent = Column(Integer, nullable=True)
    discount_amount_rub = Column(Float, nullable=True)

    valid_from = Column(DateTime, default=datetime.utcnow)
    valid_until = Column(DateTime, nullable=True)

    max_uses = Column(Integer, nullable=True)
    current_uses = Column(Integer, default=0)

    product_id = Column(UUID(as_uuid=True), ForeignKey("saas_products.id", ondelete="SET NULL"), nullable=True)
    plan_id = Column(UUID(as_uuid=True), ForeignKey("saas_product_plans.id", ondelete="SET NULL"), nullable=True)

    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# Update exports


# =============================================================================
# ANALYTICS & INSIGHTS MODELS
# =============================================================================

class InsightType(str, Enum):
    """Types of AI-generated insights."""
    PERFORMANCE_ANALYSIS = "performance_analysis"
    CONTENT_RECOMMENDATION = "content_recommendation"
    TIMING_SUGGESTION = "timing_suggestion"
    AUDIENCE_INSIGHT = "audience_insight"
    TREND_ALERT = "trend_alert"
    OPTIMIZATION_ACTION = "optimization_action"


class InsightPriority(str, Enum):
    """Priority levels for insights."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class InsightStatus(str, Enum):
    """Status of an insight."""
    PENDING = "pending"
    SHOWN = "shown"
    APPLIED = "applied"
    DISMISSED = "dismissed"
    AUTO_APPLIED = "auto_applied"


class OptimizationMode(str, Enum):
    """AI optimization modes."""
    DISABLED = "disabled"
    HINTS_ONLY = "hints_only"
    CONFIRM = "confirm"
    AUTO = "auto"


class PostMetrics(Base):
    """Time-series engagement metrics for published posts."""

    __tablename__ = "post_metrics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    publish_result_id = Column(UUID(as_uuid=True), ForeignKey("publish_results.id", ondelete="CASCADE"), nullable=False)
    post_id = Column(UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    platform = Column(String(50), nullable=False, index=True)

    # Core metrics
    views = Column(Integer, default=0)
    likes = Column(Integer, default=0)
    comments = Column(Integer, default=0)
    shares = Column(Integer, default=0)
    saves = Column(Integer, default=0)

    # Calculated metrics
    engagement_rate = Column(Float, default=0)  # (likes+comments+shares)/views
    click_through_rate = Column(Float, default=0)

    # Platform-specific (JSONB)
    platform_metrics = Column(JSONB, default=dict)
    audience_data = Column(JSONB, default=dict)

    # Growth tracking
    followers_before = Column(Integer, nullable=True)
    followers_after = Column(Integer, nullable=True)
    followers_gained = Column(Integer, default=0)

    # Time tracking
    hours_since_publish = Column(Integer, default=0)
    measured_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    post = relationship("Post", backref="metrics")
    publish_result = relationship("PublishResult", backref="metrics")

    def calculate_engagement_rate(self):
        """Calculate engagement rate from metrics."""
        if self.views and self.views > 0:
            self.engagement_rate = (self.likes + self.comments + self.shares) / self.views
        return self.engagement_rate

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "post_id": str(self.post_id),
            "platform": self.platform,
            "views": self.views,
            "likes": self.likes,
            "comments": self.comments,
            "shares": self.shares,
            "saves": self.saves,
            "engagement_rate": self.engagement_rate,
            "followers_gained": self.followers_gained,
            "hours_since_publish": self.hours_since_publish,
            "measured_at": self.measured_at.isoformat() if self.measured_at else None,
            "platform_metrics": self.platform_metrics
        }


class ContentInsight(Base):
    """AI-generated insights and recommendations."""

    __tablename__ = "content_insights"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Scope
    post_id = Column(UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=True)
    platform = Column(String(50), nullable=True)

    # Insight details
    insight_type = Column(SQLEnum(InsightType), nullable=False)
    priority = Column(SQLEnum(InsightPriority), default=InsightPriority.MEDIUM)
    status = Column(SQLEnum(InsightStatus), default=InsightStatus.PENDING)

    # Content
    title = Column(String(255), nullable=False)
    summary = Column(Text, nullable=False)
    detailed_analysis = Column(Text, nullable=True)

    # Recommendations
    recommendations = Column(JSONB, default=list)
    confidence_score = Column(Float, default=0.8)
    ai_reasoning = Column(Text, nullable=True)
    supporting_data = Column(JSONB, default=dict)

    # Auto-action
    auto_action_type = Column(String(50), nullable=True)
    auto_action_payload = Column(JSONB, nullable=True)
    auto_action_executed = Column(Boolean, default=False)
    auto_action_result = Column(JSONB, nullable=True)

    # User interaction
    user_feedback = Column(String(20), nullable=True)
    user_notes = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    shown_at = Column(DateTime, nullable=True)
    applied_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)

    # Relationships
    post = relationship("Post", backref="insights")

    def mark_shown(self):
        self.status = InsightStatus.SHOWN
        self.shown_at = datetime.utcnow()

    def mark_applied(self, feedback: str = None):
        self.status = InsightStatus.APPLIED
        self.applied_at = datetime.utcnow()
        if feedback:
            self.user_feedback = feedback

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "post_id": str(self.post_id) if self.post_id else None,
            "platform": self.platform,
            "insight_type": self.insight_type.value,
            "priority": self.priority.value,
            "status": self.status.value,
            "title": self.title,
            "summary": self.summary,
            "detailed_analysis": self.detailed_analysis,
            "recommendations": self.recommendations,
            "confidence_score": self.confidence_score,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None
        }


class AnalyticsSettings(Base):
    """User preferences for analytics and optimization."""

    __tablename__ = "analytics_settings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)

    # Collection settings
    collect_metrics = Column(Boolean, default=True)
    metrics_frequency_hours = Column(Integer, default=24)

    # Optimization mode
    optimization_mode = Column(SQLEnum(OptimizationMode), default=OptimizationMode.HINTS_ONLY)

    # Feature toggles
    auto_adjust_timing = Column(Boolean, default=False)
    auto_optimize_hashtags = Column(Boolean, default=False)
    auto_adjust_content_length = Column(Boolean, default=False)
    auto_suggest_topics = Column(Boolean, default=True)

    # Notifications
    notify_on_viral = Column(Boolean, default=True)
    notify_on_drop = Column(Boolean, default=True)
    notify_weekly_report = Column(Boolean, default=True)

    # Thresholds
    viral_threshold_multiplier = Column(Float, default=3.0)
    drop_threshold_percent = Column(Integer, default=50)
    benchmark_period_days = Column(Integer, default=30)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "optimization_mode": self.optimization_mode.value,
            "collect_metrics": self.collect_metrics,
            "auto_adjust_timing": self.auto_adjust_timing,
            "auto_optimize_hashtags": self.auto_optimize_hashtags,
            "auto_suggest_topics": self.auto_suggest_topics,
            "notify_on_viral": self.notify_on_viral,
            "notify_weekly_report": self.notify_weekly_report
        }


class PerformanceBenchmark(Base):
    """Aggregated performance data for comparison."""

    __tablename__ = "performance_benchmarks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    platform = Column(String(50), nullable=False)

    # Period
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)

    # Aggregated metrics
    total_posts = Column(Integer, default=0)
    avg_views = Column(Float, default=0)
    avg_likes = Column(Float, default=0)
    avg_comments = Column(Float, default=0)
    avg_shares = Column(Float, default=0)
    avg_engagement_rate = Column(Float, default=0)

    # Best/worst performers
    best_performing_post_id = Column(UUID(as_uuid=True), ForeignKey("posts.id"), nullable=True)
    worst_performing_post_id = Column(UUID(as_uuid=True), ForeignKey("posts.id"), nullable=True)

    # Patterns
    best_posting_times = Column(JSONB, default=list)
    best_days_of_week = Column(JSONB, default=list)
    top_hashtags = Column(JSONB, default=list)
    top_content_types = Column(JSONB, default=list)

    # Growth
    followers_start = Column(Integer, nullable=True)
    followers_end = Column(Integer, nullable=True)
    followers_growth_rate = Column(Float, nullable=True)

    # AI summary
    period_summary = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "platform": self.platform,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "total_posts": self.total_posts,
            "avg_views": self.avg_views,
            "avg_likes": self.avg_likes,
            "avg_engagement_rate": self.avg_engagement_rate,
            "best_posting_times": self.best_posting_times,
            "best_days_of_week": self.best_days_of_week,
            "top_hashtags": self.top_hashtags,
            "period_summary": self.period_summary
        }


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
    # Unified Auth & Cart
    "UserSubscriptionStatus",
    "OrderStatus",
    "PaymentStatus",
    "BillingPeriod",
    "SaaSProduct",
    "SaaSProductPlan",
    "UserSubscription",
    "Cart",
    "Order",
    "Payment",
    "PromoCode",
    # Generation progress tracking
    "GenerationStepStatus",
    "GenerationStep",
    "PostGenerationProgress",
    # Analytics & AI Insights
    "InsightType",
    "InsightPriority",
    "InsightStatus",
    "OptimizationMode",
    "PostMetrics",
    "ContentInsight",
    "AnalyticsSettings",
    "PerformanceBenchmark",
])
