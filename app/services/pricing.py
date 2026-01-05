"""Dynamic pricing service for Crosspost.

Credit-based system where expensive providers consume more credits.
Each subscription tier includes a fixed number of credits.
"""

from dataclasses import dataclass
from typing import Any

from ..core.logging import get_logger

logger = get_logger("services.pricing")


# =============================================================================
# PLATFORMS
# =============================================================================

PLATFORM_COSTS = {
    "telegram": {
        "cost_per_post": 0.00,
        "display_name": "Telegram",
        "description": "Каналы и группы",
        "icon": "telegram",
    },
    "vk": {"cost_per_post": 0.00, "display_name": "ВКонтакте", "description": "Группы и паблики", "icon": "vk"},
    "instagram": {
        "cost_per_post": 0.03,
        "display_name": "Instagram",
        "description": "Посты, Stories, Reels",
        "icon": "instagram",
    },
    "facebook": {
        "cost_per_post": 0.03,
        "display_name": "Facebook",
        "description": "Страницы и группы",
        "icon": "facebook",
    },
    "tiktok": {"cost_per_post": 0.06, "display_name": "TikTok", "description": "Видео контент", "icon": "tiktok"},
    "youtube": {"cost_per_post": 0.09, "display_name": "YouTube", "description": "Видео и Shorts", "icon": "youtube"},
    "rutube": {
        "cost_per_post": 0.03,
        "display_name": "RuTube",
        "description": "Российский видеохостинг",
        "icon": "rutube",
    },
}


# =============================================================================
# IMAGE PROVIDERS - credits per image
# Base: 1 credit = 1 Nanobana Flash image (~/bin/bash.02 actual)
# =============================================================================

IMAGE_PROVIDERS = {
    "nanobana": {
        "credits_per_image": 1,
        "cost_usd": 0.06,  # with 3x markup
        "display_name": "Nano Banana Flash",
        "quality": "standard",
        "description": "Быстрая генерация, низкая цена",
        "strengths": ["Скорость", "Низкая цена", "Большие объёмы"],
        "best_for": "Массовая генерация, тесты идей",
        "speed": "fast",
        "max_resolution": "1024x1024",
    },
    "openai": {
        "credits_per_image": 2,
        "cost_usd": 0.12,
        "display_name": "DALL-E 3",
        "quality": "high",
        "description": "Высокое качество, реалистичные изображения",
        "strengths": ["Фотореализм", "Текст на изображениях", "Сложные сцены"],
        "best_for": "Реалистичные фото, продуктовые изображения",
        "speed": "medium",
        "max_resolution": "1024x1024",
    },
    "midjourney": {
        "credits_per_image": 4,
        "cost_usd": 0.24,
        "display_name": "Midjourney",
        "quality": "premium",
        "description": "Лучшее для маркетинга и арта",
        "strengths": ["Художественный стиль", "Эстетика", "Маркетинговые материалы"],
        "best_for": "Арт, иллюстрации, креативные посты",
        "speed": "slow",
        "max_resolution": "1024x1024",
    },
    "nanobana-pro": {
        "credits_per_image": 6,
        "cost_usd": 0.36,
        "display_name": "Nano Banana Pro",
        "quality": "premium",
        "description": "Google Gemini 3 Pro - 4K качество",
        "strengths": ["4K разрешение", "Детализация", "Сложные промпты"],
        "best_for": "Премиум контент, печать, баннеры",
        "speed": "medium",
        "max_resolution": "4096x4096",
    },
}


# =============================================================================
# VIDEO PROVIDERS - credits per 5 seconds
# Base: 1 credit = 5 sec MiniMax video (~/bin/bash.14 actual)
# =============================================================================

