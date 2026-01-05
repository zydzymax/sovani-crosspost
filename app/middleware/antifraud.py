"""
Anti-Fraud Middleware for FastAPI.

Provides:
- Rate limiting per endpoint
- Bot detection
- IP blocking
- Request logging for fraud analysis
"""

import time
from collections.abc import Callable

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ..core.logging import get_logger
from ..services.antifraud import FraudRiskLevel, antifraud_service

logger = get_logger("middleware.antifraud")


class AntifraudMiddleware(BaseHTTPMiddleware):
    """
    Middleware for anti-fraud protection.

    Performs:
    - Rate limiting based on user tier
    - Bot detection
    - Suspicious activity logging
    """

    def __init__(self, app, redis_client=None):
        super().__init__(app)
        self._redis = redis_client

        # Endpoints to skip rate limiting
        self.skip_endpoints = {
            "/health",
            "/metrics",
            "/docs",
            "/redoc",
            "/openapi.json",
        }

        # Endpoints with stricter limits
        self.strict_endpoints = {
            "/api/v1/auth/telegram": 10,  # 10 per minute
            "/api/v1/cloud-storage/connect": 5,  # 5 per minute
            "/api/v1/video-gen/generate": 3,  # 3 per minute
            "/api/v1/content-plan/generate": 5,  # 5 per minute
        }

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request through antifraud checks."""
        start_time = time.time()

        # Skip certain endpoints
        path = request.url.path
        if any(path.startswith(skip) for skip in self.skip_endpoints):
            return await call_next(request)

        # Get client identifier
        client_ip = self._get_client_ip(request)
        user_id = getattr(request.state, "user_id", None)
        identifier = user_id or client_ip

        # Get user tier for rate limiting
        tier = getattr(request.state, "user_tier", "anonymous")

        # Initialize Redis if available
        if self._redis:
            antifraud_service.set_redis(self._redis)

        try:
            # Check rate limit
            rate_result = await antifraud_service.check_rate_limit(
                identifier=identifier,
                tier=tier,
                endpoint=path
            )

            if not rate_result.allowed:
                logger.warning(
                    "Rate limit exceeded",
                    identifier=identifier[:16] + "..." if len(identifier) > 16 else identifier,
                    path=path,
                    current=rate_result.current_count,
                    limit=rate_result.limit
                )

                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={
                        "error": "rate_limit_exceeded",
                        "message": "Too many requests. Please try again later.",
                        "retry_after": rate_result.retry_after
                    },
                    headers={
                        "X-RateLimit-Limit": str(rate_result.limit),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(int(rate_result.reset_at.timestamp())),
                        "Retry-After": str(rate_result.retry_after)
                    }
                )

            # Check endpoint-specific limits
            if path in self.strict_endpoints:
                strict_limit = self.strict_endpoints[path]
                strict_result = await antifraud_service.check_rate_limit(
                    identifier=f"{identifier}:{path}",
                    tier="strict",
                    endpoint=path
                )
                # Override limit for strict endpoints
                if strict_result.current_count > strict_limit:
                    return JSONResponse(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        content={
                            "error": "endpoint_rate_limit_exceeded",
                            "message": f"Too many requests to {path}. Please wait.",
                            "retry_after": 60
                        },
                        headers={"Retry-After": "60"}
                    )

            # Bot detection for suspicious endpoints
            if path.startswith("/api/v1/auth") or path.startswith("/api/v1/payments"):
                user_agent = request.headers.get("user-agent", "")
                bot_signal = await antifraud_service.check_bot_activity(
                    user_agent=user_agent,
                    ip_address=client_ip
                )

                if bot_signal.risk_level in [FraudRiskLevel.HIGH, FraudRiskLevel.CRITICAL]:
                    logger.warning(
                        "Bot activity detected",
                        ip=client_ip[:8] + "...",
                        path=path,
                        risk=bot_signal.risk_level.value,
                        score=bot_signal.score
                    )

                    # For critical, block immediately
                    if bot_signal.risk_level == FraudRiskLevel.CRITICAL:
                        return JSONResponse(
                            status_code=status.HTTP_403_FORBIDDEN,
                            content={
                                "error": "access_denied",
                                "message": "Automated access detected and blocked."
                            }
                        )

            # Process request
            response = await call_next(request)

            # Add rate limit headers to response
            response.headers["X-RateLimit-Limit"] = str(rate_result.limit)
            response.headers["X-RateLimit-Remaining"] = str(
                max(0, rate_result.limit - rate_result.current_count)
            )
            response.headers["X-RateLimit-Reset"] = str(int(rate_result.reset_at.timestamp()))

            # Log slow requests for analysis
            duration = time.time() - start_time
            if duration > 5.0:  # Requests taking > 5 seconds
                logger.warning(
                    "Slow request detected",
                    path=path,
                    duration=round(duration, 2),
                    identifier=identifier[:16] + "..."
                )

            return response

        except Exception as e:
            logger.error(
                "Antifraud middleware error",
                error=str(e),
                path=path,
                exc_info=True
            )
            # Don't block on middleware errors
            return await call_next(request)

    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP from request."""
        # Check X-Forwarded-For header (for proxied requests)
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # Get first IP in chain (original client)
            return forwarded.split(",")[0].strip()

        # Check X-Real-IP header
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip

        # Fallback to direct connection
        if request.client:
            return request.client.host

        return "unknown"


class IPBlockMiddleware(BaseHTTPMiddleware):
    """
    Middleware for IP-based blocking.

    Checks against:
    - Manually blocked IPs
    - Automatically blocked IPs (from fraud detection)
    """

    def __init__(self, app, redis_client=None):
        super().__init__(app)
        self._redis = redis_client

        # Static blocklist (can be loaded from config)
        self.blocked_ips = set()

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Check if IP is blocked."""
        client_ip = self._get_client_ip(request)

        # Check static blocklist
        if client_ip in self.blocked_ips:
            logger.warning("Blocked IP attempted access", ip=client_ip[:8] + "...")
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"error": "access_denied", "message": "Access denied."}
            )

        # Check Redis blocklist
        if self._redis:
            is_blocked = await self._redis.get(f"antifraud:blocked_ip:{client_ip}")
            if is_blocked:
                logger.warning("Redis-blocked IP attempted access", ip=client_ip[:8] + "...")
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"error": "access_denied", "message": "Access denied."}
                )

        return await call_next(request)

    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP from request."""
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip
        if request.client:
            return request.client.host
        return "unknown"

    def block_ip(self, ip: str):
        """Add IP to static blocklist."""
        self.blocked_ips.add(ip)
        logger.info("IP added to blocklist", ip=ip[:8] + "...")

    def unblock_ip(self, ip: str):
        """Remove IP from static blocklist."""
        self.blocked_ips.discard(ip)
        logger.info("IP removed from blocklist", ip=ip[:8] + "...")


def get_device_fingerprint(request: Request) -> str | None:
    """
    Generate device fingerprint from request headers.

    Components:
    - User-Agent
    - Accept-Language
    - Screen resolution (if provided)
    - Timezone (if provided)
    """
    import hashlib

    components = []

    # User agent
    ua = request.headers.get("user-agent", "")
    components.append(ua)

    # Accept language
    lang = request.headers.get("accept-language", "")
    components.append(lang)

    # Custom fingerprint headers (set by frontend)
    screen = request.headers.get("x-screen-resolution", "")
    timezone = request.headers.get("x-timezone", "")
    components.append(screen)
    components.append(timezone)

    # Create hash
    fingerprint_str = "|".join(components)
    return hashlib.sha256(fingerprint_str.encode()).hexdigest()[:32]
