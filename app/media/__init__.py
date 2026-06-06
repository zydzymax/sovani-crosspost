"""
Media processing module for SalesWhisper Crosspost.

This module provides intelligent media adaptation for social platforms:
- SmartMediaAdapter: Face-aware, content-aware cropping
- FFmpegWrapper: Video transcoding and conversion
- Platform specifications and quality presets
"""

from . import ffmpeg_wrapper as ffmpeg_wrapper
from . import smart_crop_stub as smart_crop_stub
from . import smart_media_adapter as smart_media_adapter
from .ffmpeg_wrapper import (
    AspectRatio,
    ConversionParams,
    ConversionResult,
    ConversionStrategy,
    FFmpegWrapper,
    QualityProfile,
    convert_for_platform,
    convert_to_aspect_ratio,
    get_video_info,
)
from .ffmpeg_wrapper import (
    ffmpeg_wrapper as ffmpeg_wrapper_instance,
)
from .smart_crop_stub import (
    ContentType,
    CropStrategy,
    SmartCropStub,
    analyze_for_smart_crop,
    get_platform_strategy,
    get_smart_crop_info,
)
from .smart_crop_stub import (
    smart_crop_stub as smart_crop_stub_instance,
)
from .smart_media_adapter import (
    PLATFORM_SPECS,
    AdaptationResult,
    CropMode,
    RegionOfInterest,
    SmartMediaAdapter,
    adapt_for_platforms,
    adapt_image_for_platform,
    adapt_video_for_platform,
)
from .smart_media_adapter import (
    smart_adapter as smart_adapter_instance,
)

smart_adapter = smart_adapter_instance

__all__ = [
    # Smart Media Adapter
    "SmartMediaAdapter",
    "smart_media_adapter",
    "smart_adapter",
    "smart_adapter_instance",
    "adapt_image_for_platform",
    "adapt_video_for_platform",
    "adapt_for_platforms",
    "CropMode",
    "AdaptationResult",
    "RegionOfInterest",
    "PLATFORM_SPECS",
    # FFmpeg Wrapper
    "FFmpegWrapper",
    "ffmpeg_wrapper",
    "ffmpeg_wrapper_instance",
    "AspectRatio",
    "ConversionStrategy",
    "QualityProfile",
    "ConversionParams",
    "ConversionResult",
    "convert_to_aspect_ratio",
    "convert_for_platform",
    "get_video_info",
    # Smart Crop (legacy stub)
    "SmartCropStub",
    "smart_crop_stub",
    "smart_crop_stub_instance",
    "CropStrategy",
    "ContentType",
    "analyze_for_smart_crop",
    "get_platform_strategy",
    "get_smart_crop_info",
]
