"""P5 on a live PostgreSQL: the quality run and the triggers of migration 0007.

The ORM guards are one layer; these tests attack the second - the database's own
triggers and constraints - with raw SQL that bypasses the ORM entirely. Skips
without ``REQPILOT_TEST_DATABASE_URL``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from tests.conftest import requires_postgres
from tests.p5_helpers import make_world

from reqpilot.domain.enums import ConflictResolution, ConflictStatus
from reqpilot.domain.models.elicitation import QualityFinding
from reqpilot.domain.models.quality import Conflict
from reqpilot.services.audit import AuditService
from reqpilot.services.quality import ConflictService, FindingReviewService, GlossaryService

pytestmark = [pytest.mark.integration, requires_postgres]


@pytest.fixture
def world(pg_session: Session):
    world = make_world(pg_session)
    world.analyse()
    pg_session.flush()
    return world


def refused(session: Session, sql: str, params: dict | None = None) -> str:
    savepoint = session.begin_nested()
    with pytest.raises(DBAPIError) as excinfo:
        session.execute(text(sql), params or {})
    savepoint.rollback()
    return str(excinfo.value)


def test_the_quality_run_completes_on_postgresql(world) -> None:
    conflicts = list(world.session.scalars(select(Conflict)))
    assert conflicts and all(c.project_id == world.project_id for c in conflicts)
    assert list(world.session.scalars(select(QualityFinding)))
    assert AuditService(world.session).verify_project_chain(world.project_id) == (True, None)


def test_a_conflicts_content_is_immutable_and_it_is_never_deleted(world) -> None:
    conflict = world.session.scalars(select(Conflict)).first()
    params = {"i": conflict.id}
    assert "immutable" in refused(
        world.session, "UPDATE conflict SET rationale = 'x' WHERE id = :i", params
    )
    assert "immutable" in refused(
        world.session, "UPDATE conflict SET version_b_id = version_a_id WHERE id = :i", params
    )
    assert "never deleted" in refused(world.session, "DELETE FROM conflict WHERE id = :i", params)


def test_a_closed_conflict_never_reopens(world) -> None:
    conflict = world.session.scalars(select(Conflict)).first()
    ConflictService(world.session, world.analyst).resolve(
        world.project_id,
        conflict.id,
        resolution=ConflictResolution.CHOOSE_A,
        reason="ok (synthetic)",
    )
    world.session.flush()
    assert "closed" in refused(
        world.session, "UPDATE conflict SET status = 'OPEN' WHERE id = :i", {"i": conflict.id}
    )


def test_a_findings_detection_provenance_is_immutable_and_it_closes_once(world) -> None:
    finding = world.session.scalars(select(QualityFinding)).first()
    params = {"i": finding.id}
    assert "immutable" in refused(
        world.session, "UPDATE quality_finding SET rule_id = 'forged' WHERE id = :i", params
    )
    assert "immutable" in refused(
        world.session, "UPDATE quality_finding SET evidence = '[]'::jsonb WHERE id = :i", params
    )
    FindingReviewService(world.session, world.analyst).dismiss(world.project_id, finding.id, "no")
    world.session.flush()
    assert "once" in refused(
        world.session, "UPDATE quality_finding SET status = 'OPEN' WHERE id = :i", params
    )
    assert "closed_has_reason" in refused(
        world.session,
        "UPDATE quality_finding SET status = 'RESOLVED', resolution_reason = NULL "
        "WHERE id = (SELECT id FROM quality_finding WHERE status = 'OPEN' LIMIT 1)",
    )


def test_glossary_terms_are_append_only(world) -> None:
    term = GlossaryService(world.session, world.analyst).add(
        world.project_id, term="KYC", definition="Know your customer (synthetic)."
    )
    world.session.flush()
    refused(
        world.session, "UPDATE glossary_term SET definition = 'x' WHERE id = :i", {"i": term.id}
    )
    refused(world.session, "DELETE FROM glossary_term WHERE id = :i", {"i": term.id})


def test_a_conflict_cannot_reference_another_projects_version(world, pg_session: Session) -> None:
    other = make_world(pg_session, "Other bank (synthetic)")
    pg_session.flush()
    savepoint = pg_session.begin_nested()
    with pytest.raises(IntegrityError):
        pg_session.execute(
            text(
                "INSERT INTO conflict (id, project_id, version_a_id, version_b_id, "
                " conflict_class, kind, rationale, evidence_a, evidence_b, severity, "
                " involves_stakeholder_disagreement, detected_by, recorded_by, status, "
                " created_at, updated_at) VALUES (gen_random_uuid(), :p, :a, :b, 'DEFINITE', "
                " 'OTHER', 'x', 'x', 'y', 'HIGH', false, 'RULE', :r, 'OPEN', now(), now())"
            ),
            {
                "p": world.project_id,
                "a": world.versions["Q01"].id,
                "b": other.versions["Q02"].id,
                "r": world.analyst.actor_id,
            },
        )
    savepoint.rollback()


def test_one_active_conflict_per_pair(world) -> None:
    conflict = world.session.scalars(
        select(Conflict).where(Conflict.status == ConflictStatus.OPEN)
    ).first()
    columns = [
        c
        for c in world.session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'conflict' ORDER BY ordinal_position"
            )
        ).scalars()
        if c != "id"
    ]
    listed = ", ".join(columns)
    savepoint = world.session.begin_nested()
    with pytest.raises(IntegrityError, match="uq_conflict_active_pair"):
        world.session.execute(
            text(
                f"INSERT INTO conflict (id, {listed}) "
                f"SELECT gen_random_uuid(), {listed} FROM conflict WHERE id = :i"
            ),
            {"i": conflict.id},
        )
    savepoint.rollback()
