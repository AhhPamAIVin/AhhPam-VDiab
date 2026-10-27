from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Keyword construction by field name (Settings(openai_api_key=...))
        # keeps working alongside the env-var aliases above.
        populate_by_name=True,
    )

    # App
    app_name: str = "DiaB Agent"
    app_env: Literal["development", "production", "test"] = "development"
    app_port: int = Field(default=8100, ge=1, le=65535)
    app_host: str = "0.0.0.0"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    cors_origins: str = "http://localhost:3100"

    # Reminders — wall-clock timezone the fixed monitoring-plan cron (glucose/BP/
    # medication reminders) schedules against, independent of the server's own TZ.
    reminder_timezone: str = "Asia/Ho_Chi_Minh"

    # LLM — any OpenAI-compatible endpoint. Read from OPENAI_API_KEY /
    # OPENAI_BASE_URL, or the provider-neutral AI_API_KEY / AI_API_URL names
    # .env uses. Empty base URL = api.openai.com.
    openai_api_key: str = Field(default="", validation_alias=AliasChoices("OPENAI_API_KEY", "AI_API_KEY"))
    openai_base_url: str = Field(default="", validation_alias=AliasChoices("OPENAI_BASE_URL", "AI_API_URL"))
    model_name: str = "gpt-4o-mini"
    llm_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    # Per-node model override (Epic 5.4), "node=model,node=model", e.g.
    # "verify_citations=gpt-4.1-nano". Nodes not listed use model_name.
    llm_node_models: str = ""

    # Meal photo recognition (Epic 1) — POST /health/meals/photo-analysis
    # sends the photo to the vision model (MODEL_NAME, or LLM_NODE_MODELS
    # "recognize_meal_photo=...") after the patient's own consent, and never
    # stores it. false turns the feature off for everyone.
    meal_photo_analysis_enabled: bool = True
    meal_photo_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
    # Meal carb_load bands per meal (Epic 1.3): < low_max -> low,
    # >= high_min -> high, between -> medium. Default after the ~45-60 g/meal
    # range of ADA carb-counting education; pending the clinical team's
    # sign-off — see codebase/models/agents/nodes/meal/analysis.py.
    meal_carb_low_max_g: float = Field(default=30.0, gt=0)
    meal_carb_high_min_g: float = Field(default=60.0, gt=0)
    # Meal <-> post-meal glucose pairing (Epic 1.4, health/meal_response.py):
    # an AFTER_MEAL reading 60-180 min after the meal, a BEFORE_MEAL/FASTING
    # one up to 60 min before; a food/carb band is shown only from min_n meals.
    meal_response_after_min_minutes: int = Field(default=60, ge=0)
    meal_response_after_max_minutes: int = Field(default=180, ge=1)
    meal_response_before_max_minutes: int = Field(default=60, ge=0)
    meal_response_min_n: int = Field(default=3, ge=1)

    # Patient agent (Epic 8): patient chat runs nodes/agent_plan.py (tool
    # calling, up to patient_agent_max_rounds rounds) instead of the fixed
    # plan_response planner. Doctors and summaries keep the planner.
    patient_agent_enabled: bool = True
    patient_agent_max_rounds: int = Field(default=3, ge=1, le=6)
    # false: stop after the first round whose tool calls all succeeded (one
    # LLM call per turn, like the planner); a further round only retries a
    # failed call. true: always let the model look at the observations and
    # decide on more tools — multi-step, but one more LLM call per turn.
    patient_agent_reflect: bool = False

    # Weekly patient report (Epic 2): generated for the Sunday-Saturday week
    # just ended, at this local time (reminder_timezone).
    weekly_reports_enabled: bool = True
    weekly_report_day_of_week: Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"] = "sun"
    weekly_report_hour: int = Field(default=8, ge=0, le=23)

    # Database
    db_host: str = "localhost"
    db_port: int = Field(default=5533, ge=1, le=65535)
    db_user: str = "postgres"
    db_password: str = "postgres"
    db_name: str = "diab"
    db_sslmode: str = "disable"

    # Auth / JWT
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=60, ge=1)

    # Admin bootstrap (used by scripts/seed_admin.py)
    admin_seed_email: str = ""
    admin_seed_password: str = ""
    admin_seed_name: str = "System Admin"

    # Rate limits (codebase/backend/security/rate_limit.py), requests per
    # minute; 0 disables one limit, rate_limit_enabled=false disables both.
    rate_limit_enabled: bool = True
    rate_limit_login_per_minute: int = Field(default=5, ge=0)
    rate_limit_agent_per_minute: int = Field(default=20, ge=0)

    # Vector Store — Qdrant server at qdrant_url (see docker-compose.yml,
    # `make db-up`). qdrant_location=":memory:" runs an in-process store with
    # no server (tests). See codebase/models/rag/vector_store.py.
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    qdrant_location: str = ""

    # Embeddings — local sentence-transformers model, see
    # codebase/models/rag/embeddings.py. BAAI/bge-m3: multilingual (Vietnamese
    # included), 1024-dim dense, 8192-token context, cosine, no query prefix.
    # The vector size is read from the model, never configured by hand.
    embedding_model: str = "BAAI/bge-m3"
    embedding_device: Literal["auto", "cpu", "cuda", "mps"] = "auto"
    embedding_batch_size: int = Field(default=16, ge=1, le=256)
    # Longest chunk the chunker emits is ~800 tokens plus a short context
    # prefix; capping here bounds memory on batches without truncating real text.
    embedding_max_seq_length: int = Field(default=1024, ge=128, le=8192)

    # Langfuse LLM tracing (replaces LangSmith). Disabled unless both keys are
    # set. See codebase/models/tracing.py and docker-compose.langfuse.yml.
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3300"

    # Zalo OA / ZBS notifications. Credentials must only be set via .env.
    zalo_notifications_enabled: bool = False
    zalo_oa_access_token: str = ""
    zalo_oa_api_url: str = "https://openapi.zalo.me/v3.0/oa/message/cs"
    zalo_zbs_access_token: str = ""
    zalo_zbs_api_url: str = "https://business.openapi.zalo.me/message/template"
    zalo_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    zalo_max_retries: int = Field(default=2, ge=0, le=5)

    # Zalo Bot Platform (bot-api.zaloplatforms.com) — separate product from Zalo
    # OA above; auth is a bot token embedded in the URL, not an access_token header.
    zalo_bot_enabled: bool = False
    zalo_bot_token: str = ""
    zalo_bot_api_base: str = "https://bot-api.zaloplatforms.com"
    # The link a patient opens in Zalo to start a private chat with the bot (shown
    # by Zalo when the bot was created — distinct from a group-invite link). Must
    # be set by hand; there's no API to derive it from the bot token.
    zalo_bot_chat_url: str = ""
    zalo_link_code_ttl_minutes: int = Field(default=10, gt=0, le=60)

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}?sslmode={self.db_sslmode}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
