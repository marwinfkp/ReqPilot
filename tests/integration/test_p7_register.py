"""The risk register: persistence, linkage, immutability and the derived views (P7).

What the database refuses, what the engine records, and what the register
reports. The severity is the through-line: every test here is a way of asking
"could a severity that is not the matrix's get into, or out of, the register?"
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from tests.p7_helpers import make_p7_world, risk_rules

from reqpilot.domain.enums import (
    AuditEventType,
    FindingDetector,
    MitigationStatus,
    RiskCategory,
    RiskImpact,
    RiskLikelihood,
    RiskScope,
    RiskSeverity,
    RiskStatus,
    Role,
)
from reqpilot.domain.errors import ImmutableRecordError, ReqPilotError, ScopeGuardError
from reqpilot.domain.models.audit import AuditEvent
from reqpilot.domain.models.risk import Risk, RiskMatrixCell, RiskMitigation
from reqpilot.services.risk import RiskRegisterService, RiskService
from reqpilot.services.risk.register import REGISTER_NOTICE

pytestmark = pytest.mark.integration


@pytest.fixture
def world(db_session: Session):
    w = make_p7_world(db_session)
    w.full_analysis()
    return w


def evidence_id(world) -> uuid.UUID:
    """One piece of this project's evidence, through the ORM (never raw SQL)."""
    from reqpilot.domain.models.knowledge import Evidence

    found = world.session.scalars(
        select(Evidence.id).where(Evidence.project_id == world.project_id)
    ).first()
    assert found is not None, "the compliance run records evidence this project can cite"
    return found


def _params(world, risk, version: uuid.UUID | None = None) -> dict[str, object]:
    """Raw-SQL parameters. SQLite's driver binds no UUID, so ids go as text."""
    return {
        "i": str(uuid.uuid4()),
        "p": str(world.project_id),
        "v": str(version) if version is not None else None,
        "m": risk.matrix_version,
        "h": "0" * 64,
        "a": str(risk.recorded_by),
        "t": risk.created_at.isoformat(),
    }


def audit_types(session: Session) -> list[str]:
    return [str(e.event_type) for e in session.scalars(select(AuditEvent))]


# --- what a run records ------------------------------------------------------------------------


def test_a_run_records_risks_with_ratings_rationales_evidence_and_a_computed_severity(
    world,
) -> None:
    risks = world.risks()
    assert risks, "the scripted world records risks"
    matrix = risk_rules().matrix
    for risk in risks:
        assert risk.category in set(RiskCategory)
        assert risk.likelihood in set(RiskLikelihood) and risk.impact in set(RiskImpact)
        assert risk.likelihood_rationale and risk.impact_rationale
        assert risk.evidence_count >= 1 and risk.citations
        assert risk.matrix_version == matrix.version
        # The severity is the matrix's value for this risk's own cell - checked
        # against the matrix rather than against a remembered expectation.
        assert risk.severity is matrix.severity(risk.likelihood, risk.impact)
        assert risk.owner_role is risk_rules().owner_for(risk.category)
        assert risk.detected_by is FindingDetector.AGENT
        assert risk.scope_rules_version and risk.rules_version


def test_every_severity_the_matrix_can_produce_appears(world) -> None:
    """The synthetic world is built to cover the matrix, so the gate tests are real."""
    assert {r.severity for r in world.risks()} == set(RiskSeverity)


def test_a_requirement_risk_names_the_exact_version_and_a_project_risk_names_none(world) -> None:
    by_scope = {}
    for risk in world.risks():
        by_scope.setdefault(risk.scope, []).append(risk)
    assert RiskScope.REQUIREMENT in by_scope and RiskScope.PROJECT in by_scope
    known = {v.id for v in world.versions.values()}
    for risk in by_scope[RiskScope.REQUIREMENT]:
        assert risk.requirement_version_id in known
    for risk in by_scope[RiskScope.PROJECT]:
        assert risk.requirement_version_id is None


def test_a_high_risk_is_under_review_from_the_moment_it_exists(world) -> None:
    """``FR-RSK-007``, fail closed: it counts against the baseline guard before
    its G8 task exists, not after."""
    for risk in world.risks():
        if risk.severity is RiskSeverity.HIGH:
            assert risk.status is RiskStatus.UNDER_REVIEW
        else:
            assert risk.status is RiskStatus.PROPOSED


