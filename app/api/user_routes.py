"""
User API routes for SalesWhisper Crosspost.
CRUD for user data: accounts, posts, topics, schedules.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from .deps import check_subscription_active, get_current_user, get_db_async_session

logger = get_logger("api.user")

router = APIRouter(prefix="/user", tags=["User API"])


# ==================== SCHEMAS ====================

class AccountCreate(BaseModel):
    platform: str = Field(..., description="Platform: telegram, vk, instagram, tiktok, youtube, rutube, facebook, dzen")
    access_token: str | None = Field(None, description="Platform access token")
    platform_user_id: str | None = Field(None, description="Platform user ID")
    username: str | None = Field(None, description="Platform username")
    display_name: str | None = Field(None, description="Display name")
    credentials: dict | None = Field(None, description="Platform-specific credentials")

    @model_validator(mode='after')
    def validate_and_extract_credentials(self):
        # If credentials provided, extract tokens from it
        if self.credentials:
            # Extract access_token
            if not self.access_token:
                for key in ['access_token', 'bot_token', 'api_key', 'refresh_token', 'client_secret']:
                    if key in self.credentials:
                        self.access_token = str(self.credentials[key])
                        break
            # Extract platform_user_id
            if not self.platform_user_id:
                for key in ['chat_id', 'group_id', 'page_id', 'instagram_business_id', 'open_id', 'channel_id']:
                    if key in self.credentials:
                        self.platform_user_id = str(self.credentials[key])
                        break

        # Validation: must have access_token
        if not self.access_token:
            raise ValueError('access_token is required (either directly or in credentials)')

        return self


class AccountResponse(BaseModel):
    id: str
    platform: str
    platform_user_id: str
    username: str | None
    display_name: str | None
    is_active: bool
    is_verified: bool
    can_publish: bool
    is_primary: bool
    created_at: datetime


class TopicCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    color: str = Field(default="#6366f1", pattern="^#[0-9a-fA-F]{6}$")
    tone: str | None = Field(None, description="Content tone: professional, casual, humorous")
    hashtags: list[str] | None = None
    call_to_action: str | None = None


class TopicUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    color: str | None = Field(None, pattern="^#[0-9a-fA-F]{6}$")
    tone: str | None = None
    hashtags: list[str] | None = None
    call_to_action: str | None = None
    is_active: bool | None = None


class TopicResponse(BaseModel):
    id: str
    name: str
    description: str | None
    color: str
    tone: str | None
    hashtags: list[str] | None
    call_to_action: str | None
    is_active: bool
    created_at: datetime


class PostCreate(BaseModel):
    text: str = Field(..., min_length=1)
    topic_id: str | None = None
    platforms: list[str] = Field(..., min_items=1)
    scheduled_at: datetime | None = None
    media_urls: list[str] | None = None


class PostResponse(BaseModel):
    id: str
    original_text: str
    generated_caption: str | None
    status: str
    topic_id: str | None
    scheduled_at: datetime | None
    created_at: datetime
    published_at: datetime | None


class UserSettingsUpdate(BaseModel):
    image_gen_provider: str | None = Field(None, description="openai, stability, flux")


class UserStatsResponse(BaseModel):
    posts_count_this_month: int
    images_generated_this_month: int
    subscription_plan: str
    demo_days_left: int | None
    accounts_count: int
    topics_count: int


# ==================== ACCOUNTS ROUTES ====================

@router.get("/accounts", response_model=list[AccountResponse])
async def list_accounts(
    user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_async_session)
):
    """List user's connected social accounts."""
    from ..models.entities import SocialAccount, UserSocialAccount

    result = await db.execute(
        select(UserSocialAccount, SocialAccount)
        .join(SocialAccount, UserSocialAccount.account_id == SocialAccount.id)
        .where(UserSocialAccount.user_id == user.id)
    )
    rows = result.all()

    return [
        AccountResponse(
            id=str(usa.account_id),
            platform=acc.platform,
            platform_user_id=acc.platform_user_id,
            username=acc.username,
            display_name=acc.display_name,
            is_active=acc.is_active,
            is_verified=acc.is_verified,
            can_publish=usa.can_publish,
            is_primary=usa.is_primary,
            created_at=usa.created_at,
        )
        for usa, acc in rows
    ]


