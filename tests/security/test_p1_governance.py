"""Governance tests: every way P1 could be cheated, and why each one fails.

The happy path is proved in ``tests/workflow/test_p1_exit_test.py``. This file
is the other half: if these stop failing-closed, P1 has lost its point.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session
from tests.workflow.test_p1_exit_test import (
    content,
    drive_to_validated,
    make_member,
    make_project,
)

from reqpilot.domain.enums import (
    Action,
    ActorKind,
    ApprovalDecisionType,
    ApprovalTaskStatus,
    Gate,
    ResourceType,
    Role,
)
from reqpilot.domain.errors import (
    ApprovalError,
    AuthorizationError,
    BaselineInvariantError,
    ProjectIsolationError,
    ReqPilotError,
    SelfApprovalError,
    StaleApprovalError,
    StateTransitionError,
)
from reqpilot.domain.ids import ActorId, ProjectId, new_uuid
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.policy import Actor, Decision, ResourceRef, can
from reqpilot.domain.policy import policy as policy_module
from reqpilot.services.approval import ApprovalService
from reqpilot.services.baseline import BaselineService
from reqpilot.services.requirements import RequirementContent, RequirementService

pytestmark = pytest.mark.security


@pytest.fixture
def scenario(db_session: Session):
    project = make_project(db_session)
    return {
        "project_id": ProjectId(project.id),
        "project": project,
        "author": make_member(db_session, project, Role.ANALYST, "a@example.test"),
        "reviewer": make_member(db_session, project, Role.ANALYST, "r@example.test"),
        "officer": make_member(db_session, project, Role.COMPLIANCE_OFFICER, "c@example.test"),
        "security": make_member(db_session, project, Role.SECURITY_REVIEWER, "s@example.test"),
    }


def submitted_task(db_session: Session, scenario, statement: str = "The system shall X."):
    """A requirement driven to an open G1 task. The common starting point."""
    project_id = scenario["project_id"]
    service = RequirementService(db_session, scenario["author"])
    requirement, version = service.create_requirement(
        project_id=project_id, domain="LOAN", content=content(statement)
    )
    drive_to_validated(service, project_id, version.id)
    tasks = ApprovalService(db_session, scenario["author"]).submit_versions_for_baseline(
        project_id=project_id, version_ids=[version.id]
    )
    return requirement, version, TaskPair(tasks)


class TaskPair:
    """The two co-approval tasks G1 raises for one version.

    ``.analyst`` and ``.officer`` name them; ``.any`` is for checks that do not
    care which of the two is used.
    """

    def __init__(self, tasks) -> None:
        self.all = list(tasks)
        self.analyst = next(t for t in tasks if t.required_role is Role.ANALYST)
        self.officer = next(t for t in tasks if t.required_role is Role.COMPLIANCE_OFFICER)


def approve_fully(db_session: Session, scenario, pair, label: str):
    """Collect both required roles, which passes G1 and commits the baseline."""
    ApprovalService(db_session, scenario["reviewer"]).decide(
        project_id=scenario["project_id"],
        task_id=pair.analyst.id,
        decision=ApprovalDecisionType.APPROVE,
        role_exercised=Role.ANALYST,
    )
    return ApprovalService(db_session, scenario["officer"]).decide(
        project_id=scenario["project_id"],
        task_id=pair.officer.id,
        decision=ApprovalDecisionType.APPROVE,
        role_exercised=Role.COMPLIANCE_OFFICER,
        baseline_label=label,
    )


# ---------------------------------------------------------------------------
# Approval: role, actor kind, self-approval, reuse
# ---------------------------------------------------------------------------


def test_wrong_role_cannot_approve(db_session: Session, scenario) -> None:
    """A security reviewer has no standing at G1, and the policy says so."""
    _, _, pair = submitted_task(db_session, scenario)
    with pytest.raises(AuthorizationError, match="may not decide"):
        ApprovalService(db_session, scenario["security"]).decide(
            project_id=scenario["project_id"],
            task_id=pair.analyst.id,
            decision=ApprovalDecisionType.APPROVE,
            role_exercised=Role.SECURITY_REVIEWER,
        )


def test_actor_cannot_claim_a_role_they_do_not_hold(db_session: Session, scenario) -> None:
    """Naming a permitted role is not the same as holding it."""
    _, _, pair = submitted_task(db_session, scenario)
    with pytest.raises(AuthorizationError, match="does not hold"):
        ApprovalService(db_session, scenario["security"]).decide(
            project_id=scenario["project_id"],
            task_id=pair.officer.id,
            decision=ApprovalDecisionType.APPROVE,
            role_exercised=Role.COMPLIANCE_OFFICER,
        )


def test_non_human_actor_cannot_approve(db_session: Session, scenario) -> None:
    """The governance rule that survives everything: no agent decides a gate."""
    _, _, pair = submitted_task(db_session, scenario)
    agent = Actor(
        actor_id=ActorId(new_uuid()),
        kind=ActorKind.AGENT_ROLE,
        roles_by_project={scenario["project_id"]: frozenset({Role.COMPLIANCE_OFFICER})},
    )
    with pytest.raises(AuthorizationError, match="never decide an approval gate"):
        ApprovalService(db_session, agent).decide(
            project_id=scenario["project_id"],
            task_id=pair.officer.id,
            decision=ApprovalDecisionType.APPROVE,
            role_exercised=Role.COMPLIANCE_OFFICER,
        )


def test_a_policy_denial_stops_a_human_approval(
    db_session: Session, scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: the approval service must enforce ``policy.can``, not just call it.

    The attempt is otherwise valid - a human analyst who did not author the
    version, deciding the Analyst task as an Analyst - and the real policy allows
    it. If ``policy.can`` denies it, nothing may be decided or written.
    """
    project_id = scenario["project_id"]
    reviewer = scenario["reviewer"]
    _, version, pair = submitted_task(db_session, scenario)
    expected = ResourceRef(
        resource_type=ResourceType.APPROVAL_TASK,
        project_id=project_id,
        resource_id=str(pair.analyst.id),
        gate=Gate.G1_REQUIREMENT_BASELINE,
        role_exercised=Role.ANALYST,
    )
    assert can(reviewer, Action.APPROVAL_DECIDE, expected).allowed, "precondition"

    real_can = policy_module.can
    consulted: list[ResourceRef] = []

    def denying_can(actor: Actor, action: Action, resource: ResourceRef) -> Decision:
        if action is Action.APPROVAL_DECIDE:
            consulted.append(resource)
            return Decision(False, "denied by the policy under test")
        return real_can(actor, action, resource)

    monkeypatch.setattr(policy_module, "can", denying_can)
    with pytest.raises(AuthorizationError, match="denied by the policy under test"):
        ApprovalService(db_session, reviewer).decide(
            project_id=project_id,
            task_id=pair.analyst.id,
            decision=ApprovalDecisionType.APPROVE,
            role_exercised=Role.ANALYST,
        )
    monkeypatch.undo()

    assert consulted == [expected], "the policy must be asked, with the gate and role"
    service = ApprovalService(db_session, reviewer)
    assert service.decisions_for(project_id, pair.analyst.id) == []
    task = service.get_task(project_id, pair.analyst.id)
    assert task is not None
    assert task.status is ApprovalTaskStatus.OPEN
    db_session.refresh(version)
    assert version.state is RequirementState.PENDING_APPROVAL


