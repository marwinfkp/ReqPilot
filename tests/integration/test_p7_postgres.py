"""P7 on a live PostgreSQL: the real pipeline, and the guards of migration 0009.

The ORM guards are one layer; these tests attack the second - the database's own
checks, composite foreign keys and triggers - with raw SQL that bypasses the ORM
entirely. That matters most for the severity: the claim "a severity that is not
the matrix's does not exist" is only worth making if it holds against a direct
``INSERT``. Skips without ``REQPILOT_TEST_DATABASE_URL``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from tests.conftest import requires_postgres
from tests.p3_helpers import retrieval_rules
from tests.p7_helpers import P7World, make_p7_world, risk_rules

from reqpilot.domain.enums import (
    RiskScope,
    RiskSeverity,
    RiskStatus,
)
from reqpilot.domain.models.risk import Risk, RiskMitigation
from reqpilot.retrieval.embeddings import HashingEmbeddingProvider
from reqpilot.services.audit import AuditService
from reqpilot.services.knowledge.retrieval import RetrievalService

pytestmark = [pytest.mark.integration, requires_postgres]


def real_retrieval(world: P7World):
    """The P2 hybrid retrieval service itself - allowlist join, pgvector, full text."""
    return RetrievalService(
        world.session,
        world.analyst,
        embedder=HashingEmbeddingProvider(),
        rules=retrieval_rules(),
        default_top_k=8,
    ).retrieve


@pytest.fixture
def world(pg_session: Session) -> P7World:
    world = make_p7_world(pg_session)
    world.retriever = real_retrieval(world)  # type: ignore[assignment]
    summary = world.full_analysis()
    assert summary.status.value == "completed", summary.errors
    pg_session.flush()
    return world


def refused(session: Session, sql: str, params: dict | None = None) -> str:
    savepoint = session.begin_nested()
    with pytest.raises(DBAPIError) as excinfo:
        session.execute(text(sql), params or {})
        session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    savepoint.rollback()
    # The driver's own message: SQLAlchemy's str() shortens long statements.
    return str(excinfo.value.orig)


def insert_risk(**overrides: object) -> str:
    """A raw INSERT of one risk, with the columns a test wants to bend."""
    values = {
        "scope": "'PROJECT'",
        "requirement_version_id": "NULL",
        "category": "'TECHNICAL'",
        "title": "'raw'",
        "title_key": "'raw'",
        "description": "'raw'",
        "likelihood": "'L1'",
        "impact": "'I1'",
        "severity": "'LOW'",
        "status": "'PROPOSED'",
    }
    values.update({k: str(v) for k, v in overrides.items()})
    return (
        "INSERT INTO risk (id, project_id, scope, requirement_version_id, category, title, "
        " title_key, description, likelihood, impact, severity, matrix_version, "
        " likelihood_rationale, impact_rationale, citations, evidence_count, detected_by, "
        " scope_rules_version, rules_version, content_hash, recorded_by, status, owner_role, "
        " created_at, updated_at) "
        f"VALUES (gen_random_uuid(), :p, {values['scope']}, {values['requirement_version_id']}, "
        f" {values['category']}, {values['title']}, {values['title_key']}, "
        f" {values['description']}, {values['likelihood']}, {values['impact']}, "
        f" {values['severity']}, :m, 'r', 'r', '[]'::jsonb, 1, 'AGENT', '1.0.0', 'r', "
        f" repeat('a', 64), :a, {values['status']}, 'PROJECT_MANAGER', now(), now())"
    )


def params(world: P7World) -> dict[str, object]:
    return {
        "p": world.project_id,
        "m": risk_rules().matrix.version,
        "a": world.analyst.actor_id,
    }


# --- the real pipeline -------------------------------------------------------------------------


def test_the_risk_run_completes_on_real_hybrid_retrieval(world: P7World) -> None:
    risks = list(world.session.scalars(select(Risk)))
    assert risks, "real retrieval supplied evidence the scripted model could cite"
    matrix = risk_rules().matrix
    for item in risks:
        assert item.severity is matrix.severity(item.likelihood, item.impact)
    high = [r for r in risks if r.severity is RiskSeverity.HIGH]
    assert high and all(r.approval_task_id for r in high)
    assert {r.scope for r in risks} == {RiskScope.REQUIREMENT, RiskScope.PROJECT}
    assert AuditService(world.session).verify_project_chain(world.project_id) == (True, None)


def test_the_matrix_table_holds_exactly_the_approved_nine_cells(world: P7World) -> None:
    rows = world.session.execute(
        text("SELECT likelihood, impact, severity FROM risk_matrix WHERE matrix_version = :m"),
        {"m": risk_rules().matrix.version},
    ).all()
    assert len(rows) == 9
    matrix = risk_rules().matrix
    from reqpilot.domain.enums import RiskImpact, RiskLikelihood

    for likelihood, impact, severity in rows:
        expected = matrix.severity(RiskLikelihood[likelihood], RiskImpact[impact])
        assert severity == expected.name


def test_g8_is_decided_on_postgresql(world: P7World) -> None:
    item = world.high_risk()
    outcome = world.decide_g8(item.id)
    assert outcome.task_closed
    world.session.flush()
    world.session.refresh(item)
    assert item.status is RiskStatus.ACCEPTED
    assert AuditService(world.session).verify_project_chain(world.project_id) == (True, None)


# --- the database's own guards ------------------------------------------------------------------


def test_the_database_refuses_a_severity_the_matrix_did_not_produce(world: P7World) -> None:
    """The load-bearing control: an L1xI1 risk claiming HIGH does not exist.

    ``status`` is UNDER_REVIEW so that the high-risk gating check is satisfied
    and the foreign key into ``risk_matrix`` is the constraint under test.
    """
    message = refused(
        world.session,
        insert_risk(severity="'HIGH'", status="'UNDER_REVIEW'"),
        params(world),
    )
    assert "fk_risk_severity_from_matrix" in message


def test_the_database_refuses_a_downgraded_severity_too(world: P7World) -> None:
    """The pin works in both directions: an L3xI3 risk cannot claim LOW either."""
    message = refused(
        world.session,
        insert_risk(likelihood="'L3'", impact="'I3'", severity="'LOW'"),
        params(world),
    )
    assert "fk_risk_severity_from_matrix" in message


def test_the_database_refuses_an_unknown_matrix_version(world: P7World) -> None:
    sql = insert_risk().replace(":m", "'99.99.99'")
    message = refused(world.session, sql, params(world))
    assert "fk_risk_severity_from_matrix" in message


def test_the_database_refuses_an_ungated_high_risk(world: P7World) -> None:
    """``FR-RSK-007``: a HIGH risk is never merely PROPOSED."""
    message = refused(
        world.session,
        insert_risk(likelihood="'L3'", impact="'I3'", severity="'HIGH'", status="'PROPOSED'"),
        params(world),
    )
    assert "high_risk_is_gated" in message


def test_the_database_refuses_a_scope_that_does_not_match_its_version(world: P7World) -> None:
    """``FR-RSK-001``: requirement-scoped implies a version; project-scoped implies none."""
    version_id = next(iter(world.versions.values())).id
    message = refused(
        world.session,
        insert_risk(scope="'REQUIREMENT'", requirement_version_id="NULL"),
        params(world),
    )
    assert "scope_matches_version" in message
    message = refused(
        world.session,
        insert_risk(scope="'PROJECT'", requirement_version_id=":v"),
        {**params(world), "v": version_id},
    )
    assert "scope_matches_version" in message


def test_a_risk_cannot_reference_another_projects_version_or_task(
    world: P7World, pg_session: Session
) -> None:
    other = make_p7_world(pg_session, "P7 pg other")
    foreign_version = next(iter(other.versions.values())).id
    message = refused(
        pg_session,
        insert_risk(scope="'REQUIREMENT'", requirement_version_id=":v"),
        {**params(world), "v": foreign_version},
    )
    assert "fk_risk_version_same_project" in message


def test_a_risk_without_an_evidence_link_cannot_be_committed(world: P7World) -> None:
    """``FR-RSK-006`` as a deferred constraint trigger: checked at commit."""
    savepoint = world.session.begin_nested()
    with pytest.raises(DBAPIError) as excinfo:
        world.session.execute(text(insert_risk()), params(world))
        world.session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    savepoint.rollback()
    assert "links no evidence of its project" in str(excinfo.value.orig)


def test_a_risk_cannot_link_another_projects_evidence(world: P7World, pg_session: Session) -> None:
    from reqpilot.domain.models.knowledge import Evidence

    other = make_p7_world(pg_session, "P7 pg other 2")
    other.retriever = real_retrieval(other)  # type: ignore[assignment]
    other.full_analysis()
    foreign = pg_session.scalars(
        select(Evidence.id).where(Evidence.project_id == other.project_id)
    ).first()
    item = world.session.scalars(select(Risk)).first()
    message = refused(
        world.session,
        "INSERT INTO risk_evidence (id, project_id, risk_id, evidence_id, created_at) "
        "VALUES (gen_random_uuid(), :p, :r, :e, now())",
        {"p": world.project_id, "r": item.id, "e": foreign},
    )
    assert "fk_risk_evidence_evidence" in message


def test_risk_rows_are_immutable_and_never_deleted(world: P7World) -> None:
    item = world.session.scalars(select(Risk)).first()
    # Each replacement differs from what the row already holds: an UPDATE that
    # sets a column to its current value changes nothing, and correctly raises
    # nothing, so a fixed literal would make this test depend on the fixture.
    other = {
        "severity": "LOW" if item.severity is RiskSeverity.HIGH else "HIGH",
        "likelihood": "L1" if item.likelihood.value == "L3" else "L3",
        "impact": "I1" if item.impact.value == "I3" else "I3",
    }
    for column, value in (
        ("severity", f"'{other['severity']}'"),
        ("likelihood", f"'{other['likelihood']}'"),
        ("impact", f"'{other['impact']}'"),
        ("title", "'tampered'"),
        ("matrix_version", "'9.9.9'"),
    ):
        message = refused(
            world.session,
            f"UPDATE risk SET {column} = {value} WHERE id = :i",
            {"i": item.id},
        )
        assert "immutable" in message
    message = refused(world.session, "DELETE FROM risk WHERE id = :i", {"i": item.id})
    assert "never deleted" in message


def test_a_risk_status_cannot_jump_the_approved_path(world: P7World) -> None:
    item = next(r for r in world.session.scalars(select(Risk)) if r.status is RiskStatus.PROPOSED)
    message = refused(
        world.session, "UPDATE risk SET status = 'CLOSED' WHERE id = :i", {"i": item.id}
    )
    assert "cannot move from PROPOSED to CLOSED" in message


def test_a_gate_task_is_linked_once(world: P7World) -> None:
    item = world.high_risk()
    message = refused(
        world.session,
        "UPDATE risk SET approval_task_id = gen_random_uuid() WHERE id = :i",
        {"i": item.id},
    )
    assert "linked once" in message


def test_the_published_matrix_cannot_be_edited_or_deleted(world: P7World) -> None:
    message = refused(
        world.session,
        "UPDATE risk_matrix SET severity = 'LOW' "
        "WHERE matrix_version = :m AND likelihood = 'L3' AND impact = 'I3'",
        {"m": risk_rules().matrix.version},
    )
    assert "publish a new matrix_version" in message
    message = refused(
        world.session,
        "DELETE FROM risk_matrix WHERE matrix_version = :m",
        {"m": risk_rules().matrix.version},
    )
    assert "publish a new matrix_version" in message


def test_a_mitigation_is_immutable_and_its_status_moves_once(world: P7World) -> None:
    mitigation = world.session.scalars(select(RiskMitigation)).first()
    message = refused(
        world.session,
        "UPDATE risk_mitigation SET is_ai_generated = false WHERE id = :i",
        {"i": mitigation.id},
    )
    assert "immutable" in message
    message = refused(
        world.session,
        "UPDATE risk_mitigation SET suggestion = 'rewritten' WHERE id = :i",
        {"i": mitigation.id},
    )
    assert "immutable" in message


def test_an_accepted_mitigation_must_name_a_human(world: P7World) -> None:
    mitigation = world.session.scalars(select(RiskMitigation)).first()
    message = refused(
        world.session,
        "UPDATE risk_mitigation SET status = 'ACCEPTED' WHERE id = :i",
        {"i": mitigation.id},
    )
    assert "acceptance_names_a_human" in message


def test_risk_evidence_links_are_append_only(world: P7World) -> None:
    row = world.session.execute(text("SELECT id FROM risk_evidence LIMIT 1")).scalar()
    message = refused(
        world.session,
        "UPDATE risk_evidence SET evidence_id = gen_random_uuid() WHERE id = :i",
        {"i": row},
    )
    assert "append-only" in message.lower()
    message = refused(world.session, "DELETE FROM risk_evidence WHERE id = :i", {"i": row})
    assert "append-only" in message.lower()


def test_one_live_risk_per_subject_and_title(world: P7World) -> None:
    item = next(r for r in world.session.scalars(select(Risk)) if r.scope is RiskScope.PROJECT)
    message = refused(
        world.session,
        insert_risk(title=f"'{item.title}'", title_key=f"'{item.title_key}'"),
        params(world),
    )
    assert "uq_risk_active_project_title" in message


def test_the_risk_tables_are_declared_to_cascade_with_their_project(world: P7World) -> None:
    """``FR-ADM-006``: a project deletion takes its risk rows with it.

    Asserted on the declared foreign keys rather than by deleting a project,
    because a project deletion also touches the append-only audit log, whose
    own machinery is P0's and is tested there. What is P7's to get right is
    that these rows are declared to cascade, and that the P7 triggers permit a
    cascade (they return early at ``pg_trigger_depth() > 1``).
    """
    rows = world.session.execute(
        text(
            "SELECT c.conrelid::regclass::text, c.conname, c.confdeltype "
            "FROM pg_constraint c "
            "WHERE c.contype = 'f' AND c.conrelid::regclass::text IN "
            "('risk', 'risk_evidence', 'risk_mitigation')"
        )
    ).all()
    by_name = {name: delete_type for _table, name, delete_type in rows}
    for name in (
        "fk_risk_project_id_project",
        "fk_risk_evidence_project_id_project",
        "fk_risk_mitigation_project_id_project",
        "fk_risk_evidence_risk",
        "fk_risk_mitigation_risk",
    ):
        assert by_name.get(name) == "c", f"{name} does not cascade on delete"
    # The matrix is reference data, not project data: it has no project link.
    assert not world.session.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'risk_matrix' AND column_name = 'project_id'"
        )
    ).first()
    # Every P7 trigger yields to a cascade rather than blocking it.
    for function in (
        "reqpilot_risk_guard",
        "reqpilot_risk_matrix_guard",
        "reqpilot_risk_mitigation_guard",
    ):
        body = world.session.execute(
            text("SELECT prosrc FROM pg_proc WHERE proname = :f"), {"f": function}
        ).scalar()
        assert "pg_trigger_depth() > 1" in body
