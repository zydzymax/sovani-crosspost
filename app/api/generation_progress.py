"""
Generation Progress API - tracks content generation checklist and progress.
"""
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.entities import ContentPlan, GenerationStepStatus, PostGenerationProgress, User
from .deps import get_current_user, get_db_async_session

router = APIRouter(prefix="/progress", tags=["generation-progress"])


# === Response Models ===

class StepProgress(BaseModel):
    """Individual step progress."""
    status: str
    started_at: str | None = None
    completed_at: str | None = None
    result: Any | None = None
    error: str | None = None
    provider: str | None = None


class PostProgress(BaseModel):
    """Progress for a single post."""
    id: str
    post_index: int
    post_date: str
    post_topic: str | None
    steps: dict[str, StepProgress]
    overall_status: str
    progress_percent: int
    last_error: str | None
    created_at: str
    updated_at: str | None
    completed_at: str | None


class PlanProgressSummary(BaseModel):
    """Summary of progress for entire content plan."""
    content_plan_id: str
    total_posts: int
    completed_posts: int
    in_progress_posts: int
    failed_posts: int
    overall_progress_percent: int
    posts: list[PostProgress]


class UpdateStepRequest(BaseModel):
    """Request to update a step status."""
    step: str = Field(..., description="Step name: caption_generated, image_generated, etc.")
    status: str = Field(..., description="Status: pending, in_progress, completed, failed, skipped")
    result: Any | None = Field(None, description="Step result data")
    error: str | None = Field(None, description="Error message if failed")
    provider: str | None = Field(None, description="Provider used (e.g., nanobana, runway)")


class InitProgressRequest(BaseModel):
    """Request to initialize progress tracking for a plan."""
    content_plan_id: str


# === Endpoints ===

