"""Text-to-Speech API routes."""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..models.entities import User
from ..services.tts_openai import AudioFormat, OpenAITTSService, TTSModel, TTSVoice
from .deps import get_current_user

router = APIRouter(prefix="/tts", tags=["text-to-speech"])

VALID_VOICES = ("alloy", "echo", "fable", "onyx", "nova", "shimmer")
VALID_MODELS = ("tts-1", "tts-1-hd")
VALID_FORMATS = ("mp3", "opus", "aac", "flac", "wav", "pcm")
CONTENT_TYPES = {
    "mp3": "audio/mpeg",
    "opus": "audio/opus",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "wav": "audio/wav",
    "pcm": "audio/L16",
}


# Request/Response models
class TTSRequest(BaseModel):
    """Request for text-to-speech generation."""

    text: str = Field(..., min_length=1, max_length=4096, description="Text to convert to speech")
    voice: str = Field("alloy", description="Voice: alloy, echo, fable, onyx, nova, shimmer")
    model: str = Field("tts-1", description="Model: tts-1 (fast) or tts-1-hd (quality)")
    format: str = Field("mp3", description="Format: mp3, opus, aac, flac, wav")
    speed: float = Field(1.0, ge=0.25, le=4.0, description="Speed: 0.25 to 4.0")


class LongTTSRequest(BaseModel):
    """Request for long text-to-speech generation."""

    text: str = Field(..., min_length=1, description="Long text to convert to speech")
    voice: str = Field("alloy", description="Voice: alloy, echo, fable, onyx, nova, shimmer")
    model: str = Field("tts-1", description="Model: tts-1 or tts-1-hd")
    format: str = Field("mp3", description="Format: mp3, opus, aac, flac, wav")
    speed: float = Field(1.0, ge=0.25, le=4.0, description="Speed: 0.25 to 4.0")


class TTSResponse(BaseModel):
    """TTS generation response metadata."""

    success: bool
    character_count: int
    cost_estimate: float
    error: str | None = None


def _validate_tts_request(voice: str, model: str, audio_format: str) -> None:
    if voice not in VALID_VOICES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid voice. Valid options: {list(VALID_VOICES)}"
        )
    if model not in VALID_MODELS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid model. Valid options: {list(VALID_MODELS)}"
        )
    if audio_format not in VALID_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid format. Valid options: {list(VALID_FORMATS)}"
        )


def _build_audio_response(result, audio_format: str) -> Response:
    return Response(
        content=result.audio_data,
        media_type=CONTENT_TYPES.get(audio_format, "audio/mpeg"),
        headers={"X-Character-Count": str(result.character_count), "X-Cost-Estimate": str(result.cost_estimate)},
    )


async def _generate_tts_response(
    request: TTSRequest | LongTTSRequest, *, long_text: bool = False
) -> Response:
    _validate_tts_request(request.voice, request.model, request.format)

    service = OpenAITTSService()
    try:
        if long_text:
            result = await service.generate_long_speech(
                text=request.text,
                voice=TTSVoice(request.voice),
                model=TTSModel(request.model),
                response_format=AudioFormat(request.format),
                speed=request.speed,
            )
        else:
            result = await service.generate_speech(
                text=request.text,
                voice=TTSVoice(request.voice),
                model=TTSModel(request.model),
                response_format=AudioFormat(request.format),
                speed=request.speed,
            )

        if not result.success:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=result.error)
        return _build_audio_response(result, request.format)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    finally:
        await service.close()


@router.post("/generate", response_class=Response)
async def generate_speech(request: TTSRequest, current_user: User = Depends(get_current_user)):
    """Generate speech from text. Returns audio file directly."""
    return await _generate_tts_response(request)


@router.post("/generate-long", response_class=Response)
async def generate_long_speech(request: LongTTSRequest, current_user: User = Depends(get_current_user)):
    """Generate speech from long text (no 4096 char limit). Returns audio file."""
    return await _generate_tts_response(request, long_text=True)


@router.get("/voices")
async def list_voices():
    """Get available TTS voices."""
    return {
        "voices": [
            {"id": "alloy", "name": "Alloy", "description": "Нейтральный, сбалансированный голос", "gender": "neutral"},
            {"id": "echo", "name": "Echo", "description": "Мужской голос", "gender": "male"},
            {"id": "fable", "name": "Fable", "description": "Британский акцент", "gender": "neutral"},
            {"id": "onyx", "name": "Onyx", "description": "Глубокий мужской голос", "gender": "male"},
            {"id": "nova", "name": "Nova", "description": "Женский голос", "gender": "female"},
            {"id": "shimmer", "name": "Shimmer", "description": "Тёплый женский голос", "gender": "female"},
        ]
    }


@router.get("/models")
async def list_models():
    """Get available TTS models."""
    return {
        "models": [
            {
                "id": "tts-1",
                "name": "TTS-1",
                "description": "Стандартное качество, быстрая генерация",
                "cost_per_1k_chars": 0.015,
                "recommended": True,
            },
            {
                "id": "tts-1-hd",
                "name": "TTS-1 HD",
                "description": "Высокое качество, медленнее",
                "cost_per_1k_chars": 0.030,
                "recommended": False,
            },
        ]
    }


@router.get("/formats")
async def list_formats():
    """Get available audio formats."""
    return {
        "formats": [
            {"id": "mp3", "name": "MP3", "description": "Универсальный формат", "recommended": True},
            {"id": "opus", "name": "Opus", "description": "Эффективное сжатие, низкая задержка"},
            {"id": "aac", "name": "AAC", "description": "Хорошее качество при малом размере"},
            {"id": "flac", "name": "FLAC", "description": "Без потерь качества"},
            {"id": "wav", "name": "WAV", "description": "Несжатый формат"},
            {"id": "pcm", "name": "PCM", "description": "Сырые аудиоданные"},
        ]
    }
