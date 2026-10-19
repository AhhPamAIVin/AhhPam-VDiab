"""OpenAI-backed TTS engine (phase 1).

Raw `openai` SDK, mirroring codebase/stt-service/app/correction.py's client
convention rather than the backend's LangChain wrapper — this service talks
to OpenAI directly, no LangChain dependency needed for one API call.
"""

from __future__ import annotations

from openai import OpenAI

from app.config import settings
from app.engines.base import SynthesisResult


def _client() -> OpenAI:
    return OpenAI(api_key=settings.OPENAI_API_KEY)


class OpenAITTSEngine:
    name = "openai"

    def load(self) -> None:
        pass  # stateless HTTP client — nothing to warm up

    def unload(self) -> None:
        pass

    def synthesize(
        self, text: str, *, voice: str | None = None, instructions: str | None = None
    ) -> SynthesisResult:
        response = _client().audio.speech.create(
            model=settings.TTS_MODEL,
            voice=voice or settings.TTS_VOICE,
            input=text,
            instructions=instructions or settings.TTS_INSTRUCTIONS,
            response_format="mp3",
        )
        return SynthesisResult(audio_bytes=response.content, content_type="audio/mpeg")