@router.post("/init", response_model=PlanProgressSummary)
async def initialize_progress(
    request: InitProgressRequest,
    db: AsyncSession = Depends(get_db_async_session),
    current_user: User = Depends(get_current_user)
):
    """Initialize progress tracking for all posts in a content plan."""
    try:
        plan_id = uuid.UUID(request.content_plan_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid content_plan_id")

    # Get content plan
    plan = await db.get(ContentPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Content plan not found")

    if plan.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Check if progress already initialized
    result = await db.execute(
        select(PostGenerationProgress)
        .where(PostGenerationProgress.content_plan_id == plan_id)
        .limit(1)
    )
    if result.scalars().first():
        raise HTTPException(
            status_code=400,
            detail="Progress already initialized. Use GET /progress/{plan_id} to view."
        )

    # Create progress entries for each post
    posts = plan.plan_data or []
    progress_entries = []

    for idx, post in enumerate(posts):
        # Determine required steps based on media_type
        media_type = post.get("media_type", "IMAGE").upper()

        initial_steps = {
            "caption_generated": {"status": "pending"},
            "hashtags_generated": {"status": "pending"},
        }

        if media_type in ["IMAGE", "CAROUSEL"]:
            initial_steps["image_prompt_generated"] = {"status": "pending"}
            initial_steps["image_generated"] = {"status": "pending"}
        elif media_type == "VIDEO":
            initial_steps["image_prompt_generated"] = {"status": "pending"}
            initial_steps["image_generated"] = {"status": "pending"}
            initial_steps["video_generated"] = {"status": "pending"}
        elif media_type == "AUDIO" or media_type == "VOICE":
            initial_steps["audio_generated"] = {"status": "pending"}

        # Add publishing steps
        initial_steps["post_scheduled"] = {"status": "pending"}
        initial_steps["post_published"] = {"status": "pending"}

        # Add quality checks if enabled
        initial_steps["content_reviewed"] = {"status": "pending"}

        progress = PostGenerationProgress(
            content_plan_id=plan_id,
            post_index=idx,
            post_date=datetime.strptime(post.get("date", "2024-01-01"), "%Y-%m-%d").date(),
            post_topic=post.get("topic", "")[:500],
            steps=initial_steps,
            overall_status=GenerationStepStatus.PENDING,
            progress_percent=0
        )
        progress_entries.append(progress)
        db.add(progress)

    await db.commit()

    # Refresh and build response
    for p in progress_entries:
        await db.refresh(p)

    return _build_plan_summary(str(plan_id), progress_entries)


@router.get("/{plan_id}", response_model=PlanProgressSummary)
async def get_plan_progress(
    plan_id: str,
    db: AsyncSession = Depends(get_db_async_session),
    current_user: User = Depends(get_current_user)
):
    """Get progress for all posts in a content plan."""
    try:
        pid = uuid.UUID(plan_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid plan_id")

    # Check access
    plan = await db.get(ContentPlan, pid)
    if not plan:
        raise HTTPException(status_code=404, detail="Content plan not found")
    if plan.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Get progress entries
    result = await db.execute(
        select(PostGenerationProgress)
        .where(PostGenerationProgress.content_plan_id == pid)
        .order_by(PostGenerationProgress.post_index)
    )
    entries = result.scalars().all()

    if not entries:
        raise HTTPException(
            status_code=404,
            detail="Progress not initialized. Use POST /progress/init first."
        )

    return _build_plan_summary(plan_id, entries)


@router.get("/{plan_id}/post/{post_index}", response_model=PostProgress)
async def get_post_progress(
    plan_id: str,
    post_index: int,
    db: AsyncSession = Depends(get_db_async_session),
    current_user: User = Depends(get_current_user)
):
    """Get progress for a specific post."""
    try:
        pid = uuid.UUID(plan_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid plan_id")

    # Check access
    plan = await db.get(ContentPlan, pid)
    if not plan:
        raise HTTPException(status_code=404, detail="Content plan not found")
    if plan.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Get specific post progress
    result = await db.execute(
        select(PostGenerationProgress)
        .where(PostGenerationProgress.content_plan_id == pid)
        .where(PostGenerationProgress.post_index == post_index)
    )
    progress = result.scalars().first()

    if not progress:
        raise HTTPException(status_code=404, detail="Post progress not found")

    return _to_post_progress(progress)


@router.patch("/{plan_id}/post/{post_index}/step")
async def update_step(
    plan_id: str,
    post_index: int,
    request: UpdateStepRequest,
    db: AsyncSession = Depends(get_db_async_session),
    current_user: User = Depends(get_current_user)
):
    """Update a generation step status for a post."""
    try:
        pid = uuid.UUID(plan_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid plan_id")

    # Check access
    plan = await db.get(ContentPlan, pid)
    if not plan:
        raise HTTPException(status_code=404, detail="Content plan not found")
    if plan.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Validate status
    valid_statuses = ["pending", "in_progress", "completed", "failed", "skipped"]
    if request.status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {valid_statuses}"
        )

    # Get progress entry
    result = await db.execute(
        select(PostGenerationProgress)
        .where(PostGenerationProgress.content_plan_id == pid)
        .where(PostGenerationProgress.post_index == post_index)
    )
    progress = result.scalars().first()

    if not progress:
        raise HTTPException(status_code=404, detail="Post progress not found")

    # Update step
    steps = dict(progress.steps) if progress.steps else {}
    step_data = steps.get(request.step, {})
    step_data["status"] = request.status
    step_data["updated_at"] = datetime.utcnow().isoformat()

    if request.status == "in_progress" and "started_at" not in step_data:
        step_data["started_at"] = datetime.utcnow().isoformat()
    elif request.status == "completed":
        step_data["completed_at"] = datetime.utcnow().isoformat()
        if request.result:
            step_data["result"] = request.result
    elif request.status == "failed" and request.error:
        step_data["error"] = request.error
        progress.last_error = request.error
        progress.error_count = (progress.error_count or 0) + 1

    if request.provider:
        step_data["provider"] = request.provider

    steps[request.step] = step_data
    progress.steps = steps
    progress.updated_at = datetime.utcnow()

    # Recalculate progress
    _recalculate_progress(progress)

    await db.commit()
    await db.refresh(progress)

    return {"status": "updated", "progress": _to_post_progress(progress)}


@router.post("/{plan_id}/reset")
async def reset_progress(
    plan_id: str,
    db: AsyncSession = Depends(get_db_async_session),
    current_user: User = Depends(get_current_user)
):
    """Reset progress for a content plan (delete and reinitialize)."""
    try:
        pid = uuid.UUID(plan_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid plan_id")

    # Check access
    plan = await db.get(ContentPlan, pid)
    if not plan:
        raise HTTPException(status_code=404, detail="Content plan not found")
    if plan.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Delete existing progress
    result = await db.execute(
        select(PostGenerationProgress)
        .where(PostGenerationProgress.content_plan_id == pid)
    )
    entries = result.scalars().all()
    for entry in entries:
        await db.delete(entry)

    await db.commit()

    return {"status": "reset", "deleted_count": len(entries)}


# === Helper Functions ===

def _to_post_progress(p: PostGenerationProgress) -> PostProgress:
    """Convert DB model to response model."""
    steps_response = {}
    for step_name, step_data in (p.steps or {}).items():
        steps_response[step_name] = StepProgress(
            status=step_data.get("status", "pending"),
            started_at=step_data.get("started_at"),
            completed_at=step_data.get("completed_at"),
            result=step_data.get("result"),
            error=step_data.get("error"),
            provider=step_data.get("provider")
        )

    return PostProgress(
        id=str(p.id),
        post_index=p.post_index,
        post_date=p.post_date.isoformat() if p.post_date else "",
        post_topic=p.post_topic,
        steps=steps_response,
        overall_status=p.overall_status.value if p.overall_status else "pending",
        progress_percent=p.progress_percent or 0,
        last_error=p.last_error,
        created_at=p.created_at.isoformat() if p.created_at else "",
        updated_at=p.updated_at.isoformat() if p.updated_at else None,
        completed_at=p.completed_at.isoformat() if p.completed_at else None
    )


def _build_plan_summary(plan_id: str, entries: list[PostGenerationProgress]) -> PlanProgressSummary:
    """Build summary from progress entries."""
    posts = [_to_post_progress(e) for e in entries]

    completed = sum(1 for e in entries if e.overall_status == GenerationStepStatus.COMPLETED)
    in_progress = sum(1 for e in entries if e.overall_status == GenerationStepStatus.IN_PROGRESS)
    failed = sum(1 for e in entries if e.overall_status == GenerationStepStatus.FAILED)

    total = len(entries)
    overall_progress = int(sum(e.progress_percent or 0 for e in entries) / total) if total > 0 else 0

    return PlanProgressSummary(
        content_plan_id=plan_id,
        total_posts=total,
        completed_posts=completed,
        in_progress_posts=in_progress,
        failed_posts=failed,
        overall_progress_percent=overall_progress,
        posts=posts
    )


def _recalculate_progress(progress: PostGenerationProgress):
    """Recalculate progress percentage and status."""
    if not progress.steps:
        progress.progress_percent = 0
        return

    # Step weights
    weights = {
        "caption_generated": 15,
        "hashtags_generated": 5,
        "image_prompt_generated": 10,
        "image_generated": 30,
        "video_generated": 30,
        "audio_generated": 10,
        "post_scheduled": 20,
        "post_published": 10,
        "content_reviewed": 5,
        "compliance_checked": 5
    }

    total_weight = 0
    completed_weight = 0

    for step, data in progress.steps.items():
        weight = weights.get(step, 10)
        total_weight += weight
        status = data.get("status", "pending")
        if status == "completed" or status == "skipped":
            completed_weight += weight
        elif status == "in_progress":
            completed_weight += weight * 0.5

    progress.progress_percent = int(completed_weight / total_weight * 100) if total_weight > 0 else 0

    # Update overall status
    statuses = [d.get("status") for d in progress.steps.values()]
    if all(s in ["completed", "skipped"] for s in statuses):
        progress.overall_status = GenerationStepStatus.COMPLETED
        progress.completed_at = datetime.utcnow()
    elif any(s == "failed" for s in statuses):
        progress.overall_status = GenerationStepStatus.FAILED
    elif any(s == "in_progress" for s in statuses):
        progress.overall_status = GenerationStepStatus.IN_PROGRESS
    else:
        progress.overall_status = GenerationStepStatus.PENDING
