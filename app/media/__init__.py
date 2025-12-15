"""
Media processing module for SoVAni Crosspost.

This module provides intelligent media adaptation for social platforms:
- SmartMediaAdapter: Face-aware, content-aware cropping
- FFmpegWrapper: Video transcoding and conversion
- Platform specifications and quality presets
"""

from .smart_media_adapter import (
    SmartMediaAdapter,
    smart_adapter,
    adapt_image_for_platform,
    adapt_video_for_platform,
    adapt_for_platforms,
    CropMode,
    AdaptationResult,
    RegionOfInterest,
    PLATFORM_SPECS,
)

from .ffmpeg_wrapper import (
    FFmpegWrapper,
    ffmpeg_wrapper,
    AspectRatio,
    ConversionStrategy,
    QualityProfile,
    ConversionParams,
    ConversionResult,
    convert_to_aspect_ratio,
    convert_for_platform,
    get_video_info,
)

from .smart_crop_stub import (
    SmartCropStub,
    smart_crop_stub,
    CropStrategy,
    ContentType,
    analyze_for_smart_crop,
    get_platform_strategy,
    get_smart_crop_info,
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