@router.post("/accounts", response_model=AccountResponse)
async def add_account(
    data: AccountCreate,
    user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_async_session)
):
    """Add a new social account."""
    check_subscription_active(user)

    from ..core.security import SecurityUtils
    from ..models.entities import Platform, SocialAccount, UserSocialAccount

    # Validate platform
    try:
        platform = Platform(data.platform.lower())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid platform: {data.platform}"
        )

    # Check if account already exists
    result = await db.execute(
        select(SocialAccount).where(
            SocialAccount.platform == platform,
            SocialAccount.platform_user_id == (data.platform_user_id or data.access_token[:50])
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        # Check if already linked to this user
        link_result = await db.execute(
            select(UserSocialAccount).where(
                UserSocialAccount.user_id == user.id,
                UserSocialAccount.account_id == existing.id
            )
        )
        if link_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Account already connected"
            )

        # Link existing account to user
        link = UserSocialAccount(
            user_id=user.id,
            account_id=existing.id,
        )
        db.add(link)
        await db.commit()

        return AccountResponse(
            id=str(existing.id),
            platform=existing.platform.value if hasattr(existing.platform, 'value') else existing.platform,
            platform_user_id=existing.platform_user_id,
            username=existing.username,
            display_name=existing.display_name,
            is_active=existing.is_active,
            is_verified=existing.is_verified,
            can_publish=True,
            is_primary=False,
            created_at=datetime.utcnow(),
        )

    # Create new account
    account = SocialAccount(
        platform=platform,
        platform_user_id=data.platform_user_id or data.access_token[:50],
        username=data.username,
        access_token_encrypted=SecurityUtils.encrypt_token(data.access_token),
        is_active=True,
    )
    db.add(account)
    await db.flush()

    # Link to user
    link = UserSocialAccount(
        user_id=user.id,
        account_id=account.id,
    )
    db.add(link)
    await db.commit()

    logger.info(f"Account added: {account.id} for user {user.id}")

    return AccountResponse(
        id=str(account.id),
        platform=platform.value,
        platform_user_id=account.platform_user_id,
        username=account.username,
        display_name=None,
        is_active=True,
        is_verified=False,
        can_publish=True,
        is_primary=False,
        created_at=datetime.utcnow(),
    )


@router.delete("/accounts/{account_id}")
async def remove_account(
    account_id: UUID,
    user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_async_session)
):
    """Remove a social account from user."""
    from ..models.entities import UserSocialAccount

    result = await db.execute(
        delete(UserSocialAccount).where(
            UserSocialAccount.user_id == user.id,
            UserSocialAccount.account_id == account_id
        )
    )

    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found"
        )

    await db.commit()
    return {"success": True, "message": "Account removed"}


# ==================== TOPICS ROUTES ====================

@router.get("/topics", response_model=list[TopicResponse])
async def list_topics(
    user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_async_session)
):
    """List user's content topics."""
    from ..models.entities import Topic

    result = await db.execute(
        select(Topic).where(Topic.user_id == user.id).order_by(Topic.created_at.desc())
    )
    topics = result.scalars().all()

    return [
        TopicResponse(
            id=str(t.id),
            name=t.name,
            description=t.description,
            color=t.color,
            tone=t.tone,
            hashtags=t.hashtags,
            call_to_action=t.call_to_action,
            is_active=t.is_active,
            created_at=t.created_at,
        )
        for t in topics
    ]


@router.post("/topics", response_model=TopicResponse)
async def create_topic(
    data: TopicCreate,
    user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_async_session)
):
    """Create a new content topic."""
    check_subscription_active(user)

    from ..models.entities import Topic

    topic = Topic(
        user_id=user.id,
        name=data.name,
        description=data.description,
        color=data.color,
        tone=data.tone,
        hashtags=data.hashtags or [],
        call_to_action=data.call_to_action,
    )
    db.add(topic)
    await db.commit()
    await db.refresh(topic)

    return TopicResponse(
        id=str(topic.id),
        name=topic.name,
        description=topic.description,
        color=topic.color,
        tone=topic.tone,
        hashtags=topic.hashtags,
        call_to_action=topic.call_to_action,
        is_active=topic.is_active,
        created_at=topic.created_at,
    )


