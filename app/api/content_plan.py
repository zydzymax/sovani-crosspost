"""Content Plan API routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..core.logging import get_logger

logger = get_logger("api.content_plan")
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.entities import ContentPlan, ContentPlanStatus, User
from ..services.content_planner import Tone, generate_content_plan
from .deps import get_current_user, get_db_async_session

router = APIRouter(prefix="/content-plan", tags=["content-plan"])
VALID_PLATFORMS = ("telegram", "vk", "instagram", "facebook", "tiktok", "youtube", "rutube")
VALID_MEDIA_TYPES = ("IMAGE", "VIDEO", "CAROUSEL", "TEXT_ONLY")


# Request/Response models
class GeneratePlanRequest(BaseModel):
    """Request to generate content plan."""

    niche: str = Field(..., min_length=2, max_length=100, description="Business niche")
    duration_days: int = Field(7, ge=1, le=90, description="Plan duration in days")
    posts_per_day: int = Field(1, ge=1, le=5, description="Posts per day")
    platforms: list[str] = Field(default=["telegram", "instagram"], description="Target platforms")
    tone: str = Field("professional", description="Content tone")
    target_audience: str | None = Field(None, description="Target audience description")
    brand_guidelines: str | None = Field(None, description="Brand guidelines")


class PlannedPostResponse(BaseModel):
    """Single planned post."""

    date: str
    day_of_week: str
    time: str
    topic: str
    caption_draft: str
    hashtags: list[str]
    platforms: list[str]
    media_type: str
    image_prompt: str | None = None
    call_to_action: str | None = None


class ContentPlanResponse(BaseModel):
    """Content plan response."""

    id: str
    niche: str
    duration_days: int
    posts_per_day: int
    tone: str
    platforms: list[str]
    status: str
    posts: list[PlannedPostResponse]
    total_posts: int
    posts_created: int = 0
    posts_published: int = 0


class RegeneratPostRequest(BaseModel):
    """Request to regenerate a single post."""

    post_index: int = Field(..., ge=0, description="Index of post to regenerate")
    feedback: str | None = Field(None, description="Feedback for regeneration")


def _to_plan_response(plan: ContentPlan) -> "ContentPlanResponse":
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
        posts_published=plan.posts_published,
    )


def _ensure_plan_access(plan: ContentPlan | None, current_user: User) -> ContentPlan:
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content plan not found")
    if plan.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return plan


def _normalize_and_validate_platforms(platforms: list[str]) -> list[str]:
    normalized = [p.lower() for p in platforms]
    invalid_platforms = sorted({p for p in normalized if p not in VALID_PLATFORMS})
    if invalid_platforms:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid platform: {invalid_platforms[0]}")
    return normalized


def _normalize_and_validate_media_type(media_type: str) -> str:
    normalized = media_type.upper()
    if normalized not in VALID_MEDIA_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid media_type: {media_type}. Valid: {list(VALID_MEDIA_TYPES)}",
        )
    return normalized


@router.post("/generate", response_model=ContentPlanResponse)
async def generate_plan(
    request: GeneratePlanRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_async_session),
):
    """Generate AI-powered content plan."""
    request.platforms = _normalize_and_validate_platforms(request.platforms)

    # Validate tone
    valid_tones = [t.value for t in Tone]
    if request.tone not in valid_tones:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid tone. Valid options: {valid_tones}"
        )

    # Generate plan
    result = await generate_content_plan(
        niche=request.niche,
        duration_days=request.duration_days,
        posts_per_day=request.posts_per_day,
        platforms=request.platforms,
        tone=request.tone,
    )

    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to generate plan: {result.error}"
        )

    # Save plan to database
    plan = ContentPlan(
        user_id=current_user.id,
        niche=result.niche,
        duration_days=result.duration_days,
        posts_per_day=result.posts_per_day,
        tone=result.tone,
        platforms=result.platforms,
        plan_data=[
            {
                "date": p.date,
                "day_of_week": p.day_of_week,
                "time": p.time,
                "topic": p.topic,
                "caption_draft": p.caption_draft,
                "hashtags": p.hashtags,
                "platforms": p.platforms,
                "media_type": p.media_type.value,
                "image_prompt": p.image_prompt,
                "call_to_action": p.call_to_action,
            }
            for p in result.posts
        ],
        status=ContentPlanStatus.DRAFT,
    )

    db.add(plan)
    await db.commit()
    await db.refresh(plan)

    return _to_plan_response(plan)


@router.get("/{plan_id}", response_model=ContentPlanResponse)
async def get_plan(
    plan_id: UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_async_session)
):
    """Get content plan by ID."""
    plan = _ensure_plan_access(await db.get(ContentPlan, plan_id), current_user)
    return _to_plan_response(plan)


@router.get("/", response_model=list[ContentPlanResponse])
async def list_plans(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_async_session),
    limit: int = 20,
    offset: int = 0,
):
    """List user's content plans."""
    from sqlalchemy import select

    query = (
        select(ContentPlan)
        .where(ContentPlan.user_id == current_user.id)
        .order_by(ContentPlan.created_at.desc())
        .limit(limit)
        .offset(offset)
    )

    result = await db.execute(query)
    plans = result.scalars().all()

    return [_to_plan_response(plan) for plan in plans]


