"""The masking and injection-tagging step every ingestion path runs (P11).

``FR-ING-003`` / J.2: text is masked **before** anything else sees it - before it
is segmented, stored, cited or sent to a model - and the unmasking map is kept
apart, project-scoped, where no prompt, log, audit payload or response reaches
it. Q.4: the (masked) text is scanned for injection signals; a hit tags the
record and raises ``INJECTION_SUSPECTED`` with codes and positions only.

Two paths use this: source documents (P3 ingestion) and utterances - interview
and clarification answers, and the questions the interviewer role proposed
(P4). Both call :func:`protect_text` and then :func:`record_map` once the row
they belong to has an id.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from reqpilot.domain.enums import ActorKind, AuditEventType, InjectionSignal, MaskingStatus
from reqpilot.domain.models.guardrails import MaskingMapEntry
from reqpilot.security.injection import signals
from reqpilot.security.masking import Masker, MaskResult, default_masker
from reqpilot.services.audit import AuditService


@dataclass(frozen=True)
class IngestedText:
    """Masked text, what the masker did, and the injection signals it carries."""

    result: MaskResult
    signals: tuple[InjectionSignal, ...]

    @property
    def text(self) -> str:
        return self.result.text

    @property
    def status(self) -> MaskingStatus:
        return self.result.status

    @property
    def masker_id(self) -> str:
        return self.result.masker_id

    @property
    def signal_codes(self) -> list[str]:
        return [s.value for s in self.signals]


def protect_text(text: str, masker: Masker | None = None) -> IngestedText:
    """Mask ``text``, then scan the masked text for injection signals."""
    result = (masker or default_masker()).mask(text)
    return IngestedText(result=result, signals=signals(result.text))


def record_map(
    session: Session,
    *,
    project_id: uuid.UUID,
    source_type: str,
    source_id: uuid.UUID,
    protected: IngestedText,
) -> int:
    """Store the unmasking map for one masked source. Returns the entry count."""
    for entry in protected.result.entries:
        session.add(
            MaskingMapEntry(
                project_id=project_id,
                source_type=source_type,
                source_id=source_id,
                token=entry.token,
                category=entry.category.value,
                value=entry.value,
                masker_id=protected.masker_id,
            )
        )
    if protected.result.entries:
        session.flush()
    return len(protected.result.entries)


def audit_injection(
    session: Session,
    *,
    actor_kind: ActorKind,
    actor_ref: str,
    project_id: uuid.UUID,
    subject_type: str,
    subject_id: str,
    stage: str,
    signal_codes: list[str],
    positions: list[int] | None = None,
) -> None:
    """``INJECTION_SUSPECTED`` (architecture O.2, Q.4): codes and positions, never text."""
    payload: dict[str, object] = {"stage": stage, "signals": sorted(set(signal_codes))}
    if positions is not None:
        payload["ordinals"] = positions
    AuditService(session).append(
        event_type=AuditEventType.INJECTION_SUSPECTED,
        actor_kind=actor_kind,
        actor_ref=actor_ref,
        project_id=project_id,
        subject_type=subject_type,
        subject_id=subject_id,
        payload=payload,
    )