def test_a_member_of_another_project_cannot_approve(db_session: Session, scenario) -> None:
    """The right role in the wrong project has no standing on this gate."""
    _, _, pair = submitted_task(db_session, scenario)
    elsewhere = make_project(db_session, "Payments")
    outsider = make_member(db_session, elsewhere, Role.COMPLIANCE_OFFICER, "x@example.test")

    with pytest.raises(ProjectIsolationError):
        ApprovalService(db_session, outsider).decide(
            project_id=scenario["project_id"],
            task_id=pair.officer.id,
            decision=ApprovalDecisionType.APPROVE,
            role_exercised=Role.COMPLIANCE_OFFICER,
        )
    # Addressed through their own project, the task does not exist there.
    with pytest.raises(ReqPilotError, match="not found"):
        ApprovalService(db_session, outsider).decide(
            project_id=ProjectId(elsewhere.id),
            task_id=pair.officer.id,
            decision=ApprovalDecisionType.APPROVE,
            role_exercised=Role.COMPLIANCE_OFFICER,
        )
    assert pair.officer.status is ApprovalTaskStatus.OPEN


def test_author_cannot_approve_their_own_version(db_session: Session, scenario) -> None:
    """Segregation of duties, enforced rather than requested."""
    _, _, pair = submitted_task(db_session, scenario)
    with pytest.raises(SelfApprovalError, match="may not approve"):
        ApprovalService(db_session, scenario["author"]).decide(
            project_id=scenario["project_id"],
            task_id=pair.analyst.id,
            decision=ApprovalDecisionType.APPROVE,
            role_exercised=Role.ANALYST,
        )


