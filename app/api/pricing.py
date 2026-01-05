"""Pricing API routes with credit-based system."""

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ..services.pricing import (
    USD_TO_RUB,
    get_available_image_providers,
    get_available_platforms,
    get_available_tts_providers,
    get_available_video_providers,
    get_provider_comparison,
    get_subscription_plans,
    pricing_service,
)

router = APIRouter(prefix="/pricing", tags=["pricing"])


# =============================================================================
# PLATFORMS
# =============================================================================


@router.get("/platforms")
async def list_platforms():
    """Get list of available platforms."""
    return get_available_platforms()


# =============================================================================
# IMAGE PROVIDERS
# =============================================================================


@router.get("/image-providers")
async def list_image_providers():
    """Get image providers with credit costs and plan limits.

    Returns providers with:
    - credits_per_image: how many credits one image costs
    - images_per_plan: how many images you get on each plan
    - strengths, best_for: provider characteristics
    """
    return get_available_image_providers()


# =============================================================================
# VIDEO PROVIDERS
# =============================================================================


@router.get("/video-providers")
async def list_video_providers():
    """Get video providers with credit costs and plan limits.

    Returns providers with:
    - credits_per_5sec: how many credits one 5-sec clip costs
    - clips_per_plan: how many clips you get on each plan
    - strengths, best_for: provider characteristics
    """
    return get_available_video_providers()


# =============================================================================
# TTS PROVIDERS
# =============================================================================


@router.get("/tts-providers")
async def list_tts_providers():
    """Get TTS (text-to-speech) providers with credit costs.

    Returns providers with:
    - credits_per_1k_chars: how many credits 1000 characters cost
    - chars_per_plan: how many characters you get on each plan
    - voices: available voice options
    """
    return get_available_tts_providers()


# =============================================================================
# SUBSCRIPTION PLANS
# =============================================================================


@router.get("/subscription-plans")
async def list_subscription_plans():
    """Get subscription plans with included credits.

    Returns plans with:
    - image_credits, video_credits, tts_credits: included credits
    - posts_limit, platforms_limit: usage limits
    - features: list of included features
    """
    return get_subscription_plans()


# =============================================================================
# PROVIDER COMPARISON
# =============================================================================


@router.get("/compare-providers")
async def compare_providers(plan_id: str = Query("pro", description="Plan to compare for")):
    """Compare all providers for a specific plan.

    Shows how many images/videos/chars you get with each provider
    on the selected plan.
    """
    return get_provider_comparison(plan_id)


# =============================================================================
# CALCULATE USAGE
# =============================================================================


class UsageRequest(BaseModel):
    image_provider: str = "nanobana"
    images_count: int = 50
    video_provider: str | None = None
    video_clips: int = 0
    tts_provider: str | None = None
    tts_chars: int = 0
    plan_id: str = "pro"


@router.post("/calculate-usage")
async def calculate_usage(request: UsageRequest):
    """Calculate credits usage for given providers and quantities.

    Returns:
    - credits_used vs credits_included
    - overage if exceeding plan limits
    - overage_cost_usd if any
    """
    usage = pricing_service.calculate_usage(
        image_provider=request.image_provider,
        images_count=request.images_count,
        video_provider=request.video_provider,
        video_clips=request.video_clips,
        tts_provider=request.tts_provider,
        tts_chars=request.tts_chars,
        plan_id=request.plan_id,
    )

    return {
        "image": {
            "credits_used": usage.image_credits_used,
            "credits_included": usage.image_credits_included,
            "overage": usage.image_overage,
        },
        "video": {
            "credits_used": usage.video_credits_used,
            "credits_included": usage.video_credits_included,
            "overage": usage.video_overage,
        },
        "tts": {
            "credits_used": usage.tts_credits_used,
            "credits_included": usage.tts_credits_included,
            "overage": usage.tts_overage,
        },
        "overage_cost_usd": usage.overage_cost_usd,
        "overage_cost_rub": round(usage.overage_cost_usd * USD_TO_RUB, -1),
    }


# =============================================================================
# RECOMMEND PLAN
# =============================================================================


class RecommendRequest(BaseModel):
    image_provider: str = "nanobana"
    images_per_month: int = 50
    video_provider: str | None = None
    video_clips_per_month: int = 0
    tts_provider: str | None = None
    tts_chars_per_month: int = 0
    platforms_count: int = 3


@router.post("/recommend-plan")
async def recommend_plan(request: RecommendRequest):
    """Recommend best plan for given usage.

    Returns:
    - recommended plan
    - what you get (images/videos/chars per provider)
    - overage cost if usage exceeds plan
    """
    recommendation = pricing_service.recommend_plan(
        image_provider=request.image_provider,
        images_per_month=request.images_per_month,
        video_provider=request.video_provider,
        video_clips_per_month=request.video_clips_per_month,
        tts_provider=request.tts_provider,
        tts_chars_per_month=request.tts_chars_per_month,
        platforms_count=request.platforms_count,
    )

    return {
        "plan": {
            "id": recommendation.plan_id,
            "name": recommendation.plan_name,
            "price_rub": recommendation.monthly_cost_rub,
            "price_usd": recommendation.monthly_cost_usd,
        },
        "included": {
            "images": recommendation.images_available,
            "videos": recommendation.videos_available,
            "tts_chars": recommendation.tts_chars_available,
        },
        "overage_cost_usd": recommendation.overage_cost_usd,
        "total": {"usd": recommendation.total_cost_usd, "rub": recommendation.total_cost_rub},
    }


# =============================================================================
# QUICK ESTIMATE (legacy compatibility)
# =============================================================================


@router.get("/quick-estimate")
async def quick_estimate(
    platforms_count: int = Query(3, ge=1, le=7),
    images_per_month: int = Query(50, ge=0, le=1000),
    video_clips_per_month: int = Query(0, ge=0, le=100),
    tts_chars_per_month: int = Query(0, ge=0, le=500000),
    image_provider: str = Query("nanobana"),
    video_provider: str = Query("minimax"),
    tts_provider: str = Query("openai-tts"),
):
    """Get quick price estimate."""
    recommendation = pricing_service.recommend_plan(
        image_provider=image_provider if images_per_month > 0 else None,
        images_per_month=images_per_month,
        video_provider=video_provider if video_clips_per_month > 0 else None,
        video_clips_per_month=video_clips_per_month,
        tts_provider=tts_provider if tts_chars_per_month > 0 else None,
        tts_chars_per_month=tts_chars_per_month,
        platforms_count=platforms_count,
    )

    return {
        "recommended_plan": recommendation.plan_id,
        "plan_price_rub": recommendation.monthly_cost_rub,
        "overage_cost_usd": recommendation.overage_cost_usd,
        "total_cost_rub": recommendation.total_cost_rub,
        "total_cost_usd": recommendation.total_cost_usd,
        "images_included": recommendation.images_available.get(image_provider, 0),
        "video_clips_included": recommendation.videos_available.get(video_provider, 0) if video_provider else 0,
        "tts_chars_included": recommendation.tts_chars_available.get(tts_provider, 0) if tts_provider else 0,
    }
