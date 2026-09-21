"""``elicitation_graph`` end to end with the scripted persona (architecture C.4; P4).

Real LangGraph interrupt/resume on a real checkpointer (the process-wide
in-memory saver offline; PostgreSQL in ``test_p4_postgres``), real gateway,
real validation - only the model is scripted.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.p4_helpers import make_world, run_persona_interview

from reqpilot.domain.enums import (
    AuditEventType,
    GraphRunStatus,
    InterviewSessionStatus,
    SpeakerKind,
    TopicStatus,
)
from reqpilot.domain.errors import ElicitationError
from reqpilot.domain.models.audit import AuditEvent
from reqpilot.domain.models.elicitation import Utterance
from reqpilot.domain.models.runs import GraphRun
from reqpilot.services.audit import AuditService

pytestmark = pytest.mark.workflow


@pytest.fixture
def world(db_session: Session):
    return make_world(db_session)


def test_start_asks_the_first_required_topic_and_suspends(world) -> None:
    turn = world.start()
    assert turn.question is not None
    assert turn.question.topic_id == "business_objectives"
    assert turn.question.speaker_kind is SpeakerKind.SYSTEM and turn.question.seq == 1
    assert turn.session.status is InterviewSessionStatus.ACTIVE
    run = world.session.get(GraphRun, turn.session.graph_run_id)
    assert run.status is GraphRunStatus.SUSPENDED
    # A real interrupt: the thread's next node is await_answer, and the state
    # holds the question's id - never its text.
    next_nodes, values = world.runner().thread_position(turn.session)
    assert next_nodes == ("await_answer",)
    assert values["pending_question_id"] == str(turn.question.id)
    assert turn.question.text not in repr(values)


def test_the_persona_interview_runs_to_completion(world) -> None:
    turn = run_persona_interview(world, world.start())
    assert turn.complete
    coverage = turn.coverage
    assert coverage.summary.remaining == () and coverage.summary.in_progress == ()
    assert "business_rules" in coverage.summary.unresolved
    assert "inputs" in coverage.summary.covered and "performance" in coverage.summary.covered
    run = world.session.get(GraphRun, turn.session.graph_run_id)
    assert run.status is GraphRunStatus.COMPLETED
    assert AuditService(world.session).verify_project_chain(world.project_id) == (True, None)


def test_follow_ups_are_bounded_and_on_the_same_topic(world) -> None:
    turn = run_persona_interview(world, world.start())
    utterances = list(
        world.session.scalars(
            select(Utterance).where(Utterance.session_id == turn.session.id).order_by(Utterance.seq)
        )
    )
    rules_questions = [
        u
        for u in utterances
        if u.speaker_kind is SpeakerKind.SYSTEM and u.topic_id == "business_rules"
    ]
    assert len(rules_questions) == 1 + world.rules.max_followups_per_topic
    assert [q.is_followup for q in rules_questions] == [False, True, True]
    entry = turn.coverage.entries["business_rules"]
    assert entry["status"] == TopicStatus.UNRESOLVED
    assert entry["followups"] == world.rules.max_followups_per_topic


def test_a_completed_session_accepts_no_answer(world) -> None:
    turn = run_persona_interview(world, world.start())
    with pytest.raises(ElicitationError):
        world.answer(turn.session.id, "one more thing")


def test_every_answer_is_audited(world) -> None:
    turn = run_persona_interview(world, world.start())
    events = list(
        world.session.scalars(select(AuditEvent).where(AuditEvent.project_id == world.project_id))
    )
    kinds = {e.event_type for e in events}
    assert {
        AuditEventType.INTERVIEW_SESSION_CREATED,
        AuditEventType.QUESTION_GENERATED,
        AuditEventType.UTTERANCE_RECORDED,
        AuditEventType.ANSWER_ASSESSED,
        AuditEventType.INTERVIEW_SESSION_COMPLETED,
        AuditEventType.RUN_SUSPENDED,
        AuditEventType.RUN_RESUMED,
    } <= kinds
    assert turn.session.questions_asked == sum(
        1 for e in events if e.event_type is AuditEventType.QUESTION_GENERATED
    )
