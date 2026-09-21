"""The review queue: a human's decisions about AI proposals (``FR-CLS-002``, M.5).

**This is not approval.** Resolving an item decides what happens to one AI
proposal - keep it, discard it, relabel it, merge it. Requirements are approved
only at gate G1, by an Analyst *and* a Compliance Officer, exactly as P1
implements it. A requirement whose review items are all resolved is no nearer
to approval than it was.

Which resolutions an item admits depends on why it was raised, and each
resolution acts through the service that owns the change: an override through
:class:`ClassificationService`, a rejection through P1's withdrawal, a merge
through :class:`RequirementMergeService`. Every resolution is human-only
(policy rule 6) and audited.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Literal

from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    RequirementCategory,
    ReviewReason,
    ReviewResolution,
    ReviewStatus,
)
from reqpilot.domain.errors import ReviewError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.extraction import ReviewItem
from reqpilot.domain.policy import Actor
from reqpilot.repositories.extraction import ReviewItemRepository
from reqpilot.repositories.requirements import RequirementVersionRepository
from reqpilot.services.classification import ClassificationService
from reqpilot.services.extraction.merge import RequirementMergeService
from reqpilot.services.requirements import RequirementService
from reqpilot.services.review.queue import ReviewQueue

R = ReviewResolution

#: What each kind of item may be resolved as.
ALLOWED_RESOLUTIONS: dict[ReviewReason, frozenset[ReviewResolution]] = {
    ReviewReason.MALFORMED_OUTPUT: frozenset({R.ACKNOWLEDGED}),
    ReviewReason.EXTRACTION_INVALID: frozenset({R.ACKNOWLEDGED}),
    ReviewReason.UNRESOLVED_SOURCE: frozenset({R.ACKNOWLEDGED, R.ACCEPTED, R.REJECTED}),
    ReviewReason.LOW_EXTRACTION_SIGNAL: frozenset({R.ACCEPTED, R.REJECTED}),
    ReviewReason.ACCEPTANCE_CRITERIA_INVALID: frozenset({R.ACKNOWLEDGED, R.REJECTED}),
    ReviewReason.LOW_CLASSIFICATION_SIGNAL: frozenset({R.ACCEPTED, R.OVERRIDDEN}),
    ReviewReason.UNKNOWN_LABEL: frozenset({R.ACKNOWLEDGED, R.OVERRIDDEN}),
    ReviewReason.CLASSIFICATION_FAILED: frozenset({R.ACKNOWLEDGED, R.OVERRIDDEN}),
    ReviewReason.POSSIBLE_DUPLICATE: frozenset({R.KEPT_DISTINCT, R.MERGED}),
}

#: Resolutions that change a requirement, and so need the reason recorded.
_NEEDS_NOTE = frozenset({R.REJECTED, R.OVERRIDDEN, R.MERGED})

#: Resolutions that act on a persisted requirement version.
_VERSION_ONLY = frozenset({R.ACCEPTED, R.REJECTED, R.OVERRIDDEN, R.MERGED, R.KEPT_DISTINCT})


class ReviewQueueService:
    def __init__(self, session: Session, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._repo = ReviewItemRepository(session, actor)
        self._queue = ReviewQueue(session, actor)

    def list(
        self, project_id: ProjectId, *, status: ReviewStatus | None = None
    ) -> list[ReviewItem]:
        return self._repo.list_for_project(project_id, status=status)

    def get(self, project_id: ProjectId, item_id: uuid.UUID) -> ReviewItem | None:
        return self._repo.get(project_id, item_id)

    def resolve(
        self,
        *,
        project_id: ProjectId,
        item_id: uuid.UUID,
        resolution: ReviewResolution,
        note: str | None = None,
        categories: Sequence[RequirementCategory] | None = None,
        keep: Literal["subject", "related"] = "related",
    ) -> ReviewItem:
        item = self._repo.get(project_id, item_id)
        if item is None:
            raise ReviewError("review item not found in this project")
        if item.status is not ReviewStatus.OPEN:
            raise ReviewError("this review item is already resolved")
        allowed = ALLOWED_RESOLUTIONS[item.reason]
        if resolution not in allowed:
            raise ReviewError(
                f"a {item.reason} item may be resolved as {sorted(str(r) for r in allowed)}, "
                f"not {resolution}"
            )
        if resolution in _VERSION_ONLY and item.subject_type != "requirement_version":
            raise ReviewError(
                f"{resolution} acts on a requirement; this item is about a rejected "
                "proposal, which can only be acknowledged"
            )
        note = " ".join((note or "").split()) or None
        if resolution in _NEEDS_NOTE and not note:
            raise ReviewError(f"a {resolution} resolution needs a note saying why")

        if resolution is R.OVERRIDDEN:
            if not categories:
                raise ReviewError("an override needs the labels to set")
            # The override closes every open label item for the version,
            # including this one.
            ClassificationService(self._session, self._actor).override(
                project_id=project_id,
                version_id=item.subject_id,
                categories=categories,
                reason=note or "",
            )
            self._session.refresh(item)
            return item

        if resolution is R.REJECTED:
            RequirementService(self._session, self._actor).withdraw(
                project_id=project_id, version_id=item.subject_id, reason=note or ""
            )
            return self._queue.close(
                item, resolution, note, withdrawn_version_id=str(item.subject_id)
            )

        if resolution is R.MERGED:
            if item.related_subject_id is None:
                raise ReviewError("this item names no second requirement to merge with")
            survivor_v, duplicate_v = (
                (item.related_subject_id, item.subject_id)
                if keep == "related"
                else (item.subject_id, item.related_subject_id)
            )
            versions = RequirementVersionRepository(self._session, self._actor)
            survivor = versions.get(project_id, survivor_v)
            duplicate = versions.get(project_id, duplicate_v)
            if survivor is None or duplicate is None:
                raise ReviewError("a requirement named by this item is no longer in this project")
            merged = RequirementMergeService(self._session, self._actor).merge(
                project_id=project_id,
                survivor_id=survivor.requirement_id,
                duplicate_id=duplicate.requirement_id,
                reason=note or "",
            )
            return self._queue.close(item, resolution, note, merged_version_id=str(merged.id))

        return self._queue.close(item, resolution, note)