def test_a_decided_task_cannot_be_decided_again(db_session: Session, scenario) -> None:
    _, _, pair = submitted_task(db_session, scenario)
    approve_fully(db_session, scenario, pair, "b1")
    with pytest.raises(ApprovalError, match="already"):
        ApprovalService(db_session, scenario["reviewer"]).decide(
            project_id=scenario["project_id"],
            task_id=pair.analyst.id,
            decision=ApprovalDecisionType.APPROVE,
            role_exercised=Role.ANALYST,
        )


def test_the_same_role_cannot_approve_twice(db_session: Session, scenario) -> None:
    """Co-approval must not be satisfiable by one role approving repeatedly."""
    _, _, pair = submitted_task(db_session, scenario)
    ApprovalService(db_session, scenario["reviewer"]).decide(
        project_id=scenario["project_id"],
        task_id=pair.analyst.id,
        decision=ApprovalDecisionType.APPROVE,
        role_exercised=Role.ANALYST,
    )
    other_analyst = make_member(db_session, scenario["project"], Role.ANALYST, "a2@example.test")
    with pytest.raises(ApprovalError, match="already"):
        ApprovalService(db_session, other_analyst).decide(
            project_id=scenario["project_id"],
            task_id=pair.analyst.id,
            decision=ApprovalDecisionType.APPROVE,
            role_exercised=Role.ANALYST,
        )


def test_an_analyst_cannot_sign_the_compliance_task(db_session: Session, scenario) -> None:
    """Each task names one role; signing the wrong one would collapse co-approval."""
    _, _, pair = submitted_task(db_session, scenario)
    dual = make_member(db_session, scenario["project"], Role.ANALYST, "dual@example.test")
    with pytest.raises(ApprovalError, match="must be decided as"):
        ApprovalService(db_session, dual).decide(
            project_id=scenario["project_id"],
            task_id=pair.officer.id,
            decision=ApprovalDecisionType.APPROVE,
            role_exercised=Role.ANALYST,
        )


def test_analyst_approval_alone_cannot_baseline(db_session: Session, scenario) -> None:
    """Acceptance criterion: an Analyst signature on its own does nothing final."""
    project_id = scenario["project_id"]
    _, version, pair = submitted_task(db_session, scenario)

    outcome = ApprovalService(db_session, scenario["reviewer"]).decide(
        project_id=project_id,
        task_id=pair.analyst.id,
        decision=ApprovalDecisionType.APPROVE,
        role_exercised=Role.ANALYST,
        baseline_label="premature",
    )
    assert outcome.group_complete is False
    assert outcome.baseline_id is None
    db_session.refresh(version)
    assert version.state is RequirementState.PENDING_APPROVAL
    assert BaselineService(db_session, scenario["officer"]).list_for_project(project_id) == []


def test_compliance_approval_alone_cannot_baseline(db_session: Session, scenario) -> None:
    """The mirror case: a Compliance Officer signature alone is equally insufficient."""
    project_id = scenario["project_id"]
    _, version, pair = submitted_task(db_session, scenario)

    outcome = ApprovalService(db_session, scenario["officer"]).decide(
        project_id=project_id,
        task_id=pair.officer.id,
        decision=ApprovalDecisionType.APPROVE,
        role_exercised=Role.COMPLIANCE_OFFICER,
        baseline_label="premature",
    )
    assert outcome.group_complete is False
    assert outcome.baseline_id is None
    db_session.refresh(version)
    assert version.state is RequirementState.PENDING_APPROVAL
    assert BaselineService(db_session, scenario["officer"]).list_for_project(project_id) == []


