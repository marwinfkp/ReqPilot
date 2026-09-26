"""SDLC recommendation endpoints (P9; architecture S, C.5, L, M.3 G6).

What P9 needs to be usable, and no more:

* ``POST /projects/{id}/sdlc-runs`` - start a recommendation from one approved
  baseline (Analyst; ``SDLC_RUN_START``). Refused (409) unless every input is
  approved.
* ``GET  /projects/{id}/sdlc-runs`` - the project's runs, newest last.
* ``GET  /sdlc-runs/{id}`` - one run: the 13 factors with evidence, provenance,
  proposals and overrides; the ranking with every score and rule effect; the
  reversal conditions; the explanation with its discrepancies; the G6 status.
* ``POST /sdlc-runs/{id}/factors/{factor}/override`` - a human override with a
  reason (``FR-SDL-003``; Analyst or Project Manager). It creates a new run that
  supersedes this one (a full recompute); the response is the new run.
* ``POST /sdlc-runs/{id}/explanation`` - retry the explanation of a ranked run
  whose explanation failed or was never generated (Analyst).

**G6 is decided through the existing** ``POST /approval-tasks/{id}/decide``;
there is no other decision path, and nothing here selects an SDLC, reorders a
ranking or changes a score except through a recorded human override.

Every handler authorises through the policy; a resource outside the caller's
reach is a 404, exactly as for one that does not exist.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status
from sqlalchemy.orm import Session

from reqpilot.api.dependencies import (
    AppSettings,
    CurrentActor,
    DbSession,
    Gateway,
    RiskRulesDep,
    SdlcRulesDep,
)
from reqpilot.api.lookup import require_found
from reqpilot.api.sdlc_schemas import (
    CandidateOut,
    ExplanationOut,
    FactorOut,
    FactorOverrideIn,
    G6TaskOut,
    RuleApplicationOut,
    SdlcRunIn,
    SdlcRunOut,
    SdlcRunStartOut,
    SdlcRunSummaryOut,
)
from reqpilot.domain.enums import Action, ApprovalTaskStatus, ResourceType
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.sdlc import SdlcRun
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.graph.sdlc_runner import SdlcRunner, SdlcRunSummary
from reqpilot.rules.sdlc import SdlcRules
from reqpilot.services.sdlc.service import SdlcService

router = APIRouter(prefix="/api/v1", tags=["sdlc"])


def _project(actor: Actor, project_id: uuid.UUID, action: Action) -> ProjectId:
    pid = ProjectId(project_id)
    require(actor, action, ResourceRef(resource_type=ResourceType.SDLC_RUN, project_id=pid))
    return pid


def _start_out(summary: SdlcRunSummary) -> SdlcRunStartOut:
    return SdlcRunStartOut(
        graph_run_id=summary.graph_run_id,
        status=summary.status,
        sdlc_run_id=summary.sdlc_run_id,
        explanation_status=summary.explanation_status,
        g6_task_ids=list(summary.g6_task_ids),
        proposals_accepted=summary.proposals_accepted,
        proposals_rejected=summary.proposals_rejected,
        semantic_failures=summary.semantic_failures,
        provider_calls=summary.provider_calls,
        tokens_in=summary.tokens_in,
        tokens_out=summary.tokens_out,
        errors=list(summary.errors),
    )


def g6_status(run: SdlcRun, tasks: list) -> str:  # type: ignore[type-arg]
    """A plain reading of where G6 stands, from the persisted tasks."""
    if not tasks:
        return "not raised (the ranking waits for its explanation)"
    approved = sum(1 for t in tasks if t.status is ApprovalTaskStatus.APPROVED)
    if all(t.status is ApprovalTaskStatus.APPROVED for t in tasks):
        return f"passed ({approved}/{len(tasks)} approved)"
    if any(t.status is ApprovalTaskStatus.REJECTED for t in tasks):
        return "rejected"
    if any(t.status is ApprovalTaskStatus.OPEN for t in tasks):
        return f"open ({approved}/{len(tasks)} approved)"
    return f"closed ({run.status})"


def sdlc_run_out(
    session: Session, actor: Actor, rules: SdlcRules, project_id: ProjectId, run: SdlcRun
) -> SdlcRunOut:
    service = SdlcService(session, actor, rules)
    labels = {str(f): d.label for f, d in rules.config.factors.items()}
    tasks = service.g6_tasks(project_id, run)
    return SdlcRunOut(
        id=run.id,
        project_id=run.project_id,
        status=run.status,
        baseline_id=run.baseline_id,
        graph_run_id=run.graph_run_id,
        supersedes_run_id=run.supersedes_run_id,
        ruleset_ref=run.ruleset_ref,
        rules_sha256=run.rules_sha256,
        weights_version=run.weights_version,
        input_fingerprint=run.input_fingerprint,
        profile_hash=run.profile_hash,
        ranking_hash=run.ranking_hash,
        recommendation_hash=run.recommendation_hash,
        top_candidate=run.top_candidate,
        runner_up_candidate=run.runner_up_candidate,
        reversal_conditions=list(run.reversal_conditions or []),
        facts_summary=dict(run.facts_summary or {}),
        factors=[
            FactorOut(
                factor_id=f.factor_id,
                label=labels.get(f.factor_id, f.factor_id),
                score=f.score,
                source=f.source,
                weight=f.weight,
                derived_score=f.derived_score,
                rationale=f.rationale,
                evidence_refs=list(f.evidence_refs or []),
                evidence_state=f.evidence_state,
                basis=dict(f.basis or {}),
                proposal_status=f.proposal_status,
                proposed_score=f.proposed_score,
                proposal_rationale=f.proposal_rationale,
                proposal_evidence_refs=list(f.proposal_evidence_refs or []),
                proposal_rejection_reason=f.proposal_rejection_reason,
                is_overridden=f.is_overridden,
                previous_score=f.previous_score,
                override_reason=f.override_reason,
                overridden_by=f.overridden_by,
                override_role=f.override_role,
                overridden_at=f.overridden_at,
            )
            for f in service.factors(project_id, run.id)
        ],
        candidates=[
            CandidateOut(
                candidate_key=c.candidate_key,
                label=c.label,
                rank=c.rank,
                score=c.normalised_score,
                mcda_score=c.mcda_score,
                raw_score=c.raw_score,
                max_raw=c.max_raw,
                vetoed_by=list(c.vetoed_by or []),
                boosted_by=list(c.boosted_by or []),
                required_by=list(c.required_by or []),
                contributions={k: float(v) for k, v in (c.contributions or {}).items()},
            )
            for c in service.candidates(project_id, run.id)
        ],
        rules_applied=[
            RuleApplicationOut(
                rule_id=r.rule_id,
                effect=r.effect,
                affected_candidate=r.affected_candidate,
                changed_ranking=r.changed_ranking,
                reason=r.reason,
                trigger_values={k: int(v) for k, v in (r.trigger_values or {}).items()},
            )
            for r in service.rule_applications(project_id, run.id)
        ],
        explanation=ExplanationOut(
            status=run.explanation_status,
            narrative=run.explanation_narrative,
            counter_arguments=list(run.explanation_counter_arguments or []),
            asserted_top_candidate=run.asserted_top_candidate,
            asserted_scores={k: float(v) for k, v in (run.asserted_scores or {}).items()},
            discrepancies=list(run.explanation_discrepancies or []),
            attempts=run.explanation_attempts,
            model=run.explanation_model,
            prompt=run.explanation_prompt,
        ),
        g6_task_group_id=run.g6_task_group_id,
        g6_tasks=[
            G6TaskOut(
                id=t.id,
                required_role=t.required_role,
                status=t.status,
                subject_version_hash=t.subject_version_hash,
            )
            for t in tasks
        ],
        g6_status=g6_status(run, tasks),
        selected_candidate=run.selected_candidate,
        selected_at=run.selected_at,
        created_by=run.created_by,
        created_at=run.created_at,
    )


def _find(
    actor: Actor, session: Session, rules: SdlcRules, run_id: uuid.UUID
) -> tuple[ProjectId, SdlcRun]:
    return require_found(
        actor,
        Action.SDLC_READ,
        ResourceType.SDLC_RUN,
        lambda pid: SdlcService(session, actor, rules).get(pid, run_id),
    )


# --- runs ----------------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/sdlc-runs",
    response_model=SdlcRunStartOut,
    status_code=status.HTTP_201_CREATED,
)
def start_sdlc_run(
    project_id: uuid.UUID,
    payload: SdlcRunIn,
    session: DbSession,
    actor: CurrentActor,
    gateway: Gateway,
    sdlc_rules: SdlcRulesDep,
    risk_rules: RiskRulesDep,
    settings: AppSettings,
) -> SdlcRunStartOut:
    """C.5 end to end: evidence, proposals, rules and MCDA, explanation, G6 raised."""
    pid = _project(actor, project_id, Action.SDLC_RUN_START)
    summary = SdlcRunner(
        session, gateway, settings=settings, sdlc_rules=sdlc_rules, risk_rules=risk_rules
    ).start(actor=actor, project_id=pid, baseline_id=payload.baseline_id, semantic=payload.semantic)
    return _start_out(summary)


@router.get("/projects/{project_id}/sdlc-runs", response_model=list[SdlcRunSummaryOut])
def list_sdlc_runs(
    project_id: uuid.UUID, session: DbSession, actor: CurrentActor, sdlc_rules: SdlcRulesDep
) -> list[SdlcRunSummaryOut]:
    pid = _project(actor, project_id, Action.SDLC_READ)
    return [
        SdlcRunSummaryOut(
            id=r.id,
            status=r.status,
            baseline_id=r.baseline_id,
            top_candidate=r.top_candidate,
            runner_up_candidate=r.runner_up_candidate,
            explanation_status=r.explanation_status,
            selected_candidate=r.selected_candidate,
            supersedes_run_id=r.supersedes_run_id,
            created_at=r.created_at,
        )
        for r in SdlcService(session, actor, sdlc_rules).list_runs(pid)
    ]


@router.get("/sdlc-runs/{run_id}", response_model=SdlcRunOut)
def get_sdlc_run(
    run_id: uuid.UUID, session: DbSession, actor: CurrentActor, sdlc_rules: SdlcRulesDep
) -> SdlcRunOut:
    pid, run = _find(actor, session, sdlc_rules, run_id)
    return sdlc_run_out(session, actor, sdlc_rules, pid, run)


@router.post(
    "/sdlc-runs/{run_id}/factors/{factor_id}/override",
    response_model=SdlcRunStartOut,
    status_code=status.HTTP_201_CREATED,
)
def override_factor(
    run_id: uuid.UUID,
    factor_id: str,
    payload: FactorOverrideIn,
    session: DbSession,
    actor: CurrentActor,
    gateway: Gateway,
    sdlc_rules: SdlcRulesDep,
    risk_rules: RiskRulesDep,
    settings: AppSettings,
) -> SdlcRunStartOut:
    """``FR-SDL-003``: a recorded human override; a new run supersedes this one."""
    pid, run = _find(actor, session, sdlc_rules, run_id)
    summary = SdlcRunner(
        session, gateway, settings=settings, sdlc_rules=sdlc_rules, risk_rules=risk_rules
    ).override(
        actor=actor,
        project_id=pid,
        run_id=run.id,
        factor=factor_id,
        new_score=payload.score,
        reason=payload.reason,
        role=payload.role,
    )
    return _start_out(summary)


@router.post(
    "/sdlc-runs/{run_id}/explanation",
    response_model=SdlcRunStartOut,
    status_code=status.HTTP_201_CREATED,
)
def retry_explanation(
    run_id: uuid.UUID,
    session: DbSession,
    actor: CurrentActor,
    gateway: Gateway,
    sdlc_rules: SdlcRulesDep,
    risk_rules: RiskRulesDep,
    settings: AppSettings,
) -> SdlcRunStartOut:
    pid, run = _find(actor, session, sdlc_rules, run_id)
    summary = SdlcRunner(
        session, gateway, settings=settings, sdlc_rules=sdlc_rules, risk_rules=risk_rules
    ).explain(actor=actor, project_id=pid, run_id=run.id)
    return _start_out(summary)
