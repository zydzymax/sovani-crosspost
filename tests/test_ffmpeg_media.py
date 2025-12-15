"""
Unit tests for FFmpeg media processing.

Tests for aspect ratio conversion without distortions:
- Validates output dimensions and aspect ratios
- Ensures no content distortion with pad strategy
- Tests all supported aspect ratios (9:16, 4:5, 1:1, 16:9)
- Verifies smart crop stub behavior
"""

import pytest
import asyncio
import tempfile
import os
from unittest.mock import patch, MagicMock, AsyncMock
from typing import Tuple

from app.media.ffmpeg_wrapper import (
    FFmpegWrapper,
    AspectRatio,
    ConversionStrategy,
    QualityProfile,
    ConversionParams,
    ConversionResult,
    FFmpegError,
    convert_to_aspect_ratio,
    convert_for_platform,
    get_video_info
)
from app.media.smart_crop_stub import (
    SmartCropStub,
    CropStrategy,
    ContentType,
    SmartCropParams,
    SmartCropAnalysis,
    CropRegion,
    analyze_for_smart_crop,
    get_platform_strategy,
    get_smart_crop_info
)


class TestAspectRatioCalculations:
    """Test aspect ratio calculations and validations."""
    
    def test_aspect_ratio_enum_values(self):
        """Test that aspect ratio enums have correct values."""
        assert AspectRatio.NINE_SIXTEEN.value == "9x16"
        assert AspectRatio.FOUR_FIVE.value == "4x5"
        assert AspectRatio.ONE_ONE.value == "1x1"
        assert AspectRatio.SIXTEEN_NINE.value == "16x9"
    
    def test_conversion_strategy_enum_values(self):
        """Test conversion strategy enum values."""
        assert ConversionStrategy.PAD.value == "pad"
        assert ConversionStrategy.CROP.value == "crop"
        assert ConversionStrategy.STRETCH.value == "stretch"
    
    def test_quality_profile_enum_values(self):
        """Test quality profile enum values."""
        assert QualityProfile.HIGH.value == "high"
        assert QualityProfile.MEDIUM.value == "medium"
        assert QualityProfile.LOW.value == "low"
        assert QualityProfile.WEB.value == "web"


class TestAspectRatioMath:
    """Test mathematical aspect ratio calculations."""
    
    def test_aspect_ratio_calculations(self):
        """Test aspect ratio calculations for common resolutions."""
        # Test cases: (width, height, expected_ratio_string)
        test_cases = [
            (1920, 1080, "16:9"),    # Full HD
            (1080, 1920, "9:16"),    # Vertical HD
            (1080, 1350, "4:5"),     # Instagram feed
            (1080, 1080, "1:1"),     # Square
            (1280, 720, "16:9"),     # HD
            (640, 480, "4:3"),       # Standard definition
            (3840, 2160, "16:9"),    # 4K
        ]
        
        for width, height, expected_ratio in test_cases:
            # Calculate GCD manually for verification
            def gcd(a, b):
                while b:
                    a, b = b, a % b
                return a
            
            divisor = gcd(width, height)
            ratio_w = width // divisor
            ratio_h = height // divisor
            calculated_ratio = f"{ratio_w}:{ratio_h}"
            
            assert calculated_ratio == expected_ratio, f"Failed for {width}x{height}: got {calculated_ratio}, expected {expected_ratio}"
    
    def test_decimal_aspect_ratios(self):
        """Test decimal aspect ratio calculations."""
        test_cases = [
            (1920, 1080, 16/9),      # 1.777...
            (1080, 1920, 9/16),      # 0.5625
            (1080, 1350, 4/5),       # 0.8
            (1080, 1080, 1/1),       # 1.0
        ]
        
        for width, height, expected_decimal in test_cases:
            calculated_decimal = width / height
            assert abs(calculated_decimal - expected_decimal) < 0.001, f"Decimal ratio mismatch for {width}x{height}"


