"""Standalone settings for the STT streaming service.

Deliberately isolated from codebase/backend/config.py — this service ships
and deploys independently on a GPU box that does not run the main backend.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    HOST: str = "0.0.0.0"
    PORT: int = 8300

    # Shared secret the frontend must send as ?token= on the WS connection.
    # This service has no access to VDiab's user DB/JWT signing key, so it
    # cannot authenticate a patient — this only keeps the exposed GPU
    # endpoint from being hit by random internet traffic. Real patient auth
    # still happens where it already did, on the api.agentChat() call.
    STT_SERVICE_TOKEN: str = ""

    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    # Which ASR engine backs a WS connection when the client omits ?engine=.
    # One of: "erax", "whisper", "qwen_omni". Kept swappable via query param
    # for benchmarking; set this once a winner is picked.
    DEFAULT_ASR_ENGINE: str = "erax"

    ERAX_MODEL_ID: str = "erax-ai/EraX-WoW-Turbo-V1.1"
    WHISPER_MODEL_ID: str = "large-v3-turbo"
    QWEN_OMNI_MODEL_ID: str = "Qwen/Qwen2.5-Omni-3B"

    ASR_DEVICE: str = "cuda"
    ASR_COMPUTE_TYPE: str = "float16"

    # How many ASR engines may stay resident in VRAM at once. Loading all
    # three together is tight on a 16GB card (Qwen2.5-Omni-3B alone is
    # ~6-7GB in bf16) — default to 1 so switching ?engine= during a
    # benchmarking session evicts the previous one instead of stacking up.
    MAX_RESIDENT_ENGINES: int = 1

    # CTranslate2 conversion cache for HF checkpoints that aren't already
    # published in CT2 format (see engines/faster_whisper_engine.py).
    CT2_CACHE_DIR: str = "./.ct2-cache"

    # --- VAD (simple energy-based, no extra ML dependency) ---
    VAD_FRAME_MS: int = 20
    VAD_SPEECH_RMS_THRESHOLD: float = 500.0  # int16 RMS
    VAD_SILENCE_MS: int = 700  # silence needed to finalize an utterance
    VAD_MIN_SPEECH_MS: int = 250  # ignore blips shorter than this
    PARTIAL_INTERVAL_MS: int = 1000  # re-decode cadence for live partials

    # --- Correction / HITL gate ---
    OPENAI_API_KEY: str = ""
    CORRECTION_MODEL: str = "gpt-4o-mini"

    SAMPLE_RATE: int = 16000


settings = Settings()
