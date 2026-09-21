"""Authorization policy tests (ADR-009).

The architecture asks for ``(role x action x resource)`` to be assertable as a
matrix. This file is that matrix.

Three properties carry the most weight and are tested first: deny-by-default,
project isolation before permission, and the rule that a non-human actor can
never decide an approval gate.
"""

from __future__ import annotations

import pytest
from tests.conftest import make_actor

from reqpilot.domain.enums import GATE_REQUIRED_ROLES, Action, ActorKind, Gate, ResourceType, Role
from reqpilot.domain.errors import AuthorizationError, ProjectIsolationError
from reqpilot.domain.ids import ActorId, ProjectId, new_uuid
from reqpilot.domain.policy import Actor, ResourceRef, can, require

pytestmark = pytest.mark.unit


def project_resource(project_id: ProjectId) -> ResourceRef:
    return ResourceRef(resource_type=ResourceType.PROJECT, project_id=project_id)


# --- allow / deny --------------------------------------------------------


def test_authorized_action_is_allowed(analyst: Actor, project_id: ProjectId) -> None:
    decision = can(analyst, Action.RUN_START, project_resource(project_id))
    assert decision.allowed
    assert bool(decision) is True


def test_unauthorized_action_is_denied(analyst: Actor, project_id: ProjectId) -> None:
    """An analyst may not delete a project - that is the PM's action."""
    decision = can(analyst, Action.PROJECT_DELETE, project_resource(project_id))
    assert not decision.allowed
    assert "may perform" in decision.reason


def test_actor_with_no_roles_is_denied(project_id: ProjectId) -> None:
    nobody = Actor(actor_id=ActorId(new_uuid()))
    assert not can(nobody, Action.PROJECT_READ, project_resource(project_id))


# --- project isolation ---------------------------------------------------


def test_correct_role_in_wrong_project_is_denied(
    analyst: Actor, other_project_id: ProjectId
) -> None:
    """The isolation case: right role, wrong project.

    Isolation is evaluated before permission, so this must fail on isolation
    rather than falling through to a role check that would have passed.
    """
    decision = can(analyst, Action.RUN_START, project_resource(other_project_id))
    assert not decision.allowed
    assert "project isolation" in decision.reason


def test_isolation_failure_raises_a_distinguishable_error(
    analyst: Actor, other_project_id: ProjectId
) -> None:
    with pytest.raises(ProjectIsolationError):
        require(analyst, Action.RUN_START, project_resource(other_project_id))


def test_ordinary_denial_raises_authorization_error(analyst: Actor, project_id: ProjectId) -> None:
    with pytest.raises(AuthorizationError) as excinfo:
        require(analyst, Action.PROJECT_DELETE, project_resource(project_id))
    assert not isinstance(excinfo.value, ProjectIsolationError)


def test_scoped_action_without_a_project_is_denied(analyst: Actor) -> None:
    unscoped = ResourceRef(resource_type=ResourceType.PROJECT, project_id=None)
    decision = can(analyst, Action.RUN_START, unscoped)
    assert not decision.allowed
    assert "requires a project-scoped resource" in decision.reason


# --- deny by default -----------------------------------------------------


def test_unknown_action_is_safely_denied(analyst: Actor, project_id: ProjectId) -> None:
    """A value that is not a known Action must be refused, not crash."""

    class NotAnAction:
        pass

    decision = can(analyst, NotAnAction(), project_resource(project_id))  # type: ignore[arg-type]
    assert not decision.allowed


def test_action_without_a_grant_is_denied_by_default(project_id: ProjectId) -> None:
    """APPROVAL_DECIDE has no blanket grant; it is authorised per-gate."""
    officer = make_actor(project_id=project_id, roles={Role.COMPLIANCE_OFFICER})
    decision = can(
        officer,
        Action.APPROVAL_DECIDE,
        ResourceRef(resource_type=ResourceType.APPROVAL_TASK, project_id=project_id),
    )
    assert not decision.allowed
    assert "per-gate" in decision.reason


# --- the governance rule -------------------------------------------------


