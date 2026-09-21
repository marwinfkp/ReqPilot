"""P4 on a live PostgreSQL: the durable checkpointer, restarts and the database triggers.

The interview runs with ``LANGGRAPH_CHECKPOINT_BACKEND=postgres``, so every
turn suspends into, and resumes from, the ``checkpoints`` tables on their own
connection - each turn is a fresh runner and a fresh connection, as after a
process restart. The triggers of migration ``0006`` are attacked with raw SQL
that bypasses the ORM. Skips without ``REQPILOT_TEST_DATABASE_URL``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from tests.conftest import postgres_url, requires_postgres
from tests.p3_helpers import TEST_SETTINGS
from tests.p4_helpers import extraction_rules, make_world, run_persona_interview

from reqpilot.config import Settings
from reqpilot.domain.enums import (
    FindingSeverity,
    InterviewSessionStatus,
    QualityFindingType,
    TopicStatus,
)
from reqpilot.domain.models.elicitation import Clarification, Utterance
from reqpilot.domain.models.requirements import RequirementVersion
from reqpilot.graph import builder
from reqpilot.graph.builder import thread_id_for
from reqpilot.graph.clarification_runner import ClarificationRunner
from reqpilot.graph.elicitation_runner import ElicitationRunner
from reqpilot.graph.runner import AnalysisRunner
from reqpilot.services.clarification import QualityFindingService

pytestmark = [pytest.mark.integration, requires_postgres]


def pg_settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None, DATABASE_URL=postgres_url(), LANGGRAPH_CHECKPOINT_BACKEND="postgres"
    )


@pytest.fixture
def world(pg_session: Session):
    world = make_world(pg_session)
    settings = pg_settings()
    # Every call builds a new runner, and the checkpointer a new connection.
    world.runner = lambda: ElicitationRunner(  # type: ignore[method-assign]
        pg_session, world.gateway, world.rules, settings=settings
    )
    return world


def refused(session: Session, sql: str, params: dict | None = None) -> str:
    savepoint = session.begin_nested()
    with pytest.raises(DBAPIError) as excinfo:
        session.execute(text(sql), params or {})
    savepoint.rollback()
    return str(excinfo.value)


def checkpoint_rows(run_id) -> list[str]:  # type: ignore[no-untyped-def]
    engine = create_engine(postgres_url(), future=True)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT checkpoint::text FROM checkpoints WHERE thread_id = :t"),
                {"t": thread_id_for(run_id)},
            ).all()
            writes = conn.execute(
                text("SELECT encode(blob, 'escape') FROM checkpoint_writes WHERE thread_id = :t"),
                {"t": thread_id_for(run_id)},
            ).all()
            blobs = conn.execute(
                text("SELECT encode(blob, 'escape') FROM checkpoint_blobs WHERE thread_id = :t"),
                {"t": thread_id_for(run_id)},
            ).all()
    finally:
        engine.dispose()
    return [r[0] for r in rows] + [r[0] or "" for r in writes] + [r[0] or "" for r in blobs]


# --- the durable checkpointer -------------------------------------------------------------


def test_the_checkpoint_tables_exist(pg_engine_migrated) -> None:
    with builder.checkpointer_scope(pg_settings()):
        pass
    with pg_engine_migrated.connect() as conn:
        names = set(
            conn.execute(
                text("SELECT tablename FROM pg_tables WHERE tablename LIKE 'checkpoint%'")
            ).scalars()
        )
    assert {"checkpoints", "checkpoint_blobs", "checkpoint_writes"} <= names


def test_the_persona_interview_runs_on_the_postgres_checkpointer(world) -> None:
    turn = world.start()
    assert turn.question is not None
    rows = checkpoint_rows(turn.session.graph_run_id)
    assert rows, "the suspended thread is in PostgreSQL"
    turn = run_persona_interview(world, turn)
    assert turn.complete and turn.session.status is InterviewSessionStatus.COMPLETED
    entries = turn.coverage.entries
    assert entries["business_rules"]["status"] == TopicStatus.UNRESOLVED
    assert entries["inputs"]["status"] == TopicStatus.COVERED
    # The checkpoint holds ids and counters, never what was said.
    dumped = "\n".join(checkpoint_rows(turn.session.graph_run_id))
    for topic in world.model.data["topics"].values():
        for answer in topic["answers"]:
            assert answer["text"] not in dumped


def test_a_restart_mid_interview_resumes_the_same_pending_question(world, monkeypatch) -> None:
    turn = world.start()
    turn = world.answer(turn.session.id, world.persona_answer(turn))
    pending = turn.question.id
    # A new process: nothing in memory survives, not even the "tables are ready" note.
    monkeypatch.setattr(builder, "_POSTGRES_READY", set())
    from langgraph.checkpoint.memory import MemorySaver

    monkeypatch.setattr(builder, "_SHARED_MEMORY_SAVER", MemorySaver())
    runner = ElicitationRunner(world.session, world.gateway, world.rules, settings=pg_settings())
    position = runner.turn(
        actor=world.analyst, project_id=world.project_id, session_id=turn.session.id
    )
    assert position.question.id == pending, "no question is re-asked after a restart"
    calls = world.model.calls["stakeholder_interview_question"]
    turn = world.answer(turn.session.id, world.persona_answer(position))
    assert turn.question is not None and turn.question.id != pending
    assert world.model.calls["stakeholder_interview_question"] == calls + 1
    assert run_persona_interview(world, turn).complete


def test_the_memory_backend_is_not_what_ran(world) -> None:
    """Guard against a silently-memory test: the settings really name postgres."""
    assert pg_settings().checkpoint_backend == "postgres"
    assert TEST_SETTINGS.checkpoint_backend == "memory"


# --- the triggers of migration 0006 -------------------------------------------------------


def test_an_utterance_can_never_be_changed_or_deleted(world) -> None:
    turn = world.start()
    session = world.session
    session.flush()
    params = {"i": turn.question.id}
    assert "append-only" in refused(
        session, "UPDATE utterance SET text = 'x' WHERE id = :i", params
    )
    assert "append-only" in refused(session, "DELETE FROM utterance WHERE id = :i", params)


def test_a_sessions_identity_is_fixed_and_it_is_never_deleted(world) -> None:
    turn = world.start()
    session = world.session
    session.flush()
    params = {"i": turn.session.id}
    assert "fixed" in refused(
        session, "UPDATE interview_session SET template_id = 'security' WHERE id = :i", params
    )
    assert "never deleted" in refused(
        session, "DELETE FROM interview_session WHERE id = :i", params
    )
    assert "never deleted" in refused(
        session, "DELETE FROM stakeholder WHERE id = :i", {"i": world.stakeholder.id}
    )


def test_an_utterance_cannot_point_at_another_projects_session(world, pg_session) -> None:
    turn = world.start()
    other = make_world(pg_session, "Other project (synthetic)")
    pg_session.flush()
    savepoint = pg_session.begin_nested()
    with pytest.raises(IntegrityError):
        pg_session.execute(
            text(
                "INSERT INTO utterance (id, project_id, session_id, seq, speaker_kind, text, "
                " is_followup, on_behalf, created_at) VALUES (gen_random_uuid(), :p, :s, 99, "
                " 'SYSTEM', 'x', false, false, now())"
            ),
            {"p": other.project_id, "s": turn.session.id},
        )
    savepoint.rollback()


@pytest.fixture
def clarified(world):
    turn = run_persona_interview(world, world.start())
    AnalysisRunner(
        world.session, world.gateway, extraction_rules(), settings=TEST_SETTINGS
    ).extract(
        actor=world.analyst,
        project_id=world.project_id,
        session_ids=[turn.session.id],
        domain="LOAN",
    )
    target = next(
        v
        for v in world.session.scalars(select(RequirementVersion))
        if "decision letter quickly" in v.statement
    )
    data = world.model.data["clarification"]
    finding = QualityFindingService(world.session, world.analyst).record(
        project_id=world.project_id,
        version_id=target.id,
        finding_type=QualityFindingType.AMBIGUITY,
        severity=FindingSeverity.MEDIUM,
        rationale=data["finding"]["rationale"],
        span_quote=data["finding"]["span"],
    )
    runner = ClarificationRunner(
        world.session, world.gateway, world.rules, extraction_rules(), settings=TEST_SETTINGS
    )
    raised = runner.raise_for_finding(
        actor=world.analyst,
        project_id=world.project_id,
        finding_id=finding.id,
        asked_of=world.stakeholder.id,
    )
    assert raised.clarification is not None, raised.error
    return world, finding, raised.clarification, runner


def test_one_open_clarification_per_finding_is_enforced_by_the_database(clarified) -> None:
    world, _finding, clarification, _runner = clarified
    session = world.session
    session.flush()
    columns = [
        c
        for c in session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'clarification' ORDER BY ordinal_position"
            )
        ).scalars()
        if c != "id"
    ]
    listed = ", ".join(columns)
    savepoint = session.begin_nested()
    with pytest.raises(IntegrityError, match="uq_clarification_open_per_finding"):
        # A second OPEN row for the same finding, bypassing the service.
        session.execute(
            text(
                f"INSERT INTO clarification (id, {listed}) "
                f"SELECT gen_random_uuid(), {listed} FROM clarification WHERE id = :i"
            ),
            {"i": clarification.id},
        )
    savepoint.rollback()


def test_a_clarification_is_resolved_once_and_its_question_is_fixed(clarified) -> None:
    world, finding, clarification, runner = clarified
    session = world.session
    params = {"i": clarification.id}
    assert "immutable" in refused(
        session, "UPDATE clarification SET question = 'rewritten' WHERE id = :i", params
    )
    runner.answer(
        actor=world.stakeholder_user,
        project_id=world.project_id,
        clarification_id=clarification.id,
        text=world.model.data["clarification"]["answer"],
    )
    session.flush()
    stored = session.get(Clarification, clarification.id)
    assert stored.reanalysis_status is not None
    assert "once" in refused(
        session, "UPDATE clarification SET status = 'OPEN' WHERE id = :i", params
    )
    assert "never deleted" in refused(session, "DELETE FROM clarification WHERE id = :i", params)
    assert "immutable" in refused(
        session,
        "UPDATE quality_finding SET rationale = 'rewritten' WHERE id = :i",
        {"i": finding.id},
    )
    assert "never deleted" in refused(
        session, "DELETE FROM quality_finding WHERE id = :i", {"i": finding.id}
    )
    answer = session.get(Utterance, stored.answer_utterance_id)
    assert answer is not None and answer.replies_to_id == stored.question_utterance_id
