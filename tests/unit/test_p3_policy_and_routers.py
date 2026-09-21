"""P3 authorisation (policy rule 6) and the analysis-graph routers (architecture C.3)."""

from __future__ import annotations

import uuid

import pytest

from reqpilot.domain.enums import Action, ActorKind, Gate, ResourceType, Role
from reqpilot.domain.errors import AuthorizationError
from reqpilot.domain.ids import ActorId, ProjectId
from reqpilot.domain.policy import Actor, ResourceRef, can
from reqpilot.graph.routers import (
    assert_is_deterministic_router,
    route_after_scope,
    route_extraction,
    route_validation,
)
from reqpilot.graph.state import AnalysisState, assert_state_shape
from reqpilot.services.extraction import pipeline_actor

pytestmark = pytest.mark.unit

PROJECT = ProjectId(uuid.uuid4())
OTHER = ProjectId(uuid.uuid4())


def person(*roles: Role) -> Actor:
    return Actor(actor_id=ActorId(uuid.uuid4()), roles_by_project={PROJECT: frozenset(roles)})


def ref(project: ProjectId = PROJECT) -> ResourceRef:
    return ResourceRef(resource_type=ResourceType.PROJECT, project_id=project)


PIPELINE_ALLOWED = (
    Action.REQUIREMENT_CREATE,
    Action.REQUIREMENT_TRANSITION,
    Action.CLASSIFICATION_PROPOSE,
    Action.RUN_RECORD,
    Action.RUN_READ,
    Action.SOURCE_READ,
    Action.REVIEW_READ,
)
HUMAN_ONLY = (
    Action.REQUIREMENT_SUBMIT,
    Action.REQUIREMENT_WITHDRAW,
    Action.BASELINE_CREATE,
    Action.CLASSIFICATION_OVERRIDE,
    Action.REVIEW_RESOLVE,
    Action.REQUIREMENT_MERGE,
    Action.SOURCE_CREATE,
    Action.RUN_START,
)


def test_the_pipeline_actor_is_a_single_role_system_actor_in_one_project() -> None:
    analyst = person(Role.ANALYST)
    run_id = uuid.uuid4()
    pipeline = pipeline_actor(analyst, PROJECT, run_id)
    assert pipeline.kind is ActorKind.SYSTEM and pipeline.actor_id == run_id
    assert pipeline.roles_by_project == {PROJECT: frozenset({Role.ANALYST})}


@pytest.mark.parametrize(
    "role", [Role.COMPLIANCE_OFFICER, Role.AUDITOR, Role.STAKEHOLDER, Role.KB_ADMIN]
)
def test_only_an_analyst_can_start_a_run(role: Role) -> None:
    with pytest.raises(AuthorizationError):
        pipeline_actor(person(role), PROJECT, uuid.uuid4())


@pytest.mark.parametrize("action", PIPELINE_ALLOWED)
def test_the_pipeline_may_record_what_a_model_proposed(action: Action) -> None:
    pipeline = pipeline_actor(person(Role.ANALYST), PROJECT, uuid.uuid4())
    assert can(pipeline, action, ref()).allowed


@pytest.mark.parametrize("action", HUMAN_ONLY)
def test_the_pipeline_can_never_make_a_human_decision(action: Action) -> None:
    pipeline = pipeline_actor(person(Role.ANALYST), PROJECT, uuid.uuid4())
    decision = can(pipeline, action, ref())
    assert not decision.allowed and "human decision" in decision.reason


@pytest.mark.parametrize("action", HUMAN_ONLY)
def test_even_a_superuser_agent_cannot_make_a_human_decision(action: Action) -> None:
    agent = Actor(
        actor_id=ActorId(uuid.uuid4()),
        kind=ActorKind.AGENT_ROLE,
        roles_by_project={PROJECT: frozenset(Role)},
        is_superuser=True,
    )
    assert not can(agent, action, ref()).allowed


def test_the_pipeline_can_never_decide_a_gate() -> None:
    pipeline = pipeline_actor(person(Role.ANALYST), PROJECT, uuid.uuid4())
    for role in (Role.ANALYST, Role.COMPLIANCE_OFFICER):
        decision = can(
            pipeline,
            Action.APPROVAL_DECIDE,
            ResourceRef(
                resource_type=ResourceType.APPROVAL_TASK,
                project_id=PROJECT,
                gate=Gate.G1_REQUIREMENT_BASELINE,
                role_exercised=role,
            ),
        )
        assert not decision.allowed


def test_the_pipeline_cannot_reach_another_project() -> None:
    pipeline = pipeline_actor(person(Role.ANALYST), PROJECT, uuid.uuid4())
    assert not can(pipeline, Action.REQUIREMENT_CREATE, ref(OTHER)).allowed


@pytest.mark.parametrize(
    ("role", "action", "allowed"),
    [
        (Role.ANALYST, Action.CLASSIFICATION_OVERRIDE, True),
        (Role.COMPLIANCE_OFFICER, Action.CLASSIFICATION_OVERRIDE, False),
        (Role.ANALYST, Action.REVIEW_RESOLVE, True),
        (Role.COMPLIANCE_OFFICER, Action.REVIEW_READ, True),
        (Role.COMPLIANCE_OFFICER, Action.REVIEW_RESOLVE, False),
        (Role.AUDITOR, Action.REVIEW_READ, True),
        (Role.AUDITOR, Action.REVIEW_RESOLVE, False),
        (Role.AUDITOR, Action.SOURCE_READ, True),
        (Role.STAKEHOLDER, Action.REVIEW_READ, False),
        (Role.STAKEHOLDER, Action.SOURCE_READ, False),
        (Role.KB_ADMIN, Action.SOURCE_CREATE, False),
        (Role.PROJECT_MANAGER, Action.RUN_START, False),
    ],
)
def test_the_p3_grants(role: Role, action: Action, allowed: bool) -> None:
    assert can(person(role), action, ref()).allowed is allowed


# --- routers ---------------------------------------------------------------------


def test_routing_reads_only_state_flags() -> None:
    assert route_after_scope({"scope_source_ids": ["x"]}) == "extract_requirements"
    assert route_after_scope({"scope_version_ids": ["x"]}) == "classify"
    assert route_after_scope({"scope_source_ids": ["x"], "errors": [{}]}) == "error_handler"  # type: ignore[list-item]
    assert route_extraction({}) == "validate_extraction"
    assert route_extraction({"errors": [{}]}) == "error_handler"  # type: ignore[list-item]
    assert route_validation({}) == "persist_candidates"
    assert route_validation({"errors": [{}]}) == "error_handler"  # type: ignore[list-item]


def test_the_routers_close_over_nothing_llm_related() -> None:
    for router in (route_after_scope, route_extraction, route_validation):
        assert_is_deterministic_router(router)


def test_the_analysis_state_carries_no_content() -> None:
    assert_state_shape(AnalysisState)
    forbidden = {"statement", "text", "prompt", "quote", "transcript", "candidates"}
    assert not forbidden & set(AnalysisState.__annotations__)