def test_mitigations_are_stored_as_ai_suggestions_requiring_validation(world) -> None:
    """``FR-RSK-005``."""
    mitigations = list(world.session.scalars(select(RiskMitigation)))
    assert mitigations
    for mitigation in mitigations:
        assert mitigation.is_ai_generated is True
        assert mitigation.status is MitigationStatus.SUGGESTED
        assert mitigation.accepted_by is None


def test_the_run_is_audited_from_start_to_escalation(world) -> None:
    types = audit_types(world.session)
    for expected in (
        AuditEventType.RISK_ANALYSIS_STARTED,
        AuditEventType.RISK_PROPOSED,
        AuditEventType.RISK_RECORDED,
        AuditEventType.RISK_SEVERITY_COMPUTED,
        AuditEventType.RISK_ESCALATED,
    ):
        assert str(expected) in types, f"{expected} was not audited"


def test_the_severity_audit_event_explains_the_computation(world) -> None:
    events = [
        e
        for e in world.session.scalars(select(AuditEvent))
        if e.event_type is AuditEventType.RISK_SEVERITY_COMPUTED
    ]
    assert events
    for event in events:
        payload = event.payload
        assert payload["matrix_version"] == risk_rules().matrix.version
        assert payload["likelihood"] and payload["impact"] and payload["severity"]
        assert "by risk matrix" in payload["explanation"]


def test_audit_payloads_carry_references_not_requirement_text(world) -> None:
    statements = [v.statement for v in world.versions.values()]
    for event in world.session.scalars(select(AuditEvent)):
        blob = str(event.payload)
        for statement in statements:
            assert statement not in blob


def test_re_running_records_no_duplicate_risks(world) -> None:
    before = len(world.risks())
    summary = world.analyse_risk()
    assert summary.status.value == "completed"
    assert len(world.risks()) == before


# --- what the database refuses -----------------------------------------------------------------


def test_a_forged_severity_cannot_be_inserted_even_by_raw_sql(world) -> None:
    """The composite foreign key into ``risk_matrix`` is the load-bearing control.

    Architecture G.6 asks for "a DB CHECK against ``risk_matrix``"; a SQL CHECK
    cannot reference another table, so the constraint is the foreign key that
    can. This test is what makes that claim testable: an L1xI1 risk claiming
    HIGH does not exist, whatever wrote it.
    """
    risk = world.risks()[0]
    with pytest.raises(IntegrityError):
        world.session.execute(
            text(
                "INSERT INTO risk (id, project_id, scope, requirement_version_id, category, "
                " title, title_key, description, likelihood, impact, severity, matrix_version, "
                " likelihood_rationale, impact_rationale, citations, evidence_count, detected_by, "
                " scope_rules_version, rules_version, content_hash, recorded_by, status, "
                " owner_role, created_at, updated_at) "
                "VALUES (:i, :p, 'PROJECT', NULL, 'TECHNICAL', 'forged', 'forged', 'forged', "
                " 'L1', 'I1', 'HIGH', :m, 'r', 'r', '[]', 1, 'AGENT', '1.0.0', 'r', "
                " :h, :a, 'PROPOSED', 'PROJECT_MANAGER', :t, :t)"
            ),
            _params(world, risk),
        )
    world.session.rollback()


def test_a_high_risk_cannot_be_stored_as_merely_proposed(world) -> None:
    """``FR-RSK-007`` at the database, so it is structural rather than a habit."""
    risk = world.risks()[0]
    with pytest.raises(IntegrityError):
        world.session.execute(
            text(
                "INSERT INTO risk (id, project_id, scope, requirement_version_id, category, "
                " title, title_key, description, likelihood, impact, severity, matrix_version, "
                " likelihood_rationale, impact_rationale, citations, evidence_count, detected_by, "
                " scope_rules_version, rules_version, content_hash, recorded_by, status, "
                " owner_role, created_at, updated_at) "
                "VALUES (:i, :p, 'PROJECT', NULL, 'TECHNICAL', 'ungated', 'ungated', 'ungated', "
                " 'L3', 'I3', 'HIGH', :m, 'r', 'r', '[]', 1, 'AGENT', '1.0.0', 'r', "
                " :h, :a, 'PROPOSED', 'PROJECT_MANAGER', :t, :t)"
            ),
            _params(world, risk),
        )
    world.session.rollback()


