# VDiab STT streaming service

Standalone speech-to-text microservice for the patient AI consultant chat's
"speak" button. Runs on this GPU box only — it does **not** run
`codebase/backend` or the LangGraph agent, and it never calls
the VDiab backend API itself. It just turns voice into a corrected
Vietnamese text string; the frontend hands that string to the
already-existing `api.agentChat()` call, exactly as if it had been typed,
which is what reaches the real consultant.

## Why it's a separate service

- Own `requirements.txt`/`.env`/`Dockerfile` — no changes to the repo root
  `requirements.txt`, `docker-compose.yml`, or `codebase/backend`.
- Talks to the frontend over its own WebSocket port; the frontend is the
  only thing that bridges STT output into the real chat pipeline.

## Models

Three swappable engines behind one WebSocket protocol, for the Vietnamese
ASR-quality comparison:

| `?engine=` | Model | Notes |
| --- | --- | --- |
| `erax` (default) | `erax-ai/EraX-WoW-Turbo-V1.1` | Whisper-turbo fine-tuned for Vietnamese, via faster-whisper/CTranslate2 |
| `whisper` | `large-v3-turbo` | Multilingual Whisper turbo, via faster-whisper/CTranslate2 |
| `qwen_omni` | `Qwen/Qwen2.5-Omni-3B` | Multimodal generative model; needs `requirements-qwen.txt`. No true incremental streaming — only runs on VAD-finalized segments, not on every live partial |

`erax` and `whisper` share one implementation
(`app/engines/faster_whisper_engine.py`) — just a different model id. If a
checkpoint isn't already published in CTranslate2 format, the engine
converts it on first load into `CT2_CACHE_DIR` (requires
`requirements-qwen.txt` for that one-time conversion, since CT2's
`TransformersConverter` needs `transformers`/`torch`).

`MAX_RESIDENT_ENGINES` (default `1`) keeps VRAM use predictable on a 16GB
card — switching `?engine=` evicts the previous one rather than stacking
models up. Bump it if you want to keep two resident at once during
side-by-side testing.

## Setup

```bash
cd codebase/stt-service
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# only if you want to test qwen_omni, or need on-the-fly CT2 conversion:
pip install -r requirements-qwen.txt   # see its header re: Python 3.14 torch wheels

cp .env.example .env   # fill in OPENAI_API_KEY and STT_SERVICE_TOKEN
uvicorn app.main:app --host 0.0.0.0 --port 8300
```

Or from the repo root: `make stt-run` / `docker compose -f docker-compose.stt.yml up --build`.

## Comparing the 3 models on Vietnamese speech

```bash
cd codebase/stt-service
python -m eval.benchmark                          # uses the repo's 022878.wav
python -m eval.benchmark --audio path/to/sample.wav
python -m eval.benchmark --engines erax whisper    # skip qwen_omni
```

Prints load time, inference latency, and the transcript for each engine
side by side — no ground-truth transcript is bundled, so judge Vietnamese
quality by eye. Once you've picked a winner, set `DEFAULT_ASR_ENGINE` in
`.env` (the frontend can still override per-connection via `?engine=` for
continued testing).

## WebSocket protocol — `/ws/transcribe?engine=erax&token=...`

Client sends binary 16kHz mono PCM16 frames (raw audio) and JSON control
frames `{"type":"cancel"}` / `{"type":"stop"}`.

Server sends JSON events:
- `{"type":"partial","text":...}` — live growing transcript while speaking
- `{"type":"processing"}` — VAD detected end of utterance, correction call in flight
- `{"type":"final","text":...}` — corrected transcript, judged clear enough
  to auto-send to the consultant
- `{"type":"needs_clarification","question":...}` — transcript was too
  garbled/ambiguous to safely forward; ask the patient to repeat instead
  (human-in-the-loop safety gate, see `app/correction.py`)
- `{"type":"cancelled"}` — acknowledges a client `cancel`; the in-progress
  utterance is dropped, no correction or send happens
- `{"type":"error","message":...}`

`token` is a static shared secret (`STT_SERVICE_TOKEN`), not patient
authentication — this box has no access to VDiab's user DB/JWT secret. It
only keeps the exposed GPU endpoint from being hit by random traffic. Real
patient auth still happens on the `api.agentChat()` call the frontend makes
afterward.

## Production deployment

Run it on a GPU box and expose `/ws/transcribe` over `wss://` behind a TLS
reverse proxy (e.g. Caddy, which handles the certificate and WebSocket
upgrade automatically). If the GPU box sits behind NAT, a reverse SSH tunnel
(`autossh -R 8300:localhost:8300 <proxy-host>`) lets the proxy reach it
without opening any inbound port. Keep uvicorn bound to `127.0.0.1` so the
proxy/tunnel is the only way in.

Useful commands (from the GPU box, if run under systemd):
```bash
systemctl status stt-service stt-tunnel     # check both are up
journalctl -u stt-service -f                # tail service logs
curl http://127.0.0.1:8300/health           # local check
```

`codebase/stt-service/.env` on this box holds the real `STT_SERVICE_TOKEN`
and a copy of the project's `OPENAI_API_KEY` — the frontend's
`NEXT_PUBLIC_STT_SERVICE_TOKEN` must match that token exactly.