def test_rejecting_one_task_cancels_its_sibling(db_session: Session, scenario) -> None:
    """A rejected version must not remain approvable through the other role."""
    project_id = scenario["project_id"]
    _, _version, pair = submitted_task(db_session, scenario)

    ApprovalService(db_session, scenario["officer"]).decide(
        project_id=project_id,
        task_id=pair.officer.id,
        decision=ApprovalDecisionType.REJECT,
        role_exercised=Role.COMPLIANCE_OFFICER,
        justification="not acceptable",
    )
    db_session.refresh(pair.analyst)
    assert pair.analyst.status is ApprovalTaskStatus.CANCELLED

    with pytest.raises(ApprovalError, match="cancelled"):
        ApprovalService(db_session, scenario["reviewer"]).decide(
            project_id=project_id,
            task_id=pair.analyst.id,
            decision=ApprovalDecisionType.APPROVE,
            role_exercised=Role.ANALYST,
        )


def test_rejection_requires_a_justification(db_session: Session, scenario) -> None:
    _, _, pair = submitted_task(db_session, scenario)
    with pytest.raises(ApprovalError, match="justification"):
        ApprovalService(db_session, scenario["officer"]).decide(
            project_id=scenario["project_id"],
            task_id=pair.officer.id,
            decision=ApprovalDecisionType.REJECT,
            role_exercised=Role.COMPLIANCE_OFFICER,
        )


def test_rejection_sends_the_version_to_rejected(db_session: Session, scenario) -> None:
    _, version, pair = submitted_task(db_session, scenario)
    ApprovalService(db_session, scenario["officer"]).decide(
        project_id=scenario["project_id"],
        task_id=pair.officer.id,
        decision=ApprovalDecisionType.REJECT,
        role_exercised=Role.COMPLIANCE_OFFICER,
        justification="the document list is not specific enough",
    )
    db_session.refresh(version)
    assert version.state is RequirementState.REJECTED
    db_session.refresh(pair.officer)
    assert pair.officer.status is ApprovalTaskStatus.REJECTED


# ---------------------------------------------------------------------------
# Exact-version binding (the mandatory case)
# ---------------------------------------------------------------------------


def test_an_approval_cannot_be_reused_against_a_later_version(
    db_session: Session, scenario
) -> None:
    """The exact-version invariant, end to end.

    V1 is submitted and a task is raised against it. A successor V2 is then
    created. The V1 task must not be usable to approve anything about V2, and a
    fresh task is required.
    """
    project_id = scenario["project_id"]
    requirement, v1, pair = submitted_task(db_session, scenario)

    for task in pair.all:
        assert task.subject_id == v1.id
        assert task.subject_version_hash == v1.content_hash

    # The governed subject changes underneath the open task.
    authoring = RequirementService(db_session, scenario["author"])
    v2 = authoring.create_version(
        project_id=project_id,
        requirement_id=requirement.id,
        content=content("The system shall X, with a named document list."),
        change_reason="reviewer asked for specifics",
    )
    assert v2.content_hash != v1.content_hash

    # The old tasks still point at V1, and V1 itself is unchanged, so they
    # remain valid *for V1* - their scope stayed exact.
    for task in pair.all:
        db_session.refresh(task)
        assert task.subject_id == v1.id
        assert task.subject_version_hash == v1.content_hash

    # There is no task covering V2, so V2 cannot be approved through the old one.
    tasks_for_v2 = [
        t
        for t in ApprovalService(db_session, scenario["officer"]).list_tasks(project_id)
        if t.subject_id == v2.id and t.gate is Gate.G1_REQUIREMENT_BASELINE
    ]
    assert tasks_for_v2 == [], "no G1 task may cover a version nobody submitted"


def test_a_tampered_binding_is_refused(db_session: Session, scenario) -> None:
    """If a task's recorded hash no longer matches, its decision is refused."""
    _, _version, pair = submitted_task(db_session, scenario)

    # Simulate the subject having changed after the task was raised.
    pair.officer.subject_version_hash = "0" * 64
    db_session.flush()

    with pytest.raises(StaleApprovalError, match="no longer covers"):
        ApprovalService(db_session, scenario["officer"]).decide(
            project_id=scenario["project_id"],
            task_id=pair.officer.id,
            decision=ApprovalDecisionType.APPROVE,
            role_exercised=Role.COMPLIANCE_OFFICER,
        )


