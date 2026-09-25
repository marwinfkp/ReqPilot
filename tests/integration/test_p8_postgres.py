"""P8 on a live PostgreSQL: the governed flow end to end, and the guards of migration 0010.

The ORM guards are one layer; these tests attack the second - the database's own
allowlist check, composite foreign keys and triggers - with raw SQL that bypasses
the ORM. Skips without ``REQPILOT_TEST_DATABASE_URL``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from tests.conftest import postgres_url, requires_postgres
from tests.p8_helpers import P8World, make_p8_world

from reqpilot.artifacts.docx import docx_text
from reqpilot.domain.enums import ArtifactFormat, ArtifactType
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.artifacts import Artifact, ArtifactSection, ArtifactVersion
from reqpilot.domain.models.traceability import TraceabilityLink
from reqpilot.services.audit import AuditService
from reqpilot.services.documents import ArtifactService
from reqpilot.services.traceability import TraceGraphSync

pytestmark = [pytest.mark.integration, requires_postgres]

REPO_ROOT = Path(__file__).resolve().parents[2]
P8_TABLES = {"traceability_link", "artifact", "artifact_version", "artifact_section"}


@pytest.fixture
def world(pg_session: Session) -> P8World:
    return make_p8_world(pg_session)


@pytest.fixture
def generated(world: P8World) -> tuple[P8World, uuid.UUID, ArtifactVersion]:
    baseline = world.govern_and_baseline(["L01", "L03", "L08"], "B1")
    srs = ArtifactService(world.session, world.analyst).generate(
        world.project_id, baseline, ArtifactType.SRS
    )
    assert not srs.refused, srs.blockers
    world.session.flush()
    return world, baseline, srs.version


def refused(session: Session, sql: str, params: dict | None = None) -> str:
    savepoint = session.begin_nested()
    with pytest.raises(DBAPIError) as excinfo:
        session.execute(text(sql), params or {})
        session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    savepoint.rollback()
    return str(excinfo.value.orig)


def link_insert(project_id: uuid.UUID, **overrides: object) -> tuple[str, dict[str, object]]:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "p": project_id,
        "ft": "risk",
        "fid": str(uuid.uuid4()),
        "lt": "MITIGATED_BY",
        "tt": "risk_mitigation",
        "tid": str(uuid.uuid4()),
        "a": None,
        "now": dt.datetime.now(dt.UTC),
    }
    values.update(overrides)
    return (
        "INSERT INTO traceability_link (id, project_id, from_type, from_id, link_type, to_type, "
        "to_id, anchor_version_id, origin, created_at) VALUES "
        "(:id, :p, :ft, :fid, :lt, :tt, :tid, :a, 'raw test', :now)",
        values,
    )


# --- the governed flow on PostgreSQL ------------------------------------------------


def test_the_whole_p8_flow_runs_on_postgresql(generated) -> None:
    world, baseline, srs = generated
    service = ArtifactService(world.session, world.analyst)
    for key in ("L01", "L03", "L08"):
        assert world.version(key).state is RequirementState.BASELINED
    outcomes = service.generate_set(world.project_id, baseline)
    assert all(not o.refused for o in outcomes), [(o.artifact_type, o.blockers) for o in outcomes]
    assert {o.artifact_type for o in outcomes} == set(ArtifactType)
    assert next(o for o in outcomes if o.artifact_type is ArtifactType.SRS).reused
    docx = service.export(world.project_id, srs.id, ArtifactFormat.DOCX)
    assert world.human_id("L03") in docx_text(docx.data)
    assert service.export(world.project_id, srs.id, ArtifactFormat.DOCX).data == docx.data
    service.verify(srs)
    coverage = service.coverage(world.project_id, baseline)
    assert coverage.total == 3 and coverage.e6 is not None
    assert TraceGraphSync(world.session, world.analyst).sync(world.project_id).created == 0
    assert AuditService(world.session).verify_project_chain(world.project_id) == (True, None)


# --- migration 0010's database-level guards ----------------------------------------


def test_the_database_refuses_an_untyped_or_unlisted_trace_link(world: P8World) -> None:
    sql, values = link_insert(world.project_id)
    world.session.execute(text(sql), values)  # an allowlisted triple is accepted
    for overrides in (
        {"lt": "APPROVED"},  # not a link type
        {"ft": "requirement_version", "lt": "MITIGATED_BY"},  # wrong endpoints
        {"ft": "artifact_section", "lt": "CITES", "tt": "stakeholder"},  # P8 cites less
    ):
        sql, values = link_insert(world.project_id, **overrides)
        assert "ck_traceability_link_allowed_triple" in refused(world.session, sql, values)
    sql, values = link_insert(world.project_id, fid="")
    assert "ck_traceability_link_ends_present" in refused(world.session, sql, values)


def test_trace_links_are_append_only(generated) -> None:
    world, _baseline, _srs = generated
    link = world.session.scalars(
        select(TraceabilityLink).where(TraceabilityLink.project_id == world.project_id)
    ).first()
    assert link is not None
    for sql in (
        "UPDATE traceability_link SET to_id = 'forged' WHERE id = :id",
        "DELETE FROM traceability_link WHERE id = :id",
    ):
        assert "append-only" in refused(world.session, sql, {"id": link.id})


def test_a_trace_link_cannot_anchor_to_another_projects_version(
    world: P8World, pg_session: Session
) -> None:
    other = make_p8_world(pg_session, "Another lender (synthetic)")
    foreign = next(iter(other.versions.values())).id
    sql, values = link_insert(world.project_id, a=foreign)
    assert "fk_traceability_link_anchor_version" in refused(world.session, sql, values)


def test_artefact_versions_and_sections_are_immutable(generated) -> None:
    world, _baseline, srs = generated
    section = world.session.scalars(
        select(ArtifactSection).where(ArtifactSection.artifact_version_id == srs.id)
    ).first()
    assert section is not None
    for sql, ident in (
        ("UPDATE artifact_version SET markdown = 'rewritten' WHERE id = :id", srs.id),
        ("UPDATE artifact_version SET content_hash = repeat('0', 64) WHERE id = :id", srs.id),
        ("DELETE FROM artifact_version WHERE id = :id", srs.id),
        ("UPDATE artifact_section SET title = 'forged' WHERE id = :id", section.id),
        ("DELETE FROM artifact_section WHERE id = :id", section.id),
    ):
        assert "append-only" in refused(world.session, sql, {"id": ident}), sql


def test_an_artefact_is_never_deleted_and_points_only_at_its_own_versions(generated) -> None:
    world, baseline, srs = generated
    rtm = ArtifactService(world.session, world.analyst).generate(
        world.project_id, baseline, ArtifactType.RTM
    )
    world.session.flush()
    artifact_id = srs.artifact_id
    assert "never deleted" in refused(
        world.session, "DELETE FROM artifact WHERE id = :id", {"id": artifact_id}
    )
    assert "identity is immutable" in refused(
        world.session, "UPDATE artifact SET title = 'forged' WHERE id = :id", {"id": artifact_id}
    )
    assert "one of its own versions" in refused(
        world.session,
        "UPDATE artifact SET current_version_id = :v WHERE id = :id",
        {"id": artifact_id, "v": rtm.version.id},
    )
    assert world.session.get(Artifact, artifact_id).current_version_id == srs.id


def test_an_artefact_version_cannot_bind_another_projects_baseline(
    generated, pg_session: Session
) -> None:
    world, _baseline, srs = generated
    other = make_p8_world(pg_session, "Another lender (synthetic)")
    foreign_baseline = other.govern_and_baseline(["L03"], "B1")
    pg_session.flush()
    sql = (
        "INSERT INTO artifact_version (id, artifact_id, project_id, version_no, artifact_type, "
        "baseline_id, template_id, template_version, generator, model_identifier, "
        "kb_version_source, content_hash, input_fingerprint, structure, markdown, "
        "markdown_sha256, section_count, cited_version_count, generated_by, generated_at) "
        "SELECT :id, artifact_id, project_id, 99, artifact_type, :b, template_id, "
        "template_version, generator, model_identifier, kb_version_source, content_hash, "
        "input_fingerprint, structure, markdown, markdown_sha256, section_count, "
        "cited_version_count, generated_by, generated_at FROM artifact_version WHERE id = :src"
    )
    message = refused(
        world.session, sql, {"id": uuid.uuid4(), "b": foreign_baseline, "src": srs.id}
    )
    assert "fk_artifact_version_baseline" in message
    message = refused(
        world.session,
        sql.replace(":b", "baseline_id").replace("project_id, 99", ":p, 99"),
        {"id": uuid.uuid4(), "p": other.project_id, "src": srs.id},
    )
    assert "fk_artifact_version_artifact" in message or "fk_artifact_version_baseline" in message


def test_the_new_rows_cascade_with_their_project_only_through_the_project(generated) -> None:
    """Declared ``ON DELETE CASCADE`` from project; nothing else removes history."""
    world, _baseline, _srs = generated
    bind = world.session.connection()
    for table in P8_TABLES:
        fks = inspect(bind).get_foreign_keys(table)
        project_fk = [fk for fk in fks if fk["referred_table"] == "project"]
        assert project_fk and project_fk[0]["options"].get("ondelete") == "CASCADE", table
    assert world.session.scalar(select(ArtifactVersion.id).limit(1)) is not None


# --- migration round trip ---------------------------------------------------------------


def _config(url: str) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_migration_0010_downgrades_and_upgrades_on_postgresql(pg_engine_migrated: Engine) -> None:
    """Up -> down -> up on the live database. Runs on the (empty) migrated schema:
    every other PostgreSQL test rolls its transaction back, so nothing is lost."""
    url = postgres_url()
    assert url is not None
    config = _config(url)
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as connection:
            assert set(inspect(connection).get_table_names()) >= P8_TABLES
        command.downgrade(config, "0009_p7_risk_register")
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert not P8_TABLES & set(inspector.get_table_names())
            assert "assignee_user_id" not in {
                c["name"] for c in inspector.get_columns("approval_task")
            }
            triggers = connection.execute(
                text("SELECT tgname FROM pg_trigger WHERE tgname LIKE 'artifact%'")
            ).all()
            assert triggers == []
            enum = connection.execute(
                text("SELECT 1 FROM pg_type WHERE typname = 'artifact_type_enum'")
            ).all()
            assert enum == []
        command.upgrade(config, "head")
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert set(inspector.get_table_names()) >= P8_TABLES
            assert "assignee_user_id" in {c["name"] for c in inspector.get_columns("approval_task")}
            names = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT tgname FROM pg_trigger WHERE tgname IN ('artifact_guard', "
                        "'artifact_version_append_only', 'artifact_section_append_only', "
                        "'traceability_link_append_only')"
                    )
                )
            }
            assert len(names) == 4
    finally:
        command.upgrade(config, "head")
        engine.dispose()
