"""Content Plan API routes."""

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.db import get_db
from ..models.entities import User, ContentPlan, ContentPlanStatus
from ..services.content_planner import ContentPlannerService, Tone, generate_content_plan
from .deps import get_current_user

router = APIRouter(prefix="/content-plan", tags=["content-plan"])


# Request/Response models
class GeneratePlanRequest(BaseModel):
    """Request to generate content plan."""
    niche: str = Field(..., min_length=2, max_length=100, description="Business niche")
    duration_days: int = Field(7, ge=1, le=90, description="Plan duration in days")
    posts_per_day: int = Field(1, ge=1, le=5, description="Posts per day")
    platforms: List[str] = Field(default=["telegram", "instagram"], description="Target platforms")
    tone: str = Field("professional", description="Content tone")
    target_audience: Optional[str] = Field(None, description="Target audience description")
    brand_guidelines: Optional[str] = Field(None, description="Brand guidelines")


class PlannedPostResponse(BaseModel):
    """Single planned post."""
    date: str
    day_of_week: str
    time: str
    topic: str
    caption_draft: str
    hashtags: List[str]
    platforms: List[str]
    media_type: str
    image_prompt: Optional[str] = None
    call_to_action: Optional[str] = None


class ContentPlanResponse(BaseModel):
    """Content plan response."""
    id: str
    niche: str
    duration_days: int
    posts_per_day: int
    tone: str
    platforms: List[str]
    status: str
    posts: List[PlannedPostResponse]
    total_posts: int
    posts_created: int = 0
    posts_published: int = 0


class RegeneratPostRequest(BaseModel):
    """Request to regenerate a single post."""
    post_index: int = Field(..., ge=0, description="Index of post to regenerate")
    feedback: Optional[str] = Field(None, description="Feedback for regeneration")


@router.post("/generate", response_model=ContentPlanResponse)
async def generate_plan(
    request: GeneratePlanRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Generate AI-powered content plan."""
    # Validate platforms
    valid_platforms = ["telegram", "vk", "instagram", "facebook", "tiktok", "youtube", "rutube"]
    for platform in request.platforms:
        if platform not in valid_platforms:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid platform: {platform}"
            )

    # Validate tone
    valid_tones = [t.value for t in Tone]
    if request.tone not in valid_tones:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid tone. Valid options: {valid_tones}"
        )

    # Generate plan
    result = await generate_content_plan(
        niche=request.niche,
        duration_days=request.duration_days,
        posts_per_day=request.posts_per_day,
        platforms=request.platforms,
        tone=request.tone
    )

    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate plan: {result.error}"
        )

    # Save plan to database
    plan = ContentPlan(
        user_id=current_user.id,
        niche=result.niche,
        duration_days=result.duration_days,
        posts_per_day=result.posts_per_day,
        tone=result.tone,
        platforms=result.platforms,
        plan_data=[{
            "date": p.date,
            "day_of_week": p.day_of_week,
            "time": p.time,
            "topic": p.topic,
            "caption_draft": p.caption_draft,
            "hashtags": p.hashtags,
            "platforms": p.platforms,
            "media_type": p.media_type.value,
            "image_prompt": p.image_prompt,
            "call_to_action": p.call_to_action
        } for p in result.posts],
        status=ContentPlanStatus.DRAFT
    )

    db.add(plan)
    await db.commit()
    await db.refresh(plan)

    return ContentPlanResponse(
        id=str(plan.id),
        niche=plan.niche,
        duration_days=plan.duration_days,
        posts_per_day=plan.posts_per_day,
        tone=plan.tone,
        platforms=plan.platforms,
        status=plan.status.value,
        posts=[PlannedPostResponse(**p) for p in plan.plan_data],
        total_posts=len(plan.plan_data),
        posts_created=plan.posts_created,
        posts_published=plan.posts_published
    )


@router.get("/{plan_id}", response_model=ContentPlanResponse)
async def get_plan(
    plan_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get content plan by ID."""
    plan = await db.get(ContentPlan, plan_id)

    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Content plan not found"
        )

    if plan.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    return ContentPlanResponse(
        id=str(plan.id),
        niche=plan.niche,
        duration_days=plan.duration_days,
        posts_per_day=plan.posts_per_day,
        tone=plan.tone,
        platforms=plan.platforms,
        status=plan.status.value,
        posts=[PlannedPostResponse(**p) for p in plan.plan_data],
        total_posts=len(plan.plan_data),
        posts_created=plan.posts_created,
        posts_published=plan.posts_published
    )


