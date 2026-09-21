"""Module M1 - the interview console and the open-issues page (P4; architecture ADR-008).

Deliberately plain - correctness over polish. What these pages must make obvious:

* **The interview is adaptive, the coverage deterministic.** The console shows
  the pending question, the stakeholder, live coverage and the remaining topics
  (``FR-ELI-006``). Questions come from the model; which topic comes next, and
  when the interview ends, come from the coverage tracker.
* **Answers are the stakeholder's.** An analyst can type an answer on their
  behalf (``FR-ELI-005``); the transcript shows who typed it.
* **Answering approves nothing.** Neither an interview answer nor a clarification
  answer approves, validates or baselines a requirement. A clarification answer
  creates a new requirement *version*, which starts its own lifecycle.

As everywhere in the UI, hidden buttons are a convenience; every action goes
through the same services and runners as the API, which authorise through
``policy.can``.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from reqpilot.api.dependencies import (
    AppSettings,
    CurrentActor,
    DbSession,
    ElicitationRulesDep,
    ExtractionRulesDep,
    Gateway,
)
from reqpilot.api.lookup import require_found
from reqpilot.domain import coverage as tracker
from reqpilot.domain.classification import display_label
from reqpilot.domain.enums import (
    Action,
    DataSensitivity,
    FindingSeverity,
    InterviewSessionKind,
    QualityFindingType,
    ResourceType,
    StakeholderAuthority,
    TopicStatus,
)
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.elicitation import Clarification, InterviewSession
from reqpilot.domain.models.identity import Project
from reqpilot.domain.policy import Actor, ResourceRef, can, require
from reqpilot.graph.clarification_runner import ClarificationRunner
from reqpilot.graph.elicitation_runner import ElicitationRunner
from reqpilot.graph.runner import AnalysisRunner
from reqpilot.repositories.requirements import RequirementRepository, RequirementVersionRepository
from reqpilot.rules.elicitation import ElicitationRules
from reqpilot.services.clarification import RAISE_PATHS, ClarificationService, QualityFindingService
from reqpilot.services.elicitation import (
    InterviewSessionService,
    StakeholderService,
    topic_plan,
)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
TEMPLATES.env.globals["label"] = display_label

router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)

NOT_APPROVAL = (
    "Answering approves nothing. Requirements are approved only at gate G1, by an "
    "Analyst and a Compliance Officer, on the approvals page. A clarification answer "
    "creates a new requirement version, which starts its own lifecycle."
)


def _may(actor: Actor, action: Action, project_id: ProjectId) -> bool:
    return can(
        actor, action, ResourceRef(resource_type=ResourceType.PROJECT, project_id=project_id)
    ).allowed


def _project(session: DbSession, actor: Actor, project_id: uuid.UUID, action: Action) -> Project:
    pid = ProjectId(project_id)
    require(actor, action, ResourceRef(resource_type=ResourceType.PROJECT, project_id=pid))
    project = session.get(Project, pid)
    if project is None:  # pragma: no cover - membership implies existence
        raise LookupError("project not found")
    return project


def _see_other(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# Interviews
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/interviews", response_class=HTMLResponse)
def interviews_page(
    project_id: uuid.UUID,
    request: Request,
    session: DbSession,
    actor: CurrentActor,
    rules: ElicitationRulesDep,
    gateway: Gateway,
) -> HTMLResponse:
    project = _project(session, actor, project_id, Action.SESSION_READ)
    pid = ProjectId(project.id)
    sessions = InterviewSessionService(session, actor, rules).list_sessions(
        pid, kind=InterviewSessionKind.INTERVIEW
    )
    stakeholders = StakeholderService(session, actor, rules).list_stakeholders(pid)
    names = {s.id: s.name for s in stakeholders}
    covered = sorted(
        {
            t
            for row in sessions
            for t, e in row.topic_coverage.items()
            if e["status"] == TopicStatus.COVERED
        }
    )
    suggestion = tracker.suggest_next_role(
        covered,
        {t.template_id: (t.stakeholder_role, topic_plan(t)) for t in rules.templates.values()},
    )
    return TEMPLATES.TemplateResponse(
        request,
        "interviews.html",
        {
            "actor": actor,
            "project": project,
            "stakeholders": stakeholders,
            "sessions": [
                (row, names.get(row.stakeholder_id, "?"), tracker.summarise(row.topic_coverage))
                for row in sessions
            ],
            "roles": rules.stakeholder_roles,
            "authorities": list(StakeholderAuthority),
            "sensitivities": list(DataSensitivity),
            "templates": rules.templates,
            "suggestion": suggestion,
            "may_create": _may(actor, Action.STAKEHOLDER_CREATE, pid),
            "may_start": _may(actor, Action.SESSION_CREATE, pid),
            "may_extract": _may(actor, Action.RUN_START, pid),
            "provider": gateway.provider_name,
            "provider_is_model": gateway.is_model,
        },
    )


@router.post("/projects/{project_id}/stakeholders")
def add_stakeholder(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    rules: ElicitationRulesDep,
    name: str = Form(...),
    stakeholder_role: str = Form(...),
    authority_level: StakeholderAuthority = Form(StakeholderAuthority.CONTRIBUTOR),
    user_id: str = Form(""),
) -> RedirectResponse:
    StakeholderService(session, actor, rules).create(
        project_id=ProjectId(project_id),
        name=name,
        stakeholder_role=stakeholder_role,
        authority_level=authority_level,
        user_id=uuid.UUID(user_id) if user_id.strip() else None,
    )
    return _see_other(f"/ui/projects/{project_id}/interviews")


@router.post("/projects/{project_id}/sessions")
def start_session(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    rules: ElicitationRulesDep,
    gateway: Gateway,
    settings: AppSettings,
    stakeholder_id: uuid.UUID = Form(...),
    sensitivity: DataSensitivity = Form(DataSensitivity.UNCLASSIFIED),
) -> RedirectResponse:
    turn = ElicitationRunner(session, gateway, rules, settings=settings).start(
        actor=actor,
        project_id=ProjectId(project_id),
        stakeholder_id=stakeholder_id,
        sensitivity=sensitivity,
    )
    return _see_other(f"/ui/sessions/{turn.session.id}")


def _found_session(
    session: DbSession, actor: Actor, rules: ElicitationRules, session_id: uuid.UUID
) -> tuple[ProjectId, InterviewSession]:
    service = InterviewSessionService(session, actor, rules)
    return require_found(
        actor,
        Action.SESSION_READ,
        ResourceType.INTERVIEW_SESSION,
        lambda pid: service.get(pid, session_id),
    )


@router.get("/sessions/{session_id}", response_class=HTMLResponse)
def interview_console(
    session_id: uuid.UUID,
    request: Request,
    session: DbSession,
    actor: CurrentActor,
    rules: ElicitationRulesDep,
    gateway: Gateway,
    settings: AppSettings,
) -> HTMLResponse:
    project_id, row = _found_session(session, actor, rules, session_id)
    turn = ElicitationRunner(session, gateway, rules, settings=settings).turn(
        actor=actor, project_id=project_id, session_id=row.id
    )
    service = InterviewSessionService(session, actor, rules)
    project = session.get(Project, project_id)
    template = service.template(row)
    return TEMPLATES.TemplateResponse(
        request,
        "interview.html",
        {
            "actor": actor,
            "project": project,
            "row": row,
            "turn": turn,
            "stakeholder": service.stakeholder(row),
            "template": template,
            "topics": rules.topics,
            "coverage": turn.coverage,
            "utterances": service.utterances(row),
            "may_answer": _may(actor, Action.SESSION_ANSWER, project_id),
            "may_manage": _may(actor, Action.SESSION_MANAGE, project_id),
            "may_extract": _may(actor, Action.RUN_START, project_id),
            "not_approval": NOT_APPROVAL,
        },
    )


@router.post("/sessions/{session_id}/answer")
def answer(
    session_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    rules: ElicitationRulesDep,
    gateway: Gateway,
    settings: AppSettings,
    text: str = Form(...),
) -> RedirectResponse:
    project_id, row = _found_session(session, actor, rules, session_id)
    ElicitationRunner(session, gateway, rules, settings=settings).answer(
        actor=actor, project_id=project_id, session_id=row.id, text=text
    )
    return _see_other(f"/ui/sessions/{session_id}")


@router.post("/sessions/{session_id}/pause")
def pause(
    session_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    rules: ElicitationRulesDep,
    gateway: Gateway,
    settings: AppSettings,
) -> RedirectResponse:
    project_id, row = _found_session(session, actor, rules, session_id)
    ElicitationRunner(session, gateway, rules, settings=settings).pause(
        actor=actor, project_id=project_id, session_id=row.id
    )
    return _see_other(f"/ui/sessions/{session_id}")


@router.post("/sessions/{session_id}/resume")
def resume(
    session_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    rules: ElicitationRulesDep,
    gateway: Gateway,
    settings: AppSettings,
) -> RedirectResponse:
    project_id, row = _found_session(session, actor, rules, session_id)
    ElicitationRunner(session, gateway, rules, settings=settings).resume(
        actor=actor, project_id=project_id, session_id=row.id
    )
    return _see_other(f"/ui/sessions/{session_id}")


@router.post("/sessions/{session_id}/extract")
def extract_session(
    session_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    rules: ElicitationRulesDep,
    extraction_rules: ExtractionRulesDep,
    gateway: Gateway,
    settings: AppSettings,
    domain: str = Form("LOAN"),
) -> RedirectResponse:
    """P3 extraction and classification over this interview's answers."""
    project_id, row = _found_session(session, actor, rules, session_id)
    summary = AnalysisRunner(session, gateway, extraction_rules, settings=settings).extract(
        actor=actor, project_id=project_id, session_ids=[row.id], domain=domain
    )
    return _see_other(f"/ui/runs/{summary.run_id}")


