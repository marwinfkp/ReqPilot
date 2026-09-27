"""The P9 end-to-end exit test (roadmap P9 "SDLC recommendation").

The roadmap exit: the recommendation is derived from the approved baseline and
the governed risk register, ranked deterministically, explained afterwards and
checked for consistency, and selected only by G6 co-approval.

One synthetic loan-origination workshop (``tests.p8_helpers.WORKSHOP``, fictional
speakers) goes through the real P3-P8 pathways to an approved baseline, then
through P9's ``sdlc_graph``: evidence, bounded proposals, rules and MCDA, the
explanation and its consistency check, and G6 through the one approval service.
The model is the scripted P9 model; nothing reaches a network.

The seventeen steps, and where each is asserted:

1.  an approved baseline ..................................... step 1
2.  the governed risk register feeds the risk-derived factors  step 2
3.  thirteen factors with evidence and provenance ............ step 3
4.  validated model proposals (one accepted, one refused) .... step 4
5.  deterministic rules (a human override triggers a veto) ... step 5
6.  weighted MCDA, by hand ................................... step 6
7.  a persisted, reproducible ranking ........................ step 7
8.  the explanation, generated after the ranking ............. step 8
9.  its consistency check .................................... step 9
10. four G6 tasks, one per role, one group ................... step 10
11. no single role completes G6 .............................. step 11
12. all four approve ......................................... step 12
13. the selection is recorded (the computed first) ........... step 13
14. the audit chain .......................................... step 14
15. traceability ............................................. step 15
16. project isolation ........................................ step 16
17. unauthorised, stale and wrong-role decisions refused ..... step 17

No P10 workflow is generated.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.p9_helpers import P9World, make_p9_world

from reqpilot.domain.enums import (
    ApprovalDecisionType,
    ApprovalTaskStatus,
    AuditEventType,
    ExplanationStatus,
    GraphRunStatus,
    RiskStatus,
    Role,
    SdlcRunStatus,
)
from reqpilot.domain.errors import (
    ApprovalError,
    AuthorizationError,
    ProjectIsolationError,
    SelfApprovalError,
)
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.audit import AuditEvent
from reqpilot.domain.models.risk import Risk
from reqpilot.domain.policy import Actor
from reqpilot.domain.sdlc.factors import FACTOR_ORDER, FactorId, norm
from reqpilot.domain.sdlc.profile import ranking_hash
from reqpilot.domain.sdlc.scoring import score_candidates
from reqpilot.domain.traceability import TraceLinkType, TraceNodeType
from reqpilot.services.audit import AuditService
from reqpilot.services.traceability import TraceGraphSync, TraceQueryService

pytestmark = pytest.mark.workflow

N, L = TraceNodeType, TraceLinkType


@pytest.fixture
def world(db_session: Session) -> P9World:
    return make_p9_world(db_session)


def audit_types(world: P9World) -> list[AuditEventType]:
    return [
        e.event_type
        for e in world.session.scalars(
            select(AuditEvent)
            .where(AuditEvent.project_id == world.project_id)
            .order_by(AuditEvent.seq)
        )
    ]


def test_p9_exit_story(world: P9World) -> None:
    session, pid = world.session, world.project_id
    service = world.service()
    config = service.rules.config

    # -- 1. an approved baseline -------------------------------------------------------
    members = world.p8.baseline_members(world.baseline_id)
    assert members, "step 1"
    assert all(v.state is RequirementState.BASELINED for v in members), "step 1"

    # -- 2. the governed risk register --------------------------------------------------
    risks = list(session.scalars(select(Risk).where(Risk.project_id == pid)))
    assert risks, "step 2: P7 recorded risks"
    in_scope = {v.id for v in members}
    governed = [
        r for r in risks if r.requirement_version_id in in_scope or r.requirement_version_id is None
    ]
    assert governed, "step 2"
    assert not [r for r in governed if r.status is RiskStatus.UNDER_REVIEW], "step 2: G8 decided"
    assert [r for r in risks if r.requirement_version_id not in in_scope], (
        "step 2: the unapproved requirements' risks exist but are outside the approved scope"
    )

    first = world.start()
    assert first.status is GraphRunStatus.COMPLETED, f"step 3: {first.errors}"
    run_a = service.get(pid, first.sdlc_run_id)
    assert run_a.facts_summary["eligible_risks"] >= 1, "step 2"

    # -- 3. thirteen factors with evidence and provenance -------------------------------
    factors = service.factors(pid, run_a.id)
    assert [f.factor_id for f in factors] == [str(f) for f in FACTOR_ORDER], "step 3"
    by_id = {f.factor_id: f for f in factors}
    for row in factors:
        assert row.rationale and row.evidence_state, f"step 3: {row.factor_id}"
        assert row.evidence_refs or row.evidence_state == "not_recorded", f"step 3: {row.factor_id}"
    for f in ("security_risk", "consequences_of_failure", "regulatory_criticality",
              "project_complexity"):  # fmt: skip
        assert by_id[f].source == "risk_aggregate", f"step 3: {f} is the P7 aggregate"
    assert any(r.startswith("risk:") for r in by_id["consequences_of_failure"].evidence_refs)

    # -- 4. validated proposals ---------------------------------------------------------
    assert by_id["need_for_formal_verification"].proposal_status == "accepted", "step 4"
    assert (
        abs(
            by_id["need_for_formal_verification"].score
            - by_id["need_for_formal_verification"].derived_score
        )
        <= config.max_proposal_deviation
    ), "step 4: bounded"
    assert by_id["security_risk"].proposal_status == "rejected", "step 4: risk-derived refused"

    # -- 5. deterministic rules: a recorded human override triggers R1 ------------------
    assert by_id["regulatory_criticality"].score >= 4
    override = world.runner().override(
        actor=world.p8.analyst,
        project_id=pid,
        run_id=run_a.id,
        factor="requirement_stability",
        new_score=2,
        reason="Two regulator consultations are still open (synthetic).",
        role=Role.ANALYST,
    )
    assert override.status is GraphRunStatus.COMPLETED, f"step 5: {override.errors}"
    run = service.get(pid, override.sdlc_run_id)
    assert service.get(pid, run_a.id).status is SdlcRunStatus.SUPERSEDED, "step 5"
    rules = service.rule_applications(pid, run.id)
    veto = next(r for r in rules if r.rule_id == "R1-veto-waterfall-regulated-unstable")
    assert veto.changed_ranking, "step 5: the veto moved the highest-scoring waterfall"
    candidates = service.candidates(pid, run.id)
    waterfall = next(c for c in candidates if c.candidate_key == "waterfall")
    assert waterfall.vetoed_by and waterfall.rank > 1, "step 5"
    assert waterfall.normalised_score == max(c.normalised_score for c in candidates), (
        "step 5: the veto moves the rank, never the score"
    )

    # -- 6. weighted MCDA, recomputed by hand from the persisted profile ---------------
    profile = {FactorId(r.factor_id): r.score for r in service.factors(pid, run.id)}
    top = candidates[0]
    definition = config.candidate(top.candidate_key)
    raw = sum(
        config.weight(f) * definition.coefficients[f] * norm(profile[f]) for f in FACTOR_ORDER
    )
    maximum = sum(config.weight(f) * abs(definition.coefficients[f]) for f in FACTOR_ORDER)
    assert top.raw_score == pytest.approx(raw, abs=1e-6), "step 6"
    assert top.mcda_score == pytest.approx(round(50 * (1 + raw / maximum), 2)), "step 6"

    # -- 7. a persisted, reproducible ranking -------------------------------------------
    assert [c.rank for c in candidates] == list(range(1, 8)), "step 7"
    assert run.top_candidate == top.candidate_key != "waterfall", "step 7"
    again = score_candidates(profile, config)
    assert ranking_hash(again, config.ruleset_ref, config.weights_version) == run.ranking_hash
    assert run.reversal_conditions is not None, "step 7: FR-SDL-007 recorded"

    # -- 8/9. the explanation after the ranking, and its consistency --------------------
    assert run.explanation_status is ExplanationStatus.GENERATED, "step 8"
    assert run.explanation_narrative and run.explained_at >= run.created_at, "step 8"
    assert run.asserted_top_candidate == run.top_candidate, "step 9"
    assert run.explanation_discrepancies == [], "step 9"
    assert any(c["candidate"] == run.runner_up_candidate for c in run.explanation_counter_arguments)

    # -- 10. four G6 tasks --------------------------------------------------------------
    tasks = world.g6_tasks(ApprovalTaskStatus.OPEN)
    assert {t.required_role for t in tasks} == {
        Role.PROJECT_MANAGER,
        Role.ARCHITECT,
        Role.SECURITY_REVIEWER,
        Role.COMPLIANCE_OFFICER,
    }, "step 10"
    assert len(tasks) == 4 and len({t.task_group_id for t in tasks}) == 1, "step 10"
    assert {t.subject_id for t in tasks} == {run.id}, "step 10"
    stale_tasks = [
        t for t in world.g6_tasks(ApprovalTaskStatus.CANCELLED) if t.subject_id == run_a.id
    ]
    assert len(stale_tasks) == 4, "step 10: the superseded run's G6 was cancelled"

    # -- 17 (part). unauthorised, stale and wrong-role decisions --------------------------
    by_role = {t.required_role: t for t in tasks}
    with pytest.raises(AuthorizationError):
        world.decide(by_role[Role.PROJECT_MANAGER], actor=world.p8.analyst, role=Role.ANALYST)
    with pytest.raises(ApprovalError):
        world.decide(by_role[Role.PROJECT_MANAGER], actor=world.architect, role=Role.ARCHITECT)
    with pytest.raises(ApprovalError):
        world.decide(stale_tasks[0])
    # The analyst who started the run and overrode a factor holds no G6 role; even
    # holding one, the self-approval rule refuses them.
    analyst_as_co = Actor(
        actor_id=world.p8.analyst.actor_id,
        kind=world.p8.analyst.kind,
        roles_by_project={pid: frozenset({Role.ANALYST, Role.COMPLIANCE_OFFICER})},
    )
    with pytest.raises(SelfApprovalError):
        world.decide(by_role[Role.COMPLIANCE_OFFICER], actor=analyst_as_co)

    # -- 11. no single role completes G6 --------------------------------------------------
    order = [Role.PROJECT_MANAGER, Role.ARCHITECT, Role.SECURITY_REVIEWER]
    for role in order:
        world.decide(by_role[role])
        current = service.get(pid, run.id)
        assert current.status is SdlcRunStatus.AWAITING_G6, f"step 11: after {role}"
        assert current.selected_candidate is None, "step 11"

    # -- 12/13. all four approve; the selection is the computed first --------------------
    world.decide(by_role[Role.COMPLIANCE_OFFICER])
    run = service.get(pid, run.id)
    assert run.status is SdlcRunStatus.SELECTED, "step 12"
    assert run.selected_candidate == run.top_candidate == candidates[0].candidate_key, "step 13"
    assert run.selected_at is not None, "step 13"
    for task in tasks:
        session.refresh(task)
        assert task.status is ApprovalTaskStatus.APPROVED, "step 12"

    # -- 14. the audit chain -------------------------------------------------------------
    kinds = audit_types(world)
    for event in (
        AuditEventType.FACTOR_PROPOSED,
        AuditEventType.FACTOR_OVERRIDDEN,
        AuditEventType.RULES_APPLIED,
        AuditEventType.MCDA_COMPUTED,
        AuditEventType.EXPLANATION_GENERATED,
        AuditEventType.SDLC_RUN_SUPERSEDED,
        AuditEventType.APPROVAL_TASK_CREATED,
        AuditEventType.APPROVAL_GRANTED,
        AuditEventType.GATE_PASSED,
        AuditEventType.SDLC_SELECTION_RECORDED,
    ):
        assert event in kinds, f"step 14: {event}"
    assert kinds.index(AuditEventType.MCDA_COMPUTED) < kinds.index(
        AuditEventType.EXPLANATION_GENERATED
    ), "step 14: the ranking precedes its explanation"
    assert AuditService(session).verify_project_chain(pid)[0], "step 14: the hash chain verifies"

    # -- 15. traceability -----------------------------------------------------------------
    TraceGraphSync(session, world.p8.analyst).sync(pid)
    graph = TraceQueryService(session, world.p8.analyst).graph(pid)
    rid = str(run.id)
    touching = {
        (e.from_type, e.link_type, e.to_type) for e in graph.links if rid in (e.from_id, e.to_id)
    }
    assert (str(N.BASELINE), str(L.INFORMED), str(N.SDLC_RUN)) in touching, "step 15"
    assert (str(N.SDLC_RUN), str(L.CONTAINS), str(N.SDLC_FACTOR)) in touching, "step 15"
    assert (str(N.SDLC_RUN), str(L.APPROVED_BY), str(N.APPROVAL_DECISION)) in touching, "step 15"
    factor_ids = {str(f.id) for f in service.factors(pid, run.id)}
    feeders = {e.from_type for e in graph.links if e.to_id in factor_ids}
    assert {str(N.RISK), str(N.REQUIREMENT_VERSION), str(N.BASELINE)} <= feeders, "step 15"

    # -- 16. project isolation ------------------------------------------------------------
    other = make_p9_world(session, "Another lender (synthetic)")
    with pytest.raises(ProjectIsolationError):
        world.service(other.p8.analyst).get(pid, run.id)
    assert other.service().list_runs(other.project_id) == [], "step 16"
    with pytest.raises(ProjectIsolationError):
        other.runner().start(actor=other.p8.analyst, project_id=pid, baseline_id=world.baseline_id)

    # -- 17. a decided task cannot be re-decided; a decision after selection is refused ---
    with pytest.raises(ApprovalError):
        world.decide(by_role[Role.ARCHITECT], ApprovalDecisionType.REJECT, justification="late")

    # -- no P10 ------------------------------------------------------------------------------
    # The workflow tables exist from P10; the P9 story itself generates no workflow -
    # that is a separate, human-requested step after G6 (tests/workflow/test_p10_exit_test.py).
    from reqpilot.domain.models.workflow import Workflow

    assert not session.scalars(select(Workflow).where(Workflow.project_id == pid)).all(), (
        "the P9 story generates no P10 workflow"
    )
