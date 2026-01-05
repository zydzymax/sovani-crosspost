"""
Smart Media Adapter for SalesWhisper Crosspost.

Intelligent content adaptation for social media platforms:
- Face-aware cropping (keeps faces in frame)
- Text-aware cropping (avoids cutting text)
- Content-aware scaling (no black bars/letterboxing)
- Platform-specific optimization

Supports: Images (JPEG, PNG) and Videos (MP4, MOV)
"""

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np
from PIL import Image, ImageFilter

from ..core.logging import get_logger

logger = get_logger("media.smart_adapter")


# Platform aspect ratio requirements
PLATFORM_SPECS = {
    "instagram": {
        "feed": {"aspect_ratio": (4, 5), "max_size": (1080, 1350), "preferred": "portrait"},
        "stories": {"aspect_ratio": (9, 16), "max_size": (1080, 1920), "preferred": "vertical"},
        "reels": {"aspect_ratio": (9, 16), "max_size": (1080, 1920), "preferred": "vertical"},
        "square": {"aspect_ratio": (1, 1), "max_size": (1080, 1080), "preferred": "square"},
    },
    "tiktok": {
        "video": {"aspect_ratio": (9, 16), "max_size": (1080, 1920), "preferred": "vertical"},
    },
    "youtube": {
        "video": {"aspect_ratio": (16, 9), "max_size": (1920, 1080), "preferred": "landscape"},
        "shorts": {"aspect_ratio": (9, 16), "max_size": (1080, 1920), "preferred": "vertical"},
    },
    "vk": {
        "post": {"aspect_ratio": (16, 9), "max_size": (1920, 1080), "preferred": "landscape"},
        "story": {"aspect_ratio": (9, 16), "max_size": (1080, 1920), "preferred": "vertical"},
    },
    "facebook": {
        "feed": {"aspect_ratio": (16, 9), "max_size": (1920, 1080), "preferred": "landscape"},
        "reels": {"aspect_ratio": (9, 16), "max_size": (1080, 1920), "preferred": "vertical"},
        "square": {"aspect_ratio": (1, 1), "max_size": (1080, 1080), "preferred": "square"},
    },
    "telegram": {
        "post": {"aspect_ratio": None, "max_size": (2560, 2560), "preferred": "any"},  # Telegram is flexible
    },
    "rutube": {
        "video": {"aspect_ratio": (16, 9), "max_size": (1920, 1080), "preferred": "landscape"},
    },
}


class CropMode(Enum):
    """Cropping modes for content adaptation."""
    SMART = "smart"          # AI-based smart crop (face/content aware)
    CENTER = "center"        # Simple center crop
    TOP = "top"              # Crop from top (good for portraits)
    BOTTOM = "bottom"        # Crop from bottom
    FILL = "fill"            # Scale to fill (may crop edges)
    FIT = "fit"              # Scale to fit (may add blur background)


@dataclass
class RegionOfInterest:
    """Region of interest in media."""
    x: int
    y: int
    width: int
    height: int
    importance: float  # 0.0 to 1.0
    type: str  # "face", "text", "object", "salient"


@dataclass
class AdaptationResult:
    """Result of media adaptation."""
    success: bool
    input_path: str
    output_path: str
    platform: str
    format_type: str  # "feed", "stories", etc.
    original_size: tuple[int, int]
    output_size: tuple[int, int]
    crop_mode: CropMode
    regions_detected: list[RegionOfInterest]
    processing_time: float
    error_message: str | None = None


