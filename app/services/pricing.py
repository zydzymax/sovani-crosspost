"""Dynamic pricing service for Crosspost.

Calculates subscription costs based on:
- Selected social networks
- Number of posts per month
- Image generation provider
- Video generation usage
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum

from ..core.logging import get_logger


logger = get_logger("services.pricing")


class PlatformCost(str, Enum):
    """Platform cost tiers."""
    FREE = "free"
    BASIC = "basic"
    PREMIUM = "premium"


class ImageProvider(str, Enum):
    """Image generation providers."""
    OPENAI = "openai"      # DALL-E 3
    MIDJOURNEY = "midjourney"
    NANOBANA = "nanobana"
    NONE = "none"


class VideoProvider(str, Enum):
    """Video generation providers."""
    RUNWAY = "runway"
    NONE = "none"


# Platform posting costs per post (actual cost × 3 markup)
# Free platforms have zero API cost, but we still charge for convenience
PLATFORM_COSTS = {
    "telegram": {
        "cost_per_post": 0.00,  # Free API
        "display_name": "Telegram",
        "description": "Каналы и группы"
    },
    "vk": {
        "cost_per_post": 0.00,  # Free API
        "display_name": "ВКонтакте",
        "description": "Группы и паблики"
    },
    "instagram": {
        "cost_per_post": 0.03,  # ~$0.01 API cost × 3
        "display_name": "Instagram",
        "description": "Посты, Stories, Reels"
    },
    "facebook": {
        "cost_per_post": 0.03,  # ~$0.01 API cost × 3
        "display_name": "Facebook",
        "description": "Страницы и группы"
    },
    "tiktok": {
        "cost_per_post": 0.06,  # ~$0.02 API cost × 3 (complex API)
        "display_name": "TikTok",
        "description": "Видео контент"
    },
    "youtube": {
        "cost_per_post": 0.09,  # ~$0.03 API cost × 3
        "display_name": "YouTube",
        "description": "Видео и Shorts"
    },
    "rutube": {
        "cost_per_post": 0.03,  # ~$0.01 API cost × 3
        "display_name": "RuTube",
        "description": "Российский видеохостинг"
    }
}

# Image generation costs per image (actual cost × 3 markup)
IMAGE_GEN_COSTS = {
    "openai": {
        "cost_per_image": 0.12,  # $0.04 actual × 3
        "display_name": "DALL-E 3",
        "quality": "high",
        "description": "OpenAI's flagship model"
    },
    "midjourney": {
        "cost_per_image": 0.24,  # $0.08 actual × 3
        "display_name": "Midjourney",
        "quality": "premium",
        "description": "Лучшее качество для маркетинга"
    },
    "nanobana": {
        "cost_per_image": 0.03,  # $0.01 actual × 3
        "display_name": "Nanobana",
        "quality": "standard",
        "description": "Бюджетный вариант"
    },
    "none": {
        "cost_per_image": 0.00,
        "display_name": "Без генерации",
        "quality": "none",
        "description": "Только свои изображения"
    }
}

# Video generation costs per second (actual cost × 3 markup)
VIDEO_GEN_COSTS = {
    "runway": {
        "cost_per_second": 0.15,  # $0.05 actual × 3
        "display_name": "Runway ML",
        "quality": "high",
        "description": "Gen-3 Alpha видео"
    },
    "none": {
        "cost_per_second": 0.00,
        "display_name": "Без генерации",
        "quality": "none",
        "description": "Только свои видео"
    }
}

# Base subscription fees (monthly)
BASE_SUBSCRIPTION = {
    "demo": {
        "price": 0.00,
        "posts_limit": 10,
        "images_limit": 5,
        "video_seconds_limit": 0,
        "duration_days": 7
    },
    "starter": {
        "price": 990,  # ₽
        "posts_limit": 50,
        "images_limit": 20,
        "video_seconds_limit": 30,
        "platforms_limit": 3
    },
    "pro": {
        "price": 2990,  # ₽
        "posts_limit": 200,
        "images_limit": 100,
        "video_seconds_limit": 120,
        "platforms_limit": 5
    },
    "business": {
        "price": 9990,  # ₽
        "posts_limit": -1,  # Unlimited
        "images_limit": 500,
        "video_seconds_limit": 600,
        "platforms_limit": -1  # Unlimited
    }
}

# RUB to USD exchange rate (approximate)
RUB_TO_USD = 0.011  # 1 RUB = ~$0.011
USD_TO_RUB = 90  # $1 = ~90 RUB


@dataclass
class PriceBreakdown:
    """Detailed price breakdown."""
    platform_cost: float
    image_gen_cost: float
    video_gen_cost: float
    base_subscription: float
    total_usd: float
    total_rub: float

    # Details
    platforms: List[Dict[str, Any]]
    image_provider: str
    video_provider: str
    posts_per_month: int
    images_per_post: float
    video_seconds_per_post: float


@dataclass
class PriceEstimate:
    """Price estimate result."""
    monthly_cost_usd: float
    monthly_cost_rub: float
    breakdown: PriceBreakdown
    recommended_plan: str
    savings_vs_manual: float  # % savings compared to manual work


class PricingService:
    """Service for calculating subscription prices."""

    def __init__(self):
        """Initialize pricing service."""
        logger.info("Pricing service initialized")

    def calculate_price(
        self,
        platforms: List[str],
        posts_per_month: int,
        image_provider: str = "openai",
        images_per_post: float = 1.0,
        video_provider: str = "none",
        video_seconds_per_post: float = 0.0
    ) -> PriceEstimate:
        """
        Calculate monthly subscription price.

        Args:
            platforms: List of platform names
            posts_per_month: Number of posts per month
            image_provider: Image generation provider name
            images_per_post: Average images per post (can be fractional)
            video_provider: Video generation provider name
            video_seconds_per_post: Average video seconds per post

        Returns:
            PriceEstimate with breakdown
        """
        # Calculate platform costs
        platform_details = []
        platform_total = 0.0

        for platform in platforms:
            if platform in PLATFORM_COSTS:
                cost_data = PLATFORM_COSTS[platform]
                platform_cost = cost_data["cost_per_post"] * posts_per_month
                platform_total += platform_cost
                platform_details.append({
                    "name": platform,
                    "display_name": cost_data["display_name"],
                    "cost_per_post": cost_data["cost_per_post"],
                    "posts": posts_per_month,
                    "total": platform_cost
                })

        # Calculate image generation costs
        image_cost = 0.0
        if image_provider in IMAGE_GEN_COSTS:
            cost_per_image = IMAGE_GEN_COSTS[image_provider]["cost_per_image"]
            total_images = posts_per_month * images_per_post
            image_cost = cost_per_image * total_images

        # Calculate video generation costs
        video_cost = 0.0
        if video_provider in VIDEO_GEN_COSTS:
            cost_per_second = VIDEO_GEN_COSTS[video_provider]["cost_per_second"]
            total_seconds = posts_per_month * video_seconds_per_post
            video_cost = cost_per_second * total_seconds

        # Determine recommended plan based on usage
        recommended_plan = self._recommend_plan(
            platforms, posts_per_month,
            int(posts_per_month * images_per_post),
            int(posts_per_month * video_seconds_per_post)
        )

        base_subscription_rub = BASE_SUBSCRIPTION[recommended_plan]["price"]
        base_subscription_usd = base_subscription_rub * RUB_TO_USD

        # Calculate totals
        variable_costs_usd = platform_total + image_cost + video_cost
        total_usd = base_subscription_usd + variable_costs_usd
        total_rub = total_usd * USD_TO_RUB

        # Round to reasonable values
        total_usd = round(total_usd, 2)
        total_rub = round(total_rub, -1)  # Round to nearest 10 RUB

        # Calculate savings (assumed 5 minutes per post manual work at $10/hour)
        manual_hours = (posts_per_month * len(platforms) * 5) / 60
        manual_cost_usd = manual_hours * 10
        savings_percent = ((manual_cost_usd - total_usd) / manual_cost_usd * 100) if manual_cost_usd > 0 else 0

        breakdown = PriceBreakdown(
            platform_cost=round(platform_total, 2),
            image_gen_cost=round(image_cost, 2),
            video_gen_cost=round(video_cost, 2),
            base_subscription=base_subscription_usd,
            total_usd=total_usd,
            total_rub=total_rub,
            platforms=platform_details,
            image_provider=image_provider,
            video_provider=video_provider,
            posts_per_month=posts_per_month,
            images_per_post=images_per_post,
            video_seconds_per_post=video_seconds_per_post
        )

        return PriceEstimate(
            monthly_cost_usd=total_usd,
            monthly_cost_rub=total_rub,
            breakdown=breakdown,
            recommended_plan=recommended_plan,
            savings_vs_manual=round(savings_percent, 1)
        )

    def _recommend_plan(
        self,
        platforms: List[str],
        posts_per_month: int,
        images_per_month: int,
        video_seconds_per_month: int
    ) -> str:
        """Recommend subscription plan based on usage."""
        num_platforms = len(platforms)

        # Check limits for each plan
        for plan_name in ["starter", "pro", "business"]:
            plan = BASE_SUBSCRIPTION[plan_name]

            # Check platform limit
            if plan["platforms_limit"] != -1 and num_platforms > plan["platforms_limit"]:
                continue

            # Check posts limit
            if plan["posts_limit"] != -1 and posts_per_month > plan["posts_limit"]:
                continue

            # Check images limit
            if plan["images_limit"] != -1 and images_per_month > plan["images_limit"]:
                continue

            # Check video limit
            if plan["video_seconds_limit"] != -1 and video_seconds_per_month > plan["video_seconds_limit"]:
                continue

            return plan_name

        return "business"

    def get_platform_info(self) -> List[Dict[str, Any]]:
        """Get information about all platforms."""
        return [
            {
                "id": platform_id,
                **platform_data
            }
            for platform_id, platform_data in PLATFORM_COSTS.items()
        ]

    def get_image_providers(self) -> List[Dict[str, Any]]:
        """Get information about image providers."""
        return [
            {
                "id": provider_id,
                **provider_data
            }
            for provider_id, provider_data in IMAGE_GEN_COSTS.items()
        ]

    def get_video_providers(self) -> List[Dict[str, Any]]:
        """Get information about video providers."""
        return [
            {
                "id": provider_id,
                **provider_data
            }
            for provider_id, provider_data in VIDEO_GEN_COSTS.items()
        ]

    def get_subscription_plans(self) -> List[Dict[str, Any]]:
        """Get information about subscription plans."""
        plans = []
        for plan_id, plan_data in BASE_SUBSCRIPTION.items():
            if plan_id == "demo":
                continue  # Skip demo in public listing
            plans.append({
                "id": plan_id,
                "price_rub": plan_data["price"],
                "price_usd": round(plan_data["price"] * RUB_TO_USD, 2),
                **{k: v for k, v in plan_data.items() if k != "price"}
            })
        return plans

    def compare_providers(
        self,
        posts_per_month: int,
        images_per_post: float = 1.0
    ) -> Dict[str, Dict[str, Any]]:
        """Compare image providers by cost."""
        total_images = posts_per_month * images_per_post

        comparison = {}
        for provider_id, provider_data in IMAGE_GEN_COSTS.items():
            if provider_id == "none":
                continue

            total_cost = provider_data["cost_per_image"] * total_images
            comparison[provider_id] = {
                "display_name": provider_data["display_name"],
                "quality": provider_data["quality"],
                "cost_per_image": provider_data["cost_per_image"],
                "total_images": int(total_images),
                "total_cost_usd": round(total_cost, 2),
                "total_cost_rub": round(total_cost * USD_TO_RUB, -1)
            }

        return comparison


# Global instance
pricing_service = PricingService()


# Convenience functions
def calculate_subscription_price(
    platforms: List[str],
    posts_per_month: int,
    image_provider: str = "openai",
    images_per_post: float = 1.0,
    video_provider: str = "none",
    video_seconds_per_post: float = 0.0
) -> PriceEstimate:
    """Calculate subscription price."""
    return pricing_service.calculate_price(
        platforms=platforms,
        posts_per_month=posts_per_month,
        image_provider=image_provider,
        images_per_post=images_per_post,
        video_provider=video_provider,
        video_seconds_per_post=video_seconds_per_post
    )


def get_available_platforms() -> List[Dict[str, Any]]:
    """Get list of available platforms."""
    return pricing_service.get_platform_info()


def get_available_image_providers() -> List[Dict[str, Any]]:
    """Get list of available image providers."""
    return pricing_service.get_image_providers()


def get_available_video_providers() -> List[Dict[str, Any]]:
    """Get list of available video providers."""
    return pricing_service.get_video_providers()