@router.put("/{plan_id}", response_model=ContentPlanResponse)
async def update_plan(
    plan_id: UUID,
    posts: list[PlannedPostResponse],
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_async_session),
):
    """Update content plan posts."""
    plan = _ensure_plan_access(await db.get(ContentPlan, plan_id), current_user)

    # Update plan data
    plan.plan_data = [p.model_dump() for p in posts]
    await db.commit()
    await db.refresh(plan)

    return _to_plan_response(plan)


@router.post("/{plan_id}/activate")
async def activate_plan(
    plan_id: UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_async_session)
):
    """Activate content plan and create scheduled posts."""
    import json
    from datetime import datetime
    from uuid import uuid4

    from sqlalchemy import text

    plan = _ensure_plan_access(await db.get(ContentPlan, plan_id), current_user)

    if plan.status != ContentPlanStatus.DRAFT:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Plan is already activated or completed")

    # Create posts from plan using raw SQL (ORM model mismatch with user_id)
    created_posts = 0
    created_post_ids = []  # For image generation
    for post_data in plan.plan_data:
        post_id = uuid4()

        # Parse scheduled time
        try:
            scheduled_at = datetime.fromisoformat(f"{post_data['date']}T{post_data['time']}:00")
        except Exception:
            scheduled_at = datetime.utcnow()

        enrichment = json.dumps({"image_prompt": post_data.get("image_prompt"), "topic": post_data.get("topic")})

        # Create Post using raw SQL to include user_id
        await db.execute(
            text(
                """
                INSERT INTO posts (id, user_id, original_text, generated_caption, hashtags,
                                   status, is_scheduled, scheduled_at, source_platform,
                                   enrichment_data, created_at, updated_at)
                VALUES (:id, :user_id, :text, :caption, :hashtags,
                        'draft', true, :scheduled_at, 'telegram',
                        CAST(:enrichment AS jsonb), NOW(), NOW())
            """
            ),
            {
                "id": post_id,
                "user_id": current_user.id,
                "text": post_data.get("caption_draft", ""),
                "caption": post_data.get("caption_draft", ""),
                "hashtags": post_data.get("hashtags", []),
                "scheduled_at": scheduled_at,
                "enrichment": enrichment,
            },
        )
        created_posts += 1
        # Track for image generation if has image_prompt
        if post_data.get("image_prompt"):
            created_post_ids.append(str(post_id))

        # Create content queue entries for each platform
        for platform_name in post_data.get("platforms", ["telegram"]):
            queue_id = uuid4()
            await db.execute(
                text(
                    """
                    INSERT INTO content_queue (id, post_id, platform, scheduled_for, status, created_at)
                    VALUES (:id, :post_id, :platform, :scheduled_for, 'pending', NOW())
                """
                ),
                {"id": queue_id, "post_id": post_id, "platform": platform_name.lower(), "scheduled_for": scheduled_at},
            )

    # Update plan status
    plan.status = ContentPlanStatus.ACTIVE
    plan.activated_at = datetime.utcnow()
    plan.posts_created = created_posts

    await db.commit()

    # Trigger image generation for posts with image_prompt
    images_queued = 0
    if created_post_ids:
        try:
            from ..workers.tasks.image_generate import batch_generate_images

            batch_generate_images.delay(created_post_ids)
            images_queued = len(created_post_ids)
            logger.info("Queued image generation", posts_count=images_queued)
        except Exception as e:
            logger.warning("Failed to queue image generation", error=str(e))

    return {
        "success": True,
        "message": f"Plan activated. {created_posts} posts scheduled. {images_queued} images queued.",
        "posts_created": created_posts,
        "images_queued": images_queued,
    }


