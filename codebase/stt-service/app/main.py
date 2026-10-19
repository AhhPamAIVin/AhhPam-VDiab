"""Standalone streaming STT + correction service.

Runs on this GPU box only. Produces corrected Vietnamese text and nothing
else — it never talks to VDiab's real backend API;
the frontend is responsible for handing the final text to the existing
agentChat() call once we're done here. See codebase/stt-service/README.md.
"""

from __future__ import annotations

import json
import logging

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.correction import correct_transcript
from app.engines.registry import registry
from app.vad import UtteranceSegmenter, VADEventType

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="VDiab STT streaming service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok", "engines": registry.available_engines()}


def _partial_interval_bytes() -> int:
    return int(settings.SAMPLE_RATE * 2 * settings.PARTIAL_INTERVAL_MS / 1000)


@app.websocket("/ws/transcribe")
async def ws_transcribe(
    websocket: WebSocket,
    engine: str = Query(default=None),
    token: str = Query(default=""),
):
    if settings.STT_SERVICE_TOKEN and token != settings.STT_SERVICE_TOKEN:
        await websocket.close(code=4401)
        return

    engine_name = engine or settings.DEFAULT_ASR_ENGINE
    try:
        asr = await run_in_threadpool(registry.get, engine_name)
    except ValueError as exc:
        await websocket.close(code=4400, reason=str(exc))
        return

    await websocket.accept()
    logger.info("Client connected, engine=%s", engine_name)

    segmenter = UtteranceSegmenter()
    buffer = bytearray()
    bytes_since_partial = 0
    partial_interval_bytes = _partial_interval_bytes()

    async def finalize_utterance() -> None:
        nonlocal buffer, bytes_since_partial
        raw_bytes = bytes(buffer)
        buffer = bytearray()
        bytes_since_partial = 0
        if not raw_bytes:
            return
        await websocket.send_json({"type": "processing"})
        raw_text = await run_in_threadpool(asr.transcribe, raw_bytes)
        if not raw_text:
            # VAD thought this was speech but decoding found nothing usable
            # (noise/blip) — reset the client's UI without a clarification
            # prompt, empty text is a no-op on the frontend's send path.
            await websocket.send_json({"type": "final", "text": ""})
            return
        result = await run_in_threadpool(correct_transcript, raw_text)
        if result.is_clear:
            await websocket.send_json({"type": "final", "text": result.corrected_text})
        else:
            await websocket.send_json(
                {"type": "needs_clarification", "question": result.clarification_question}
            )

    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break

            audio_chunk = message.get("bytes")
            if audio_chunk is not None:
                event = segmenter.push(audio_chunk)
                buffer.extend(audio_chunk)
                bytes_since_partial += len(audio_chunk)

                if event.type == VADEventType.UTTERANCE_END:
                    await finalize_utterance()
                elif (
                    event.type == VADEventType.SPEECH_CONTINUES
                    and bytes_since_partial >= partial_interval_bytes
                ):
                    bytes_since_partial = 0
                    partial_text = await run_in_threadpool(asr.transcribe, bytes(buffer))
                    if partial_text:
                        await websocket.send_json({"type": "partial", "text": partial_text})
                continue

            text_frame = message.get("text")
            if text_frame is not None:
                try:
                    control = json.loads(text_frame)
                except ValueError:
                    continue
                control_type = control.get("type")
                if control_type == "cancel":
                    buffer = bytearray()
                    bytes_since_partial = 0
                    segmenter.reset()
                    await websocket.send_json({"type": "cancelled"})
                elif control_type == "stop":
                    break
    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception:  # noqa: BLE001
        logger.exception("Unhandled error in STT websocket")
        try:
            await websocket.send_json({"type": "error", "message": "internal_error"})
        except Exception:  # noqa: BLE001
            pass