def test_a_requirement_risk_without_a_version_and_a_project_risk_with_one_are_both_refused(
    world,
) -> None:
    """``FR-RSK-001``: the two scopes are distinct, and neither is faked as the other."""
    # Captured before the loop: a rollback expires the ORM object, and this
    # test deliberately rolls back between the two refusals.
    params = _params(world, world.risks()[0])
    version_id = next(v.id for v in world.versions.values())
    for scope, version in (("REQUIREMENT", None), ("PROJECT", version_id)):
        with pytest.raises(IntegrityError):
            world.session.execute(
                text(
                    "INSERT INTO risk (id, project_id, scope, requirement_version_id, category, "
                    " title, title_key, description, likelihood, impact, severity, matrix_version,"
                    " likelihood_rationale, impact_rationale, citations, evidence_count, "
                    " detected_by, scope_rules_version, rules_version, content_hash, recorded_by, "
                    " status, owner_role, created_at, updated_at) "
                    f"VALUES (:i, :p, '{scope}', :v, 'TECHNICAL', 'x', 'x', 'x', "
                    " 'L1', 'I1', 'LOW', :m, 'r', 'r', '[]', 1, 'AGENT', '1.0.0', 'r', "
                    " :h, :a, 'PROPOSED', 'PROJECT_MANAGER', :t, :t)"
                ),
                {**params, "i": str(uuid.uuid4()), "v": str(version) if version else None},
            )
        world.session.rollback()


@pytest.mark.parametrize(
    "field",
    ["severity", "likelihood", "impact", "title", "description", "matrix_version", "category"],
)
def test_risk_content_including_its_severity_is_immutable(world, field: str) -> None:
    risk = world.risks()[0]
    current = getattr(risk, field)
    replacement = {
        "severity": RiskSeverity.LOW,
        "likelihood": RiskLikelihood.L1,
        "impact": RiskImpact.I1,
        "category": RiskCategory.BUSINESS,
    }.get(field, f"tampered-{field}")
    setattr(risk, field, replacement if replacement != current else "tampered")
    with pytest.raises(ImmutableRecordError, match="immutable"):
        world.session.flush()
    world.session.rollback()


def test_a_risk_is_never_deleted(world) -> None:
    world.session.delete(world.risks()[0])
    with pytest.raises(ImmutableRecordError, match="never deleted"):
        world.session.flush()
    world.session.rollback()


def test_the_published_matrix_cannot_be_edited_in_place(world) -> None:
    """Editing a cell would silently re-rate every risk that cites that version."""
    cell = world.session.scalars(
        select(RiskMatrixCell).where(RiskMatrixCell.severity != RiskSeverity.HIGH)
    ).first()
    cell.severity = RiskSeverity.HIGH  # the upgrade-everything edit
    with pytest.raises(ImmutableRecordError, match="immutable"):
        world.session.flush()
    world.session.rollback()


def test_a_status_cannot_jump_the_approved_path(world) -> None:
    risk = next(r for r in world.risks() if r.status is RiskStatus.PROPOSED)
    risk.status = RiskStatus.CLOSED
    with pytest.raises(ImmutableRecordError, match="cannot move"):
        world.session.flush()
    world.session.rollback()


# --- the register view (FR-RSK-008) -------------------------------------------------------------


def test_the_register_reports_every_field_the_requirement_asks_for(world) -> None:
    register = RiskRegisterService(world.session, world.analyst).register(world.project_id)
    assert register.risks
    for view in register.risks:
        assert view.id and view.category and view.title and view.description
        assert view.likelihood and view.impact and view.severity
        assert view.matrix_version and view.likelihood_rationale and view.impact_rationale
        assert view.owner_role in set(Role) and view.status in set(RiskStatus)
        assert view.created_at and view.updated_at
        assert view.evidence_count >= 1
        assert view.subject  # requirement label or "project-level"


def test_the_register_is_ordered_most_severe_first(world) -> None:
    register = RiskRegisterService(world.session, world.analyst).register(world.project_id)
    ranks = [{"high": 3, "medium": 2, "low": 1}[str(r.severity)] for r in register.risks]
    assert ranks == sorted(ranks, reverse=True)


