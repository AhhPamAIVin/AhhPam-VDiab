from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class AuditEventPublic(BaseModel):
    id: UUID
    ts: datetime
    actor_id: UUID | None
    actor_role: str
    actor_name: str | None
    action: str
    resource_type: str | None
    resource_id: str | None
    patient_id: UUID | None
    patient_name: str | None
    request_id: str | None
    ip: str | None
    outcome: str
    detail: str | None


class AuditEventPage(BaseModel):
    items: list[AuditEventPublic]
    total: int
