"""
FFmpeg Wrapper for SalesWhisper Crosspost.

This module provides a Python wrapper around the FFmpeg profiles bash script,
handling process execution, timeouts, retries, and error handling.
"""

import asyncio
import os
import tempfile
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ..core.config import settings
from ..core.logging import get_logger, with_logging_context
from ..observability.metrics import metrics

logger = get_logger("media.ffmpeg_wrapper")


class AspectRatio(Enum):
    """Supported aspect ratios."""

    NINE_SIXTEEN = "9x16"  # Instagram Stories, TikTok
    FOUR_FIVE = "4x5"  # Instagram Feed
    ONE_ONE = "1x1"  # Instagram Square
    SIXTEEN_NINE = "16x9"  # YouTube, VK, Facebook


class ConversionStrategy(Enum):
    """Video conversion strategies."""

    PAD = "pad"  # Add padding (no distortion)
    CROP = "crop"  # Crop to fit (may lose content)
    STRETCH = "stretch"  # Stretch to fit (may distort)


class QualityProfile(Enum):
    """Quality profiles for conversion."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    WEB = "web"


@dataclass
class ConversionParams:
    """Parameters for video conversion."""

    input_path: str
    output_path: str
    aspect_ratio: AspectRatio
    strategy: ConversionStrategy = ConversionStrategy.PAD
    background_color: str = "black"
    quality: QualityProfile = QualityProfile.MEDIUM
    timeout_seconds: int = 300
    max_retries: int = 3


@dataclass
class ConversionResult:
    """Result of video conversion operation."""

    success: bool
    input_path: str
    output_path: str
    execution_time: float
    file_size_input: int
    file_size_output: int
    stdout: str
    stderr: str
    error_message: str | None = None
    aspect_ratio_input: str | None = None
    aspect_ratio_output: str | None = None
    dimensions_input: tuple[int, int] | None = None
    dimensions_output: tuple[int, int] | None = None


class FFmpegError(Exception):
    """Custom exception for FFmpeg operations."""

    pass


class FFmpegTimeoutError(FFmpegError):
    """Exception raised when FFmpeg operation times out."""

    pass


class FFmpegWrapper:
    """Python wrapper for FFmpeg profile bash script."""

    def __init__(self):
        """Initialize FFmpeg wrapper."""
        self.temp_dir = tempfile.gettempdir()
        self.script_path = None

        try:
            self.script_path = self._get_script_path()

            # Validate script exists and is executable
            if self.script_path and not os.path.exists(self.script_path):
                logger.warning(f"FFmpeg profiles script not found: {self.script_path}")
                self.script_path = None

            if self.script_path and not os.access(self.script_path, os.X_OK):
                # Try to make it executable
                try:
                    os.chmod(self.script_path, 0o755)
                except Exception as e:
                    logger.warning(f"FFmpeg script is not executable: {e}")

            if self.script_path:
                logger.info("FFmpegWrapper initialized", script_path=self.script_path)
            else:
                logger.info("FFmpegWrapper initialized without profile script (using direct ffmpeg)")
        except FFmpegError:
            logger.info("FFmpegWrapper initialized without profile script (using direct ffmpeg)")
            self.script_path = None

    def _get_script_path(self) -> str | None:
        """Get path to FFmpeg profiles script."""
        # Try to find script relative to project root
        current_dir = Path(__file__).parent
        project_root = current_dir.parent.parent
        script_path = project_root / "helpers" / "ffmpeg_profiles.sh"

        if script_path.exists():
            return str(script_path)

        # Fallback to configured path if available
        if hasattr(settings, "ffmpeg_script_path") and settings.ffmpeg_script_path:
            if Path(settings.ffmpeg_script_path).exists():
                return settings.ffmpeg_script_path

        # Return None instead of raising - we can work without the script
        return None

    async def convert_aspect_ratio(self, params: ConversionParams) -> ConversionResult:
        """
        Convert video to target aspect ratio.

        Args:
            params: Conversion parameters

        Returns:
            Conversion result with detailed information
        """
        start_time = time.time()
        correlation_id = f"ffmpeg_{int(start_time)}"

        with with_logging_context(correlation_id=correlation_id):
            logger.info(
                "Starting video conversion",
                input_path=params.input_path,
                output_path=params.output_path,
                aspect_ratio=params.aspect_ratio.value,
                strategy=params.strategy.value,
            )

            # Validate input file
            if not os.path.exists(params.input_path):
                raise FFmpegError(f"Input file does not exist: {params.input_path}")

            # Get input file information
            input_info = await self._get_file_info(params.input_path)
            input_size = os.path.getsize(params.input_path)

            # Create output directory if needed
            output_dir = os.path.dirname(params.output_path)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)

            # Execute conversion with retries
            result = await self._execute_with_retries(params, input_info, input_size)

            # Calculate total execution time
            total_time = time.time() - start_time
            result.execution_time = total_time

            # Track metrics
            if result.success:
                metrics.track_media_processed(
                    media_type="video",
                    platform="ffmpeg",
                    success=True,
                    duration=total_time,
                    file_size=result.file_size_output,
                )

                logger.info(
                    "Video conversion completed successfully",
                    input_path=params.input_path,
                    output_path=params.output_path,
                    execution_time=total_time,
                    compression_ratio=result.file_size_output / result.file_size_input,
                )
            else:
                metrics.track_media_failed("video", "ffmpeg", "conversion_error")

                logger.error(
                    "Video conversion failed",
                    input_path=params.input_path,
                    error=result.error_message,
                    execution_time=total_time,
                )

            return result

    async def _execute_with_retries(
        self, params: ConversionParams, input_info: dict[str, Any], input_size: int
    ) -> ConversionResult:
        """Execute conversion with retry logic."""
        last_error = None

        for attempt in range(params.max_retries):
            try:
                logger.info(f"Conversion attempt {attempt + 1}/{params.max_retries}")

                result = await self._execute_conversion(params, input_info, input_size)

                if result.success:
                    return result
                else:
                    last_error = result.error_message
                    if attempt < params.max_retries - 1:
                        # Wait before retry (exponential backoff)
                        wait_time = 2**attempt
                        logger.warning(f"Conversion failed, retrying in {wait_time}s", error=last_error)
                        await asyncio.sleep(wait_time)

            except Exception as e:
                last_error = str(e)
                logger.warning(f"Conversion attempt {attempt + 1} failed", error=last_error)

                if attempt < params.max_retries - 1:
                    wait_time = 2**attempt
                    await asyncio.sleep(wait_time)

        # All retries failed
        return ConversionResult(
            success=False,
            input_path=params.input_path,
            output_path=params.output_path,
            execution_time=0.0,
            file_size_input=input_size,
            file_size_output=0,
            stdout="",
            stderr="",
            error_message=f"All {params.max_retries} conversion attempts failed. Last error: {last_error}",
            **input_info,
        )

    async def _execute_conversion(
        self, params: ConversionParams, input_info: dict[str, Any], input_size: int
    ) -> ConversionResult:
        """Execute single conversion attempt."""
        # Build command
        command = self._build_command(params)

        logger.debug("Executing FFmpeg command", command=" ".join(command))

        try:
            # Execute command with timeout
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=os.path.dirname(self.script_path),
            )

            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=params.timeout_seconds)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise FFmpegTimeoutError(f"Conversion timed out after {params.timeout_seconds}s")

            stdout_str = stdout.decode("utf-8", errors="replace")
            stderr_str = stderr.decode("utf-8", errors="replace")

            # Check if conversion was successful
            success = process.returncode == 0 and os.path.exists(params.output_path)

            # Get output file information if successful
            output_info = {}
            output_size = 0

            if success:
                try:
                    output_info = await self._get_file_info(params.output_path)
                    output_size = os.path.getsize(params.output_path)
                except Exception as e:
                    logger.warning(f"Failed to get output file info: {e}")

            error_message = None
            if not success:
                if process.returncode != 0:
                    error_message = f"FFmpeg failed with exit code {process.returncode}: {stderr_str}"
                elif not os.path.exists(params.output_path):
                    error_message = f"Output file was not created: {params.output_path}"

            return ConversionResult(
                success=success,
                input_path=params.input_path,
                output_path=params.output_path,
                execution_time=0.0,  # Will be set by caller
                file_size_input=input_size,
                file_size_output=output_size,
                stdout=stdout_str,
                stderr=stderr_str,
                error_message=error_message,
                aspect_ratio_input=input_info.get("aspect_ratio"),
                aspect_ratio_output=output_info.get("aspect_ratio"),
                dimensions_input=input_info.get("dimensions"),
                dimensions_output=output_info.get("dimensions"),
            )

        except FFmpegTimeoutError:
            raise
        except Exception as e:
            return ConversionResult(
                success=False,
                input_path=params.input_path,
                output_path=params.output_path,
                execution_time=0.0,
                file_size_input=input_size,
                file_size_output=0,
                stdout="",
                stderr="",
                error_message=f"Command execution failed: {str(e)}",
                **input_info,
            )

    def _build_command(self, params: ConversionParams) -> list[str]:
        """Build bash command for conversion."""
        # Source the script and call the appropriate function
        function_name = f"to_{params.aspect_ratio.value.replace('x', 'x')}"

        command = [
            "bash",
            "-c",
            f"source '{self.script_path}' && {function_name} '{params.input_path}' '{params.output_path}' '{params.strategy.value}' '{params.background_color}'",
        ]

        return command

    async def _get_file_info(self, file_path: str) -> dict[str, Any]:
        """Get video file information using ffprobe."""
        try:
            # Get dimensions
            cmd = [
                "ffprobe",
                "-v",
                "quiet",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=p=0",
                file_path,
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                dimensions_str = stdout.decode().strip()
                if "," in dimensions_str:
                    width, height = map(int, dimensions_str.split(","))

                    # Calculate aspect ratio
                    gcd = self._gcd(width, height)
                    ratio_w = width // gcd
                    ratio_h = height // gcd

                    return {"dimensions": (width, height), "aspect_ratio": f"{ratio_w}:{ratio_h}"}

            logger.warning(f"Failed to get file info for {file_path}: {stderr.decode()}")
            return {}

        except Exception as e:
            logger.warning(f"Error getting file info for {file_path}: {e}")
            return {}

    def _gcd(self, a: int, b: int) -> int:
        """Calculate greatest common divisor."""
        while b:
            a, b = b, a % b
        return a

    async def get_aspect_info(self, file_path: str) -> dict[str, Any]:
        """Get detailed aspect ratio information about a file."""
        try:
            # Use bash script's get_aspect_info function
            command = ["bash", "-c", f"source '{self.script_path}' && get_aspect_info '{file_path}'"]

            process = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                output = stdout.decode("utf-8").strip()

                # Parse output
                info = {}
                for line in output.split("\n"):
                    if ":" in line:
                        key, value = line.split(":", 1)
                        info[key.strip()] = value.strip()

                return info
            else:
                logger.error(f"Failed to get aspect info: {stderr.decode()}")
                return {}

        except Exception as e:
            logger.error(f"Error getting aspect info: {e}")
            return {}

    async def batch_convert(
        self,
        input_dir: str,
        output_dir: str,
        aspect_ratio: AspectRatio,
        strategy: ConversionStrategy = ConversionStrategy.PAD,
        quality: QualityProfile = QualityProfile.MEDIUM,
    ) -> list[ConversionResult]:
        """
        Batch convert multiple files.

        Args:
            input_dir: Directory containing input files
            output_dir: Directory for output files
            aspect_ratio: Target aspect ratio
            strategy: Conversion strategy
            quality: Quality profile

        Returns:
            List of conversion results
        """
        if not os.path.isdir(input_dir):
            raise FFmpegError(f"Input directory does not exist: {input_dir}")

        os.makedirs(output_dir, exist_ok=True)

        # Find video files
        video_extensions = [".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"]
        input_files = []

        for ext in video_extensions:
            pattern = os.path.join(input_dir, f"*{ext}")
            import glob

            input_files.extend(glob.glob(pattern))

        logger.info(f"Found {len(input_files)} video files for batch conversion")

        results = []

        for input_file in input_files:
            try:
                # Generate output filename
                basename = os.path.splitext(os.path.basename(input_file))[0]
                output_file = os.path.join(output_dir, f"{basename}_{aspect_ratio.value}.mp4")

                # Create conversion parameters
                params = ConversionParams(
                    input_path=input_file,
                    output_path=output_file,
                    aspect_ratio=aspect_ratio,
                    strategy=strategy,
                    quality=quality,
                )

                # Convert file
                result = await self.convert_aspect_ratio(params)
                results.append(result)

            except Exception as e:
                logger.error(f"Failed to convert {input_file}: {e}")
                results.append(
                    ConversionResult(
                        success=False,
                        input_path=input_file,
                        output_path="",
                        execution_time=0.0,
                        file_size_input=0,
                        file_size_output=0,
                        stdout="",
                        stderr="",
                        error_message=str(e),
                    )
                )

        # Log batch results
        success_count = sum(1 for r in results if r.success)
        failed_count = len(results) - success_count

        logger.info(
            "Batch conversion completed", total_files=len(results), successful=success_count, failed=failed_count
        )

        return results


# Global wrapper instance
ffmpeg_wrapper = FFmpegWrapper()


# Convenience functions
async def convert_to_aspect_ratio(
    input_path: str,
    output_path: str,
    aspect_ratio: AspectRatio,
    strategy: ConversionStrategy = ConversionStrategy.PAD,
    background_color: str = "black",
    quality: QualityProfile = QualityProfile.MEDIUM,
    timeout_seconds: int = 300,
) -> ConversionResult:
    """
    Convert video to specified aspect ratio.

    Args:
        input_path: Path to input video file
        output_path: Path for output video file
        aspect_ratio: Target aspect ratio
        strategy: Conversion strategy (pad, crop, stretch)
        background_color: Background color for padding
        quality: Quality profile
        timeout_seconds: Timeout for conversion

    Returns:
        Conversion result
    """
    params = ConversionParams(
        input_path=input_path,
        output_path=output_path,
        aspect_ratio=aspect_ratio,
        strategy=strategy,
        background_color=background_color,
        quality=quality,
        timeout_seconds=timeout_seconds,
    )

    return await ffmpeg_wrapper.convert_aspect_ratio(params)


async def convert_for_platform(
    input_path: str, output_dir: str, platform: str, strategy: ConversionStrategy = ConversionStrategy.PAD
) -> ConversionResult:
    """
    Convert video for specific social media platform.

    Args:
        input_path: Path to input video
        output_dir: Directory for output files
        platform: Target platform (instagram_stories, instagram_feed, instagram_square, youtube, vk, tiktok)
        strategy: Conversion strategy

    Returns:
        Conversion result
    """
    # Platform to aspect ratio mapping
    platform_ratios = {
        "instagram_stories": AspectRatio.NINE_SIXTEEN,
        "instagram_feed": AspectRatio.FOUR_FIVE,
        "instagram_square": AspectRatio.ONE_ONE,
        "youtube": AspectRatio.SIXTEEN_NINE,
        "vk": AspectRatio.SIXTEEN_NINE,
        "tiktok": AspectRatio.NINE_SIXTEEN,
        "facebook": AspectRatio.SIXTEEN_NINE,
    }

    if platform not in platform_ratios:
        raise FFmpegError(f"Unknown platform: {platform}")

    aspect_ratio = platform_ratios[platform]

    # Generate output filename
    basename = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(output_dir, f"{basename}_{platform}.mp4")

    return await convert_to_aspect_ratio(
        input_path=input_path, output_path=output_path, aspect_ratio=aspect_ratio, strategy=strategy
    )


async def get_video_info(file_path: str) -> dict[str, Any]:
    """Get video file information."""
    return await ffmpeg_wrapper.get_aspect_info(file_path)
