"""G8 and the baseline block (P7; ``FR-RSK-007``, architecture I.5, M.3).

The governance half of the phase. Every test here asks one of two questions:

* can a high-severity risk reach a baseline without a human having looked?
* can anyone but the right human, on the right subject, at the right version,
  clear it?

The answer must be no in every direction, and it must be no in *deterministic
application code* - not in the UI, not in a prompt, and not as a warning. The
baseline tests therefore drive the real lifecycle transition and assert that it
raises, rather than inspecting a flag.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.conftest import make_actor
from tests.p7_helpers import make_p7_world

from reqpilot.domain.enums import (
    ActorKind,
    ApprovalDecisionType,
    ApprovalTaskStatus,
    AuditEventType,
    Gate,
    RiskSeverity,
    RiskStatus,
    Role,
)
from reqpilot.domain.errors import (
    ApprovalError,
    AuthorizationError,
    ProjectIsolationError,
    SelfApprovalError,
    StaleApprovalError,
    StateTransitionError,
)
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.audit import AuditEvent
from reqpilot.domain.models.risk import Risk
from reqpilot.services.approval.service import ApprovalService
from reqpilot.services.requirements import RequirementService
from reqpilot.services.requirements.service import RequirementContent
from reqpilot.services.risk.gates import RISK_SUBJECT

pytestmark = pytest.mark.integration


@pytest.fixture
def world(db_session: Session):
    w = make_p7_world(db_session)
    w.full_analysis()
    return w


def advance(world, version_id: uuid.UUID, target: RequirementState):
    """Drive the real lifecycle transition, so a guard failure is a real failure."""
    return RequirementService(world.session, world.analyst).transition(
        project_id=world.project_id, version_id=version_id, target=target
    )


def clear_p6_gates(world, version_id: uuid.UUID) -> None:
    """Settle the G2/G3 tasks P6 raised for this version.

    A baseline needs every blocking gate cleared, not only G8. Clearing P6's
    here keeps the P7 tests about P7: what is left blocking is the risk.
    """
    from reqpilot.repositories.compliance import (
        ComplianceMappingRepository,
        SecurityFindingRepository,
    )

    subjects: list[tuple[uuid.UUID, Role]] = []
    for mapping in ComplianceMappingRepository(world.session, world.analyst).list_for_project(
        world.project_id, version_id=version_id
    ):
        if mapping.approval_task_id is not None:
            subjects.append((mapping.approval_task_id, Role.COMPLIANCE_OFFICER))
    for finding in SecurityFindingRepository(world.session, world.analyst).list_for_project(
        world.project_id, version_id=version_id
    ):
        if finding.approval_task_id is not None:
            subjects.append((finding.approval_task_id, Role.SECURITY_REVIEWER))
    for task_id, role in subjects:
        decider = (
            world.compliance_officer if role is Role.COMPLIANCE_OFFICER else world.security_reviewer
        )
        task = ApprovalService(world.session, decider).get_task(world.project_id, task_id)
        if task is None or task.status is not ApprovalTaskStatus.OPEN:
            continue
        ApprovalService(world.session, decider).decide(
            project_id=world.project_id,
            task_id=task_id,
            decision=ApprovalDecisionType.APPROVE,
            role_exercised=role,
            justification="Reviewed for the P7 baseline test.",
        )


def to_analyzed(world, version_id: uuid.UUID) -> None:
    """Walk the real lifecycle to ANALYZED, the state the risk guard applies at."""
    for state in (
        RequirementState.EXTRACTED,
        RequirementState.CLASSIFIED,
        RequirementState.ANALYZED,
    ):
        advance(world, version_id, state)


# --- threshold and task creation ---------------------------------------------------------------


def test_only_high_severity_risks_raise_g8(world) -> None:
    tasks = world.g8_tasks()
    gated = {t.subject_id for t in tasks}
    for risk in world.risks():
        if risk.severity is RiskSeverity.HIGH:
            assert risk.id in gated, f"a HIGH risk did not raise G8: {risk.title}"
        else:
            assert risk.id not in gated


def test_a_g8_task_is_blocking_and_belongs_to_the_security_reviewer(world) -> None:
    for task in world.g8_tasks():
        assert task.gate is Gate.G8_HIGH_SEVERITY_RISK
        assert task.subject_type == RISK_SUBJECT
        assert task.blocking is True
        assert task.required_role is Role.SECURITY_REVIEWER
        assert task.status is ApprovalTaskStatus.OPEN


def test_the_gate_task_is_bound_to_the_risk_it_was_raised_for(world) -> None:
    for task in world.g8_tasks():
        risk = world.session.get(Risk, task.subject_id)
        assert risk is not None
        assert task.subject_version_hash == risk.content_hash
        assert risk.approval_task_id == task.id


def test_the_escalation_is_audited_with_the_severity_that_caused_it(world) -> None:
    events = [
        e
        for e in world.session.scalars(select(AuditEvent))
        if e.event_type is AuditEventType.RISK_ESCALATED
    ]
    assert events
    for event in events:
        assert event.payload["gate"] == str(Gate.G8_HIGH_SEVERITY_RISK)
        assert event.payload["severity"] == str(RiskSeverity.HIGH)
        assert event.payload["blocking"] is True
        assert event.payload["required_role"] == str(Role.SECURITY_REVIEWER)


def test_the_fan_out_reads_the_persisted_severity_not_the_proposal(world) -> None:
    """A risk the model rated low but the matrix rated HIGH is still escalated.

    The scripted model proposes ratings, never a severity; the matrix produced
    HIGH from L2xI3 and L3xI3, and those are exactly the risks with a G8 task.
    """
    gated = {t.subject_id for t in world.g8_tasks()}
    high = {r.id for r in world.risks() if r.severity is RiskSeverity.HIGH}
    assert gated == high


# --- who may decide -------------------------------------------------------------------------


def test_the_correct_reviewer_can_approve_and_the_risk_is_accepted(world) -> None:
    risk_id = world.high_risk().id
    outcome = world.decide_g8(risk_id)
    assert outcome.task_closed and outcome.task.status is ApprovalTaskStatus.APPROVED
    risk = world.session.get(Risk, risk_id)
    assert risk.status is RiskStatus.ACCEPTED
    assert risk.decided_by == world.security_reviewer.actor_id
    assert risk.decision_rationale


def test_rejecting_at_g8_also_clears_the_block(world) -> None:
    """Either decision means a human has looked, which is what FR-RSK-007 requires."""
    risk_id = world.high_risk().id
    world.decide_g8(
        risk_id, decision=ApprovalDecisionType.REJECT, justification="Not a risk as recorded."
    )
    assert world.session.get(Risk, risk_id).status is RiskStatus.REJECTED


@pytest.mark.parametrize(
    "role", [Role.ANALYST, Role.COMPLIANCE_OFFICER, Role.PROJECT_MANAGER, Role.AUDITOR]
)
def test_the_wrong_role_cannot_decide_g8(world, role: Role) -> None:
    actor = make_actor(project_id=world.project_id, roles={role})
    with pytest.raises((AuthorizationError, ApprovalError)):
        world.decide_g8(world.high_risk().id, actor=actor, role=role)
    world.session.rollback()


def test_holding_the_right_role_in_another_project_is_not_enough(world, db_session) -> None:
    other = make_p7_world(db_session, "P7 elsewhere")
    with pytest.raises(ProjectIsolationError):
        world.decide_g8(world.high_risk().id, actor=other.security_reviewer)
    db_session.rollback()


def test_a_g8_task_cannot_be_decided_from_another_project(world, db_session) -> None:
    other = make_p7_world(db_session, "P7 elsewhere")
    other.full_analysis()
    task = world.g8_task_for(world.high_risk().id)
    # The other project's reviewer, using this project's task id, in their own
    # project: the task is simply not there.
    from reqpilot.domain.errors import ReqPilotError

    with pytest.raises(ReqPilotError, match="not found in this project"):
        ApprovalService(db_session, other.security_reviewer).decide(
            project_id=other.project_id,
            task_id=task.id,
            decision=ApprovalDecisionType.APPROVE,
            role_exercised=Role.SECURITY_REVIEWER,
            justification="cross-project",
        )
    db_session.rollback()


@pytest.mark.parametrize("kind", [ActorKind.AGENT_ROLE, ActorKind.SYSTEM])
def test_a_non_human_actor_cannot_decide_g8(world, kind: ActorKind) -> None:
    """The pipeline raises G8; nothing it carries lets it decide one."""
    agent = make_actor(project_id=world.project_id, roles={Role.SECURITY_REVIEWER}, kind=kind)
    with pytest.raises(AuthorizationError, match="never decide an approval gate"):
        world.decide_g8(world.high_risk().id, actor=agent)
    world.session.rollback()


def test_the_author_of_the_requirement_cannot_sign_off_its_risk(world) -> None:
    """No self-approval: the analyst whose requirement produced the risk is barred
    even if they also hold the Security Reviewer role."""
    author = world.versions["C01"].created_by
    both = make_actor(project_id=world.project_id, roles={Role.SECURITY_REVIEWER})
    both = type(both)(actor_id=author, kind=both.kind, roles_by_project=both.roles_by_project)
    risk = next(
        r
        for r in world.risks()
        if r.severity is RiskSeverity.HIGH and r.requirement_version_id is not None
    )
    with pytest.raises(SelfApprovalError):
        world.decide_g8(risk.id, actor=both)
    world.session.rollback()


def test_a_decided_task_cannot_be_decided_again(world) -> None:
    risk_id = world.high_risk().id
    world.decide_g8(risk_id)
    with pytest.raises(ApprovalError, match="already"):
        world.decide_g8(risk_id)
    world.session.rollback()


def test_a_modify_decision_leaves_the_risk_under_review(world) -> None:
    risk_id = world.high_risk().id
    outcome = world.decide_g8(
        risk_id, decision=ApprovalDecisionType.MODIFY, justification="Reword the description."
    )
    assert not outcome.task_closed
    assert world.session.get(Risk, risk_id).status is RiskStatus.UNDER_REVIEW


# --- staleness and versioning ------------------------------------------------------------------


def test_a_g8_decision_on_a_superseded_version_is_refused_as_stale(world) -> None:
    """``FR-RSK-007`` with P1's version identity: a later version is a different
    subject, needing its own analysis and its own gate."""
    risk = next(
        r
        for r in world.risks()
        if r.severity is RiskSeverity.HIGH and r.requirement_version_id is not None
    )
    version = world.session.get(
        type(next(iter(world.versions.values()))), risk.requirement_version_id
    )
    RequirementService(world.session, world.analyst).create_version(
        project_id=world.project_id,
        requirement_id=version.requirement_id,
        content=RequirementContent(
            statement="The system shall require two-step verification for privileged users.",
            source_refs=tuple(version.source_refs or ()),
        ),
        change_reason="revised after review",
    )
    with pytest.raises(StaleApprovalError, match="no longer current"):
        world.decide_g8(risk.id)
    world.session.rollback()


def test_a_new_version_gets_its_own_analysis_and_the_old_risks_stand(world) -> None:
    risk = next(
        r
        for r in world.risks()
        if r.severity is RiskSeverity.HIGH and r.requirement_version_id is not None
    )
    old_version_id = risk.requirement_version_id
    old_hash, old_severity, old_status = risk.content_hash, risk.severity, risk.status
    version = world.session.get(type(next(iter(world.versions.values()))), old_version_id)
    new_version = RequirementService(world.session, world.analyst).create_version(
        project_id=world.project_id,
        requirement_id=version.requirement_id,
        content=RequirementContent(
            statement=(
                "Loan officers shall complete multi-factor authentication, including a "
                "second factor, before opening an application."
            ),
            source_refs=tuple(version.source_refs or ()),
        ),
        change_reason="revised after review",
    )
    world.full_analysis()
    # v1's risk is untouched: same hash, same rating, same status.
    unchanged = world.session.get(Risk, risk.id)
    assert unchanged.content_hash == old_hash
    assert unchanged.severity is old_severity and unchanged.status is old_status
    assert unchanged.requirement_version_id == old_version_id
    # v2 has its own risks, and they are different rows.
    v2_risks = world.risks(version_id=new_version.id)
    assert v2_risks and all(r.id != risk.id for r in v2_risks)
    # A stale G8 task from v1 does not govern v2: v2's own high risks have their
    # own tasks, bound to their own hashes.
    for v2_risk in v2_risks:
        if v2_risk.severity is RiskSeverity.HIGH:
            task = world.g8_task_for(v2_risk.id)
            assert task.subject_version_hash == v2_risk.content_hash
            assert task.subject_id != risk.id


# --- the baseline block (FR-RSK-007) ------------------------------------------------------------


def version_with_high_risk(world):
    risk = next(
        r
        for r in world.risks()
        if r.severity is RiskSeverity.HIGH and r.requirement_version_id is not None
    )
    return risk, risk.requirement_version_id


def version_without_high_risk(world):
    high = {r.requirement_version_id for r in world.risks() if r.severity is RiskSeverity.HIGH}
    for version in world.versions.values():
        if version.id not in high and world.risks(version_id=version.id):
            return version
    raise AssertionError("the scripted world has a version with only low/medium risks")


def test_a_requirement_with_low_or_medium_risk_is_not_blocked_by_risk(world) -> None:
    """Low and medium risks are recorded and tracked; they do not block (I.5)."""
    from reqpilot.domain.lifecycle import check_transition

    version = version_without_high_risk(world)
    to_analyzed(world, version.id)
    context = RequirementService(world.session, world.analyst).build_context(
        world.project_id, world.session.get(type(version), version.id)
    )
    # The P7 contribution to the guard is zero for this version...
    assert context.unreviewed_high_risk_count == 0
    reason = check_transition(RequirementState.ANALYZED, RequirementState.VALIDATED, context)
    assert "high-severity risk" not in (reason or "")
    # ...and with the P6 gates settled, the transition really does succeed.
    clear_p6_gates(world, version.id)
    advance(world, version.id, RequirementState.VALIDATED)
    assert world.session.get(type(version), version.id).state is RequirementState.VALIDATED


def test_a_high_severity_risk_blocks_the_baseline_transition(world) -> None:
    """The transition itself fails. Not a warning, not a UI check."""
    _risk, version_id = version_with_high_risk(world)
    to_analyzed(world, version_id)
    with pytest.raises(StateTransitionError, match="unreviewed high-severity risk"):
        advance(world, version_id, RequirementState.VALIDATED)
    world.session.rollback()


def test_the_block_holds_even_if_the_g8_task_is_missing(world) -> None:
    """Fail closed: the guard counts the persisted risk, not the task.

    A task that was never raised, or was cancelled, cannot unblock a baseline -
    which is what makes the block a property of the risk rather than of the
    workflow around it.
    """
    risk, version_id = version_with_high_risk(world)
    task = world.g8_task_for(risk.id)
    task.status = ApprovalTaskStatus.CANCELLED
    world.session.flush()
    to_analyzed(world, version_id)
    with pytest.raises(StateTransitionError, match="unreviewed high-severity risk"):
        advance(world, version_id, RequirementState.VALIDATED)
    world.session.rollback()


def test_after_the_g8_decision_the_baseline_can_proceed(world) -> None:
    risk, version_id = version_with_high_risk(world)
    to_analyzed(world, version_id)
    clear_p6_gates(world, version_id)
    # With every other gate settled, the risk is the only thing still blocking.
    with pytest.raises(StateTransitionError, match="unreviewed high-severity risk"):
        advance(world, version_id, RequirementState.VALIDATED)
    world.decide_g8(risk.id)
    # The same transition now succeeds, and the G8 decision is the only change.
    advance(world, version_id, RequirementState.VALIDATED)
    version = world.session.get(type(next(iter(world.versions.values()))), version_id)
    assert version.state is RequirementState.VALIDATED


def test_accepting_the_risk_in_the_register_does_not_clear_the_gate(world) -> None:
    """A register decision is not a gate decision: the blocking task still stands."""
    from reqpilot.services.risk import RiskService

    risk, version_id = version_with_high_risk(world)
    RiskService(world.session, world.analyst).decide(
        project_id=world.project_id,
        risk_id=risk.id,
        status=RiskStatus.ACCEPTED,
        rationale="Accepted in the register by the analyst.",
    )
    to_analyzed(world, version_id)
    # The risk no longer counts as unreviewed, but its blocking G8 task does.
    with pytest.raises(StateTransitionError, match="blocking approval task"):
        advance(world, version_id, RequirementState.VALIDATED)
    world.session.rollback()


def test_a_g8_decision_never_moves_a_requirements_lifecycle_state(world) -> None:
    risk, version_id = version_with_high_risk(world)
    before = world.session.get(type(next(iter(world.versions.values()))), version_id).state
    world.decide_g8(risk.id)
    after = world.session.get(type(next(iter(world.versions.values()))), version_id).state
    assert after is before, "approving a risk is not approving a requirement (that is G1)"


def test_the_decision_is_audited_against_the_risk(world) -> None:
    risk_id = world.high_risk().id
    world.decide_g8(risk_id)
    events = [
        e
        for e in world.session.scalars(select(AuditEvent))
        if e.event_type is AuditEventType.RISK_DECISION_RECORDED
    ]
    assert events
    recorded = events[-1]
    assert recorded.subject_type == RISK_SUBJECT
    assert recorded.payload["gate"] == str(Gate.G8_HIGH_SEVERITY_RISK)
    assert recorded.payload["status"] == str(RiskStatus.ACCEPTED)
    assert recorded.payload["subject_version_hash"]
