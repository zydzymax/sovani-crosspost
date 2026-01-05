"""
FastAPI dependency providers for SalesWhisper Crosspost.

This module provides:
- Database session dependencies
- Redis client dependencies
- Configuration access
- Authentication and authorization
- Request validation utilities
"""

from typing import Generator, Optional
import redis.asyncio as redis

from fastapi import Depends, HTTPException, status, Request, Header
from sqlalchemy.orm import Session

from ..core.config import settings, Settings
from ..core.logging import get_logger
from ..core.security import verify_webhook, decode_jwt_token, SecurityUtils
from ..models.db import db_manager


logger = get_logger("api.deps")


def get_settings() -> Settings:
    """
    Get application settings.
    
    Returns:
        Settings instance
    """
    return settings


def get_db_session() -> Generator[Session, None, None]:
    """
    Get database session dependency.

    Yields:
        SQLAlchemy session
    """
    with db_manager.get_sync_session() as session:
        logger.debug("Database session created")
        yield session
        logger.debug("Database session closed")


async def get_db_async_session():
    """
    Get async database session dependency.

    Yields:
        Async SQLAlchemy session
    """
    session = await db_manager.get_async_session()
    try:
        logger.debug("Async database session created")
        yield session
    finally:
        await session.close()
        logger.debug("Async database session closed")


async def get_redis_client() -> redis.Redis:
    """
    Get Redis client dependency.
    
    Returns:
        Redis client instance
    """
    try:
        client = redis.from_url(
            settings.get_redis_url(),
            encoding="utf-8",
            decode_responses=True,
            max_connections=settings.redis.max_connections,
            retry_on_timeout=settings.redis.retry_on_timeout,
            socket_timeout=settings.redis.socket_timeout
        )
        
        # Test connection
        await client.ping()
        logger.debug("Redis connection established")
        
        return client
        
    except Exception as e:
        logger.error("Redis connection failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Redis connection error"
        )


def verify_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings)
) -> str:
    """
    Verify API key authentication.
    
    Args:
        x_api_key: API key from header
        settings: Application settings
        
    Returns:
        Verified API key
        
    Raises:
        HTTPException: If API key is invalid
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required",
            headers={"WWW-Authenticate": "APIKey"}
        )
    
    try:
        # In a real implementation, this would validate against a database
        # For now, we'll just check if it's present and log the attempt
        logger.info("API key authentication attempted", api_key_prefix=x_api_key[:8] + "...")
        
        # Placeholder validation - in production this would check against database
        if len(x_api_key) < 16:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key format"
            )
        
        return x_api_key
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("API key validation error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication error"
        )


def verify_jwt_token(
    authorization: Optional[str] = Header(None),
    settings: Settings = Depends(get_settings)
) -> dict:
    """
    Verify JWT token authentication.
    
    Args:
        authorization: Authorization header with JWT token
        settings: Application settings
        
    Returns:
        Decoded token payload
        
    Raises:
        HTTPException: If token is invalid
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header required",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    try:
        # Extract token from "Bearer <token>" format
        scheme, token = authorization.split()
        if scheme.lower() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication scheme"
            )
        
        # Verify and decode token
        payload = decode_jwt_token(token)
        
        logger.debug("JWT token verified", user_id=payload.get("user_id"))
        return payload
        
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format"
        )
    except Exception as e:
        logger.warning("JWT token verification failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )


def verify_telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(None),
    settings: Settings = Depends(get_settings)
) -> bool:
    """
    Verify Telegram webhook signature.
    
    Args:
        request: FastAPI request object
        x_telegram_bot_api_secret_token: Telegram secret token header
        settings: Application settings
        
    Returns:
        True if webhook is verified
        
    Raises:
        HTTPException: If webhook verification fails
    """
    try:
        # Get webhook secret from settings
        webhook_secret = settings.security.webhook_secret.get_secret_value()
        
        # In production, this would verify the actual webhook signature
        # For now, we'll check the secret token header
        if x_telegram_bot_api_secret_token:
            if not SecurityUtils.constant_time_compare(
                x_telegram_bot_api_secret_token, 
                webhook_secret
            ):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid webhook secret token"
                )
        
        logger.debug("Telegram webhook verified")
        return True
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Webhook verification error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook verification failed"
        )


def get_current_user(
    token_payload: dict = Depends(verify_jwt_token),
    db: Session = Depends(get_db_session)
) -> dict:
    """
    Get current authenticated user.
    
    Args:
        token_payload: JWT token payload
        db: Database session
        
    Returns:
        User information
        
    Raises:
        HTTPException: If user not found
    """
    try:
        user_id = token_payload.get("user_id")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload"
            )
        
        # In production, this would query the user from database
        # For now, return basic user info from token
        user_info = {
            "id": user_id,
            "type": token_payload.get("type", "user"),
            "scopes": token_payload.get("scopes", [])
        }
        
        logger.debug("Current user retrieved", user_id=user_id)
        return user_info
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("User retrieval error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="User retrieval failed"
        )