@router.get("/", response_model=List[ContentPlanResponse])
async def list_plans(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = 20,
    offset: int = 0
):
    """List user's content plans."""
    from sqlalchemy import select

    query = select(ContentPlan).where(
        ContentPlan.user_id == current_user.id
    ).order_by(ContentPlan.created_at.desc()).limit(limit).offset(offset)

    result = await db.execute(query)
    plans = result.scalars().all()

    return [
        ContentPlanResponse(
            id=str(plan.id),
            niche=plan.niche,
            duration_days=plan.duration_days,
            posts_per_day=plan.posts_per_day,
            tone=plan.tone,
            platforms=plan.platforms,
            status=plan.status.value,
            posts=[PlannedPostResponse(**p) for p in plan.plan_data],
            total_posts=len(plan.plan_data),
            posts_created=plan.posts_created,
            posts_published=plan.posts_published
        )
        for plan in plans
    ]


@router.put("/{plan_id}", response_model=ContentPlanResponse)
async def update_plan(
    plan_id: UUID,
    posts: List[PlannedPostResponse],
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update content plan posts."""
    plan = await db.get(ContentPlan, plan_id)

    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Content plan not found"
        )

    if plan.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    # Update plan data
    plan.plan_data = [p.model_dump() for p in posts]
    await db.commit()
    await db.refresh(plan)

    return ContentPlanResponse(
        id=str(plan.id),
        niche=plan.niche,
        duration_days=plan.duration_days,
        posts_per_day=plan.posts_per_day,
        tone=plan.tone,
        platforms=plan.platforms,
        status=plan.status.value,
        posts=[PlannedPostResponse(**p) for p in plan.plan_data],
        total_posts=len(plan.plan_data),
        posts_created=plan.posts_created,
        posts_published=plan.posts_published
    )


@router.post("/{plan_id}/activate")
async def activate_plan(
    plan_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Activate content plan and create scheduled posts."""
    from datetime import datetime
    from ..models.entities import Post, PostStatus, Platform, ContentQueue

    plan = await db.get(ContentPlan, plan_id)

    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Content plan not found"
        )

    if plan.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    if plan.status != ContentPlanStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Plan is already activated or completed"
        )

    # Create posts from plan
    created_posts = 0
    for post_data in plan.plan_data:
        # Create Post entity
        post = Post(
            source_platform=Platform.TELEGRAM,  # Default source
            original_text=post_data["caption_draft"],
            generated_caption=post_data["caption_draft"],
            hashtags=post_data["hashtags"],
            status=PostStatus.DRAFT,
            is_scheduled=True,
            scheduled_at=datetime.fromisoformat(f"{post_data['date']}T{post_data['time']}:00")
        )
        db.add(post)
        created_posts += 1

        # Queue for each platform
        for platform_name in post_data["platforms"]:
            try:
                platform = Platform(platform_name)
                queue_item = ContentQueue(
                    post_id=post.id,
                    platform=platform,
                    scheduled_for=post.scheduled_at,
                    status="pending"
                )
                db.add(queue_item)
            except:
                continue

    # Update plan status
    plan.status = ContentPlanStatus.ACTIVE
    plan.activated_at = datetime.utcnow()
    plan.posts_created = created_posts

    await db.commit()

    return {
        "success": True,
        "message": f"Plan activated. {created_posts} posts scheduled.",
        "posts_created": created_posts
    }


@router.delete("/{plan_id}")
async def delete_plan(
    plan_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete content plan."""
    plan = await db.get(ContentPlan, plan_id)

    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Content plan not found"
        )

    if plan.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    await db.delete(plan)
    await db.commit()

    return {"success": True, "message": "Plan deleted"}


@router.post("/{plan_id}/regenerate-post")
async def regenerate_post(
    plan_id: UUID,
    request: RegeneratPostRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Regenerate a single post in the plan."""
    plan = await db.get(ContentPlan, plan_id)

    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Content plan not found"
        )

    if plan.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    if request.post_index >= len(plan.plan_data):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid post index"
        )

    # Get current post
    from ..services.content_planner import PlannedPost, MediaType, content_planner

    current_post_data = plan.plan_data[request.post_index]
    current_post = PlannedPost(
        date=current_post_data["date"],
        day_of_week=current_post_data["day_of_week"],
        time=current_post_data["time"],
        topic=current_post_data["topic"],
        caption_draft=current_post_data["caption_draft"],
        hashtags=current_post_data["hashtags"],
        platforms=current_post_data["platforms"],
        media_type=MediaType(current_post_data["media_type"]),
        image_prompt=current_post_data.get("image_prompt"),
        call_to_action=current_post_data.get("call_to_action")
    )

    # Regenerate
    new_post = await content_planner.regenerate_post(current_post, request.feedback)

    # Update plan data
    plan.plan_data[request.post_index] = {
        "date": new_post.date,
        "day_of_week": new_post.day_of_week,
        "time": new_post.time,
        "topic": new_post.topic,
        "caption_draft": new_post.caption_draft,
        "hashtags": new_post.hashtags,
        "platforms": new_post.platforms,
        "media_type": new_post.media_type.value,
        "image_prompt": new_post.image_prompt,
        "call_to_action": new_post.call_to_action
    }

    await db.commit()
    await db.refresh(plan)

    return PlannedPostResponse(**plan.plan_data[request.post_index])
