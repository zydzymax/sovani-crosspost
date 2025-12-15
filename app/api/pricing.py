"""Pricing API routes."""

from typing import List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ..services.pricing import (
    pricing_service,
    calculate_subscription_price,
    get_available_platforms,
    get_available_image_providers,
    get_available_video_providers,
    PLATFORM_COSTS,
    IMAGE_GEN_COSTS,
    VIDEO_GEN_COSTS,
    BASE_SUBSCRIPTION,
    USD_TO_RUB
)

router = APIRouter(prefix="/pricing", tags=["pricing"])


# Response models
class PlatformInfo(BaseModel):
    """Platform information."""
    id: str
    display_name: str
    description: str
    cost_per_post: float


class ImageProviderInfo(BaseModel):
    """Image provider information."""
    id: str
    display_name: str
    quality: str
    description: str
    cost_per_image: float


class VideoProviderInfo(BaseModel):
    """Video provider information."""
    id: str
    display_name: str
    quality: str
    description: str
    cost_per_second: float


class SubscriptionPlanInfo(BaseModel):
    """Subscription plan information."""
    id: str
    price_rub: float
    price_usd: float
    posts_limit: int
    images_limit: int
    video_seconds_limit: int
    platforms_limit: Optional[int] = None


class PriceBreakdownItem(BaseModel):
    """Price breakdown item."""
    name: str
    display_name: str
    cost_per_post: float
    posts: int
    total: float


class PriceBreakdownResponse(BaseModel):
    """Detailed price breakdown."""
    platform_cost: float
    image_gen_cost: float
    video_gen_cost: float
    base_subscription: float
    total_usd: float
    total_rub: float
    platforms: List[PriceBreakdownItem]
    image_provider: str
    video_provider: str
    posts_per_month: int
    images_per_post: float
    video_seconds_per_post: float


class PriceEstimateResponse(BaseModel):
    """Price estimate response."""
    monthly_cost_usd: float
    monthly_cost_rub: float
    recommended_plan: str
    savings_vs_manual: float
    breakdown: PriceBreakdownResponse


class CompareProvidersResponse(BaseModel):
    """Provider comparison response."""
    providers: dict


@router.get("/platforms", response_model=List[PlatformInfo])
async def list_platforms():
    """Get list of available platforms with pricing."""
    return [
        PlatformInfo(
            id=platform_id,
            display_name=data["display_name"],
            description=data["description"],
            cost_per_post=data["cost_per_post"]
        )
        for platform_id, data in PLATFORM_COSTS.items()
    ]


@router.get("/image-providers", response_model=List[ImageProviderInfo])
async def list_image_providers():
    """Get list of available image generation providers."""
    return [
        ImageProviderInfo(
            id=provider_id,
            display_name=data["display_name"],
            quality=data["quality"],
            description=data["description"],
            cost_per_image=data["cost_per_image"]
        )
        for provider_id, data in IMAGE_GEN_COSTS.items()
        if provider_id != "none"
    ]


@router.get("/video-providers", response_model=List[VideoProviderInfo])
async def list_video_providers():
    """Get list of available video generation providers."""
    return [
        VideoProviderInfo(
            id=provider_id,
            display_name=data["display_name"],
            quality=data["quality"],
            description=data["description"],
            cost_per_second=data["cost_per_second"]
        )
        for provider_id, data in VIDEO_GEN_COSTS.items()
        if provider_id != "none"
    ]


@router.get("/subscription-plans", response_model=List[SubscriptionPlanInfo])
async def list_subscription_plans():
    """Get list of subscription plans."""
    plans = []
    for plan_id, data in BASE_SUBSCRIPTION.items():
        if plan_id == "demo":
            continue  # Skip demo in public API

        plans.append(SubscriptionPlanInfo(
            id=plan_id,
            price_rub=data["price"],
            price_usd=round(data["price"] * 0.011, 2),  # RUB to USD
            posts_limit=data["posts_limit"],
            images_limit=data["images_limit"],
            video_seconds_limit=data["video_seconds_limit"],
            platforms_limit=data.get("platforms_limit")
        ))

    return plans