@router.delete("/{plan_id}")
async def delete_plan(
    plan_id: UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_async_session)
):
    """Delete content plan."""
    plan = _ensure_plan_access(await db.get(ContentPlan, plan_id), current_user)

    await db.delete(plan)
    await db.commit()

    return {"success": True, "message": "Plan deleted"}


@router.post("/{plan_id}/regenerate-post")
async def regenerate_post(
    plan_id: UUID,
    request: RegeneratPostRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_async_session),
):
    """Regenerate a single post in the plan."""
    plan = _ensure_plan_access(await db.get(ContentPlan, plan_id), current_user)

    if request.post_index >= len(plan.plan_data):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid post index")

    # Get current post
    from ..services.content_planner import MediaType, PlannedPost, content_planner

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
        call_to_action=current_post_data.get("call_to_action"),
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
        "call_to_action": new_post.call_to_action,
    }

    await db.commit()
    await db.refresh(plan)

    return PlannedPostResponse(**plan.plan_data[request.post_index])


# ==================== MANUAL UPLOAD ====================


class ManualPostUpload(BaseModel):
    """Single post for manual upload."""

    date: str = Field(..., description="Post date YYYY-MM-DD")
    time: str = Field("12:00", description="Post time HH:MM")
    topic: str = Field(..., min_length=2, description="Post topic")
    caption: str = Field(..., min_length=10, description="Post caption/text")
    hashtags: list[str] | None = Field(default=[], description="Hashtags")
    platforms: list[str] = Field(default=["telegram"], description="Target platforms")
    media_type: str = Field("IMAGE", description="IMAGE, VIDEO, CAROUSEL, TEXT_ONLY")
    image_prompt: str | None = Field(None, description="Prompt for AI image generation")
    media_url: str | None = Field(None, description="URL to existing media")


class ManualPlanUpload(BaseModel):
    """Manual content plan upload."""

    name: str = Field(..., min_length=2, max_length=100, description="Plan name")
    niche: str = Field("custom", description="Business niche")
    tone: str = Field("professional", description="Content tone")
    posts: list[ManualPostUpload] = Field(..., min_length=1, description="Posts list")


class UploadFromCSVRequest(BaseModel):
    """CSV content for upload."""

    csv_content: str = Field(
        ..., description="CSV content with columns: date,time,topic,caption,hashtags,platforms,media_type"
    )
    name: str = Field(..., min_length=2, description="Plan name")
    niche: str = Field("custom", description="Business niche")


