"""Quality analysis and conflict detection (P5; architecture P, API; C.3 nodes 6-8).

The architecture's endpoint - ``GET /projects/{id}/conflicts`` ("conflicts with
both sides") - plus what P5 needs to be usable: starting a quality run, reading
and closing findings, reviewing, resolving (G4) and dismissing conflicts,
routing a conflict into the P4 clarification loop, and the project glossary.

Every handler authorises through the policy; a resource outside the caller's
reach is a 404, exactly as for one that does not exist. Only the quality run and
the conflict clarification reach a model, and only through the one gateway. No
handler accepts a severity, a detector, a class, a lifecycle state or an
approval from the client.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from reqpilot.api.dependencies import (
    AppSettings,
    CurrentActor,
    DbSession,
    ElicitationRulesDep,
    Embedder,
    ExtractionRulesDep,
    Gateway,
    QualityRulesDep,
)
from reqpilot.api.elicitation_schemas import ClarificationOut
from reqpilot.api.lookup import require_found
from reqpilot.api.quality_schemas import (
    ConflictClarificationIn,
    ConflictOut,
    ConflictSideOut,
    GlossaryTermIn,
    GlossaryTermOut,
    QualityFindingDetailOut,
    QualityRunIn,
    QualityRunOut,
    ReasonIn,
    ResolveConflictIn,
)
from reqpilot.api.routes.elicitation import issue_out
from reqpilot.domain.enums import (
    Action,
    ConflictStatus,
    QualityFindingStatus,
    QualityFindingType,
    ResourceType,
)
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.elicitation import QualityFinding
from reqpilot.domain.models.quality import Conflict
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.graph.clarification_runner import ClarificationRunner
from reqpilot.graph.runner import AnalysisRunner
from reqpilot.repositories.requirements import RequirementRepository, RequirementVersionRepository
from reqpilot.rules.quality import QualityRules
from reqpilot.services.clarification import ClarificationService
from reqpilot.services.quality import ConflictService, FindingReviewService, GlossaryService

router = APIRouter(prefix="/api/v1", tags=["quality"])


def _project(actor: Actor, project_id: uuid.UUID, action: Action) -> ProjectId:
    pid = ProjectId(project_id)
    require(actor, action, ResourceRef(resource_type=ResourceType.PROJECT, project_id=pid))
    return pid


class _Versions:
    """Human ids and statements for the versions a response names."""

    def __init__(self, session: Session, actor: Actor, project_id: ProjectId) -> None:
        self._versions = RequirementVersionRepository(session, actor)
        self._requirements = RequirementRepository(session, actor)
        self._project_id = project_id

    def describe(self, version_id: uuid.UUID) -> tuple[str | None, int | None, str | None]:
        version = self._versions.get(self._project_id, version_id)
        if version is None:
            return None, None, None
        requirement = self._requirements.get(self._project_id, version.requirement_id)
        return (
            (requirement.human_id if requirement else None),
            version.version_no,
            version.statement,
        )


def finding_out(
    finding: QualityFinding, versions: _Versions, rules: QualityRules
) -> QualityFindingDetailOut:
    human_id, version_no, _statement = versions.describe(finding.requirement_version_id)
    return QualityFindingDetailOut(
        id=finding.id,
        requirement_version_id=finding.requirement_version_id,
        requirement_human_id=human_id,
        version_no=version_no,
        finding_type=finding.finding_type,
        severity=finding.severity,
        review_signal=finding.review_signal,
        review_priority=rules.priority_of(finding.review_signal),
        rationale=finding.rationale,
        span_quote=finding.span_quote,
        evidence=list(finding.evidence or []),
        related_version_id=finding.related_version_id,
        detected_by=finding.detected_by,
        rule_id=finding.rule_id,
        graph_run_id=finding.graph_run_id,
        agent_run_id=finding.agent_run_id,
        status=finding.status,
        resolution_reason=finding.resolution_reason,
        resolved_by=finding.resolved_by,
        resolved_at=finding.resolved_at,
        created_at=finding.created_at,
    )


def conflict_out(conflict: Conflict, versions: _Versions, rules: QualityRules) -> ConflictOut:
    def side(version_id: uuid.UUID, evidence: str, stakeholder: str | None) -> ConflictSideOut:
        human_id, version_no, statement = versions.describe(version_id)
        return ConflictSideOut(
            version_id=version_id,
            requirement_human_id=human_id,
            version_no=version_no,
            statement=statement,
            evidence=evidence,
            stakeholder=stakeholder,
        )

    return ConflictOut(
        id=conflict.id,
        conflict_class=conflict.conflict_class,
        kind=conflict.kind,
        severity=conflict.severity,
        review_signal=conflict.review_signal,
        review_priority=rules.priority_of(conflict.review_signal),
        rationale=conflict.rationale,
        a=side(conflict.version_a_id, conflict.evidence_a, conflict.stakeholder_a),
        b=side(conflict.version_b_id, conflict.evidence_b, conflict.stakeholder_b),
        involves_stakeholder_disagreement=conflict.involves_stakeholder_disagreement,
        detected_by=conflict.detected_by,
        rule_id=conflict.rule_id,
        graph_run_id=conflict.graph_run_id,
        agent_run_id=conflict.agent_run_id,
        status=conflict.status,
        resolution=conflict.resolution,
        resolution_reason=conflict.resolution_reason,
        resolved_by=conflict.resolved_by,
        resolved_at=conflict.resolved_at,
        created_at=conflict.created_at,
    )


# --- quality runs ------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/quality-runs",
    response_model=QualityRunOut,
    status_code=status.HTTP_201_CREATED,
)
def start_quality_run(
    project_id: uuid.UUID,
    payload: QualityRunIn,
    session: DbSession,
    actor: CurrentActor,
    gateway: Gateway,
    extraction_rules: ExtractionRulesDep,
    quality_rules: QualityRulesDep,
    embedder: Embedder,
    settings: AppSettings,
) -> QualityRunOut:
    """Quality analysis and conflict detection (C.3 nodes 6-8). Analyst only (RUN_START)."""
    summary = AnalysisRunner(
        session,
        gateway,
        extraction_rules,
        settings=settings,
        quality_rules=quality_rules,
        embedder=embedder,
    ).analyse_quality(
        actor=actor,
        project_id=_project(actor, project_id, Action.RUN_START),
        version_ids=payload.version_ids,
        semantic=payload.semantic,
    )
    return QualityRunOut(
        run_id=summary.run_id,
        status=summary.status,
        quality_finding_ids=list(summary.quality_finding_ids),
        conflict_ids=list(summary.conflict_ids),
        semantic_failures=summary.semantic_failures,
        provider_calls=summary.provider_calls,
        tokens_in=summary.tokens_in,
        tokens_out=summary.tokens_out,
        errors=list(summary.errors),
    )


# --- findings -----------------------------------------------------------------------------


@router.get("/projects/{project_id}/quality-findings", response_model=list[QualityFindingDetailOut])
def list_project_findings(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    rules: QualityRulesDep,
    status_filter: QualityFindingStatus | None = None,
    finding_type: QualityFindingType | None = None,
) -> list[QualityFindingDetailOut]:
    pid = _project(actor, project_id, Action.QUALITY_FINDING_READ)
    versions = _Versions(session, actor, pid)
    return [
        finding_out(f, versions, rules)
        for f in FindingReviewService(session, actor).list_for_project(
            pid, status=status_filter, finding_type=finding_type
        )
    ]


def _finding(
    session: DbSession, actor: Actor, finding_id: uuid.UUID
) -> tuple[ProjectId, QualityFinding]:
    service = FindingReviewService(session, actor)
    return require_found(
        actor,
        Action.QUALITY_FINDING_READ,
        ResourceType.QUALITY_FINDING,
        lambda pid: service.get(pid, finding_id),
    )


@router.get("/quality-findings/{finding_id}", response_model=QualityFindingDetailOut)
def get_finding(
    finding_id: uuid.UUID, session: DbSession, actor: CurrentActor, rules: QualityRulesDep
) -> QualityFindingDetailOut:
    pid, finding = _finding(session, actor, finding_id)
    return finding_out(finding, _Versions(session, actor, pid), rules)


@router.post("/quality-findings/{finding_id}/resolve", response_model=QualityFindingDetailOut)
def resolve_finding(
    finding_id: uuid.UUID,
    payload: ReasonIn,
    session: DbSession,
    actor: CurrentActor,
    rules: QualityRulesDep,
) -> QualityFindingDetailOut:
    pid, finding = _finding(session, actor, finding_id)
    closed = FindingReviewService(session, actor).resolve(pid, finding.id, payload.reason)
    return finding_out(closed, _Versions(session, actor, pid), rules)


@router.post("/quality-findings/{finding_id}/dismiss", response_model=QualityFindingDetailOut)
def dismiss_finding(
    finding_id: uuid.UUID,
    payload: ReasonIn,
    session: DbSession,
    actor: CurrentActor,
    rules: QualityRulesDep,
) -> QualityFindingDetailOut:
    pid, finding = _finding(session, actor, finding_id)
    closed = FindingReviewService(session, actor).dismiss(pid, finding.id, payload.reason)
    return finding_out(closed, _Versions(session, actor, pid), rules)


# --- conflicts ------------------------------------------------------------------------------


@router.get("/projects/{project_id}/conflicts", response_model=list[ConflictOut])
def list_conflicts(
    project_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    rules: QualityRulesDep,
    status_filter: ConflictStatus | None = None,
) -> list[ConflictOut]:
    """Architecture API: conflicts with both sides."""
    pid = _project(actor, project_id, Action.CONFLICT_READ)
    versions = _Versions(session, actor, pid)
    return [
        conflict_out(c, versions, rules)
        for c in ConflictService(session, actor).list_for_project(pid, status=status_filter)
    ]


def _conflict(
    session: DbSession, actor: Actor, conflict_id: uuid.UUID
) -> tuple[ProjectId, Conflict]:
    service = ConflictService(session, actor)
    return require_found(
        actor,
        Action.CONFLICT_READ,
        ResourceType.CONFLICT,
        lambda pid: service.get(pid, conflict_id),
    )


@router.get("/conflicts/{conflict_id}", response_model=ConflictOut)
def get_conflict(
    conflict_id: uuid.UUID, session: DbSession, actor: CurrentActor, rules: QualityRulesDep
) -> ConflictOut:
    pid, conflict = _conflict(session, actor, conflict_id)
    return conflict_out(conflict, _Versions(session, actor, pid), rules)


@router.post("/conflicts/{conflict_id}/review", response_model=ConflictOut)
def review_conflict(
    conflict_id: uuid.UUID, session: DbSession, actor: CurrentActor, rules: QualityRulesDep
) -> ConflictOut:
    pid, conflict = _conflict(session, actor, conflict_id)
    reviewed = ConflictService(session, actor).review(pid, conflict.id)
    return conflict_out(reviewed, _Versions(session, actor, pid), rules)


@router.post("/conflicts/{conflict_id}/resolve", response_model=ConflictOut)
def resolve_conflict(
    conflict_id: uuid.UUID,
    payload: ResolveConflictIn,
    session: DbSession,
    actor: CurrentActor,
    rules: QualityRulesDep,
) -> ConflictOut:
    """The G4 decision (``FR-CNF-005``): a human's, with a reason."""
    pid, conflict = _conflict(session, actor, conflict_id)
    resolved = ConflictService(session, actor).resolve(
        pid,
        conflict.id,
        resolution=payload.resolution,
        reason=payload.reason,
        withdraw_other=payload.withdraw_other,
    )
    return conflict_out(resolved, _Versions(session, actor, pid), rules)


