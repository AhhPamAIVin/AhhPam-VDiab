import logging
import os
import threading
from contextlib import asynccontextmanager

import openai
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from codebase.backend.admin.router import router as admin_router
from codebase.backend.agent.chat_suggestions import router as chat_suggestions_router
from codebase.backend.agent.router import router as agent_router
from codebase.backend.agent.summary_metrics import router as summary_metrics_router
from codebase.backend.appointments.router import router as appointments_router
from codebase.backend.audit.context import AuditContextMiddleware
from codebase.backend.audit.router import router as audit_router
from codebase.backend.auth.router import router as auth_router
from codebase.backend.config import get_settings
from codebase.backend.db import create_db_and_tables
from codebase.backend.health.router import router as health_router
from codebase.backend.insights.router import router as insights_router
from codebase.backend.insights.scheduler import (
    shutdown_scheduler as shutdown_insights_scheduler,
)
from codebase.backend.insights.scheduler import (
    start_scheduler as start_insights_scheduler,
)
from codebase.backend.medications.router import router as medications_router
from codebase.backend.notifications.router import router as notifications_router
from codebase.backend.notifications.scheduler import shutdown_scheduler, start_scheduler
from codebase.backend.notifications.zalo_link_listener import start_zalo_link_listener, stop_zalo_link_listener
from codebase.backend.patients.router import router as patients_router
from codebase.backend.privacy.router import admin_router as privacy_admin_router
from codebase.backend.privacy.router import router as privacy_router
from codebase.backend.reports.router import router as reports_router
from codebase.backend.security.rate_limit import limit_agent
from codebase.backend.security.sql_injection_guard import SQLInjectionGuardMiddleware
from codebase.models.tracing import flush_tracing


def _warm_up_embeddings() -> None:
    try:
        from codebase.models.rag.embeddings import warm_up

        warm_up()
    except Exception:
        logging.getLogger(__name__).exception("embedding model warm-up failed")


_LANGSMITH_SWITCHES = ("LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING", "LANGSMITH_TRACING", "LANGSMITH_TRACING_V2")


def check_production_settings(settings, environ=os.environ) -> None:
    """Refuse to start a production backend that would leak patient data:
    - against an unauthenticated Qdrant (patient_insights holds patient data,
      Epic 5.7 / 6.4);
    - with LangSmith tracing switched on through the environment: LangChain
      then ships every prompt and answer to LangSmith's cloud, unredacted
      (task 6.4). docker-compose's env_file exports .env into the container,
      so a LANGCHAIN_TRACING_V2=true left there is enough. Langfuse
      (self-hosted, content redacted in production) is the tracing path."""
    if settings.app_env != "production":
        return
    if not settings.qdrant_location and not settings.qdrant_api_key:
        raise RuntimeError(
            "QDRANT_API_KEY must be set when APP_ENV=production (and the Qdrant server started with "
            "QDRANT__SERVICE__API_KEY) — see docs/OPERATIONS_RUNBOOK.md."
        )
    enabled = [name for name in _LANGSMITH_SWITCHES if environ.get(name, "").strip().lower() in {"1", "true", "yes"}]
    if enabled:
        raise RuntimeError(
            f"{', '.join(enabled)} must not be enabled when APP_ENV=production — LangSmith would receive "
            "patient data. Use Langfuse (docs/DATA_FLOWS.md)."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    check_production_settings(settings)
    print(f"Starting {settings.app_name} in {settings.app_env} mode")
    create_db_and_tables()
    if settings.app_env != "test":
        # Load the local embedding model off the event loop so the first chat
        # request doesn't pay the multi-second model load.
        threading.Thread(target=_warm_up_embeddings, name="embeddings-warmup", daemon=True).start()
    start_scheduler()
    start_insights_scheduler()
    start_zalo_link_listener()
    yield
    stop_zalo_link_listener()
    shutdown_insights_scheduler()
    shutdown_scheduler()
    flush_tracing()
    print("Shutting down...")


app = FastAPI(
    title="VDiab Agent",
    description="AI Agent built with LangGraph",
    version="1.0.0",
    lifespan=lifespan,
)

settings = get_settings()
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# httpx logs every request URL at INFO, and the Zalo Bot API carries its
# token in the URL path (notifications/zalo.py) — task 6.4 log review.
logging.getLogger("httpx").setLevel(max(logging.WARNING, logging.getLogger().level))
logging.getLogger("httpcore").setLevel(max(logging.WARNING, logging.getLogger().level))

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SQLInjectionGuardMiddleware)
# Added last = outermost: every request, including ones the inner middlewares
# reject, runs inside its audit context (request id, client IP, actor).
app.add_middleware(AuditContextMiddleware)

app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(admin_router, prefix="/api/v1/admin", tags=["admin"])
app.include_router(audit_router, prefix="/api/v1/admin", tags=["admin"])
app.include_router(privacy_admin_router, prefix="/api/v1/admin", tags=["admin"])
app.include_router(privacy_router, prefix="/api/v1/privacy", tags=["privacy"])
app.include_router(patients_router, prefix="/api/v1/doctor", tags=["patients"])
app.include_router(insights_router, prefix="/api/v1/doctor", tags=["insights"])
app.include_router(appointments_router, prefix="/api/v1/appointments", tags=["appointments"])
app.include_router(health_router, prefix="/api/v1/health", tags=["health"])
app.include_router(medications_router, prefix="/api/v1/medications", tags=["medications"])
app.include_router(agent_router, prefix="/api/v1/agent", tags=["agent"], dependencies=[Depends(limit_agent)])
app.include_router(summary_metrics_router, prefix="/api/v1/agent", tags=["agent"])
app.include_router(chat_suggestions_router, prefix="/api/v1/agent", tags=["agent"])
app.include_router(notifications_router, prefix="/api/v1/notifications", tags=["notifications"])
app.include_router(reports_router, prefix="/api/v1/reports", tags=["reports"])


logger = logging.getLogger(__name__)


@app.exception_handler(openai.APIError)
async def llm_provider_error_handler(request: Request, exc: openai.APIError) -> JSONResponse:
    """The LLM provider failing (quota exhausted, rate limit, outage, timeout)
    is a temporary unavailability, not a server bug — verified live: an
    exhausted proxy quota (403) surfaced to the patient's chat as a bare 500."""
    logger.warning("LLM provider error on %s %s: %s", request.method, request.url.path, type(exc).__name__)
    return JSONResponse(
        status_code=503,
        content={"detail": "Trợ lý AI tạm thời không khả dụng. Vui lòng thử lại sau ít phút."},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort safety net for any exception no router/service already
    turned into an HTTPException — logs the full traceback server-side but
    never leaks internals (stack trace, DB error text) to the client, since
    PHI-adjacent data can end up in exception messages (e.g. a failed query's
    parameters)."""
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/health")
async def health():
    return {"status": "ok", "env": settings.app_env}
