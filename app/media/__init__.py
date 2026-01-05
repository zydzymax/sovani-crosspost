"""
Media processing module for SalesWhisper Crosspost.

This module provides intelligent media adaptation for social platforms:
- SmartMediaAdapter: Face-aware, content-aware cropping
- FFmpegWrapper: Video transcoding and conversion
- Platform specifications and quality presets
"""

from .ffmpeg_wrapper import (
    AspectRatio,
    ConversionParams,
    ConversionResult,
    ConversionStrategy,
    FFmpegWrapper,
    QualityProfile,
    convert_for_platform,
    convert_to_aspect_ratio,
    ffmpeg_wrapper,
    get_video_info,
)
from .smart_crop_stub import (
    ContentType,
    CropStrategy,
    SmartCropStub,
    analyze_for_smart_crop,
    get_platform_strategy,
    get_smart_crop_info,
    smart_crop_stub,
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
    smart_adapter,
)

__all__ = [
    # Smart Media Adapter
    "SmartMediaAdapter",
    "smart_adapter",
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
    "CropStrategy",
    "ContentType",
    "analyze_for_smart_crop",
    "get_platform_strategy",
    "get_smart_crop_info",
]