def require_admin(
    current_user: dict = Depends(get_current_user)
) -> dict:
    """
    Require admin privileges.
    
    Args:
        current_user: Current authenticated user
        
    Returns:
        User information if admin
        
    Raises:
        HTTPException: If user is not admin
    """
    try:
        user_scopes = current_user.get("scopes", [])
        if "admin" not in user_scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin privileges required"
            )
        
        logger.debug("Admin access granted", user_id=current_user["id"])
        return current_user
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Admin check error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authorization check failed"
        )


def get_pagination_params(
    page: int = 1,
    per_page: int = 20,
    max_per_page: int = 100
) -> dict:
    """
    Get pagination parameters with validation.
    
    Args:
        page: Page number (1-based)
        per_page: Items per page
        max_per_page: Maximum items per page
        
    Returns:
        Pagination parameters
        
    Raises:
        HTTPException: If parameters are invalid
    """
    if page < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Page number must be >= 1"
        )
    
    if per_page < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Per page must be >= 1"
        )
    
    if per_page > max_per_page:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Per page must be <= {max_per_page}"
        )
    
    # Calculate offset
    offset = (page - 1) * per_page
    
    return {
        "page": page,
        "per_page": per_page,
        "offset": offset,
        "limit": per_page
    }


def validate_platform(platform: str) -> str:
    """
    Validate platform parameter.
    
    Args:
        platform: Platform name
        
    Returns:
        Validated platform name
        
    Raises:
        HTTPException: If platform is not supported
    """
    supported_platforms = ["instagram", "vk", "tiktok", "youtube"]
    
    if platform.lower() not in supported_platforms:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported platform. Supported: {supported_platforms}"
        )
    
    return platform.lower()


def get_rate_limit_info(
    request: Request,
    redis_client: redis.Redis = Depends(get_redis_client),
    settings: Settings = Depends(get_settings)
) -> dict:
    """
    Get rate limiting information.
    
    Args:
        request: FastAPI request object
        redis_client: Redis client
        settings: Application settings
        
    Returns:
        Rate limit information
    """
    try:
        # Get client IP
        client_ip = request.client.host if request.client else "unknown"
        
        # Rate limit key
        rate_limit_key = f"rate_limit:{client_ip}"
        
        # This is a placeholder implementation
        # In production, this would implement sliding window rate limiting
        current_requests = 0  # Would get from Redis
        
        rate_limit_info = {
            "client_ip": client_ip,
            "current_requests": current_requests,
            "limit": settings.app.api_rate_limit_per_minute,
            "remaining": max(0, settings.app.api_rate_limit_per_minute - current_requests)
        }
        
        logger.debug("Rate limit info retrieved", **rate_limit_info)
        return rate_limit_info
        
    except Exception as e:
        logger.warning("Rate limit check failed", error=str(e))
        # Return permissive values on error
        return {
            "client_ip": "unknown",
            "current_requests": 0,
            "limit": settings.app.api_rate_limit_per_minute,
            "remaining": settings.app.api_rate_limit_per_minute
        }


# Optional dependencies for different authentication methods
def get_optional_api_key(x_api_key: Optional[str] = Header(None, alias="X-API-Key")) -> Optional[str]:
    return x_api_key

def get_optional_jwt_token(authorization: Optional[str] = Header(None)) -> Optional[str]:
    return authorization

OptionalAPIKey = Depends(get_optional_api_key)
OptionalJWTToken = Depends(get_optional_jwt_token)

# ==================== JWT AUTH DEPENDENCIES ====================

import jwt
from datetime import datetime
from sqlalchemy import select

async def get_current_user(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db = Depends(get_db_async_session)
):
    """
    Get current authenticated user from JWT token.
    
    Args:
        authorization: Bearer token from header
        db: Database session
        
    Returns:
        User object
        
    Raises:
        HTTPException: If not authenticated or token invalid
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Extract token from "Bearer <token>"
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token = parts[1]
    
    # Decode JWT
    try:
        secret_key = settings.security.jwt_secret_key.get_secret_value()
        payload = jwt.decode(
            token,
            secret_key,
            algorithms=["HS256"],
            options={"verify_aud": False}  # Cross-service SSO
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Get user from database
    from ..models.entities import User
    
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )
    
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is deactivated",
        )
    
    return user


async def get_current_user_optional(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db = Depends(get_db_async_session)
):
    """
    Get current user if authenticated, None otherwise.
    For endpoints that work both with and without auth.
    """
    if not authorization:
        return None
    
    try:
        return await get_current_user(authorization, db)
    except HTTPException:
        return None


def check_subscription_active(user):
    """
    Check if user has active subscription or demo.
    
    Args:
        user: User object
        
    Raises:
        HTTPException: If subscription expired
    """
    from ..models.entities import SubscriptionPlan
    # Master accounts bypass all subscription checks
    if getattr(user, "is_master", False):
        return
    
    if user.subscription_plan == SubscriptionPlan.DEMO:
        if user.demo_started_at:
            days_passed = (datetime.utcnow() - user.demo_started_at).days
            if days_passed > 7:
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail="Demo period expired. Please upgrade to continue.",
                )
    elif user.subscription_expires_at:
        if datetime.utcnow() > user.subscription_expires_at:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="Subscription expired. Please renew to continue.",
            )
