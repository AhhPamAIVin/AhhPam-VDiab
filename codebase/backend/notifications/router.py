from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session

from codebase.backend.auth.dependencies import get_current_user, require_roles
from codebase.backend.auth.models import User, UserRole
from codebase.backend.config import get_settings
from codebase.backend.db import get_session
from codebase.backend.notifications import service
from codebase.backend.notifications.models import Notification, NotificationPreference, NotificationStatus
from codebase.backend.notifications.schemas import (
    NotificationPreferenceCreate,
    NotificationPreferencePublic,
    NotificationPreferenceUpdate,
    NotificationPublic,
    NotificationStatusUpdate,
    ZaloLinkCodePublic,
    ZaloLinkStatusPublic,
    ZaloNotificationPublic,
    ZaloNotificationRequest,
)
from codebase.backend.notifications.zalo import ZaloAPIError, ZaloBotNotifier, ZaloConfigurationError, ZaloNotifier
from codebase.backend.patients.access import ensure_patient_access

router = APIRouter()

_can_respond = require_roles(UserRole.PATIENT, UserRole.DOCTOR)
_can_manage_plan = require_roles(UserRole.PATIENT, UserRole.DOCTOR)
_can_send_zalo = require_roles(UserRole.ADMIN, UserRole.DOCTOR)
_can_link_zalo = require_roles(UserRole.PATIENT)


def _resolve_patient_id(current_user: User, patient_id: UUID | None) -> UUID:
    """Patients default to themselves; Doctor/Admin must specify which patient."""
    if patient_id is not None:
        return patient_id
    if current_user.role == UserRole.PATIENT:
        return current_user.id
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="patient_id is required")


@router.get("", response_model=list[NotificationPublic])
def list_notifications(
    patient_id: UUID | None = Query(default=None),
    status_filter: NotificationStatus | None = Query(default=None, alias="status"),
    on_date: date | None = Query(default=None, description="Filter to reminders scheduled for this calendar date"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[Notification]:
    target_patient_id = _resolve_patient_id(current_user, patient_id)
    ensure_patient_access(session, current_user, target_patient_id)
    return service.list_notifications_for_patient(
        session, target_patient_id, status_filter=status_filter, on_date=on_date
    )


@router.patch("/{notification_id}", response_model=NotificationPublic)
def update_notification(
    notification_id: UUID,
    request: NotificationStatusUpdate,
    current_user: User = Depends(_can_respond),
    session: Session = Depends(get_session),
) -> Notification:
    notification = service.get_notification_or_404(session, notification_id)
    ensure_patient_access(session, current_user, notification.patient_id)
    return service.update_notification_status(
        session,
        notification=notification,
        new_status=request.status,
        snooze_minutes=request.snooze_minutes,
    )


# ---- Notification preferences — the patient's adjustable reminder plan ----


@router.get("/preferences", response_model=list[NotificationPreferencePublic])
def list_notification_preferences(
    patient_id: UUID | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[NotificationPreference]:
    target_patient_id = _resolve_patient_id(current_user, patient_id)
    ensure_patient_access(session, current_user, target_patient_id)
    return service.get_effective_preferences(session, target_patient_id)


@router.post("/preferences", response_model=NotificationPreferencePublic, status_code=status.HTTP_201_CREATED)
def add_notification_preference(
    request: NotificationPreferenceCreate,
    patient_id: UUID | None = Query(default=None),
    current_user: User = Depends(_can_manage_plan),
    session: Session = Depends(get_session),
) -> NotificationPreference:
    target_patient_id = _resolve_patient_id(current_user, patient_id)
    ensure_patient_access(session, current_user, target_patient_id)
    return service.create_custom_preference(
        session,
        patient_id=target_patient_id,
        label=request.label,
        time_of_day=request.time_of_day,
        notification_type=request.notification_type,
    )


@router.patch("/preferences/{preference_id}", response_model=NotificationPreferencePublic)
def update_notification_preference(
    preference_id: UUID,
    request: NotificationPreferenceUpdate,
    current_user: User = Depends(_can_manage_plan),
    session: Session = Depends(get_session),
) -> NotificationPreference:
    preference = service.get_preference_or_404(session, preference_id)
    ensure_patient_access(session, current_user, preference.patient_id)
    return service.update_preference(
        session,
        preference=preference,
        time_of_day=request.time_of_day,
        is_enabled=request.is_enabled,
        label=request.label,
    )


@router.delete("/preferences/{preference_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_notification_preference(
    preference_id: UUID,
    current_user: User = Depends(_can_manage_plan),
    session: Session = Depends(get_session),
) -> None:
    preference = service.get_preference_or_404(session, preference_id)
    ensure_patient_access(session, current_user, preference.patient_id)
    service.delete_preference(session, preference)


@router.get("/zalo/link-status", response_model=ZaloLinkStatusPublic)
def get_zalo_link_status(current_user: User = Depends(_can_link_zalo)) -> ZaloLinkStatusPublic:
    return ZaloLinkStatusPublic(linked=current_user.zalo_chat_id is not None)


@router.post("/zalo/link-code", response_model=ZaloLinkCodePublic, status_code=status.HTTP_201_CREATED)
def create_zalo_link_code(
    current_user: User = Depends(_can_link_zalo),
    session: Session = Depends(get_session),
) -> ZaloLinkCodePublic:
    settings = get_settings()
    link_code = service.create_link_code(
        session, user_id=current_user.id, ttl_minutes=settings.zalo_link_code_ttl_minutes
    )
    return ZaloLinkCodePublic(
        code=link_code.code,
        expires_at=link_code.expires_at,
        chat_url=settings.zalo_bot_chat_url or None,
    )


@router.post("/zalo", response_model=ZaloNotificationPublic)
async def send_zalo_notification(
    request: ZaloNotificationRequest,
    _current_user: User = Depends(_can_send_zalo),
) -> ZaloNotificationPublic:
    """Send one Zalo notification; never logs recipient identifiers or content."""
    if request.channel in ("oa", "bot") and not request.text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="text is required for this channel"
        )
    if request.channel == "zbs" and not request.template_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="template_id is required for ZBS")

    try:
        if request.channel == "bot":
            result = await ZaloBotNotifier().send_message(request.recipient_id, request.text or "")
        elif request.channel == "oa":
            result = await ZaloNotifier().send_oa_text(request.recipient_id, request.text or "")
        else:
            result = await ZaloNotifier().send_zbs_template(
                request.recipient_id,
                request.template_id or "",
                request.template_data,
                request.tracking_id,
            )
    except ZaloConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except ZaloAPIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return ZaloNotificationPublic(
        success=True,
        channel=request.channel,
        recipient_type=request.recipient_type,
        tracking_id=result.tracking_id,
        message="Notification accepted by Zalo",
    )