@router.post("/upload", response_model=ContentPlanResponse)
async def upload_plan(
    request: ManualPlanUpload,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_async_session),
):
    """Upload content plan manually (JSON)."""
    from datetime import datetime

    # Validate platforms
    posts_data = []
    for post in request.posts:
        normalized_platforms = _normalize_and_validate_platforms(post.platforms)
        normalized_media_type = _normalize_and_validate_media_type(post.media_type)

        # Parse date to get day of week
        try:
            date_obj = datetime.strptime(post.date, "%Y-%m-%d")
            day_of_week = date_obj.strftime("%A")
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid date format: {post.date}. Use YYYY-MM-DD"
            )

        posts_data.append(
            {
                "date": post.date,
                "day_of_week": day_of_week,
                "time": post.time,
                "topic": post.topic,
                "caption_draft": post.caption,
                "hashtags": post.hashtags or [],
                "platforms": normalized_platforms,
                "media_type": normalized_media_type,
                "image_prompt": post.image_prompt,
                "media_url": post.media_url,
                "call_to_action": None,
            }
        )

    # Calculate duration
    dates = [datetime.strptime(p["date"], "%Y-%m-%d") for p in posts_data]
    duration_days = (max(dates) - min(dates)).days + 1 if dates else 1

    # Create plan
    plan = ContentPlan(
        user_id=current_user.id,
        niche=request.niche,
        duration_days=duration_days,
        posts_per_day=len(posts_data) // duration_days if duration_days > 0 else len(posts_data),
        tone=request.tone,
        platforms=list({p for post in posts_data for p in post["platforms"]}),
        plan_data=posts_data,
        status=ContentPlanStatus.DRAFT,
    )

    db.add(plan)
    await db.commit()
    await db.refresh(plan)

    return _to_plan_response(plan)


@router.post("/upload-csv", response_model=ContentPlanResponse)
async def upload_plan_from_csv(
    request: UploadFromCSVRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_async_session),
):
    """Upload content plan from CSV format.

    CSV columns: date,time,topic,caption,hashtags,platforms,media_type
    Example:
    2025-01-01,10:00,New Year Sale,Happy New Year! 🎉,#newyear #sale,telegram|instagram,IMAGE
    """
    import csv
    from datetime import datetime
    from io import StringIO

    try:
        reader = csv.DictReader(StringIO(request.csv_content))
        posts_data = []

        required_columns = ["date", "topic", "caption"]

        for row in reader:
            # Check required columns
            for col in required_columns:
                if col not in row or not row[col]:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST, detail=f"Missing required column: {col}"
                    )

            # Parse date
            try:
                date_obj = datetime.strptime(row["date"].strip(), "%Y-%m-%d")
                day_of_week = date_obj.strftime("%A")
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid date format: {row['date']}. Use YYYY-MM-DD",
                )

            # Parse hashtags (comma or space separated)
            hashtags_raw = row.get("hashtags", "")
            hashtags = [h.strip() for h in hashtags_raw.replace(",", " ").split() if h.strip()]

            # Parse platforms (pipe or comma separated)
            platforms_raw = row.get("platforms", "telegram")
            platforms = _normalize_and_validate_platforms(
                [p.strip() for p in platforms_raw.replace("|", ",").split(",") if p.strip()]
            )
            media_type = _normalize_and_validate_media_type(row.get("media_type", "IMAGE").strip())

            posts_data.append(
                {
                    "date": row["date"].strip(),
                    "day_of_week": day_of_week,
                    "time": row.get("time", "12:00").strip(),
                    "topic": row["topic"].strip(),
                    "caption_draft": row["caption"].strip(),
                    "hashtags": hashtags,
                    "platforms": platforms,
                    "media_type": media_type,
                    "image_prompt": row.get("image_prompt", "").strip() or None,
                    "call_to_action": row.get("call_to_action", "").strip() or None,
                }
            )

        if not posts_data:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No valid posts found in CSV")

    except csv.Error as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"CSV parsing error: {str(e)}")

    # Calculate duration
    dates = [datetime.strptime(p["date"], "%Y-%m-%d") for p in posts_data]
    duration_days = (max(dates) - min(dates)).days + 1 if dates else 1

    # Create plan
    plan = ContentPlan(
        user_id=current_user.id,
        niche=request.niche,
        duration_days=duration_days,
        posts_per_day=len(posts_data) // duration_days if duration_days > 0 else len(posts_data),
        tone="custom",
        platforms=list({p for post in posts_data for p in post["platforms"]}),
        plan_data=posts_data,
        status=ContentPlanStatus.DRAFT,
    )

    db.add(plan)
    await db.commit()
    await db.refresh(plan)

    return _to_plan_response(plan)
