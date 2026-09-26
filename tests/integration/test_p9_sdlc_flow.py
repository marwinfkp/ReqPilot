"""P9 SDLC recommendation - the service flow over the synthetic P9 world.

Start a run from the approved baseline; the 13 factors with evidence and
provenance; the validated proposals (a risk-derived proposal refused); the
persisted ranking and rule applications; the explanation and its consistency;
the four G6 tasks; the co-approval; the recorded selection.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.p9_helpers import (
    P9World,
    consistent_explanation,
    make_p9_world,
    proposals,
    ranking_of,
)

from reqpilot.domain.enums import (
    ApprovalTaskStatus,
    AuditEventType,
    ExplanationStatus,
    GraphRunStatus,
    Role,
    SdlcRunStatus,
)
from reqpilot.domain.errors import AuthorizationError
from reqpilot.domain.models.audit import AuditEvent
from reqpilot.domain.policy import Actor
from reqpilot.domain.sdlc.factors import FACTOR_ORDER, RISK_DERIVED_FACTORS

pytestmark = pytest.mark.integration


@pytest.fixture
def world(db_session: Session) -> P9World:
    return make_p9_world(db_session)


def events(world: P9World, event_type: AuditEventType) -> list[AuditEvent]:
    return list(
        world.session.scalars(
            select(AuditEvent).where(
                AuditEvent.project_id == world.project_id, AuditEvent.event_type == event_type
            )
        )
    )


def test_start_ranks_explains_and_raises_g6(world: P9World) -> None:
    summary = world.start()
    assert summary.status is GraphRunStatus.COMPLETED, summary.errors
    assert summary.sdlc_run_id is not None
    service = world.service()
    run = service.get(world.project_id, summary.sdlc_run_id)
    assert run is not None
    assert run.status is SdlcRunStatus.AWAITING_G6
    assert run.explanation_status is ExplanationStatus.GENERATED, run.explanation_discrepancies

    factors = service.factors(world.project_id, run.id)
    assert [f.factor_id for f in factors] == [str(f) for f in FACTOR_ORDER]
    by_id = {f.factor_id: f for f in factors}
    for row in factors:
        assert row.evidence_refs or row.evidence_state == "not_recorded", row.factor_id
    verification = by_id["need_for_formal_verification"]
    assert verification.proposal_status == "accepted"
    assert verification.source == "model_proposal"
    assert abs(verification.score - verification.derived_score) <= 1
    security = by_id["security_risk"]
    assert security.proposal_status == "rejected"
    assert "RISK_DERIVED" in (security.proposal_rejection_reason or "")
    for f in RISK_DERIVED_FACTORS:
        assert by_id[str(f)].source == "risk_aggregate"

    candidates = service.candidates(world.project_id, run.id)
    assert [c.rank for c in candidates] == list(range(1, 8))
    assert candidates[0].candidate_key == run.top_candidate

    tasks = world.g6_tasks(ApprovalTaskStatus.OPEN)
    assert {t.required_role for t in tasks} == {
        Role.PROJECT_MANAGER,
        Role.ARCHITECT,
        Role.SECURITY_REVIEWER,
        Role.COMPLIANCE_OFFICER,
    }
    assert len({t.task_group_id for t in tasks}) == 1
    assert all(t.subject_version_hash == run.recommendation_hash for t in tasks)
    for event in (
        AuditEventType.FACTOR_PROPOSED,
        AuditEventType.RULES_APPLIED,
        AuditEventType.MCDA_COMPUTED,
        AuditEventType.EXPLANATION_GENERATED,
    ):
        assert events(world, event), event

    for task in tasks[:-1]:
        world.decide(task)
        assert service.get(world.project_id, run.id).status is SdlcRunStatus.AWAITING_G6
    world.decide(tasks[-1])
    run = service.get(world.project_id, run.id)
    assert run.status is SdlcRunStatus.SELECTED
    assert run.selected_candidate == run.top_candidate
    assert events(world, AuditEventType.SDLC_SELECTION_RECORDED)


# --- inputs must be approved ------------------------------------------------------------------


def test_a_run_needs_an_approved_baseline_in_this_project(world: P9World) -> None:
    from reqpilot.domain.errors import ProjectIsolationError, SdlcError
    from reqpilot.domain.models.sdlc import SdlcRun

    runner = world.runner()
    summary = runner.start(
        actor=world.p8.analyst, project_id=world.project_id, baseline_id=uuid.uuid4()
    )
    assert summary.status is GraphRunStatus.FAILED and summary.sdlc_run_id is None
    assert "baseline not found" in " ".join(summary.errors)
    assert not list(world.session.scalars(select(SdlcRun)))
    # Only an analyst starts one; a project manager, an auditor or the architect cannot.
    for who in (world.p8.project_manager, world.p8.auditor, world.architect):
        with pytest.raises(AuthorizationError):
            runner.start(actor=who, project_id=world.project_id, baseline_id=world.baseline_id)
    # Another project's analyst cannot reach this project at all.
    other = make_p9_world(world.session, "Another lender (synthetic)")
    with pytest.raises(ProjectIsolationError):
        runner.start(
            actor=other.p8.analyst, project_id=world.project_id, baseline_id=world.baseline_id
        )
    # Nor use this project's baseline in their own project.
    crossed = runner.start(
        actor=other.p8.analyst, project_id=other.project_id, baseline_id=world.baseline_id
    )
    assert crossed.sdlc_run_id is None and crossed.status is GraphRunStatus.FAILED
    assert SdlcError  # the failure is recorded, not raised, inside the run


def test_an_ungoverned_risk_register_refuses_the_run(world: P9World) -> None:
    """A project-level HIGH risk that no one has reviewed blocks the recommendation."""
    from reqpilot.domain.enums import RiskScope, RiskSeverity, RiskStatus
    from reqpilot.domain.models.risk import Risk

    risk = world.session.scalars(
        select(Risk).where(Risk.project_id == world.project_id, Risk.severity == RiskSeverity.HIGH)
    ).first()
    assert risk is not None
    world.session.execute(
        Risk.__table__.update()
        .where(Risk.id == risk.id)
        .values(
            scope=RiskScope.PROJECT, requirement_version_id=None, status=RiskStatus.UNDER_REVIEW
        )
    )
    world.session.expire_all()
    summary = world.start()
    assert summary.sdlc_run_id is None
    assert "G8_UNREVIEWED" in " ".join(summary.errors)


# --- overrides (FR-SDL-003) -------------------------------------------------------------------


def test_an_override_is_recorded_and_recomputes_as_a_new_run(world: P9World) -> None:
    first = world.start()
    service = world.service()
    old = service.get(world.project_id, first.sdlc_run_id)
    old_tasks = world.g6_tasks(ApprovalTaskStatus.OPEN)
    pm = world.p8.project_manager
    second = world.runner().override(
        actor=pm,
        project_id=world.project_id,
        run_id=old.id,
        factor="expected_frequency_of_change",
        new_score=5,
        reason="Product confirmed monthly regulatory rule changes (synthetic).",
        role=Role.PROJECT_MANAGER,
    )
    assert second.status is GraphRunStatus.COMPLETED, second.errors
    new = service.get(world.project_id, second.sdlc_run_id)
    assert new.supersedes_run_id == old.id
    assert service.get(world.project_id, old.id).status is SdlcRunStatus.SUPERSEDED
    for task in old_tasks:
        world.session.refresh(task)
        assert task.status is ApprovalTaskStatus.CANCELLED, "the old G6 tasks are moot"
    row = {f.factor_id: f for f in service.factors(world.project_id, new.id)}[
        "expected_frequency_of_change"
    ]
    assert (row.score, row.source, row.is_overridden) == (5, "human_override", True)
    assert row.previous_score == 1 and row.derived_score == 1
    assert row.overridden_by == pm.actor_id and row.override_role == "project_manager"
    assert row.override_reason and row.overridden_at is not None
    overridden = events(world, AuditEventType.FACTOR_OVERRIDDEN)
    assert len(overridden) == 1
    payload = overridden[0].payload
    assert payload["factor"] == "expected_frequency_of_change"
    assert (payload["previous_score"], payload["new_score"]) == (1, 5)
    assert payload["role"] == "project_manager" and "reason_sha256" in payload
    assert payload["sdlc_run_id"] == str(new.id)
    assert events(world, AuditEventType.SDLC_RUN_SUPERSEDED)
    # The old run's rows are untouched history.
    old_row = {f.factor_id: f for f in service.factors(world.project_id, old.id)}[
        "expected_frequency_of_change"
    ]
    assert old_row.score == 1 and not old_row.is_overridden

    # A second override carries the first forward.
    third = world.runner().override(
        actor=world.p8.analyst,
        project_id=world.project_id,
        run_id=new.id,
        factor="security_risk",
        new_score=4,
        reason="Pen-test findings raised the security exposure (synthetic).",
        role=Role.ANALYST,
    )
    rows = {f.factor_id: f for f in service.factors(world.project_id, third.sdlc_run_id)}
    assert rows["expected_frequency_of_change"].score == 5
    assert rows["expected_frequency_of_change"].overridden_by == pm.actor_id
    assert rows["security_risk"].score == 4 and rows["security_risk"].source == "human_override"


def test_override_refusals(world: P9World) -> None:
    from reqpilot.domain.errors import SdlcError

    run_id = world.start().sdlc_run_id
    runner = world.runner()

    def attempt(**kwargs):  # type: ignore[no-untyped-def]
        values = {
            "actor": world.p8.analyst,
            "project_id": world.project_id,
            "run_id": run_id,
            "factor": "system_size",
            "new_score": 4,
            "reason": "A reason (synthetic).",
            "role": Role.ANALYST,
        }
        values.update(kwargs)
        return runner.override(**values)

    with pytest.raises(SdlcError, match="reason"):
        attempt(reason="   ")
    with pytest.raises(SdlcError, match="13 SDLC factors"):
        attempt(factor="choose_agile")
    with pytest.raises(SdlcError, match="1 to 5"):
        attempt(new_score=9)
    with pytest.raises(SdlcError, match="already"):
        attempt(new_score=1)
    with pytest.raises(SdlcError, match="does not hold"):
        attempt(role=Role.PROJECT_MANAGER)
    for who in (world.p8.compliance_officer, world.p8.security_reviewer, world.architect,
                world.p8.auditor, world.p8.priya):  # fmt: skip
        with pytest.raises(AuthorizationError):
            attempt(actor=who, role=next(iter(who.roles_in(world.project_id))))
    attempt()
    with pytest.raises(SdlcError, match="superseded"):
        attempt(new_score=5)  # the run it names has been replaced


# --- the explanation ------------------------------------------------------------------------


def _wrong_top(request):  # type: ignore[no-untyped-def]
    body = consistent_explanation(request)
    ranking = ranking_of(request)
    body["asserted_top_candidate"] = ranking[1][0]
    body["narrative"] += " The AI selected this methodology."
    return json.dumps(body)


def test_a_discrepancy_regenerates_once_then_is_stored_and_shown(world: P9World) -> None:
    world.model.overrides["sdlc_explanation"] = _wrong_top
    summary = world.start()
    run = world.service().get(world.project_id, summary.sdlc_run_id)
    assert world.model.calls["sdlc_explanation"] == 2, "one regeneration, no more"
    assert run.explanation_status is ExplanationStatus.DISCREPANCY
    assert run.explanation_attempts == 2
    found = {d["code"] for d in run.explanation_discrepancies}
    assert {"TOP_MISMATCH", "MODEL_SELECTION_CLAIM"} <= found
    assert run.asserted_top_candidate != run.top_candidate
    assert (
        run.top_candidate == world.service().candidates(world.project_id, run.id)[0].candidate_key
    )
    assert run.status is SdlcRunStatus.AWAITING_G6, "shown to the approvers, not hidden"
    event = events(world, AuditEventType.EXPLANATION_DISCREPANCY)
    assert event and event[0].payload["ranking_unchanged"] is True


def test_a_failed_explanation_leaves_the_ranking_and_waits_for_a_retry(world: P9World) -> None:
    from reqpilot.domain.errors import SdlcError

    world.model.overrides["sdlc_explanation"] = lambda _r: "not json at all"
    summary = world.start()
    service = world.service()
    run = service.get(world.project_id, summary.sdlc_run_id)
    assert run.status is SdlcRunStatus.RANKED
    assert run.explanation_status is ExplanationStatus.FAILED
    assert run.explanation_narrative is None
    assert not world.g6_tasks(), "G6 waits until an explanation exists"
    with pytest.raises(AuthorizationError):
        world.runner().explain(
            actor=world.p8.project_manager, project_id=world.project_id, run_id=run.id
        )
    del world.model.overrides["sdlc_explanation"]
    retried = world.runner().explain(
        actor=world.p8.analyst, project_id=world.project_id, run_id=run.id
    )
    assert retried.status is GraphRunStatus.COMPLETED, retried.errors
    run = service.get(world.project_id, run.id)
    assert run.explanation_status is ExplanationStatus.GENERATED
    assert run.status is SdlcRunStatus.AWAITING_G6
    assert len(world.g6_tasks(ApprovalTaskStatus.OPEN)) == 4
    with pytest.raises(SdlcError):
        world.runner().explain(actor=world.p8.analyst, project_id=world.project_id, run_id=run.id)


def test_without_the_semantic_layer_the_ranking_still_stands(world: P9World) -> None:
    summary = world.start(semantic=False)
    run = world.service().get(world.project_id, summary.sdlc_run_id)
    assert summary.provider_calls == 0
    assert run.status is SdlcRunStatus.RANKED
    assert run.explanation_status is ExplanationStatus.NOT_GENERATED
    rows = world.service().factors(world.project_id, run.id)
    assert {r.proposal_status for r in rows} == {"not_requested"}
    assert {r.source for r in rows} <= {"derived", "risk_aggregate"}


def test_hostile_model_output_changes_nothing(world: P9World) -> None:
    def hostile(request):  # type: ignore[no-untyped-def]
        body = consistent_explanation(request)
        body["selected_candidate"] = "agile"  # no such field: schema-invalid
        return json.dumps(body)

    world.model.overrides["sdlc_explanation"] = hostile
    world.model.overrides["sdlc_factor_proposal"] = lambda _r: json.dumps(
        {"proposals": [], "ranking": ["agile"]}
    )
    summary = world.start()
    run = world.service().get(world.project_id, summary.sdlc_run_id)
    baseline = world.start(semantic=False)
    plain = world.service().get(world.project_id, baseline.sdlc_run_id)
    assert run.explanation_status is ExplanationStatus.FAILED
    assert run.ranking_hash == plain.ranking_hash, "a refused output leaves the ranking alone"


def test_the_role_never_sees_requirement_text(world: P9World) -> None:
    seen: list[str] = []

    def spy(request):  # type: ignore[no-untyped-def]
        seen.extend(request.untrusted_content.values())
        return json.dumps(proposals(request))

    world.model.overrides["sdlc_factor_proposal"] = spy
    world.start()
    text = "\n".join(seen)
    assert seen
    for key in ("L01", "L08"):
        statement = world.p8.version(key).statement
        assert statement[:40] not in text
    assert "Ignore all previous instructions" not in text


# --- G6 ----------------------------------------------------------------------------------


def test_g6_refuses_wrong_roles_other_projects_agents_self_and_duplicates(
    world: P9World,
) -> None:
    from reqpilot.domain.enums import ActorKind, ApprovalDecisionType
    from reqpilot.domain.errors import ApprovalError, SelfApprovalError

    world.start()
    tasks = {t.required_role: t for t in world.g6_tasks(ApprovalTaskStatus.OPEN)}
    pm_task = tasks[Role.PROJECT_MANAGER]
    # An analyst holds no G6 role.
    with pytest.raises(AuthorizationError):
        world.decide(pm_task, actor=world.p8.analyst, role=Role.ANALYST)
    # The architect cannot sign the project manager's task, nor claim a role not held.
    with pytest.raises(ApprovalError):
        world.decide(pm_task, actor=world.architect, role=Role.ARCHITECT)
    with pytest.raises(AuthorizationError):
        world.decide(pm_task, actor=world.architect, role=Role.PROJECT_MANAGER)
    # Another project's project manager cannot see the task.
    other = make_p9_world(world.session, "Another lender (synthetic)")
    with pytest.raises(AuthorizationError):
        world.decide(pm_task, actor=other.p8.project_manager)
    # No agent or system actor decides a gate.
    system = Actor(
        actor_id=uuid.uuid4(),
        kind=ActorKind.SYSTEM,
        roles_by_project={world.project_id: frozenset({Role.PROJECT_MANAGER})},
    )
    with pytest.raises(AuthorizationError):
        world.decide(pm_task, actor=system)
    # One role alone completes nothing.
    world.decide(pm_task)
    run = world.service().get(world.project_id, pm_task.subject_id)
    assert run.status is SdlcRunStatus.AWAITING_G6 and run.selected_candidate is None
    with pytest.raises(ApprovalError):
        world.decide(pm_task)  # a decided task cannot be decided again
    # A rejection must say why.
    with pytest.raises(ApprovalError, match="justification"):
        world.decide(tasks[Role.ARCHITECT], ApprovalDecisionType.REJECT, justification="")
    assert SelfApprovalError


def test_the_author_of_an_override_cannot_sign_g6(world: P9World) -> None:
    from reqpilot.domain.errors import SelfApprovalError

    run_id = world.start().sdlc_run_id
    world.runner().override(
        actor=world.p8.project_manager,
        project_id=world.project_id,
        run_id=run_id,
        factor="system_size",
        new_score=3,
        reason="Scope will double in phase two (synthetic).",
        role=Role.PROJECT_MANAGER,
    )
    pm_task = next(
        t
        for t in world.g6_tasks(ApprovalTaskStatus.OPEN)
        if t.required_role is Role.PROJECT_MANAGER
    )
    with pytest.raises(SelfApprovalError):
        world.decide(pm_task, actor=world.p8.project_manager)


def test_a_cancelled_or_tampered_recommendation_cannot_be_signed(world: P9World) -> None:
    from reqpilot.domain.errors import ApprovalError, StaleApprovalError
    from reqpilot.domain.models.sdlc import SdlcRun

    run_id = world.start().sdlc_run_id
    tasks = world.g6_tasks(ApprovalTaskStatus.OPEN)
    # Tamper with the stored explanation beneath the ORM (SQLite has no trigger):
    # the binding no longer matches, so the signature is refused as stale.
    world.session.execute(
        SdlcRun.__table__.update()
        .where(SdlcRun.id == run_id)
        .values(explanation_narrative="A different explanation.")
    )
    world.session.expire_all()
    with pytest.raises(StaleApprovalError):
        world.decide(tasks[0])
    # A superseded run's tasks are cancelled and refused.
    world.session.execute(
        SdlcRun.__table__.update().where(SdlcRun.id == run_id).values(explanation_narrative=None)
    )
    world.session.expire_all()
    world.start(semantic=False)  # a new run supersedes the live one
    for task in tasks:
        world.session.refresh(task)
        assert task.status is ApprovalTaskStatus.CANCELLED
        with pytest.raises(ApprovalError):
            world.decide(task)


def test_reject_and_modify_close_g6_without_a_selection(world: P9World) -> None:
    from reqpilot.domain.enums import ApprovalDecisionType

    service = world.service()
    first = world.start().sdlc_run_id
    tasks = {t.required_role: t for t in world.g6_tasks(ApprovalTaskStatus.OPEN)}
    world.decide(tasks[Role.SECURITY_REVIEWER], ApprovalDecisionType.REJECT, justification="No.")
    run = service.get(world.project_id, first)
    assert run.status is SdlcRunStatus.REJECTED and run.selected_candidate is None
    for role, task in tasks.items():
        world.session.refresh(task)
        expected = (
            ApprovalTaskStatus.REJECTED
            if role is Role.SECURITY_REVIEWER
            else ApprovalTaskStatus.CANCELLED
        )
        assert task.status is expected
    assert events(world, AuditEventType.SDLC_G6_SETTLED)

    second = world.start().sdlc_run_id
    assert service.get(world.project_id, first).status is SdlcRunStatus.REJECTED, "kept"
    tasks = {t.required_role: t for t in world.g6_tasks(ApprovalTaskStatus.OPEN)}
    world.decide(tasks[Role.ARCHITECT], ApprovalDecisionType.MODIFY, justification="Revise.")
    assert service.get(world.project_id, second).status is SdlcRunStatus.REVISION_REQUESTED
    assert not world.g6_tasks(ApprovalTaskStatus.OPEN)


def test_a_later_selection_supersedes_an_earlier_one(world: P9World) -> None:
    service = world.service()
    first = world.start().sdlc_run_id
    for task in world.g6_tasks(ApprovalTaskStatus.OPEN):
        world.decide(task)
    assert service.get(world.project_id, first).status is SdlcRunStatus.SELECTED
    second = (
        world.runner()
        .override(
            actor=world.p8.analyst,
            project_id=world.project_id,
            run_id=first,
            factor="need_for_continuous_delivery",
            new_score=4,
            reason="Weekly releases were agreed (synthetic).",
            role=Role.ANALYST,
        )
        .sdlc_run_id
    )
    assert service.get(world.project_id, first).status is SdlcRunStatus.SELECTED, (
        "a selection stands until another is approved"
    )
    for task in world.g6_tasks(ApprovalTaskStatus.OPEN):
        world.decide(task)
    assert service.get(world.project_id, second).status is SdlcRunStatus.SELECTED
    assert service.get(world.project_id, first).status is SdlcRunStatus.SUPERSEDED


def test_the_ranking_is_reproducible_from_its_persisted_profile(world: P9World) -> None:
    from reqpilot.domain.sdlc.factors import FactorId
    from reqpilot.domain.sdlc.profile import ranking_hash
    from reqpilot.domain.sdlc.scoring import score_candidates

    run_id = world.start().sdlc_run_id
    service = world.service()
    run = service.get(world.project_id, run_id)
    config = service.rules.config
    scores = {FactorId(r.factor_id): r.score for r in service.factors(world.project_id, run_id)}
    again = score_candidates(scores, config)
    assert ranking_hash(again, config.ruleset_ref, config.weights_version) == run.ranking_hash
    assert run.rules_sha256 == service.rules.content_sha256
    assert run.ruleset_ref == config.ruleset_ref


def test_the_run_is_traced(world: P9World) -> None:
    from reqpilot.domain.traceability import TraceLinkType, TraceNodeType
    from reqpilot.services.traceability import TraceGraphSync, TraceQueryService

    run_id = world.start().sdlc_run_id
    for task in world.g6_tasks(ApprovalTaskStatus.OPEN):
        world.decide(task)
    TraceGraphSync(world.session, world.p8.analyst).sync(world.project_id)
    graph = TraceQueryService(world.session, world.p8.analyst).graph(world.project_id)
    rid = str(run_id)
    n, lk = TraceNodeType, TraceLinkType
    touching = [e for e in graph.links if rid in (e.from_id, e.to_id)]
    kinds = {(e.from_type, e.link_type, e.to_type) for e in touching}
    assert (str(n.BASELINE), str(lk.INFORMED), str(n.SDLC_RUN)) in kinds
    assert (str(n.SDLC_RUN), str(lk.CONTAINS), str(n.SDLC_FACTOR)) in kinds
    assert (str(n.SDLC_RUN), str(lk.CONTAINS), str(n.SDLC_CANDIDATE)) in kinds
    approvals = [e for e in touching if e.link_type == str(lk.APPROVED_BY)]
    assert len(approvals) == 4, "N.2 #23: one APPROVED_BY edge per G6 signature"
    aggregated = {e.from_type for e in graph.links if e.link_type == str(lk.AGGREGATED_INTO)}
    assert {str(n.RISK), str(n.REQUIREMENT_VERSION)} <= aggregated
    assert any(
        e.link_type == str(lk.INFORMED) and e.to_type == str(n.SDLC_CANDIDATE) for e in graph.links
    )
