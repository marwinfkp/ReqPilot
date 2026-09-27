"""P10 security: authorisation, isolation, G6 bypass, forged provenance, immutability.

Each property is asserted where it is enforced - the policy, the repository, the
service, the ORM guard - not through a page that could simply hide a button.
"""

from __future__ import annotations

import inspect
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from tests.p3_helpers import member
from tests.p10_helpers import P10World, make_p10_world

from reqpilot.domain.enums import Action, ActorKind, Gate, ResourceType, Role, SdlcRunStatus
from reqpilot.domain.errors import (
    AuthorizationError,
    ImmutableRecordError,
    ProjectIsolationError,
)
from reqpilot.domain.ids import ActorId, ProjectId
from reqpilot.domain.models.approval import ApprovalTask
from reqpilot.domain.models.workflow import (
    Workflow,
    WorkflowActivity,
    WorkflowChange,
    WorkflowGate,
    WorkflowSource,
)
from reqpilot.domain.policy import Actor, ResourceRef, can
from reqpilot.domain.workflow.edits import UpdatePhase
from reqpilot.graph.sdlc_runner import SdlcRunner

pytestmark = pytest.mark.security


@pytest.fixture
def world(db_session: Session) -> P10World:
    return make_p10_world(db_session)


def actor(project: uuid.UUID, *roles: Role, kind: ActorKind = ActorKind.HUMAN) -> Actor:
    return Actor(
        actor_id=ActorId(uuid.uuid4()),
        kind=kind,
        roles_by_project={ProjectId(project): frozenset(roles)},
    )


def allowed(who: Actor, action: Action, project: uuid.UUID) -> bool:
    ref = ResourceRef(resource_type=ResourceType.WORKFLOW, project_id=ProjectId(project))
    return can(who, action, ref).allowed


# --- the policy --------------------------------------------------------------------------


def test_only_the_project_manager_edits_and_generation_is_the_analyst_or_the_pm() -> None:
    project = uuid.uuid4()
    for role in Role:
        who = actor(project, role)
        assert allowed(who, Action.WORKFLOW_EDIT, project) == (role is Role.PROJECT_MANAGER), role
        assert allowed(who, Action.WORKFLOW_GENERATE, project) == (
            role in (Role.ANALYST, Role.PROJECT_MANAGER)
        ), role
    for role in (Role.AUDITOR, Role.ARCHITECT, Role.COMPLIANCE_OFFICER, Role.SECURITY_REVIEWER):
        assert allowed(actor(project, role), Action.WORKFLOW_READ, project), role
        assert allowed(actor(project, role), Action.WORKFLOW_EXPORT, project), role
    assert not allowed(actor(project, Role.STAKEHOLDER), Action.WORKFLOW_READ, project)
    assert not allowed(actor(project, Role.KB_ADMIN), Action.WORKFLOW_READ, project)


@pytest.mark.parametrize("kind", [ActorKind.AGENT_ROLE, ActorKind.SYSTEM])
def test_no_agent_or_pipeline_actor_generates_or_edits_a_workflow(kind: ActorKind) -> None:
    project = uuid.uuid4()
    machine = actor(project, Role.ANALYST, Role.PROJECT_MANAGER, kind=kind)
    assert not allowed(machine, Action.WORKFLOW_GENERATE, project)
    assert not allowed(machine, Action.WORKFLOW_EDIT, project)


def test_membership_elsewhere_grants_nothing_here() -> None:
    here, there = uuid.uuid4(), uuid.uuid4()
    manager_there = actor(there, Role.PROJECT_MANAGER, Role.ANALYST)
    for action in (Action.WORKFLOW_READ, Action.WORKFLOW_EDIT, Action.WORKFLOW_GENERATE):
        assert not allowed(manager_there, action, here)


# --- isolation, G6 bypass and forged inputs --------------------------------------------------


def test_the_repository_refuses_another_projects_actor(world: P10World) -> None:
    run = world.select()
    summary = world.generate(run.id)
    from tests.workflow.test_p1_exit_test import make_project

    other_project = make_project(world.session, "Another lender (synthetic)")
    outsider = member(world.session, other_project, Role.PROJECT_MANAGER, "pm-x@example.test")
    service = world.workflows(outsider)
    with pytest.raises((ProjectIsolationError, AuthorizationError)):
        service.get(world.project_id, summary.workflow_id)  # type: ignore[arg-type]
    with pytest.raises((ProjectIsolationError, AuthorizationError)):
        service.edit(
            world.project_id,
            summary.workflow_id,  # type: ignore[arg-type]
            UpdatePhase("requirements", {"name": "x"}),
            reason="r",
        )
    with pytest.raises((ProjectIsolationError, AuthorizationError)):
        world.p9.runner().generate_workflow(
            actor=outsider, project_id=world.project_id, run_id=run.id
        )


