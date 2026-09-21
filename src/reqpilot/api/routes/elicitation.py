"""Stakeholders, interview sessions and the clarification loop (P4; architecture P, API).

The architecture's endpoints - ``POST /projects/{id}/stakeholders``,
``POST /projects/{id}/sessions``, ``POST /sessions/{id}/answer``,
``GET /sessions/{id}/coverage``, ``GET /projects/{id}/clarifications``,
``POST /clarifications/{id}/answer``, ``POST /clarifications/{id}/dismiss`` - plus
the few P4 needs to be usable: reading a session and its utterances, pause and
resume (``FR-ELI-005``), recording a quality finding and raising a clarification
for it, retrying a failed re-analysis, and the ``FR-ELI-007`` suggestion.

Every handler authorises through the policy (and the services' "only their own
session" rule), and a resource outside the caller's reach is a 404, exactly as
for one that does not exist. The only routes that reach a model - starting or
answering an interview, raising or answering a clarification - do so through the
one gateway; none writes a lifecycle state, an approval or a coverage value
supplied by the client.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from reqpilot.api.dependencies import (
    AppSettings,
    CurrentActor,
    DbSession,
    ElicitationRulesDep,
    ExtractionRulesDep,
    Gateway,
)
from reqpilot.api.elicitation_schemas import (
    AnswerIn,
    ClarificationAnswerIn,
    ClarificationAnswerOut,
    ClarificationOut,
    CoverageOut,
    DismissIn,
    FindingIn,
    FindingOut,
    RaiseClarificationIn,
    SessionIn,
    SessionOut,
    StakeholderIn,
    StakeholderOut,
    SuggestionOut,
    TurnOut,
    UtteranceOut,
)
from reqpilot.api.lookup import require_found
from reqpilot.domain import coverage as tracker
from reqpilot.domain.enums import (
    Action,
    ClarificationStatus,
    InterviewSessionKind,
    ResourceType,
    TopicStatus,
)
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.elicitation import Clarification, InterviewSession
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.graph.clarification_runner import AnswerOutcome, ClarificationRunner
from reqpilot.graph.elicitation_runner import ElicitationRunner, InterviewTurn
from reqpilot.repositories.requirements import RequirementVersionRepository
from reqpilot.rules.elicitation import ElicitationRules
from reqpilot.services.clarification import ClarificationService, IssueView, QualityFindingService
from reqpilot.services.elicitation import (
    CoverageView,
    InterviewSessionService,
    StakeholderService,
    topic_plan,
)

router = APIRouter(prefix="/api/v1", tags=["elicitation"])


def _project(actor: Actor, project_id: uuid.UUID, action: Action) -> ProjectId:
    pid = ProjectId(project_id)
    require(actor, action, ResourceRef(resource_type=ResourceType.PROJECT, project_id=pid))
    return pid


def coverage_out(view: CoverageView) -> CoverageOut:
    summary = view.summary
    return CoverageOut(
        session_id=view.session_id,
        status=view.status,
        current_topic=view.current_topic,
        followups_this_topic=view.followups_this_topic,
        max_followups=view.max_followups,
        applicable=list(summary.applicable),
        covered=list(summary.covered),
        unresolved=list(summary.unresolved),
        in_progress=list(summary.in_progress),
        remaining=list(summary.remaining),
        required_remaining=list(summary.required_remaining),
        topics=view.entries,
    )


def turn_out(turn: InterviewTurn, *, utterance: object | None = None) -> TurnOut:
    return TurnOut(
        session=SessionOut.model_validate(turn.session),
        utterance=UtteranceOut.model_validate(utterance) if utterance is not None else None,
        next_question=UtteranceOut.model_validate(turn.question) if turn.question else None,
        complete=turn.complete,
        coverage=coverage_out(turn.coverage),
    )


def issue_out(view: IssueView) -> ClarificationOut:
    c = view.clarification
    return ClarificationOut(
        id=c.id,
        requirement_version_id=c.requirement_version_id,
        requirement_human_id=view.requirement_human_id,
        version_no=view.version_no,
        statement=view.statement,
        quality_finding_id=c.quality_finding_id,
        finding_type=view.finding.finding_type,
        question=c.question,
        expected_answer_shape=c.expected_answer_shape,
        status=c.status,
        asked_of_stakeholder_id=c.asked_of_stakeholder_id,
        stakeholder_name=view.stakeholder_name,
        assignee_user_id=c.assignee_user_id,
        age_seconds=max(0, int(view.age.total_seconds())),
        created_at=c.created_at,
        answered_at=c.answered_at,
        answer=view.answer,
        dismissed_reason=c.dismissed_reason,
        reanalysis_status=c.reanalysis_status,
        resulting_version_id=c.resulting_version_id,
        resulting_version_no=view.resulting_version_no,
    )


def _session(
    actor: Actor, rules: ElicitationRules, session: DbSession, session_id: uuid.UUID
) -> tuple[ProjectId, InterviewSession]:
    service = InterviewSessionService(session, actor, rules)
    return require_found(
        actor,
        Action.SESSION_READ,
        ResourceType.INTERVIEW_SESSION,
        lambda pid: service.get(pid, session_id),
    )


# --- stakeholders ---------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/stakeholders",
    response_model=StakeholderOut,
    status_code=status.HTTP_201_CREATED,
)
def create_stakeholder(
    project_id: uuid.UUID,
    payload: StakeholderIn,
    session: DbSession,
    actor: CurrentActor,
    rules: ElicitationRulesDep,
) -> StakeholderOut:
    stakeholder = StakeholderService(session, actor, rules).create(
        project_id=_project(actor, project_id, Action.STAKEHOLDER_CREATE),
        name=payload.name,
        stakeholder_role=payload.stakeholder_role,
        authority_level=payload.authority_level,
        user_id=payload.user_id,
    )
    return StakeholderOut.model_validate(stakeholder)


@router.get("/projects/{project_id}/stakeholders", response_model=list[StakeholderOut])
def list_stakeholders(
    project_id: uuid.UUID, session: DbSession, actor: CurrentActor, rules: ElicitationRulesDep
) -> list[StakeholderOut]:
    pid = _project(actor, project_id, Action.STAKEHOLDER_READ)
    return [
        StakeholderOut.model_validate(s)
        for s in StakeholderService(session, actor, rules).list_stakeholders(pid)
    ]


@router.get("/projects/{project_id}/stakeholder-suggestion", response_model=SuggestionOut)
def suggest_stakeholder(
    project_id: uuid.UUID, session: DbSession, actor: CurrentActor, rules: ElicitationRulesDep
) -> SuggestionOut:
    """``FR-ELI-007`` (secondary): the role whose template best fills coverage gaps."""
    pid = _project(actor, project_id, Action.SESSION_READ)
    rows = InterviewSessionService(session, actor, rules).list_sessions(
        pid, kind=InterviewSessionKind.INTERVIEW
    )
    covered = sorted(
        {
            topic
            for row in rows
            for topic, entry in row.topic_coverage.items()
            if entry["status"] == TopicStatus.COVERED
        }
    )
    suggestion = tracker.suggest_next_role(
        covered,
        {t.template_id: (t.stakeholder_role, topic_plan(t)) for t in rules.templates.values()},
    )
    if suggestion is None:
        return SuggestionOut(
            stakeholder_role=None,
            template_id=None,
            gap_topics=[],
            covered_topics=covered,
            basis="every topic of every template is covered",
        )
    return SuggestionOut(
        stakeholder_role=suggestion.stakeholder_role,
        template_id=suggestion.template_id,
        gap_topics=list(suggestion.gap_topics),
        covered_topics=covered,
        basis=(
            "uncovered topics this role's template addresses, weighted 2 when required "
            f"and 1 when optional (score {suggestion.score})"
        ),
    )


# --- sessions ---------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/sessions", response_model=TurnOut, status_code=status.HTTP_201_CREATED
)
def create_session(
    project_id: uuid.UUID,
    payload: SessionIn,
    session: DbSession,
    actor: CurrentActor,
    rules: ElicitationRulesDep,
    gateway: Gateway,
    settings: AppSettings,
) -> TurnOut:
    """``InterviewSession`` + first question (architecture API)."""
    turn = ElicitationRunner(session, gateway, rules, settings=settings).start(
        actor=actor,
        project_id=_project(actor, project_id, Action.SESSION_CREATE),
        stakeholder_id=payload.stakeholder_id,
        template_id=payload.template_id,
        sensitivity=payload.sensitivity,
    )
    return turn_out(turn)


@router.get("/projects/{project_id}/sessions", response_model=list[SessionOut])
def list_sessions(
    project_id: uuid.UUID, session: DbSession, actor: CurrentActor, rules: ElicitationRulesDep
) -> list[SessionOut]:
    pid = _project(actor, project_id, Action.SESSION_READ)
    return [
        SessionOut.model_validate(r)
        for r in InterviewSessionService(session, actor, rules).list_sessions(
            pid, kind=InterviewSessionKind.INTERVIEW
        )
    ]


@router.get("/sessions/{session_id}", response_model=TurnOut)
def get_session(
    session_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    rules: ElicitationRulesDep,
    gateway: Gateway,
    settings: AppSettings,
) -> TurnOut:
    pid, row = _session(actor, rules, session, session_id)
    turn = ElicitationRunner(session, gateway, rules, settings=settings).turn(
        actor=actor, project_id=pid, session_id=row.id
    )
    return turn_out(turn)


@router.post("/sessions/{session_id}/answer", response_model=TurnOut)
def answer_session(
    session_id: uuid.UUID,
    payload: AnswerIn,
    session: DbSession,
    actor: CurrentActor,
    rules: ElicitationRulesDep,
    gateway: Gateway,
    settings: AppSettings,
) -> TurnOut:
    """``{text}`` -> ``{utterance, next_question | complete}`` (architecture API).

    The stakeholder answers for themself; an Analyst may record the answer on
    their behalf (``FR-ELI-005``), which the utterance records as such.
    """
    pid, row = _session(actor, rules, session, session_id)
    pending = row.pending_question_id
    turn = ElicitationRunner(session, gateway, rules, settings=settings).answer(
        actor=actor, project_id=pid, session_id=row.id, text=payload.text
    )
    service = InterviewSessionService(session, actor, rules)
    answered = next(
        (u for u in service.utterances(turn.session) if u.replies_to_id == pending), None
    )
    return turn_out(turn, utterance=answered)


@router.get("/sessions/{session_id}/coverage", response_model=CoverageOut)
def session_coverage(
    session_id: uuid.UUID, session: DbSession, actor: CurrentActor, rules: ElicitationRulesDep
) -> CoverageOut:
    _pid, row = _session(actor, rules, session, session_id)
    return coverage_out(InterviewSessionService(session, actor, rules).coverage(row))


@router.get("/sessions/{session_id}/utterances", response_model=list[UtteranceOut])
def session_utterances(
    session_id: uuid.UUID, session: DbSession, actor: CurrentActor, rules: ElicitationRulesDep
) -> list[UtteranceOut]:
    _pid, row = _session(actor, rules, session, session_id)
    return [
        UtteranceOut.model_validate(u)
        for u in InterviewSessionService(session, actor, rules).utterances(row)
    ]


@router.post("/sessions/{session_id}/pause", response_model=TurnOut)
def pause_session(
    session_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    rules: ElicitationRulesDep,
    gateway: Gateway,
    settings: AppSettings,
) -> TurnOut:
    pid, row = _session(actor, rules, session, session_id)
    turn = ElicitationRunner(session, gateway, rules, settings=settings).pause(
        actor=actor, project_id=pid, session_id=row.id
    )
    return turn_out(turn)


@router.post("/sessions/{session_id}/resume", response_model=TurnOut)
def resume_session(
    session_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    rules: ElicitationRulesDep,
    gateway: Gateway,
    settings: AppSettings,
) -> TurnOut:
    pid, row = _session(actor, rules, session, session_id)
    turn = ElicitationRunner(session, gateway, rules, settings=settings).resume(
        actor=actor, project_id=pid, session_id=row.id
    )
    return turn_out(turn)


# --- quality findings (the minimal P4 interface) ------------------------------------------


@router.post(
    "/requirement-versions/{version_id}/quality-findings",
    response_model=FindingOut,
    status_code=status.HTTP_201_CREATED,
)
def record_finding(
    version_id: uuid.UUID, payload: FindingIn, session: DbSession, actor: CurrentActor
) -> FindingOut:
    """Record a defect by hand. Detecting defects automatically is P5."""
    versions = RequirementVersionRepository(session, actor)
    project_id, version = require_found(
        actor,
        Action.REQUIREMENT_READ,
        ResourceType.REQUIREMENT_VERSION,
        lambda pid: versions.get(pid, version_id),
    )
    finding = QualityFindingService(session, actor).record(
        project_id=project_id,
        version_id=version.id,
        finding_type=payload.finding_type,
        severity=payload.severity,
        rationale=payload.rationale,
        span_quote=payload.span_quote,
    )
    return FindingOut.model_validate(finding)


@router.get("/requirement-versions/{version_id}/quality-findings", response_model=list[FindingOut])
def list_findings(
    version_id: uuid.UUID, session: DbSession, actor: CurrentActor
) -> list[FindingOut]:
    service = QualityFindingService(session, actor)
    versions = RequirementVersionRepository(session, actor)
    project_id, version = require_found(
        actor,
        Action.QUALITY_FINDING_READ,
        ResourceType.REQUIREMENT_VERSION,
        lambda pid: versions.get(pid, version_id),
    )
    return [FindingOut.model_validate(f) for f in service.for_version(project_id, version.id)]


# --- clarifications ------------------------------------------------------------------------


@router.post(
    "/quality-findings/{finding_id}/clarifications",
    response_model=ClarificationOut,
    status_code=status.HTTP_201_CREATED,
)
def raise_clarification(
    finding_id: uuid.UUID,
    payload: RaiseClarificationIn,
    session: DbSession,
    actor: CurrentActor,
    rules: ElicitationRulesDep,
    extraction_rules: ExtractionRulesDep,
    gateway: Gateway,
    settings: AppSettings,
) -> ClarificationOut | JSONResponse:
    """Role #4 proposes the question; code binds and validates it (``FR-CLR-001``)."""
    findings = QualityFindingService(session, actor)
    project_id, finding = require_found(
        actor,
        Action.QUALITY_FINDING_READ,
        ResourceType.QUALITY_FINDING,
        lambda pid: findings.get(pid, finding_id),
    )
    outcome = ClarificationRunner(
        session, gateway, rules, extraction_rules, settings=settings
    ).raise_for_finding(
        actor=actor,
        project_id=project_id,
        finding_id=finding.id,
        asked_of=payload.asked_of_stakeholder_id,
    )
    if outcome.clarification is None:
        # The failed attempt is recorded (the run and its agent runs are kept);
        # nothing was asked.
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": outcome.error, "run_id": str(outcome.run_id)},
        )
    service = ClarificationService(session, actor, rules)
    return issue_out(service.issue(project_id, outcome.clarification))


