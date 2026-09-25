"""P8 approval governance on the synthetic world (SQLite): G1, G2/G3/G8 integration,
G4, G5, G7, baseline readiness and the gate fan-out - all through the one approval
service, all fail-closed."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.conftest import make_actor
from tests.p8_helpers import P8World, make_p8_world

from reqpilot.domain.enums import (
    ActorKind,
    ApprovalDecisionType,
    ApprovalTaskStatus,
    AuditEventType,
    Gate,
    RequirementCategory,
    Role,
)
from reqpilot.domain.errors import (
    ApprovalError,
    AuthorizationError,
    GovernanceBlockedError,
    ProjectIsolationError,
    SelfApprovalError,
    StaleApprovalError,
    StateTransitionError,
)
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.approval import ApprovalDecision
from reqpilot.domain.models.audit import AuditEvent
from reqpilot.services.approval.service import ApprovalService
from reqpilot.services.governance import GovernanceFanOut, GovernanceReadinessService
from reqpilot.services.requirements import RequirementContent, RequirementService

pytestmark = pytest.mark.integration


@pytest.fixture
def world(db_session: Session) -> P8World:
    return make_p8_world(db_session)


def codes(world: P8World, key: str) -> set[str]:
    readiness = GovernanceReadinessService(world.session, world.analyst)
    return {
        b.code
        for b in readiness.evaluate(
            world.project_id, world.version(key), stage="submission"
        ).blockers
    }


# --- the world really exercises every gate ----------------------------------------


def test_the_world_raises_real_p6_p7_gates_and_needs_p8_gates(world) -> None:
    assert {"G2_PENDING", "G3_PENDING", "G8_UNREVIEWED"} <= codes(world, "L01")
    assert "G5_REQUIRED" in codes(world, "L05")
    assert "OPEN_CONFLICT" in codes(world, "L06")
    assert world.conflict().involves_stakeholder_disagreement


# --- G1: co-approval preserved; readiness enforced at submission and approval -------


def test_submission_is_refused_while_g2_g3_g8_are_unresolved(world) -> None:
    world.to_analyzed("L01")
    # The P1/P6/P7 guard already refuses VALIDATED; P8 does not weaken it.
    with pytest.raises(StateTransitionError):
        RequirementService(world.session, world.analyst).transition(
            project_id=world.project_id,
            version_id=world.versions["L01"].id,
            target=RequirementState.VALIDATED,
        )
    world.clear_analysis_gates("L01")
    assert not codes(world, "L01")


def test_g1_is_still_co_approval_and_one_signature_changes_nothing(world) -> None:
    world.validate("L03")
    tasks = world.submit(["L03"])
    assert {t.required_role for t in tasks} == {Role.ANALYST, Role.COMPLIANCE_OFFICER}
    analyst_task = next(t for t in tasks if t.required_role is Role.ANALYST)
    outcome = world.decide(analyst_task)
    assert outcome.baseline_id is None and not outcome.group_complete
    assert world.version("L03").state is RequirementState.PENDING_APPROVAL
    officer_task = next(t for t in tasks if t.required_role is Role.COMPLIANCE_OFFICER)
    outcome = world.decide(officer_task)
    assert outcome.baseline_id is not None
    assert world.version("L03").state is RequirementState.BASELINED


def test_a_g1_approval_is_refused_if_a_blocker_appears_after_submission(world) -> None:
    world.validate("L03")
    tasks = world.submit(["L03"])
    # An analyst flags the pending version architecture-critical: G5 now applies.
    GovernanceFanOut(world.session, world.analyst).flag_architecture_critical(
        world.project_id, world.versions["L03"].id, "It fixes the status API contract."
    )
    with pytest.raises(GovernanceBlockedError, match="G5"):
        world.decide(tasks[0])
    world.sign_g5()
    for task in tasks:
        world.decide(task)
    assert world.version("L03").state is RequirementState.BASELINED


def test_the_g1_author_still_cannot_sign(world) -> None:
    """A version a human analyst authored may not be signed by that analyst (P1)."""
    service = RequirementService(world.session, world.analyst)
    _requirement, version = service.create_requirement(
        project_id=world.project_id,
        domain="LOAN",
        content=RequirementContent(
            statement="The system shall email the applicant a receipt.",
            category=RequirementCategory.FUNCTIONAL,
            source_refs=tuple(world.version("L03").source_refs),
        ),
    )
    for target in (
        RequirementState.EXTRACTED,
        RequirementState.CLASSIFIED,
        RequirementState.ANALYZED,
        RequirementState.VALIDATED,
    ):
        service.transition(project_id=world.project_id, version_id=version.id, target=target)
    tasks = ApprovalService(world.session, world.analyst).submit_versions_for_baseline(
        project_id=world.project_id, version_ids=[version.id]
    )
    task = next(t for t in tasks if t.required_role is Role.ANALYST)
    with pytest.raises(SelfApprovalError):
        world.decide(task, actor=world.analyst)


# --- G4: conflicting stakeholder decision --------------------------------------------


def test_g4_requires_the_analyst_and_each_affected_stakeholder(world) -> None:
    world.resolve_conflict()
    # The P5 guard lifts on resolution (P5 semantics unchanged) ...
    world.to_analyzed("L06")
    world.clear_analysis_gates("L06")
    RequirementService(world.session, world.analyst).transition(
        project_id=world.project_id,
        version_id=world.versions["L06"].id,
        target=RequirementState.VALIDATED,
    )
    # ... but the stakeholder disagreement still needs G4 before G1.
    assert codes(world, "L06") == {"G4_REQUIRED"}
    with pytest.raises(GovernanceBlockedError, match="G4"):
        world.submit(["L06"])
    result = world.fan_out()
    roles = sorted(t.required_role.value for t in result.g4)
    assert roles == ["analyst", "stakeholder", "stakeholder"]
    assignees = {t.assignee_user_id for t in result.g4 if t.required_role is Role.STAKEHOLDER}
    assert assignees == {world.priya.actor_id, world.omar.actor_id}
    assert len({t.task_group_id for t in result.g4}) == 1
    assert codes(world, "L06") == {"G4_PENDING"}
    # Idempotent: nothing new is raised the second time.
    assert world.fan_out().total == 0
    world.sign_g4()
    assert not codes(world, "L06")
    events = {e.event_type for e in world.session.scalars(select(AuditEvent))}
    assert AuditEventType.CONFLICT_GATE_SETTLED in events


def test_g4_signatures_are_personal_role_bound_and_not_self_approved(world) -> None:
    world.resolve_conflict()
    tasks = world.fan_out().g4
    priya_task = next(t for t in tasks if t.assignee_user_id == world.priya.actor_id)
    analyst_task = next(t for t in tasks if t.required_role is Role.ANALYST)
    with pytest.raises(ApprovalError, match="assigned"):
        world.decide(priya_task, actor=world.omar)  # the other stakeholder
    with pytest.raises(AuthorizationError):
        world.decide(analyst_task, actor=world.priya)  # a stakeholder is not an analyst
    # No self-approval: whoever authored either conflicting version may not sign.
    # (P3-extracted versions are authored by the extraction run's actor.)
    from reqpilot.services.governance import GovernanceGateService

    authors = (
        GovernanceGateService(world.session, world.analyst)
        .subject(world.project_id, analyst_task)
        .authors
    )
    assert authors == {world.version("L06").created_by, world.version("L07").created_by}
    author = next(iter(authors))
    as_author = make_actor(project_id=world.project_id, roles={Role.ANALYST})
    as_author = type(as_author)(
        actor_id=author, kind=ActorKind.HUMAN, roles_by_project=as_author.roles_by_project
    )
    with pytest.raises(SelfApprovalError):
        world.decide(analyst_task, actor=as_author)
    agent = make_actor(
        project_id=world.project_id, roles={Role.STAKEHOLDER}, kind=ActorKind.AGENT_ROLE
    )
    with pytest.raises(AuthorizationError):
        ApprovalService(world.session, agent).decide(
            project_id=world.project_id,
            task_id=priya_task.id,
            decision=ApprovalDecisionType.APPROVE,
            role_exercised=Role.STAKEHOLDER,
        )


def test_a_g4_rejection_keeps_the_version_blocked(world) -> None:
    world.resolve_conflict()
    tasks = world.fan_out().g4
    omar_task = next(t for t in tasks if t.assignee_user_id == world.omar.actor_id)
    world.decide(
        omar_task,
        ApprovalDecisionType.REJECT,
        actor=world.omar,
        justification="Operations never agreed to 15 minutes.",
    )
    remaining = world.tasks(gate=Gate.G4_STAKEHOLDER_CONFLICT)
    assert {t.status for t in remaining} == {
        ApprovalTaskStatus.REJECTED,
        ApprovalTaskStatus.CANCELLED,
    }
    assert "G4_REJECTED" in codes(world, "L06")


def test_there_is_still_no_conflicted_state(world) -> None:
    assert "CONFLICTED" not in {s.value for s in RequirementState}
    world.resolve_conflict()
    assert world.version("L06").state is not None


# --- G5: architecture-critical requirement -------------------------------------------


def test_g5_is_raised_by_the_m3_predicate_and_decided_by_the_project_manager(world) -> None:
    assert codes(world, "L05") == {"G5_REQUIRED"}
    (task,) = world.fan_out().g5
    assert (
        task.required_role is Role.PROJECT_MANAGER and task.subject_id == world.versions["L05"].id
    )
    # An open G5 task blocks VALIDATED through the P1 guard (architecture H.3).
    world.to_analyzed("L05")
    with pytest.raises(StateTransitionError, match="blocking approval task"):
        RequirementService(world.session, world.analyst).transition(
            project_id=world.project_id,
            version_id=world.versions["L05"].id,
            target=RequirementState.VALIDATED,
        )
    with pytest.raises(AuthorizationError):
        world.decide(task, actor=world.reviewer_analyst)
    world.decide(task)
    assert not codes(world, "L05")
    world.validate("L05")


def test_a_g5_rejection_sends_the_version_back_to_clarification(world) -> None:
    (task,) = world.fan_out().g5
    world.to_analyzed("L05")
    world.decide(task, ApprovalDecisionType.REJECT, justification="Unclear load profile.")
    assert world.version("L05").state is RequirementState.CLARIFICATION_REQUIRED
    assert "G5_REJECTED" in codes(world, "L05")


def test_an_analyst_flag_raises_g5_and_is_audited(world) -> None:
    task = GovernanceFanOut(world.session, world.analyst).flag_architecture_critical(
        world.project_id, world.versions["L04"].id, "It defines the disbursement service boundary."
    )
    assert task.gate is Gate.G5_ARCHITECTURE_CRITICAL
    assert "G5_PENDING" in codes(world, "L04")
    with pytest.raises(ApprovalError):
        GovernanceFanOut(world.session, world.analyst).flag_architecture_critical(
            world.project_id, world.versions["L04"].id, "again"
        )
    events = [
        e
        for e in world.session.scalars(select(AuditEvent))
        if e.event_type is AuditEventType.ARCHITECTURE_CRITICAL_FLAGGED
    ]
    assert events and "reason" not in str(events[0].payload).lower().replace("reason_supplied", "")


def test_only_a_human_analyst_may_flag(world) -> None:
    for actor in (
        world.compliance_officer,
        make_actor(project_id=world.project_id, roles={Role.ANALYST}, kind=ActorKind.AGENT_ROLE),
    ):
        with pytest.raises(AuthorizationError):
            GovernanceFanOut(world.session, actor).flag_architecture_critical(
                world.project_id, world.versions["L04"].id, "x"
            )


# --- G7: change to an approved requirement --------------------------------------------


def _baseline_l03(world: P8World):  # type: ignore[no-untyped-def]
    world.validate("L03")
    return world.approve_g1(world.submit(["L03"]), "B1")


def _successor(world: P8World, statement: str):  # type: ignore[no-untyped-def]
    v1 = world.version("L03")
    return RequirementService(world.session, world.analyst).create_version(
        project_id=world.project_id,
        requirement_id=v1.requirement_id,
        content=RequirementContent(
            statement=statement,
            category=RequirementCategory.FUNCTIONAL,
            source_refs=tuple(v1.source_refs),
        ),
        change_reason="the applicant also sees the next step",
    )


def test_g7_governs_a_change_and_the_old_approval_stays_historically_valid(world) -> None:
    b1 = _baseline_l03(world)
    v1 = world.version("L03")
    v1_decisions = [
        (d.id, d.subject_version_hash)
        for d in world.session.scalars(select(ApprovalDecision))
        if d.subject_version_hash == v1.content_hash
    ]
    v2 = _successor(
        world,
        "The system shall display the current loan status and the next step to the applicant.",
    )
    g7 = world.tasks(gate=Gate.G7_APPROVED_REQUIREMENT_CHANGE)
    assert {t.required_role for t in g7} == {Role.ANALYST, Role.COMPLIANCE_OFFICER}
    assert all(t.subject_id == v2.id and t.subject_version_hash == v2.content_hash for t in g7)
    # The approved predecessor is untouched while the change is pending.
    assert world.version("L03").state is RequirementState.BASELINED
    world.versions["L03v2"] = v2
    service = RequirementService(world.session, world.analyst)
    for target in (
        RequirementState.EXTRACTED,
        RequirementState.CLASSIFIED,
        RequirementState.ANALYZED,
    ):
        service.transition(project_id=world.project_id, version_id=v2.id, target=target)
    with pytest.raises(StateTransitionError):  # an open G7 blocks VALIDATED
        service.transition(
            project_id=world.project_id, version_id=v2.id, target=RequirementState.VALIDATED
        )
    with pytest.raises(SelfApprovalError):
        world.decide(next(t for t in g7 if t.required_role is Role.ANALYST), actor=world.analyst)
    for task in g7:
        world.decide(task)
    service.transition(
        project_id=world.project_id, version_id=v2.id, target=RequirementState.VALIDATED
    )
    b2 = world.approve_g1(
        ApprovalService(world.session, world.analyst).submit_versions_for_baseline(
            project_id=world.project_id, version_ids=[v2.id]
        ),
        "B2",
    )
    assert b2 is not None and b2 != b1
    old = world.session.get(type(v1), v1.id)
    assert old.state is RequirementState.SUPERSEDED and old.superseded_by_id == v2.id
    assert old.content_hash == v1.content_hash
    after = [
        (d.id, d.subject_version_hash)
        for d in world.session.scalars(select(ApprovalDecision))
        if d.subject_version_hash == v1.content_hash
    ]
    assert after == v1_decisions, "the predecessor's approval is never altered"
    assert [m.id for m in world.baseline_members(b1)] == [v1.id]


def test_a_g7_rejection_withdraws_the_successor(world) -> None:
    _baseline_l03(world)
    v2 = _successor(world, "The system shall display a status banner to the applicant.")
    g7 = world.tasks(gate=Gate.G7_APPROVED_REQUIREMENT_CHANGE)
    world.decide(g7[0], ApprovalDecisionType.REJECT, justification="Out of scope for this release.")
    assert world.session.get(type(v2), v2.id).state is RequirementState.WITHDRAWN
    assert world.version("L03").state is RequirementState.BASELINED
    # P1 gap closed: a further edit still needs G7, because an approved version exists.
    v3 = _successor(world, "The system shall display a status banner and a help link.")
    assert any(t.subject_id == v3.id for t in world.tasks(gate=Gate.G7_APPROVED_REQUIREMENT_CHANGE))


def test_a_stale_g7_binding_is_refused(world) -> None:
    _baseline_l03(world)
    _successor(world, "The system shall display the loan status in two languages.")
    task = world.tasks(gate=Gate.G7_APPROVED_REQUIREMENT_CHANGE)[0]
    task.subject_version_hash = "0" * 64
    world.session.flush()
    with pytest.raises(StaleApprovalError):
        world.decide(task)


def test_a_g7_task_of_a_superseded_draft_is_stale(world) -> None:
    _baseline_l03(world)
    _successor(world, "Draft one of the change.")
    old_task = world.tasks(gate=Gate.G7_APPROVED_REQUIREMENT_CHANGE)[0]
    _successor(world, "Draft two of the change.")
    with pytest.raises(StaleApprovalError):
        world.decide(old_task)


# --- isolation and the fan-out's own authority ----------------------------------------


def test_cross_project_decisions_and_fan_out_are_refused(world, db_session) -> None:
    other = make_p8_world(db_session, "Another bank (synthetic)")
    (task,) = world.fan_out().g5
    with pytest.raises(ProjectIsolationError):
        ApprovalService(db_session, other.project_manager).decide(
            project_id=world.project_id,
            task_id=task.id,
            decision=ApprovalDecisionType.APPROVE,
            role_exercised=Role.PROJECT_MANAGER,
        )
    with pytest.raises(ProjectIsolationError):
        GovernanceFanOut(db_session, other.analyst).raise_required(world.project_id)
    with pytest.raises(ProjectIsolationError):
        GovernanceReadinessService(db_session, other.analyst).evaluate(
            world.project_id, world.version("L01"), stage="submission"
        )


def test_the_fan_out_raises_but_never_decides(world) -> None:
    world.resolve_conflict()
    result = world.fan_out()
    assert result.g4 and result.g5
    assert all(t.status is ApprovalTaskStatus.OPEN for t in [*result.g4, *result.g5])
    decided = list(world.session.scalars(select(ApprovalDecision)))
    assert all(d.decided_by != world.analyst.actor_id for d in decided)
