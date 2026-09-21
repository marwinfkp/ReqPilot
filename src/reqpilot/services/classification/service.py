"""Classification of requirement versions (``FR-CLS-001`` to ``FR-CLS-003``).

A version's classification is a sequence of **revisions**. The model's
proposal is revision 1; every human override is a new revision with every label
it keeps or adds. Nothing is edited in place, so the previous labels, who
replaced them, when, and why all remain (``FR-CLS-003``: "recorded as a
versioned change"). The requirement version itself is never touched.

Two rules that keep this honest:

* **Overrides stop at validation.** A version that is ``VALIDATED`` or further
  along - under approval, approved, baselined - cannot be relabelled: the
  approval would no longer cover what was reviewed. Such a change is a new
  version, through the change gate, as P1 established.
* **Few-shot learning from overrides (``FR-CLS-004``, secondary) is not
  implemented.** Overrides are recorded for audit and evaluation (E7) only.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.orm import Session

from reqpilot.domain.classification import ValidatedLabel
from reqpilot.domain.enums import (
    Action,
    AuditEventType,
    ProposalSource,
    RequirementCategory,
    ResourceType,
    ReviewReason,
    ReviewResolution,
)
from reqpilot.domain.errors import ClassificationError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.extraction import RequirementClassification
from reqpilot.domain.models.requirements import RequirementVersion
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.repositories.extraction import ClassificationRepository
from reqpilot.repositories.requirements import RequirementVersionRepository
from reqpilot.services.audit import AuditService
from reqpilot.services.review.queue import ReviewQueue

#: States in which a version's labels may still change (see module docstring).
RELABELLABLE_STATES: frozenset[RequirementState] = frozenset(
    {
        RequirementState.CANDIDATE,
        RequirementState.EXTRACTED,
        RequirementState.CLASSIFIED,
        RequirementState.ANALYZED,
        RequirementState.CLARIFICATION_REQUIRED,
        RequirementState.CLARIFIED,
    }
)

#: Review reasons about a version's labels; an override settles all of them.
LABEL_REVIEW_REASONS: frozenset[ReviewReason] = frozenset(
    {
        ReviewReason.LOW_CLASSIFICATION_SIGNAL,
        ReviewReason.UNKNOWN_LABEL,
        ReviewReason.CLASSIFICATION_FAILED,
    }
)


class ClassificationService:
    def __init__(self, session: Session, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._repo = ClassificationRepository(session, actor)
        self._versions = RequirementVersionRepository(session, actor)
        self._audit = AuditService(session)

    # -- reads -------------------------------------------------------------
    def current(
        self, project_id: ProjectId, version_id: uuid.UUID
    ) -> list[RequirementClassification]:
        return self._repo.current(project_id, version_id)

    def history(
        self, project_id: ProjectId, version_id: uuid.UUID
    ) -> list[list[RequirementClassification]]:
        """Every revision, oldest first."""
        revisions: dict[int, list[RequirementClassification]] = {}
        for label in self._repo.history(project_id, version_id):
            revisions.setdefault(label.revision_no, []).append(label)
        return [revisions[n] for n in sorted(revisions)]

    # -- the model's proposal ------------------------------------------------
    def record_proposal(
        self,
        *,
        project_id: ProjectId,
        version_id: uuid.UUID,
        labels: Sequence[ValidatedLabel],
        agent_run_id: uuid.UUID,
        graph_run_id: uuid.UUID,
        ruleset_version: str,
    ) -> int:
        """Record validated labels as the next revision. Returns its number."""
        version = self._version(project_id, version_id)
        if not labels:
            raise ClassificationError("a classification proposal records at least one label")
        revision = self._repo.latest_revision_no(project_id, version.id) + 1
        self._repo.add_revision(
            project_id,
            [
                RequirementClassification(
                    project_id=project_id,
                    requirement_version_id=version.id,
                    revision_no=revision,
                    category=label.category,
                    review_signal=label.review_signal,
                    source=ProposalSource.AGENT,
                    needs_review=label.needs_review,
                    rationale=label.rationale,
                    agent_run_id=agent_run_id,
                    created_by=self._actor.actor_id,
                )
                for label in labels
            ],
            action=Action.CLASSIFICATION_PROPOSE,
        )
        self._audit.append(
            event_type=AuditEventType.CLASSIFICATION_PROPOSED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="requirement_version",
            subject_id=str(version.id),
            subject_version=str(version.version_no),
            graph_run_id=graph_run_id,
            agent_run_id=agent_run_id,
            payload={
                "revision": revision,
                "categories": sorted(str(label.category) for label in labels),
                "needs_review": sorted(
                    str(label.category) for label in labels if label.needs_review
                ),
                "ruleset_version": ruleset_version,
            },
        )
        return revision

    # -- a human override (FR-CLS-003) ---------------------------------------
    def override(
        self,
        *,
        project_id: ProjectId,
        version_id: uuid.UUID,
        categories: Sequence[RequirementCategory],
        reason: str,
    ) -> int:
        """Replace a version's labels with ``categories``, as a new revision.

        Human-only (policy rule 6). Open label review items for the version are
        resolved as ``overridden``: the human has now decided the labels.
        """
        require(
            self._actor,
            Action.CLASSIFICATION_OVERRIDE,
            ResourceRef(resource_type=ResourceType.CLASSIFICATION, project_id=project_id),
        )
        reason = " ".join((reason or "").split())
        if not reason:
            raise ClassificationError("an override needs a reason")
        chosen = list(dict.fromkeys(RequirementCategory(c) for c in categories))
        if not chosen:
            raise ClassificationError("an override keeps at least one label (FR-CLS-001)")
        version = self._version(project_id, version_id)
        if version.state not in RELABELLABLE_STATES:
            raise ClassificationError(
                f"a {version.state} version cannot be relabelled: its approval would no longer "
                "cover what was reviewed. Create a new version instead."
            )

        before = self._repo.current(project_id, version.id)
        before_categories = sorted(str(label.category) for label in before)
        after_categories = sorted(str(c) for c in chosen)
        if before and before_categories == after_categories:
            raise ClassificationError("the override does not change the labels")
        revision = (before[0].revision_no if before else 0) + 1
        self._repo.add_revision(
            project_id,
            [
                RequirementClassification(
                    project_id=project_id,
                    requirement_version_id=version.id,
                    revision_no=revision,
                    category=category,
                    review_signal=None,
                    source=ProposalSource.HUMAN,
                    needs_review=False,
                    rationale=None,
                    created_by=self._actor.actor_id,
                    change_reason=reason[:1000],
                )
                for category in chosen
            ],
            action=Action.CLASSIFICATION_OVERRIDE,
        )
        self._audit.append(
            event_type=AuditEventType.HUMAN_OVERRIDE,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="requirement_version",
            subject_id=str(version.id),
            subject_version=str(version.version_no),
            payload={
                "override": "classification",
                "from_revision": before[0].revision_no if before else None,
                "to_revision": revision,
                "before": before_categories,
                "after": after_categories,
            },
        )

        queue = ReviewQueue(self._session, self._actor)
        for item in queue.open_for_version(project_id, version.id):
            if item.reason in LABEL_REVIEW_REASONS:
                queue.close(item, ReviewResolution.OVERRIDDEN, reason, to_revision=revision)
        return revision

    def _version(self, project_id: ProjectId, version_id: uuid.UUID) -> RequirementVersion:
        version = self._versions.get(project_id, version_id)
        if version is None:
            raise ClassificationError("requirement version not found in this project")
        return version
