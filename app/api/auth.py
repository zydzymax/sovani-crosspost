"""
Authentication API routes for SalesWhisper Crosspost.
Email-based authentication with JWT tokens.
"""

import hashlib
import hmac
import time
import random
import string
import re
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, EmailStr
import jwt
import httpx
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.logging import get_logger
from .deps import get_db_async_session, get_redis_client
from ..services.email_service import EmailService, EmailConfig, get_email_service, init_email_service

logger = get_logger("api.auth")

router = APIRouter(prefix="/auth", tags=["Authentication"])

# Code storage prefix
AUTH_CODE_PREFIX = "auth:code:email:"
AUTH_CODE_TTL = 300  # 5 minutes

# Initialize email service
try:
    email_config = EmailConfig(
        smtp_host=settings.email.host,
        smtp_port=settings.email.port,
        smtp_user=settings.email.user,
        smtp_password=settings.email.password.get_secret_value() if settings.email.password else "",
        from_email=settings.email.from_email,
        use_ssl=settings.email.use_ssl,
        timeout=settings.email.timeout
    )
    init_email_service(email_config)
except Exception as e:
    logger.warning(f"Email service not initialized: {e}")


# ==================== SCHEMAS ====================

class TelegramAuthData(BaseModel):
    """Telegram Login Widget data (legacy support)."""
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


class SendCodeRequest(BaseModel):
    """Request to send auth code via email."""
    email: EmailStr = Field(..., description="User email address")


class SendCodeResponse(BaseModel):
    """Response after sending code."""
    success: bool
    message: str
    expires_in: int = 300


class VerifyCodeRequest(BaseModel):
    """Request to verify auth code."""
    email: EmailStr = Field(..., description="User email address")
    code: str = Field(..., description="6-digit verification code")


class UserResponse(BaseModel):
    """User info response."""
    id: str
    email: Optional[str]
    email_verified: bool = False
    telegram_id: Optional[int]
    telegram_username: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    company_name: Optional[str]
    photo_url: Optional[str]
    is_active: bool
    created_at: datetime


class SubscriptionInfo(BaseModel):
    """User subscription info."""
    product_code: str
    product_name: str
    plan_code: str
    plan_name: str
    status: str
    expires_at: Optional[datetime]


class MeResponse(BaseModel):
    """Full user info with subscriptions."""
    user: UserResponse
    subscriptions: list[SubscriptionInfo] = []


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
    bot_token = settings.telegram.bot_token.get_secret_value()
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    
    # Calculate hash
    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256
    ).hexdigest()
    
    return calculated_hash == data.hash


def create_jwt_token(user_id: str, email: str) -> tuple[str, int]:
    """Create JWT access token for email-based auth."""
    expires_in = 60 * 60 * 24 * 7  # 7 days
    payload = {
        "sub": user_id,
        "email": email,
        "exp": datetime.utcnow() + timedelta(seconds=expires_in),
        "iat": datetime.utcnow(),
        "iss": "saleswhisper.pro",
        "aud": ["crosspost", "headofsales", "saleswhisper"],
    }
    secret_key = settings.security.jwt_secret_key.get_secret_value()
    token = jwt.encode(payload, secret_key, algorithm="HS256")
    return token, expires_in


def create_jwt_token_legacy(user_id: str, telegram_id: int) -> tuple[str, int]:
    """Create JWT access token (legacy Telegram-based)."""
    expires_in = 60 * 60 * 24 * 7  # 7 days
    payload = {
        "sub": user_id,
        "telegram_id": telegram_id,
        "exp": datetime.utcnow() + timedelta(seconds=expires_in),
        "iat": datetime.utcnow(),
    }
    secret_key = settings.security.jwt_secret_key.get_secret_value()
    token = jwt.encode(payload, secret_key, algorithm="HS256")
    return token, expires_in


def decode_jwt_token(token: str) -> Optional[dict]:
    """Decode and verify JWT token."""
    try:
        secret_key = settings.security.jwt_secret_key.get_secret_value()
        payload = jwt.decode(
            token,
            secret_key,
            algorithms=["HS256"],
            options={"verify_aud": False}  # Allow any audience for cross-service
        )
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


# ==================== ROUTES ====================

@router.post("/telegram", response_model=AuthResponse)
async def telegram_login(
    auth_data: TelegramAuthData,
    db: AsyncSession = Depends(get_db_async_session)
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
    
    # Create JWT token (use email if available, else legacy telegram)
    if user.email:
        token, expires_in = create_jwt_token(str(user.id), user.email)
    else:
        token, expires_in = create_jwt_token_legacy(str(user.id), user.telegram_id)

    return AuthResponse(
        success=True,
        access_token=token,
        expires_in=expires_in,
        user={
            "id": str(user.id),
            "email": user.email,
            "telegram_id": user.telegram_id,
            "telegram_username": user.telegram_username,
            "first_name": user.telegram_first_name,
            "last_name": user.telegram_last_name,
            "photo_url": user.telegram_photo_url,
        }
    )


@router.get("/me", response_model=MeResponse)
async def get_me(
    db: AsyncSession = Depends(get_db_async_session),
    # Will add proper auth dependency later
):
    """Get current authenticated user info with subscriptions."""
    # This will be implemented with proper auth dependency
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Use Authorization header with Bearer token"
    )