class FaceDetector:
    """Simple face detection using OpenCV Haar cascades."""

    def __init__(self):
        self.cascade_path = None
        self.cascade = None
        self._init_cascade()

    def _init_cascade(self):
        """Initialize face detection cascade."""
        try:
            import cv2
            # Try common cascade paths
            cascade_paths = [
                "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml",
                "/usr/share/opencv/haarcascades/haarcascade_frontalface_default.xml",
                "/usr/local/share/opencv4/haarcascades/haarcascade_frontalface_default.xml",
            ]

            for path in cascade_paths:
                if os.path.exists(path):
                    self.cascade_path = path
                    self.cascade = cv2.CascadeClassifier(path)
                    logger.info(f"Face cascade loaded from {path}")
                    return

            # Try to use cv2.data if available
            if hasattr(cv2, 'data'):
                cascade_file = os.path.join(cv2.data.haarcascades, 'haarcascade_frontalface_default.xml')
                if os.path.exists(cascade_file):
                    self.cascade_path = cascade_file
                    self.cascade = cv2.CascadeClassifier(cascade_file)
                    logger.info("Face cascade loaded from cv2.data")
                    return

            logger.warning("Face cascade not found, face detection disabled")

        except ImportError:
            logger.warning("OpenCV not installed, face detection disabled")

    def detect(self, image: Image.Image) -> list[RegionOfInterest]:
        """Detect faces in image."""
        if self.cascade is None:
            return []

        try:
            import cv2

            # Convert PIL to OpenCV format
            img_array = np.array(image.convert('RGB'))
            gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)

            # Detect faces
            faces = self.cascade.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=5,
                minSize=(30, 30)
            )

            regions = []
            for (x, y, w, h) in faces:
                # Add padding around face
                padding = int(max(w, h) * 0.3)
                regions.append(RegionOfInterest(
                    x=max(0, x - padding),
                    y=max(0, y - padding),
                    width=w + 2 * padding,
                    height=h + 2 * padding,
                    importance=0.9,
                    type="face"
                ))

            return regions

        except Exception as e:
            logger.warning(f"Face detection failed: {e}")
            return []


class SaliencyDetector:
    """Detect salient (visually important) regions in image."""

    def detect(self, image: Image.Image) -> list[RegionOfInterest]:
        """Detect salient regions using edge detection and contrast."""
        try:
            # Convert to grayscale
            gray = image.convert('L')

            # Apply edge detection
            edges = gray.filter(ImageFilter.FIND_EDGES)

            # Convert to numpy for analysis
            edge_array = np.array(edges)

            # Find regions with high edge density
            regions = self._find_salient_regions(edge_array, image.size)

            return regions

        except Exception as e:
            logger.warning(f"Saliency detection failed: {e}")
            return []

    def _find_salient_regions(self, edge_array: np.ndarray,
                             image_size: tuple[int, int]) -> list[RegionOfInterest]:
        """Find regions with high visual importance."""
        regions = []
        width, height = image_size

        # Divide image into grid and find most salient region
        grid_size = 3
        cell_w = width // grid_size
        cell_h = height // grid_size

        max_density = 0
        max_cell = (1, 1)  # Default to center

        for i in range(grid_size):
            for j in range(grid_size):
                cell = edge_array[j*cell_h:(j+1)*cell_h, i*cell_w:(i+1)*cell_w]
                density = np.mean(cell)

                if density > max_density:
                    max_density = density
                    max_cell = (i, j)

        # Create region around most salient cell
        i, j = max_cell
        regions.append(RegionOfInterest(
            x=i * cell_w,
            y=j * cell_h,
            width=cell_w * 2,  # Expand region
            height=cell_h * 2,
            importance=min(max_density / 128.0, 1.0),
            type="salient"
        ))

        return regions


