"""Requirement endpoints (architecture section S).

Every handler authorises through the service layer, which authorises through
``policy.can``. Nothing here touches a model or a session directly.

Note what is absent: there is no endpoint that writes ``state``, and no payload
field that carries an approval. ``PATCH`` creates a successor version rather
than editing one, because versions are immutable.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from reqpilot.api.dependencies import CurrentActor, DbSession
from reqpilot.api.lookup import require_found
from reqpilot.api.schemas import (
    CreateRequirementIn,
    RequirementContentIn,
    RequirementDetailOut,
    RequirementOut,
    RequirementVersionOut,
    TransitionIn,
    UpdateRequirementIn,
    WithdrawIn,
)
from reqpilot.domain.enums import Action, ResourceType
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.lifecycle import allowed_targets, check_transition
from reqpilot.domain.models.requirements import Requirement
from reqpilot.domain.policy import Actor
from reqpilot.services.requirements import RequirementContent, RequirementService

router = APIRouter(prefix="/api/v1", tags=["requirements"])


def to_content(payload: RequirementContentIn) -> RequirementContent:
    return RequirementContent(
        statement=payload.statement,
        original_text=payload.original_text,
        category=payload.category,
        priority=payload.priority,
        justification=payload.justification,
        dependencies=tuple(payload.dependencies),
        assumptions=tuple(payload.assumptions),
        source_refs=tuple(ref.model_dump() for ref in payload.source_refs),
    )


def find_requirement(
    session: DbSession, actor: Actor, requirement_id: uuid.UUID
) -> tuple[ProjectId, Requirement]:
    """Locate a requirement among the projects this actor belongs to."""
    service = RequirementService(session, actor)
    return require_found(
        actor,
        Action.REQUIREMENT_READ,
        ResourceType.REQUIREMENT,
        lambda pid: service.get_requirement(pid, requirement_id),
    )


def detail_for(
    session: DbSession, actor: Actor, project_id: ProjectId, requirement: Requirement
) -> RequirementDetailOut:
    service = RequirementService(session, actor)
    versions = service.version_history(project_id, requirement.id)
    current = next((v for v in versions if v.id == requirement.current_version_id), None)

    targets: list = []
    if current is not None:
        ctx = service.build_context(project_id, current)
        targets = sorted(
            t
            for t in allowed_targets(current.state)
            if check_transition(current.state, t, ctx) is None
        )

    return RequirementDetailOut(
        requirement=RequirementOut.model_validate(requirement),
        current_version=(
            RequirementVersionOut.model_validate(current) if current is not None else None
        ),
        versions=[RequirementVersionOut.model_validate(v) for v in versions],
        available_transitions=targets,
    )


@router.post(
    "/projects/{project_id}/requirements",
    response_model=RequirementDetailOut,
    status_code=status.HTTP_201_CREATED,
)
def create_requirement(
    project_id: uuid.UUID,
    payload: CreateRequirementIn,
    session: DbSession,
    actor: CurrentActor,
) -> RequirementDetailOut:
    """Create a requirement and its first version in CANDIDATE."""
    service = RequirementService(session, actor)
    requirement, _version = service.create_requirement(
        project_id=ProjectId(project_id),
        domain=payload.domain,
        kind=payload.kind,
        human_id=payload.human_id,
        content=to_content(payload),
    )
    return detail_for(session, actor, ProjectId(project_id), requirement)


@router.get("/projects/{project_id}/requirements", response_model=list[RequirementOut])
def list_requirements(
    project_id: uuid.UUID, session: DbSession, actor: CurrentActor
) -> list[RequirementOut]:
    service = RequirementService(session, actor)
    return [
        RequirementOut.model_validate(r) for r in service.list_requirements(ProjectId(project_id))
    ]


@router.get("/requirements/{requirement_id}", response_model=RequirementDetailOut)
def get_requirement(
    requirement_id: uuid.UUID, session: DbSession, actor: CurrentActor
) -> RequirementDetailOut:
    project_id, requirement = find_requirement(session, actor, requirement_id)
    return detail_for(session, actor, project_id, requirement)


@router.patch("/requirements/{requirement_id}", response_model=RequirementDetailOut)
def update_requirement(
    requirement_id: uuid.UUID,
    payload: UpdateRequirementIn,
    session: DbSession,
    actor: CurrentActor,
) -> RequirementDetailOut:
    """Create a successor version.

    This never edits the current version. If that version is APPROVED or
    BASELINED it stays exactly as it is, and a G7 change gate is raised for the
    successor.
    """
    project_id, requirement = find_requirement(session, actor, requirement_id)
    RequirementService(session, actor).create_version(
        project_id=project_id,
        requirement_id=requirement.id,
        content=to_content(payload),
        change_reason=payload.change_reason,
    )
    session.refresh(requirement)
    return detail_for(session, actor, project_id, requirement)


@router.post("/requirements/{requirement_id}/withdraw", response_model=RequirementDetailOut)
def withdraw_requirement(
    requirement_id: uuid.UUID,
    payload: WithdrawIn,
    session: DbSession,
    actor: CurrentActor,
) -> RequirementDetailOut:
    project_id, requirement = find_requirement(session, actor, requirement_id)
    if requirement.current_version_id is None:  # pragma: no cover - defensive
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="no current version")
    RequirementService(session, actor).withdraw(
        project_id=project_id,
        version_id=requirement.current_version_id,
        reason=payload.reason,
    )
    session.refresh(requirement)
    return detail_for(session, actor, project_id, requirement)


@router.post("/requirement-versions/{version_id}/transition", response_model=RequirementVersionOut)
def transition_version(
    version_id: uuid.UUID,
    payload: TransitionIn,
    session: DbSession,
    actor: CurrentActor,
) -> RequirementVersionOut:
    """Request one guarded lifecycle transition.

    The only endpoint that changes a state, and it cannot reach APPROVED: that
    guard requires a recorded approval decision, which only the approval
    endpoint can produce.
    """
    service = RequirementService(session, actor)
    project_id, _version = require_found(
        actor,
        Action.REQUIREMENT_READ,
        ResourceType.REQUIREMENT_VERSION,
        lambda pid: service.get_version(pid, version_id),
    )
    updated = service.transition(
        project_id=project_id, version_id=version_id, target=payload.target
    )
    return RequirementVersionOut.model_validate(updated)