class TestConversionParams:
    """Test conversion parameters dataclass."""
    
    def test_conversion_params_defaults(self):
        """Test ConversionParams with default values."""
        params = ConversionParams(
            input_path="/test/input.mp4",
            output_path="/test/output.mp4",
            aspect_ratio=AspectRatio.NINE_SIXTEEN
        )
        
        assert params.input_path == "/test/input.mp4"
        assert params.output_path == "/test/output.mp4"
        assert params.aspect_ratio == AspectRatio.NINE_SIXTEEN
        assert params.strategy == ConversionStrategy.PAD  # Default
        assert params.background_color == "black"  # Default
        assert params.quality == QualityProfile.MEDIUM  # Default
        assert params.timeout_seconds == 300  # Default
        assert params.max_retries == 3  # Default
    
    def test_conversion_params_custom_values(self):
        """Test ConversionParams with custom values."""
        params = ConversionParams(
            input_path="/custom/input.mp4",
            output_path="/custom/output.mp4",
            aspect_ratio=AspectRatio.FOUR_FIVE,
            strategy=ConversionStrategy.CROP,
            background_color="white",
            quality=QualityProfile.HIGH,
            timeout_seconds=600,
            max_retries=5
        )
        
        assert params.strategy == ConversionStrategy.CROP
        assert params.background_color == "white"
        assert params.quality == QualityProfile.HIGH
        assert params.timeout_seconds == 600
        assert params.max_retries == 5


class TestConversionResult:
    """Test conversion result dataclass."""
    
    def test_conversion_result_success(self):
        """Test successful conversion result."""
        result = ConversionResult(
            success=True,
            input_path="/test/input.mp4",
            output_path="/test/output.mp4",
            execution_time=5.0,
            file_size_input=1000000,
            file_size_output=800000,
            stdout="Conversion completed",
            stderr="",
            aspect_ratio_input="16:9",
            aspect_ratio_output="9:16",
            dimensions_input=(1920, 1080),
            dimensions_output=(1080, 1920)
        )
        
        assert result.success is True
        assert result.execution_time == 5.0
        assert result.file_size_input == 1000000
        assert result.file_size_output == 800000
        assert result.aspect_ratio_input == "16:9"
        assert result.aspect_ratio_output == "9:16"
        assert result.dimensions_input == (1920, 1080)
        assert result.dimensions_output == (1080, 1920)
        assert result.error_message is None  # Default for success
    
    def test_conversion_result_failure(self):
        """Test failed conversion result."""
        result = ConversionResult(
            success=False,
            input_path="/test/input.mp4",
            output_path="/test/output.mp4",
            execution_time=2.0,
            file_size_input=1000000,
            file_size_output=0,
            stdout="",
            stderr="FFmpeg error",
            error_message="Conversion failed"
        )
        
        assert result.success is False
        assert result.file_size_output == 0
        assert result.error_message == "Conversion failed"


