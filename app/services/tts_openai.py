"""OpenAI Text-to-Speech service for Crosspost.

Supports OpenAI TTS-1 and TTS-1-HD for voice synthesis.
"""

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..core.logging import get_logger

logger = get_logger("services.tts_openai")


class TTSError(Exception):
    """Base exception for TTS API errors."""
    pass


class TTSRateLimitError(TTSError):
    """Rate limit exceeded."""
    pass


class TTSGenerationError(TTSError):
    """Speech generation failed."""
    pass


class TTSAuthError(TTSError):
    """Authentication failed."""
    pass


class TTSModel(str, Enum):
    """OpenAI TTS models."""
    TTS_1 = "tts-1"         # Standard quality, faster
    TTS_1_HD = "tts-1-hd"   # High definition, slower


class TTSVoice(str, Enum):
    """OpenAI TTS voices."""
    ALLOY = "alloy"       # Neutral
    ECHO = "echo"         # Male
    FABLE = "fable"       # British
    ONYX = "onyx"         # Deep male
    NOVA = "nova"         # Female
    SHIMMER = "shimmer"   # Female, warm


class AudioFormat(str, Enum):
    """Audio output formats."""
    MP3 = "mp3"
    OPUS = "opus"
    AAC = "aac"
    FLAC = "flac"
    WAV = "wav"
    PCM = "pcm"


@dataclass
class TTSResult:
    """Result of TTS generation."""
    success: bool
    audio_data: bytes | None = None
    audio_url: str | None = None
    file_path: str | None = None
    duration_seconds: float = 0
    character_count: int = 0
    error: str | None = None
    cost_estimate: float = 0.0  # $0.015 per 1K chars for tts-1, $0.030 for tts-1-hd


