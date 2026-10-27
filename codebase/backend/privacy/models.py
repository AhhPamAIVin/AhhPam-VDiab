from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


class DeletionRequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"  # the account and its data are gone
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class AccountDeletionRequest(SQLModel, table=True):
    """A patient asking for their account and data to be erased (task 6.5).
    Nothing is deleted until an admin approves — the admin first checks
    whether a legal hold (medical-record retention, an open dispute) applies
    (docs/DATA_RETENTION.md).

    `patient_id`/`decided_by` carry no foreign key on purpose: the request is
    the record that the erasure was asked for and carried out, so it must
    survive the user row it points to."""

    __tablename__ = "account_deletion_requests"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    patient_id: UUID = Field(index=True)
    status: str = Field(default=DeletionRequestStatus.PENDING, max_length=16, index=True)
    requested_at: datetime = Field(default_factory=datetime.utcnow)
    decided_at: datetime | None = None
    decided_by: UUID | None = None
    # The admin's reason for a rejection — operational text, no health data.
    decision_note: str | None = Field(default=None, max_length=500)
    deleted_rows: int | None = None
