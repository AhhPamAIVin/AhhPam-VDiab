"""Standalone settings for the TTS synthesis service.

Deliberately isolated from codebase/backend/config.py — this service ships
and deploys independently, same as codebase/stt-service.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    HOST: str = "0.0.0.0"
    PORT: int = 8400

    # Shared secret the frontend must send as `Authorization: Bearer <token>`.
    # This service has no access to VDiab's user DB/JWT signing key, so it
    # cannot authenticate a patient — this only keeps the exposed endpoint
    # from being hit by random internet traffic. Real patient auth already
    # happened on the api.agentChat() call that produced the text spoken here.
    TTS_SERVICE_TOKEN: str = ""

    CORS_ORIGINS: list[str] = ["http://localhost:3100"]

    # openai | f5_tts_vietnamese_vivoice | vieneu_tts — see
    # app/engines/registry.py. Kept swappable per-request via the `engine`
    # field for A/B comparison.
    DEFAULT_TTS_ENGINE: str = "openai"

    # Unused by the stateless openai engine; caps how many GPU-resident
    # engines (f5_tts_vietnamese_vivoice, vieneu_tts) stay loaded at once —
    # same eviction mechanism as stt-service's registry.
    MAX_RESIDENT_ENGINES: int = 1

    # --- Reference voice for the zero-shot voice-cloning engines (F5-TTS,
    # VieNeu-TTS) — both need a short reference clip AND its exact
    # transcript. assets/reference_voice.wav is codebase/stt-service's own
    # ASR benchmark sample (022878.wav), transcribed with our own erax
    # engine for this transcript. This is a BOOTSTRAP voice for proving the
    # plumbing works, not VDiab's chosen "house voice" — the confrontational
    # dialogue-clip tone is wrong for an elderly patient-facing assistant;
    # picking a real warm/calm recording is a separate follow-up decision.
    REF_AUDIO_PATH: str = "./assets/reference_voice.wav"
    REF_TEXT: str = (
        "hừ thế mày đã nuôi được bố mẹ mày bữa nào chưa hay xưa nay vẫn báo "
        "hại cơm cháo mẹ mãi mấy hôm thấy ông đơ mặt không thèm nói mày lại "
        "làm già à"
    )

    # --- F5-TTS-Vietnamese-ViVoice (hynt/F5-TTS-Vietnamese-ViVoice on HF,
    # CC-BY-NC-SA-4.0 — non-commercial only, confirmed fine for this
    # project). Confirmed live on the GPU box (2026-08-29): this repo has no
    # file actually named vocab.txt (404) — its "config.json" is mislabeled;
    # the real content is a flat newline-separated Vietnamese/pinyin
    # character list, i.e. the vocab file itself under the wrong name. ---
    F5_CKPT_REPO: str = "hynt/F5-TTS-Vietnamese-ViVoice"
    F5_CKPT_FILENAME: str = "model_last.pt"
    F5_VOCAB_FILENAME: str = "config.json"
    F5_MODEL_NAME: str = "F5TTS_v1_Base"
    # Empty = auto-detect (cuda/mps/cpu) via F5TTS's own device selection.
    F5_DEVICE: str = ""

    # --- VieNeu-TTS (pnnbao-ump/VieNeu-TTS on HF, Apache-2.0). Auto-
    # downloads its own weights on first load, no manual checkpoint mgmt. ---
    # onnx = CPU path, VieNeu's own documented recommendation over MPS on
    # Apple Silicon; set to "auto" once this runs on a real CUDA GPU box.
    VIENEU_BACKEND: str = "onnx"

    # Reuse the same key already used by codebase/backend's agent (and
    # codebase/stt-service's correction step) — TTS synthesis is a small,
    # cheap call, no new provider account needed.
    OPENAI_API_KEY: str = ""
    TTS_MODEL: str = "gpt-4o-mini-tts"
    TTS_VOICE: str = "alloy"
    # gpt-4o-mini-tts supports steerable prosody via this instructions param —
    # used here to keep delivery calm/clear for an elderly listener.
    TTS_INSTRUCTIONS: str = (
        "Nói chậm rãi, ấm áp và rõ ràng, như đang trò chuyện với một người cao tuổi."
    )
    # gpt-4o-mini-tts's real input ceiling is ~2000 tokens; reject an
    # over-length reply outright rather than silently truncating mid-sentence.
    TTS_MAX_INPUT_CHARS: int = 4000


settings = Settings()
