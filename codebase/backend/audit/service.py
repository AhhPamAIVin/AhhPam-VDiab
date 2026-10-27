"""Audit trail writers (task 6.1).

Two ways an event is written:

- `record()` — explicit, for actions that change no audited row: login,
  reading/being denied a patient's data, data export, deletion workflow.
- the ORM `before_flush` hook below — every insert/update/delete of an
  audited model (health records, medications, clinical notes, care-plan
  targets, AI summaries, accounts/consents, assignments) adds its event to
  the *same* flush, whatever code path made the change (HTTP route, the chat
  agent's record actions, a scheduler). A rolled-back change leaves no event;
  a committed one cannot exist without its event.

One action = one event. Writes that are a mechanical consequence of the
action are folded into it: the glucose record's threshold enrichment right
after its insert, or the previous care-plan/medication version superseded by
a new one, do not produce a second event (see `_fold`).
"""

from __future__ import annotations

import logging
from datetime import datetime
from functools import lru_cache
from typing import Any
from uuid import UUID

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session as ORMSession
from sqlmodel import Session

from codebase.backend.audit.context import RequestContext, current_context
from codebase.backend.audit.models import AuditEvent

logger = logging.getLogger(__name__)

CONSENT_FIELDS = ("ai_memory_consent", "meal_photo_ai_consent", "weekly_report_enabled")
_ACCOUNT_FIELDS = ("email", "full_name", "role", "is_active")

# Per-model policy: resource_type, and which operations are audited.
#   "crud": create/update/delete (record.<op>)
#   "generated": AI output; an insert or a new generated_at is one
#                ai_summary.generate, nothing else is audited
_CRUD, _GENERATED = "crud", "generated"


@lru_cache
def _policies() -> dict[type, tuple[str, str]]:
    # Imported lazily: these model modules sit above the audit package in
    # the import graph (patients/access.py imports this module).
    from codebase.backend.appointments.models import PrevisitBrief
    from codebase.backend.health.models import ActivityRecord, BloodPressureRecord, GlucoseRecord, MealRecord
    from codebase.backend.medications.models import Medication, MedicationNote
    from codebase.backend.patients.models import CarePlanTarget, ClinicalNote, PatientAssignment
    from codebase.backend.summaries.models import AgentSummarySnapshot

    return {
        GlucoseRecord: ("glucose_record", _CRUD),
        BloodPressureRecord: ("blood_pressure_record", _CRUD),
        MealRecord: ("meal_record", _CRUD),
        ActivityRecord: ("activity_record", _CRUD),
        Medication: ("medication", _CRUD),
        MedicationNote: ("medication_note", _CRUD),
        ClinicalNote: ("clinical_note", _CRUD),
        CarePlanTarget: ("care_plan_target", _CRUD),
        PatientAssignment: ("patient_assignment", _CRUD),
        AgentSummarySnapshot: ("ai_summary", _GENERATED),
        PrevisitBrief: ("previsit_brief", _GENERATED),
    }


def _actor(context: RequestContext | None) -> tuple[UUID | None, str]:
    if context is None:
        return None, "system"
    if context.actor_id is None:
        return None, "anonymous"
    return context.actor_id, context.actor_role or "anonymous"


def build_event(
    action: str,
    *,
    resource_type: str | None = None,
    resource_id: Any = None,
    patient_id: UUID | None = None,
    outcome: str = "success",
    detail: str | None = None,
    actor_id: UUID | None = None,
    actor_role: str | None = None,
) -> AuditEvent:
    context = current_context()
    ctx_actor_id, ctx_actor_role = _actor(context)
    return AuditEvent(
        ts=datetime.utcnow(),
        actor_id=actor_id if actor_id is not None else ctx_actor_id,
        actor_role=actor_role or ctx_actor_role,
        action=action,
        resource_type=resource_type,
        resource_id=None if resource_id is None else str(resource_id),
        patient_id=patient_id,
        request_id=context.request_id if context else None,
        ip=context.ip if context else None,
        outcome=outcome,
        detail=detail[:255] if detail else None,
    )


def record(session: Session, action: str, **fields: Any) -> AuditEvent:
    """Write one event and commit it. Call only where nothing else is
    pending on `session` (before the action's own write, or after its
    commit) — the commit would otherwise carry that work with it."""
    audit_event = build_event(action, **fields)
    session.add(audit_event)
    session.commit()
    return audit_event


