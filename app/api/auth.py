"""
Authentication API routes for SoVAni Crosspost.
Telegram Login + JWT authentication.
"""

import hashlib
import hmac
import time
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.logging import get_logger
from .deps import get_db_session

logger = get_logger("api.auth")

router = APIRouter(prefix="/auth", tags=["Authentication"])


# ==================== SCHEMAS ====================

class TelegramAuthData(BaseModel):
    """Telegram Login Widget data."""
    id: int = Field(..., description="Telegram user ID")
    first_name: str = Field(..., description="User first name")
    last_name: Optional[str] = Field(None, description="User last name")
    username: Optional[str] = Field(None, description="Telegram username")
    photo_url: Optional[str] = Field(None, description="Profile photo URL")
    auth_date: int = Field(..., description="Auth timestamp")
    hash: str = Field(..., description="Data hash for verification")


class AuthResponse(BaseModel):
    """Authentication response."""
    success: bool
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict


class UserResponse(BaseModel):
    """User info response."""
    id: str
    telegram_id: int
    telegram_username: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    photo_url: Optional[str]
    subscription_plan: str
    subscription_expires_at: Optional[datetime]
    demo_started_at: Optional[datetime]
    demo_days_left: Optional[int]
    image_gen_provider: str
    posts_count_this_month: int
    images_generated_this_month: int
    is_active: bool
    created_at: datetime


# ==================== HELPERS ====================

def verify_telegram_auth(data: TelegramAuthData) -> bool:
    """Verify Telegram Login Widget data."""
    # Check auth_date is not too old (24 hours)
    if time.time() - data.auth_date > 86400:
        return False
    
    # Build data-check-string
    check_dict = {
        "id": str(data.id),
        "first_name": data.first_name,
        "auth_date": str(data.auth_date),
    }
    if data.last_name:
        check_dict["last_name"] = data.last_name
    if data.username:
        check_dict["username"] = data.username
    if data.photo_url:
        check_dict["photo_url"] = data.photo_url
    
    # Sort and join
    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(check_dict.items())
    )
    
    # Create secret key from bot token
    secret_key = hashlib.sha256(settings.TG_BOT_TOKEN.encode()).digest()
    
    # Calculate hash
    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256
    ).hexdigest()
    
    return calculated_hash == data.hash


def create_jwt_token(user_id: str, telegram_id: int) -> tuple[str, int]:
    """Create JWT access token."""
    expires_in = 60 * 60 * 24 * 7  # 7 days
    payload = {
        "sub": user_id,
        "telegram_id": telegram_id,
        "exp": datetime.utcnow() + timedelta(seconds=expires_in),
        "iat": datetime.utcnow(),
    }
    token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm="HS256")
    return token, expires_in


def decode_jwt_token(token: str) -> Optional[dict]:
    """Decode and verify JWT token."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=["HS256"])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


# ==================== ROUTES ====================

@router.post("/telegram", response_model=AuthResponse)
async def telegram_login(
    auth_data: TelegramAuthData,
    db: AsyncSession = Depends(get_db_session)
):
    """
    Authenticate user via Telegram Login Widget.
    Creates new user if not exists, returns JWT token.
    """
    # Verify Telegram auth data
    if not verify_telegram_auth(auth_data):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Telegram auth data"
        )
    
    # Import here to avoid circular imports
    from ..models.entities import User, SubscriptionPlan
    
    # Find or create user
    result = await db.execute(
        select(User).where(User.telegram_id == auth_data.id)
    )
    user = result.scalar_one_or_none()
    
    if user is None:
        # Create new user with demo subscription
        user = User(
            telegram_id=auth_data.id,
            telegram_username=auth_data.username,
            telegram_first_name=auth_data.first_name,
            telegram_last_name=auth_data.last_name,
            telegram_photo_url=auth_data.photo_url,
            subscription_plan=SubscriptionPlan.DEMO,
            demo_started_at=datetime.utcnow(),
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        logger.info(f"New user created: {user.id} (tg: {auth_data.id})")
    else:
        # Update user info
        user.telegram_username = auth_data.username
        user.telegram_first_name = auth_data.first_name
        user.telegram_last_name = auth_data.last_name
        user.telegram_photo_url = auth_data.photo_url
        user.updated_at = datetime.utcnow()
        await db.commit()
        logger.info(f"User logged in: {user.id} (tg: {auth_data.id})")
    
    # Calculate demo days left
    demo_days_left = None
    if user.subscription_plan == SubscriptionPlan.DEMO and user.demo_started_at:
        days_passed = (datetime.utcnow() - user.demo_started_at).days
        demo_days_left = max(0, 7 - days_passed)
    
    # Create JWT token
    token, expires_in = create_jwt_token(str(user.id), user.telegram_id)
    
    return AuthResponse(
        success=True,
        access_token=token,
        expires_in=expires_in,
        user={
            "id": str(user.id),
            "telegram_id": user.telegram_id,
            "telegram_username": user.telegram_username,
            "first_name": user.telegram_first_name,
            "last_name": user.telegram_last_name,
            "photo_url": user.telegram_photo_url,
            "subscription_plan": user.subscription_plan.value,
            "demo_days_left": demo_days_left,
            "image_gen_provider": user.image_gen_provider.value,
        }
    )


@router.get("/me", response_model=UserResponse)
async def get_current_user(
    db: AsyncSession = Depends(get_db_session),
    token: str = None  # Will be extracted from header in deps
):
    """Get current authenticated user info."""
    # This will be implemented with proper auth dependency
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Use Authorization header"
    )


@router.post("/logout")
async def logout():
    """Logout (client-side token removal)."""
    return {"success": True, "message": "Logged out successfully"}