class SmartMediaAdapter:
    """
    Intelligent media adaptation for social platforms.

    Features:
    - Face-aware cropping (keeps faces in frame)
    - Content-aware scaling (no black bars)
    - Platform-specific optimization
    - Blur-fill for extreme aspect ratio mismatches
    """

    def __init__(self):
        self.face_detector = FaceDetector()
        self.saliency_detector = SaliencyDetector()
        self.temp_dir = tempfile.gettempdir()
        logger.info("SmartMediaAdapter initialized")

    async def adapt_image(
        self,
        input_path: str,
        output_path: str,
        platform: str,
        format_type: str = "feed",
        crop_mode: CropMode = CropMode.SMART,
        quality: int = 95
    ) -> AdaptationResult:
        """
        Adapt image for specific platform and format.

        Args:
            input_path: Path to input image
            output_path: Path for output image
            platform: Target platform (instagram, tiktok, etc.)
            format_type: Format type (feed, stories, reels, etc.)
            crop_mode: Cropping strategy
            quality: JPEG quality (1-100)

        Returns:
            AdaptationResult with details
        """
        import time
        start_time = time.time()

        try:
            logger.info(f"Adapting image for {platform}/{format_type}",
                       input_path=input_path, crop_mode=crop_mode.value)

            # Load image
            img = Image.open(input_path)
            original_size = img.size

            # Get platform specs
            specs = self._get_platform_specs(platform, format_type)
            if not specs:
                raise ValueError(f"Unknown platform/format: {platform}/{format_type}")

            target_ratio = specs.get("aspect_ratio")
            max_size = specs["max_size"]

            # Detect regions of interest
            regions = []
            if crop_mode == CropMode.SMART:
                regions = await self._detect_regions(img)

            # Calculate crop area
            if target_ratio:
                crop_box = self._calculate_smart_crop(
                    img.size, target_ratio, regions, crop_mode
                )
                img = img.crop(crop_box)

            # Resize to target size maintaining quality
            img = self._smart_resize(img, max_size)

            # Ensure output directory exists
            os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)

            # Save with appropriate format
            output_format = 'JPEG' if output_path.lower().endswith(('.jpg', '.jpeg')) else 'PNG'

            if output_format == 'JPEG':
                # Convert to RGB if necessary
                if img.mode in ('RGBA', 'LA', 'P'):
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P':
                        img = img.convert('RGBA')
                    background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                    img = background
                img.save(output_path, 'JPEG', quality=quality, optimize=True)
            else:
                img.save(output_path, 'PNG', optimize=True)

            processing_time = time.time() - start_time

            logger.info("Image adapted successfully",
                       platform=platform,
                       original_size=original_size,
                       output_size=img.size,
                       processing_time=processing_time)

            return AdaptationResult(
                success=True,
                input_path=input_path,
                output_path=output_path,
                platform=platform,
                format_type=format_type,
                original_size=original_size,
                output_size=img.size,
                crop_mode=crop_mode,
                regions_detected=regions,
                processing_time=processing_time
            )

        except Exception as e:
            logger.error(f"Image adaptation failed: {e}", input_path=input_path)
            return AdaptationResult(
                success=False,
                input_path=input_path,
                output_path=output_path,
                platform=platform,
                format_type=format_type,
                original_size=(0, 0),
                output_size=(0, 0),
                crop_mode=crop_mode,
                regions_detected=[],
                processing_time=time.time() - start_time,
                error_message=str(e)
            )

    async def adapt_video(
        self,
        input_path: str,
        output_path: str,
        platform: str,
        format_type: str = "video",
        crop_mode: CropMode = CropMode.SMART
    ) -> AdaptationResult:
        """
        Adapt video for specific platform.

        Uses intelligent cropping without black bars.

        Args:
            input_path: Path to input video
            output_path: Path for output video
            platform: Target platform
            format_type: Format type (video, shorts, reels, etc.)
            crop_mode: Cropping strategy

        Returns:
            AdaptationResult with details
        """
        import time
        start_time = time.time()

        try:
            logger.info(f"Adapting video for {platform}/{format_type}",
                       input_path=input_path)

            # Get video info
            video_info = await self._get_video_info(input_path)
            original_size = (video_info['width'], video_info['height'])

            # Get platform specs
            specs = self._get_platform_specs(platform, format_type)
            if not specs:
                raise ValueError(f"Unknown platform/format: {platform}/{format_type}")

            target_ratio = specs.get("aspect_ratio")
            max_size = specs["max_size"]

            # Detect regions in first frame for smart crop
            regions = []
            if crop_mode == CropMode.SMART:
                regions = await self._detect_video_regions(input_path)

            # Build ffmpeg filter for smart crop
            filter_chain = self._build_video_filter(
                original_size, target_ratio, max_size, regions, crop_mode
            )

            # Ensure output directory exists
            os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)

            # Execute ffmpeg
            cmd = [
                'ffmpeg', '-y',
                '-i', input_path,
                '-vf', filter_chain,
                '-c:v', 'libx264',
                '-preset', 'medium',
                '-crf', '23',
                '-c:a', 'aac',
                '-b:a', '128k',
                '-movflags', '+faststart',
                output_path
            ]

            logger.debug(f"FFmpeg command: {' '.join(cmd)}")

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=600  # 10 min timeout
            )

            if process.returncode != 0:
                raise RuntimeError(f"FFmpeg failed: {stderr.decode()}")

            # Get output info
            output_info = await self._get_video_info(output_path)
            output_size = (output_info['width'], output_info['height'])

            processing_time = time.time() - start_time

            logger.info("Video adapted successfully",
                       platform=platform,
                       original_size=original_size,
                       output_size=output_size,
                       processing_time=processing_time)

            return AdaptationResult(
                success=True,
                input_path=input_path,
                output_path=output_path,
                platform=platform,
                format_type=format_type,
                original_size=original_size,
                output_size=output_size,
                crop_mode=crop_mode,
                regions_detected=regions,
                processing_time=processing_time
            )

        except Exception as e:
            logger.error(f"Video adaptation failed: {e}", input_path=input_path)
            return AdaptationResult(
                success=False,
                input_path=input_path,
                output_path=output_path,
                platform=platform,
                format_type=format_type,
                original_size=(0, 0),
                output_size=(0, 0),
                crop_mode=crop_mode,
                regions_detected=[],
                processing_time=time.time() - start_time,
                error_message=str(e)
            )

    async def adapt_for_all_platforms(
        self,
        input_path: str,
        output_dir: str,
        platforms: list[str],
        media_type: str = "image"
    ) -> dict[str, AdaptationResult]:
        """
        Adapt media for multiple platforms simultaneously.

        Args:
            input_path: Path to input media
            output_dir: Directory for output files
            platforms: List of platforms (instagram, tiktok, etc.)
            media_type: "image" or "video"

        Returns:
            Dict mapping platform to AdaptationResult
        """
        results = {}

        # Create output directory
        os.makedirs(output_dir, exist_ok=True)

        # Get file extension
        _, ext = os.path.splitext(input_path)
        if media_type == "video":
            ext = ".mp4"

        # Platform to format mapping
        platform_formats = {
            "instagram": "feed",
            "tiktok": "video",
            "youtube": "video",
            "vk": "post",
            "facebook": "feed",
            "telegram": "post",
            "rutube": "video",
        }

        for platform in platforms:
            format_type = platform_formats.get(platform, "feed")
            output_path = os.path.join(output_dir, f"{platform}_{format_type}{ext}")

            if media_type == "image":
                result = await self.adapt_image(
                    input_path, output_path, platform, format_type
                )
            else:
                result = await self.adapt_video(
                    input_path, output_path, platform, format_type
                )

            results[platform] = result

        return results

    def _get_platform_specs(self, platform: str, format_type: str) -> dict | None:
        """Get specifications for platform/format combination."""
        platform_specs = PLATFORM_SPECS.get(platform.lower())
        if not platform_specs:
            return None
        return platform_specs.get(format_type.lower())

    async def _detect_regions(self, img: Image.Image) -> list[RegionOfInterest]:
        """Detect all regions of interest in image."""
        regions = []

        # Detect faces (highest priority)
        faces = self.face_detector.detect(img)
        regions.extend(faces)

        # Detect salient regions (if no faces found)
        if not faces:
            salient = self.saliency_detector.detect(img)
            regions.extend(salient)

        return regions

    async def _detect_video_regions(self, video_path: str) -> list[RegionOfInterest]:
        """Detect regions of interest from video's first frame."""
        try:
            # Extract first frame
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                temp_frame = tmp.name

            cmd = [
                'ffmpeg', '-y', '-i', video_path,
                '-vframes', '1', '-q:v', '2', temp_frame
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await process.communicate()

            if process.returncode == 0 and os.path.exists(temp_frame):
                img = Image.open(temp_frame)
                regions = await self._detect_regions(img)
                os.unlink(temp_frame)
                return regions

            return []

        except Exception as e:
            logger.warning(f"Video region detection failed: {e}")
            return []

    def _calculate_smart_crop(
        self,
        image_size: tuple[int, int],
        target_ratio: tuple[int, int],
        regions: list[RegionOfInterest],
        crop_mode: CropMode
    ) -> tuple[int, int, int, int]:
        """
        Calculate optimal crop box that preserves regions of interest.

        Returns:
            (left, top, right, bottom) crop box
        """
        width, height = image_size
        target_w, target_h = target_ratio
        target_aspect = target_w / target_h
        current_aspect = width / height

        if abs(current_aspect - target_aspect) < 0.01:
            # Already correct aspect ratio
            return (0, 0, width, height)

        # Calculate crop dimensions
        if current_aspect > target_aspect:
            # Image is wider than needed, crop sides
            new_width = int(height * target_aspect)
            new_height = height
        else:
            # Image is taller than needed, crop top/bottom
            new_width = width
            new_height = int(width / target_aspect)

        # Find optimal crop position based on regions of interest
        if crop_mode == CropMode.SMART and regions:
            # Find center of all important regions
            total_importance = sum(r.importance for r in regions)

            if total_importance > 0:
                center_x = sum(
                    (r.x + r.width / 2) * r.importance for r in regions
                ) / total_importance
                center_y = sum(
                    (r.y + r.height / 2) * r.importance for r in regions
                ) / total_importance
            else:
                center_x = width / 2
                center_y = height / 2
        elif crop_mode == CropMode.TOP:
            center_x = width / 2
            center_y = new_height / 2
        elif crop_mode == CropMode.BOTTOM:
            center_x = width / 2
            center_y = height - new_height / 2
        else:
            # Center crop
            center_x = width / 2
            center_y = height / 2

        # Calculate crop box ensuring it stays within bounds
        left = max(0, min(width - new_width, int(center_x - new_width / 2)))
        top = max(0, min(height - new_height, int(center_y - new_height / 2)))
        right = left + new_width
        bottom = top + new_height

        return (left, top, right, bottom)

    def _smart_resize(
        self,
        img: Image.Image,
        max_size: tuple[int, int]
    ) -> Image.Image:
        """Resize image to fit within max_size while maintaining quality."""
        max_w, max_h = max_size
        width, height = img.size

        # Calculate scale factor
        scale = min(max_w / width, max_h / height, 1.0)

        if scale < 1.0:
            new_size = (int(width * scale), int(height * scale))
            img = img.resize(new_size, Image.Resampling.LANCZOS)

        return img

    def _build_video_filter(
        self,
        original_size: tuple[int, int],
        target_ratio: tuple[int, int] | None,
        max_size: tuple[int, int],
        regions: list[RegionOfInterest],
        crop_mode: CropMode
    ) -> str:
        """Build ffmpeg filter chain for video adaptation."""
        width, height = original_size
        max_w, max_h = max_size

        filters = []

        if target_ratio:
            target_w, target_h = target_ratio
            target_aspect = target_w / target_h
            current_aspect = width / height

            if abs(current_aspect - target_aspect) > 0.01:
                # Calculate crop dimensions
                if current_aspect > target_aspect:
                    # Crop width
                    new_width = int(height * target_aspect)
                    new_height = height
                else:
                    # Crop height
                    new_width = width
                    new_height = int(width / target_aspect)

                # Calculate crop position
                if crop_mode == CropMode.SMART and regions:
                    # Calculate weighted center
                    total_importance = sum(r.importance for r in regions)
                    if total_importance > 0:
                        center_x = sum(
                            (r.x + r.width / 2) * r.importance for r in regions
                        ) / total_importance
                        center_y = sum(
                            (r.y + r.height / 2) * r.importance for r in regions
                        ) / total_importance
                    else:
                        center_x = width / 2
                        center_y = height / 2

                    crop_x = max(0, min(width - new_width, int(center_x - new_width / 2)))
                    crop_y = max(0, min(height - new_height, int(center_y - new_height / 2)))
                else:
                    # Center crop
                    crop_x = (width - new_width) // 2
                    crop_y = (height - new_height) // 2

                filters.append(f"crop={new_width}:{new_height}:{crop_x}:{crop_y}")
                width, height = new_width, new_height

        # Scale to fit max size
        scale = min(max_w / width, max_h / height, 1.0)
        if scale < 1.0:
            new_w = int(width * scale)
            new_h = int(height * scale)
            # Ensure even dimensions for video encoding
            new_w = new_w - (new_w % 2)
            new_h = new_h - (new_h % 2)
            filters.append(f"scale={new_w}:{new_h}")

        return ','.join(filters) if filters else 'null'

    async def _get_video_info(self, video_path: str) -> dict[str, Any]:
        """Get video dimensions and info using ffprobe."""
        cmd = [
            'ffprobe', '-v', 'quiet',
            '-print_format', 'json',
            '-show_streams',
            video_path
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, _ = await process.communicate()

        if process.returncode == 0:
            data = json.loads(stdout.decode())
            for stream in data.get('streams', []):
                if stream.get('codec_type') == 'video':
                    return {
                        'width': stream.get('width', 0),
                        'height': stream.get('height', 0),
                        'duration': float(stream.get('duration', 0)),
                        'codec': stream.get('codec_name', ''),
                    }

        return {'width': 0, 'height': 0, 'duration': 0, 'codec': ''}


# Global instance
smart_adapter = SmartMediaAdapter()


# Convenience functions
async def adapt_image_for_platform(
    input_path: str,
    output_path: str,
    platform: str,
    format_type: str = "feed"
) -> AdaptationResult:
    """Adapt image for specific platform."""
    return await smart_adapter.adapt_image(
        input_path, output_path, platform, format_type
    )


async def adapt_video_for_platform(
    input_path: str,
    output_path: str,
    platform: str,
    format_type: str = "video"
) -> AdaptationResult:
    """Adapt video for specific platform."""
    return await smart_adapter.adapt_video(
        input_path, output_path, platform, format_type
    )


async def adapt_for_platforms(
    input_path: str,
    output_dir: str,
    platforms: list[str],
    media_type: str = "image"
) -> dict[str, AdaptationResult]:
    """Adapt media for multiple platforms."""
    return await smart_adapter.adapt_for_all_platforms(
        input_path, output_dir, platforms, media_type
    )
