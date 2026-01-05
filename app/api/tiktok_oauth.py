"""
TikTok OAuth API routes.
Handles TikTok Login Kit flow for connecting user accounts.
"""

import secrets
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.logging import get_logger
from ..core.security import SecurityUtils
from .deps import get_db_async_session, get_current_user, get_current_user_optional

logger = get_logger("api.tiktok_oauth")

router = APIRouter(prefix="/oauth/tiktok", tags=["TikTok OAuth"])


# TikTok OAuth endpoints
TIKTOK_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
TIKTOK_USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"

# Required scopes for content posting
TIKTOK_SCOPES = [
    "user.info.basic",      # Get username, display name, avatar
    "user.info.profile",    # Get bio link
    "user.info.stats",      # Get follower count
    "video.upload",         # Upload videos (required for posting)
    "video.publish",        # Publish videos (for approved apps)
    "video.list",           # List user videos
]

# State storage (in production, use Redis)
_oauth_states: dict = {}


class TikTokAuthURLResponse(BaseModel):
    auth_url: str
    state: str


class TikTokCallbackResponse(BaseModel):
    success: bool
    message: str
    account_id: Optional[str] = None
    username: Optional[str] = None
    display_name: Optional[str] = None


@router.get("/authorize", response_model=TikTokAuthURLResponse)
async def get_tiktok_auth_url(
    user = Depends(get_current_user),
):
    """
    Get TikTok authorization URL.
    User will be redirected to TikTok to grant permissions.
    """
    if not settings.tiktok_client_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TikTok integration not configured"
        )
    
    # Generate state for CSRF protection
    state = secrets.token_urlsafe(32)
    
    # Store state with user info (expires in 10 minutes)
    _oauth_states[state] = {
        "user_id": str(user.id),
        "created_at": datetime.utcnow(),
        "expires_at": datetime.utcnow() + timedelta(minutes=10),
    }
    
    # Build authorization URL
    params = {
        "client_key": settings.tiktok_client_key,
        "scope": ",".join(TIKTOK_SCOPES),
        "response_type": "code",
        "redirect_uri": settings.tiktok_redirect_uri,
        "state": state,
    }
    
    auth_url = f"{TIKTOK_AUTH_URL}?{urlencode(params)}"
    
    logger.info(f"TikTok auth URL generated for user {user.id}")
    
    return TikTokAuthURLResponse(auth_url=auth_url, state=state)