def test_the_register_counts_what_blocks_a_baseline(world) -> None:
    register = RiskRegisterService(world.session, world.analyst).register(world.project_id)
    blocking = register.blocking
    assert blocking
    assert all(r.severity is RiskSeverity.HIGH for r in blocking)
    assert register.by_severity["high"] == len(blocking)


def test_the_register_distribution_answers_the_fr_rsk_009_example(world) -> None:
    """ "count and severity distribution of security and compliance risks"."""
    register = RiskRegisterService(world.session, world.analyst).register(world.project_id)
    for category in (RiskCategory.SECURITY, RiskCategory.COMPLIANCE):
        distribution = register.distribution(category)
        assert set(distribution) == {"low", "medium", "high"}
        assert sum(distribution.values()) == sum(
            1 for r in register.risks if r.category is category
        )


def test_the_factor_inputs_are_deterministic_and_carry_the_rows_that_produced_them(world) -> None:
    """``FR-RSK-009`` / architecture I.6: inputs to the SDLC profile, traceable to rows."""
    service = RiskRegisterService(world.session, world.analyst)
    factors = service.factor_inputs(world.project_id)
    assert {f.key for f in factors} == {
        "security_risk",
        "consequences_of_failure",
        "regulatory_criticality",
        "project_complexity",
    }
    assert factors == service.factor_inputs(world.project_id)
    ids = {r.id for r in world.risks()}
    for factor in factors:
        assert 1 <= factor.value <= 5
        assert set(factor.evidence_risk_ids) <= ids
        assert factor.description
    # A high security risk gives the top score, per the I.6 formula.
    security = next(f for f in factors if f.key == "security_risk")
    assert security.value == (5 if security.counts["high"] else security.value)


def test_the_markdown_register_is_deterministic_and_labels_its_suggestions(world) -> None:
    service = RiskRegisterService(world.session, world.analyst)
    rendered = service.render_markdown(world.project_id)
    assert rendered == service.render_markdown(world.project_id)
    assert REGISTER_NOTICE in rendered
    assert "not calibrated probabilities" in rendered
    assert "AI-suggested - requires human validation" in rendered
    assert "borrower credit risk" in rendered  # the FR-RSK-011 statement
    for risk in world.risks():
        assert risk.title in rendered
        assert f"**Severity {risk.severity}**" in rendered


# --- the human half (FR-RSK-010, FR-RSK-005) ----------------------------------------------------


def test_a_human_can_add_a_risk_and_the_matrix_rates_it(world) -> None:
    service = RiskService(world.session, world.analyst)
    risk = service.add_risk(
        project_id=world.project_id,
        category=RiskCategory.OPERATIONAL,
        title="Handover to operations is undefined",
        description="No requirement states who operates the service after go-live.",
        likelihood=RiskLikelihood.L3,
        impact=RiskImpact.I3,
        likelihood_rationale="Expected unless a runbook is written.",
        impact_rationale="Project-level failure at go-live.",
        evidence_ids=[evidence_id(world)],
        mitigation="Write an operations runbook before the pilot.",
    )
    assert risk.detected_by is FindingDetector.HUMAN
    # A human-entered risk is rated by the same matrix: there is no severity
    # parameter on this method either.
    assert risk.severity is RiskSeverity.HIGH
    assert risk.status is RiskStatus.UNDER_REVIEW
    (mitigation,) = world.session.scalars(
        select(RiskMitigation).where(RiskMitigation.risk_id == risk.id)
    ).all()
    assert mitigation.is_ai_generated is False and mitigation.status is MitigationStatus.ACCEPTED


def test_a_human_entered_borrower_credit_risk_is_refused_by_the_same_guard(world) -> None:
    with pytest.raises(ScopeGuardError, match="borrower credit risk"):
        RiskService(world.session, world.analyst).add_risk(
            project_id=world.project_id,
            category=RiskCategory.BUSINESS,
            title="Borrower credit risk is not modelled",
            description="We should compute a probability of default per applicant.",
            likelihood=RiskLikelihood.L2,
            impact=RiskImpact.I2,
            likelihood_rationale="r",
            impact_rationale="r",
            evidence_ids=[evidence_id(world)],
        )
    world.session.rollback()


