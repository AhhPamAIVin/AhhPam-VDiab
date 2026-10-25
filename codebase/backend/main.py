import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from codebase.backend.admin.router import router as admin_router
from codebase.backend.agent.chat_suggestions import router as chat_suggestions_router
from codebase.backend.agent.router import router as agent_router
from codebase.backend.agent.summary_metrics import router as summary_metrics_router
from codebase.backend.appointments.router import router as appointments_router
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
from codebase.backend.security.sql_injection_guard import SQLInjectionGuardMiddleware
from codebase.models.tracing import flush_tracing


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    print(f"Starting {settings.app_name} in {settings.app_env} mode")
    create_db_and_tables()
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SQLInjectionGuardMiddleware)

app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(admin_router, prefix="/api/v1/admin", tags=["admin"])
app.include_router(patients_router, prefix="/api/v1/doctor", tags=["patients"])
app.include_router(insights_router, prefix="/api/v1/doctor", tags=["insights"])
app.include_router(appointments_router, prefix="/api/v1/appointments", tags=["appointments"])
app.include_router(health_router, prefix="/api/v1/health", tags=["health"])
app.include_router(medications_router, prefix="/api/v1/medications", tags=["medications"])
app.include_router(agent_router, prefix="/api/v1/agent", tags=["agent"])
app.include_router(summary_metrics_router, prefix="/api/v1/agent", tags=["agent"])
app.include_router(chat_suggestions_router, prefix="/api/v1/agent", tags=["agent"])
app.include_router(notifications_router, prefix="/api/v1/notifications", tags=["notifications"])


logger = logging.getLogger(__name__)


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
