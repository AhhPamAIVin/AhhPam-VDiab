"""Patient data rights (task 6.5): self-service export, and account deletion
requested by the patient, carried out only after an admin approves."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlmodel import Session, col, select

from codebase.backend.audit import service as audit
from codebase.backend.auth.dependencies import require_roles
from codebase.backend.auth.models import User, UserRole
from codebase.backend.db import get_session
from codebase.backend.privacy import service
from codebase.backend.privacy.models import AccountDeletionRequest, DeletionRequestStatus
from codebase.backend.privacy.schemas import DeletionDecision, DeletionRequestAdminView, DeletionRequestPublic

router = APIRouter()
admin_router = APIRouter(dependencies=[Depends(require_roles(UserRole.ADMIN))])

_patient_only = require_roles(UserRole.PATIENT)


def _latest_request(session: Session, patient_id: UUID) -> AccountDeletionRequest | None:
    return session.exec(
        select(AccountDeletionRequest)
        .where(AccountDeletionRequest.patient_id == patient_id)
        .order_by(col(AccountDeletionRequest.requested_at).desc())
    ).first()


@router.get("/me/export")
async def export_my_data(
    format: Literal["json", "csv"] = Query(default="json"),
    current_user: User = Depends(_patient_only),
    session: Session = Depends(get_session),
) -> Response:
    """Everything the system holds about the caller: account, health records,
    medications, notes, AI summaries and reports, notifications and the
    patient's own chat transcripts. `csv` = a zip with one CSV per table."""
    data = await service.collect_patient_data(session, current_user)
    stamp = datetime.utcnow().strftime("%Y%m%d")
    if format == "csv":
        body, media_type, filename = service.to_csv_zip(data), "application/zip", f"vdiab-du-lieu-{stamp}.zip"
    else:
        body, media_type, filename = service.to_json_bytes(data), "application/json", f"vdiab-du-lieu-{stamp}.json"
    audit.record(
        session,
        "patient_data.export",
        resource_type="patient",
        resource_id=current_user.id,
        patient_id=current_user.id,
        detail=format,
    )
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


@router.get("/me/deletion-request", response_model=DeletionRequestPublic | None)
def get_my_deletion_request(
    current_user: User = Depends(_patient_only), session: Session = Depends(get_session)
) -> AccountDeletionRequest | None:
    return _latest_request(session, current_user.id)


@router.post("/me/deletion-request", response_model=DeletionRequestPublic, status_code=status.HTTP_201_CREATED)
def request_account_deletion(
    current_user: User = Depends(_patient_only), session: Session = Depends(get_session)
) -> AccountDeletionRequest:
    latest = _latest_request(session, current_user.id)
    if latest is not None and latest.status == DeletionRequestStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Bạn đã có một yêu cầu xóa tài khoản đang chờ duyệt."
        )
    deletion_request = AccountDeletionRequest(patient_id=current_user.id)
    session.add(deletion_request)
    session.commit()
    session.refresh(deletion_request)
    audit.record(
        session,
        "account_deletion.request",
        resource_type="account_deletion_request",
        resource_id=deletion_request.id,
        patient_id=current_user.id,
    )
    session.refresh(deletion_request)
    return deletion_request


@router.post("/me/deletion-request/cancel", response_model=DeletionRequestPublic)
def cancel_account_deletion(
    current_user: User = Depends(_patient_only), session: Session = Depends(get_session)
) -> AccountDeletionRequest:
    latest = _latest_request(session, current_user.id)
    if latest is None or latest.status != DeletionRequestStatus.PENDING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Không có yêu cầu nào đang chờ duyệt.")
    latest.status = DeletionRequestStatus.CANCELLED
    latest.decided_at = datetime.utcnow()
    session.add(latest)
    session.commit()
    audit.record(
        session,
        "account_deletion.cancel",
        resource_type="account_deletion_request",
        resource_id=latest.id,
        patient_id=current_user.id,
    )
    session.refresh(latest)
    return latest


# --- admin -----------------------------------------------------------------


def _pending_or_409(session: Session, request_id: UUID) -> AccountDeletionRequest:
    deletion_request = session.get(AccountDeletionRequest, request_id)
    if deletion_request is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deletion request not found")
    if deletion_request.status != DeletionRequestStatus.PENDING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Yêu cầu này đã được xử lý.")
    return deletion_request


@admin_router.get("/deletion-requests", response_model=list[DeletionRequestAdminView])
def list_deletion_requests(
    status_filter: str | None = Query(default=None, alias="status", max_length=16),
    session: Session = Depends(get_session),
) -> list[DeletionRequestAdminView]:
    statement = select(AccountDeletionRequest).order_by(col(AccountDeletionRequest.requested_at).desc())
    if status_filter:
        statement = statement.where(AccountDeletionRequest.status == status_filter)
    requests = session.exec(statement).all()
    ids = {r.patient_id for r in requests}
    users = {u.id: u for u in session.exec(select(User).where(col(User.id).in_(ids))).all()} if ids else {}
    return [
        DeletionRequestAdminView(
            **r.model_dump(),
            patient_name=users[r.patient_id].full_name if r.patient_id in users else None,
            patient_email=users[r.patient_id].email if r.patient_id in users else None,
        )
        for r in requests
    ]


@admin_router.post("/deletion-requests/{request_id}/approve", response_model=DeletionRequestPublic)
def approve_deletion_request(
    request_id: UUID,
    decision: DeletionDecision,
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
    session: Session = Depends(get_session),
) -> AccountDeletionRequest:
    """Irreversible: erases the patient's account and all their data."""
    admin_id = current_user.id
    patient_id = _pending_or_409(session, request_id).patient_id
    removed = service.delete_patient_account(session, patient_id)
    deletion_request = session.get(AccountDeletionRequest, request_id)  # identity map was cleared
    deletion_request.status = DeletionRequestStatus.APPROVED
    deletion_request.decided_at = datetime.utcnow()
    deletion_request.decided_by = admin_id
    deletion_request.decision_note = decision.note
    deletion_request.deleted_rows = removed
    session.add(deletion_request)
    session.commit()
    service.delete_patient_vectors(patient_id)
    audit.record(
        session,
        "account_deletion.approve",
        resource_type="account_deletion_request",
        resource_id=request_id,
        patient_id=patient_id,
        detail=f"rows={removed}",
    )
    session.refresh(deletion_request)
    return deletion_request


@admin_router.post("/deletion-requests/{request_id}/reject", response_model=DeletionRequestPublic)
def reject_deletion_request(
    request_id: UUID,
    decision: DeletionDecision,
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
    session: Session = Depends(get_session),
) -> AccountDeletionRequest:
    """E.g. a legal hold applies — say which in `note`, the patient sees it."""
    deletion_request = _pending_or_409(session, request_id)
    deletion_request.status = DeletionRequestStatus.REJECTED
    deletion_request.decided_at = datetime.utcnow()
    deletion_request.decided_by = current_user.id
    deletion_request.decision_note = decision.note
    session.add(deletion_request)
    session.commit()
    audit.record(
        session,
        "account_deletion.reject",
        resource_type="account_deletion_request",
        resource_id=request_id,
        patient_id=deletion_request.patient_id,
    )
    session.refresh(deletion_request)
    return deletion_request