VIDEO_PROVIDERS = {
    "minimax": {
        "credits_per_5sec": 1,
        "cost_usd_per_5sec": 0.24,
        "display_name": "MiniMax Hailuo",
        "quality": "high",
        "description": "Отличное соотношение цена/качество",
        "strengths": ["Реалистичные движения", "Персонажи", "Доступная цена"],
        "best_for": "Контент для соцсетей, анимация",
        "duration_options": [6],
        "aspect_ratios": ["16:9", "9:16", "1:1"],
    },
    "kling": {
        "credits_per_5sec": 1,
        "cost_usd_per_5sec": 0.25,
        "display_name": "Kling AI",
        "quality": "high",
        "description": "Image-to-video, быстрая генерация",
        "strengths": ["Image-to-video", "Низкая цена", "Быстрая генерация"],
        "best_for": "Анимация изображений, Reels/TikTok",
        "duration_options": [5, 10],
        "aspect_ratios": ["16:9", "9:16", "1:1"],
    },
    "runway": {
        "credits_per_5sec": 3,
        "cost_usd_per_5sec": 0.75,
        "display_name": "Runway Gen-3",
        "quality": "premium",
        "description": "Лучшее качество видео",
        "strengths": ["Премиум качество", "Контроль движения", "Кинематографичность"],
        "best_for": "Рекламные ролики, премиум контент",
        "duration_options": [5, 10],
        "aspect_ratios": ["16:9", "9:16", "1:1"],
    },
}


# =============================================================================
# TTS PROVIDERS - credits per 1000 characters
# Base: 1 credit = 1000 chars OpenAI TTS (~/bin/bash.015 actual)
# =============================================================================

TTS_PROVIDERS = {
    "openai-tts": {
        "credits_per_1k_chars": 1,
        "cost_usd_per_1k": 0.045,
        "display_name": "OpenAI TTS",
        "quality": "high",
        "description": "Естественная речь, 6 голосов",
        "strengths": ["Натуральность", "Многоязычность", "Быстро"],
        "best_for": "Озвучка постов, сторис",
        "voices": [
            {"id": "alloy", "name": "Alloy", "gender": "neutral"},
            {"id": "echo", "name": "Echo", "gender": "male"},
            {"id": "fable", "name": "Fable", "gender": "neutral"},
            {"id": "onyx", "name": "Onyx", "gender": "male"},
            {"id": "nova", "name": "Nova", "gender": "female"},
            {"id": "shimmer", "name": "Shimmer", "gender": "female"},
        ],
    },
    "openai-tts-hd": {
        "credits_per_1k_chars": 2,
        "cost_usd_per_1k": 0.09,
        "display_name": "OpenAI TTS HD",
        "quality": "premium",
        "description": "HD качество для подкастов",
        "strengths": ["HD качество", "Подкасты", "Профессиональное аудио"],
        "best_for": "Подкасты, профессиональная озвучка",
        "voices": [
            {"id": "alloy", "name": "Alloy", "gender": "neutral"},
            {"id": "echo", "name": "Echo", "gender": "male"},
            {"id": "fable", "name": "Fable", "gender": "neutral"},
            {"id": "onyx", "name": "Onyx", "gender": "male"},
            {"id": "nova", "name": "Nova", "gender": "female"},
            {"id": "shimmer", "name": "Shimmer", "gender": "female"},
        ],
    },
}


# =============================================================================
# SUBSCRIPTION PLANS - credits included
# =============================================================================