@router.post("/logout")
async def logout():
    """Logout (client-side token removal)."""
    return {"success": True, "message": "Logged out successfully"}


# ==================== CODE-BASED AUTH ====================

def generate_auth_code() -> str:
    """Generate 6-digit auth code."""
    return ''.join(random.choices(string.digits, k=6))


async def send_telegram_message(chat_id: int, text: str) -> bool:
    """Send message via Telegram Bot API."""
    bot_token = settings.telegram.bot_token.get_secret_value()
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML"
            })
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False


async def get_telegram_user_by_username(username: str) -> Optional[dict]:
    """Get Telegram user info by username using getChat."""
    bot_token = settings.telegram.bot_token.get_secret_value()
    url = f"https://api.telegram.org/bot{bot_token}/getChat"

    # Clean username
    clean_username = username.lstrip('@')

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json={"chat_id": f"@{clean_username}"})
            data = response.json()
            if data.get("ok"):
                return data.get("result")
            return None
        except Exception as e:
            logger.error(f"Failed to get Telegram user: {e}")
            return None


@router.post("/send-code", response_model=SendCodeResponse)
async def send_auth_code(
    request: SendCodeRequest,
    redis = Depends(get_redis_client)
):
    """
    Send authentication code to user's email.
    Works for both new and existing users.
    """
    email = request.email.lower().strip()

    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is required"
        )

    # Basic email validation
    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid email format"
        )

    # Check rate limiting (max 5 codes per email per hour)
    rate_key = f"auth:rate:{email}"
    rate_count = await redis.get(rate_key)
    if rate_count and int(rate_count) >= 5:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Слишком много запросов. Попробуйте через час."
        )

    # Generate code
    code = generate_auth_code()

    # Store in Redis with email as key
    redis_key = f"{AUTH_CODE_PREFIX}{email}"
    await redis.setex(redis_key, AUTH_CODE_TTL, code)

    # Increment rate limit counter
    await redis.incr(rate_key)
    await redis.expire(rate_key, 3600)  # 1 hour expiry

    # Send code via email
    sent = False
    try:
        email_service = get_email_service()
        sent = await email_service.send_auth_code(
            to_email=email,
            code=code,
            expires_minutes=5
        )
    except RuntimeError:
        # Email service not configured
        logger.warning(f"Email service not configured")
    except Exception as e:
        logger.error(f"Email sending error: {e}")

    # In development mode, log the code if email wasn't sent
    if not sent:
        if settings.app.is_development:
            logger.warning(f"DEV MODE: Auth code for {email}: {code}")
            sent = True  # Allow login in dev mode
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Не удалось отправить код. Попробуйте позже."
            )

    logger.info(f"Auth code {'sent' if sent else 'generated'} for {email}")

    return SendCodeResponse(
        success=True,
        message=f"Код отправлен на {email}",
        expires_in=AUTH_CODE_TTL
    )


@router.post("/verify-code", response_model=AuthResponse)
async def verify_auth_code(
    request: VerifyCodeRequest,
    db: AsyncSession = Depends(get_db_async_session),
    redis = Depends(get_redis_client)
):
    """
    Verify email authentication code and return JWT token.
    Creates new user if not exists.
    """
    # Clean inputs
    email = request.email.lower().strip()
    code = request.code.strip()

    if not email or not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email and code are required"
        )

    # Get stored code from Redis
    redis_key = f"{AUTH_CODE_PREFIX}{email}"
    stored_code = await redis.get(redis_key)

    if not stored_code:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Код истёк или не существует. Запросите новый код."
        )

    # Verify code
    if code != stored_code:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный код"
        )

    # Delete used code
    await redis.delete(redis_key)

    # Import here to avoid circular imports
    from ..models.entities import User, SubscriptionPlan

    # Find user by email
    result = await db.execute(
        select(User).where(User.email == email)
    )
    user = result.scalar_one_or_none()

    if user is None:
        # Create new user with demo subscription
        user = User(
            email=email,
            email_verified=True,  # Verified by code
            subscription_plan=SubscriptionPlan.DEMO,
            demo_started_at=datetime.utcnow(),
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        logger.info(f"New user created via email auth: {user.id} ({email})")
    else:
        # Update user - mark email as verified
        if not user.email_verified:
            user.email_verified = True
        user.last_login_at = datetime.utcnow()
        user.updated_at = datetime.utcnow()
        await db.commit()
        logger.info(f"User logged in via email auth: {user.id} ({email})")

    # Create JWT token
    token, expires_in = create_jwt_token(str(user.id), email)

    return AuthResponse(
        success=True,
        access_token=token,
        expires_in=expires_in,
        user={
            "id": str(user.id),
            "email": user.email,
            "email_verified": user.email_verified,
            "telegram_id": user.telegram_id,
            "telegram_username": user.telegram_username,
            "first_name": user.telegram_first_name or user.first_name,
            "last_name": user.telegram_last_name or user.last_name,
            "company_name": user.company_name,
            "is_active": user.is_active,
        }
    )
