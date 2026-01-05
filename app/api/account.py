"""
Account API routes for SalesWhisper unified auth.
Profile management and subscriptions.
"""

from datetime import datetime
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from .deps import get_db_async_session, get_current_user

logger = get_logger("api.account")

router = APIRouter(prefix="/account", tags=["Account"])


# ==================== SCHEMAS ====================

class ProfileResponse(BaseModel):
    """User profile response."""
    id: str
    email: Optional[str]
    email_verified: bool = False
    telegram_id: Optional[int]
    telegram_username: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    company_name: Optional[str]
    phone: Optional[str]
    photo_url: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ProfileUpdate(BaseModel):
    """Profile update request."""
    first_name: Optional[str] = Field(None, max_length=255)
    last_name: Optional[str] = Field(None, max_length=255)
    company_name: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)


class SubscriptionResponse(BaseModel):
    """Subscription info."""
    id: str
    product_code: str
    product_name: str
    plan_code: str
    plan_name: str
    status: str
    price_rub: float
    billing_period: str
    current_period_start: Optional[datetime]
    current_period_end: Optional[datetime]
    expires_at: Optional[datetime]


class UsageStatsResponse(BaseModel):
    """Usage statistics."""
    posts_count_this_month: int
    images_generated_this_month: int
    videos_generated_this_month: int = 0
    usage_reset_at: Optional[datetime]


class AccountSummaryResponse(BaseModel):
    """Full account summary."""
    profile: ProfileResponse
    subscriptions: List[SubscriptionResponse]
    usage: UsageStatsResponse
    legacy_plan: Optional[str]  # From old subscription system
    demo_days_left: Optional[int]


# ==================== ROUTES ====================

@router.get("/profile", response_model=ProfileResponse)
async def get_profile(
    user = Depends(get_current_user)
):
    """Get current user's profile."""
    return ProfileResponse(
        id=str(user.id),
        email=user.email,
        email_verified=user.email_verified,
        telegram_id=user.telegram_id,
        telegram_username=user.telegram_username,
        first_name=user.first_name or user.telegram_first_name,
        last_name=user.last_name or user.telegram_last_name,
        company_name=user.company_name,
        phone=user.phone,
        photo_url=user.telegram_photo_url,
        is_active=user.is_active,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.patch("/profile", response_model=ProfileResponse)
async def update_profile(
    data: ProfileUpdate,
    user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_async_session)
):
    """Update user profile."""
    # Update only provided fields
    update_data = data.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(user, field, value)

    user.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(user)

    logger.info(f"Profile updated: {user.id}")

    return ProfileResponse(
        id=str(user.id),
        email=user.email,
        email_verified=user.email_verified,
        telegram_id=user.telegram_id,
        telegram_username=user.telegram_username,
        first_name=user.first_name or user.telegram_first_name,
        last_name=user.last_name or user.telegram_last_name,
        company_name=user.company_name,
        phone=user.phone,
        photo_url=user.telegram_photo_url,
        is_active=user.is_active,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.get("/subscriptions", response_model=List[SubscriptionResponse])
async def get_subscriptions(
    user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_async_session)
):
    """Get user's active subscriptions."""
    from ..models.entities import UserSubscription, SaaSProduct, SaaSProductPlan

    result = await db.execute(
        select(UserSubscription, SaaSProduct, SaaSProductPlan)
        .join(SaaSProduct, UserSubscription.product_id == SaaSProduct.id)
        .join(SaaSProductPlan, UserSubscription.plan_id == SaaSProductPlan.id)
        .where(UserSubscription.user_id == user.id)
    )
    rows = result.all()

    subscriptions = []
    for sub, product, plan in rows:
        subscriptions.append(SubscriptionResponse(
            id=str(sub.id),
            product_code=product.code,
            product_name=product.name,
            plan_code=plan.code,
            plan_name=plan.name,
            status=sub.status.value if hasattr(sub.status, 'value') else str(sub.status),
            price_rub=float(plan.price_rub),
            billing_period=plan.billing_period.value if hasattr(plan.billing_period, 'value') else str(plan.billing_period),
            current_period_start=sub.current_period_start,
            current_period_end=sub.current_period_end,
            expires_at=sub.expires_at,
        ))

    return subscriptions


@router.get("/summary", response_model=AccountSummaryResponse)
async def get_account_summary(
    user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_async_session)
):
    """Get full account summary with profile, subscriptions, and usage."""
    from ..models.entities import UserSubscription, SaaSProduct, SaaSProductPlan, SubscriptionPlan

    # Get subscriptions
    result = await db.execute(
        select(UserSubscription, SaaSProduct, SaaSProductPlan)
        .join(SaaSProduct, UserSubscription.product_id == SaaSProduct.id)
        .join(SaaSProductPlan, UserSubscription.plan_id == SaaSProductPlan.id)
        .where(UserSubscription.user_id == user.id)
    )
    rows = result.all()

    subscriptions = []
    for sub, product, plan in rows:
        subscriptions.append(SubscriptionResponse(
            id=str(sub.id),
            product_code=product.code,
            product_name=product.name,
            plan_code=plan.code,
            plan_name=plan.name,
            status=sub.status.value if hasattr(sub.status, 'value') else str(sub.status),
            price_rub=float(plan.price_rub),
            billing_period=plan.billing_period.value if hasattr(plan.billing_period, 'value') else str(plan.billing_period),
            current_period_start=sub.current_period_start,
            current_period_end=sub.current_period_end,
            expires_at=sub.expires_at,
        ))

    # Calculate demo days left (from legacy system)
    demo_days_left = None
    if user.subscription_plan == SubscriptionPlan.DEMO and user.demo_started_at:
        days_passed = (datetime.utcnow() - user.demo_started_at).days
        demo_days_left = max(0, 7 - days_passed)

    return AccountSummaryResponse(
        profile=ProfileResponse(
            id=str(user.id),
            email=user.email,
            email_verified=user.email_verified,
            telegram_id=user.telegram_id,
            telegram_username=user.telegram_username,
            first_name=user.first_name or user.telegram_first_name,
            last_name=user.last_name or user.telegram_last_name,
            company_name=user.company_name,
            phone=user.phone,
            photo_url=user.telegram_photo_url,
            is_active=user.is_active,
            created_at=user.created_at,
            updated_at=user.updated_at,
        ),
        subscriptions=subscriptions,
        usage=UsageStatsResponse(
            posts_count_this_month=user.posts_count_this_month,
            images_generated_this_month=user.images_generated_this_month,
            videos_generated_this_month=0,  # Not tracked yet
            usage_reset_at=user.usage_reset_at,
        ),
        legacy_plan=user.subscription_plan.value if user.subscription_plan else None,
        demo_days_left=demo_days_left,
    )


@router.post("/link-telegram")
async def link_telegram_account(
    telegram_id: int,
    user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_async_session)
):
    """Link Telegram account to current user (for migration)."""
    from ..models.entities import User

    # Check if telegram_id is already used
    result = await db.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    existing = result.scalar_one_or_none()

    if existing and existing.id != user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Telegram account already linked to another user"
        )

    user.telegram_id = telegram_id
    user.updated_at = datetime.utcnow()
    await db.commit()

    logger.info(f"Telegram linked: {telegram_id} -> {user.id}")

    return {"success": True, "message": "Telegram account linked"}
