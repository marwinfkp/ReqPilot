"""Request and response models for the P11 guardrail endpoints.

**No request model carries an identity, a role, a project scope, a capability
or an approval.** A session is opened for the identity the request already
proved; a deletion names only the confirmation text; replay and the audit view
take ids and filters. ``extra="forbid"`` turns any other field into a 422.

No response carries a session token except the one that issues it, and none
carries the unmasking map, a prompt, or - for a deleted project - an unredacted
payload.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionIssuedOut(BaseModel):
    session_id: uuid.UUID
    user_id: uuid.UUID
    expires_at: dt.datetime
    #: Shown once. The server keeps only its SHA-256.
    token: str
    token_type: str = "bearer"  # noqa: S105 - the OAuth token-type name, not a secret


class WhoAmIOut(BaseModel):
    user_id: uuid.UUID
    authenticated_by: str
    #: Project id -> the roles held there, read from ``project_member`` now.
    roles_by_project: dict[str, list[str]]


class DeleteProjectIn(_In):
    #: The project's current name, repeated - a guard against deleting the wrong one.
    confirm_name: str = Field(min_length=1, max_length=200)


class DeletionOut(BaseModel):
    project_id: uuid.UUID
    deleted: bool
    already_deleted: bool
    deleted_at: dt.datetime
    deleted_by: uuid.UUID
    rows_removed: dict[str, int]
    total_removed: int
    checkpoint_threads: int
    retained: list[str]
    notice: str = (
        "The project's content has been deleted. Its audit trail is retained with "
        "content-bearing payload values redacted (architecture P.2); who did what, and when, "
        "survives. No retention period is asserted."
    )


class AuditEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    seq: int
    occurred_at: dt.datetime
    actor_kind: str
    actor_ref: str
    event_type: str
    subject_type: str | None
    subject_id: str | None
    subject_version: str | None
    payload: dict[str, Any]


class AuditVerifyOut(BaseModel):
    project_id: uuid.UUID
    intact: bool
    first_divergence: int | None
    events: int


class ReplayStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    occurred_at: dt.datetime
    actor_kind: str
    actor_ref: str
    event_type: str
    subject_type: str | None
    subject_id: str | None
    change: dict[str, Any]
    state_after: dict[str, Any]


class ReplayOut(BaseModel):
    entity_type: str
    entity_id: uuid.UUID
    project_id: uuid.UUID
    complete: bool
    gaps: list[str]
    chain_ok: bool
    redacted: bool
    #: The current persisted record - separate from the reconstruction.
    current: dict[str, Any] | None
    reconstructed: dict[str, Any]
    steps: list[ReplayStepOut]
    notice: str = (
        "Reconstructed from the append-only audit trail and compared with the current record. "
        "A history is marked complete only when every event applied, the result matches the "
        "record and the hash chain verifies; otherwise every gap is listed. Replay is read-only."
    )
