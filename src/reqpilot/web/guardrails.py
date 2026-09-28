"""Demonstration UI for the P11 guardrails (architecture ADR-008, O.3-O.5, P.2, ADR-009).

* ``/ui/requirements/{id}/history`` and ``/ui/risks/{id}/history`` - replay:
  the **current record** and the **reconstructed history** side by side, and a
  banner whenever the history cannot be shown as complete (with every gap).
* ``/ui/projects/{id}/delete`` - the Project Manager's deletion, confirmed by
  typing the project's name; the result page is shown only after the deletion
  was verified and committed.
* ``/ui/login`` and ``/ui/logout`` - a server-side session cookie for the
  browser (ADR-009). Obtaining one still rests on the development identity
  claim; password login and MFA are not implemented.

Every page is read through the same services as the API; hiding a control is a
convenience, never the control.
"""

from __future__ import annotations

import contextlib
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from reqpilot.api.dependencies import AppSettings, CurrentActor, DbSession
from reqpilot.api.routes.guardrails import replay_requirement, replay_risk
from reqpilot.config import AppEnv
from reqpilot.domain.enums import Role
from reqpilot.domain.errors import AuthSessionError, DeletionError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.identity import Project
from reqpilot.services.guardrails.deletion import ProjectDeletionService
from reqpilot.services.guardrails.sessions import SESSION_COOKIE, SESSION_TTL, AuthSessionService
from reqpilot.web.router import TEMPLATES

router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)


# --- replay ----------------------------------------------------------------------------


@router.get("/requirements/{requirement_id}/history", response_class=HTMLResponse)
def requirement_history(
    requirement_id: uuid.UUID, request: Request, session: DbSession, actor: CurrentActor
) -> HTMLResponse:
    result = replay_requirement(session, actor, requirement_id)
    return TEMPLATES.TemplateResponse(
        request, "history.html", {"result": result, "actor": actor, "title": "Requirement"}
    )


@router.get("/risks/{risk_id}/history", response_class=HTMLResponse)
def risk_history(
    risk_id: uuid.UUID, request: Request, session: DbSession, actor: CurrentActor
) -> HTMLResponse:
    result = replay_risk(session, actor, risk_id)
    return TEMPLATES.TemplateResponse(
        request, "history.html", {"result": result, "actor": actor, "title": "Risk"}
    )


# --- deletion (FR-ADM-006) ------------------------------------------------------------------


def _project_for_manager(session: DbSession, actor: CurrentActor, project_id: uuid.UUID) -> Project:
    project = session.get(Project, project_id)
    if project is None or ProjectId(project_id) not in actor.roles_by_project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return project


@router.get("/projects/{project_id}/delete", response_class=HTMLResponse)
def delete_form(
    project_id: uuid.UUID, request: Request, session: DbSession, actor: CurrentActor
) -> HTMLResponse:
    project = _project_for_manager(session, actor, project_id)
    may_delete = Role.PROJECT_MANAGER in actor.roles_in(ProjectId(project_id))
    return TEMPLATES.TemplateResponse(
        request,
        "project_delete.html",
        {
            "project": project,
            "actor": actor,
            "may_delete": may_delete,
            "error": request.query_params.get("error"),
        },
    )


@router.post("/projects/{project_id}/delete", response_model=None)
def delete_project(
    project_id: uuid.UUID,
    request: Request,
    session: DbSession,
    actor: CurrentActor,
    confirm_name: str = Form(""),
) -> HTMLResponse | RedirectResponse:
    from reqpilot.graph.builder import forget_memory_threads

    _project_for_manager(session, actor, project_id)
    try:
        receipt = ProjectDeletionService(
            session, actor, forget_threads=forget_memory_threads
        ).delete(ProjectId(project_id), confirm_name=confirm_name)
    except DeletionError as exc:
        return RedirectResponse(
            f"/ui/projects/{project_id}/delete?error={quote(str(exc))}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    # The result page is rendered only after the deletion is durable.
    session.commit()
    return TEMPLATES.TemplateResponse(
        request, "project_deleted.html", {"receipt": receipt, "actor": actor}
    )


# --- sessions (ADR-009) ----------------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, settings: AppSettings) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request,
        "login.html",
        {"actor": None, "production": settings.app_env is AppEnv.PRODUCTION},
    )


@router.post("/login", response_model=None)
def login(session: DbSession, settings: AppSettings, user_id: str = Form("")) -> RedirectResponse:
    """Development only, like the identity header it stands in for."""
    if settings.app_env is AppEnv.PRODUCTION:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="no login")
    try:
        issued = AuthSessionService(session).issue(uuid.UUID(user_id.strip()))
    except (ValueError, AuthSessionError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="unknown actor"
        ) from None
    response = RedirectResponse("/ui/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE,
        issued.token,
        httponly=True,
        samesite="strict",
        max_age=int(SESSION_TTL.total_seconds()),
    )
    return response


@router.post("/logout", response_model=None)
def logout(request: Request, session: DbSession) -> RedirectResponse:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with contextlib.suppress(AuthSessionError):  # already invalid: nothing to revoke
            AuthSessionService(session).revoke(token)
    response = RedirectResponse("/ui/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE)
    return response