def test_agent_actor_can_never_decide_a_gate(agent_actor: Actor, project_id: ProjectId) -> None:
    """An LLM-backed actor cannot approve, whatever roles it appears to hold.

    This is the code-level form of "an LLM cannot approve its own output"
    (architecture J.1, M.2). The fixture deliberately gives the agent the
    compliance-officer role to prove the check does not depend on roles.
    """
    decision = can(
        agent_actor,
        Action.APPROVAL_DECIDE,
        ResourceRef(resource_type=ResourceType.APPROVAL_TASK, project_id=project_id),
    )
    assert not decision.allowed
    assert "never decide an approval gate" in decision.reason


def test_agent_gate_denial_survives_superuser(project_id: ProjectId) -> None:
    """Even a superuser flag must not let a non-human decide a gate."""
    rogue = Actor(
        actor_id=ActorId(new_uuid()),
        kind=ActorKind.AGENT_ROLE,
        roles_by_project={project_id: frozenset({Role.COMPLIANCE_OFFICER})},
        is_superuser=True,
    )
    decision = can(
        rogue,
        Action.APPROVAL_DECIDE,
        ResourceRef(resource_type=ResourceType.APPROVAL_TASK, project_id=project_id),
    )
    assert not decision.allowed


# --- gate decisions ------------------------------------------------------


def gate_resource(project_id: ProjectId, gate: Gate, role: Role) -> ResourceRef:
    return ResourceRef(
        resource_type=ResourceType.APPROVAL_TASK,
        project_id=project_id,
        gate=gate,
        role_exercised=role,
    )


@pytest.mark.parametrize("gate", list(Gate))
def test_a_gate_is_decided_only_by_its_own_roles(gate: Gate, project_id: ProjectId) -> None:
    """APPROVAL_DECIDE is not a blanket grant: every role is tried at every gate.

    Each actor is human and holds the role it exercises in this project, so the
    only thing that can differ is whether GATE_REQUIRED_ROLES names that role.
    """
    for role in Role:
        actor = make_actor(project_id=project_id, roles={role})
        decision = can(actor, Action.APPROVAL_DECIDE, gate_resource(project_id, gate, role))
        assert decision.allowed is (role in GATE_REQUIRED_ROLES[gate]), (role, decision.reason)


def test_a_gate_role_must_be_held_in_this_project(
    project_id: ProjectId, other_project_id: ProjectId
) -> None:
    analyst = make_actor(project_id=project_id, roles={Role.ANALYST})
    gate = Gate.G1_REQUIREMENT_BASELINE

    claimed = can(
        analyst, Action.APPROVAL_DECIDE, gate_resource(project_id, gate, Role.COMPLIANCE_OFFICER)
    )
    assert not claimed.allowed
    assert "does not hold" in claimed.reason

    elsewhere = can(
        analyst, Action.APPROVAL_DECIDE, gate_resource(other_project_id, gate, Role.ANALYST)
    )
    assert not elsewhere.allowed
    assert "project isolation" in elsewhere.reason


def test_superuser_flag_does_not_grant_a_gate_decision(project_id: ProjectId) -> None:
    """Holding the gate's role is the only way in; the flag is not a substitute."""
    root = Actor(
        actor_id=ActorId(new_uuid()),
        roles_by_project={project_id: frozenset({Role.PROJECT_MANAGER})},
        is_superuser=True,
    )
    bare = ResourceRef(resource_type=ResourceType.APPROVAL_TASK, project_id=project_id)
    assert not can(root, Action.APPROVAL_DECIDE, bare)
    assert not can(
        root,
        Action.APPROVAL_DECIDE,
        gate_resource(project_id, Gate.G1_REQUIREMENT_BASELINE, Role.ANALYST),
    )


def test_agent_is_refused_even_with_a_complete_gate_context(
    agent_actor: Actor, project_id: ProjectId
) -> None:
    decision = can(
        agent_actor,
        Action.APPROVAL_DECIDE,
        gate_resource(project_id, Gate.G1_REQUIREMENT_BASELINE, Role.COMPLIANCE_OFFICER),
    )
    assert not decision.allowed
    assert "never decide an approval gate" in decision.reason


# --- role-specific behaviour ---------------------------------------------


