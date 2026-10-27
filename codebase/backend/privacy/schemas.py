from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class DeletionRequestPublic(BaseModel):
    id: UUID
    patient_id: UUID
    status: str
    requested_at: datetime
    decided_at: datetime | None
    decision_note: str | None
    deleted_rows: int | None


class DeletionRequestAdminView(DeletionRequestPublic):
    # Looked up live for display; None once the account is erased.
    patient_name: str | None
    patient_email: str | None


class DeletionDecision(BaseModel):
    note: str | None = Field(default=None, max_length=500)