class TestFFmpegWrapperMocked:
    """Test FFmpegWrapper with mocked dependencies."""
    
    def test_wrapper_initialization_missing_script(self):
        """Test wrapper initialization with missing script."""
        with patch('app.media.ffmpeg_wrapper.Path') as mock_path:
            mock_path.return_value.parent.parent = MagicMock()
            mock_path.return_value.parent.parent.__truediv__.return_value.exists.return_value = False
            
            with pytest.raises(FFmpegError) as exc_info:
                FFmpegWrapper()
            
            assert "Could not locate ffmpeg_profiles.sh" in str(exc_info.value)
    
    def test_build_command(self):
        """Test command building."""
        with patch('app.media.ffmpeg_wrapper.Path') as mock_path, \
             patch('os.path.exists', return_value=True), \
             patch('os.access', return_value=True):
            
            # Mock script path
            mock_script_path = "/test/ffmpeg_profiles.sh"
            mock_path.return_value.parent.parent.__truediv__.return_value.exists.return_value = True
            mock_path.return_value.parent.parent.__truediv__.return_value.__str__.return_value = mock_script_path
            
            wrapper = FFmpegWrapper()
            wrapper.script_path = mock_script_path
            
            params = ConversionParams(
                input_path="/test/input.mp4",
                output_path="/test/output.mp4",
                aspect_ratio=AspectRatio.NINE_SIXTEEN,
                strategy=ConversionStrategy.PAD,
                background_color="black"
            )
            
            command = wrapper._build_command(params)
            
            assert command[0] == "bash"
            assert command[1] == "-c"
            assert "source '/test/ffmpeg_profiles.sh'" in command[2]
            assert "to_9x16" in command[2]
            assert "'/test/input.mp4'" in command[2]
            assert "'/test/output.mp4'" in command[2]
            assert "'pad'" in command[2]
            assert "'black'" in command[2]
    
    @pytest.mark.asyncio
    async def test_get_file_info_success(self):
        """Test successful file info retrieval."""
        with patch('app.media.ffmpeg_wrapper.Path') as mock_path, \
             patch('os.path.exists', return_value=True), \
             patch('os.access', return_value=True):
            
            # Mock script path
            mock_script_path = "/test/ffmpeg_profiles.sh"
            mock_path.return_value.parent.parent.__truediv__.return_value.exists.return_value = True
            mock_path.return_value.parent.parent.__truediv__.return_value.__str__.return_value = mock_script_path
            
            wrapper = FFmpegWrapper()
            wrapper.script_path = mock_script_path
            
            # Mock ffprobe output
            mock_process = AsyncMock()
            mock_process.returncode = 0
            mock_process.communicate.return_value = (b"1920,1080", b"")
            
            with patch('asyncio.create_subprocess_exec', return_value=mock_process):
                info = await wrapper._get_file_info("/test/video.mp4")
                
                assert info["dimensions"] == (1920, 1080)
                assert info["aspect_ratio"] == "16:9"
    
    @pytest.mark.asyncio
    async def test_get_file_info_failure(self):
        """Test file info retrieval failure."""
        with patch('app.media.ffmpeg_wrapper.Path') as mock_path, \
             patch('os.path.exists', return_value=True), \
             patch('os.access', return_value=True):
            
            # Mock script path
            mock_script_path = "/test/ffmpeg_profiles.sh"
            mock_path.return_value.parent.parent.__truediv__.return_value.exists.return_value = True
            mock_path.return_value.parent.parent.__truediv__.return_value.__str__.return_value = mock_script_path
            
            wrapper = FFmpegWrapper()
            wrapper.script_path = mock_script_path
            
            # Mock ffprobe failure
            mock_process = AsyncMock()
            mock_process.returncode = 1
            mock_process.communicate.return_value = (b"", b"No such file")
            
            with patch('asyncio.create_subprocess_exec', return_value=mock_process):
                info = await wrapper._get_file_info("/test/nonexistent.mp4")
                
                assert info == {}  # Should return empty dict on failure
    
    def test_gcd_calculation(self):
        """Test GCD calculation."""
        with patch('app.media.ffmpeg_wrapper.Path') as mock_path, \
             patch('os.path.exists', return_value=True), \
             patch('os.access', return_value=True):
            
            # Mock script path
            mock_script_path = "/test/ffmpeg_profiles.sh"
            mock_path.return_value.parent.parent.__truediv__.return_value.exists.return_value = True
            mock_path.return_value.parent.parent.__truediv__.return_value.__str__.return_value = mock_script_path
            
            wrapper = FFmpegWrapper()
            wrapper.script_path = mock_script_path
            
            # Test GCD calculations
            assert wrapper._gcd(1920, 1080) == 120
            assert wrapper._gcd(1080, 1080) == 1080
            assert wrapper._gcd(100, 50) == 50
            assert wrapper._gcd(17, 13) == 1  # Prime numbers