def test_a_risk_decision_needs_a_recorded_rationale(world) -> None:
    risk = next(r for r in world.risks() if r.status is RiskStatus.PROPOSED)
    service = RiskService(world.session, world.analyst)
    with pytest.raises(ReqPilotError, match="rationale"):
        service.decide(
            project_id=world.project_id,
            risk_id=risk.id,
            status=RiskStatus.ACCEPTED,
            rationale="  ",
        )
    world.session.rollback()


def test_a_human_cannot_move_a_risk_back_to_proposed(world) -> None:
    risk = next(r for r in world.risks() if r.status is RiskStatus.PROPOSED)
    with pytest.raises(ReqPilotError, match="a human may move a risk"):
        RiskService(world.session, world.analyst).decide(
            project_id=world.project_id,
            risk_id=risk.id,
            status=RiskStatus.PROPOSED,
            rationale="no",
        )
    world.session.rollback()


def test_accepting_an_ai_mitigation_is_recorded_as_a_human_decision(world) -> None:
    mitigation = world.session.scalars(select(RiskMitigation)).first()
    decided = RiskService(world.session, world.analyst).decide_mitigation(
        project_id=world.project_id,
        mitigation_id=mitigation.id,
        status=MitigationStatus.ACCEPTED,
        rationale="Agreed for the next iteration.",
    )
    assert decided.status is MitigationStatus.ACCEPTED
    assert decided.accepted_by == world.analyst.actor_id
    assert decided.is_ai_generated is True  # provenance is never rewritten
    assert str(AuditEventType.RISK_MITIGATION_DECIDED) in audit_types(world.session)


def test_a_mitigations_text_and_provenance_are_immutable(world) -> None:
    mitigation = world.session.scalars(select(RiskMitigation)).first()
    mitigation.is_ai_generated = False
    with pytest.raises(ImmutableRecordError, match="immutable"):
        world.session.flush()
    world.session.rollback()


# --- project isolation ---------------------------------------------------------------------------


def test_another_projects_risks_are_invisible(db_session: Session) -> None:
    mine = make_p7_world(db_session, "P7 mine")
    mine.full_analysis()
    theirs = make_p7_world(db_session, "P7 theirs")
    assert mine.risks()
    # The other project's analyst sees none of them, and the repository's scoped
    # query is what enforces it - not a filter applied afterwards.
    from reqpilot.repositories.risk import RiskRepository

    visible = RiskRepository(db_session, theirs.analyst).list_for_project(theirs.project_id)
    assert visible == []
    for risk in mine.risks():
        assert RiskRepository(db_session, theirs.analyst).get(theirs.project_id, risk.id) is None


def test_a_risk_cannot_cite_another_projects_evidence(db_session: Session) -> None:
    mine = make_p7_world(db_session, "P7 mine")
    mine.full_analysis()
    theirs = make_p7_world(db_session, "P7 theirs")
    theirs.full_analysis()
    foreign = evidence_id(theirs)
    with pytest.raises((IntegrityError, ReqPilotError)):
        RiskService(db_session, mine.analyst).add_risk(
            project_id=mine.project_id,
            category=RiskCategory.TECHNICAL,
            title="Cross-project citation",
            description="Cites evidence from another project.",
            likelihood=RiskLikelihood.L1,
            impact=RiskImpact.I1,
            likelihood_rationale="r",
            impact_rationale="r",
            evidence_ids=[foreign],
        )
        db_session.flush()
    db_session.rollback()


def test_the_register_of_an_empty_project_is_empty_not_an_error(db_session: Session) -> None:
    world = make_p7_world(db_session, "P7 empty")
    register = RiskRegisterService(db_session, world.analyst).register(world.project_id)
    assert register.risks == () and register.blocking == ()
    assert REGISTER_NOTICE in RiskRegisterService(db_session, world.analyst).render_markdown(
        world.project_id
    )


def test_risk_rows_have_no_link_to_any_customer_or_applicant_entity() -> None:
    """Architecture I.1's third enforcement point, asserted structurally.

    The schema has no customer or applicant table, so a borrower-level risk is
    not merely refused - it is unrepresentable.
    """
    columns = {c.name for c in Risk.__table__.columns}
    for forbidden in ("customer_id", "applicant_id", "borrower_id", "account_id", "loan_id"):
        assert forbidden not in columns
    tables = set(Risk.metadata.tables)
    for forbidden in ("customer", "applicant", "borrower", "loan", "loan_application"):
        assert forbidden not in tables