@router.get("/projects/{project_id}/clarifications", response_model=list[ClarificationOut])
def list_clarifications(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    rules: ElicitationRulesDep,
    status_filter: ClarificationStatus | None = None,
) -> list[ClarificationOut]:
    """The open-issues list (``FR-CLR-002``): open first, oldest first."""
    pid = _project(actor, project_id, Action.CLARIFICATION_READ)
    return [
        issue_out(v)
        for v in ClarificationService(session, actor, rules).issues(pid, status=status_filter)
    ]


def _clarification(
    actor: Actor, rules: ElicitationRules, session: DbSession, clarification_id: uuid.UUID
) -> tuple[ProjectId, Clarification]:
    service = ClarificationService(session, actor, rules)
    return require_found(
        actor,
        Action.CLARIFICATION_READ,
        ResourceType.CLARIFICATION,
        lambda pid: service.get(pid, clarification_id),
    )


@router.get("/clarifications/{clarification_id}", response_model=ClarificationOut)
def get_clarification(
    clarification_id: uuid.UUID, session: DbSession, actor: CurrentActor, rules: ElicitationRulesDep
) -> ClarificationOut:
    project_id, clarification = _clarification(actor, rules, session, clarification_id)
    service = ClarificationService(session, actor, rules)
    return issue_out(service.issue(project_id, clarification))


