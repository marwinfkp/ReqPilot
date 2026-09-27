"""P10 on a live PostgreSQL: the workflow end to end, and the guards of migration 0012.

The ORM guard is one layer; these tests attack the second - the database's
append-only triggers on provenance and the change log, the workflow and element
guards, the partial unique indexes (one live workflow per project, one
production-readiness gate per workflow), the idempotency key, the composite foreign
keys and the widened trace allowlist - with raw SQL that bypasses the ORM. Skips
without ``REQPILOT_TEST_DATABASE_URL``.
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
from tests.p10_helpers import P10World, make_p10_world

from reqpilot.domain.enums import ArtifactFormat, GraphRunStatus

pytestmark = [pytest.mark.integration, requires_postgres]

REPO_ROOT = Path(__file__).resolve().parents[2]
P10_TABLES = {
    "workflow",
    "workflow_phase",
    "workflow_activity",
    "workflow_gate",
    "workflow_source",
    "workflow_change",
}


@pytest.fixture
def generated(pg_session: Session) -> tuple[P10World, uuid.UUID]:
    world = make_p10_world(pg_session)
    run = world.select()
    summary = world.generate(run.id)
    assert summary.status is GraphRunStatus.COMPLETED, summary.errors
    assert summary.workflow_id is not None
    world.session.flush()
    return world, summary.workflow_id


def refused(session: Session, sql: str, params: dict | None = None) -> str:  # type: ignore[type-arg]
    savepoint = session.begin_nested()
    with pytest.raises(DBAPIError) as excinfo:
        session.execute(text(sql), params or {})
        session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    savepoint.rollback()
    return str(excinfo.value.orig)


def one(session: Session, sql: str, params: dict) -> object:  # type: ignore[type-arg]
    return session.execute(text(sql), params).scalar_one()


def test_the_whole_p10_flow_runs_on_postgresql(generated) -> None:  # type: ignore[no-untyped-def]
    world, workflow_id = generated
    edges = world.session.execute(
        text(
            "SELECT link_type, count(*) FROM traceability_link WHERE project_id = :p AND "
            "link_type IN ('REALISED_AS', 'REQUIRES_CHECKPOINT', 'REQUIRES_ACTIVITY') "
            "GROUP BY link_type"
        ),
        {"p": world.project_id},
    ).all()
    counts = dict(edges)
    assert counts["REALISED_AS"] == 1 and counts["REQUIRES_CHECKPOINT"] >= 3
    assert counts["REQUIRES_ACTIVITY"] >= 4
    again = world.generate(
        uuid.UUID(
            str(
                one(
                    world.session,
                    "SELECT sdlc_run_id FROM workflow WHERE id = :w",
                    {"w": workflow_id},
                )
            )
        )
    )
    assert again.workflow_reused and again.workflow_id == workflow_id
    export = world.workflows().export(world.project_id, workflow_id, ArtifactFormat.DOCX)
    assert export.data[:2] == b"PK"


def test_provenance_and_the_change_log_are_append_only(generated) -> None:  # type: ignore[no-untyped-def]
    world, workflow_id = generated
    session = world.session
    source = one(
        session, "SELECT id FROM workflow_source WHERE workflow_id = :w LIMIT 1", {"w": workflow_id}
    )
    assert "append-only" in refused(
        session, "UPDATE workflow_source SET relation = 'x' WHERE id = :i", {"i": source}
    )
    assert "append-only" in refused(
        session, "DELETE FROM workflow_source WHERE id = :i", {"i": source}
    )

    from reqpilot.domain.workflow.edits import UpdatePhase

    manager = world.workflows(world.manager)
    workflow = manager.get(world.project_id, workflow_id)
    assert workflow is not None
    phase = manager.view(world.project_id, workflow).phases[0]
    manager.edit(
        world.project_id,
        workflow_id,
        UpdatePhase(phase.key, {"name": "Renamed (synthetic)"}),
        reason="Wording (synthetic).",
    )
    session.flush()
    change = one(
        session, "SELECT id FROM workflow_change WHERE workflow_id = :w", {"w": workflow_id}
    )
    assert "append-only" in refused(
        session, "UPDATE workflow_change SET reason = 'rewritten' WHERE id = :i", {"i": change}
    )
    assert "append-only" in refused(
        session, "DELETE FROM workflow_change WHERE id = :i", {"i": change}
    )


def test_the_workflow_guard_holds_the_generated_workflow(generated) -> None:  # type: ignore[no-untyped-def]
    world, workflow_id = generated
    session = world.session
    params = {"w": workflow_id}
    for sql in (
        "UPDATE workflow SET generated_structure = '{}'::jsonb WHERE id = :w",
        "UPDATE workflow SET candidate_key = 'agile' WHERE id = :w",
        "UPDATE workflow SET input_fingerprint = repeat('0', 64) WHERE id = :w",
        "UPDATE workflow SET revision = 0 WHERE id = :w",
    ):
        message = refused(session, sql, params)
        assert "immutable" in message or "revision" in message, sql
    assert "never deleted" in refused(session, "DELETE FROM workflow WHERE id = :w", params)
    session.execute(text("UPDATE workflow SET status = 'SUPERSEDED' WHERE id = :w"), params)
    assert "superseded" in refused(
        session, "UPDATE workflow SET revision = revision + 1 WHERE id = :w", params
    )


def test_the_element_guards_fix_identity_kind_and_the_mandatory_flag(generated) -> None:  # type: ignore[no-untyped-def]
    world, workflow_id = generated
    session = world.session
    w = {"w": workflow_id}
    phase = one(session, "SELECT id FROM workflow_phase WHERE workflow_id = :w AND position = 1", w)
    mandatory = one(
        session, "SELECT id FROM workflow_activity WHERE workflow_id = :w AND mandatory LIMIT 1", w
    )
    template = one(
        session,
        "SELECT id FROM workflow_activity WHERE workflow_id = :w AND kind = 'template' LIMIT 1",
        w,
    )
    gate = one(session, "SELECT id FROM workflow_gate WHERE workflow_id = :w LIMIT 1", w)
    assert "fixed" in refused(
        session, "UPDATE workflow_phase SET key = 'x' WHERE id = :i", {"i": phase}
    )
    assert "fixed" in refused(
        session,
        "UPDATE workflow_phase SET stages = '[\"release\"]'::jsonb WHERE id = :i",
        {"i": phase},
    )
    assert "never deleted" in refused(
        session, "DELETE FROM workflow_phase WHERE id = :i", {"i": phase}
    )
    assert "fixed" in refused(
        session, "UPDATE workflow_activity SET mandatory = false WHERE id = :i", {"i": mandatory}
    )
    assert "fixed" in refused(
        session, "UPDATE workflow_activity SET kind = 'manual' WHERE id = :i", {"i": mandatory}
    )
    assert "never deleted" in refused(
        session, "DELETE FROM workflow_activity WHERE id = :i", {"i": mandatory}
    )
    assert "never deleted" in refused(
        session, "DELETE FROM workflow_gate WHERE id = :i", {"i": gate}
    )
    # A template activity is not mandatory; the edit path may remove it.
    session.execute(text("DELETE FROM workflow_activity WHERE id = :i"), {"i": template})
    # Wording may change.
    session.execute(text("UPDATE workflow_gate SET name = 'Reworded' WHERE id = :i"), {"i": gate})


def test_one_live_workflow_and_one_readiness_gate_and_one_workflow_per_inputs(generated) -> None:  # type: ignore[no-untyped-def]
    world, workflow_id = generated
    session = world.session
    copy = (
        "INSERT INTO workflow (id, project_id, sdlc_run_id, selected_candidate_id, candidate_key, "
        "baseline_id, graph_run_id, supersedes_workflow_id, status, template_ref, "
        "templates_sha256, "
        "input_fingerprint, generated_structure, generated_hash, revision, content_hash, "
        "open_items, "
        "generated_by, created_at, updated_at) SELECT :new, project_id, sdlc_run_id, "
        "selected_candidate_id, candidate_key, baseline_id, NULL, NULL, :status, template_ref, "
        "templates_sha256, :fp, generated_structure, generated_hash, 1, content_hash, open_items, "
        "generated_by, now(), now() FROM workflow WHERE id = :w"
    )
    fingerprint = one(
        session, "SELECT input_fingerprint FROM workflow WHERE id = :w", {"w": workflow_id}
    )
    assert "uq_workflow_one_live_per_project" in refused(
        session, copy, {"new": uuid.uuid4(), "w": workflow_id, "status": "COMPLETE", "fp": "f" * 64}
    )
    assert "uq_workflow_run_inputs" in refused(
        session,
        copy,
        {"new": uuid.uuid4(), "w": workflow_id, "status": "SUPERSEDED", "fp": fingerprint},
    )
    duplicate_gate = (
        "INSERT INTO workflow_gate (id, project_id, workflow_id, phase_id, position, key, kind, "
        "name, purpose, approver_roles, required_evidence, entry_criteria, exit_criteria, "
        "mandatory, origin, created_at, updated_at) SELECT :new, project_id, workflow_id, "
        "phase_id, 99, 'second_readiness', kind, name, purpose, approver_roles, required_evidence, "
        "entry_criteria, exit_criteria, mandatory, origin, now(), now() FROM workflow_gate "
        "WHERE workflow_id = :w AND kind = 'production_readiness'"
    )
    assert "uq_workflow_gate_one_production_readiness" in refused(
        session, duplicate_gate, {"new": uuid.uuid4(), "w": workflow_id}
    )


def test_a_workflow_cannot_point_at_another_projects_run(generated, pg_session: Session) -> None:  # type: ignore[no-untyped-def]
    _world, workflow_id = generated
    from tests.workflow.test_p1_exit_test import make_project

    other = make_project(pg_session, "Another lender (synthetic)")
    pg_session.flush()
    message = refused(
        pg_session,
        "INSERT INTO workflow (id, project_id, sdlc_run_id, selected_candidate_id, candidate_key, "
        "baseline_id, status, template_ref, templates_sha256, input_fingerprint, "
        "generated_structure, generated_hash, revision, content_hash, open_items, generated_by, "
        "created_at, updated_at) SELECT :new, :other, sdlc_run_id, selected_candidate_id, "
        "candidate_key, baseline_id, 'COMPLETE', template_ref, templates_sha256, repeat('e', 64), "
        "generated_structure, generated_hash, 1, content_hash, open_items, generated_by, now(), "
        "now() FROM workflow WHERE id = :w",
        {"new": uuid.uuid4(), "other": other.id, "w": workflow_id},
    )
    assert "fk_workflow" in message


def test_the_trace_allowlist_admits_workflow_edges_and_nothing_else(generated) -> None:  # type: ignore[no-untyped-def]
    world, _workflow_id = generated
    insert = (
        "INSERT INTO traceability_link (id, project_id, from_type, from_id, link_type, to_type, "
        "to_id, anchor_version_id, origin, created_at) VALUES "
        "(:id, :p, :ft, :fid, :lt, :tt, :tid, NULL, 'raw test', :now)"
    )
    base = {"p": world.project_id, "fid": str(uuid.uuid4()), "tid": str(uuid.uuid4())}
    world.session.execute(
        text(insert),
        {**base, "id": uuid.uuid4(), "ft": "risk_mitigation", "lt": "REQUIRES_ACTIVITY",
         "tt": "workflow_activity", "now": dt.datetime.now(dt.UTC)},
    )  # fmt: skip
    message = refused(
        world.session,
        insert,
        {**base, "id": uuid.uuid4(), "ft": "workflow_gate", "lt": "REQUIRES_CHECKPOINT",
         "tt": "compliance_mapping", "now": dt.datetime.now(dt.UTC)},
    )  # fmt: skip
    assert "allowed_triple" in message


def _config(url: str) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_migration_0012_downgrades_and_upgrades_on_postgresql(pg_engine_migrated: Engine) -> None:
    """Up -> down -> up on the live database: tables, triggers, enum and the exact allowlist."""
    url = postgres_url()
    assert url is not None
    config = _config(url)
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as connection:
            assert set(inspect(connection).get_table_names()) >= P10_TABLES
        command.downgrade(config, "0011_p9_sdlc_recommendation")
        with engine.connect() as connection:
            assert not P10_TABLES & set(inspect(connection).get_table_names())
            assert (
                connection.execute(
                    text("SELECT tgname FROM pg_trigger WHERE tgname LIKE 'workflow%'")
                ).all()
                == []
            )
            assert (
                connection.execute(
                    text("SELECT 1 FROM pg_type WHERE typname = 'workflow_status_enum'")
                ).all()
                == []
            )
            check = connection.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname LIKE '%traceability_link_allowed_triple'"
                )
            ).scalar_one()
            assert "workflow" not in check and "sdlc_candidate" in check, "the P9 allowlist"
        command.upgrade(config, "head")
        with engine.connect() as connection:
            names = {
                row[0]
                for row in connection.execute(
                    text("SELECT tgname FROM pg_trigger WHERE tgname LIKE 'workflow%'")
                )
            }
            assert names == {
                "workflow_guard",
                "workflow_phase_guard",
                "workflow_phase_shape_guard",
                "workflow_activity_guard",
                "workflow_activity_derived_guard",
                "workflow_gate_guard",
                "workflow_gate_derived_guard",
                "workflow_source_append_only",
                "workflow_change_append_only",
            }
    finally:
        command.upgrade(config, "head")
        engine.dispose()