def record_patient_access(session: Session, actor, patient_id: UUID, *, allowed: bool) -> None:
    """A doctor/admin touching a patient's data (ensure_patient_access), once
    per request and patient; every denial. A patient's access to their own
    data is not logged — that is the bulk of all traffic and not a privacy
    event."""
    if allowed and actor.id == patient_id:
        return
    context = current_context()
    if allowed and context is not None:
        key = ("patient_data.access", str(patient_id))
        if key in context.seen:
            return
        context.seen.add(key)
    detail = f"{context.method} {context.route}" if context is not None and context.route else None
    try:
        record(
            session,
            "patient_data.access",
            resource_type="patient",
            resource_id=patient_id,
            patient_id=patient_id,
            outcome="success" if allowed else "denied",
            detail=detail,
        )
    except Exception:
        # Never let auditing a read break the read itself, but say so loudly.
        session.rollback()
        logger.exception("could not write the patient_data.access audit event")


# --- ORM hook --------------------------------------------------------------


def _patient_of(obj: Any) -> UUID | None:
    from codebase.backend.auth.models import User, UserRole

    if isinstance(obj, User):
        return obj.id if obj.role == UserRole.PATIENT else None
    return getattr(obj, "patient_id", None)


def _changed(obj: Any, name: str) -> bool:
    return inspect(obj).attrs[name].history.has_changes()


def _dedup_store(session: ORMSession) -> tuple[set, set]:
    """Per-request when there is one (a request may span several commits —
    a glucose record is inserted, committed, then enriched and committed
    again), otherwise per session transaction."""
    context = current_context()
    if context is not None:
        return context.seen, context.created
    store = session.info.setdefault("audit_dedup", (set(), set()))
    return store


def _user_events(obj: Any, op: str) -> list[tuple[str, str | None]]:
    if op == "create":
        return [("user.create", None)]
    if op == "delete":
        return [("user.delete", None)]
    events: list[tuple[str, str | None]] = []
    consents = [f"{name}={str(getattr(obj, name)).lower()}" for name in CONSENT_FIELDS if _changed(obj, name)]
    if consents:
        events.append(("consent.update", ",".join(consents)))
    if _changed(obj, "hashed_password"):
        context = current_context()
        own = context is not None and context.actor_id == obj.id
        events.append(("user.password_change" if own else "user.password_reset", None))
    changed_fields = [name for name in _ACCOUNT_FIELDS if _changed(obj, name)]
    if changed_fields:
        events.append(("user.update", ",".join(changed_fields)))
    return events


def _fold(session: ORMSession, resource_type: str, obj: Any, op: str, patient_id: UUID | None) -> bool:
    """True when this write is part of an action already recorded: the same
    write seen twice, or an update after a create of the same resource type
    for the same patient — the created row's own enrichment, or the version
    a new one supersedes (care plan, medication change: both in one flush,
    inserts processed first)."""
    seen, created = _dedup_store(session)
    key = (resource_type, str(obj.id))
    if op == "update" and (key in created or (resource_type, f"patient:{patient_id}") in created):
        return True
    if (resource_type, op, str(obj.id)) in seen:
        return True
    seen.add((resource_type, op, str(obj.id)))
    if op == "create":
        created.add(key)
        created.add((resource_type, f"patient:{patient_id}"))
    return False


@event.listens_for(ORMSession, "before_flush")
def _audit_before_flush(session: ORMSession, flush_context, instances) -> None:
    from codebase.backend.auth.models import User

    policies = _policies()
    pending: list[AuditEvent] = []
    # Inserts first, so an update in the same flush (the version a new one
    # supersedes) is folded into the insert's event.
    batches = (
        ("create", list(session.new)),
        ("delete", list(session.deleted)),
        ("update", [obj for obj in session.dirty if session.is_modified(obj, include_collections=False)]),
    )
    for op, objects in batches:
        for obj in objects:
            if isinstance(obj, AuditEvent):
                continue
            if isinstance(obj, User):
                for action, detail in _user_events(obj, op):
                    pending.append(
                        build_event(
                            action, resource_type="user", resource_id=obj.id, patient_id=_patient_of(obj), detail=detail
                        )
                    )
                continue
            policy = policies.get(type(obj))
            if policy is None:
                continue
            resource_type, kind = policy
            patient_id = _patient_of(obj)
            if kind == _GENERATED:
                if op == "delete" or (op == "update" and not _changed(obj, "generated_at")):
                    continue
                action = "ai_summary.generate"
            else:
                if _fold(session, resource_type, obj, op, patient_id):
                    continue
                action = f"{resource_type}.{op}"
            pending.append(build_event(action, resource_type=resource_type, resource_id=obj.id, patient_id=patient_id))
    for audit_event in pending:
        session.add(audit_event)


@event.listens_for(ORMSession, "after_commit")
@event.listens_for(ORMSession, "after_rollback")
def _reset_dedup(session: ORMSession) -> None:
    session.info.pop("audit_dedup", None)