# ---------------------------------------------------------------------------
# Open issues: clarifications
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/clarifications", response_class=HTMLResponse)
def clarifications_page(
    project_id: uuid.UUID,
    request: Request,
    session: DbSession,
    actor: CurrentActor,
    rules: ElicitationRulesDep,
) -> HTMLResponse:
    project = _project(session, actor, project_id, Action.CLARIFICATION_READ)
    pid = ProjectId(project.id)
    raisable = []
    if _may(actor, Action.CLARIFICATION_RAISE, pid):
        requirements = RequirementRepository(session, actor)
        versions = RequirementVersionRepository(session, actor)
        for requirement in requirements.list_for_project(pid):
            current = (
                versions.get(pid, requirement.current_version_id)
                if requirement.current_version_id
                else None
            )
            if current is not None and current.state in RAISE_PATHS:
                raisable.append((requirement, current))
    return TEMPLATES.TemplateResponse(
        request,
        "clarifications.html",
        {
            "actor": actor,
            "project": project,
            "issues": ClarificationService(session, actor, rules).issues(pid),
            "raisable": raisable,
            "stakeholders": StakeholderService(session, actor, rules).list_stakeholders(pid),
            "finding_types": list(QualityFindingType),
            "severities": list(FindingSeverity),
            "may_raise": _may(actor, Action.CLARIFICATION_RAISE, pid),
            "may_answer": _may(actor, Action.CLARIFICATION_ANSWER, pid),
            "may_dismiss": _may(actor, Action.CLARIFICATION_DISMISS, pid),
            "not_approval": NOT_APPROVAL,
        },
    )


