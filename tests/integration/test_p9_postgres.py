"""P9 on a live PostgreSQL: the recommendation end to end, and the guards of migration 0011.

The ORM guards are one layer; these tests attack the second - the database's own
append-only triggers, the run guard, the composite foreign keys and the widened
trace allowlist - with raw SQL that bypasses the ORM. Skips without
``REQPILOT_TEST_DATABASE_URL``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from tests.conftest import postgres_url, requires_postgres
from tests.p9_helpers import P9World, make_p9_world

from reqpilot.domain.enums import ApprovalTaskStatus, SdlcRunStatus
from reqpilot.services.traceability import TraceGraphSync

pytestmark = [pytest.mark.integration, requires_postgres]

REPO_ROOT = Path(__file__).resolve().parents[2]
P9_TABLES = {"sdlc_run", "sdlc_factor", "sdlc_candidate", "sdlc_rule_application"}


@pytest.fixture
def world(pg_session: Session) -> P9World:
    return make_p9_world(pg_session)


@pytest.fixture
def selected(world: P9World) -> tuple[P9World, uuid.UUID]:
    run_id = world.start().sdlc_run_id
    assert run_id is not None
    for task in world.g6_tasks(ApprovalTaskStatus.OPEN):
        world.decide(task)
    world.session.flush()
    return world, run_id


def refused(session: Session, sql: str, params: dict | None = None) -> str:
    savepoint = session.begin_nested()
    with pytest.raises(DBAPIError) as excinfo:
        session.execute(text(sql), params or {})
        session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    savepoint.rollback()
    return str(excinfo.value.orig)


def test_the_whole_p9_flow_runs_on_postgresql(selected) -> None:  # type: ignore[no-untyped-def]
    world, run_id = selected
    run = world.service().get(world.project_id, run_id)
    assert run.status is SdlcRunStatus.SELECTED
    assert run.selected_candidate == run.top_candidate
    TraceGraphSync(world.session, world.p8.analyst).sync(world.project_id)
    count = world.session.execute(
        text(
            "SELECT count(*) FROM traceability_link WHERE project_id = :p AND "
            "from_type = 'sdlc_run' AND link_type = 'APPROVED_BY'"
        ),
        {"p": world.project_id},
    ).scalar_one()
    assert count == 4


def test_factors_candidates_and_rules_are_append_only(selected) -> None:  # type: ignore[no-untyped-def]
    world, run_id = selected
    # An override that triggers R1 (regulatory >= 4 and stability <= 2), so the run
    # has a rule application row to attack as well.
    from reqpilot.domain.enums import Role

    vetoed = (
        world.runner()
        .override(
            actor=world.p8.analyst,
            project_id=world.project_id,
            run_id=run_id,
            factor="requirement_stability",
            new_score=1,
            reason="Scope is still moving (synthetic).",
            role=Role.ANALYST,
        )
        .sdlc_run_id
    )
    world.session.flush()
    session = world.session
    rules = session.execute(
        text("SELECT count(*) FROM sdlc_rule_application WHERE sdlc_run_id = :r"), {"r": vetoed}
    ).scalar_one()
    assert rules >= 1
    for table, column, value in (
        ("sdlc_factor", "score", 5),
        ("sdlc_candidate", "normalised_score", 99.0),
        ("sdlc_rule_application", "changed_ranking", True),
    ):
        message = refused(
            session,
            f"UPDATE {table} SET {column} = :v WHERE sdlc_run_id = :r",
            {"v": value, "r": vetoed},
        )
        assert "append-only" in message or "immutable" in message, (table, message)
        message = refused(session, f"DELETE FROM {table} WHERE sdlc_run_id = :r", {"r": vetoed})
        assert "append-only" in message or "immutable" in message, (table, message)


def test_the_run_guard_holds_the_ranking_and_the_selection(selected) -> None:  # type: ignore[no-untyped-def]
    world, run_id = selected
    session = world.session
    assert "immutable" in refused(
        session, "UPDATE sdlc_run SET top_candidate = 'agile' WHERE id = :r", {"r": run_id}
    )
    assert "immutable" in refused(
        session, "UPDATE sdlc_run SET ranking_hash = repeat('0', 64) WHERE id = :r", {"r": run_id}
    )
    assert "written once" in refused(
        session,
        "UPDATE sdlc_run SET explanation_narrative = 'rewritten' WHERE id = :r",
        {"r": run_id},
    )
    assert "written once" in refused(
        session, "UPDATE sdlc_run SET selected_candidate = 'agile' WHERE id = :r", {"r": run_id}
    )
    assert "never deleted" in refused(session, "DELETE FROM sdlc_run WHERE id = :r", {"r": run_id})


def test_only_the_computed_first_can_be_selected(world: P9World) -> None:
    run_id = world.start().sdlc_run_id
    world.session.flush()
    message = refused(
        world.session,
        "UPDATE sdlc_run SET status = 'SELECTED', selected_candidate = 'no_such', "
        "selected_at = now() WHERE id = :r",
        {"r": run_id},
    )
    assert "computed first" in message


def test_a_run_cannot_bind_another_projects_baseline(world: P9World) -> None:
    other = make_p9_world(world.session, "Another lender (synthetic)")
    run_id = world.start().sdlc_run_id
    world.session.flush()
    message = refused(
        world.session,
        "UPDATE sdlc_factor SET project_id = :o WHERE sdlc_run_id = :r",
        {"o": other.project_id, "r": run_id},
    )
    assert message
    message = refused(
        world.session,
        "INSERT INTO sdlc_run (id, project_id, baseline_id, status, ruleset_version, ruleset_ref, "
        "rules_sha256, weights_version, input_fingerprint, profile_hash, ranking_hash, "
        "top_candidate, reversal_conditions, facts_summary, explanation_status, "
        "explanation_counter_arguments, asserted_scores, explanation_discrepancies, "
        "explanation_attempts, created_by, created_at, updated_at) VALUES (:id, :p, :b, "
        "'RANKED', '1.0.0', 'x', repeat('a', 64), '1.0.0', repeat('a', 64), repeat('a', 64), "
        "repeat('a', 64), 'agile', '[]', '{}', 'NOT_GENERATED', '[]', '{}', '[]', 0, :u, :t, :t)",
        {
            "id": uuid.uuid4(),
            "p": other.project_id,
            "b": world.baseline_id,
            "u": uuid.uuid4(),
            "t": dt.datetime.now(dt.UTC),
        },
    )
    assert "fk_sdlc_run_baseline" in message or "foreign key" in message


def test_the_trace_allowlist_admits_sdlc_edges_and_nothing_else(world: P9World) -> None:
    insert = (
        "INSERT INTO traceability_link (id, project_id, from_type, from_id, link_type, to_type, "
        "to_id, anchor_version_id, origin, created_at) VALUES "
        "(:id, :p, :ft, :fid, :lt, :tt, :tid, NULL, 'raw test', :now)"
    )
    base = {"p": world.project_id, "fid": str(uuid.uuid4()), "tid": str(uuid.uuid4())}
    world.session.execute(
        text(insert),
        {
            **base,
            "id": uuid.uuid4(),
            "ft": "sdlc_run",
            "lt": "CONTAINS",
            "tt": "sdlc_factor",
            "now": dt.datetime.now(dt.UTC),
        },
    )
    message = refused(
        world.session,
        insert,
        {
            **base,
            "id": uuid.uuid4(),
            "ft": "sdlc_run",
            "lt": "SELECTED",
            "tt": "sdlc_candidate",
            "now": dt.datetime.now(dt.UTC),
        },
    )
    assert "allowed_triple" in message


def _config(url: str) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_migration_0011_downgrades_and_upgrades_on_postgresql(pg_engine_migrated: Engine) -> None:
    """Up -> down -> up on the live database (the empty migrated schema)."""
    url = postgres_url()
    assert url is not None
    config = _config(url)
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as connection:
            assert set(inspect(connection).get_table_names()) >= P9_TABLES
        command.downgrade(config, "0010_p8_trace_documents")
        with engine.connect() as connection:
            assert not P9_TABLES & set(inspect(connection).get_table_names())
            assert (
                connection.execute(
                    text("SELECT tgname FROM pg_trigger WHERE tgname LIKE 'sdlc%'")
                ).all()
                == []
            )
            assert (
                connection.execute(
                    text("SELECT 1 FROM pg_type WHERE typname = 'sdlc_run_status_enum'")
                ).all()
                == []
            )
            check = connection.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname LIKE '%traceability_link_allowed_triple'"
                )
            ).scalar_one()
            assert "sdlc_run" not in check, "the P8 allowlist is restored"
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert set(inspect(connection).get_table_names()) >= P9_TABLES
            names = {
                row[0]
                for row in connection.execute(
                    text("SELECT tgname FROM pg_trigger WHERE tgname LIKE 'sdlc%'")
                )
            }
            assert names == {
                "sdlc_run_guard",
                "sdlc_factor_append_only",
                "sdlc_candidate_append_only",
                "sdlc_rule_application_append_only",
            }
    finally:
        command.upgrade(config, "head")
        engine.dispose()
