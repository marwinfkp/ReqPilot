"""The clarification loop (P4; FR-CLR-001..004; architecture E #4, G.4, H.3).

A persona interview is extracted through P3, then clarifications are raised,
answered, dismissed and re-analysed against the resulting requirements.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.p3_helpers import TEST_SETTINGS
from tests.p4_helpers import extraction_rules, make_world, run_persona_interview, segment_id

from reqpilot.domain.enums import (
    ClarificationStatus,
    FindingSeverity,
    QualityFindingStatus,
    QualityFindingType,
    ReanalysisStatus,
)
from reqpilot.domain.errors import (
    AuthorizationError,
    ClarificationError,
    ProjectIsolationError,
    StateTransitionError,
)
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.elicitation import Clarification
from reqpilot.domain.models.requirements import Requirement, RequirementVersion
from reqpilot.graph.clarification_runner import ClarificationRunner
from reqpilot.graph.runner import AnalysisRunner
from reqpilot.services.clarification import ClarificationService, QualityFindingService
from reqpilot.services.requirements import RequirementService

pytestmark = pytest.mark.integration


@pytest.fixture
def world(db_session: Session):
    world = make_world(db_session)
    turn = run_persona_interview(world, world.start())
    AnalysisRunner(db_session, world.gateway, extraction_rules(), settings=TEST_SETTINGS).extract(
        actor=world.analyst,
        project_id=world.project_id,
        session_ids=[turn.session.id],
        domain="LOAN",
    )
    world.target = next(  # type: ignore[attr-defined]
        v
        for v in db_session.scalars(select(RequirementVersion))
        if "decision letter quickly" in v.statement
    )
    return world


def runner(world) -> ClarificationRunner:  # type: ignore[no-untyped-def]
    return ClarificationRunner(
        world.session, world.gateway, world.rules, extraction_rules(), settings=TEST_SETTINGS
    )


def finding(world, **overrides):  # type: ignore[no-untyped-def]
    data = world.model.data["clarification"]["finding"]
    return QualityFindingService(world.session, overrides.pop("actor", world.analyst)).record(
        project_id=world.project_id,
        version_id=overrides.pop("version_id", world.target.id),
        finding_type=QualityFindingType.AMBIGUITY,
        severity=FindingSeverity.MEDIUM,
        rationale=data["rationale"],
        span_quote=overrides.pop("span_quote", data["span"]),
    )


def raise_one(world) -> Clarification:  # type: ignore[no-untyped-def]
    outcome = runner(world).raise_for_finding(
        actor=world.analyst,
        project_id=world.project_id,
        finding_id=finding(world).id,
        asked_of=world.stakeholder.id,
    )
    assert outcome.clarification is not None, outcome.error
    return outcome.clarification


def answer(world, clarification: Clarification, *, actor=None, text=None):  # type: ignore[no-untyped-def]
    return runner(world).answer(
        actor=actor or world.stakeholder_user,
        project_id=world.project_id,
        clarification_id=clarification.id,
        text=text or world.model.data["clarification"]["answer"],
    )


# --- findings (the minimal P4 interface) ----------------------------------------------------


def test_a_finding_is_bound_to_words_of_the_statement(world) -> None:
    with pytest.raises(ClarificationError, match="words of the requirement"):
        finding(world, span_quote="not in the statement")


def test_only_an_analyst_records_findings(world) -> None:
    with pytest.raises(AuthorizationError):
        finding(world, actor=world.stakeholder_user)


def test_an_open_finding_blocks_validation(world) -> None:
    finding(world)
    service = RequirementService(world.session, world.analyst)
    service.transition(
        project_id=world.project_id, version_id=world.target.id, target=RequirementState.ANALYZED
    )
    with pytest.raises(StateTransitionError, match="open quality defect"):
        service.transition(
            project_id=world.project_id,
            version_id=world.target.id,
            target=RequirementState.VALIDATED,
        )


# --- raising --------------------------------------------------------------------------


def test_raising_binds_the_question_and_moves_the_version(world) -> None:
    clarification = raise_one(world)
    assert clarification.status is ClarificationStatus.OPEN
    assert clarification.assignee_user_id == world.analyst.actor_id
    assert clarification.asked_of_stakeholder_id == world.stakeholder.id
    assert world.target.state is RequirementState.CLARIFICATION_REQUIRED
    question = ClarificationService(world.session, world.analyst, world.rules).question_utterance(
        clarification
    )
    assert question is not None and question.text == clarification.question


def test_one_open_clarification_per_finding(world) -> None:
    clarification = raise_one(world)
    with pytest.raises(ClarificationError, match="already exists"):
        runner(world).raise_for_finding(
            actor=world.analyst,
            project_id=world.project_id,
            finding_id=clarification.quality_finding_id,
            asked_of=world.stakeholder.id,
        )


def test_a_generic_question_is_never_asked(world) -> None:
    world.model.overrides["clarification_question"] = lambda r: json.dumps(
        {
            "question": "Can you clarify this?",
            "expected_answer_shape": "anything",
            "defect_id": r.instructions.split(" id ")[1][:36],
        }
    )
    state_before = world.target.state
    outcome = runner(world).raise_for_finding(
        actor=world.analyst,
        project_id=world.project_id,
        finding_id=finding(world).id,
        asked_of=world.stakeholder.id,
    )
    assert outcome.clarification is None and "generic" in (outcome.error or "")
    assert (
        world.model.calls["clarification_question"]
        == world.rules.clarification_max_question_attempts
    )
    assert world.session.scalars(select(Clarification)).first() is None
    assert world.target.state is state_before


def test_a_stakeholder_cannot_raise(world) -> None:
    with pytest.raises(AuthorizationError):
        runner(world).raise_for_finding(
            actor=world.stakeholder_user,
            project_id=world.project_id,
            finding_id=finding(world).id,
            asked_of=world.stakeholder.id,
        )


# --- answering ----------------------------------------------------------------------------


def test_an_answer_is_recorded_once(world) -> None:
    clarification = raise_one(world)
    outcome = answer(world, clarification)
    assert outcome.clarification.status is ClarificationStatus.ANSWERED
    assert outcome.answer.replies_to_id == clarification.question_utterance_id
    with pytest.raises(ClarificationError, match="already been answered"):
        answer(world, clarification)


def test_an_analyst_may_answer_on_behalf_and_it_is_recorded(world) -> None:
    clarification = raise_one(world)
    outcome = answer(world, clarification, actor=world.analyst)
    assert outcome.answer.on_behalf and outcome.answer.recorded_by == world.analyst.actor_id
    assert outcome.answer.speaker_ref == world.stakeholder.id


def test_re_analysis_that_confirms_the_statement_creates_no_version(world) -> None:
    clarification = raise_one(world)
    original = world.model.data["extraction"][4]  # the "decision letter quickly" item
    world.model.overrides["requirement_extraction"] = lambda r: json.dumps(
        {
            "requirements": [
                {
                    "candidate_key": "c1",
                    "statement": original["statement"],
                    "requirement_type": "functional",
                    "evidence": [
                        {"segment_id": segment_id(r, original["quote"]), "quote": original["quote"]}
                    ],
                    "review_signal": 0.9,
                }
            ]
        }
    )
    outcome = answer(world, clarification)
    assert outcome.clarification.reanalysis_status is ReanalysisStatus.NO_CHANGE
    requirement = world.session.get(Requirement, world.target.requirement_id)
    assert requirement.current_version_id == world.target.id
    assert world.target.state is RequirementState.CLARIFIED


def test_a_failed_re_analysis_keeps_the_answer_and_can_be_retried(world) -> None:
    clarification = raise_one(world)
    world.model.overrides["requirement_extraction"] = lambda _r: "not json"
    outcome = answer(world, clarification)
    assert outcome.error is not None
    assert clarification.status is ClarificationStatus.ANSWERED
    assert clarification.reanalysis_status is ReanalysisStatus.FAILED
    requirement = world.session.get(Requirement, world.target.requirement_id)
    assert requirement.current_version_id == world.target.id, "no version was fabricated"
    # A stakeholder cannot retry; an analyst can, once the cause is gone.
    with pytest.raises(AuthorizationError):
        runner(world).reanalyse(
            actor=world.stakeholder_user,
            project_id=world.project_id,
            clarification_id=clarification.id,
        )
    del world.model.overrides["requirement_extraction"]
    retried = runner(world).reanalyse(
        actor=world.analyst, project_id=world.project_id, clarification_id=clarification.id
    )
    assert retried.error is None
    assert clarification.reanalysis_status is ReanalysisStatus.NEW_VERSION
    with pytest.raises(ClarificationError, match="already completed"):
        runner(world).reanalyse(
            actor=world.analyst, project_id=world.project_id, clarification_id=clarification.id
        )


def test_a_finding_on_a_superseded_version_cannot_be_clarified(world) -> None:
    clarification = raise_one(world)
    answer(world, clarification)
    stale = finding(world, span_quote=None)
    with pytest.raises(ClarificationError, match="no longer current"):
        runner(world).raise_for_finding(
            actor=world.analyst,
            project_id=world.project_id,
            finding_id=stale.id,
            asked_of=world.stakeholder.id,
        )


# --- dismissing ---------------------------------------------------------------------------


def test_dismissal_needs_an_analyst_and_a_reason_and_changes_nothing_else(world) -> None:
    clarification = raise_one(world)
    service = ClarificationService(world.session, world.analyst, world.rules)
    with pytest.raises(ClarificationError, match="reason"):
        service.dismiss(project_id=world.project_id, clarification_id=clarification.id, reason=" ")
    with pytest.raises(AuthorizationError):
        ClarificationService(world.session, world.stakeholder_user, world.rules).dismiss(
            project_id=world.project_id, clarification_id=clarification.id, reason="no"
        )
    service.dismiss(
        project_id=world.project_id,
        clarification_id=clarification.id,
        reason="Superseded by the service-level agreement workshop (synthetic).",
    )
    assert clarification.status is ClarificationStatus.DISMISSED
    # Dismissing is not approval and not a fix: the version and its finding stand.
    assert world.target.state is RequirementState.CLARIFICATION_REQUIRED
    stored = QualityFindingService(world.session, world.analyst).get(
        world.project_id, clarification.quality_finding_id
    )
    assert stored.status is QualityFindingStatus.OPEN
    with pytest.raises(ClarificationError, match="dismissed"):
        answer(world, clarification)


# --- the open-issues list -------------------------------------------------------------------


def test_the_open_issues_list_shows_status_assignee_and_age(world) -> None:
    clarification = raise_one(world)
    issues = ClarificationService(world.session, world.analyst, world.rules).issues(
        world.project_id
    )
    (issue,) = issues
    assert issue.clarification.id == clarification.id
    assert issue.requirement_human_id.startswith("FR-LOAN-")
    assert issue.finding.finding_type is QualityFindingType.AMBIGUITY
    assert issue.stakeholder_name == world.stakeholder.name
    assert issue.age.total_seconds() >= 0


def test_another_projects_members_see_no_clarification(world, db_session: Session) -> None:
    clarification = raise_one(world)
    other = make_world(db_session, "Other project (synthetic)")
    service = ClarificationService(db_session, other.analyst, other.rules)
    with pytest.raises(ProjectIsolationError):
        service.issues(world.project_id)
    with pytest.raises(ProjectIsolationError):
        service.dismiss(
            project_id=world.project_id, clarification_id=clarification.id, reason="not mine"
        )