def test_a_stale_binding_on_one_task_blocks_the_whole_gate(db_session: Session, scenario) -> None:
    """Co-approval is only as strong as its weakest task.

    If the Compliance Officer's task is stale, an Analyst approval on the other
    task must not be able to carry the gate on its own.
    """
    project_id = scenario["project_id"]
    _, version, pair = submitted_task(db_session, scenario)
    pair.officer.subject_version_hash = "0" * 64
    db_session.flush()

    outcome = ApprovalService(db_session, scenario["reviewer"]).decide(
        project_id=project_id,
        task_id=pair.analyst.id,
        decision=ApprovalDecisionType.APPROVE,
        role_exercised=Role.ANALYST,
    )
    assert outcome.group_complete is False
    assert outcome.baseline_id is None
    db_session.refresh(version)
    assert version.state is RequirementState.PENDING_APPROVAL


# ---------------------------------------------------------------------------
# Baseline invariants (the mandatory cases)
# ---------------------------------------------------------------------------


def genuine_approval_decision(db_session: Session, scenario):
    """A real, valid APPROVE decision from an unrelated successful gate.

    Used to isolate the *version-state* invariant: with a perfectly good
    authorising decision in hand, the only thing that can refuse the baseline is
    the state of the versions being offered.
    """
    _, _version, pair = submitted_task(db_session, scenario, "The system shall be a decoy.")
    return approve_fully(db_session, scenario, pair, "decoy-baseline").decision


def test_a_pending_version_cannot_enter_a_baseline(db_session: Session, scenario) -> None:
    """A valid approval elsewhere does not launder a version that is not approved."""
    project_id = scenario["project_id"]
    decision = genuine_approval_decision(db_session, scenario)

    _, version, _pair = submitted_task(db_session, scenario, "The system shall stay pending.")
    db_session.refresh(version)
    assert version.state is RequirementState.PENDING_APPROVAL

    with pytest.raises(BaselineInvariantError, match="only APPROVED versions"):
        BaselineService(db_session, scenario["officer"]).commit(
            project_id=project_id,
            label="forced",
            version_ids=[version.id],
            approval_decision_id=decision.id,
        )


def test_a_baseline_without_an_authorising_decision_is_refused(
    db_session: Session, scenario
) -> None:
    """A baseline must name the decision behind it; an invented id will not do."""
    _, version, _pair = submitted_task(db_session, scenario)
    with pytest.raises(BaselineInvariantError, match="must name the approval decision"):
        BaselineService(db_session, scenario["officer"]).commit(
            project_id=scenario["project_id"],
            label="forced",
            version_ids=[version.id],
            approval_decision_id=new_uuid(),
        )


def test_a_rejected_version_cannot_enter_a_baseline(db_session: Session, scenario) -> None:
    project_id = scenario["project_id"]
    decision = genuine_approval_decision(db_session, scenario)

    _, version, pair = submitted_task(db_session, scenario, "The system shall be rejected.")
    ApprovalService(db_session, scenario["officer"]).decide(
        project_id=project_id,
        task_id=pair.officer.id,
        decision=ApprovalDecisionType.REJECT,
        role_exercised=Role.COMPLIANCE_OFFICER,
        justification="not acceptable",
    )
    db_session.refresh(version)
    assert version.state is RequirementState.REJECTED

    with pytest.raises(BaselineInvariantError, match="only APPROVED versions"):
        BaselineService(db_session, scenario["officer"]).commit(
            project_id=project_id,
            label="forced",
            version_ids=[version.id],
            approval_decision_id=decision.id,
        )


def test_a_baseline_cannot_be_authorised_by_a_rejection(db_session: Session, scenario) -> None:
    """Even a real decision row does not authorise a baseline unless it approved."""
    project_id = scenario["project_id"]
    _, _v, pair = submitted_task(db_session, scenario)
    outcome = ApprovalService(db_session, scenario["officer"]).decide(
        project_id=project_id,
        task_id=pair.officer.id,
        decision=ApprovalDecisionType.REJECT,
        role_exercised=Role.COMPLIANCE_OFFICER,
        justification="no",
    )
    _, other_version, other_pair = submitted_task(db_session, scenario, "The system shall Y.")
    approve_fully(db_session, scenario, other_pair, "ok-1")
    db_session.refresh(other_version)

    with pytest.raises(BaselineInvariantError, match="not an approval"):
        BaselineService(db_session, scenario["officer"]).commit(
            project_id=project_id,
            label="second",
            version_ids=[other_version.id],
            approval_decision_id=outcome.decision.id,
        )


