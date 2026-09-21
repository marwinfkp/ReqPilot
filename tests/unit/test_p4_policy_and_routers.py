"""P4 authorization (policy rule 7) and the elicitation routers (architecture C.4, ADR-009)."""

from __future__ import annotations

import uuid

import pytest

from reqpilot.domain.enums import Action, ActorKind, ResourceType, Role
from reqpilot.domain.ids import ActorId, ProjectId
from reqpilot.domain.policy import Actor, ResourceRef, can
from reqpilot.graph import routers
from reqpilot.graph.state import ElicitationState, assert_state_shape

pytestmark = pytest.mark.unit

PROJECT = ProjectId(uuid.uuid4())
OTHER = ProjectId(uuid.uuid4())


def actor(*roles: Role, kind: ActorKind = ActorKind.HUMAN, project: ProjectId = PROJECT) -> Actor:
    return Actor(
        actor_id=ActorId(uuid.uuid4()), kind=kind, roles_by_project={project: frozenset(roles)}
    )


def allowed(who: Actor, action: Action, project: ProjectId = PROJECT) -> bool:
    return can(
        who, action, ResourceRef(resource_type=ResourceType.PROJECT, project_id=project)
    ).allowed


def test_the_analyst_runs_interviews_and_owns_the_open_issues() -> None:
    analyst = actor(Role.ANALYST)
    for action in (
        Action.STAKEHOLDER_CREATE,
        Action.SESSION_CREATE,
        Action.SESSION_ANSWER,
        Action.SESSION_MANAGE,
        Action.QUALITY_FINDING_CREATE,
        Action.CLARIFICATION_RAISE,
        Action.CLARIFICATION_ANSWER,
        Action.CLARIFICATION_DISMISS,
    ):
        assert allowed(analyst, action), action


def test_a_stakeholder_answers_but_decides_nothing() -> None:
    stakeholder = actor(Role.STAKEHOLDER)
    assert allowed(stakeholder, Action.SESSION_ANSWER)
    assert allowed(stakeholder, Action.CLARIFICATION_ANSWER)
    assert allowed(stakeholder, Action.SESSION_READ)
    for action in (
        Action.STAKEHOLDER_CREATE,
        Action.SESSION_CREATE,
        Action.SESSION_MANAGE,
        Action.CLARIFICATION_DISMISS,
        Action.CLARIFICATION_RAISE,
        Action.QUALITY_FINDING_CREATE,
        Action.RUN_START,
    ):
        assert not allowed(stakeholder, action), action


def test_reviewing_roles_read_and_the_auditor_only_reads() -> None:
    for role in (
        Role.COMPLIANCE_OFFICER,
        Role.SECURITY_REVIEWER,
        Role.PROJECT_MANAGER,
        Role.AUDITOR,
    ):
        reader = actor(role)
        assert allowed(reader, Action.SESSION_READ) and allowed(reader, Action.CLARIFICATION_READ)
        assert not allowed(reader, Action.SESSION_ANSWER)
        assert not allowed(reader, Action.CLARIFICATION_DISMISS)


def test_rule_7_no_pipeline_answers_or_dismisses_whatever_its_roles() -> None:
    pipeline = actor(Role.ANALYST, Role.STAKEHOLDER, kind=ActorKind.SYSTEM)
    for action in (
        Action.SESSION_ANSWER,
        Action.CLARIFICATION_ANSWER,
        Action.CLARIFICATION_DISMISS,
        Action.CLARIFICATION_RAISE,
        Action.SESSION_CREATE,
        Action.SESSION_MANAGE,
        Action.STAKEHOLDER_CREATE,
        Action.QUALITY_FINDING_CREATE,
    ):
        assert not allowed(pipeline, action), action
    # What it may do: record the questions it proposed, and its run's records.
    assert allowed(pipeline, Action.UTTERANCE_RECORD)
    assert allowed(pipeline, Action.RUN_RECORD)


def test_a_role_in_one_project_grants_nothing_in_another() -> None:
    analyst = actor(Role.ANALYST, project=OTHER)
    assert not allowed(analyst, Action.SESSION_READ)
    assert not allowed(analyst, Action.CLARIFICATION_ANSWER)


# --- routers ----------------------------------------------------------------------------------


def test_the_elicitation_state_carries_no_content() -> None:
    assert_state_shape(ElicitationState)
    fields = set(ElicitationState.__annotations__)
    assert not fields & {"question", "answer", "text", "issue", "prompt", "transcript"}


@pytest.mark.parametrize(
    "point",
    ["select_next_topic", "generate_question", "await_answer", "assess_answer", "end_interview"],
)
def test_load_routes_to_the_durable_position(point: str) -> None:
    assert routers.route_after_load({"resume_point": point}) == point


def test_an_unknown_position_or_a_failure_stalls() -> None:
    assert routers.route_after_load({"resume_point": "approve_everything"}) == "stall"
    assert routers.route_after_load({"resume_point": "await_answer", "failure": "x"}) == "stall"


def test_the_assessment_router_reads_only_the_trackers_flag() -> None:
    assert routers.route_after_assessment({"awaiting_followup": True}) == "generate_question"
    assert routers.route_after_assessment({"awaiting_followup": False}) == "select_next_topic"
    assert routers.route_after_assessment({"awaiting_followup": True, "failure": "x"}) == "stall"
    # Model text in the state changes nothing: only the flag is read.
    assert (
        routers.route_after_assessment(
            {"awaiting_followup": False, "last_assessment": "vague"}  # type: ignore[typeddict-unknown-key]
        )
        == "select_next_topic"
    )


def test_coverage_complete_ends_the_interview() -> None:
    assert routers.route_after_topic({"complete": True}) == "end_interview"
    assert routers.route_after_topic({"complete": False}) == "generate_question"


@pytest.mark.parametrize(
    "router",
    [
        routers.route_after_load,
        routers.route_after_topic,
        routers.route_after_question,
        routers.route_after_await,
        routers.route_after_record,
        routers.route_after_assessment,
    ],
)
def test_elicitation_routers_close_over_nothing(router) -> None:  # type: ignore[no-untyped-def]
    routers.assert_is_deterministic_router(router)


def test_clarification_mode_routes_to_the_revision_node() -> None:
    assert routers.route_after_scope({"clarification_id": "x"}) == "extract_requirements"
    assert routers.route_after_scope({"scope_session_ids": ["s"]}) == "extract_requirements"
    assert routers.route_validation({"clarification_id": "x"}) == "persist_revision"
    assert routers.route_validation({}) == "persist_candidates"
