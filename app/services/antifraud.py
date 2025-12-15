"""
Comprehensive Anti-Fraud System for SoVAni Crosspost.

Provides protection against:
1. Demo abuse (multiple registrations, IP/device limits)
2. Payment fraud (card verification, chargeback prevention)
3. API abuse (rate limiting, DDoS protection)
"""

import hashlib
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from enum import Enum
from dataclasses import dataclass, field
from collections import defaultdict
import asyncio
import json

from ..core.logging import get_logger
from ..core.config import settings

logger = get_logger("services.antifraud")


class FraudRiskLevel(str, Enum):
    """Fraud risk levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FraudType(str, Enum):
    """Types of fraud detected."""
    DEMO_ABUSE = "demo_abuse"
    MULTIPLE_ACCOUNTS = "multiple_accounts"
    SUSPICIOUS_IP = "suspicious_ip"
    DEVICE_FINGERPRINT = "device_fingerprint"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    PAYMENT_FRAUD = "payment_fraud"
    CHARGEBACK_RISK = "chargeback_risk"
    BOT_ACTIVITY = "bot_activity"
    PROXY_VPN = "proxy_vpn"


@dataclass
class FraudSignal:
    """A detected fraud signal."""
    fraud_type: FraudType
    risk_level: FraudRiskLevel
    score: float  # 0.0 - 1.0
    description: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class FraudCheckResult:
    """Result of fraud check."""
    passed: bool
    risk_level: FraudRiskLevel
    total_score: float
    signals: List[FraudSignal]
    action: str  # allow, challenge, block
    reason: Optional[str] = None


@dataclass
class RateLimitResult:
    """Result of rate limit check."""
    allowed: bool
    current_count: int
    limit: int
    reset_at: datetime
    retry_after: Optional[int] = None


@dataclass
class TelegramTrustResult:
    """Result of Telegram account trust check."""
    score: float  # 0.0 - 1.0, higher = more trustworthy
    reason: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class AntifraudService:
    """
    Comprehensive anti-fraud service.

    Features:
    - Demo abuse detection (IP, device, phone limits)
    - Payment fraud scoring
    - API rate limiting
    - Bot detection
    - VPN/Proxy detection
    """

    def __init__(self, redis_client=None):
        """Initialize antifraud service."""
        self._redis = redis_client
        self._local_cache: Dict[str, Any] = {}

        # Configuration
        self.config = {
            # Demo abuse limits
            "demo_per_ip_limit": 1,  # Max demo accounts per IP (strict)
            "demo_per_device_limit": 1,  # Max demo accounts per device
            "demo_per_phone_limit": 1,  # Max demo accounts per phone
            "demo_cooldown_days": 30,  # Days before allowing new demo

            # Rate limits (requests per minute)
            "api_rate_limit_anonymous": 30,
            "api_rate_limit_demo": 60,
            "api_rate_limit_paid": 300,
            "api_rate_limit_business": 1000,

            # Burst limits (requests per second)
            "burst_limit_anonymous": 5,
            "burst_limit_authenticated": 20,

            # Payment thresholds
            "high_risk_countries": ["NG", "GH", "KE", "PH", "IN"],
            "max_failed_payments": 3,
            "chargeback_threshold": 0.01,  # 1% chargeback rate triggers review

            # Scoring thresholds
            "block_threshold": 0.8,
            "challenge_threshold": 0.5,
        }

        logger.info("AntifraudService initialized")

    def set_redis(self, redis_client):
        """Set Redis client for distributed state."""
        self._redis = redis_client

    # ==================== Demo Abuse Protection ====================

    async def check_demo_eligibility(
        self,
        telegram_id: int,
        ip_address: str,
        device_fingerprint: Optional[str] = None,
        phone_hash: Optional[str] = None,
        telegram_user_data: Optional[Dict[str, Any]] = None,
        browser_data: Optional[Dict[str, Any]] = None
    ) -> FraudCheckResult:
        """
        Check if user is eligible for demo account.

        Multi-layered fraud detection:
        1. VPN/Proxy detection (BLOCK for demo - no exceptions)
        2. Telegram account trust score
        3. Device fingerprint + similarity matching
        4. Phone number verification
        5. IP address (least reliable, checked last)
        6. Cross-correlation analysis
        """
        signals: List[FraudSignal] = []

        # ===== LAYER 1: VPN/Proxy - STRICT BLOCK for demo =====
        is_vpn = await self._check_vpn_proxy(ip_address)
        if is_vpn:
            signals.append(FraudSignal(
                fraud_type=FraudType.PROXY_VPN,
                risk_level=FraudRiskLevel.CRITICAL,  # Critical for demo
                score=0.95,
                description="VPN/Proxy detected - not allowed for demo",
                metadata={"ip": ip_address[:8] + "...", "blocked": True}
            ))
            # Early return - VPN users cannot get demo
            return self._calculate_result(signals, force_block=True)

        # ===== LAYER 2: Telegram Account Trust Score =====
        if telegram_user_data:
            tg_trust = await self._check_telegram_trust(telegram_id, telegram_user_data)
            if tg_trust.score < 0.5:
                signals.append(FraudSignal(
                    fraud_type=FraudType.DEMO_ABUSE,
                    risk_level=FraudRiskLevel.HIGH if tg_trust.score < 0.3 else FraudRiskLevel.MEDIUM,
                    score=1.0 - tg_trust.score,
                    description=tg_trust.reason,
                    metadata=tg_trust.metadata
                ))

        # ===== LAYER 3: Phone Hash - Most Reliable =====
        if phone_hash:
            phone_count = await self._get_demo_count_by_phone(phone_hash)
            if phone_count >= self.config["demo_per_phone_limit"]:
                signals.append(FraudSignal(
                    fraud_type=FraudType.MULTIPLE_ACCOUNTS,
                    risk_level=FraudRiskLevel.CRITICAL,
                    score=0.98,
                    description="Phone number already used for demo",
                    metadata={"phone_count": phone_count}
                ))

        # ===== LAYER 4: Device Fingerprint + Similarity =====
        if device_fingerprint:
            device_count = await self._get_demo_count_by_device(device_fingerprint)
            if device_count >= self.config["demo_per_device_limit"]:
                signals.append(FraudSignal(
                    fraud_type=FraudType.DEVICE_FINGERPRINT,
                    risk_level=FraudRiskLevel.CRITICAL,
                    score=0.95,
                    description=f"Device already used for demo",
                    metadata={"device_count": device_count}
                ))
            else:
                # Check for similar fingerprints (fuzzy matching)
                similar = await self._find_similar_fingerprints(device_fingerprint, browser_data)
                if similar:
                    signals.append(FraudSignal(
                        fraud_type=FraudType.DEVICE_FINGERPRINT,
                        risk_level=FraudRiskLevel.HIGH,
                        score=0.85,
                        description=f"Similar device fingerprint detected ({similar['similarity']}% match)",
                        metadata=similar
                    ))

        # ===== LAYER 5: Telegram ID History =====
        previous_demo = await self._get_previous_demo(telegram_id)
        if previous_demo:
            days_since = (datetime.utcnow() - previous_demo).days
            if days_since < self.config["demo_cooldown_days"]:
                signals.append(FraudSignal(
                    fraud_type=FraudType.DEMO_ABUSE,
                    risk_level=FraudRiskLevel.CRITICAL,
                    score=0.99,
                    description=f"Demo used {days_since} days ago, cooldown: {self.config['demo_cooldown_days']} days",
                    metadata={"days_since": days_since, "cooldown": self.config["demo_cooldown_days"]}
                ))

        # ===== LAYER 6: IP Address (least reliable) =====
        ip_count = await self._get_demo_count_by_ip(ip_address)
        if ip_count >= self.config["demo_per_ip_limit"]:
            signals.append(FraudSignal(
                fraud_type=FraudType.DEMO_ABUSE,
                risk_level=FraudRiskLevel.MEDIUM,  # Lower priority than device/phone
                score=0.6,
                description=f"IP {ip_address[:8]}... has {ip_count} demo accounts",
                metadata={"ip_count": ip_count, "limit": self.config["demo_per_ip_limit"]}
            ))

        # ===== LAYER 7: Cross-correlation =====
        cross_signals = await self._check_cross_correlation(
            telegram_id, ip_address, device_fingerprint, phone_hash
        )
        signals.extend(cross_signals)

        return self._calculate_result(signals)

    async def register_demo_usage(
        self,
        telegram_id: int,
        ip_address: str,
        device_fingerprint: Optional[str] = None,
        phone_hash: Optional[str] = None
    ):
        """Register demo account usage for future checks."""
        timestamp = datetime.utcnow().isoformat()

        # Store in Redis with TTL
        ttl = self.config["demo_cooldown_days"] * 86400  # Days to seconds

        if self._redis:
            pipe = self._redis.pipeline()

            # IP tracking
            ip_key = f"antifraud:demo:ip:{self._hash(ip_address)}"
            pipe.incr(ip_key)
            pipe.expire(ip_key, ttl)

            # Device tracking
            if device_fingerprint:
                device_key = f"antifraud:demo:device:{device_fingerprint}"
                pipe.incr(device_key)
                pipe.expire(device_key, ttl)

            # Phone tracking
            if phone_hash:
                phone_key = f"antifraud:demo:phone:{phone_hash}"
                pipe.incr(phone_key)
                pipe.expire(phone_key, ttl)

            # Telegram ID tracking
            tg_key = f"antifraud:demo:telegram:{telegram_id}"
            pipe.set(tg_key, timestamp, ex=ttl)

            await pipe.execute()

        logger.info(
            "Demo usage registered",
            telegram_id=telegram_id,
            ip_hash=self._hash(ip_address)[:8]
        )

    # ==================== Payment Fraud Protection ====================

    async def check_payment_risk(
        self,
        user_id: str,
        amount: float,
        currency: str,
        card_bin: Optional[str] = None,
        card_country: Optional[str] = None,
        ip_address: Optional[str] = None,
        email: Optional[str] = None
    ) -> FraudCheckResult:
        """
        Check payment for fraud risk.

        Checks:
        - Card BIN analysis
        - Country risk
        - Previous failed payments
        - Amount anomalies
        - Velocity checks
        """
        signals: List[FraudSignal] = []

        # Check high-risk countries
        if card_country and card_country.upper() in self.config["high_risk_countries"]:
            signals.append(FraudSignal(
                fraud_type=FraudType.PAYMENT_FRAUD,
                risk_level=FraudRiskLevel.MEDIUM,
                score=0.5,
                description=f"Card from high-risk country: {card_country}",
                metadata={"country": card_country}
            ))

        # Check failed payment history
        failed_count = await self._get_failed_payments(user_id)
        if failed_count >= self.config["max_failed_payments"]:
            signals.append(FraudSignal(
                fraud_type=FraudType.PAYMENT_FRAUD,
                risk_level=FraudRiskLevel.HIGH,
                score=0.7,
                description=f"User has {failed_count} failed payments",
                metadata={"failed_count": failed_count}
            ))

        # Check payment velocity (multiple payments in short time)
        recent_payments = await self._get_recent_payments(user_id, minutes=60)
        if recent_payments > 3:
            signals.append(FraudSignal(
                fraud_type=FraudType.PAYMENT_FRAUD,
                risk_level=FraudRiskLevel.MEDIUM,
                score=0.5,
                description=f"High payment velocity: {recent_payments} in last hour",
                metadata={"recent_payments": recent_payments}
            ))

        # Check if IP differs from registration IP
        if ip_address:
            registration_ip = await self._get_registration_ip(user_id)
            if registration_ip and registration_ip != ip_address:
                ip_country = await self._get_ip_country(ip_address)
                reg_country = await self._get_ip_country(registration_ip)
                if ip_country != reg_country:
                    signals.append(FraudSignal(
                        fraud_type=FraudType.PAYMENT_FRAUD,
                        risk_level=FraudRiskLevel.MEDIUM,
                        score=0.4,
                        description="Payment from different country than registration",
                        metadata={"payment_country": ip_country, "reg_country": reg_country}
                    ))

        # Check for disposable email
        if email and self._is_disposable_email(email):
            signals.append(FraudSignal(
                fraud_type=FraudType.PAYMENT_FRAUD,
                risk_level=FraudRiskLevel.MEDIUM,
                score=0.3,
                description="Disposable email domain detected",
                metadata={"email_domain": email.split("@")[-1]}
            ))

        # Check user chargeback history
        chargeback_rate = await self._get_chargeback_rate(user_id)
        if chargeback_rate > self.config["chargeback_threshold"]:
            signals.append(FraudSignal(
                fraud_type=FraudType.CHARGEBACK_RISK,
                risk_level=FraudRiskLevel.CRITICAL,
                score=0.9,
                description=f"High chargeback rate: {chargeback_rate:.1%}",
                metadata={"chargeback_rate": chargeback_rate}
            ))

        return self._calculate_result(signals)

    async def record_payment_attempt(
        self,
        user_id: str,
        success: bool,
        amount: float,
        payment_id: str
    ):
        """Record payment attempt for fraud analysis."""
        if self._redis:
            timestamp = datetime.utcnow().isoformat()

            # Record in user's payment history
            key = f"antifraud:payments:{user_id}"
            payment_data = json.dumps({
                "id": payment_id,
                "success": success,
                "amount": amount,
                "timestamp": timestamp
            })
            await self._redis.lpush(key, payment_data)
            await self._redis.ltrim(key, 0, 99)  # Keep last 100
            await self._redis.expire(key, 86400 * 90)  # 90 days

            # Track failed payments separately
            if not success:
                failed_key = f"antifraud:failed_payments:{user_id}"
                await self._redis.incr(failed_key)
                await self._redis.expire(failed_key, 86400 * 30)  # 30 days

    # ==================== API Rate Limiting ====================

    async def check_rate_limit(
        self,
        identifier: str,
        tier: str = "anonymous",
        endpoint: Optional[str] = None
    ) -> RateLimitResult:
        """
        Check if request is within rate limits.

        Args:
            identifier: User ID, IP address, or API key
            tier: anonymous, demo, paid, business
            endpoint: Optional specific endpoint for granular limits

        Returns:
            RateLimitResult with allowed status and limits
        """
        # Get limit based on tier
        limit_key = f"api_rate_limit_{tier}"
        rate_limit = self.config.get(limit_key, self.config["api_rate_limit_anonymous"])

        # Current minute bucket
        current_minute = int(time.time() / 60)
        bucket_key = f"antifraud:rate:{identifier}:{current_minute}"

        if self._redis:
            # Increment counter
            current_count = await self._redis.incr(bucket_key)

            # Set expiry on first request
            if current_count == 1:
                await self._redis.expire(bucket_key, 120)  # 2 minutes TTL

            # Check burst limit (per second)
            burst_key = f"antifraud:burst:{identifier}:{int(time.time())}"
            burst_count = await self._redis.incr(burst_key)
            if burst_count == 1:
                await self._redis.expire(burst_key, 2)

            burst_limit = self.config.get(
                f"burst_limit_{'authenticated' if tier != 'anonymous' else 'anonymous'}",
                5
            )

            if burst_count > burst_limit:
                return RateLimitResult(
                    allowed=False,
                    current_count=burst_count,
                    limit=burst_limit,
                    reset_at=datetime.utcnow() + timedelta(seconds=1),
                    retry_after=1
                )

            allowed = current_count <= rate_limit
            reset_at = datetime.utcfromtimestamp((current_minute + 1) * 60)

            return RateLimitResult(
                allowed=allowed,
                current_count=current_count,
                limit=rate_limit,
                reset_at=reset_at,
                retry_after=None if allowed else int((reset_at - datetime.utcnow()).total_seconds())
            )

        # Fallback without Redis (allow all)
        return RateLimitResult(
            allowed=True,
            current_count=0,
            limit=rate_limit,
            reset_at=datetime.utcnow() + timedelta(minutes=1)
        )

    async def check_bot_activity(
        self,
        user_agent: str,
        ip_address: str,
        request_patterns: Optional[List[Dict]] = None
    ) -> FraudSignal:
        """
        Detect bot or automated activity.

        Checks:
        - User agent analysis
        - Request timing patterns
        - Known bot signatures
        """
        score = 0.0
        reasons = []

        # Check user agent
        bot_signatures = [
            "bot", "spider", "crawl", "scrape", "curl", "wget",
            "python-requests", "postman", "insomnia"
        ]
        ua_lower = user_agent.lower()

        for sig in bot_signatures:
            if sig in ua_lower:
                score += 0.5
                reasons.append(f"Bot signature in UA: {sig}")
                break

        # Check for missing or suspicious UA
        if not user_agent or len(user_agent) < 20:
            score += 0.3
            reasons.append("Missing or short user agent")

        # Check request timing patterns (if provided)
        if request_patterns and len(request_patterns) > 5:
            # Calculate time deltas between requests
            times = [p.get("timestamp", 0) for p in request_patterns]
            if len(times) > 1:
                deltas = [times[i+1] - times[i] for i in range(len(times)-1)]
                avg_delta = sum(deltas) / len(deltas)

                # Very consistent timing suggests automation
                variance = sum((d - avg_delta) ** 2 for d in deltas) / len(deltas)
                if variance < 0.01 and avg_delta < 1.0:  # Less than 1 second, very consistent
                    score += 0.4
                    reasons.append("Suspicious request timing pattern")

        risk_level = (
            FraudRiskLevel.CRITICAL if score > 0.8 else
            FraudRiskLevel.HIGH if score > 0.6 else
            FraudRiskLevel.MEDIUM if score > 0.3 else
            FraudRiskLevel.LOW
        )

        return FraudSignal(
            fraud_type=FraudType.BOT_ACTIVITY,
            risk_level=risk_level,
            score=min(score, 1.0),
            description="; ".join(reasons) if reasons else "No bot activity detected",
            metadata={"user_agent": user_agent[:100], "checks": reasons}
        )

    # ==================== Helper Methods ====================

    def _calculate_result(
        self,
        signals: List[FraudSignal],
        force_block: bool = False
    ) -> FraudCheckResult:
        """Calculate final fraud check result from signals."""
        if not signals:
            return FraudCheckResult(
                passed=True,
                risk_level=FraudRiskLevel.LOW,
                total_score=0.0,
                signals=[],
                action="allow"
            )

        # Force block overrides everything
        if force_block:
            highest_signal = max(signals, key=lambda s: s.score)
            return FraudCheckResult(
                passed=False,
                risk_level=FraudRiskLevel.CRITICAL,
                total_score=1.0,
                signals=signals,
                action="block",
                reason=highest_signal.description
            )

        # Calculate weighted score
        total_score = max(s.score for s in signals)  # Use max signal score

        # Determine risk level and action
        if total_score >= self.config["block_threshold"]:
            risk_level = FraudRiskLevel.CRITICAL
            action = "block"
        elif total_score >= self.config["challenge_threshold"]:
            risk_level = FraudRiskLevel.HIGH
            action = "challenge"
        elif total_score > 0.2:
            risk_level = FraudRiskLevel.MEDIUM
            action = "allow"
        else:
            risk_level = FraudRiskLevel.LOW
            action = "allow"

        # Get highest risk signal for reason
        highest_signal = max(signals, key=lambda s: s.score)

        return FraudCheckResult(
            passed=action == "allow",
            risk_level=risk_level,
            total_score=total_score,
            signals=signals,
            action=action,
            reason=highest_signal.description if action != "allow" else None
        )

    def _hash(self, value: str) -> str:
        """Create consistent hash for storage."""
        return hashlib.sha256(value.encode()).hexdigest()

    async def _get_demo_count_by_ip(self, ip_address: str) -> int:
        """Get demo account count for IP."""
        if self._redis:
            key = f"antifraud:demo:ip:{self._hash(ip_address)}"
            count = await self._redis.get(key)
            return int(count) if count else 0
        return 0

    async def _get_demo_count_by_device(self, device_fingerprint: str) -> int:
        """Get demo account count for device."""
        if self._redis:
            key = f"antifraud:demo:device:{device_fingerprint}"
            count = await self._redis.get(key)
            return int(count) if count else 0
        return 0

    async def _get_demo_count_by_phone(self, phone_hash: str) -> int:
        """Get demo account count for phone."""
        if self._redis:
            key = f"antifraud:demo:phone:{phone_hash}"
            count = await self._redis.get(key)
            return int(count) if count else 0
        return 0

    async def _get_previous_demo(self, telegram_id: int) -> Optional[datetime]:
        """Get previous demo registration date."""
        if self._redis:
            key = f"antifraud:demo:telegram:{telegram_id}"
            timestamp = await self._redis.get(key)
            if timestamp:
                return datetime.fromisoformat(timestamp)
        return None

    async def _check_telegram_trust(
        self,
        telegram_id: int,
        user_data: Dict[str, Any]
    ) -> "TelegramTrustResult":
        """
        Calculate trust score for Telegram account.

        Higher score = more trustworthy.
        Factors:
        - Has username: +0.15
        - Has profile photo: +0.15
        - Has first/last name: +0.1
        - Is premium: +0.2
        - Account age (if available): +0.4
        """
        score = 0.0
        factors = []

        # Has username
        if user_data.get("username"):
            score += 0.15
            factors.append("has_username")

        # Has profile photo
        if user_data.get("photo_url") or user_data.get("has_photo"):
            score += 0.15
            factors.append("has_photo")

        # Has real name
        first_name = user_data.get("first_name", "")
        last_name = user_data.get("last_name", "")
        if first_name and len(first_name) > 1:
            score += 0.05
            factors.append("has_first_name")
        if last_name and len(last_name) > 1:
            score += 0.05
            factors.append("has_last_name")

        # Is premium user (very trustworthy - paid for Telegram)
        if user_data.get("is_premium"):
            score += 0.3
            factors.append("is_premium")

        # Check for suspicious patterns in username
        username = user_data.get("username", "")
        if username:
            # Random-looking usernames (lots of numbers at end)
            import re
            if re.search(r'\d{5,}$', username):
                score -= 0.2
                factors.append("suspicious_username")
            # Very short usernames
            if len(username) < 4:
                score -= 0.1
                factors.append("short_username")

        # Telegram ID range check (older accounts have lower IDs)
        # IDs < 1 billion are generally older accounts
        if telegram_id < 1_000_000_000:
            score += 0.2
            factors.append("old_account_id")
        elif telegram_id > 5_000_000_000:
            score -= 0.1
            factors.append("very_new_account_id")

        # Clamp score
        score = max(0.0, min(1.0, score))

        # Determine reason
        if score < 0.3:
            reason = "Low trust: new/empty Telegram account"
        elif score < 0.5:
            reason = "Medium trust: incomplete Telegram profile"
        else:
            reason = "Acceptable trust level"

        return TelegramTrustResult(
            score=score,
            reason=reason,
            metadata={"factors": factors, "telegram_id": telegram_id}
        )

    async def _find_similar_fingerprints(
        self,
        fingerprint: str,
        browser_data: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        Find similar device fingerprints using fuzzy matching.

        Checks browser data components for similarity even if
        full fingerprint hash is different.
        """
        if not self._redis or not browser_data:
            return None

        # Get all stored fingerprints
        cursor = 0
        pattern = "antifraud:fingerprint:*"

        try:
            cursor, keys = await self._redis.scan(cursor, match=pattern, count=100)

            for key in keys:
                stored_data = await self._redis.get(key)
                if not stored_data:
                    continue

                try:
                    stored = json.loads(stored_data)
                    similarity = self._calculate_fingerprint_similarity(browser_data, stored)

                    # 70% similarity threshold
                    if similarity >= 70:
                        return {
                            "similarity": similarity,
                            "matched_key": key.split(":")[-1][:8] + "...",
                            "matching_components": self._get_matching_components(browser_data, stored)
                        }
                except json.JSONDecodeError:
                    continue
        except Exception as e:
            logger.warning("Error checking fingerprint similarity", error=str(e))

        return None

    def _calculate_fingerprint_similarity(
        self,
        fp1: Dict[str, Any],
        fp2: Dict[str, Any]
    ) -> int:
        """Calculate similarity percentage between two fingerprints."""
        components = [
            "screen_resolution",
            "timezone",
            "language",
            "platform",
            "color_depth",
            "hardware_concurrency",
            "device_memory",
            "canvas_hash",
            "webgl_vendor",
            "webgl_renderer",
            "fonts_hash"
        ]

        matches = 0
        total = 0

        for comp in components:
            if comp in fp1 and comp in fp2:
                total += 1
                if fp1[comp] == fp2[comp]:
                    matches += 1

        if total == 0:
            return 0

        return int((matches / total) * 100)

    def _get_matching_components(
        self,
        fp1: Dict[str, Any],
        fp2: Dict[str, Any]
    ) -> List[str]:
        """Get list of matching fingerprint components."""
        components = [
            "screen_resolution", "timezone", "language", "platform",
            "canvas_hash", "webgl_vendor", "webgl_renderer"
        ]

        matches = []
        for comp in components:
            if comp in fp1 and comp in fp2 and fp1[comp] == fp2[comp]:
                matches.append(comp)

        return matches

    async def _check_cross_correlation(
        self,
        telegram_id: int,
        ip_address: str,
        device_fingerprint: Optional[str],
        phone_hash: Optional[str]
    ) -> List[FraudSignal]:
        """
        Cross-correlation analysis to detect fraud patterns.

        Detects:
        - Same device, different IPs (VPN hopping)
        - Same phone, different devices (SIM swapping)
        - Multiple Telegram IDs from same device
        """
        signals = []

        if not self._redis:
            return signals

        # Check: Same device seen with multiple Telegram IDs
        if device_fingerprint:
            device_tg_key = f"antifraud:device_tg:{device_fingerprint}"
            seen_tg_ids = await self._redis.smembers(device_tg_key)

            if seen_tg_ids and str(telegram_id) not in seen_tg_ids:
                # This device was used with OTHER Telegram accounts
                signals.append(FraudSignal(
                    fraud_type=FraudType.MULTIPLE_ACCOUNTS,
                    risk_level=FraudRiskLevel.HIGH,
                    score=0.85,
                    description=f"Device used with {len(seen_tg_ids)} other Telegram accounts",
                    metadata={"other_accounts": len(seen_tg_ids)}
                ))

            # Track this combination
            await self._redis.sadd(device_tg_key, str(telegram_id))
            await self._redis.expire(device_tg_key, 30 * 86400)  # 30 days

        # Check: Same phone seen with different devices
        if phone_hash and device_fingerprint:
            phone_devices_key = f"antifraud:phone_devices:{phone_hash}"
            seen_devices = await self._redis.smembers(phone_devices_key)

            if seen_devices and device_fingerprint not in seen_devices:
                signals.append(FraudSignal(
                    fraud_type=FraudType.MULTIPLE_ACCOUNTS,
                    risk_level=FraudRiskLevel.MEDIUM,
                    score=0.6,
                    description=f"Phone number used from {len(seen_devices)} different devices",
                    metadata={"device_count": len(seen_devices)}
                ))

            await self._redis.sadd(phone_devices_key, device_fingerprint)
            await self._redis.expire(phone_devices_key, 30 * 86400)

        # Check: IP history for this device (detect VPN hopping)
        if device_fingerprint:
            device_ips_key = f"antifraud:device_ips:{device_fingerprint}"
            seen_ips = await self._redis.smembers(device_ips_key)
            ip_hash = self._hash(ip_address)

            if len(seen_ips) >= 5 and ip_hash not in seen_ips:
                signals.append(FraudSignal(
                    fraud_type=FraudType.SUSPICIOUS_IP,
                    risk_level=FraudRiskLevel.MEDIUM,
                    score=0.5,
                    description=f"Device seen from {len(seen_ips)}+ different IPs (VPN hopping?)",
                    metadata={"ip_count": len(seen_ips)}
                ))

            await self._redis.sadd(device_ips_key, ip_hash)
            await self._redis.expire(device_ips_key, 7 * 86400)  # 7 days

        return signals

    async def _check_vpn_proxy(self, ip_address: str) -> bool:
        """
        Check if IP is VPN/Proxy.
        In production, would use services like ipinfo.io, ipapi.com
        """
        # Placeholder - would call external API
        # For now, check some known datacenter ranges
        datacenter_prefixes = [
            "104.16.",  # Cloudflare
            "162.158.",  # Cloudflare
            "172.64.",  # Cloudflare
            "34.64.",  # Google Cloud
            "35.192.",  # Google Cloud
        ]
        return any(ip_address.startswith(prefix) for prefix in datacenter_prefixes)

    async def _get_failed_payments(self, user_id: str) -> int:
        """Get count of failed payments."""
        if self._redis:
            key = f"antifraud:failed_payments:{user_id}"
            count = await self._redis.get(key)
            return int(count) if count else 0
        return 0

    async def _get_recent_payments(self, user_id: str, minutes: int) -> int:
        """Get count of recent payments."""
        if self._redis:
            key = f"antifraud:payments:{user_id}"
            payments = await self._redis.lrange(key, 0, -1)
            cutoff = datetime.utcnow() - timedelta(minutes=minutes)
            count = 0
            for p in payments:
                try:
                    data = json.loads(p)
                    ts = datetime.fromisoformat(data["timestamp"])
                    if ts > cutoff:
                        count += 1
                except:
                    pass
            return count
        return 0

    async def _get_registration_ip(self, user_id: str) -> Optional[str]:
        """Get user's registration IP."""
        if self._redis:
            key = f"antifraud:registration_ip:{user_id}"
            return await self._redis.get(key)
        return None

    async def _get_ip_country(self, ip_address: str) -> Optional[str]:
        """Get country from IP. Would use geoip service in production."""
        # Placeholder
        return "RU"

    async def _get_chargeback_rate(self, user_id: str) -> float:
        """Get user's chargeback rate."""
        if self._redis:
            key = f"antifraud:payments:{user_id}"
            payments = await self._redis.lrange(key, 0, -1)
            if not payments:
                return 0.0

            total = len(payments)
            chargebacks = 0
            for p in payments:
                try:
                    data = json.loads(p)
                    if data.get("chargeback"):
                        chargebacks += 1
                except:
                    pass

            return chargebacks / total if total > 0 else 0.0
        return 0.0

    def _is_disposable_email(self, email: str) -> bool:
        """Check if email domain is disposable."""
        disposable_domains = {
            "tempmail.com", "throwaway.email", "guerrillamail.com",
            "mailinator.com", "10minutemail.com", "temp-mail.org",
            "fakeinbox.com", "trashmail.com", "yopmail.com"
        }
        domain = email.lower().split("@")[-1]
        return domain in disposable_domains


# Global service instance
antifraud_service = AntifraudService()
