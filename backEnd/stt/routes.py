"""
HTTP routes for STT-related features (transcript cleanup only).
"""

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from stt.cleanup import clean_speech_transcript
from stt.transcribe import transcribe_audio_bytes

router = APIRouter(prefix="/stt", tags=["stt"])


class STTCleanupRequest(BaseModel):
    transcript: str = Field(..., min_length=1, description="Raw text from browser STT")


class STTCleanupResponse(BaseModel):
    status: str = "success"
    cleaned_text: str


class STTTranscribeResponse(BaseModel):
    status: str = "success"
    transcript: str
    provider: str = "gemini-audio-transcribe"


@router.post("/cleanup", response_model=STTCleanupResponse)
async def post_cleanup_transcript(request: STTCleanupRequest):
    """Normalize a raw speech transcript for the command input (does not run MAS)."""
    try:
        cleaned = clean_speech_transcript(request.transcript)
    except Exception as e:
        print(f"STT cleanup error: {e}")
        raise HTTPException(
            status_code=500,
            detail="Transcript cleanup failed",
        ) from e

    if not (cleaned or "").strip():
        raise HTTPException(
            status_code=422,
            detail="Cleanup produced empty text; try speaking again",
        )

    return STTCleanupResponse(cleaned_text=cleaned.strip())


@router.post("/transcribe", response_model=STTTranscribeResponse)
async def post_transcribe_audio(audio: UploadFile = File(...)):

    """
    Transcribe uploaded audio from browser microphone capture.
    Designed as fallback for clients without Web Speech API support.
    """
    try:
        raw = await audio.read()
    except Exception as e:
        print(f"STT transcribe read error: {e}")
        raise HTTPException(status_code=400, detail="Could not read uploaded audio") from e

    if not raw:
        raise HTTPException(status_code=422, detail="Audio upload is empty")

    # Keep payload bounded for browser fallback capture.
    max_bytes = 10 * 1024 * 1024
    
    if len(raw) > max_bytes:
        raise HTTPException(status_code=413, detail="Audio file too large")

    try:
        transcript = transcribe_audio_bytes(
            audio_bytes=raw,
            mime_type=audio.content_type,
            filename=audio.filename or "",
        )
    except Exception as e:
        print(f"STT transcribe error: {e}")
        raise HTTPException(status_code=500, detail="Audio transcription failed") from e

    if not (transcript or "").strip():
        raise HTTPException(
            status_code=422,
            detail="Transcription produced empty text; please try again",
        )

    return STTTranscribeResponse(transcript=transcript.strip())
