"""Stakeholders, interview sessions and utterances (P4; FR-ELI-001..006, G.3).

Real services, real graph, real gateway; the model is the scripted persona.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.p4_helpers import make_world, run_persona_interview

from reqpilot.domain.enums import (
    AuditEventType,
    InterviewSessionStatus,
    SpeakerKind,
    StakeholderAuthority,
)
from reqpilot.domain.errors import (
    AuthorizationError,
    ElicitationError,
    ImmutableRecordError,
    ProjectIsolationError,
)
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.audit import AuditEvent
from reqpilot.domain.models.elicitation import Utterance
from reqpilot.graph import builder
from reqpilot.services.elicitation import InterviewSessionService, StakeholderService

pytestmark = pytest.mark.integration


@pytest.fixture
def world(db_session: Session):
    return make_world(db_session)


# --- stakeholders and sessions ----------------------------------------------------------


def test_a_stakeholder_role_must_be_one_the_templates_define(world) -> None:
    with pytest.raises(ElicitationError, match="unknown stakeholder role"):
        StakeholderService(world.session, world.analyst, world.rules).create(
            project_id=world.project_id,
            name="Someone",
            stakeholder_role="astrologer",
            authority_level=StakeholderAuthority.INFORMANT,
        )


def test_a_linked_user_must_hold_the_stakeholder_role_in_the_project(world) -> None:
    with pytest.raises(ElicitationError, match="Stakeholder role"):
        StakeholderService(world.session, world.analyst, world.rules).create(
            project_id=world.project_id,
            name="Not a stakeholder",
            stakeholder_role="customer",
            authority_level=StakeholderAuthority.INFORMANT,
            user_id=world.analyst.actor_id,
        )


def test_a_session_uses_the_stakeholders_role_template(world) -> None:
    turn = world.start()
    assert (turn.session.template_id, turn.session.template_version) == ("product_owner", "1.0.0")
    with pytest.raises(ElicitationError, match="FR-ELI-001"):
        world.start(template_id="security")


def test_only_an_analyst_starts_a_session(world) -> None:
    with pytest.raises(AuthorizationError):
        world.start(actor=world.stakeholder_user)


# --- utterances ----------------------------------------------------------------------------


def test_every_utterance_has_speaker_role_time_session_and_sequence(world) -> None:
    turn = world.start()
    turn = world.answer(turn.session.id, world.persona_answer(turn))
    utterances = list(
        world.session.scalars(select(Utterance).where(Utterance.session_id == turn.session.id))
    )
    question, answer = sorted(utterances, key=lambda u: u.seq)[:2]
    assert question.speaker_kind is SpeakerKind.SYSTEM and question.speaker_ref is None
    assert answer.speaker_kind is SpeakerKind.STAKEHOLDER
    assert answer.speaker_ref == world.stakeholder.id
    assert answer.stakeholder_role == "product_owner"
    assert answer.replies_to_id == question.id and answer.session_id == turn.session.id
    assert answer.project_id == world.project_id and answer.created_at is not None
    assert answer.recorded_by == world.stakeholder_user.actor_id and not answer.on_behalf


def test_utterances_are_append_only(world) -> None:
    turn = world.start()
    question = turn.question
    question.text = "rewritten"
    with pytest.raises(ImmutableRecordError, match="append-only"):
        world.session.flush()
    world.session.rollback()


def test_an_analyst_may_answer_on_the_stakeholders_behalf(world) -> None:
    turn = world.start()
    turn = world.answer(turn.session.id, world.persona_answer(turn), actor=world.analyst)
    answer = world.session.scalars(
        select(Utterance).where(
            Utterance.session_id == turn.session.id,
            Utterance.speaker_kind == SpeakerKind.STAKEHOLDER,
        )
    ).one()
    # The speaker is still the stakeholder; who typed it is recorded separately.
    assert answer.speaker_ref == world.stakeholder.id
    assert answer.recorded_by == world.analyst.actor_id and answer.on_behalf


def test_an_empty_or_oversized_answer_is_refused(world) -> None:
    turn = world.start()
    with pytest.raises(ElicitationError):
        world.answer(turn.session.id, "   ")
    with pytest.raises(ElicitationError):
        world.answer(turn.session.id, "x" * (world.rules.max_answer_chars + 1))


# --- pause and resume ------------------------------------------------------------------


def test_pause_blocks_answers_and_resume_asks_no_new_question(world) -> None:
    runner = world.runner()
    turn = world.start()
    question_id = turn.question.id
    paused = runner.pause(
        actor=world.analyst, project_id=world.project_id, session_id=turn.session.id
    )
    assert paused.session.status is InterviewSessionStatus.PAUSED
    with pytest.raises(ElicitationError, match="paused"):
        world.answer(turn.session.id, world.persona_answer(turn))
    resumed = runner.resume(
        actor=world.analyst, project_id=world.project_id, session_id=turn.session.id
    )
    assert resumed.session.status is InterviewSessionStatus.ACTIVE
    assert resumed.question.id == question_id, "the same pending question, not a new one"
    system_questions = [
        u
        for u in world.session.scalars(
            select(Utterance).where(Utterance.session_id == turn.session.id)
        )
        if u.speaker_kind is SpeakerKind.SYSTEM
    ]
    assert len(system_questions) == 1, "no duplicated question"
    # The counter and coverage survive the pause.
    turn = world.answer(turn.session.id, world.persona_answer(resumed))
    assert turn.question.topic_id == "users_and_roles"


def test_a_stakeholder_cannot_pause_or_resume(world) -> None:
    turn = world.start()
    with pytest.raises(AuthorizationError):
        world.runner().pause(
            actor=world.stakeholder_user, project_id=world.project_id, session_id=turn.session.id
        )


def test_a_lost_checkpoint_degrades_to_a_safe_restart(world, monkeypatch) -> None:
    """The durable session is the source of truth; the thread is re-derived from it."""
    turn = world.start()
    turn = world.answer(turn.session.id, world.persona_answer(turn))  # topic 2 pending
    pending = turn.question.id
    from langgraph.checkpoint.memory import MemorySaver

    monkeypatch.setattr(builder, "_SHARED_MEMORY_SAVER", MemorySaver())  # checkpoint gone
    turn = world.answer(turn.session.id, world.persona_answer(turn))
    assert turn.question is not None and turn.question.id != pending
    answers = [
        u
        for u in world.session.scalars(
            select(Utterance).where(Utterance.session_id == turn.session.id)
        )
        if u.speaker_kind is SpeakerKind.STAKEHOLDER
    ]
    assert len(answers) == 2 and turn.session.unassessed_answer_id is None
    run = run_persona_interview(world, turn)
    assert run.complete


# --- failure is safe and visible -------------------------------------------------------


def test_a_question_that_keeps_failing_validation_stalls_the_session(world) -> None:
    world.model.overrides["stakeholder_interview_question"] = lambda _r: json.dumps(
        {
            "question": "Is the weather nice today?",
            "topic_id": "business_objectives",
            "is_followup": False,
        }
    )
    turn = world.start()
    assert turn.stalled and turn.question is None
    assert turn.session.stall_reason.startswith("question_rejected")
    assert world.model.calls["stakeholder_interview_question"] == world.rules.max_question_attempts
    # The analyst retries once the cause is fixed; the interview continues.
    del world.model.overrides["stakeholder_interview_question"]
    retried = world.runner().resume(
        actor=world.analyst, project_id=world.project_id, session_id=turn.session.id
    )
    assert retried.question is not None and retried.question.topic_id == "business_objectives"


def test_malformed_assessment_output_stalls_without_advancing(world) -> None:
    turn = world.start()
    world.model.overrides["stakeholder_answer_assessment"] = lambda _r: "not json"
    stalled = world.answer(turn.session.id, world.persona_answer(turn))
    assert stalled.stalled
    assert stalled.session.unassessed_answer_id is not None, "the answer is kept for re-assessment"
    assert stalled.coverage.entries["business_objectives"]["status"] == "in_progress"
    del world.model.overrides["stakeholder_answer_assessment"]
    resumed = world.runner().resume(
        actor=world.analyst, project_id=world.project_id, session_id=turn.session.id
    )
    assert resumed.question.topic_id == "users_and_roles"
    assert resumed.coverage.entries["business_objectives"]["status"] == "covered"


# --- visibility --------------------------------------------------------------------------


def test_a_stakeholder_sees_only_their_own_sessions(world) -> None:
    turn = world.start()
    other = StakeholderService(world.session, world.analyst, world.rules).create(
        project_id=world.project_id,
        name="Someone else (fictional)",
        stakeholder_role="product_owner",
        authority_level=StakeholderAuthority.CONTRIBUTOR,
    )
    other_turn = world.start(stakeholder_id=other.id)
    service = InterviewSessionService(world.session, world.stakeholder_user, world.rules)
    assert [s.id for s in service.list_sessions(ProjectId(world.project_id))] == [turn.session.id]
    assert service.get(world.project_id, other_turn.session.id) is None
    with pytest.raises(ProjectIsolationError):
        world.answer(other_turn.session.id, "not my interview")


def test_every_interview_step_is_audited_by_reference(world) -> None:
    run_persona_interview(world, world.start())
    events = list(
        world.session.scalars(select(AuditEvent).where(AuditEvent.project_id == world.project_id))
    )
    types = {e.event_type for e in events}
    assert AuditEventType.STAKEHOLDER_CREATED in types
    assert AuditEventType.INTERVIEW_SESSION_COMPLETED in types
    persona_texts = [a["text"] for t in world.model.data["topics"].values() for a in t["answers"]]
    dumped = json.dumps([e.payload for e in events])
    for text in persona_texts:
        assert text not in dumped, "audit payloads carry references, never answers"
