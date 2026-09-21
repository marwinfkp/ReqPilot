"""Merging a duplicate requirement into another, keeping every source link (``FR-EXT-005``).

Only exact duplicates within one batch are merged by code (see the extraction
ruleset). Everything else a human decides, and when a human decides "these are
the same", this service carries it out **through the P1 repository**, so no
history is rewritten:

1. a new version of the surviving requirement is created, with its content
   unchanged and its source references extended by every reference of the
   duplicate - so no stakeholder statement loses its link;
2. the survivor's acceptance criteria are carried forward as copies (a stable
   relation, architecture N.4); its classification is **not** - analysis
   relations are recomputed for a new version, so the new version starts at
   ``EXTRACTED`` and is classified again;
3. the duplicate's current version is withdrawn, with the merge as its reason.
   Its own original wording and sources stay on it, and on the survivor.

Human-only (policy rule 6), and only before anything is under approval: a
merge after approval would be a change to an approved requirement, which is
gate G7's business.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from reqpilot.domain.enums import Action, AuditEventType, ResourceType
from reqpilot.domain.errors import ExtractionError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.extraction import AcceptanceCriterion
from reqpilot.domain.models.requirements import Requirement, RequirementVersion
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.repositories.extraction import AcceptanceCriterionRepository
from reqpilot.repositories.requirements import RequirementRepository, RequirementVersionRepository
from reqpilot.services.audit import AuditService
from reqpilot.services.requirements import RequirementContent, RequirementService

#: A merge is a pre-approval operation.
MERGEABLE_STATES: frozenset[RequirementState] = frozenset(
    {RequirementState.CANDIDATE, RequirementState.EXTRACTED, RequirementState.CLASSIFIED}
)


def _ref_key(ref: dict[str, Any]) -> tuple[str, str, str]:
    return (str(ref.get("kind")), str(ref.get("ref")), str(ref.get("span")))


class RequirementMergeService:
    def __init__(self, session: Session, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._requirements = RequirementRepository(session, actor)
        self._versions = RequirementVersionRepository(session, actor)
        self._criteria = AcceptanceCriterionRepository(session, actor)
        self._audit = AuditService(session)

    def merge(
        self,
        *,
        project_id: ProjectId,
        survivor_id: uuid.UUID,
        duplicate_id: uuid.UUID,
        reason: str,
    ) -> RequirementVersion:
        """Merge requirement ``duplicate_id`` into ``survivor_id``. Returns the new version."""
        require(
            self._actor,
            Action.REQUIREMENT_MERGE,
            ResourceRef(resource_type=ResourceType.REQUIREMENT, project_id=project_id),
        )
        reason = " ".join((reason or "").split())
        if not reason:
            raise ExtractionError("a merge needs a reason")
        if survivor_id == duplicate_id:
            raise ExtractionError("a requirement cannot be merged into itself")
        survivor, survivor_v = self._current(project_id, survivor_id)
        duplicate, duplicate_v = self._current(project_id, duplicate_id)

        refs = list(survivor_v.source_refs or [])
        seen = {_ref_key(r) for r in refs}
        added = 0
        for ref in duplicate_v.source_refs or []:
            if _ref_key(ref) not in seen:
                refs.append(ref)
                seen.add(_ref_key(ref))
                added += 1

        service = RequirementService(self._session, self._actor)
        merged = service.create_version(
            project_id=project_id,
            requirement_id=survivor.id,
            change_reason=f"merged duplicate {duplicate.human_id}: {reason}"[:1000],
            content=RequirementContent(
                statement=survivor_v.statement,
                original_text=survivor_v.original_text,
                category=survivor_v.category,
                priority=survivor_v.priority,
                justification=survivor_v.justification,
                dependencies=tuple(survivor_v.dependencies or []),
                assumptions=tuple(survivor_v.assumptions or []),
                source_refs=tuple(refs),
                review_signal=survivor_v.review_signal,
            ),
        )
        copies = [
            AcceptanceCriterion(
                project_id=project_id,
                requirement_version_id=merged.id,
                ordinal=criterion.ordinal,
                given_text=criterion.given_text,
                when_text=criterion.when_text,
                then_text=criterion.then_text,
                source=criterion.source,
                agent_run_id=criterion.agent_run_id,
                copied_from_id=criterion.id,
            )
            for criterion in self._criteria.for_version(project_id, survivor_v.id)
        ]
        if copies:
            self._criteria.add_all(project_id, copies)
        service.transition(
            project_id=project_id, version_id=merged.id, target=RequirementState.EXTRACTED
        )
        service.withdraw(
            project_id=project_id,
            version_id=duplicate_v.id,
            reason=f"merged into {survivor.human_id}: {reason}",
        )
        self._audit.append(
            event_type=AuditEventType.REQUIREMENTS_MERGED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="requirement",
            subject_id=str(survivor.id),
            payload={
                "survivor": survivor.human_id,
                "duplicate": duplicate.human_id,
                "new_version_id": str(merged.id),
                "withdrawn_version_id": str(duplicate_v.id),
                "source_refs_added": added,
                "source_ref_count": len(refs),
            },
        )
        return merged

    def _current(
        self, project_id: ProjectId, requirement_id: uuid.UUID
    ) -> tuple[Requirement, RequirementVersion]:
        requirement = self._requirements.get(project_id, requirement_id)
        if requirement is None or requirement.current_version_id is None:
            raise ExtractionError("requirement not found in this project")
        version = self._versions.get(project_id, requirement.current_version_id)
        if version is None:  # pragma: no cover - pointer integrity
            raise ExtractionError("requirement has no current version")
        if version.state not in MERGEABLE_STATES:
            raise ExtractionError(
                f"{requirement.human_id} is {version.state}; only requirements that are not "
                "yet analysed or under approval can be merged"
            )
        return requirement, version
