"""
Transcode stage tasks for SalesWhisper Crosspost.

This module handles intelligent media adaptation for different platforms:
- Smart cropping (face-aware, content-aware)
- Aspect ratio conversion without black bars
- Quality optimization per platform
"""

import asyncio
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from ...adapters.storage_s3 import s3_storage
from ...core.logging import get_logger, with_logging_context
from ...media.smart_media_adapter import PLATFORM_SPECS, AdaptationResult, CropMode, smart_adapter
from ...observability.metrics import metrics
from ..celery_app import celery

logger = get_logger("tasks.transcode")


# Platform format mapping
PLATFORM_FORMATS = {
    "instagram": "feed",
    "tiktok": "video",
    "youtube": "video",
    "vk": "post",
    "facebook": "feed",
    "telegram": "post",
    "rutube": "video",
}


def run_async(coro):
    """Run async coroutine in sync context."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(coro)


@celery.task(bind=True, name="app.workers.tasks.transcode.process_media")
def process_media(self, stage_data: dict[str, Any]) -> dict[str, Any]:
    """
    Process and transcode media files for different platforms.

    This task:
    1. Downloads original media from S3
    2. Detects faces and important regions
    3. Smart-crops to each platform's required aspect ratio
    4. Uploads transcoded versions back to S3
    5. Triggers preflight checks

    Args:
        stage_data: Data from previous stage containing post_id, media_assets, platforms

    Returns:
        Dict with processed media paths per platform
    """
    task_start_time = time.time()
    post_id = stage_data.get("post_id")

    with with_logging_context(task_id=self.request.id, post_id=post_id):
        logger.info("Starting smart media processing", post_id=post_id)

        try:
            # Extract data from previous stages
            media_assets = stage_data.get("media_assets", [])
            target_platforms = stage_data.get("platforms", ["telegram", "vk", "instagram"])

            if not media_assets:
                logger.warning("No media assets to process", post_id=post_id)
                return _trigger_next_stage(stage_data, {}, task_start_time)

            processed_media = {}

            # Process each media asset
            for asset in media_assets:
                asset_id = asset.get("id")
                media_type = asset.get("media_type", "image")
                original_path = asset.get("original_path")

                if not original_path:
                    logger.warning(f"No original path for asset {asset_id}")
                    continue

                logger.info(f"Processing asset {asset_id}",
                           media_type=media_type,
                           platforms=target_platforms)

                # Download from S3 to temp file
                local_input = _download_media(original_path)
                if not local_input:
                    logger.error(f"Failed to download media: {original_path}")
                    continue

                try:
                    # Create temp output directory
                    output_dir = tempfile.mkdtemp(prefix=f"transcode_{asset_id}_")

                    # Run smart adaptation for all platforms
                    results = run_async(
                        _adapt_media_for_platforms(
                            local_input,
                            output_dir,
                            target_platforms,
                            media_type
                        )
                    )

                    # Upload results to S3 and collect paths
                    asset_paths = {}
                    for platform, result in results.items():
                        if result.success and os.path.exists(result.output_path):
                            # Upload to S3
                            s3_key = f"transcoded/{post_id}/{asset_id}/{platform}/{os.path.basename(result.output_path)}"
                            s3_url = _upload_media(result.output_path, s3_key)

                            asset_paths[platform] = {
                                "url": s3_url,
                                "local_path": result.output_path,
                                "size": result.output_size,
                                "crop_mode": result.crop_mode.value,
                                "regions_detected": len(result.regions_detected),
                                "processing_time": result.processing_time
                            }

                            logger.info(f"Adapted {media_type} for {platform}",
                                       original_size=result.original_size,
                                       output_size=result.output_size,
                                       crop_mode=result.crop_mode.value)

                            # Track metrics
                            metrics.track_media_processed(
                                media_type=media_type,
                                platform=platform,
                                success=True,
                                duration=result.processing_time
                            )
                        else:
                            logger.error(f"Adaptation failed for {platform}",
                                        error=result.error_message if result else "Unknown")
                            metrics.track_media_failed(media_type, platform, "adaptation_failed")

                    processed_media[asset_id] = asset_paths

                finally:
                    # Cleanup local input file
                    if local_input and os.path.exists(local_input):
                        try:
                            os.unlink(local_input)
                        except Exception as e:
                            logger.warning(f"Failed to cleanup temp file: {e}")

            # Log summary
            total_time = time.time() - task_start_time
            logger.info("Media processing completed",
                       post_id=post_id,
                       assets_processed=len(processed_media),
                       total_time=total_time)

            return _trigger_next_stage(stage_data, processed_media, task_start_time)

        except Exception as e:
            logger.error("Media processing failed",
                        post_id=post_id,
                        error=str(e),
                        exc_info=True)

            if self.request.retries < self.max_retries:
                raise self.retry(countdown=60 * (self.request.retries + 1))

            return {
                "success": False,
                "post_id": post_id,
                "error": str(e),
                "stage": "transcode"
            }


async def _adapt_media_for_platforms(
    input_path: str,
    output_dir: str,
    platforms: list[str],
    media_type: str
) -> dict[str, AdaptationResult]:
    """Adapt media for multiple platforms using SmartMediaAdapter."""
    results = {}

    for platform in platforms:
        format_type = PLATFORM_FORMATS.get(platform, "feed")

        # Determine output filename
        ext = ".jpg" if media_type == "image" else ".mp4"
        output_path = os.path.join(output_dir, f"{platform}_{format_type}{ext}")

        try:
            if media_type == "image":
                result = await smart_adapter.adapt_image(
                    input_path=input_path,
                    output_path=output_path,
                    platform=platform,
                    format_type=format_type,
                    crop_mode=CropMode.SMART,
                    quality=95
                )
            else:
                result = await smart_adapter.adapt_video(
                    input_path=input_path,
                    output_path=output_path,
                    platform=platform,
                    format_type=format_type,
                    crop_mode=CropMode.SMART
                )

            results[platform] = result

        except Exception as e:
            logger.error(f"Adaptation failed for {platform}: {e}")
            results[platform] = AdaptationResult(
                success=False,
                input_path=input_path,
                output_path=output_path,
                platform=platform,
                format_type=format_type,
                original_size=(0, 0),
                output_size=(0, 0),
                crop_mode=CropMode.SMART,
                regions_detected=[],
                processing_time=0,
                error_message=str(e)
            )

    return results


def _download_media(s3_path: str) -> str | None:
    """Download media from S3 to local temp file."""
    try:
        # Determine extension from path
        ext = Path(s3_path).suffix or '.tmp'

        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            local_path = tmp.name

        # Download from S3
        if hasattr(s3_storage, 'download_file'):
            success = run_async(s3_storage.download_file(s3_path, local_path))
            if success and os.path.exists(local_path):
                return local_path
        else:
            # Fallback: if s3_storage doesn't have download method,
            # assume path is already local (for testing)
            if os.path.exists(s3_path):
                import shutil
                shutil.copy(s3_path, local_path)
                return local_path

        return None

    except Exception as e:
        logger.error(f"Failed to download media: {e}")
        return None


def _upload_media(local_path: str, s3_key: str) -> str:
    """Upload transcoded media to S3."""
    try:
        if hasattr(s3_storage, 'upload_file'):
            url = run_async(s3_storage.upload_file(local_path, s3_key))
            return url
        else:
            # Fallback: return local path for testing
            return local_path

    except Exception as e:
        logger.error(f"Failed to upload media: {e}")
        return local_path


def _trigger_next_stage(
    stage_data: dict[str, Any],
    processed_media: dict[str, Any],
    start_time: float
) -> dict[str, Any]:
    """Trigger next pipeline stage (preflight)."""
    processing_time = time.time() - start_time
    post_id = stage_data.get("post_id")

    # Prepare data for next stage
    next_stage_data = {
        **stage_data,
        "processed_media": processed_media,
        "transcode_time": processing_time
    }

    # Trigger preflight checks
    from .preflight import run_preflight_checks
    next_task = run_preflight_checks.delay(next_stage_data)

    logger.info("Triggered preflight checks",
               post_id=post_id,
               next_task_id=next_task.id)

    return {
        "success": True,
        "post_id": post_id,
        "processing_time": processing_time,
        "assets_processed": len(processed_media),
        "next_stage": "preflight",
        "next_task_id": next_task.id
    }


@celery.task(bind=True, name="app.workers.tasks.transcode.adapt_single_media")
def adapt_single_media(
    self,
    input_path: str,
    output_path: str,
    platform: str,
    media_type: str = "image",
    format_type: str = "feed"
) -> dict[str, Any]:
    """
    Adapt a single media file for a specific platform.

    This is a utility task for on-demand media adaptation.

    Args:
        input_path: Path to input media (local or S3)
        output_path: Path for output media
        platform: Target platform
        media_type: "image" or "video"
        format_type: Platform format (feed, stories, reels, etc.)

    Returns:
        Adaptation result dict
    """
    task_start_time = time.time()

    with with_logging_context(task_id=self.request.id):
        logger.info(f"Adapting single {media_type} for {platform}/{format_type}")

        try:
            # Download if S3 path
            local_input = input_path
            if input_path.startswith('s3://') or input_path.startswith('http'):
                local_input = _download_media(input_path)
                if not local_input:
                    raise ValueError(f"Failed to download: {input_path}")

            # Run adaptation
            if media_type == "image":
                result = run_async(
                    smart_adapter.adapt_image(
                        local_input, output_path, platform, format_type
                    )
                )
            else:
                result = run_async(
                    smart_adapter.adapt_video(
                        local_input, output_path, platform, format_type
                    )
                )

            return {
                "success": result.success,
                "output_path": result.output_path,
                "original_size": result.original_size,
                "output_size": result.output_size,
                "crop_mode": result.crop_mode.value,
                "regions_detected": len(result.regions_detected),
                "processing_time": result.processing_time,
                "error": result.error_message
            }

        except Exception as e:
            logger.error(f"Single media adaptation failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "processing_time": time.time() - task_start_time
            }


@celery.task(bind=True, name="app.workers.tasks.transcode.get_platform_specs")
def get_platform_specs(self, platform: str) -> dict[str, Any]:
    """Get media specifications for a platform."""
    specs = PLATFORM_SPECS.get(platform.lower(), {})
    return {
        "platform": platform,
        "specs": specs,
        "supported_formats": list(specs.keys()) if specs else []
    }