SUBSCRIPTION_PLANS = {
    "starter": {
        "price_rub": 990,
        "price_usd": 11,
        "display_name": "Starter",
        "description": "Для начинающих",
        "image_credits": 50,  # 50 Nanobana или 25 DALL-E или 12 Midjourney
        "video_credits": 6,  # 6 MiniMax/Kling клипов или 2 Runway
        "tts_credits": 20,  # 20K символов TTS или 10K TTS HD
        "posts_limit": 100,
        "platforms_limit": 3,
        "features": [
            "До 100 постов/мес",
            "3 соцсети",
            "50 кредитов на изображения",
            "6 видео-кредитов",
            "20K символов озвучки",
            "AI-подписи",
        ],
    },
    "pro": {
        "price_rub": 2990,
        "price_usd": 33,
        "display_name": "Pro",
        "description": "Для активных блогеров",
        "image_credits": 200,  # 200 Nanobana или 100 DALL-E или 50 Midjourney
        "video_credits": 24,  # 24 клипа или 8 Runway
        "tts_credits": 100,  # 100K символов
        "posts_limit": 500,
        "platforms_limit": 5,
        "features": [
            "До 500 постов/мес",
            "5 соцсетей",
            "200 кредитов на изображения",
            "24 видео-кредита",
            "100K символов озвучки",
            "AI-подписи",
            "Контент-план",
            "Приоритетная поддержка",
        ],
    },
    "business": {
        "price_rub": 9990,
        "price_usd": 111,
        "display_name": "Business",
        "description": "Для команд и агентств",
        "image_credits": 1000,  # 1000 Nanobana или 500 DALL-E или 250 Midjourney
        "video_credits": 120,  # 120 клипов или 40 Runway
        "tts_credits": 500,  # 500K символов
        "posts_limit": -1,  # Unlimited
        "platforms_limit": -1,  # Unlimited
        "features": [
            "Безлимит постов",
            "Все соцсети",
            "1000 кредитов на изображения",
            "120 видео-кредитов",
            "500K символов озвучки",
            "AI-подписи",
            "Контент-план",
            "API доступ",
            "Выделенная поддержка",
        ],
    },
}


# =============================================================================
# OVERAGE PRICING - cost per credit when exceeding limits
# =============================================================================

OVERAGE_PRICING = {
    "image_credit": 0.06,  # /bin/bash.06 per image credit
    "video_credit": 0.25,  # /bin/bash.25 per video credit (5 sec)
    "tts_credit": 0.045,  # /bin/bash.045 per 1K chars
}

USD_TO_RUB = 90


# =============================================================================
# DATA CLASSES
# =============================================================================


@dataclass
class CreditsUsage:
    """Credits usage breakdown."""

    image_credits_used: int
    video_credits_used: int
    tts_credits_used: int

    image_credits_included: int
    video_credits_included: int
    tts_credits_included: int

    image_overage: int = 0
    video_overage: int = 0
    tts_overage: int = 0

    overage_cost_usd: float = 0.0


@dataclass
class PlanRecommendation:
    """Recommended plan with breakdown."""

    plan_id: str
    plan_name: str
    monthly_cost_rub: float
    monthly_cost_usd: float

    # What you get
    images_available: dict[str, int]  # provider -> count
    videos_available: dict[str, int]  # provider -> count
    tts_chars_available: dict[str, int]  # provider -> chars

    # Overage if any
    overage_cost_usd: float = 0.0
    total_cost_usd: float = 0.0
    total_cost_rub: float = 0.0


# =============================================================================
# PRICING SERVICE
# =============================================================================


