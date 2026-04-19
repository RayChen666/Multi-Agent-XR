"""
LLM cleanup for raw Web Speech API transcripts.
Isolated from MAS agents: only normalizes text for the command input field.
"""

import os
import re
import google.generativeai as genai
from dotenv import load_dotenv
from pathlib import Path


def clean_speech_transcript(raw: str) -> str:
    """
    Turn noisy STT text into a single clean scene command line.

    Rules: fix obvious spelling, remove filler (um, uh), keep imperative meaning,
    do not invent new objects or actions. Output plain text only, no quotes/markdown.
    """
    text = (raw or "").strip()
    if not text:
        return ""

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

    model = genai.GenerativeModel("gemini-2.5-flash-lite")

    prompt = f"""You fix voice-to-text for a 3D scene voice assistant (add/move/remove/rotate objects).

    INPUT (raw speech transcript):
    {text}

    TASK:
    - Output ONE line: a clean, grammatical command the user can edit or execute.
    - Fix spelling and word errors (e.g. "chaor" -> "chair").
    - Remove filler words (um, uh, like, you know) unless they carry meaning.
    - Keep the user's intent; do NOT add objects or actions they did not say.
    - Do NOT wrap in quotes. Do NOT add explanations. Plain text only.
    - If an article (a, an, the) is used, keep it unless it doesn't make sense.
    - If the user changes their intent mid sentence, keep the latest intent: "Example: Add three chairs... No sorry add two chairs" ==> Output: Add two chairs
    """

    response = model.generate_content(
        prompt,
        generation_config=genai.types.GenerationConfig(
            temperature=0.1,
            max_output_tokens=256,
        ),
    )
    out = (response.text or "").strip()
    out = re.sub(r"^[\"']|[\"']$", "", out)
    out = out.split("\n")[0].strip()
    return out
