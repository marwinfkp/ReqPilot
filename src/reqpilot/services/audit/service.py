"""The audit service - append and read, never update or delete.

This class is the *only* sanctioned way application code touches
``audit_event``. Its public surface has no mutation method, which is the first
of the three immutability layers the architecture specifies; the database
``REVOKE`` and trigger are the other two (see the initial migration).

Deliberate omission: there is no ``update``, no ``delete``, and no
``get_session().delete(event)`` helper anywhere in this package. If you find
yourself wanting one, the architecture's answer is that you want a *new event*
instead.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from reqpilot.domain.enums import ActorKind, AuditEventType
from reqpilot.domain.errors import ImmutableRecordError
from reqpilot.domain.models.audit import AuditEvent
from reqpilot.domain.models.base import utc_now
from reqpilot.services.audit.hashing import compute_row_hash, verify_chain


class AuditService:
    """Append-only writer and reader for audit events.

    Scoped to a SQLAlchemy session supplied by the caller so that audit writes
    join the caller's transaction. That is required by the architecture: an
    audited action must have happened, and an unaudited one must not have
    (architecture T.1).
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    # -- append ----------------------------------------------------------
    def append(
        self,
        *,
        event_type: AuditEventType,
        actor_kind: ActorKind,
        actor_ref: str,
        project_id: UUID | None = None,
        subject_type: str | None = None,
        subject_id: str | None = None,
        subject_version: str | None = None,
        graph_run_id: UUID | None = None,
        agent_run_id: UUID | None = None,
        payload: dict[str, Any] | None = None,
        occurred_at: dt.datetime | None = None,
    ) -> AuditEvent:
        """Append one event, chaining it to the project's previous event.

        The payload must contain references only - ids, hashes, enum values,
        counts, decisions. Never requirement text, chunk text, prompts or
        secrets (architecture O.1).
        """
        payload = payload or {}
        self._reject_suspicious_payload(payload)

        event_id = uuid4()
        occurred = occurred_at or utc_now()
        prev_hash, prev_seq = self._chain_tip(project_id)

        row_hash = compute_row_hash(
            event_id=str(event_id),
            project_id=str(project_id) if project_id else None,
            occurred_at=occurred,
            actor_kind=str(actor_kind),
            actor_ref=actor_ref,
            event_type=str(event_type),
            subject_type=subject_type,
            subject_id=subject_id,
            payload=payload,
            prev_hash=prev_hash,
        )

        event = AuditEvent(
            id=event_id,
            seq=prev_seq + 1,
            project_id=project_id,
            occurred_at=occurred,
            actor_kind=actor_kind,
            actor_ref=actor_ref,
            event_type=event_type,
            subject_type=subject_type,
            subject_id=subject_id,
            subject_version=subject_version,
            graph_run_id=graph_run_id,
            agent_run_id=agent_run_id,
            payload=payload,
            prev_hash=prev_hash,
            row_hash=row_hash,
        )
        self._session.add(event)
        self._session.flush()
        return event

    # -- read ------------------------------------------------------------
    def list_for_project(self, project_id: UUID) -> list[AuditEvent]:
        """Return a project's events in chain order."""
        stmt = (
            select(AuditEvent).where(AuditEvent.project_id == project_id).order_by(AuditEvent.seq)
        )
        return list(self._session.scalars(stmt))

    def verify_project_chain(self, project_id: UUID) -> tuple[bool, int | None]:
        """Verify a project's hash chain, reporting the first divergence."""
        events = [
            {
                "id": str(e.id),
                "project_id": str(e.project_id) if e.project_id else None,
                "occurred_at": e.occurred_at,
                "actor_kind": str(e.actor_kind),
                "actor_ref": e.actor_ref,
                "event_type": str(e.event_type),
                "subject_type": e.subject_type,
                "subject_id": e.subject_id,
                "payload": e.payload,
                "prev_hash": e.prev_hash,
                "row_hash": e.row_hash,
            }
            for e in self.list_for_project(project_id)
        ]
        return verify_chain(events)

    # -- guards ----------------------------------------------------------
    @staticmethod
    def forbid_mutation(*_args: object, **_kwargs: object) -> None:
        """Always raises. Audit records are append-only.

        Present so the prohibition is explicit and testable rather than merely
        an absence.
        """
        raise ImmutableRecordError(
            "audit_event rows are append-only; record a new event instead of modifying one"
        )

    #: Payload keys that would indicate content is being smuggled into the audit
    #: log. The architecture's rule is references only.
    _FORBIDDEN_PAYLOAD_KEYS = frozenset(
        {"text", "content", "prompt", "statement", "chunk_text", "password", "api_key", "secret"}
    )

    @classmethod
    def _reject_suspicious_payload(cls, payload: dict[str, Any]) -> None:
        offending = cls._FORBIDDEN_PAYLOAD_KEYS & {k.lower() for k in payload}
        if offending:
            raise ValueError(
                "audit payloads carry references only, never content; "
                f"forbidden key(s): {sorted(offending)}"
            )

    def _chain_tip(self, project_id: UUID | None) -> tuple[str | None, int]:
        """Return the project's last row hash and sequence number.

        Ordered by `seq` rather than by timestamp: several appends inside one
        transaction can share a wall-clock value on a coarse-resolution clock,
        which would make the tip - and therefore the chain - ambiguous.
        """
        stmt = (
            select(AuditEvent.row_hash, AuditEvent.seq)
            .where(AuditEvent.project_id == project_id)
            .order_by(AuditEvent.seq.desc())
            .limit(1)
        )
        row = self._session.execute(stmt).first()
        if row is None:
            return None, 0
        return row[0], row[1]