def test_a_run_marked_selected_without_the_four_recorded_approvals_is_refused(
    world: P10World,
) -> None:
    run = world.rank()
    # Forge the lifecycle columns the ORM lets move - without a single G6 decision.
    run.status = SdlcRunStatus.SELECTED
    run.selected_candidate = run.top_candidate
    from reqpilot.domain.models.base import utc_now

    run.selected_at = utc_now()
    world.session.flush()
    summary = world.generate(run.id)
    assert summary.workflow_id is None
    codes = {f["code"] for f in summary.workflow_findings}
    assert codes & {"G6_NOT_PASSED", "G6_TASKS_INCOMPLETE", "G6_DECISION_MISSING"}, codes
    assert not world.session.scalar(select(func.count()).select_from(Workflow))


def test_nothing_a_caller_passes_can_stand_in_for_g6(world: P10World) -> None:
    """The runner takes a run id and nothing else - no resume payload, no flag."""
    parameters = set(inspect.signature(SdlcRunner.generate_workflow).parameters) - {"self"}
    assert parameters == {"actor", "project_id", "run_id"}


def test_generation_adds_no_reqpilot_gate_and_raises_no_approval_task(world: P10World) -> None:
    assert len(list(Gate)) == 8
    run = world.select()
    tasks = world.session.scalar(select(func.count()).select_from(ApprovalTask))
    world.generate(run.id)
    assert world.session.scalar(select(func.count()).select_from(ApprovalTask)) == tasks


# --- immutability of provenance and history ------------------------------------------------


def _generated(world: P10World) -> Workflow:
    run = world.select()
    summary = world.generate(run.id)
    workflow = world.session.get(Workflow, summary.workflow_id)
    assert workflow is not None
    return workflow


def _refused(session: Session, change) -> None:  # type: ignore[no-untyped-def]
    """Attempt ``change`` in a savepoint; the flush must refuse it."""
    with pytest.raises(ImmutableRecordError), session.begin_nested():
        change()
        session.flush()


def test_provenance_and_the_generated_workflow_cannot_be_rewritten(world: P10World) -> None:
    workflow = _generated(world)
    session = world.session
    source = session.scalars(
        select(WorkflowSource).where(WorkflowSource.workflow_id == workflow.id)
    ).first()
    assert source is not None
    _refused(session, lambda: setattr(source, "source_id", uuid.uuid4()))
    _refused(session, lambda: setattr(workflow, "generated_structure", {"forged": True}))
    _refused(session, lambda: setattr(workflow, "candidate_key", "agile"))
    activity = session.scalars(
        select(WorkflowActivity).where(
            WorkflowActivity.workflow_id == workflow.id, WorkflowActivity.mandatory.is_(True)
        )
    ).first()
    assert activity is not None
    _refused(session, lambda: setattr(activity, "mandatory", False))
    _refused(session, lambda: setattr(activity, "kind", "manual"))
    _refused(session, lambda: setattr(activity, "phase_id", uuid.uuid4()))


def test_mandatory_elements_gates_and_the_change_log_are_never_deleted(world: P10World) -> None:
    workflow = _generated(world)
    session = world.session
    mandatory = session.scalars(
        select(WorkflowActivity).where(
            WorkflowActivity.workflow_id == workflow.id, WorkflowActivity.mandatory.is_(True)
        )
    ).first()
    _refused(session, lambda: session.delete(mandatory))
    gate = session.scalars(
        select(WorkflowGate).where(WorkflowGate.workflow_id == workflow.id)
    ).first()
    _refused(session, lambda: session.delete(gate))
    _refused(session, lambda: session.delete(workflow))

    manager = world.workflows(world.manager)
    phase = manager.view(world.project_id, workflow).phases[0]
    manager.edit(
        world.project_id,
        workflow.id,
        UpdatePhase(phase.key, {"name": "Renamed (synthetic)"}),
        reason="Wording (synthetic).",
    )
    change = session.scalars(
        select(WorkflowChange).where(WorkflowChange.workflow_id == workflow.id)
    ).one()
    _refused(session, lambda: setattr(change, "reason", "rewritten"))
    _refused(session, lambda: session.delete(change))
