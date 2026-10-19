# VDiab TTS synthesis service

Standalone text-to-speech microservice for the AI chat's "listen" button (both
the patient-facing assistant and the doctor-facing clinical assistant). Turns
an already-complete assistant reply into audio — it does **not** run
`codebase/backend` or the LangGraph agent, and it never calls
the VDiab backend API itself. The frontend hands the returned audio bytes
straight to an `<audio>` element.

## Why it's a separate service

- Own `requirements.txt`/`.env`/`Dockerfile` — no changes to the repo root
  `requirements.txt`, `docker-compose.yml`, or `codebase/backend`.
- Same reasoning as `codebase/stt-service`: keeps this independently
  deployable, and a future GPU-resident engine (see below) won't need to run
  on the same box as the main backend.

## Why REST, not WebSocket

`codebase/stt-service` streams because a patient is speaking live and the
frontend needs growing partial transcripts. TTS synthesizes audio for one
already-finished piece of text — there's nothing to stream on the way in, so
this is a plain `POST text → audio bytes` endpoint.

## Engines

Three swappable engines behind `POST /tts/synthesize`, selectable per-request:

| `engine` | Model | Notes |
| --- | --- | --- |
| `openai` (default) | `gpt-4o-mini-tts` | Cheapest current OpenAI TTS model; supports a steerable `instructions` param, used here to keep delivery calm/clear for an elderly listener |
| `f5_tts_vietnamese_vivoice` | [hynt/F5-TTS-Vietnamese-ViVoice](https://huggingface.co/hynt/F5-TTS-Vietnamese-ViVoice) | Self-hosted, GPU. **CC-BY-NC-SA-4.0 — non-commercial only.** Zero-shot voice cloning from `settings.REF_AUDIO_PATH`/`REF_TEXT`. Confirmed working end-to-end on the GPU box (2026-08-29) — see `requirements-f5.txt` |
| `vieneu_tts` | [pnnbao-ump/VieNeu-TTS](https://huggingface.co/pnnbao-ump/VieNeu-TTS) | Self-hosted, Apache-2.0. Zero-shot voice cloning, same reference clip. Auto-downloads its own weights. Confirmed working both CPU/ONNX (local Mac dev) and GPU-accelerated (GPU box) — see `requirements-vieneu.txt` |

`f5_tts_vietnamese_vivoice` and `vieneu_tts` both need `assets/reference_voice.wav` +
`settings.REF_TEXT` — a **bootstrap voice only** (transcribed via our own
`stt-service` erax engine from its ASR benchmark sample), not VDiab's chosen
"house voice." The clip's confrontational dialogue tone is wrong for an
elderly patient-facing assistant — picking a real warm/calm recording is a
separate follow-up decision.

## Setup

```bash
cd codebase/tts-service
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
# opt-in, only if testing a self-hosted engine (heavy downloads — torch, and
# for f5_tts_vietnamese_vivoice a ~5GB checkpoint on first load):
#   pip install -r requirements-f5.txt
#   pip install -r requirements-vieneu.txt

cp .env.example .env   # fill in OPENAI_API_KEY (reuse the project's) and TTS_SERVICE_TOKEN
pytest tests/ -v       # strip_markdown_for_speech regression checks
uvicorn app.main:app --host 0.0.0.0 --port 8400
```

Or from the repo root: `make tts-run` / `docker compose -f docker-compose.tts.yml up --build`.

## `POST /tts/synthesize`

Header: `Authorization: Bearer <TTS_SERVICE_TOKEN>` (if `TTS_SERVICE_TOKEN` is
set — omit the check entirely in local dev by leaving it blank).

Body:
```json
{"text": "Xin chào, đây là bản tin sức khỏe của bạn.", "engine": "openai"}
```
`engine`/`voice`/`instructions` are all optional overrides — omit to use the
configured defaults (`DEFAULT_TTS_ENGINE`/`TTS_VOICE`/`TTS_INSTRUCTIONS`).

`text` is markdown from the assistant's reply — it's run through
`app/text_cleanup.strip_markdown_for_speech` server-side before synthesis, so
the caller does not need to pre-clean it.

Response: raw audio bytes on success (`audio/mpeg` for `openai`, `audio/wav`
for `f5_tts_vietnamese_vivoice`/`vieneu_tts`). Errors: `400` (empty/over-length
text, unknown engine), `401` (bad/missing token), `502` (engine load or
synthesis failed).

## Production deployment

Both self-hosted engines were validated end-to-end on the actual GPU box
(RTX 4080) that `stt-service` runs on — confirmed working, but only as a
manual, unregistered test process on a scratch port (8401), never touching
`stt-service.service`/`stt-tunnel.service` or its GPU memory. There's no
permanent systemd unit, reverse tunnel, or Caddy route for `tts-service` yet
— wiring that up for real (same shape as `codebase/stt-service/README.md`'s
production-deployment section) is a separate, explicitly-confirmed step.