class PricingService:
    """Service for calculating subscription prices."""

    def __init__(self):
        logger.info("Pricing service initialized")

    def get_platforms(self) -> list[dict[str, Any]]:
        """Get all available platforms."""
        return [{"id": k, **v} for k, v in PLATFORM_COSTS.items()]

    def get_image_providers(self) -> list[dict[str, Any]]:
        """Get all image providers with credit costs."""
        result = []
        for provider_id, data in IMAGE_PROVIDERS.items():
            provider = {"id": provider_id, **data}
            # Calculate images per plan
            provider["images_per_plan"] = {
                plan_id: plan["image_credits"] // data["credits_per_image"]
                for plan_id, plan in SUBSCRIPTION_PLANS.items()
            }
            result.append(provider)
        return result

    def get_video_providers(self) -> list[dict[str, Any]]:
        """Get all video providers with credit costs."""
        result = []
        for provider_id, data in VIDEO_PROVIDERS.items():
            provider = {"id": provider_id, **data}
            # Calculate videos per plan (5-sec clips)
            provider["clips_per_plan"] = {
                plan_id: plan["video_credits"] // data["credits_per_5sec"]
                for plan_id, plan in SUBSCRIPTION_PLANS.items()
            }
            result.append(provider)
        return result

    def get_tts_providers(self) -> list[dict[str, Any]]:
        """Get all TTS providers with credit costs."""
        result = []
        for provider_id, data in TTS_PROVIDERS.items():
            provider = {"id": provider_id, **data}
            # Calculate chars per plan
            provider["chars_per_plan"] = {
                plan_id: (plan["tts_credits"] // data["credits_per_1k_chars"]) * 1000
                for plan_id, plan in SUBSCRIPTION_PLANS.items()
            }
            result.append(provider)
        return result

    def get_subscription_plans(self) -> list[dict[str, Any]]:
        """Get all subscription plans."""
        return [{"id": k, **v} for k, v in SUBSCRIPTION_PLANS.items()]

    def calculate_usage(
        self,
        image_provider: str,
        images_count: int,
        video_provider: str,
        video_clips: int,  # 5-sec clips
        tts_provider: str,
        tts_chars: int,
        plan_id: str = "pro",
    ) -> CreditsUsage:
        """Calculate credits usage for given providers and quantities."""
        plan = SUBSCRIPTION_PLANS.get(plan_id, SUBSCRIPTION_PLANS["pro"])

        # Image credits
        img_provider = IMAGE_PROVIDERS.get(image_provider, IMAGE_PROVIDERS["nanobana"])
        image_credits = images_count * img_provider["credits_per_image"]
        image_overage = max(0, image_credits - plan["image_credits"])

        # Video credits
        vid_provider = VIDEO_PROVIDERS.get(video_provider, VIDEO_PROVIDERS["minimax"])
        video_credits = video_clips * vid_provider["credits_per_5sec"]
        video_overage = max(0, video_credits - plan["video_credits"])

        # TTS credits
        tts_prov = TTS_PROVIDERS.get(tts_provider, TTS_PROVIDERS["openai-tts"])
        tts_credits = (tts_chars / 1000) * tts_prov["credits_per_1k_chars"]
        tts_overage = max(0, tts_credits - plan["tts_credits"])

        # Calculate overage cost
        overage_cost = (
            image_overage * OVERAGE_PRICING["image_credit"]
            + video_overage * OVERAGE_PRICING["video_credit"]
            + tts_overage * OVERAGE_PRICING["tts_credit"]
        )

        return CreditsUsage(
            image_credits_used=int(image_credits),
            video_credits_used=int(video_credits),
            tts_credits_used=int(tts_credits),
            image_credits_included=plan["image_credits"],
            video_credits_included=plan["video_credits"],
            tts_credits_included=plan["tts_credits"],
            image_overage=int(image_overage),
            video_overage=int(video_overage),
            tts_overage=int(tts_overage),
            overage_cost_usd=round(overage_cost, 2),
        )

    def recommend_plan(
        self,
        image_provider: str,
        images_per_month: int,
        video_provider: str = None,
        video_clips_per_month: int = 0,
        tts_provider: str = None,
        tts_chars_per_month: int = 0,
        platforms_count: int = 3,
    ) -> PlanRecommendation:
        """Recommend best plan for given usage."""
        img_prov = IMAGE_PROVIDERS.get(image_provider, IMAGE_PROVIDERS["nanobana"])
        vid_prov = VIDEO_PROVIDERS.get(video_provider, VIDEO_PROVIDERS["minimax"]) if video_provider else None
        tts_prov = TTS_PROVIDERS.get(tts_provider, TTS_PROVIDERS["openai-tts"]) if tts_provider else None

        # Calculate required credits
        image_credits_needed = images_per_month * img_prov["credits_per_image"]
        video_credits_needed = video_clips_per_month * (vid_prov["credits_per_5sec"] if vid_prov else 0)
        tts_credits_needed = (tts_chars_per_month / 1000) * (tts_prov["credits_per_1k_chars"] if tts_prov else 0)

        # Find best plan
        best_plan = "business"
        for plan_id in ["starter", "pro", "business"]:
            plan = SUBSCRIPTION_PLANS[plan_id]

            # Check platform limit
            if plan["platforms_limit"] != -1 and platforms_count > plan["platforms_limit"]:
                continue

            # Check if credits fit
            if (
                image_credits_needed <= plan["image_credits"]
                and video_credits_needed <= plan["video_credits"]
                and tts_credits_needed <= plan["tts_credits"]
            ):
                best_plan = plan_id
                break

        plan = SUBSCRIPTION_PLANS[best_plan]

        # Calculate what you get
        images_available = {
            pid: plan["image_credits"] // pdata["credits_per_image"] for pid, pdata in IMAGE_PROVIDERS.items()
        }

        videos_available = {
            pid: plan["video_credits"] // pdata["credits_per_5sec"] for pid, pdata in VIDEO_PROVIDERS.items()
        }

        tts_available = {
            pid: (plan["tts_credits"] // pdata["credits_per_1k_chars"]) * 1000 for pid, pdata in TTS_PROVIDERS.items()
        }

        # Calculate overage
        overage_cost = 0.0
        if image_credits_needed > plan["image_credits"]:
            overage_cost += (image_credits_needed - plan["image_credits"]) * OVERAGE_PRICING["image_credit"]
        if video_credits_needed > plan["video_credits"]:
            overage_cost += (video_credits_needed - plan["video_credits"]) * OVERAGE_PRICING["video_credit"]
        if tts_credits_needed > plan["tts_credits"]:
            overage_cost += (tts_credits_needed - plan["tts_credits"]) * OVERAGE_PRICING["tts_credit"]

        total_usd = plan["price_usd"] + overage_cost
        total_rub = total_usd * USD_TO_RUB

        return PlanRecommendation(
            plan_id=best_plan,
            plan_name=plan["display_name"],
            monthly_cost_rub=plan["price_rub"],
            monthly_cost_usd=plan["price_usd"],
            images_available=images_available,
            videos_available=videos_available,
            tts_chars_available=tts_available,
            overage_cost_usd=round(overage_cost, 2),
            total_cost_usd=round(total_usd, 2),
            total_cost_rub=round(total_rub, -1),
        )

    def get_provider_comparison(self, plan_id: str = "pro") -> dict[str, Any]:
        """Get comparison of all providers for a plan."""
        plan = SUBSCRIPTION_PLANS.get(plan_id, SUBSCRIPTION_PLANS["pro"])

        return {
            "plan": {"id": plan_id, **plan},
            "image_providers": [
                {
                    "id": pid,
                    "name": pdata["display_name"],
                    "quality": pdata["quality"],
                    "images_included": plan["image_credits"] // pdata["credits_per_image"],
                    "credits_per_image": pdata["credits_per_image"],
                    "strengths": pdata["strengths"],
                    "best_for": pdata["best_for"],
                }
                for pid, pdata in IMAGE_PROVIDERS.items()
            ],
            "video_providers": [
                {
                    "id": pid,
                    "name": pdata["display_name"],
                    "quality": pdata["quality"],
                    "clips_included": plan["video_credits"] // pdata["credits_per_5sec"],
                    "credits_per_clip": pdata["credits_per_5sec"],
                    "strengths": pdata["strengths"],
                    "best_for": pdata["best_for"],
                }
                for pid, pdata in VIDEO_PROVIDERS.items()
            ],
            "tts_providers": [
                {
                    "id": pid,
                    "name": pdata["display_name"],
                    "quality": pdata["quality"],
                    "chars_included": (plan["tts_credits"] // pdata["credits_per_1k_chars"]) * 1000,
                    "credits_per_1k": pdata["credits_per_1k_chars"],
                    "strengths": pdata["strengths"],
                    "best_for": pdata["best_for"],
                }
                for pid, pdata in TTS_PROVIDERS.items()
            ],
        }


# Global instance
pricing_service = PricingService()


# Convenience functions
def get_available_platforms():
    return pricing_service.get_platforms()


def get_available_image_providers():
    return pricing_service.get_image_providers()


def get_available_video_providers():
    return pricing_service.get_video_providers()


def get_available_tts_providers():
    return pricing_service.get_tts_providers()


def get_subscription_plans():
    return pricing_service.get_subscription_plans()


def get_provider_comparison(plan_id: str = "pro"):
    return pricing_service.get_provider_comparison(plan_id)