@router.get("/callback")
async def tiktok_oauth_callback(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    error_description: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db_async_session),
):
    """
    TikTok OAuth callback.
    Exchanges authorization code for access token and creates account.
    """
    # Check for errors from TikTok
    if error:
        logger.warning(f"TikTok OAuth error: {error} - {error_description}")
        # Redirect to frontend with error
        return RedirectResponse(
            url=f"https://crosspost.saleswhisper.pro/dashboard/accounts?error={error}&message={error_description or 'Authorization failed'}"
        )
    
    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing code or state parameter"
        )
    
    # Validate state
    state_data = _oauth_states.get(state)
    if not state_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired state"
        )
    
    if datetime.utcnow() > state_data["expires_at"]:
        del _oauth_states[state]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="State expired, please try again"
        )
    
    user_id = state_data["user_id"]
    del _oauth_states[state]  # One-time use
    
    # Exchange code for tokens
    try:
        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                TIKTOK_TOKEN_URL,
                data={
                    "client_key": settings.tiktok_client_key,
                    "client_secret": settings.tiktok_client_secret.get_secret_value(),
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": settings.tiktok_redirect_uri,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            
            if token_response.status_code != 200:
                logger.error(f"TikTok token error: {token_response.text}")
                return RedirectResponse(
                    url=f"https://crosspost.saleswhisper.pro/dashboard/accounts?error=token_error&message=Failed to get access token"
                )
            
            token_data = token_response.json()
            
            if "error" in token_data:
                logger.error(f"TikTok token error: {token_data}")
                return RedirectResponse(
                    url=f"https://crosspost.saleswhisper.pro/dashboard/accounts?error={token_data.get('error')}&message={token_data.get('error_description', 'Token error')}"
                )
            
            access_token = token_data.get("access_token")
            refresh_token = token_data.get("refresh_token")
            open_id = token_data.get("open_id")
            expires_in = token_data.get("expires_in", 86400)
            scope = token_data.get("scope", "")
            
    except Exception as e:
        logger.exception(f"TikTok token exchange failed: {e}")
        return RedirectResponse(
            url=f"https://crosspost.saleswhisper.pro/dashboard/accounts?error=exchange_failed&message=Token exchange failed"
        )
    
    # Get user info from TikTok
    try:
        async with httpx.AsyncClient() as client:
            user_response = await client.get(
                TIKTOK_USER_INFO_URL,
                params={"fields": "open_id,union_id,avatar_url,display_name,username"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            
            if user_response.status_code == 200:
                user_data = user_response.json().get("data", {}).get("user", {})
                username = user_data.get("username", "")
                display_name = user_data.get("display_name", username)
                avatar_url = user_data.get("avatar_url", "")
            else:
                logger.warning(f"Failed to get TikTok user info: {user_response.text}")
                username = ""
                display_name = f"TikTok User {open_id[:8]}"
                avatar_url = ""
                
    except Exception as e:
        logger.exception(f"TikTok user info failed: {e}")
        username = ""
        display_name = f"TikTok User {open_id[:8]}"
        avatar_url = ""
    
    # Save account to database
    try:
        from uuid import UUID
        from ..models.entities import SocialAccount, UserSocialAccount, Platform, User
        
        # Get user
        user_result = await db.execute(
            select(User).where(User.id == UUID(user_id))
        )
        user = user_result.scalar_one_or_none()
        
        if not user:
            return RedirectResponse(
                url=f"https://crosspost.saleswhisper.pro/dashboard/accounts?error=user_not_found&message=User session expired"
            )
        
        # Check if account already exists
        existing_result = await db.execute(
            select(SocialAccount).where(
                SocialAccount.platform == Platform.TIKTOK,
                SocialAccount.platform_user_id == open_id
            )
        )
        existing_account = existing_result.scalar_one_or_none()
        
        if existing_account:
            # Update existing account tokens
            existing_account.access_token = SecurityUtils.encrypt_token(access_token)
            existing_account.refresh_token = SecurityUtils.encrypt_token(refresh_token) if refresh_token else None
            existing_account.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            existing_account.username = username or existing_account.username
            existing_account.display_name = display_name or existing_account.display_name
            existing_account.profile_picture_url = avatar_url or existing_account.profile_picture_url
            existing_account.is_active = True
            existing_account.updated_at = datetime.utcnow()
            
            account = existing_account
            
            # Check if already linked to user
            link_result = await db.execute(
                select(UserSocialAccount).where(
                    UserSocialAccount.user_id == user.id,
                    UserSocialAccount.account_id == existing_account.id
                )
            )
            if not link_result.scalar_one_or_none():
                # Link to user
                user_account = UserSocialAccount(
                    user_id=user.id,
                    account_id=existing_account.id,
                    can_publish=True,
                    is_primary=False,
                )
                db.add(user_account)
        else:
            # Create new account
            account = SocialAccount(
                platform=Platform.TIKTOK,
                platform_user_id=open_id,
                username=username,
                display_name=display_name,
                profile_picture_url=avatar_url,
                access_token=SecurityUtils.encrypt_token(access_token),
                refresh_token=SecurityUtils.encrypt_token(refresh_token) if refresh_token else None,
                token_expires_at=datetime.utcnow() + timedelta(seconds=expires_in),
                is_active=True,
                is_verified=True,
                credentials={
                    "scope": scope,
                    "granted_scopes": scope.split(",") if scope else [],
                },
            )
            db.add(account)
            await db.flush()
            
            # Link to user
            user_account = UserSocialAccount(
                user_id=user.id,
                account_id=account.id,
                can_publish=True,
                is_primary=False,
            )
            db.add(user_account)
        
        await db.commit()
        
        logger.info(f"TikTok account connected: {username or open_id} for user {user_id}")
        
        # Redirect to success page
        return RedirectResponse(
            url=f"https://crosspost.saleswhisper.pro/dashboard/accounts?success=true&platform=tiktok&username={username or display_name}"
        )
        
    except Exception as e:
        logger.exception(f"Failed to save TikTok account: {e}")
        await db.rollback()
        return RedirectResponse(
            url=f"https://crosspost.saleswhisper.pro/dashboard/accounts?error=save_failed&message=Failed to save account"
        )


@router.post("/refresh")
async def refresh_tiktok_token(
    account_id: str,
    user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_async_session),
):
    """Refresh TikTok access token."""
    from uuid import UUID
    from ..models.entities import SocialAccount, UserSocialAccount, Platform
    
    # Get account
    result = await db.execute(
        select(SocialAccount, UserSocialAccount)
        .join(UserSocialAccount, SocialAccount.id == UserSocialAccount.account_id)
        .where(
            SocialAccount.id == UUID(account_id),
            UserSocialAccount.user_id == user.id,
            SocialAccount.platform == Platform.TIKTOK,
        )
    )
    row = result.first()
    
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="TikTok account not found"
        )
    
    account, _ = row
    
    if not account.refresh_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No refresh token available, please reconnect account"
        )
    
    # Refresh token
    try:
        refresh_token = SecurityUtils.decrypt_token(account.refresh_token)
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                TIKTOK_TOKEN_URL,
                data={
                    "client_key": settings.tiktok_client_key,
                    "client_secret": settings.tiktok_client_secret.get_secret_value(),
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Failed to refresh TikTok token"
                )
            
            data = response.json()
            
            account.access_token = SecurityUtils.encrypt_token(data["access_token"])
            if data.get("refresh_token"):
                account.refresh_token = SecurityUtils.encrypt_token(data["refresh_token"])
            account.token_expires_at = datetime.utcnow() + timedelta(seconds=data.get("expires_in", 86400))
            account.updated_at = datetime.utcnow()
            
            await db.commit()
            
            return {"success": True, "message": "Token refreshed"}
            
    except Exception as e:
        logger.exception(f"Token refresh failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Token refresh failed"
        )