@router.put("/topics/{topic_id}", response_model=TopicResponse)
async def update_topic(
    topic_id: UUID,
    data: TopicUpdate,
    user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_async_session)
):
    """Update a content topic."""
    from ..models.entities import Topic

    result = await db.execute(
        select(Topic).where(Topic.id == topic_id, Topic.user_id == user.id)
    )
    topic = result.scalar_one_or_none()

    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")

    # Update fields
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(topic, field, value)

    topic.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(topic)

    return TopicResponse(
        id=str(topic.id),
        name=topic.name,
        description=topic.description,
        color=topic.color,
        tone=topic.tone,
        hashtags=topic.hashtags,
        call_to_action=topic.call_to_action,
        is_active=topic.is_active,
        created_at=topic.created_at,
    )


@router.delete("/topics/{topic_id}")
async def delete_topic(
    topic_id: UUID,
    user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_async_session)
):
    """Delete a content topic."""
    from ..models.entities import Topic

    result = await db.execute(
        delete(Topic).where(Topic.id == topic_id, Topic.user_id == user.id)
    )

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Topic not found")

    await db.commit()
    return {"success": True, "message": "Topic deleted"}


# ==================== USER SETTINGS & STATS ====================

@router.get("/stats", response_model=UserStatsResponse)
async def get_user_stats(
    user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_async_session)
):
    """Get user statistics."""
    from ..models.entities import SubscriptionPlan, Topic, UserSocialAccount

    # Count accounts
    acc_result = await db.execute(
        select(UserSocialAccount).where(UserSocialAccount.user_id == user.id)
    )
    accounts_count = len(acc_result.scalars().all())

    # Count topics
    topic_result = await db.execute(
        select(Topic).where(Topic.user_id == user.id, Topic.is_active)
    )
    topics_count = len(topic_result.scalars().all())

    # Calculate demo days left
    demo_days_left = None
    if user.subscription_plan == SubscriptionPlan.DEMO and user.demo_started_at:
        days_passed = (datetime.utcnow() - user.demo_started_at).days
        demo_days_left = max(0, 7 - days_passed)

    return UserStatsResponse(
        posts_count_this_month=user.posts_count_this_month,
        images_generated_this_month=user.images_generated_this_month,
        subscription_plan=user.subscription_plan.value,
        demo_days_left=demo_days_left,
        accounts_count=accounts_count,
        topics_count=topics_count,
    )


@router.patch("/settings")
async def update_settings(
    data: UserSettingsUpdate,
    user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_async_session)
):
    """Update user settings."""
    from ..models.entities import ImageGenProvider

    if data.image_gen_provider:
        try:
            provider = ImageGenProvider(data.image_gen_provider.lower())
            user.image_gen_provider = provider
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid provider: {data.image_gen_provider}"
            )

    user.updated_at = datetime.utcnow()
    await db.commit()

    return {
        "success": True,
        "image_gen_provider": user.image_gen_provider.value
    }


# ==================== IMAGE GENERATION ====================

class ImageGenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=3, max_length=1000)
    size: str = Field(default="1024x1024", pattern=r"^\d+x\d+$")


class ImageGenerateResponse(BaseModel):
    success: bool
    image_url: str | None = None
    image_base64: str | None = None
    error: str | None = None
    provider: str
    cost_estimate: float


@router.post("/images/generate", response_model=ImageGenerateResponse)
async def generate_image(
    data: ImageGenerateRequest,
    user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_async_session)
):
    """Generate an image using user's selected provider."""
    check_subscription_active(user)

    from ..models.entities import SubscriptionPlan
    from ..services.image_gen import image_service

    # Check usage limits
    if user.subscription_plan == SubscriptionPlan.DEMO:
        if user.images_generated_this_month >= 5:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="Demo image limit reached. Upgrade for more."
            )
    elif user.subscription_plan == SubscriptionPlan.PRO:
        if user.images_generated_this_month >= 50:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="Monthly image limit reached."
            )

    # Generate image
    result = await image_service.generate(
        prompt=data.prompt,
        provider=user.image_gen_provider.value,
        size=data.size
    )

    if result.success:
        # Update usage counter
        user.images_generated_this_month += 1
        user.updated_at = datetime.utcnow()
        await db.commit()

    return ImageGenerateResponse(
        success=result.success,
        image_url=result.image_url,
        image_base64=result.image_base64,
        error=result.error,
        provider=result.provider or user.image_gen_provider.value,
        cost_estimate=result.cost_estimate,
    )


@router.get("/images/providers")
async def get_image_providers():
    """Get available image generation providers."""
    from ..services.image_gen import image_service

    return {
        "providers": image_service.get_available_providers(),
        "default": "openai"
    }