def _answer_out(
    actor: Actor,
    rules: ElicitationRules,
    session: DbSession,
    project_id: ProjectId,
    outcome: AnswerOutcome,
) -> ClarificationAnswerOut:
    service = ClarificationService(session, actor, rules)
    return ClarificationAnswerOut(
        clarification=issue_out(service.issue(project_id, outcome.clarification)),
        reanalysis_run_id=outcome.reanalysis.run_id if outcome.reanalysis else None,
        error=outcome.error,
    )


@router.post("/clarifications/{clarification_id}/answer", response_model=ClarificationAnswerOut)
def answer_clarification(
    clarification_id: uuid.UUID,
    payload: ClarificationAnswerIn,
    session: DbSession,
    actor: CurrentActor,
    rules: ElicitationRulesDep,
    extraction_rules: ExtractionRulesDep,
    gateway: Gateway,
    settings: AppSettings,
) -> ClarificationAnswerOut:
    """``{answer}`` -> the answer is recorded and triggers re-analysis (``FR-CLR-003``)."""
    project_id, clarification = _clarification(actor, rules, session, clarification_id)
    outcome = ClarificationRunner(
        session, gateway, rules, extraction_rules, settings=settings
    ).answer(
        actor=actor,
        project_id=project_id,
        clarification_id=clarification.id,
        text=payload.answer,
    )
    return _answer_out(actor, rules, session, project_id, outcome)


