"""Guardrail endpoints (roadmap phase P11; architecture ADR-009, O.3-O.5, P.2, S).

* ``POST   /auth/sessions`` - open a server-side session for the identity this
  request already proved (ADR-009). The token is returned once, and set as an
  ``httpOnly``, ``SameSite=Strict`` cookie for the demonstration UI.
* ``DELETE /auth/sessions/current`` - revoke the session presented (logout).
* ``GET    /auth/whoami`` - who the server thinks is calling, and the roles read
  from ``project_member`` for this request.
* ``DELETE /projects/{id}`` - delete a project's content (``FR-ADM-006``; P.2):
  the Project Manager only, with the project's name repeated. The answer comes
  only after the deletion has been verified and committed.
* ``GET    /projects/{id}/audit/verify`` - hash-chain verification (O.5; the
  Auditor's ``AUDIT_VERIFY``).
* ``GET    /requirements/{id}/history`` and ``GET /risks/{id}/history`` - replay
  (``FR-AUD-004``; O.3, O.4). Read-only.

Nothing here decides a gate, moves a lifecycle state or reveals the unmasking
map. An id outside the caller's projects is a 404, as for one that does not
exist.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import APIRouter, Cookie, Header, HTTPException, Response, status
from sqlalchemy.orm import Session

from reqpilot.api.dependencies import AppSettings, CurrentActor, DbSession, bearer_token
from reqpilot.api.guardrails_schemas import (
    AuditVerifyOut,
    DeleteProjectIn,
    DeletionOut,
    ReplayOut,
    ReplayStepOut,
    SessionIssuedOut,
    WhoAmIOut,
)
from reqpilot.api.lookup import scan_actor_projects
from reqpilot.config import AppEnv
from reqpilot.domain.enums import Action, ResourceType
from reqpilot.domain.errors import AuthSessionError, ProjectIsolationError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.policy import Actor
from reqpilot.services.audit.replay import AuditViewer, ReplayResult, ReplayService
from reqpilot.services.guardrails.deletion import ProjectDeletionService
from reqpilot.services.guardrails.sessions import SESSION_COOKIE, SESSION_TTL, AuthSessionService

router = APIRouter(prefix="/api/v1", tags=["guardrails"])


# --- sessions (ADR-009) -----------------------------------------------------------------


@router.post("/auth/sessions", response_model=SessionIssuedOut, status_code=201)
def open_session(
    response: Response, session: DbSession, actor: CurrentActor, settings: AppSettings
) -> SessionIssuedOut:
    issued = AuthSessionService(session).issue(uuid.UUID(str(actor.actor_id)))
    response.set_cookie(
        SESSION_COOKIE,
        issued.token,
        httponly=True,
        samesite="strict",
        secure=settings.app_env is AppEnv.PRODUCTION,
        max_age=int(SESSION_TTL.total_seconds()),
    )
    return SessionIssuedOut(
        session_id=issued.session_id,
        user_id=issued.user_id,
        expires_at=issued.expires_at,
        token=issued.token,
    )


@router.delete("/auth/sessions/current", status_code=204)
def close_session(
    response: Response,
    session: DbSession,
    authorization: str | None = Header(default=None),
    reqpilot_session: str | None = Cookie(default=None),
) -> Response:
    token = bearer_token(authorization) or reqpilot_session
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "no session was presented")
    try:
        AuthSessionService(session).revoke(token)
    except AuthSessionError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid session") from None
    response.delete_cookie(SESSION_COOKIE)
    response.status_code = 204
    return response


@router.get("/auth/whoami", response_model=WhoAmIOut)
def whoami(
    actor: CurrentActor,
    authorization: str | None = Header(default=None),
    reqpilot_session: str | None = Cookie(default=None),
) -> WhoAmIOut:
    return WhoAmIOut(
        user_id=uuid.UUID(str(actor.actor_id)),
        authenticated_by=(
            "session" if (bearer_token(authorization) or reqpilot_session) else "development-header"
        ),
        roles_by_project={
            str(pid): sorted(str(r) for r in roles) for pid, roles in actor.roles_by_project.items()
        },
    )


# --- deletion (FR-ADM-006; P.2) ----------------------------------------------------------


@router.delete("/projects/{project_id}", response_model=DeletionOut)
def delete_project(
    project_id: uuid.UUID, body: DeleteProjectIn, session: DbSession, actor: CurrentActor
) -> DeletionOut:
    from reqpilot.graph.builder import forget_memory_threads

    receipt = ProjectDeletionService(session, actor, forget_threads=forget_memory_threads).delete(
        ProjectId(project_id), confirm_name=body.confirm_name
    )
    # Reported only once it is durable: a commit failure is an error, not a success.
    session.commit()
    return DeletionOut(
        project_id=receipt.project_id,
        deleted=True,
        already_deleted=receipt.already_deleted,
        deleted_at=receipt.deleted_at,
        deleted_by=receipt.deleted_by,
        rows_removed=receipt.rows_removed,
        total_removed=receipt.total_removed,
        checkpoint_threads=receipt.checkpoint_threads,
        retained=list(receipt.retained),
    )


# --- audit verification and replay (FR-AUD-004; O.3-O.5) ---------------------------------


@router.get("/projects/{project_id}/audit/verify", response_model=AuditVerifyOut)
def verify_audit(project_id: uuid.UUID, session: DbSession, actor: CurrentActor) -> AuditVerifyOut:
    ok, first_bad, count = AuditViewer(session, actor).verify(ProjectId(project_id))
    return AuditVerifyOut(
        project_id=project_id, intact=ok, first_divergence=first_bad, events=count
    )


def _replay_in_actor_projects(
    actor: Actor, replay: Callable[[ProjectId], ReplayResult]
) -> ReplayResult:
    """Replay the entity in whichever of the caller's projects holds it - searching
    only projects where the caller may read the audit trail (a 404 otherwise)."""

    def attempt(pid: ProjectId) -> ReplayResult | None:
        try:
            return replay(pid)
        except ProjectIsolationError:  # nothing of that id in this project
            return None

    found = scan_actor_projects(actor, Action.AUDIT_READ, ResourceType.AUDIT_EVENT, attempt)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return found[1]


def _replay_out(result: ReplayResult) -> ReplayOut:
    return ReplayOut(
        entity_type=result.entity_type,
        entity_id=result.entity_id,
        project_id=result.project_id,
        complete=result.complete,
        gaps=list(result.gaps),
        chain_ok=result.chain_ok,
        redacted=result.redacted,
        current=result.current,
        reconstructed=result.reconstructed,
        steps=[ReplayStepOut.model_validate(s) for s in result.steps],
    )


def replay_requirement(session: Session, actor: Actor, requirement_id: uuid.UUID) -> ReplayResult:
    service = ReplayService(session, actor)
    return _replay_in_actor_projects(actor, lambda pid: service.requirement(pid, requirement_id))


def replay_risk(session: Session, actor: Actor, risk_id: uuid.UUID) -> ReplayResult:
    service = ReplayService(session, actor)
    return _replay_in_actor_projects(actor, lambda pid: service.risk(pid, risk_id))


@router.get("/requirements/{requirement_id}/history", response_model=ReplayOut)
def requirement_history(
    requirement_id: uuid.UUID, session: DbSession, actor: CurrentActor
) -> ReplayOut:
    return _replay_out(replay_requirement(session, actor, requirement_id))


@router.get("/risks/{risk_id}/history", response_model=ReplayOut)
def risk_history(risk_id: uuid.UUID, session: DbSession, actor: CurrentActor) -> ReplayOut:
    return _replay_out(replay_risk(session, actor, risk_id))
