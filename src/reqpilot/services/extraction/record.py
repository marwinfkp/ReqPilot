"""The normalised requirement record of ``FR-EXT-002`` (problem statement section 8).

The approved schema has thirteen fields. This module assembles them from where
each actually lives, and says plainly where a field has no value yet:

=========================  ==============================================  ==========
Field                      Source                                          Phase
=========================  ==============================================  ==========
ID                         ``requirement.human_id`` (allocated by code)    P1 / P3
statement                  the current version                             P1 / P3
category                   the current classification revision             P3
source stakeholder         speakers of the version's source spans          P3
business justification     the version, only if the source states it       P3
priority                   the version, only if the source states it       P3
dependencies               the version                                     P1 / P3
assumptions                the version, only if the source states them     P3
acceptance criteria        ``acceptance_criterion`` rows                   P3
applicable regulations     **deferred** - compliance mapping, with evidence  P6
risk level                 **deferred** - risk analysis, 3x3 matrix        P7
confidence score           the extraction review signal (not a probability) P3
approval status            the version's lifecycle state                   P1
=========================  ==============================================  ==========

Nothing is inferred to fill a gap: an absent value is reported as absent, and
the two deferred fields say which phase will produce them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from reqpilot.domain.enums import Action, ResourceType
from reqpilot.domain.errors import ReqPilotError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.extraction import AcceptanceCriterion, RequirementClassification
from reqpilot.domain.models.requirements import Requirement, RequirementVersion
from reqpilot.domain.policy import Actor, ResourceRef, can
from reqpilot.repositories.extraction import (
    AcceptanceCriterionRepository,
    ClassificationRepository,
    ReviewItemRepository,
)
from reqpilot.repositories.requirements import RequirementRepository, RequirementVersionRepository

DEFERRED_REGULATIONS = "deferred: compliance mapping with evidence (roadmap P6)"
DEFERRED_RISK = "deferred: risk analysis with the 3x3 matrix (roadmap P7)"
SIGNAL_CAVEAT = "review-prioritisation signal, not a calibrated probability"


@dataclass(frozen=True)
class RequirementRecord:
    requirement: Requirement
    version: RequirementVersion
    labels: tuple[RequirementClassification, ...]
    criteria: tuple[AcceptanceCriterion, ...]
    stakeholders: tuple[str, ...]
    #: ``None`` when the reader may not see the review queue (e.g. a stakeholder).
    open_review_items: int | None

    @property
    def applicable_regulations(self) -> str:
        return DEFERRED_REGULATIONS

    @property
    def risk_level(self) -> str:
        return DEFERRED_RISK


class RequirementRecordService:
    def __init__(self, session: Session, actor: Actor) -> None:
        self._actor = actor
        self._requirements = RequirementRepository(session, actor)
        self._versions = RequirementVersionRepository(session, actor)
        self._labels = ClassificationRepository(session, actor)
        self._criteria = AcceptanceCriterionRepository(session, actor)
        self._reviews = ReviewItemRepository(session, actor)

    def record(self, project_id: ProjectId, requirement_id: uuid.UUID) -> RequirementRecord:
        requirement = self._requirements.get(project_id, requirement_id)
        if requirement is None or requirement.current_version_id is None:
            raise ReqPilotError("requirement not found in this project")
        version = self._versions.get(project_id, requirement.current_version_id)
        if version is None:  # pragma: no cover - pointer integrity
            raise ReqPilotError("requirement has no current version")
        speakers = sorted(
            {
                str(ref["speaker"])
                for ref in version.source_refs or []
                if isinstance(ref, dict) and ref.get("speaker")
            }
        )
        return RequirementRecord(
            requirement=requirement,
            version=version,
            labels=tuple(self._labels.current(project_id, version.id)),
            criteria=tuple(self._criteria.for_version(project_id, version.id)),
            stakeholders=tuple(speakers),
            open_review_items=(
                len(self._reviews.open_for_version(project_id, version.id))
                if can(
                    self._actor,
                    Action.REVIEW_READ,
                    ResourceRef(resource_type=ResourceType.REVIEW_ITEM, project_id=project_id),
                )
                else None
            ),
        )
