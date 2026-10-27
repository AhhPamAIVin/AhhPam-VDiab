"""Read-only audit trail for admins (task 6.1). There is deliberately no
write/update/delete endpoint — the table itself refuses them (audit/models.py)."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlmodel import Session, col, select

from codebase.backend.audit.models import AuditEvent
from codebase.backend.audit.schemas import AuditEventPage, AuditEventPublic
from codebase.backend.auth.dependencies import require_roles
from codebase.backend.auth.models import User, UserRole
from codebase.backend.db import get_session

router = APIRouter(dependencies=[Depends(require_roles(UserRole.ADMIN))])


@router.get("/audit-events", response_model=AuditEventPage)
def list_audit_events(
    action: str | None = Query(default=None, max_length=64, description="Exact action, or a prefix ending in '.'"),
    actor_id: UUID | None = None,
    patient_id: UUID | None = None,
    outcome: str | None = Query(default=None, max_length=16),
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> AuditEventPage:
    """Newest first. Times are naive UTC, like every other timestamp the API returns."""
    conditions = []
    if action:
        conditions.append(
            col(AuditEvent.action).startswith(action) if action.endswith(".") else AuditEvent.action == action
        )
    if actor_id:
        conditions.append(AuditEvent.actor_id == actor_id)
    if patient_id:
        conditions.append(AuditEvent.patient_id == patient_id)
    if outcome:
        conditions.append(AuditEvent.outcome == outcome)
    if since:
        conditions.append(AuditEvent.ts >= since.replace(tzinfo=None))
    if until:
        conditions.append(AuditEvent.ts < until.replace(tzinfo=None))

    total = session.exec(select(func.count()).select_from(AuditEvent).where(*conditions)).one()
    events = session.exec(
        select(AuditEvent).where(*conditions).order_by(col(AuditEvent.ts).desc()).offset(offset).limit(limit)
    ).all()

    # Names are looked up for display, never stored in the trail; a deleted
    # account shows as its id only.
    user_ids = {e.actor_id for e in events if e.actor_id} | {e.patient_id for e in events if e.patient_id}
    names = (
        dict(session.exec(select(User.id, User.full_name).where(col(User.id).in_(user_ids))).all()) if user_ids else {}
    )
    return AuditEventPage(
        total=total,
        items=[
            AuditEventPublic(
                **event.model_dump(),
                actor_name=names.get(event.actor_id),
                patient_name=names.get(event.patient_id),
            )
            for event in events
        ],
    )
