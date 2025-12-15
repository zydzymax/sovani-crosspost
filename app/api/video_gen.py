"""Video Generation API routes."""

from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..models.db import get_db
from ..models.entities import User, VideoGenTask, VideoGenStatus, VideoGenProvider
from ..services.video_gen_runway import RunwayService, RunwayVideoResult, AspectRatio
from .deps import get_current_user

router = APIRouter(prefix="/video-gen", tags=["video-generation"])


# Request/Response models
class TextToVideoRequest(BaseModel):
    """Request for text-to-video generation."""
    prompt: str = Field(..., min_length=10, max_length=1000, description="Video description")
    duration: int = Field(5, ge=5, le=10, description="Duration in seconds (5 or 10)")
    aspect_ratio: str = Field("16:9", description="Aspect ratio (16:9, 9:16, 1:1)")


class ImageToVideoRequest(BaseModel):
    """Request for image-to-video generation."""
    image_url: str = Field(..., description="Source image URL")
    prompt: str = Field("", max_length=500, description="Optional motion guidance")
    duration: int = Field(5, ge=5, le=10, description="Duration in seconds")


class VideoTaskResponse(BaseModel):
    """Video generation task response."""
    id: str
    status: str
    prompt: str
    duration_seconds: int
    video_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    cost_estimate: float
    error: Optional[str] = None
    created_at: str


@router.post("/text-to-video", response_model=VideoTaskResponse)
async def generate_video_from_text(
    request: TextToVideoRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Generate video from text prompt."""
    # Validate aspect ratio
    valid_ratios = ["16:9", "9:16", "1:1"]
    if request.aspect_ratio not in valid_ratios:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid aspect ratio. Valid options: {valid_ratios}"
        )

    # Create task record
    task = VideoGenTask(
        user_id=current_user.id,
        provider=VideoGenProvider.RUNWAY,
        prompt=request.prompt,
        duration_seconds=request.duration,
        status=VideoGenStatus.PENDING
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    # Start generation
    try:
        service = RunwayService()
        aspect = AspectRatio(request.aspect_ratio)

        # Update status
        task.status = VideoGenStatus.GENERATING
        await db.commit()

        result = await service.generate_video_from_text(
            prompt=request.prompt,
            duration=request.duration,
            aspect_ratio=aspect
        )

        # Update task with result
        if result.success:
            task.status = VideoGenStatus.COMPLETED
            task.result_url = result.video_url
            task.result_thumbnail_url = result.thumbnail_url
            task.cost_estimate = result.cost_estimate
            task.provider_task_id = result.task_id
        else:
            task.status = VideoGenStatus.FAILED
            task.error_message = result.error

        await db.commit()
        await db.refresh(task)

        await service.close()

    except Exception as e:
        task.status = VideoGenStatus.FAILED
        task.error_message = str(e)
        await db.commit()
        await db.refresh(task)

    return VideoTaskResponse(
        id=str(task.id),
        status=task.status.value,
        prompt=task.prompt,
        duration_seconds=task.duration_seconds,
        video_url=task.result_url,
        thumbnail_url=task.result_thumbnail_url,
        cost_estimate=task.cost_estimate,
        error=task.error_message,
        created_at=task.created_at.isoformat()
    )


@router.post("/image-to-video", response_model=VideoTaskResponse)
async def generate_video_from_image(
    request: ImageToVideoRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Generate video from image."""
    # Create task record
    task = VideoGenTask(
        user_id=current_user.id,
        provider=VideoGenProvider.RUNWAY,
        prompt=request.prompt or "animate this image",
        source_image_url=request.image_url,
        duration_seconds=request.duration,
        status=VideoGenStatus.PENDING
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    # Start generation
    try:
        service = RunwayService()

        task.status = VideoGenStatus.GENERATING
        await db.commit()

        result = await service.generate_video_from_image(
            image_url=request.image_url,
            prompt=request.prompt,
            duration=request.duration
        )

        if result.success:
            task.status = VideoGenStatus.COMPLETED
            task.result_url = result.video_url
            task.result_thumbnail_url = result.thumbnail_url
            task.cost_estimate = result.cost_estimate
            task.provider_task_id = result.task_id
        else:
            task.status = VideoGenStatus.FAILED
            task.error_message = result.error

        await db.commit()
        await db.refresh(task)

        await service.close()

    except Exception as e:
        task.status = VideoGenStatus.FAILED
        task.error_message = str(e)
        await db.commit()
        await db.refresh(task)

    return VideoTaskResponse(
        id=str(task.id),
        status=task.status.value,
        prompt=task.prompt,
        duration_seconds=task.duration_seconds,
        video_url=task.result_url,
        thumbnail_url=task.result_thumbnail_url,
        cost_estimate=task.cost_estimate,
        error=task.error_message,
        created_at=task.created_at.isoformat()
    )


@router.get("/task/{task_id}", response_model=VideoTaskResponse)
async def get_task_status(
    task_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get video generation task status."""
    task = await db.get(VideoGenTask, task_id)

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found"
        )

    if task.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    return VideoTaskResponse(
        id=str(task.id),
        status=task.status.value,
        prompt=task.prompt,
        duration_seconds=task.duration_seconds,
        video_url=task.result_url,
        thumbnail_url=task.result_thumbnail_url,
        cost_estimate=task.cost_estimate,
        error=task.error_message,
        created_at=task.created_at.isoformat()
    )


@router.get("/tasks", response_model=List[VideoTaskResponse])
async def list_tasks(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = 20,
    offset: int = 0
):
    """List user's video generation tasks."""
    query = select(VideoGenTask).where(
        VideoGenTask.user_id == current_user.id
    ).order_by(VideoGenTask.created_at.desc()).limit(limit).offset(offset)

    result = await db.execute(query)
    tasks = result.scalars().all()

    return [
        VideoTaskResponse(
            id=str(task.id),
            status=task.status.value,
            prompt=task.prompt,
            duration_seconds=task.duration_seconds,
            video_url=task.result_url,
            thumbnail_url=task.result_thumbnail_url,
            cost_estimate=task.cost_estimate,
            error=task.error_message,
            created_at=task.created_at.isoformat()
        )
        for task in tasks
    ]


@router.get("/providers")
async def get_video_providers():
    """Get available video generation providers."""
    return {
        "providers": [
            {
                "id": "runway",
                "name": "Runway ML",
                "description": "Gen-3 Alpha text-to-video and image-to-video",
                "cost_per_second": 0.15,
                "max_duration": 10,
                "features": ["text-to-video", "image-to-video"],
                "aspect_ratios": ["16:9", "9:16", "1:1"]
            }
        ]
    }
