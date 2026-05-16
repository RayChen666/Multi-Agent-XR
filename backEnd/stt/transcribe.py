"""
Audio transcription helper for browser-uploaded microphone recordings.

Used by /stt/transcribe route so WebXR clients without Web Speech API support
(e.g. Meta Quest Browser) can still produce command text.
"""

import base64
import os
import re
from pathlib import Path
from typing import Optional

import google.generativeai as genai
from dotenv import load_dotenv


_MODEL = None


def _get_model():

    """
    Get the Gemini model.
    """
    global _MODEL
    if _MODEL is not None:
        return _MODEL

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    _MODEL = genai.GenerativeModel("gemini-2.5-flash")
    return _MODEL


def _normalize_mime_type(mime_type: Optional[str], filename: str = "") -> str:
    """
    Normalize MIME type for Gemini.
    """

    mt = (mime_type or "").strip().lower()
    if mt:
        # Keep the base type only; Gemini accepts generic audio/* types.
        return mt.split(";")[0].strip()

    name = (filename or "").lower()
    if name.endswith(".webm"):
        return "audio/webm"

    if name.endswith(".ogg") or name.endswith(".opus"):
        return "audio/ogg"

    if name.endswith(".wav"):
        return "audio/wav"

    if name.endswith(".mp3"):
        return "audio/mpeg"

    if name.endswith(".m4a") or name.endswith(".mp4"):
        return "audio/mp4"

    return "audio/webm"


def transcribe_audio_bytes(audio_bytes: bytes, mime_type: Optional[str], filename: str = "") -> str:
    """
    Transcribe uploaded audio into one command line.
    """
    if not audio_bytes:
        return ""

    model = _get_model()
    normalized_mime = _normalize_mime_type(mime_type, filename)

    prompt = (
        "Transcribe this microphone audio to plain text for a 3D scene command assistant. "
        "Return only the spoken transcript. No markdown, no quotes, no explanations."
    )

    response = model.generate_content(
        [
            {"text": prompt},
            {
                "inline_data": {
                    "mime_type": normalized_mime,
                    "data": base64.b64encode(audio_bytes).decode("utf-8"),
                }
            },
        ],
        generation_config=genai.types.GenerationConfig(
            temperature=0.0,
            max_output_tokens=512,
        ),
    )

    out = (response.text or "").strip()
    out = re.sub(r"^[\"']|[\"']$", "", out)
    out = out.split("\n")[0].strip()
    return out