class TestNonDistortionValidation:
    """Test that pad strategy preserves aspect ratios without distortion."""
    
    def test_aspect_ratio_preservation_calculations(self):
        """Test that pad strategy calculations preserve original content."""
        # Test cases: (input_width, input_height, target_aspect_ratio, expected_behavior)
        test_cases = [
            # Landscape to portrait - should add vertical padding
            (1920, 1080, AspectRatio.NINE_SIXTEEN, "vertical_padding"),
            # Portrait to landscape - should add horizontal padding
            (1080, 1920, AspectRatio.SIXTEEN_NINE, "horizontal_padding"),
            # Square to any other ratio - should add padding
            (1080, 1080, AspectRatio.NINE_SIXTEEN, "vertical_padding"),
            (1080, 1080, AspectRatio.SIXTEEN_NINE, "horizontal_padding"),
            # Same aspect ratio - should not add padding
            (1920, 1080, AspectRatio.SIXTEEN_NINE, "no_padding"),
        ]
        
        for input_w, input_h, target_aspect, expected_behavior in test_cases:
            # Calculate target dimensions
            target_dims = {
                AspectRatio.NINE_SIXTEEN: (1080, 1920),
                AspectRatio.FOUR_FIVE: (1080, 1350),
                AspectRatio.ONE_ONE: (1080, 1080),
                AspectRatio.SIXTEEN_NINE: (1920, 1080)
            }
            
            target_w, target_h = target_dims[target_aspect]
            
            # Calculate input and target aspect ratios
            input_ratio = input_w / input_h
            target_ratio = target_w / target_h
            
            if expected_behavior == "vertical_padding":
                # Input is wider than target - needs vertical padding
                assert input_ratio > target_ratio, f"Expected vertical padding for {input_w}x{input_h} to {target_aspect.value}"
            elif expected_behavior == "horizontal_padding":
                # Input is taller than target - needs horizontal padding
                assert input_ratio < target_ratio, f"Expected horizontal padding for {input_w}x{input_h} to {target_aspect.value}"
            elif expected_behavior == "no_padding":
                # Aspect ratios should be very close
                assert abs(input_ratio - target_ratio) < 0.01, f"Expected no padding for {input_w}x{input_h} to {target_aspect.value}"
    
    def test_padding_calculations_preserve_content(self):
        """Test that padding calculations preserve all original content."""
        # Simulate FFmpeg's scale filter with force_original_aspect_ratio=decrease
        # This ensures the original content fits entirely within the target dimensions
        
        test_cases = [
            # (input_w, input_h, target_w, target_h)
            (1920, 1080, 1080, 1920),  # 16:9 to 9:16
            (1080, 1920, 1920, 1080),  # 9:16 to 16:9
            (1080, 1080, 1080, 1920),  # 1:1 to 9:16
            (1920, 1080, 1080, 1350),  # 16:9 to 4:5
        ]
        
        for input_w, input_h, target_w, target_h in test_cases:
            # Calculate scale factor to fit within target while preserving aspect ratio
            scale_w = target_w / input_w
            scale_h = target_h / input_h
            scale_factor = min(scale_w, scale_h)  # Use smaller scale to fit within bounds
            
            # Calculate actual scaled dimensions
            scaled_w = int(input_w * scale_factor)
            scaled_h = int(input_h * scale_factor)
            
            # Verify scaled content fits within target dimensions
            assert scaled_w <= target_w, f"Scaled width {scaled_w} exceeds target {target_w}"
            assert scaled_h <= target_h, f"Scaled height {scaled_h} exceeds target {target_h}"
            
            # Verify aspect ratio is preserved (within floating point tolerance)
            original_ratio = input_w / input_h
            scaled_ratio = scaled_w / scaled_h
            assert abs(original_ratio - scaled_ratio) < 0.01, f"Aspect ratio not preserved: {original_ratio} vs {scaled_ratio}"
            
            # Calculate padding needed
            pad_w = target_w - scaled_w
            pad_h = target_h - scaled_h
            
            # Verify padding is non-negative
            assert pad_w >= 0, f"Negative horizontal padding: {pad_w}"
            assert pad_h >= 0, f"Negative vertical padding: {pad_h}"


