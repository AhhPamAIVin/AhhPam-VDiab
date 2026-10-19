"""Standalone TTS synthesis service.

Turns one already-complete assistant reply into audio. Unlike
codebase/stt-service (streaming WS, live partials while a patient speaks),
this is a plain REST request/response — there's nothing to stream while
synthesizing a fixed piece of text. Never talks to VDiab's real backend; the
frontend hands the returned audio to an <audio> element directly. See
codebase/tts-service/README.md.
"""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from app.config import settings
from app.engines.registry import registry
from app.text_cleanup import strip_markdown_for_speech

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="VDiab TTS service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok", "engines": registry.available_engines()}


def require_token(authorization: str | None = Header(default=None)) -> None:
    """Static shared-secret bearer check — same non-patient-auth rationale as
    STT_SERVICE_TOKEN, adapted from a WS ?token= query param to an HTTP header
    since this service is plain REST."""
    if settings.TTS_SERVICE_TOKEN and authorization != f"Bearer {settings.TTS_SERVICE_TOKEN}":
        raise HTTPException(status_code=401, detail="invalid or missing token")


class SynthesizeRequest(BaseModel):
    text: str = Field(..., min_length=1)
    engine: str | None = None
    voice: str | None = None
    instructions: str | None = None


@app.post("/tts/synthesize", dependencies=[Depends(require_token)])
async def synthesize(payload: SynthesizeRequest) -> Response:
    text = strip_markdown_for_speech(payload.text)
    if not text:
        raise HTTPException(status_code=400, detail="empty text after stripping markdown")
    if len(text) > settings.TTS_MAX_INPUT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"text exceeds {settings.TTS_MAX_INPUT_CHARS} characters",
        )

    try:
        engine = await run_in_threadpool(registry.get, payload.engine or settings.DEFAULT_TTS_ENGINE)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - engine.load() failing (bad checkpoint/vocab/etc.) must not 500
        logger.exception("TTS engine load failed")
        raise HTTPException(status_code=502, detail="Không thể tạo giọng nói. Vui lòng thử lại.") from exc

    try:
        result = await run_in_threadpool(
            engine.synthesize, text, voice=payload.voice, instructions=payload.instructions
        )
    except Exception as exc:  # noqa: BLE001 - any engine failure must surface as a clean 502
        logger.exception("TTS synthesis failed")
        raise HTTPException(status_code=502, detail="Không thể tạo giọng nói. Vui lòng thử lại.") from exc

    return Response(content=result.audio_bytes, media_type=result.content_type)