@router.get("/calculate", response_model=PriceEstimateResponse)
async def calculate_price(
    platforms: List[str] = Query(..., description="List of platform IDs"),
    posts_per_month: int = Query(..., ge=1, le=1000, description="Posts per month"),
    image_provider: str = Query("openai", description="Image generation provider"),
    images_per_post: float = Query(1.0, ge=0, le=10, description="Average images per post"),
    video_provider: str = Query("none", description="Video generation provider"),
    video_seconds_per_post: float = Query(0, ge=0, le=60, description="Video seconds per post")
):
    """Calculate estimated subscription price."""
    # Validate platforms
    valid_platforms = list(PLATFORM_COSTS.keys())
    for p in platforms:
        if p not in valid_platforms:
            return {"error": f"Invalid platform: {p}. Valid: {valid_platforms}"}

    # Validate image provider
    if image_provider not in IMAGE_GEN_COSTS:
        return {"error": f"Invalid image provider. Valid: {list(IMAGE_GEN_COSTS.keys())}"}

    # Validate video provider
    if video_provider not in VIDEO_GEN_COSTS:
        return {"error": f"Invalid video provider. Valid: {list(VIDEO_GEN_COSTS.keys())}"}

    # Calculate price
    estimate = calculate_subscription_price(
        platforms=platforms,
        posts_per_month=posts_per_month,
        image_provider=image_provider,
        images_per_post=images_per_post,
        video_provider=video_provider,
        video_seconds_per_post=video_seconds_per_post
    )

    return PriceEstimateResponse(
        monthly_cost_usd=estimate.monthly_cost_usd,
        monthly_cost_rub=estimate.monthly_cost_rub,
        recommended_plan=estimate.recommended_plan,
        savings_vs_manual=estimate.savings_vs_manual,
        breakdown=PriceBreakdownResponse(
            platform_cost=estimate.breakdown.platform_cost,
            image_gen_cost=estimate.breakdown.image_gen_cost,
            video_gen_cost=estimate.breakdown.video_gen_cost,
            base_subscription=estimate.breakdown.base_subscription,
            total_usd=estimate.breakdown.total_usd,
            total_rub=estimate.breakdown.total_rub,
            platforms=[
                PriceBreakdownItem(**p) for p in estimate.breakdown.platforms
            ],
            image_provider=estimate.breakdown.image_provider,
            video_provider=estimate.breakdown.video_provider,
            posts_per_month=estimate.breakdown.posts_per_month,
            images_per_post=estimate.breakdown.images_per_post,
            video_seconds_per_post=estimate.breakdown.video_seconds_per_post
        )
    )


@router.get("/compare-image-providers")
async def compare_image_providers(
    posts_per_month: int = Query(..., ge=1, le=1000),
    images_per_post: float = Query(1.0, ge=0, le=10)
):
    """Compare image generation providers by cost."""
    comparison = pricing_service.compare_providers(
        posts_per_month=posts_per_month,
        images_per_post=images_per_post
    )

    return {
        "posts_per_month": posts_per_month,
        "images_per_post": images_per_post,
        "total_images": int(posts_per_month * images_per_post),
        "providers": comparison
    }


@router.get("/quick-estimate")
async def quick_estimate(
    platforms_count: int = Query(3, ge=1, le=7),
    posts_per_month: int = Query(30, ge=1, le=1000),
    with_images: bool = Query(True),
    with_video: bool = Query(False)
):
    """Get quick price estimate with defaults."""
    # Default platforms based on count
    all_platforms = ["telegram", "vk", "instagram", "facebook", "tiktok", "youtube", "rutube"]
    platforms = all_platforms[:platforms_count]

    # Calculate
    estimate = calculate_subscription_price(
        platforms=platforms,
        posts_per_month=posts_per_month,
        image_provider="openai" if with_images else "none",
        images_per_post=1.0 if with_images else 0,
        video_provider="runway" if with_video else "none",
        video_seconds_per_post=5.0 if with_video else 0
    )

    return {
        "platforms": platforms,
        "posts_per_month": posts_per_month,
        "with_images": with_images,
        "with_video": with_video,
        "monthly_cost_usd": estimate.monthly_cost_usd,
        "monthly_cost_rub": estimate.monthly_cost_rub,
        "recommended_plan": estimate.recommended_plan,
        "savings_percent": estimate.savings_vs_manual
    }
