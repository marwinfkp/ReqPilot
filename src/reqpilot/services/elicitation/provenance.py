"""What a requirement version's sources say about egress (P3 rule, extended in P4, P11).

A version cites document chunks (P3) and, from P4, interview utterances. Whether
text derived from it may leave the machine is decided by the gateway's egress
rule from two facts: were *all* of its sources masked, and were *all* declared
synthetic? A source that cannot be found counts as neither - fail closed. From
P11 an utterance is masked when it records that it passed the protective masker;
utterances recorded before P11 say they did not.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy.orm import Session

from reqpilot.domain.enums import DataSensitivity, MaskingStatus
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.policy import Actor
from reqpilot.repositories.elicitation import InterviewSessionRepository, UtteranceRepository
from reqpilot.repositories.extraction import SourceDocumentRepository


def source_facts(
    session: Session, actor: Actor, project_id: ProjectId, refs: Iterable[dict]
) -> tuple[bool, bool]:
    """``(all masked, all synthetic)`` for the documents and sessions ``refs`` cite."""
    document_ids: set[uuid.UUID] = set()
    session_ids: set[uuid.UUID] = set()
    utterance_ids: set[uuid.UUID] = set()
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        if ref.get("kind") == "utterance" and ref.get("session"):
            session_ids.add(uuid.UUID(str(ref["session"])))
            if ref.get("ref"):
                try:
                    utterance_ids.add(uuid.UUID(str(ref["ref"])))
                except ValueError:
                    return False, False
        elif ref.get("document"):
            document_ids.add(uuid.UUID(str(ref["document"])))
    if not document_ids and not session_ids:
        return False, False

    documents = SourceDocumentRepository(session, actor).documents_by_id(
        project_id, sorted(document_ids)
    )
    if len(documents) != len(document_ids):
        return False, False
    sessions_repo = InterviewSessionRepository(session, actor)
    sessions = [sessions_repo.get(project_id, sid) for sid in sorted(session_ids)]
    if any(s is None for s in sessions):
        return False, False

    utterances_repo = UtteranceRepository(session, actor)
    utterances = [utterances_repo.get(project_id, uid) for uid in sorted(utterance_ids)]
    masked = (
        all(d.masking_status is MaskingStatus.MASKED for d in documents.values())
        # Every cited utterance was found and passed the protective masker (P11);
        # an utterance ref without its id cannot be shown masked.
        and all(u is not None and u.masking_status is MaskingStatus.MASKED for u in utterances)
        and not (session_ids and not utterance_ids)
    )
    synthetic = all(d.sensitivity is DataSensitivity.SYNTHETIC for d in documents.values()) and all(
        s is not None and s.sensitivity is DataSensitivity.SYNTHETIC for s in sessions
    )
    return masked, synthetic
