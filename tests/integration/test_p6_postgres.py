"""P6 on a live PostgreSQL: the real hybrid retrieval, and the guards of migration 0008.

The ORM guards are one layer; these tests attack the second - the database's own
checks, composite foreign keys and triggers - with raw SQL that bypasses the ORM
entirely. Skips without ``REQPILOT_TEST_DATABASE_URL``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from tests.conftest import requires_postgres
from tests.p3_helpers import retrieval_rules
from tests.p6_helpers import P6World, make_world

from reqpilot.domain.enums import ApprovalDecisionType, Role, SecurityRiskLevel
from reqpilot.domain.models.approval import ApprovalTask
from reqpilot.domain.models.compliance import (
    ComplianceGap,
    ComplianceMapping,
    SecurityPrivacyFinding,
)
from reqpilot.domain.models.knowledge import Evidence
from reqpilot.retrieval.embeddings import HashingEmbeddingProvider
from reqpilot.services.approval import ApprovalService
from reqpilot.services.audit import AuditService
from reqpilot.services.knowledge.retrieval import RetrievalService

pytestmark = [pytest.mark.integration, requires_postgres]


def real_retrieval(world: P6World):
    """The P2 hybrid retrieval service itself - allowlist join, pgvector, full text."""
    return RetrievalService(
        world.session,
        world.analyst,
        embedder=HashingEmbeddingProvider(),
        rules=retrieval_rules(),
        default_top_k=8,
    ).retrieve


@pytest.fixture
def world(pg_session: Session) -> P6World:
    world = make_world(pg_session)
    world.retriever = real_retrieval(world)  # type: ignore[assignment]
    summary = world.analyse()
    assert summary.status.value == "completed", summary.errors
    pg_session.flush()
    return world


def refused(session: Session, sql: str, params: dict | None = None) -> str:
    savepoint = session.begin_nested()
    with pytest.raises(DBAPIError) as excinfo:
        session.execute(text(sql), params or {})
        session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    savepoint.rollback()
    # The driver's own message: SQLAlchemy's str() shortens long statements and names.
    return str(excinfo.value.orig)


def one(session: Session, model):
    row = session.scalars(select(model)).first()
    assert row is not None
    return row


# --- the real pipeline ---------------------------------------------------------------------------


def test_the_compliance_run_completes_on_real_hybrid_retrieval(world: P6World) -> None:
    mappings = list(world.session.scalars(select(ComplianceMapping)))
    assert mappings, "real retrieval supplied evidence the scripted model could cite"
    evidence = set(world.session.scalars(select(Evidence.id)))
    for mapping in mappings:
        assert {c["evidence_id"] for c in mapping.citations} <= {str(e) for e in evidence}
    assert list(world.session.scalars(select(ComplianceGap)))
    high = [
        f
        for f in world.session.scalars(select(SecurityPrivacyFinding))
        if f.risk_level is SecurityRiskLevel.HIGH
    ]
    assert high and all(f.approval_task_id for f in high)
    assert AuditService(world.session).verify_project_chain(world.project_id) == (True, None)


def test_g2_and_g3_are_decided_on_postgresql(world: P6World) -> None:
    for task in world.session.scalars(select(ApprovalTask)):
        actor = (
            world.compliance_officer
            if task.required_role is Role.COMPLIANCE_OFFICER
            else world.security_reviewer
        )
        ApprovalService(world.session, actor).decide(
            project_id=world.project_id,
            task_id=task.id,
            decision=ApprovalDecisionType.APPROVE,
            role_exercised=task.required_role,
        )
    world.session.flush()
    assert AuditService(world.session).verify_project_chain(world.project_id) == (True, None)


# --- composite foreign keys: no cross-project rows -----------------------------------------------


def test_a_mapping_cannot_reference_another_projects_version_or_task(pg_session: Session) -> None:
    first = make_world(pg_session)
    first.retriever = real_retrieval(first)  # type: ignore[assignment]
    first.analyse()
    other = make_world(pg_session, "Another bank (synthetic)")
    other.retriever = real_retrieval(other)  # type: ignore[assignment]
    other.analyse()
    pg_session.flush()
    mapping = one(pg_session, ComplianceMapping)
    foreign_version = other.versions["C01"].id
    foreign_task = next(
        t.id for t in pg_session.scalars(select(ApprovalTask)) if t.project_id == other.project_id
    )
    # Content is immutable (a trigger), and a new row naming another project's
    # version fails its composite foreign key.
    assert "immutable" in refused(
        pg_session,
        "UPDATE compliance_mapping SET requirement_version_id = :v WHERE id = :i",
        {"v": foreign_version, "i": mapping.id},
    )
    assert "fk_compliance_mapping_version_same_project" in refused(
        pg_session,
        "INSERT INTO compliance_mapping SELECT gen_random_uuid(), project_id, :v, graph_run_id, "
        "agent_run_id, 'LO-CROSS', control_title, obligation_kind, checklist_ref, "
        "checklist_domain, checklist_jurisdiction, relationship, rationale, candidate_text, "
        "implied_obligation, jurisdiction, source_type, jurisdictions, source_types, citations, "
        "evidence_count, false, high_impact_reasons, review_signal, language_rules_version, "
        "content_hash, recorded_by, 'CANDIDATE', NULL, now(), now() "
        "FROM compliance_mapping WHERE id = :i",
        {"v": foreign_version, "i": mapping.id},
    )
    new = pg_session.scalars(
        select(ComplianceMapping).where(
            ComplianceMapping.project_id == first.project_id,
            ComplianceMapping.approval_task_id.is_(None),
        )
    ).first()
    assert new is not None
    assert (
        "foreign key"
        in refused(
            pg_session,
            "UPDATE compliance_mapping SET approval_task_id = :t WHERE id = :i",
            {"t": foreign_task, "i": new.id},
        ).lower()
    )
    foreign_evidence = next(
        e.id for e in pg_session.scalars(select(Evidence)) if e.project_id == other.project_id
    )
    assert (
        "foreign key"
        in refused(
            pg_session,
            "INSERT INTO compliance_mapping_evidence (id, project_id, mapping_id, evidence_id, "
            "created_at) VALUES (:id, :p, :m, :e, now())",
            {"id": uuid.uuid4(), "p": first.project_id, "m": mapping.id, "e": foreign_evidence},
        ).lower()
    )


# --- checks: INV-G3 and FR-CMP-004 at the database -----------------------------------------------


def _finding_insert(world: P6World, **overrides) -> tuple[str, dict]:
    values = {
        "id": uuid.uuid4(),
        "p": world.project_id,
        "v": world.versions["C04"].id,
        "category": "SECURITY",
        "family": "AUTHENTICATION",
        "proposed": "low",
        "normalised": "LOW",
        "floor": "HIGH",
        "level": "HIGH",
        "status": "PENDING_REVIEW",
    }
    values.update(overrides)
    sql = (
        "INSERT INTO security_privacy_finding (id, project_id, requirement_version_id, category, "
        "family, derived_requirement, rationale, evidence_status, evidence_count, citations, "
        "proposed_risk_level, normalised_proposed_level, catalogue_floor, risk_level, "
        "risk_rules_version, escalation_reason, detected_by, content_hash, recorded_by, status, "
        "created_at, updated_at) VALUES (:id, :p, :v, :category, :family, 'The system shall x.', "
        "'r', 'UNAVAILABLE', 0, '[]', :proposed, :normalised, :floor, :level, 'r@1', 'e', 'RULE', "
        "'h', :p, :status, now(), now())"
    )
    return sql, values


def test_the_database_refuses_a_downgraded_or_ungated_risk(world: P6World) -> None:
    session = world.session
    for overrides, constraint in (
        ({"level": "LOW"}, "risk_is_max"),  # below the HIGH floor
        ({"floor": "MEDIUM", "level": "MEDIUM", "status": "PROPOSED"}, "high_impact_family_floor"),
        ({"status": "PROPOSED"}, "high_risk_is_gated"),
        (
            {
                "category": "PRIVACY",
                "family": "DATA_MINIMISATION",
                "floor": "LOW",
                "level": "LOW",
                "status": "PROPOSED",
            },
            "privacy_floor",
        ),
        (
            {
                "normalised": "HIGH",
                "floor": "LOW",
                "family": "SESSION_MANAGEMENT",
                "level": "MEDIUM",
            },
            "risk_is_max",
        ),
    ):
        sql, values = _finding_insert(world, **overrides)
        assert constraint in refused(session, sql, values), overrides
    sql, values = _finding_insert(world)  # the valid shape is accepted
    session.execute(text(sql), values)


def test_a_high_impact_mapping_cannot_be_an_ungated_candidate(world: P6World) -> None:
    mapping = next(m for m in world.session.scalars(select(ComplianceMapping)) if m.is_high_impact)
    message = refused(
        world.session,
        "INSERT INTO compliance_mapping SELECT gen_random_uuid(), project_id, "
        "requirement_version_id, graph_run_id, agent_run_id, control_key || '-X', control_title, "
        "obligation_kind, checklist_ref, checklist_domain, checklist_jurisdiction, relationship, "
        "rationale, candidate_text, implied_obligation, jurisdiction, source_type, jurisdictions, "
        "source_types, citations, evidence_count, true, high_impact_reasons, review_signal, "
        "language_rules_version, content_hash, recorded_by, 'CANDIDATE', NULL, now(), now() "
        "FROM compliance_mapping WHERE id = :i",
        {"i": mapping.id},
    )
    assert "high_impact_is_gated" in message


def test_a_mapping_without_evidence_cannot_be_committed(world: P6World) -> None:
    mapping = one(world.session, ComplianceMapping)
    message = refused(
        world.session,
        "INSERT INTO compliance_mapping SELECT gen_random_uuid(), project_id, "
        "requirement_version_id, graph_run_id, agent_run_id, 'LO-NO-EVIDENCE', control_title, "
        "obligation_kind, checklist_ref, checklist_domain, checklist_jurisdiction, relationship, "
        "rationale, candidate_text, implied_obligation, jurisdiction, source_type, jurisdictions, "
        "source_types, citations, evidence_count, false, high_impact_reasons, review_signal, "
        "language_rules_version, content_hash, recorded_by, 'CANDIDATE', NULL, now(), now() "
        "FROM compliance_mapping WHERE id = :i",
        {"i": mapping.id},
    )
    assert "cites no evidence" in message


def test_one_active_mapping_per_version_and_control(world: P6World) -> None:
    mapping = one(world.session, ComplianceMapping)
    message = refused(
        world.session,
        "INSERT INTO compliance_mapping SELECT gen_random_uuid(), project_id, "
        "requirement_version_id, graph_run_id, agent_run_id, control_key, control_title, "
        "obligation_kind, checklist_ref, checklist_domain, checklist_jurisdiction, relationship, "
        "rationale, candidate_text, implied_obligation, jurisdiction, source_type, jurisdictions, "
        "source_types, citations, evidence_count, is_high_impact, high_impact_reasons, "
        "review_signal, language_rules_version, content_hash, recorded_by, status, NULL, now(), "
        "now() FROM compliance_mapping WHERE id = :i",
        {"i": mapping.id},
    )
    assert "uq_compliance_mapping_active" in message


# --- triggers: immutability ----------------------------------------------------------------------


def test_p6_rows_are_immutable_and_never_deleted(world: P6World) -> None:
    session = world.session
    mapping = one(session, ComplianceMapping)
    finding = next(
        f
        for f in session.scalars(select(SecurityPrivacyFinding))
        if f.risk_level is SecurityRiskLevel.HIGH
    )
    gap = one(session, ComplianceGap)
    for sql, params, words in (
        (
            "UPDATE compliance_mapping SET rationale = 'x' WHERE id = :i",
            {"i": mapping.id},
            "immutable",
        ),
        (
            "UPDATE compliance_mapping SET is_high_impact = NOT is_high_impact WHERE id = :i",
            {"i": mapping.id},
            "immutable",
        ),
        (
            "UPDATE compliance_mapping SET jurisdiction = 'SG' WHERE id = :i",
            {"i": mapping.id},
            "immutable",
        ),
        ("DELETE FROM compliance_mapping WHERE id = :i", {"i": mapping.id}, "never deleted"),
        (
            "UPDATE security_privacy_finding SET risk_level = 'LOW' WHERE id = :i",
            {"i": finding.id},
            "",
        ),
        (
            "UPDATE security_privacy_finding SET proposed_risk_level = 'high' WHERE id = :i",
            {"i": finding.id},
            "immutable",
        ),
        (
            "UPDATE security_privacy_finding SET status = 'PROPOSED' WHERE id = :i",
            {"i": finding.id},
            "",
        ),
        ("DELETE FROM security_privacy_finding WHERE id = :i", {"i": finding.id}, "never deleted"),
        ("UPDATE compliance_gap SET reason = 'x' WHERE id = :i", {"i": gap.id}, "append-only"),
        ("DELETE FROM compliance_gap WHERE id = :i", {"i": gap.id}, "append-only"),
        (
            "DELETE FROM compliance_mapping_evidence WHERE mapping_id = :i",
            {"i": mapping.id},
            "append-only",
        ),
        (
            "UPDATE security_privacy_finding SET approval_task_id = gen_random_uuid() "
            "WHERE id = :i",
            {"i": finding.id},
            "",
        ),
    ):
        assert words in refused(session, sql, params), sql


def test_a_decided_status_never_moves_again(world: P6World) -> None:
    task = next(
        t
        for t in world.session.scalars(select(ApprovalTask))
        if t.required_role is Role.SECURITY_REVIEWER
    )
    ApprovalService(world.session, world.security_reviewer).decide(
        project_id=world.project_id,
        task_id=task.id,
        decision=ApprovalDecisionType.REJECT,
        role_exercised=Role.SECURITY_REVIEWER,
        justification="not needed (synthetic)",
    )
    world.session.flush()
    assert "cannot move" in refused(
        world.session,
        "UPDATE security_privacy_finding SET status = 'APPROVED' WHERE id = :i",
        {"i": task.subject_id},
    )


def test_the_audit_log_stays_append_only(world: P6World) -> None:
    assert refused(
        world.session,
        "UPDATE audit_event SET payload = '{}' WHERE project_id = :p",
        {"p": world.project_id},
    )