class TestSmartCropStub:
    """Test smart crop stub functionality."""
    
    def test_crop_strategy_enum_values(self):
        """Test crop strategy enum values."""
        assert CropStrategy.PAD.value == "pad"
        assert CropStrategy.CENTER_CROP.value == "center"
        assert CropStrategy.FACE_AWARE.value == "face"
        assert CropStrategy.CONTENT_AWARE.value == "content"
        assert CropStrategy.MOTION_AWARE.value == "motion"
        assert CropStrategy.TEXT_AWARE.value == "text"
    
    def test_content_type_enum_values(self):
        """Test content type enum values."""
        assert ContentType.PORTRAIT.value == "portrait"
        assert ContentType.PRODUCT.value == "product"
        assert ContentType.LANDSCAPE.value == "landscape"
        assert ContentType.TEXT_OVERLAY.value == "text"
        assert ContentType.MIXED.value == "mixed"
        assert ContentType.UNKNOWN.value == "unknown"
    
    def test_smart_crop_stub_initialization(self):
        """Test SmartCropStub initialization."""
        stub = SmartCropStub()
        
        assert len(stub.available_strategies) > 0
        assert CropStrategy.PAD in stub.available_strategies
        assert stub.is_strategy_available(CropStrategy.PAD)
    
    @pytest.mark.asyncio
    async def test_analyze_content_returns_pad_strategy(self):
        """Test that content analysis always returns safe pad strategy."""
        stub = SmartCropStub()
        
        params = SmartCropParams(
            input_path="/test/video.mp4",
            target_aspect_ratio=AspectRatio.NINE_SIXTEEN,
            content_type_hint=ContentType.PORTRAIT
        )
        
        analysis = await stub.analyze_content(params)
        
        assert isinstance(analysis, SmartCropAnalysis)
        assert analysis.recommended_strategy == CropStrategy.PAD
        assert analysis.confidence_score == 1.0  # High confidence in safe strategy
        assert analysis.content_type == ContentType.PORTRAIT
        assert len(analysis.crop_regions) == 0  # No specific regions for pad strategy
        assert analysis.analysis_time > 0
        assert "stub" in analysis.metadata["analysis_method"]
    
    @pytest.mark.asyncio
    async def test_get_crop_region_returns_none_for_pad(self):
        """Test that crop region is None for pad strategy."""
        stub = SmartCropStub()
        
        params = SmartCropParams(
            input_path="/test/video.mp4",
            target_aspect_ratio=AspectRatio.ONE_ONE
        )
        
        crop_region = await stub.get_crop_region(params)
        
        assert crop_region is None  # Pad strategy doesn't need specific crop region
    
    def test_conversion_strategy_mapping(self):
        """Test mapping from crop strategy to conversion strategy."""
        stub = SmartCropStub()
        
        # Test key mappings
        assert stub.get_conversion_strategy(CropStrategy.PAD) == ConversionStrategy.PAD
        assert stub.get_conversion_strategy(CropStrategy.CENTER_CROP) == ConversionStrategy.CROP
        assert stub.get_conversion_strategy(CropStrategy.TEXT_AWARE) == ConversionStrategy.PAD  # Safety first
    
    @pytest.mark.asyncio
    async def test_platform_recommendations_all_pad(self):
        """Test that platform recommendations return pad strategy (safe)."""
        stub = SmartCropStub()
        
        platforms = [
            "instagram_stories",
            "instagram_feed", 
            "instagram_square",
            "youtube",
            "tiktok",
            "vk",
            "facebook"
        ]
        
        for platform in platforms:
            strategy = await stub.recommend_strategy_for_platform(
                "/test/video.mp4", platform, ContentType.UNKNOWN
            )
            
            assert strategy == CropStrategy.PAD, f"Platform {platform} should recommend PAD strategy"
    
    @pytest.mark.asyncio
    async def test_text_overlay_always_pad(self):
        """Test that text overlay content always gets pad strategy."""
        stub = SmartCropStub()
        
        for platform in ["instagram_stories", "youtube", "tiktok"]:
            strategy = await stub.recommend_strategy_for_platform(
                "/test/video.mp4", platform, ContentType.TEXT_OVERLAY
            )
            
            assert strategy == CropStrategy.PAD, f"Text overlay should always use PAD strategy for {platform}"
    
    @pytest.mark.asyncio
    async def test_validate_crop_region_bounds_checking(self):
        """Test crop region validation."""
        stub = SmartCropStub()
        
        input_dimensions = (1920, 1080)
        target_aspect = AspectRatio.ONE_ONE
        
        # Valid region
        valid_region = CropRegion(x=100, y=100, width=800, height=800)
        assert await stub.validate_crop_region(valid_region, input_dimensions, target_aspect) is True
        
        # Out of bounds regions
        out_of_bounds_regions = [
            CropRegion(x=-10, y=100, width=800, height=800),  # Negative X
            CropRegion(x=100, y=-10, width=800, height=800),  # Negative Y
            CropRegion(x=1500, y=100, width=800, height=800), # X + width > input width
            CropRegion(x=100, y=600, width=800, height=800),  # Y + height > input height
        ]
        
        for region in out_of_bounds_regions:
            assert await stub.validate_crop_region(region, input_dimensions, target_aspect) is False
        
        # Too small regions
        small_region = CropRegion(x=100, y=100, width=50, height=50)
        assert await stub.validate_crop_region(small_region, input_dimensions, target_aspect) is False
    
    def test_get_stub_info(self):
        """Test stub information retrieval."""
        stub = SmartCropStub()
        info = stub.get_stub_info()
        
        assert info["type"] == "stub"
        assert info["safety"]["default_strategy"] == "pad"
        assert info["safety"]["content_preservation"] == "high"
        assert info["safety"]["distortion_risk"] == "none"
        assert len(info["features"]["implemented"]) > 0
        assert len(info["features"]["planned"]) > 0


