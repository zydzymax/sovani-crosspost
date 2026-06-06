"""
TikTok OAuth API routes.
Handles TikTok Login Kit flow for connecting user accounts.
"""

import json
import os
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.logging import get_logger
from ..core.security import decrypt_data, encrypt_data
from .deps import get_current_user, get_db_async_session, get_redis_client

logger = get_logger("api.tiktok_oauth")

router = APIRouter(prefix="/oauth/tiktok", tags=["TikTok OAuth"])


# TikTok OAuth endpoints
TIKTOK_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
TIKTOK_USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"
DASHBOARD_ACCOUNTS_URL = "https://crosspost.saleswhisper.pro/dashboard/accounts"

# Required scopes for content posting
TIKTOK_SCOPES = [
    "user.info.basic",  # Get username, display name, avatar
    "user.info.profile",  # Get bio link
    "user.info.stats",  # Get follower count
    "video.upload",  # Upload videos (required for posting)
    "video.publish",  # Publish videos (for approved apps)
    "video.list",  # List user videos
]

# Redis-backed state storage with in-memory fallback.
OAUTH_STATE_PREFIX = "oauth:tiktok:state:"
OAUTH_STATE_TTL_SECONDS = 600
_oauth_states: dict = {}


def _dashboard_redirect(**params: str) -> RedirectResponse:
    clean_params = {k: v for k, v in params.items() if v}
    if not clean_params:
        return RedirectResponse(url=DASHBOARD_ACCOUNTS_URL)
    return RedirectResponse(url=f"{DASHBOARD_ACCOUNTS_URL}?{urlencode(clean_params)}")


def _utcnow() -> datetime:
    return datetime.utcnow()


def _fallback_display_name(open_id: str | None) -> str:
    return f"TikTok User {(open_id or 'unknown')[:8]}"


def _is_strict_oauth_state_mode() -> bool:
    env_override = str(os.getenv("TIKTOK_OAUTH_STATE_STRICT", "")).strip().lower()
    if env_override in {"1", "true", "yes", "on"}:
        return True
    if env_override in {"0", "false", "no", "off"}:
        return False
    app_env = getattr(getattr(settings, "app", None), "environment", "")
    return app_env in {"staging", "production"}


def _serialize_state(payload: dict[str, str]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _deserialize_state(raw_value: str | None) -> dict[str, str] | None:
    if not raw_value:
        return None
    try:
        parsed = json.loads(raw_value)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


async def _get_optional_redis():
    try:
        return await get_redis_client()
    except HTTPException as exc:
        if _is_strict_oauth_state_mode():
            logger.error(
                "Redis unavailable for TikTok OAuth state in strict mode",
                status_code=exc.status_code,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OAuth state storage is temporarily unavailable",
            ) from exc
        logger.warning("Redis unavailable for TikTok OAuth state, using in-memory fallback")
        return None


async def _store_oauth_state(state: str, user_id: str):
    payload = {"user_id": user_id, "created_at": _utcnow().isoformat()}
    redis_client = await _get_optional_redis()

    if redis_client is not None:
        key = f"{OAUTH_STATE_PREFIX}{state}"
        try:
            await redis_client.setex(key, OAUTH_STATE_TTL_SECONDS, _serialize_state(payload))
            return
        except Exception as e:
            logger.warning("Failed to store TikTok OAuth state in Redis", error=str(e))

    # Fallback for local/dev resilience.
    _oauth_states[state] = {
        "user_id": user_id,
        "created_at": _utcnow(),
        "expires_at": _utcnow() + timedelta(seconds=OAUTH_STATE_TTL_SECONDS),
    }


async def _consume_oauth_state(state: str) -> dict | None:
    redis_client = await _get_optional_redis()
    if redis_client is not None:
        key = f"{OAUTH_STATE_PREFIX}{state}"
        try:
            raw_value = await redis_client.execute_command("GETDEL", key)
        except Exception:
            # Redis < 6.2 fallback (best effort)
            raw_value = await redis_client.get(key)
            if raw_value is not None:
                await redis_client.delete(key)
        parsed = _deserialize_state(raw_value)
        if parsed:
            return parsed

    # In-memory fallback
    state_data = _oauth_states.pop(state, None)
    if not state_data:
        return None
    if _utcnow() > state_data.get("expires_at", _utcnow()):
        return None
    return state_data


class TikTokAuthURLResponse(BaseModel):
    auth_url: str
    state: str


class TikTokCallbackResponse(BaseModel):
    success: bool
    message: str
    account_id: str | None = None
    username: str | None = None
    display_name: str | None = None


@router.get("/authorize", response_model=TikTokAuthURLResponse)
async def get_tiktok_auth_url(
    user=Depends(get_current_user),
):
    """
    Get TikTok authorization URL.
    User will be redirected to TikTok to grant permissions.
    """
    if not settings.tiktok_client_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="TikTok integration not configured")

    # Generate state for CSRF protection
    state = secrets.token_urlsafe(32)

    # Store state with user info (expires in 10 minutes)
    await _store_oauth_state(state, str(user.id))

    # Build authorization URL
    params = {
        "client_key": settings.tiktok_client_key,
        "scope": ",".join(TIKTOK_SCOPES),
        "response_type": "code",
        "redirect_uri": settings.tiktok_redirect_uri,
        "state": state,
    }

    auth_url = f"{TIKTOK_AUTH_URL}?{urlencode(params)}"

    logger.info("TikTok auth URL generated", user_id=str(user.id))

    return TikTokAuthURLResponse(auth_url=auth_url, state=state)


