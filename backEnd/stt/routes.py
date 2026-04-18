"""
HTTP routes for STT-related features (transcript cleanup only).
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from stt.cleanup import clean_speech_transcript

router = APIRouter(prefix="/stt", tags=["stt"])


class STTCleanupRequest(BaseModel):
    transcript: str = Field(..., min_length=1, description="Raw text from browser STT")


class STTCleanupResponse(BaseModel):
    status: str = "success"
    cleaned_text: str


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