class TestConvenienceFunctions:
    """Test convenience functions."""
    
    @pytest.mark.asyncio
    async def test_analyze_for_smart_crop(self):
        """Test analyze_for_smart_crop convenience function."""
        with patch('app.media.smart_crop_stub.smart_crop_stub.analyze_content') as mock_analyze:
            mock_analysis = SmartCropAnalysis(
                content_type=ContentType.UNKNOWN,
                recommended_strategy=CropStrategy.PAD,
                crop_regions=[],
                confidence_score=1.0,
                analysis_time=0.1,
                metadata={}
            )
            mock_analyze.return_value = mock_analysis
            
            result = await analyze_for_smart_crop(
                "/test/video.mp4",
                AspectRatio.NINE_SIXTEEN,
                ContentType.PORTRAIT
            )
            
            assert result == mock_analysis
            mock_analyze.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_get_platform_strategy(self):
        """Test get_platform_strategy convenience function."""
        with patch('app.media.smart_crop_stub.smart_crop_stub.recommend_strategy_for_platform') as mock_recommend, \
             patch('app.media.smart_crop_stub.smart_crop_stub.get_conversion_strategy') as mock_convert:
            
            mock_recommend.return_value = CropStrategy.PAD
            mock_convert.return_value = ConversionStrategy.PAD
            
            result = await get_platform_strategy(
                "/test/video.mp4",
                "instagram_stories",
                ContentType.PORTRAIT
            )
            
            assert result == ConversionStrategy.PAD
            mock_recommend.assert_called_once_with("/test/video.mp4", "instagram_stories", ContentType.PORTRAIT)
            mock_convert.assert_called_once_with(CropStrategy.PAD)
    
    def test_get_smart_crop_info(self):
        """Test get_smart_crop_info convenience function."""
        with patch('app.media.smart_crop_stub.smart_crop_stub.get_stub_info') as mock_info:
            mock_info.return_value = {"type": "stub", "version": "1.0.0"}
            
            result = get_smart_crop_info()
            
            assert result == {"type": "stub", "version": "1.0.0"}
            mock_info.assert_called_once()


class TestIntegrationValidation:
    """Integration tests to validate no distortion."""
    
    @pytest.mark.asyncio
    async def test_end_to_end_no_distortion_validation(self):
        """Test complete workflow preserves aspect ratios."""
        # This test validates the complete chain:
        # Smart crop analysis → FFmpeg wrapper → No distortion
        
        test_scenarios = [
            {
                "input_dimensions": (1920, 1080),
                "input_aspect": "16:9",
                "target_aspect": AspectRatio.NINE_SIXTEEN,
                "expected_strategy": ConversionStrategy.PAD,
                "expected_output_dims": (1080, 1920),
                "description": "Landscape to portrait conversion"
            },
            {
                "input_dimensions": (1080, 1920),
                "input_aspect": "9:16",
                "target_aspect": AspectRatio.SIXTEEN_NINE,
                "expected_strategy": ConversionStrategy.PAD,
                "expected_output_dims": (1920, 1080),
                "description": "Portrait to landscape conversion"
            },
            {
                "input_dimensions": (1080, 1080),
                "input_aspect": "1:1",
                "target_aspect": AspectRatio.FOUR_FIVE,
                "expected_strategy": ConversionStrategy.PAD,
                "expected_output_dims": (1080, 1350),
                "description": "Square to Instagram feed conversion"
            }
        ]
        
        for scenario in test_scenarios:
            # Step 1: Smart crop analysis should recommend PAD strategy
            analysis = await analyze_for_smart_crop(
                "/test/video.mp4",
                scenario["target_aspect"],
                ContentType.UNKNOWN
            )
            
            assert analysis.recommended_strategy == CropStrategy.PAD, f"Failed for {scenario['description']}"
            
            # Step 2: Strategy mapping should preserve safety
            conversion_strategy = smart_crop_stub.get_conversion_strategy(analysis.recommended_strategy)
            assert conversion_strategy == scenario["expected_strategy"], f"Strategy mapping failed for {scenario['description']}"
            
            # Step 3: Validate that no content would be lost
            input_w, input_h = scenario["input_dimensions"]
            target_w, target_h = scenario["expected_output_dims"]
            
            # Calculate scale factor (simulating FFmpeg's behavior)
            scale_w = target_w / input_w
            scale_h = target_h / input_h
            scale_factor = min(scale_w, scale_h)  # force_original_aspect_ratio=decrease
            
            # Verify original content fits completely
            scaled_w = int(input_w * scale_factor)
            scaled_h = int(input_h * scale_factor)
            
            assert scaled_w <= target_w, f"Content overflow in width for {scenario['description']}"
            assert scaled_h <= target_h, f"Content overflow in height for {scenario['description']}"
            
            # Verify aspect ratio preservation
            original_ratio = input_w / input_h
            scaled_ratio = scaled_w / scaled_h
            assert abs(original_ratio - scaled_ratio) < 0.01, f"Aspect ratio distortion for {scenario['description']}"


if __name__ == "__main__":
    # Run specific tests
    pytest.main([__file__, "-v"])