@router.post("/conflicts/{conflict_id}/dismiss", response_model=ConflictOut)
def dismiss_conflict(
    conflict_id: uuid.UUID,
    payload: ReasonIn,
    session: DbSession,
    actor: CurrentActor,
    rules: QualityRulesDep,
) -> ConflictOut:
    pid, conflict = _conflict(session, actor, conflict_id)
    dismissed = ConflictService(session, actor).dismiss(pid, conflict.id, payload.reason)
    return conflict_out(dismissed, _Versions(session, actor, pid), rules)


@router.post(
    "/conflicts/{conflict_id}/clarifications",
    response_model=ClarificationOut,
    status_code=status.HTTP_201_CREATED,
)
def clarify_conflict(
    conflict_id: uuid.UUID,
    payload: ConflictClarificationIn,
    session: DbSession,
    actor: CurrentActor,
    elicitation_rules: ElicitationRulesDep,
    extraction_rules: ExtractionRulesDep,
    gateway: Gateway,
    settings: AppSettings,
) -> ClarificationOut | JSONResponse:
    """Route a conflict into the P4 clarification loop, on the side chosen (FR-QAL-007)."""
    pid, conflict = _conflict(session, actor, conflict_id)
    finding = ConflictService(session, actor).clarification_finding(
        pid, conflict.id, side=payload.side
    )
    outcome = ClarificationRunner(
        session, gateway, elicitation_rules, extraction_rules, settings=settings
    ).raise_for_finding(
        actor=actor,
        project_id=pid,
        finding_id=finding.id,
        asked_of=payload.asked_of_stakeholder_id,
    )
    if outcome.clarification is None:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": outcome.error, "run_id": str(outcome.run_id)},
        )
    service = ClarificationService(session, actor, elicitation_rules)
    return issue_out(service.issue(pid, outcome.clarification))


# --- glossary ---------------------------------------------------------------------------------


@router.get("/projects/{project_id}/glossary", response_model=list[GlossaryTermOut])
def list_glossary(
    project_id: uuid.UUID, session: DbSession, actor: CurrentActor
) -> list[GlossaryTermOut]:
    pid = _project(actor, project_id, Action.GLOSSARY_READ)
    return [
        GlossaryTermOut.model_validate(t) for t in GlossaryService(session, actor).list_terms(pid)
    ]


@router.post(
    "/projects/{project_id}/glossary",
    response_model=GlossaryTermOut,
    status_code=status.HTTP_201_CREATED,
)
def add_glossary_term(
    project_id: uuid.UUID, payload: GlossaryTermIn, session: DbSession, actor: CurrentActor
) -> GlossaryTermOut:
    pid = _project(actor, project_id, Action.GLOSSARY_MANAGE)
    term = GlossaryService(session, actor).add(
        pid, term=payload.term, definition=payload.definition
    )
    return GlossaryTermOut.model_validate(term)