def test_auditor_is_read_only(auditor: Actor, project_id: ProjectId) -> None:
    assert can(auditor, Action.AUDIT_READ, project_resource(project_id))
    assert can(auditor, Action.PROJECT_READ, project_resource(project_id))
    assert not can(auditor, Action.RUN_START, project_resource(project_id))
    assert not can(auditor, Action.PROJECT_DELETE, project_resource(project_id))


def test_only_auditor_may_verify_the_audit_chain(
    analyst: Actor, auditor: Actor, project_id: ProjectId
) -> None:
    resource = ResourceRef(resource_type=ResourceType.AUDIT_EVENT, project_id=project_id)
    assert can(auditor, Action.AUDIT_VERIFY, resource)
    assert not can(analyst, Action.AUDIT_VERIFY, resource)


def test_holding_several_roles_grants_the_union(project_id: ProjectId) -> None:
    """Realistic for a student team: one person, several roles, one project."""
    both = make_actor(project_id=project_id, roles={Role.ANALYST, Role.PROJECT_MANAGER})
    assert can(both, Action.RUN_START, project_resource(project_id))
    assert can(both, Action.PROJECT_DELETE, project_resource(project_id))


def test_auditor_plus_another_role_is_not_crippled(project_id: ProjectId) -> None:
    """The read-only rule applies to a pure Auditor, not to anyone holding it."""
    mixed = make_actor(project_id=project_id, roles={Role.AUDITOR, Role.ANALYST})
    assert can(mixed, Action.RUN_START, project_resource(project_id))


# --- the full matrix -----------------------------------------------------

_EXPECTED_MATRIX: dict[tuple[Role, Action], bool] = {
    (Role.ANALYST, Action.PROJECT_READ): True,
    (Role.ANALYST, Action.RUN_START): True,
    (Role.ANALYST, Action.AUDIT_READ): True,
    (Role.ANALYST, Action.PROJECT_DELETE): False,
    (Role.ANALYST, Action.MEMBER_ADD): False,
    (Role.ANALYST, Action.AUDIT_VERIFY): False,
    (Role.STAKEHOLDER, Action.PROJECT_READ): True,
    (Role.STAKEHOLDER, Action.RUN_START): False,
    (Role.STAKEHOLDER, Action.AUDIT_READ): False,
    (Role.COMPLIANCE_OFFICER, Action.PROJECT_READ): True,
    (Role.COMPLIANCE_OFFICER, Action.AUDIT_READ): True,
    (Role.COMPLIANCE_OFFICER, Action.RUN_START): False,
    (Role.SECURITY_REVIEWER, Action.AUDIT_READ): True,
    (Role.SECURITY_REVIEWER, Action.PROJECT_DELETE): False,
    (Role.PROJECT_MANAGER, Action.PROJECT_DELETE): True,
    (Role.PROJECT_MANAGER, Action.MEMBER_ADD): True,
    (Role.PROJECT_MANAGER, Action.RUN_START): False,
    (Role.AUDITOR, Action.AUDIT_VERIFY): True,
    (Role.AUDITOR, Action.RUN_START): False,
    (Role.KB_ADMIN, Action.PROJECT_READ): True,
    (Role.KB_ADMIN, Action.RUN_START): False,
    (Role.KB_ADMIN, Action.AUDIT_READ): False,
}


@pytest.mark.parametrize(
    ("role", "action", "expected"),
    [(role, action, expected) for (role, action), expected in _EXPECTED_MATRIX.items()],
)
def test_policy_matrix(role: Role, action: Action, expected: bool, project_id: ProjectId) -> None:
    actor = make_actor(project_id=project_id, roles={role})
    assert bool(can(actor, action, project_resource(project_id))) is expected


def test_gate_enum_has_exactly_eight_members() -> None:
    """G1-G8, and production-readiness is deliberately not among them.

    Approved baseline: eight ReqPilot platform gates. Production-readiness is a
    gate inside the generated project's workflow, not a ninth platform gate.
    """
    assert len(list(Gate)) == 8
    assert {g.value for g in Gate} == {"G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8"}
    assert not any("production" in g.name.lower() for g in Gate)