@router.post("/projects/{project_id}/clarifications")
def raise_clarification(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    rules: ElicitationRulesDep,
    extraction_rules: ExtractionRulesDep,
    gateway: Gateway,
    settings: AppSettings,
    version_id: uuid.UUID = Form(...),
    finding_type: QualityFindingType = Form(...),
    severity: FindingSeverity = Form(FindingSeverity.MEDIUM),
    rationale: str = Form(...),
    span_quote: str = Form(""),
    asked_of: uuid.UUID = Form(...),
) -> RedirectResponse:
    """Record the finding (P4's minimal interface), then raise its clarification."""
    pid = ProjectId(project_id)
    finding = QualityFindingService(session, actor).record(
        project_id=pid,
        version_id=version_id,
        finding_type=finding_type,
        severity=severity,
        rationale=rationale,
        span_quote=span_quote or None,
    )
    ClarificationRunner(
        session, gateway, rules, extraction_rules, settings=settings
    ).raise_for_finding(actor=actor, project_id=pid, finding_id=finding.id, asked_of=asked_of)
    return _see_other(f"/ui/projects/{project_id}/clarifications")


def _found_clarification(
    session: DbSession, actor: Actor, rules: ElicitationRules, clarification_id: uuid.UUID
) -> tuple[ProjectId, Clarification]:
    service = ClarificationService(session, actor, rules)
    return require_found(
        actor,
        Action.CLARIFICATION_READ,
        ResourceType.CLARIFICATION,
        lambda pid: service.get(pid, clarification_id),
    )


@router.post("/clarifications/{clarification_id}/answer")
def answer_clarification(
    clarification_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    rules: ElicitationRulesDep,
    extraction_rules: ExtractionRulesDep,
    gateway: Gateway,
    settings: AppSettings,
    answer: str = Form(...),
) -> RedirectResponse:
    project_id, clarification = _found_clarification(session, actor, rules, clarification_id)
    ClarificationRunner(session, gateway, rules, extraction_rules, settings=settings).answer(
        actor=actor, project_id=project_id, clarification_id=clarification.id, text=answer
    )
    return _see_other(f"/ui/projects/{project_id}/clarifications")


@router.post("/clarifications/{clarification_id}/reanalyse")
def reanalyse_clarification(
    clarification_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    rules: ElicitationRulesDep,
    extraction_rules: ExtractionRulesDep,
    gateway: Gateway,
    settings: AppSettings,
) -> RedirectResponse:
    project_id, clarification = _found_clarification(session, actor, rules, clarification_id)
    ClarificationRunner(session, gateway, rules, extraction_rules, settings=settings).reanalyse(
        actor=actor, project_id=project_id, clarification_id=clarification.id
    )
    return _see_other(f"/ui/projects/{project_id}/clarifications")


@router.post("/clarifications/{clarification_id}/dismiss")
def dismiss_clarification(
    clarification_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    rules: ElicitationRulesDep,
    reason: str = Form(...),
) -> RedirectResponse:
    project_id, clarification = _found_clarification(session, actor, rules, clarification_id)
    ClarificationService(session, actor, rules).dismiss(
        project_id=project_id, clarification_id=clarification.id, reason=reason
    )
    return _see_other(f"/ui/projects/{project_id}/clarifications")