class OpenAITTSService:
    """OpenAI Text-to-Speech service."""

    API_BASE = "https://api.openai.com/v1"

    # Cost per 1000 characters
    COST_PER_1K_TTS1 = 0.015
    COST_PER_1K_TTS1_HD = 0.030

    # Max characters per request
    MAX_CHARS = 4096

    def __init__(self, api_key: str = None):
        """Initialize OpenAI TTS service."""
        self.api_key = api_key or self._get_api_key()

        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
        )

        logger.info("OpenAI TTS service initialized")

    def _get_api_key(self) -> str:
        """Get OpenAI API key from settings or environment."""
        return os.getenv('OPENAI_API_KEY', '')

    def _calculate_cost(self, text: str, model: TTSModel) -> float:
        """Calculate estimated cost for TTS generation."""
        char_count = len(text)
        cost_per_1k = self.COST_PER_1K_TTS1_HD if model == TTSModel.TTS_1_HD else self.COST_PER_1K_TTS1
        return (char_count / 1000) * cost_per_1k

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((httpx.TimeoutException, TTSRateLimitError))
    )
    async def generate_speech(
        self,
        text: str,
        voice: TTSVoice = TTSVoice.ALLOY,
        model: TTSModel = TTSModel.TTS_1,
        response_format: AudioFormat = AudioFormat.MP3,
        speed: float = 1.0
    ) -> TTSResult:
        """Generate speech from text.

        Args:
            text: Text to convert to speech (max 4096 chars)
            voice: Voice to use
            model: TTS model (tts-1 or tts-1-hd)
            response_format: Audio format (mp3, opus, aac, flac, wav, pcm)
            speed: Speaking speed (0.25 to 4.0)

        Returns:
            TTSResult with audio data
        """
        if len(text) > self.MAX_CHARS:
            return TTSResult(
                success=False,
                error=f"Text too long: {len(text)} chars (max {self.MAX_CHARS})"
            )

        if not text.strip():
            return TTSResult(
                success=False,
                error="Empty text provided"
            )

        # Clamp speed to valid range
        speed = max(0.25, min(4.0, speed))

        logger.info(
            "Starting TTS generation",
            voice=voice.value,
            model=model.value,
            chars=len(text)
        )

        payload = {
            "model": model.value,
            "input": text,
            "voice": voice.value,
            "response_format": response_format.value,
            "speed": speed
        }

        try:
            response = await self.http_client.post(
                f"{self.API_BASE}/audio/speech",
                json=payload
            )

            if response.status_code == 401:
                raise TTSAuthError("Invalid API credentials")
            elif response.status_code == 429:
                raise TTSRateLimitError("Rate limit exceeded")
            elif response.status_code != 200:
                error_data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                error_msg = error_data.get("error", {}).get("message", f"API error: {response.status_code}")
                raise TTSGenerationError(error_msg)

            audio_data = response.content
            cost = self._calculate_cost(text, model)

            logger.info(
                "TTS generation completed",
                size_bytes=len(audio_data),
                cost=cost
            )

            return TTSResult(
                success=True,
                audio_data=audio_data,
                character_count=len(text),
                cost_estimate=cost
            )

        except httpx.TimeoutException:
            logger.error("OpenAI TTS API timeout")
            raise
        except (TTSAuthError, TTSRateLimitError, TTSGenerationError):
            raise
        except Exception as e:
            logger.error(f"TTS generation error: {e}")
            return TTSResult(
                success=False,
                error=str(e)
            )

    async def generate_speech_to_file(
        self,
        text: str,
        output_path: str = None,
        voice: TTSVoice = TTSVoice.ALLOY,
        model: TTSModel = TTSModel.TTS_1,
        response_format: AudioFormat = AudioFormat.MP3,
        speed: float = 1.0
    ) -> TTSResult:
        """Generate speech and save to file.

        Args:
            text: Text to convert to speech
            output_path: Path to save audio file (optional, auto-generated if not provided)
            voice: Voice to use
            model: TTS model
            response_format: Audio format
            speed: Speaking speed

        Returns:
            TTSResult with file path
        """
        result = await self.generate_speech(
            text=text,
            voice=voice,
            model=model,
            response_format=response_format,
            speed=speed
        )

        if not result.success:
            return result

        # Generate output path if not provided
        if not output_path:
            ext = response_format.value
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = f"/tmp/tts_{timestamp}.{ext}"

        # Save to file
        try:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(result.audio_data)

            result.file_path = output_path
            logger.info(f"Audio saved to {output_path}")

        except Exception as e:
            logger.error(f"Failed to save audio file: {e}")
            result.success = False
            result.error = f"Failed to save file: {e}"

        return result

    async def generate_long_speech(
        self,
        text: str,
        voice: TTSVoice = TTSVoice.ALLOY,
        model: TTSModel = TTSModel.TTS_1,
        response_format: AudioFormat = AudioFormat.MP3,
        speed: float = 1.0
    ) -> TTSResult:
        """Generate speech for text longer than 4096 characters.

        Splits text into chunks and concatenates audio.

        Args:
            text: Long text to convert to speech
            voice: Voice to use
            model: TTS model
            response_format: Audio format
            speed: Speaking speed

        Returns:
            TTSResult with concatenated audio data
        """
        if len(text) <= self.MAX_CHARS:
            return await self.generate_speech(
                text=text,
                voice=voice,
                model=model,
                response_format=response_format,
                speed=speed
            )

        # Split text into chunks at sentence boundaries
        chunks = self._split_text(text)
        audio_parts = []
        total_cost = 0.0

        logger.info(f"Generating long speech in {len(chunks)} chunks")

        for i, chunk in enumerate(chunks):
            result = await self.generate_speech(
                text=chunk,
                voice=voice,
                model=model,
                response_format=response_format,
                speed=speed
            )

            if not result.success:
                return TTSResult(
                    success=False,
                    error=f"Failed at chunk {i+1}: {result.error}"
                )

            audio_parts.append(result.audio_data)
            total_cost += result.cost_estimate

            # Small delay between chunks
            await asyncio.sleep(0.5)

        # Concatenate audio (simple concatenation works for MP3/AAC)
        combined_audio = b"".join(audio_parts)

        return TTSResult(
            success=True,
            audio_data=combined_audio,
            character_count=len(text),
            cost_estimate=total_cost
        )

    def _split_text(self, text: str, max_chunk: int = 4000) -> list[str]:
        """Split text into chunks at sentence boundaries."""
        sentences = []
        current = ""

        # Split by common sentence endings
        for char in text:
            current += char
            if char in ".!?" and len(current) > 50:
                sentences.append(current.strip())
                current = ""

        if current.strip():
            sentences.append(current.strip())

        # Combine sentences into chunks
        chunks = []
        current_chunk = ""

        for sentence in sentences:
            if len(current_chunk) + len(sentence) < max_chunk:
                current_chunk += " " + sentence if current_chunk else sentence
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = sentence

        if current_chunk:
            chunks.append(current_chunk)

        return chunks if chunks else [text[:max_chunk]]

    async def list_voices(self) -> list[dict[str, str]]:
        """List available voices with descriptions."""
        return [
            {"id": "alloy", "name": "Alloy", "description": "Neutral, balanced voice"},
            {"id": "echo", "name": "Echo", "description": "Male voice"},
            {"id": "fable", "name": "Fable", "description": "British accent"},
            {"id": "onyx", "name": "Onyx", "description": "Deep male voice"},
            {"id": "nova", "name": "Nova", "description": "Female voice"},
            {"id": "shimmer", "name": "Shimmer", "description": "Warm female voice"},
        ]

    async def close(self):
        """Close HTTP client."""
        if self.http_client:
            await self.http_client.aclose()


# Singleton instance
_tts_service: OpenAITTSService | None = None


def get_tts_service() -> OpenAITTSService:
    """Get or create TTS service instance."""
    global _tts_service
    if _tts_service is None:
        _tts_service = OpenAITTSService()
    return _tts_service