@router.get("/callback")
async def tiktok_oauth_callback(
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    error_description: str | None = Query(None),
    db: AsyncSession = Depends(get_db_async_session),
):
    """
    TikTok OAuth callback.
    Exchanges authorization code for access token and creates account.
    """
    # Check for errors from TikTok
    if error:
        logger.warning("TikTok OAuth error", error=error, error_description=error_description)
        # Redirect to frontend with error
        return _dashboard_redirect(error=error, message=error_description or "Authorization failed")

    if not code or not state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing code or state parameter")

    # Validate state
    state_data = await _consume_oauth_state(state)
    if not state_data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired state")

    user_id = state_data["user_id"]

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
                logger.error("TikTok token error", response_text=token_response.text)
                return _dashboard_redirect(error="token_error", message="Failed to get access token")

            token_data = token_response.json()

            if "error" in token_data:
                logger.error("TikTok token response contains error", token_data=token_data)
                return _dashboard_redirect(
                    error=token_data.get("error"),
                    message=token_data.get("error_description", "Token error"),
                )

            access_token = token_data.get("access_token")
            refresh_token = token_data.get("refresh_token")
            open_id = token_data.get("open_id")
            expires_in = token_data.get("expires_in", 86400)
            scope = token_data.get("scope", "")
            if not open_id:
                logger.error("TikTok token response missing open_id", token_data=token_data)
                return _dashboard_redirect(error="token_error", message="Missing TikTok account identifier")

    except Exception as e:
        logger.exception("TikTok token exchange failed", error=str(e))
        return _dashboard_redirect(error="exchange_failed", message="Token exchange failed")

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
                logger.warning("Failed to get TikTok user info", response_text=user_response.text)
                username = ""
                display_name = _fallback_display_name(open_id)
                avatar_url = ""

    except Exception as e:
        logger.exception("TikTok user info failed", error=str(e))
        username = ""
        display_name = _fallback_display_name(open_id)
        avatar_url = ""

    # Save account to database
    try:
        from uuid import UUID

        from ..models.entities import Platform, SocialAccount, User, UserSocialAccount

        # Get user
        user_result = await db.execute(select(User).where(User.id == UUID(user_id)))
        user = user_result.scalar_one_or_none()

        if not user:
            return _dashboard_redirect(error="user_not_found", message="User session expired")

        # Check if account already exists
        existing_result = await db.execute(
            select(SocialAccount).where(
                SocialAccount.platform == Platform.TIKTOK, SocialAccount.platform_user_id == open_id
            )
        )
        existing_account = existing_result.scalar_one_or_none()

        if existing_account:
            # Update existing account tokens
            existing_account.access_token = encrypt_data(access_token)
            existing_account.refresh_token = encrypt_data(refresh_token) if refresh_token else None
            existing_account.token_expires_at = _utcnow() + timedelta(seconds=expires_in)
            existing_account.platform_username = username or existing_account.platform_username
            existing_account.platform_display_name = display_name or existing_account.platform_display_name
            extra_credentials = existing_account.extra_credentials if isinstance(existing_account.extra_credentials, dict) else {}
            extra_credentials.update(
                {
                    "avatar_url": avatar_url,
                    "scope": scope,
                    "granted_scopes": scope.split(",") if scope else [],
                    "open_id": open_id,
                }
            )
            existing_account.extra_credentials = extra_credentials
            existing_account.is_active = True
            existing_account.updated_at = _utcnow()

            account = existing_account

            # Check if already linked to user
            link_result = await db.execute(
                select(UserSocialAccount).where(
                    UserSocialAccount.user_id == user.id, UserSocialAccount.account_id == existing_account.id
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
                platform_username=username,
                platform_display_name=display_name,
                access_token=encrypt_data(access_token),
                refresh_token=encrypt_data(refresh_token) if refresh_token else None,
                token_expires_at=_utcnow() + timedelta(seconds=expires_in),
                is_active=True,
                is_verified=True,
                extra_credentials={
                    "avatar_url": avatar_url,
                    "open_id": open_id,
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

        logger.info("TikTok account connected", account=username or open_id, user_id=user_id)

        # Redirect to success page
        return _dashboard_redirect(success="true", platform="tiktok", username=username or display_name)

    except Exception as e:
        logger.exception("Failed to save TikTok account", error=str(e))
        await db.rollback()
        return _dashboard_redirect(error="save_failed", message="Failed to save account")


@router.post("/refresh")
async def refresh_tiktok_token(
    account_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db_async_session),
):
    """Refresh TikTok access token."""
    from uuid import UUID

    from ..models.entities import Platform, SocialAccount, UserSocialAccount

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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="TikTok account not found")

    account, _ = row

    if not account.refresh_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No refresh token available, please reconnect account"
        )

    # Refresh token
    try:
        try:
            refresh_token = decrypt_data(account.refresh_token)
        except Exception:
            refresh_token = account.refresh_token

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
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to refresh TikTok token")

            data = response.json()

            account.access_token = encrypt_data(data["access_token"])
            if data.get("refresh_token"):
                account.refresh_token = encrypt_data(data["refresh_token"])
            account.token_expires_at = _utcnow() + timedelta(seconds=data.get("expires_in", 86400))
            account.updated_at = _utcnow()

            await db.commit()

            return {"success": True, "message": "Token refreshed"}

    except Exception as e:
        logger.exception("Token refresh failed", error=str(e))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Token refresh failed")
