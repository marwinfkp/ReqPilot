"""The P1 exit test: create → version → validate → G1 → approve → baseline.

This is the scenario P1 exists to prove. It is written first and kept first,
because if it stops passing the phase has stopped meeting its purpose.

Everything here is deterministic: no model, no network, no orchestration.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    ActorKind,
    ApprovalDecisionType,
    ApprovalTaskStatus,
    AuditEventType,
    Gate,
    RequirementCategory,
    RequirementPriority,
    Role,
)
from reqpilot.domain.ids import ActorId, ProjectId, new_uuid
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.identity import Project, ProjectMember, User
from reqpilot.domain.policy import Actor
from reqpilot.domain.requirement_ids import RequirementKind
from reqpilot.services.approval import ApprovalService
from reqpilot.services.audit import AuditService
from reqpilot.services.baseline import BaselineService
from reqpilot.services.requirements import RequirementContent, RequirementService

pytestmark = pytest.mark.workflow


# ---------------------------------------------------------------------------
# Scenario fixtures: three people, one project, three distinct roles.
# ---------------------------------------------------------------------------


def make_project(session: Session, name: str = "Loan Origination") -> Project:
    project = Project(name=name, domain="loan_origination")
    session.add(project)
    session.flush()
    return project


def make_member(session: Session, project: Project, role: Role, email: str) -> Actor:
    """Create a real user + membership row, and the matching policy actor."""
    user = User(email=email, display_name=email.split("@")[0])
    session.add(user)
    session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
    session.flush()
    return Actor(
        actor_id=ActorId(user.id),
        kind=ActorKind.HUMAN,
        roles_by_project={ProjectId(project.id): frozenset({role})},
    )


@pytest.fixture
def scenario(db_session: Session):
    """One project, an authoring analyst, a reviewing analyst, a compliance officer.

    Two analysts because approval is subject to segregation of duties: the
    person who wrote a version may not be the person who approves it.
    """
    project = make_project(db_session)
    author = make_member(db_session, project, Role.ANALYST, "author@example.test")
    reviewer = make_member(db_session, project, Role.ANALYST, "reviewer@example.test")
    officer = make_member(db_session, project, Role.COMPLIANCE_OFFICER, "compliance@example.test")
    return {
        "project_id": ProjectId(project.id),
        "author": author,
        "reviewer": reviewer,
        "officer": officer,
    }


def content(statement: str, **overrides) -> RequirementContent:
    """A complete, manually supplied requirement. Nothing is generated."""
    base = {
        "statement": statement,
        "original_text": "we need the applicant's identity checked before disbursal",
        "category": RequirementCategory.FUNCTIONAL,
        "priority": RequirementPriority.MUST,
        "justification": "regulatory onboarding obligation",
        "source_refs": ({"kind": "utterance", "ref": "interview-1", "span": [0, 42]},),
    }
    base.update(overrides)
    return RequirementContent(**base)  # type: ignore[arg-type]


def task_for(tasks, role: Role):
    """The task in a G1 group that the given role must sign.

    G1 is a co-approval gate: each required role gets its own task, so a
    submission of one version produces two tasks.
    """
    return next(t for t in tasks if t.required_role is role)


def drive_to_validated(
    service: RequirementService, project_id: ProjectId, version_id: uuid.UUID
) -> None:
    """Walk a version along the approved transition path, one guarded step at a time.

    Each call is validated against the transition table; none of them assigns a
    state directly, because no such operation exists.
    """
    for target in (
        RequirementState.EXTRACTED,
        RequirementState.CLASSIFIED,
        RequirementState.ANALYZED,
        RequirementState.VALIDATED,
    ):
        service.transition(project_id=project_id, version_id=version_id, target=target)


# ---------------------------------------------------------------------------
# The exit test
# ---------------------------------------------------------------------------


def test_p1_exit_scenario(db_session: Session, scenario) -> None:
    """create → edit/version → validate → submit → G1 → approve → baseline."""
    project_id = scenario["project_id"]
    author, reviewer, officer = scenario["author"], scenario["reviewer"], scenario["officer"]

    authoring = RequirementService(db_session, author)

    # 1. Create -----------------------------------------------------------
    requirement, v1 = authoring.create_requirement(
        project_id=project_id,
        domain="LOAN",
        kind=RequirementKind.FUNCTIONAL,
        content=content("The system shall verify applicant identity before disbursal."),
    )
    assert requirement.human_id == "FR-LOAN-001"
    assert v1.version_no == 1
    assert v1.state is RequirementState.CANDIDATE
    assert requirement.current_version_id == v1.id

    # 2. Edit, producing an immutable successor ---------------------------
    v2 = authoring.create_version(
        project_id=project_id,
        requirement_id=requirement.id,
        content=content(
            "The system shall verify applicant identity against an approved "
            "document before disbursal."
        ),
        change_reason="clarified which documents count",
    )
    assert v2.version_no == 2
    assert v2.content_hash != v1.content_hash, "an edit must change the exact-version binding"

    db_session.refresh(v1)
    assert v1.state is RequirementState.CANDIDATE, "the predecessor must not be mutated"
    assert requirement.current_version_id == v2.id

    # 3. Validate ---------------------------------------------------------
    drive_to_validated(authoring, project_id, v2.id)
    db_session.refresh(v2)
    assert v2.state is RequirementState.VALIDATED

    # 4. Submit for G1 ----------------------------------------------------
    tasks = ApprovalService(db_session, author).submit_versions_for_baseline(
        project_id=project_id, version_ids=[v2.id]
    )
    # G1 is a co-approval gate: one task per required role, sharing a group.
    assert len(tasks) == 2
    assert {t.required_role for t in tasks} == {Role.ANALYST, Role.COMPLIANCE_OFFICER}
    assert len({t.task_group_id for t in tasks}) == 1, "both tasks share one group"
    for task in tasks:
        assert task.gate is Gate.G1_REQUIREMENT_BASELINE
        assert task.status is ApprovalTaskStatus.OPEN
        assert task.subject_version_hash == v2.content_hash, "each task binds the exact version"

    db_session.refresh(v2)
    assert v2.state is RequirementState.PENDING_APPROVAL

    # 5. The reviewing analyst signs the Analyst task. Not enough on its own.
    outcome = ApprovalService(db_session, reviewer).decide(
        project_id=project_id,
        task_id=task_for(tasks, Role.ANALYST).id,
        decision=ApprovalDecisionType.APPROVE,
        role_exercised=Role.ANALYST,
    )
    assert outcome.task_closed is True, "the Analyst task itself is closed"
    assert outcome.group_complete is False, "co-approval: the gate has not passed"
    assert outcome.baseline_id is None, "one role alone must not baseline anything"
    db_session.refresh(v2)
    assert v2.state is RequirementState.PENDING_APPROVAL

    # 6. The compliance officer signs the Compliance Officer task. Now the
    #    gate passes and the baseline is committed (architecture M.3).
    outcome = ApprovalService(db_session, officer).decide(
        project_id=project_id,
        task_id=task_for(tasks, Role.COMPLIANCE_OFFICER).id,
        decision=ApprovalDecisionType.APPROVE,
        role_exercised=Role.COMPLIANCE_OFFICER,
        baseline_label="release-1",
    )
    assert outcome.task_closed is True
    assert outcome.group_complete is True
    assert outcome.baseline_id is not None

    # 7. The exact approved version is APPROVED, then BASELINED -----------
    db_session.refresh(v2)
    assert v2.state is RequirementState.BASELINED
    db_session.refresh(v1)
    assert v1.state is RequirementState.CANDIDATE, "an untouched version stays untouched"

    # 8. The baseline contains only approved versions ---------------------
    baselines = BaselineService(db_session, officer)
    members = baselines.member_versions(project_id, outcome.baseline_id)
    assert [m.id for m in members] == [v2.id]
    assert all(m.state is RequirementState.BASELINED for m in members)

    baseline = baselines.get(project_id, outcome.baseline_id)
    assert baseline is not None
    assert baseline.label == "release-1"
    assert baseline.approval_decision_id == outcome.decision.id

    db_session.refresh(requirement)
    assert requirement.baselined_version_id == v2.id

    # 9. Version history is correct ---------------------------------------
    history = authoring.version_history(project_id, requirement.id)
    assert [v.version_no for v in history] == [1, 2]

    # 10. Every meaningful action is audited ------------------------------
    events = AuditService(db_session).list_for_project(project_id)
    types = [e.event_type for e in events]
    for expected in (
        AuditEventType.REQUIREMENT_CREATED,
        AuditEventType.REQUIREMENT_VERSION_CREATED,
        AuditEventType.STATE_TRANSITION,
        AuditEventType.APPROVAL_TASK_CREATED,
        AuditEventType.APPROVAL_GRANTED,
        AuditEventType.GATE_PASSED,
        AuditEventType.BASELINE_MEMBER_ADDED,
        AuditEventType.BASELINE_COMMITTED,
    ):
        assert expected in types, f"missing audit event: {expected}"

    ok, index = AuditService(db_session).verify_project_chain(project_id)
    assert ok is True and index is None, "the audit chain must verify after the full flow"


def test_an_auditor_can_reconstruct_the_whole_story(db_session: Session, scenario) -> None:
    """The audit trail must answer who did what, to which version, and when."""
    project_id = scenario["project_id"]
    author, reviewer, officer = scenario["author"], scenario["reviewer"], scenario["officer"]

    service = RequirementService(db_session, author)
    _requirement, v1 = service.create_requirement(
        project_id=project_id, domain="LOAN", content=content("The system shall log disbursals.")
    )
    drive_to_validated(service, project_id, v1.id)
    tasks = ApprovalService(db_session, author).submit_versions_for_baseline(
        project_id=project_id, version_ids=[v1.id]
    )
    ApprovalService(db_session, reviewer).decide(
        project_id=project_id,
        task_id=task_for(tasks, Role.ANALYST).id,
        decision=ApprovalDecisionType.APPROVE,
        role_exercised=Role.ANALYST,
    )
    ApprovalService(db_session, officer).decide(
        project_id=project_id,
        task_id=task_for(tasks, Role.COMPLIANCE_OFFICER).id,
        decision=ApprovalDecisionType.APPROVE,
        role_exercised=Role.COMPLIANCE_OFFICER,
        baseline_label="audit-demo",
    )

    events = AuditService(db_session).list_for_project(project_id)

    created = next(e for e in events if e.event_type is AuditEventType.REQUIREMENT_CREATED)
    assert created.actor_ref == str(author.actor_id), "who created the requirement"

    granted = [e for e in events if e.event_type is AuditEventType.APPROVAL_GRANTED]
    deciders = {e.actor_ref for e in granted}
    assert deciders == {str(reviewer.actor_id), str(officer.actor_id)}

    roles = {e.payload["role_exercised"] for e in granted}
    assert roles == {str(Role.ANALYST), str(Role.COMPLIANCE_OFFICER)}, "which role was exercised"

    # Which exact version was approved.
    assert all(e.payload["subject_version_hash"] == v1.content_hash for e in granted)

    committed = next(e for e in events if e.event_type is AuditEventType.BASELINE_COMMITTED)
    assert committed.payload["member_count"] == 1

    # References only: no requirement text anywhere in any payload.
    for event in events:
        blob = str(event.payload).lower()
        assert "the system shall" not in blob


def test_audit_payloads_never_carry_requirement_text(db_session: Session, scenario) -> None:
    """The foundation's references-only rule survives P1 (architecture O.1)."""
    project_id = scenario["project_id"]
    service = RequirementService(db_session, scenario["author"])
    service.create_requirement(
        project_id=project_id,
        domain="LOAN",
        content=content("The system shall never leak this sentence into an audit payload."),
    )
    for event in AuditService(db_session).list_for_project(project_id):
        assert "statement" not in {k.lower() for k in event.payload}
        assert "leak this sentence" not in str(event.payload)


def test_unrelated_project_cannot_see_the_requirement(db_session: Session, scenario) -> None:
    """Project isolation holds at the service layer, not only in the UI."""
    project_a = scenario["project_id"]
    service = RequirementService(db_session, scenario["author"])
    service.create_requirement(
        project_id=project_a, domain="LOAN", content=content("The system shall do a thing.")
    )

    project_b = make_project(db_session, "Payments")
    outsider = make_member(db_session, project_b, Role.ANALYST, "outsider@example.test")

    from reqpilot.domain.errors import ProjectIsolationError

    with pytest.raises(ProjectIsolationError):
        RequirementService(db_session, outsider).list_requirements(project_a)

    # And the outsider genuinely can see their own, so the refusal is about
    # isolation rather than a broken fixture.
    assert RequirementService(db_session, outsider).list_requirements(ProjectId(project_b.id)) == []


_ = new_uuid  # imported for use by tests that need a non-existent id