@router.post("/clarifications/{clarification_id}/reanalyse", response_model=ClarificationAnswerOut)
def reanalyse_clarification(
    clarification_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    rules: ElicitationRulesDep,
    extraction_rules: ExtractionRulesDep,
    gateway: Gateway,
    settings: AppSettings,
) -> ClarificationAnswerOut:
    """Retry a failed re-analysis (an Analyst). The recorded answer stands."""
    project_id, clarification = _clarification(actor, rules, session, clarification_id)
    outcome = ClarificationRunner(
        session, gateway, rules, extraction_rules, settings=settings
    ).reanalyse(
        actor=actor,
        project_id=project_id,
        clarification_id=clarification.id,
    )
    return _answer_out(actor, rules, session, project_id, outcome)


@router.post("/clarifications/{clarification_id}/dismiss", response_model=ClarificationOut)
def dismiss_clarification(
    clarification_id: uuid.UUID,
    payload: DismissIn,
    session: DbSession,
    actor: CurrentActor,
    rules: ElicitationRulesDep,
) -> ClarificationOut:
    """``{reason}`` -> dismissed (``FR-CLR-004``). The requirement is not changed."""
    project_id, clarification = _clarification(actor, rules, session, clarification_id)
    service = ClarificationService(session, actor, rules)
    dismissed = service.dismiss(
        project_id=project_id,
        clarification_id=clarification.id,
        reason=payload.reason,
    )
    return issue_out(service.issue(project_id, dismissed))
