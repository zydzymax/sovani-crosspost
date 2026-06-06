"""Video Generation API routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.entities import User, VideoGenProvider, VideoGenStatus, VideoGenTask
from ..services.video_gen_kling import AspectRatio as KlingAspectRatio
from ..services.video_gen_kling import KlingService, VideoDuration
from ..services.video_gen_minimax import MinimaxService
from ..services.video_gen_runway import AspectRatio as RunwayAspectRatio
from ..services.video_gen_runway import RunwayService
from .deps import get_current_user, get_db_async_session

router = APIRouter(prefix="/video-gen", tags=["video-generation"])

VALID_VIDEO_PROVIDERS = ("kling", "minimax", "runway")
VALID_ASPECT_RATIOS = ("16:9", "9:16", "1:1")
PROVIDER_MAP = {
    "kling": VideoGenProvider.KLING,
    "minimax": VideoGenProvider.MINIMAX,
    "runway": VideoGenProvider.RUNWAY,
}


def _validate_provider(provider: str) -> None:
    if provider not in VALID_VIDEO_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid provider. Valid options: {list(VALID_VIDEO_PROVIDERS)}",
        )


def _validate_aspect_ratio(aspect_ratio: str) -> None:
    if aspect_ratio not in VALID_ASPECT_RATIOS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid aspect ratio. Valid options: {list(VALID_ASPECT_RATIOS)}",
        )


def _to_video_task_response(task: VideoGenTask) -> "VideoTaskResponse":
    return VideoTaskResponse(
        id=str(task.id),
        status=task.status.value,
        prompt=task.prompt,
        duration_seconds=task.duration_seconds,
        video_url=task.result_url,
        thumbnail_url=task.result_thumbnail_url,
        cost_estimate=task.cost_estimate,
        error=task.error_message,
        created_at=task.created_at.isoformat(),
    )


async def _mark_task_failed(db: AsyncSession, task: VideoGenTask, error_message: str) -> None:
    task.status = VideoGenStatus.FAILED
    task.error_message = error_message
    await db.commit()
    await db.refresh(task)


async def _apply_generation_result(db: AsyncSession, task: VideoGenTask, result) -> None:
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


async def _get_user_task_or_404(db: AsyncSession, task_id: UUID, user_id) -> VideoGenTask:
    task = await db.get(VideoGenTask, task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    if task.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return task


async def _generate_video_text_result(request: "TextToVideoRequest"):
    if request.provider == "kling":
        service = KlingService()
        duration = VideoDuration.SHORT if request.duration <= 5 else VideoDuration.LONG
        aspect = KlingAspectRatio(request.aspect_ratio)
        try:
            return await service.generate_from_text(prompt=request.prompt, duration=duration, aspect_ratio=aspect)
        finally:
            await service.close()

    if request.provider == "minimax":
        service = MinimaxService()
        try:
            return await service.generate_from_text(prompt=request.prompt)
        finally:
            await service.close()

    service = RunwayService()
    aspect = RunwayAspectRatio(request.aspect_ratio)
    try:
        return await service.generate_video_from_text(prompt=request.prompt, duration=request.duration, aspect_ratio=aspect)
    finally:
        await service.close()


async def _generate_video_image_result(request: "ImageToVideoRequest"):
    if request.provider == "kling":
        service = KlingService()
        duration = VideoDuration.SHORT if request.duration <= 5 else VideoDuration.LONG
        try:
            return await service.generate_from_image(image_url=request.image_url, prompt=request.prompt, duration=duration)
        finally:
            await service.close()

    if request.provider == "minimax":
        service = MinimaxService()
        try:
            return await service.generate_from_image(image_url=request.image_url, prompt=request.prompt)
        finally:
            await service.close()

    service = RunwayService()
    try:
        return await service.generate_video_from_image(
            image_url=request.image_url,
            prompt=request.prompt,
            duration=request.duration,
        )
    finally:
        await service.close()


# Request/Response models
class TextToVideoRequest(BaseModel):
    """Request for text-to-video generation."""

    prompt: str = Field(..., min_length=10, max_length=1000, description="Video description")
    duration: int = Field(5, ge=5, le=10, description="Duration in seconds (5 or 10)")
    aspect_ratio: str = Field("16:9", description="Aspect ratio (16:9, 9:16, 1:1)")
    provider: str = Field("kling", description="Provider: kling, minimax, runway")


class ImageToVideoRequest(BaseModel):
    """Request for image-to-video generation."""

    image_url: str = Field(..., description="Source image URL")
    prompt: str = Field("", max_length=500, description="Optional motion guidance")
    duration: int = Field(5, ge=5, le=10, description="Duration in seconds")
    provider: str = Field("kling", description="Provider: kling, minimax, runway")


class VideoTaskResponse(BaseModel):
    """Video generation task response."""

    id: str
    status: str
    prompt: str
    duration_seconds: int
    video_url: str | None = None
    thumbnail_url: str | None = None
    cost_estimate: float
    error: str | None = None
    created_at: str


@router.post("/text-to-video", response_model=VideoTaskResponse)
async def generate_video_from_text(
    request: TextToVideoRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_async_session),
):
    """Generate video from text prompt."""
    _validate_provider(request.provider)
    _validate_aspect_ratio(request.aspect_ratio)

    # Create task record
    task = VideoGenTask(
        user_id=current_user.id,
        provider=PROVIDER_MAP[request.provider],
        prompt=request.prompt,
        duration_seconds=request.duration,
        status=VideoGenStatus.PENDING,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    # Start generation based on provider
    try:
        task.status = VideoGenStatus.GENERATING
        await db.commit()
        result = await _generate_video_text_result(request)
        await _apply_generation_result(db, task, result)

    except Exception as e:
        await _mark_task_failed(db, task, str(e))

    return _to_video_task_response(task)


@router.post("/image-to-video", response_model=VideoTaskResponse)
async def generate_video_from_image(
    request: ImageToVideoRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_async_session),
):
    """Generate video from image."""
    _validate_provider(request.provider)

    # Create task record
    task = VideoGenTask(
        user_id=current_user.id,
        provider=PROVIDER_MAP[request.provider],
        prompt=request.prompt or "animate this image",
        source_image_url=request.image_url,
        duration_seconds=request.duration,
        status=VideoGenStatus.PENDING,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    # Start generation based on provider
    try:
        task.status = VideoGenStatus.GENERATING
        await db.commit()
        result = await _generate_video_image_result(request)
        await _apply_generation_result(db, task, result)

    except Exception as e:
        await _mark_task_failed(db, task, str(e))

    return _to_video_task_response(task)


@router.get("/task/{task_id}", response_model=VideoTaskResponse)
async def get_task_status(
    task_id: UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_async_session)
):
    """Get video generation task status."""
    task = await _get_user_task_or_404(db, task_id, current_user.id)
    return _to_video_task_response(task)


@router.get("/tasks", response_model=list[VideoTaskResponse])
async def list_tasks(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_async_session),
    limit: int = 20,
    offset: int = 0,
):
    """List user's video generation tasks."""
    query = (
        select(VideoGenTask)
        .where(VideoGenTask.user_id == current_user.id)
        .order_by(VideoGenTask.created_at.desc())
        .limit(limit)
        .offset(offset)
    )

    result = await db.execute(query)
    tasks = result.scalars().all()

    return [_to_video_task_response(task) for task in tasks]


@router.get("/providers")
async def get_video_providers():
    """Get available video generation providers."""
    return {
        "providers": [
            {
                "id": "kling",
                "name": "Kling AI",
                "description": "Kling 2.0 - высококачественная генерация видео",
                "cost_per_video_5s": 0.25,
                "cost_per_video_10s": 0.50,
                "max_duration": 10,
                "features": ["text-to-video", "image-to-video"],
                "aspect_ratios": ["16:9", "9:16", "1:1"],
                "recommended": True,
            },
            {
                "id": "minimax",
                "name": "MiniMax Hailuo",
                "description": "Hailuo Video-01 - кинематографическое качество",
                "cost_per_video": 0.28,
                "max_duration": 6,
                "features": ["text-to-video", "image-to-video"],
                "aspect_ratios": ["16:9"],
                "recommended": True,
            },
            {
                "id": "runway",
                "name": "Runway ML",
                "description": "Gen-3 Alpha - быстрая генерация",
                "cost_per_second": 0.15,
                "max_duration": 10,
                "features": ["text-to-video", "image-to-video"],
                "aspect_ratios": ["16:9", "9:16", "1:1"],
                "recommended": False,
                "note": "Требуется платная подписка для API",
            },
        ]
    }