def test_a_baseline_cannot_cross_project_boundaries(db_session: Session, scenario) -> None:
    """A version from another project is not an approvable member."""
    project_id = scenario["project_id"]
    _, _version, pair = submitted_task(db_session, scenario)
    outcome = approve_fully(db_session, scenario, pair, "home")

    other_project = make_project(db_session, "Payments")
    other_author = make_member(db_session, other_project, Role.ANALYST, "o@example.test")
    other_service = RequirementService(db_session, other_author)
    _, foreign_version = other_service.create_requirement(
        project_id=ProjectId(other_project.id),
        domain="PAY",
        content=content("The system shall step up authentication."),
    )

    with pytest.raises(BaselineInvariantError, match="not an approvable member"):
        BaselineService(db_session, scenario["officer"]).commit(
            project_id=project_id,
            label="mixed",
            version_ids=[foreign_version.id],
            approval_decision_id=outcome.decision.id,
        )


def test_an_empty_baseline_is_refused(db_session: Session, scenario) -> None:
    with pytest.raises(BaselineInvariantError, match="at least one version"):
        BaselineService(db_session, scenario["officer"]).commit(
            project_id=scenario["project_id"],
            label="empty",
            version_ids=[],
            approval_decision_id=new_uuid(),
        )


# ---------------------------------------------------------------------------
# Lifecycle bypass attempts
# ---------------------------------------------------------------------------


def test_a_version_cannot_jump_straight_to_approved(db_session: Session, scenario) -> None:
    """There is no shortcut from authoring to approval."""
    project_id = scenario["project_id"]
    service = RequirementService(db_session, scenario["author"])
    _, version = service.create_requirement(
        project_id=project_id, domain="LOAN", content=content("The system shall Z.")
    )
    with pytest.raises(StateTransitionError, match="not an allowed transition"):
        service.transition(
            project_id=project_id, version_id=version.id, target=RequirementState.APPROVED
        )


def test_pending_cannot_become_approved_without_a_decision(db_session: Session, scenario) -> None:
    """Even from the right state, approval needs a real decision."""
    project_id = scenario["project_id"]
    _, version, _pair = submitted_task(db_session, scenario)
    service = RequirementService(db_session, scenario["author"])
    with pytest.raises(StateTransitionError, match="approval decision"):
        service.transition(
            project_id=project_id, version_id=version.id, target=RequirementState.APPROVED
        )


def test_an_unvalidated_version_cannot_be_submitted(db_session: Session, scenario) -> None:
    project_id = scenario["project_id"]
    service = RequirementService(db_session, scenario["author"])
    _, version = service.create_requirement(
        project_id=project_id, domain="LOAN", content=content("The system shall W.")
    )
    with pytest.raises(StateTransitionError):
        ApprovalService(db_session, scenario["author"]).submit_versions_for_baseline(
            project_id=project_id, version_ids=[version.id]
        )


def test_a_version_without_a_source_reference_cannot_advance(db_session: Session, scenario) -> None:
    """``FR-EXT-007``: no requirement without provenance."""
    project_id = scenario["project_id"]
    service = RequirementService(db_session, scenario["author"])
    _, version = service.create_requirement(
        project_id=project_id,
        domain="LOAN",
        content=RequirementContent(statement="The system shall have no source.", source_refs=()),
    )
    with pytest.raises(StateTransitionError, match="source reference"):
        service.transition(
            project_id=project_id, version_id=version.id, target=RequirementState.EXTRACTED
        )


def test_a_baselined_version_cannot_be_withdrawn(db_session: Session, scenario) -> None:
    project_id = scenario["project_id"]
    _, version, pair = submitted_task(db_session, scenario)
    approve_fully(db_session, scenario, pair, "frozen")
    db_session.refresh(version)
    assert version.state is RequirementState.BASELINED

    with pytest.raises(StateTransitionError):
        RequirementService(db_session, scenario["author"]).withdraw(
            project_id=project_id, version_id=version.id, reason="changed my mind"
        )


# ---------------------------------------------------------------------------
# Project isolation
# ---------------------------------------------------------------------------


