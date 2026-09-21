"""Review-queue primitives: raising and closing items (architecture M.5).

Kept apart from :mod:`reqpilot.services.review.service`, which resolves items by
acting on them (override, withdraw, merge), so that the services those actions
use can raise and close items without a circular dependency.

A review item is about an **AI proposal**. Raising or closing one never
approves, baselines or transitions a requirement; approval is gate G1.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    Action,
    AuditEventType,
    RequirementCategory,
    ResourceType,
    ReviewReason,
    ReviewResolution,
    ReviewStatus,
)
from reqpilot.domain.errors import ReviewError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.base import utc_now
from reqpilot.domain.models.extraction import ReviewItem
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.repositories.extraction import ReviewItemRepository
from reqpilot.services.audit import AuditService


class ReviewQueue:
    def __init__(self, session: Session, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._repo = ReviewItemRepository(session, actor)
        self._audit = AuditService(session)

    def raise_item(
        self,
        *,
        project_id: ProjectId,
        reason: ReviewReason,
        subject_type: str,
        subject_id: uuid.UUID,
        requirement_version_id: uuid.UUID | None = None,
        related_subject_id: uuid.UUID | None = None,
        category: RequirementCategory | None = None,
        review_signal: float | None = None,
        graph_run_id: uuid.UUID | None = None,
        agent_run_id: uuid.UUID | None = None,
        detail: dict[str, Any] | None = None,
    ) -> ReviewItem:
        item = self._repo.add(
            ReviewItem(
                project_id=project_id,
                reason=reason,
                subject_type=subject_type,
                subject_id=subject_id,
                requirement_version_id=requirement_version_id,
                related_subject_id=related_subject_id,
                category=category,
                review_signal=review_signal,
                graph_run_id=graph_run_id,
                agent_run_id=agent_run_id,
                detail=detail or {},
                status=ReviewStatus.OPEN,
            )
        )
        self._audit.append(
            event_type=AuditEventType.REVIEW_ITEM_RAISED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="review_item",
            subject_id=str(item.id),
            graph_run_id=graph_run_id,
            agent_run_id=agent_run_id,
            payload={
                "reason": str(reason),
                "about": subject_type,
                "about_id": str(subject_id),
                "requirement_version_id": (
                    str(requirement_version_id) if requirement_version_id else None
                ),
                "category": str(category) if category else None,
            },
        )
        return item

    def close(
        self,
        item: ReviewItem,
        resolution: ReviewResolution,
        note: str | None,
        **effects: Any,
    ) -> ReviewItem:
        """Resolve an item once, as the current (human) actor, and audit it."""
        require(
            self._actor,
            Action.REVIEW_RESOLVE,
            ResourceRef(
                resource_type=ResourceType.REVIEW_ITEM, project_id=ProjectId(item.project_id)
            ),
        )
        if item.status is not ReviewStatus.OPEN:
            raise ReviewError("this review item is already resolved")
        item.status = ReviewStatus.RESOLVED
        item.resolution = resolution
        item.resolution_note = (note or "").strip()[:2000] or None
        item.resolved_by = self._actor.actor_id
        item.resolved_at = utc_now()
        self._repo.record_resolution(item)
        self._audit.append(
            event_type=AuditEventType.REVIEW_ITEM_RESOLVED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=ProjectId(item.project_id),
            subject_type="review_item",
            subject_id=str(item.id),
            payload={
                "reason": str(item.reason),
                "resolution": str(resolution),
                "note_supplied": item.resolution_note is not None,
                **effects,
            },
        )
        return item

    def open_for_version(self, project_id: ProjectId, version_id: uuid.UUID) -> list[ReviewItem]:
        return self._repo.open_for_version(project_id, version_id)