@pytest.fixture
def two_projects(db_session: Session):
    a = make_project(db_session, "Project A")
    b = make_project(db_session, "Project B")
    author_a = make_member(db_session, a, Role.ANALYST, "aa@example.test")
    member_b = make_member(db_session, b, Role.ANALYST, "bb@example.test")
    service = RequirementService(db_session, author_a)
    requirement, version = service.create_requirement(
        project_id=ProjectId(a.id), domain="LOAN", content=content("The system shall isolate.")
    )
    return {
        "a": ProjectId(a.id),
        "b": ProjectId(b.id),
        "author_a": author_a,
        "member_b": member_b,
        "requirement": requirement,
        "version": version,
    }


def test_project_a_member_can_see_their_own(db_session: Session, two_projects) -> None:
    found = RequirementService(db_session, two_projects["author_a"]).list_requirements(
        two_projects["a"]
    )
    assert len(found) == 1


def test_project_b_member_cannot_read(db_session: Session, two_projects) -> None:
    with pytest.raises(ProjectIsolationError):
        RequirementService(db_session, two_projects["member_b"]).get_requirement(
            two_projects["a"], two_projects["requirement"].id
        )


def test_project_b_member_cannot_edit(db_session: Session, two_projects) -> None:
    with pytest.raises(ProjectIsolationError):
        RequirementService(db_session, two_projects["member_b"]).create_version(
            project_id=two_projects["a"],
            requirement_id=two_projects["requirement"].id,
            content=content("hijacked"),
            change_reason="hijack",
        )


def test_project_b_member_cannot_submit(db_session: Session, two_projects) -> None:
    with pytest.raises(ProjectIsolationError):
        ApprovalService(db_session, two_projects["member_b"]).submit_versions_for_baseline(
            project_id=two_projects["a"], version_ids=[two_projects["version"].id]
        )


def test_project_b_member_cannot_baseline(db_session: Session, two_projects) -> None:
    with pytest.raises(ProjectIsolationError):
        BaselineService(db_session, two_projects["member_b"]).commit(
            project_id=two_projects["a"],
            label="stolen",
            version_ids=[two_projects["version"].id],
            approval_decision_id=new_uuid(),
        )


def test_scoped_lookup_of_a_foreign_id_returns_nothing(db_session: Session, two_projects) -> None:
    """Looking up project A's id *within project B* finds nothing.

    The caller turns this into the same answer it would give for an id that does
    not exist, so cross-project existence is not disclosed.
    """
    service = RequirementService(db_session, two_projects["member_b"])
    assert service.get_requirement(two_projects["b"], two_projects["requirement"].id) is None


# ---------------------------------------------------------------------------
# Policy-level guarantees the repository layer relies on
# ---------------------------------------------------------------------------


def test_requirement_actions_are_denied_outside_the_project() -> None:
    """The central policy refuses the new P1 actions across project boundaries."""
    home, away = ProjectId(new_uuid()), ProjectId(new_uuid())
    actor = Actor(
        actor_id=ActorId(new_uuid()),
        roles_by_project={home: frozenset({Role.ANALYST})},
    )
    for action in (
        Action.REQUIREMENT_CREATE,
        Action.REQUIREMENT_UPDATE,
        Action.REQUIREMENT_SUBMIT,
        Action.BASELINE_CREATE,
    ):
        decision = can(
            actor, action, ResourceRef(resource_type=ResourceType.REQUIREMENT, project_id=away)
        )
        assert not decision.allowed
        assert "project isolation" in decision.reason


def test_reviewers_cannot_author(db_session: Session, scenario) -> None:
    """A compliance officer approves; they do not write requirements."""
    with pytest.raises(AuthorizationError, match="may perform"):
        RequirementService(db_session, scenario["officer"]).create_requirement(
            project_id=scenario["project_id"],
            domain="LOAN",
            content=content("The officer shall write their own requirement."),
        )


def test_stakeholder_cannot_transition(db_session: Session, scenario) -> None:
    stakeholder = make_member(db_session, scenario["project"], Role.STAKEHOLDER, "st@example.test")
    _, version, _pair = submitted_task(db_session, scenario)
    with pytest.raises(AuthorizationError):
        RequirementService(db_session, stakeholder).transition(
            project_id=scenario["project_id"],
            version_id=version.id,
            target=RequirementState.WITHDRAWN,
        )


_ = uuid  # used by type annotations above
