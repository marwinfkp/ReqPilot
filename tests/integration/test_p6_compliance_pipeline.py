"""The P6 pipeline on SQLite: retrieval -> evidence -> model -> validation -> gaps ->
security/privacy evaluation -> G2/G3, and the human side of both gates.

The model is the scripted P6 model; retrieval is the allowlist-joined test double
(PostgreSQL runs the real hybrid retrieval, in ``test_p6_postgres.py``).
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.p5_helpers import quality_rules
from tests.p6_helpers import (
    FixtureRetriever,
    P6World,
    cite,
    finding,
    make_world,
    mapping,
    requirement_text,
    version_id,
)

from reqpilot.domain.compliance.language import COMPLIANCE_ADVISORY_NOTICE, find_prohibited
from reqpilot.domain.enums import (
    ApprovalDecisionType,
    ApprovalTaskStatus,
    AuditEventType,
    ComplianceGapOrigin,
    ComplianceMappingStatus,
    EvidenceStatus,
    FindingDetector,
    Gate,
    ReviewReason,
    Role,
    SecurityControlFamily,
    SecurityFindingStatus,
    SecurityRiskLevel,
)
from reqpilot.domain.errors import (
    AuthorizationError,
    ProjectIsolationError,
    ReqPilotError,
    SelfApprovalError,
    StaleApprovalError,
    StateTransitionError,
)
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.approval import ApprovalTask
from reqpilot.domain.models.audit import AuditEvent
from reqpilot.domain.models.compliance import (
    ComplianceGap,
    ComplianceMapping,
    ComplianceMappingEvidence,
    SecurityPrivacyFinding,
)
from reqpilot.domain.models.extraction import ReviewItem
from reqpilot.domain.models.runs import AgentRun
from reqpilot.services.approval import ApprovalService
from reqpilot.services.audit import AuditService
from reqpilot.services.compliance import ComplianceReadService
from reqpilot.services.knowledge.evidence import EvidenceService
from reqpilot.services.requirements import RequirementService
from reqpilot.services.requirements.service import RequirementContent

pytestmark = pytest.mark.integration


@pytest.fixture
def world(db_session: Session) -> P6World:
    return make_world(db_session)


def mappings(session: Session) -> list[ComplianceMapping]:
    return list(session.scalars(select(ComplianceMapping)))


def findings(session: Session) -> list[SecurityPrivacyFinding]:
    return list(session.scalars(select(SecurityPrivacyFinding)))


def by_key(world: P6World, rows) -> dict[tuple[str, str], object]:
    return {
        (
            world.key_of(r.requirement_version_id),
            getattr(r, "control_key", None) or str(r.family),
        ): r
        for r in rows
    }


def events(session: Session, event_type: AuditEventType) -> list[AuditEvent]:
    return list(session.scalars(select(AuditEvent).where(AuditEvent.event_type == event_type)))


def task(session: Session, task_id) -> ApprovalTask:
    row = session.get(ApprovalTask, task_id)
    assert row is not None
    return row


def advance_to_analyzed(world: P6World, key: str) -> None:
    """Move a version to ANALYZED through the guarded P1 transitions (as a human)."""
    service = RequirementService(world.session, world.analyst)
    version = world.versions[key]
    for target in (
        RequirementState.EXTRACTED,
        RequirementState.CLASSIFIED,
        RequirementState.ANALYZED,
    ):
        service.transition(project_id=world.project_id, version_id=version.id, target=target)


# --- the pipeline ------------------------------------------------------------------------------


def test_the_run_records_grounded_mappings_gaps_findings_and_gates(world: P6World) -> None:
    summary = world.analyse()
    assert summary.status.value == "completed" and not summary.errors
    rows = by_key(world, mappings(world.session))
    assert set(rows) == {
        ("C01", "LO-RET-APPLICATION-RECORDS"),
        ("C02", "LO-AUTH-MFA-PRIVILEGED"),
        ("C03", "LO-PRV-CONSENT"),
        ("C07", "LO-TXN-DISBURSEMENT-INTEGRITY"),
        ("C07", "LO-APR-SANCTION-CHECKPOINT"),
    }
    retention = rows[("C01", "LO-RET-APPLICATION-RECORDS")]
    assert retention.is_high_impact and retention.status is ComplianceMappingStatus.PENDING_REVIEW
    mfa = rows[("C02", "LO-AUTH-MFA-PRIVILEGED")]
    assert not mfa.is_high_impact and mfa.status is ComplianceMappingStatus.CANDIDATE
    # FR-CMP-005: jurisdiction and source type from the cited source, never the model.
    for row in rows.values():
        assert row.jurisdiction == "IN" and row.source_type.value == "org_policy"
        assert row.evidence_count >= 1 and row.citations
        assert {
            "source_title",
            "issuing_body",
            "source_version",
            "curated_on",
            "kb_version",
        } <= set(row.citations[0])
    # FR-CMP-002: gaps = the 14 expected controls - the 5 covered.
    assert len(summary.compliance_gap_ids) == 9
    gap_keys = {g.control_key for g in world.session.scalars(select(ComplianceGap))}
    assert "LO-RET-APPLICATION-RECORDS" not in gap_keys and "LO-PRV-SUBJECT-RIGHTS" in gap_keys
    # G2 for each high-impact mapping; G3 for each authoritative HIGH finding.
    gates = [task(world.session, t) for t in summary.gate_task_ids]
    assert {t.gate for t in gates} == {
        Gate.G2_REGULATORY_INTERPRETATION,
        Gate.G3_HIGH_RISK_SECURITY,
    }
    for t in gates:
        assert t.blocking and t.status is ApprovalTaskStatus.OPEN
        expected = (
            Role.COMPLIANCE_OFFICER
            if t.gate is Gate.G2_REGULATORY_INTERPRETATION
            else Role.SECURITY_REVIEWER
        )
        assert t.required_role is expected
    assert {m.approval_task_id for m in rows.values() if m.is_high_impact} <= set(
        summary.gate_task_ids
    )
    # Nothing moved any requirement.
    for version in world.versions.values():
        world.session.refresh(version)
        assert version.state is RequirementState.CANDIDATE


def test_every_mapping_citation_resolves_to_evidence_of_its_own_run(world: P6World) -> None:
    summary = world.analyse()
    evidence = EvidenceService(world.session, world.auditor)
    run_evidence = evidence.evidence_ids_for_run(world.project_id, summary.run_id)
    assert run_evidence == set(summary.evidence_ids)
    for mapping_row in mappings(world.session):
        links = list(
            world.session.scalars(
                select(ComplianceMappingEvidence.evidence_id).where(
                    ComplianceMappingEvidence.mapping_id == mapping_row.id
                )
            )
        )
        assert len(links) == mapping_row.evidence_count
        for evidence_id in links:
            assert evidence_id in run_evidence
            citation = evidence.resolve_citation(
                world.project_id, evidence_id, allowed_evidence_ids=run_evidence
            )
            assert citation.quote and citation.jurisdiction == mapping_row.jurisdiction
    # The model was shown exactly the evidence the agent run records.
    runs = world.session.scalars(select(AgentRun).where(AgentRun.node == "compliance_map")).all()
    assert runs and all(r.evidence_ids for r in runs)


def test_the_catalogue_floor_overrides_a_low_proposal(world: P6World) -> None:
    world.analyse()
    rows = by_key(world, findings(world.session))
    auth = rows[("C02", "authentication")]
    assert auth.proposed_risk_level == "low"
    assert auth.normalised_proposed_level is SecurityRiskLevel.LOW
    assert auth.catalogue_floor is SecurityRiskLevel.HIGH
    assert auth.risk_level is SecurityRiskLevel.HIGH
    assert auth.status is SecurityFindingStatus.PENDING_REVIEW and auth.approval_task_id
    assert "overrides the proposed low" in auth.escalation_reason
    minimisation = rows[("C05", "data_minimisation")]
    assert minimisation.risk_level is SecurityRiskLevel.MEDIUM  # privacy floor
    assert minimisation.status is SecurityFindingStatus.PROPOSED
    assert minimisation.approval_task_id is None
    evaluated = events(world.session, AuditEventType.SECURITY_RISK_EVALUATED)
    assert {e.payload["proposed_risk_level"] for e in evaluated} >= {"low"}


def test_without_a_model_gaps_and_catalogue_findings_still_gate(world: P6World) -> None:
    """Removing the LLM never removes a gate (I.8): the stub makes no calls."""
    summary = world.analyse(semantic=False)
    assert world.model.calls.total() == 0
    assert not summary.compliance_mapping_ids
    assert len(summary.compliance_gap_ids) == 14  # nothing covered
    rows = findings(world.session)
    assert rows and all(r.detected_by is FindingDetector.RULE for r in rows)
    assert all(r.proposed_risk_level is None for r in rows)
    families = {(world.key_of(r.requirement_version_id), r.family) for r in rows}
    assert ("C02", SecurityControlFamily.AUTHENTICATION) in families
    assert ("C01", SecurityControlFamily.RETENTION) in families
    high = [r for r in rows if r.risk_level is SecurityRiskLevel.HIGH]
    assert high and all(r.approval_task_id is not None for r in high)
    assert all(r.evidence_status is EvidenceStatus.UNAVAILABLE for r in rows)


def test_a_model_omitting_an_indicated_family_cannot_suppress_it(world: P6World) -> None:
    def silent(request):
        category = "security" if "SECURITY" in request.instructions else "privacy"
        return json.dumps(
            {"requirement_version_id": version_id(request), "category": category, "findings": []}
        )

    world.model.overrides["security_requirement_analysis"] = silent
    world.model.overrides["privacy_requirement_analysis"] = silent
    world.analyse()
    rows = by_key(world, findings(world.session))
    auth = rows[("C02", "authentication")]
    assert auth.detected_by is FindingDetector.RULE
    assert auth.risk_level is SecurityRiskLevel.HIGH and auth.approval_task_id


@pytest.mark.parametrize("malformed", [None, "", "critical", 3, {"level": "low"}, ["low"]])
def test_a_malformed_proposed_level_normalises_to_medium_then_the_floor(world, malformed) -> None:
    def derive(request):
        category = "security" if "SECURITY" in request.instructions else "privacy"
        items = []
        if category == "security" and "multi-factor" in requirement_text(request):
            items.append(
                finding("authentication", "The system shall require MFA.", level=malformed)
            )
        if category == "privacy" and "date of birth" in requirement_text(request):
            items.append(
                finding(
                    "data_minimisation",
                    "The system shall collect only what is needed.",
                    level=malformed,
                )
            )
        return json.dumps(
            {"requirement_version_id": version_id(request), "category": category, "findings": items}
        )

    world.model.overrides["security_requirement_analysis"] = derive
    world.model.overrides["privacy_requirement_analysis"] = derive
    world.analyse()
    rows = by_key(world, findings(world.session))
    assert rows[("C02", "authentication")].normalised_proposed_level is SecurityRiskLevel.MEDIUM
    assert rows[("C02", "authentication")].risk_level is SecurityRiskLevel.HIGH
    assert rows[("C05", "data_minimisation")].risk_level is SecurityRiskLevel.MEDIUM


# --- dropped claims -----------------------------------------------------------------------------


def test_fabricated_citations_and_prohibited_language_are_dropped_and_audited(
    world: P6World,
) -> None:
    fabricated = str(uuid.uuid4())

    def rogue(request):
        text = requirement_text(request)
        items = []
        if "retained" in text:
            items.append(
                mapping(
                    request,
                    "LO-RET-APPLICATION-RECORDS",
                    cite(request, "retained")[:1],
                    rationale="The requirement is fully compliant with the retention policy.",
                )
            )
        if "multi-factor" in text:
            evidence = cite(request, "second factor")[:1]
            item = mapping(request, "LO-AUTH-MFA-PRIVILEGED", evidence)
            item["evidence_ids"] = [fabricated]
            items.append(item)
        if "consent" in text:
            items.append(
                mapping(
                    request,
                    "LO-PRV-CONSENT",
                    cite(request, "recorded consent")[:1],
                    candidate_text="This guarantees compliance; no review is needed.",
                )
            )
        return json.dumps({"requirement_version_id": version_id(request), "mappings": items})

    world.model.overrides["compliance_mapping"] = rogue
    summary = world.analyse()
    assert not summary.compliance_mapping_ids
    assert summary.claims_dropped >= 3
    dropped = events(world.session, AuditEventType.COMPLIANCE_CLAIM_DROPPED)
    reasons = {e.payload["reason"] for e in dropped}
    assert {"prohibited_language", "unsupported_citation", "authority_claim"} <= reasons
    for event in dropped:
        # References and reason codes only - never the dropped text.
        blob = json.dumps(event.payload).lower()
        assert "retention policy" not in blob and "guarantees compliance" not in blob
    items = world.session.scalars(
        select(ReviewItem).where(ReviewItem.reason == ReviewReason.CLAIM_DROPPED)
    ).all()
    assert items
    # The fabricated id never became evidence and never resolves.
    assert uuid.UUID(fabricated) not in set(summary.evidence_ids)
    # The uncovered controls are gaps - a dropped claim covers nothing.
    gap_keys = {g.control_key for g in world.session.scalars(select(ComplianceGap))}
    assert {"LO-RET-APPLICATION-RECORDS", "LO-AUTH-MFA-PRIVILEGED", "LO-PRV-CONSENT"} <= gap_keys


def test_malformed_model_output_persists_nothing_and_raises_a_review_item(world: P6World) -> None:
    world.model.overrides["compliance_mapping"] = lambda _r: json.dumps(
        {"requirement_version_id": "x", "mappings": [], "status": "approved"}
    )
    summary = world.analyse()
    assert summary.status.value == "completed"
    assert not summary.compliance_mapping_ids and summary.semantic_failures >= 1
    assert world.session.scalars(
        select(ReviewItem).where(ReviewItem.reason == ReviewReason.MALFORMED_OUTPUT)
    ).first()
    assert summary.compliance_gap_ids  # the rule engine still ran


def test_a_model_trying_to_write_the_authoritative_risk_is_schema_invalid(world: P6World) -> None:
    def forge(request):
        category = "security" if "SECURITY" in request.instructions else "privacy"
        items = []
        if category == "security" and "multi-factor" in requirement_text(request):
            items.append(
                finding("authentication", "The system shall require MFA.", risk_level="low")
            )
        return json.dumps(
            {"requirement_version_id": version_id(request), "category": category, "findings": items}
        )

    world.model.overrides["security_requirement_analysis"] = forge
    world.analyse()
    rows = by_key(world, findings(world.session))
    auth = rows[("C02", "authentication")]
    # The forged output was refused; the catalogue baseline stands, at HIGH.
    assert auth.detected_by is FindingDetector.RULE and auth.risk_level is SecurityRiskLevel.HIGH


def test_empty_retrieval_escalates_and_is_never_answered_from_memory(db_session: Session) -> None:
    world = make_world(db_session)
    world.retriever = FixtureRetriever(db_session, world.analyst, empty_for=("retained",))
    summary = world.analyse()
    c01 = world.versions["C01"].id
    assert c01 in summary.evidence_unavailable_ids
    asked = [
        r for r in world.provider.requests if r.prompt_template_id.startswith("compliance_mapping")
    ]
    assert all("retained" not in requirement_text(r) for r in asked)
    assert not [m for m in mappings(db_session) if m.requirement_version_id == c01]
    items = db_session.scalars(
        select(ReviewItem).where(ReviewItem.reason == ReviewReason.EVIDENCE_UNAVAILABLE)
    ).all()
    assert c01 in {i.subject_id for i in items}
    retrieved = events(db_session, AuditEventType.COMPLIANCE_RETRIEVED)
    assert any(e.payload["outcome"] == "RETRIEVAL_EMPTY" for e in retrieved)


def test_no_allowlist_means_no_evidence_and_no_mapping(db_session: Session) -> None:
    from tests.workflow.test_p1_exit_test import make_project

    world = make_world(db_session)
    bare = make_project(db_session, "No allowlist (synthetic)")
    from tests.p3_helpers import member
    from tests.p6_helpers import fixture, seed_requirements

    analyst = member(db_session, bare, Role.ANALYST, "bare-analyst@example.test")
    seed_requirements(db_session, analyst, bare.id, fixture()["requirements"][:2])
    runner = world.runner(retriever=FixtureRetriever(db_session, analyst))
    summary = runner.analyse_compliance(actor=analyst, project_id=bare.id)
    assert summary.status.value == "completed"
    assert not summary.evidence_ids and not summary.compliance_mapping_ids
    assert len(summary.evidence_unavailable_ids) == 2
    # No checklist applies without a jurisdiction scope: no invented expectations.
    assert not summary.compliance_gap_ids


# --- P5 integration ---------------------------------------------------------------------------


def test_a_p5_privacy_signal_becomes_p6_derived_requirements(world: P6World) -> None:
    quality = world.runner(quality_rules=quality_rules()).analyse_quality(
        actor=world.analyst, project_id=world.project_id, semantic=False
    )
    assert quality.status.value == "completed"
    world.analyse(semantic=False)
    c05 = world.versions["C05"].id
    rows = [f for f in findings(world.session) if f.requirement_version_id == c05]
    families = {f.family: f for f in rows}
    assert SecurityControlFamily.RETENTION in families
    assert SecurityControlFamily.DATA_MINIMISATION in families
    assert families[SecurityControlFamily.RETENTION].source_signal_finding_id is not None
    assert families[SecurityControlFamily.RETENTION].risk_level is SecurityRiskLevel.HIGH


# --- G2 / G3: the human side ------------------------------------------------------------------


def _g2(world: P6World, key: str = "C01") -> tuple[ComplianceMapping, ApprovalTask]:
    world.analyse()
    mapping_row = next(
        m
        for m in mappings(world.session)
        if m.requirement_version_id == world.versions[key].id and m.is_high_impact
    )
    return mapping_row, task(world.session, mapping_row.approval_task_id)


def _g3(world: P6World, key: str = "C02") -> tuple[SecurityPrivacyFinding, ApprovalTask]:
    world.analyse()
    row = next(
        f
        for f in findings(world.session)
        if f.requirement_version_id == world.versions[key].id
        and f.risk_level is SecurityRiskLevel.HIGH
    )
    return row, task(world.session, row.approval_task_id)


def test_the_compliance_officer_approves_g2_and_nothing_else_moves(world: P6World) -> None:
    mapping_row, g2 = _g2(world)
    version = world.versions["C01"]
    outcome = ApprovalService(world.session, world.compliance_officer).decide(
        project_id=world.project_id,
        task_id=g2.id,
        decision=ApprovalDecisionType.APPROVE,
        role_exercised=Role.COMPLIANCE_OFFICER,
        justification="interpretation reviewed",
    )
    assert outcome.task_closed and outcome.decision.subject_version_hash == mapping_row.content_hash
    world.session.refresh(mapping_row)
    world.session.refresh(version)
    assert mapping_row.status is ComplianceMappingStatus.APPROVED
    assert version.state is RequirementState.CANDIDATE  # a G2 decision never moves a requirement
    reviewed = events(world.session, AuditEventType.COMPLIANCE_MAPPING_REVIEWED)
    assert reviewed and reviewed[-1].payload["decision"] == "APPROVE"
    assert events(world.session, AuditEventType.GATE_PASSED)


def test_a_g2_rejection_discards_the_mapping_and_records_a_gap(world: P6World) -> None:
    mapping_row, g2 = _g2(world)
    ApprovalService(world.session, world.compliance_officer).decide(
        project_id=world.project_id,
        task_id=g2.id,
        decision=ApprovalDecisionType.REJECT,
        role_exercised=Role.COMPLIANCE_OFFICER,
        justification="the evidence does not support this reading",
    )
    world.session.refresh(mapping_row)
    assert mapping_row.status is ComplianceMappingStatus.REJECTED
    gap = world.session.scalars(
        select(ComplianceGap).where(ComplianceGap.origin == ComplianceGapOrigin.G2_REJECTION)
    ).one()
    assert gap.control_key == mapping_row.control_key and gap.related_mapping_id == mapping_row.id
    _run, gaps = ComplianceReadService(world.session, world.auditor).current_gaps(world.project_id)
    assert gap.id in {g.id for g in gaps}


def test_the_security_reviewer_decides_g3(world: P6World) -> None:
    row, g3 = _g3(world)
    ApprovalService(world.session, world.security_reviewer).decide(
        project_id=world.project_id,
        task_id=g3.id,
        decision=ApprovalDecisionType.APPROVE,
        role_exercised=Role.SECURITY_REVIEWER,
    )
    world.session.refresh(row)
    assert row.status is SecurityFindingStatus.APPROVED
    assert row.risk_level is SecurityRiskLevel.HIGH  # the decision never rewrites the level


@pytest.mark.parametrize(
    ("gate_of", "who", "role"),
    [
        ("g2", "security_reviewer", Role.SECURITY_REVIEWER),
        ("g2", "analyst", Role.ANALYST),
        ("g2", "auditor", Role.AUDITOR),
        ("g3", "compliance_officer", Role.COMPLIANCE_OFFICER),
        ("g3", "analyst", Role.ANALYST),
        ("g3", "security_reviewer", Role.COMPLIANCE_OFFICER),
    ],
)
def test_wrong_role_approval_is_refused(world: P6World, gate_of: str, who: str, role: Role) -> None:
    _row, gate_task = _g2(world) if gate_of == "g2" else _g3(world)
    with pytest.raises(AuthorizationError):
        ApprovalService(world.session, getattr(world, who)).decide(
            project_id=world.project_id,
            task_id=gate_task.id,
            decision=ApprovalDecisionType.APPROVE,
            role_exercised=role,
        )
    assert task(world.session, gate_task.id).status is ApprovalTaskStatus.OPEN


def test_cross_project_approval_is_refused(world: P6World) -> None:
    _row, g2 = _g2(world)
    other = make_world(world.session, "Another project (synthetic)")
    with pytest.raises((ProjectIsolationError, ReqPilotError)):
        ApprovalService(world.session, other.compliance_officer).decide(
            project_id=world.project_id,
            task_id=g2.id,
            decision=ApprovalDecisionType.APPROVE,
            role_exercised=Role.COMPLIANCE_OFFICER,
        )
    with pytest.raises(ReqPilotError, match="not found"):
        ApprovalService(world.session, other.compliance_officer).decide(
            project_id=other.project_id,
            task_id=g2.id,
            decision=ApprovalDecisionType.APPROVE,
            role_exercised=Role.COMPLIANCE_OFFICER,
        )


def test_an_approval_after_the_requirement_changed_is_stale(world: P6World) -> None:
    mapping_row, g2 = _g2(world)
    RequirementService(world.session, world.analyst).create_version(
        project_id=world.project_id,
        requirement_id=world.versions["C01"].requirement_id,
        content=RequirementContent(
            statement="Loan application records shall be retained for ten years.",
            source_refs=tuple(world.versions["C01"].source_refs),
        ),
        change_reason="stakeholder correction",
    )
    with pytest.raises(StaleApprovalError):
        ApprovalService(world.session, world.compliance_officer).decide(
            project_id=world.project_id,
            task_id=g2.id,
            decision=ApprovalDecisionType.APPROVE,
            role_exercised=Role.COMPLIANCE_OFFICER,
        )
    world.session.refresh(mapping_row)
    assert mapping_row.status is ComplianceMappingStatus.PENDING_REVIEW


def test_the_requirement_author_may_not_decide_its_interpretation(world: P6World) -> None:
    _row, g2 = _g2(world)
    author = world.analyst
    # Give the author the Compliance Officer role too; segregation of duties still holds.
    both = type(author)(
        actor_id=author.actor_id,
        kind=author.kind,
        roles_by_project={world.project_id: frozenset({Role.ANALYST, Role.COMPLIANCE_OFFICER})},
    )
    with pytest.raises(SelfApprovalError):
        ApprovalService(world.session, both).decide(
            project_id=world.project_id,
            task_id=g2.id,
            decision=ApprovalDecisionType.APPROVE,
            role_exercised=Role.COMPLIANCE_OFFICER,
        )


def test_open_g2_and_g3_block_validated_until_decided(world: P6World) -> None:
    advance_to_analyzed(world, "C02")
    advance_to_analyzed(world, "C04")
    row, g3 = _g3(world, "C02")
    service = RequirementService(world.session, world.analyst)
    with pytest.raises(StateTransitionError, match="blocking approval task"):
        service.transition(
            project_id=world.project_id,
            version_id=world.versions["C02"].id,
            target=RequirementState.VALIDATED,
        )
    # A version with nothing pending is not blocked by another version's gate.
    service.transition(
        project_id=world.project_id,
        version_id=world.versions["C04"].id,
        target=RequirementState.VALIDATED,
    )
    ApprovalService(world.session, world.security_reviewer).decide(
        project_id=world.project_id,
        task_id=g3.id,
        decision=ApprovalDecisionType.APPROVE,
        role_exercised=Role.SECURITY_REVIEWER,
    )
    # C02's G2? MFA mapping is not high-impact, so G3 was its only gate.
    service.transition(
        project_id=world.project_id,
        version_id=world.versions["C02"].id,
        target=RequirementState.VALIDATED,
    )
    world.session.refresh(row)
    assert row.status is SecurityFindingStatus.APPROVED
    assert world.versions["C02"].state is RequirementState.VALIDATED


def test_the_pipeline_cannot_decide_its_own_gates(world: P6World) -> None:
    summary = world.analyse()
    run_actor = type(world.analyst)(
        actor_id=summary.run_id,  # the pipeline acts as its run
        kind=type(world.analyst.kind).SYSTEM,
        roles_by_project={
            world.project_id: frozenset({Role.COMPLIANCE_OFFICER, Role.SECURITY_REVIEWER})
        },
    )
    for task_id in summary.gate_task_ids:
        gate_task = task(world.session, task_id)
        with pytest.raises(AuthorizationError):
            ApprovalService(world.session, run_actor).decide(
                project_id=world.project_id,
                task_id=task_id,
                decision=ApprovalDecisionType.APPROVE,
                role_exercised=gate_task.required_role,
            )


# --- idempotence, versions, report, audit --------------------------------------------------------


def test_a_second_run_records_no_duplicates_and_its_own_gaps(world: P6World) -> None:
    first = world.analyse()
    second = world.analyse()
    assert not second.compliance_mapping_ids and not second.security_finding_ids
    assert not second.gate_task_ids
    assert len(second.compliance_gap_ids) == len(first.compliance_gap_ids)
    run_id, gaps = ComplianceReadService(world.session, world.auditor).current_gaps(
        world.project_id
    )
    assert run_id == second.run_id and {g.graph_run_id for g in gaps} == {second.run_id}


def test_a_new_version_gets_its_own_analysis(world: P6World) -> None:
    world.analyse()
    old = world.versions["C01"]
    new = RequirementService(world.session, world.analyst).create_version(
        project_id=world.project_id,
        requirement_id=old.requirement_id,
        content=RequirementContent(
            statement="Loan application records shall be retained for eight years after closure.",
            source_refs=tuple(old.source_refs),
        ),
        change_reason="wording",
    )
    before = {m.id for m in mappings(world.session)}
    world.analyse(version_ids=[new.id])
    after = [m for m in mappings(world.session) if m.id not in before]
    assert after and all(m.requirement_version_id == new.id for m in after)
    # The old version's analysis is untouched, and nothing was transferred.
    assert all(
        m.requirement_version_id == old.id
        for m in mappings(world.session)
        if m.id in before and m.control_key == "LO-RET-APPLICATION-RECORDS"
    )


def test_the_compliance_report_carries_the_notice_and_no_prohibited_assertion(world) -> None:
    world.analyse()
    report = ComplianceReadService(world.session, world.auditor).render_markdown(world.project_id)
    assert report.count(COMPLIANCE_ADVISORY_NOTICE) == 2
    assert find_prohibited(report) == []
    for words in (
        "Implied checkpoints and obligations",
        "approval checkpoint",
        "retention obligation",
    ):
        assert words in report
    assert "Authoritative risk level: high" in report


def test_audit_trail_is_complete_references_only_and_verifies(world: P6World) -> None:
    summary = world.analyse()
    for event_type in (
        AuditEventType.RUN_STARTED,
        AuditEventType.COMPLIANCE_RETRIEVED,
        AuditEventType.COMPLIANCE_PROPOSED,
        AuditEventType.COMPLIANCE_MAPPING_ACCEPTED,
        AuditEventType.COMPLIANCE_GAP_FOUND,
        AuditEventType.SECURITY_REQUIREMENT_DERIVED,
        AuditEventType.PRIVACY_REQUIREMENT_DERIVED,
        AuditEventType.SECURITY_RISK_EVALUATED,
        AuditEventType.APPROVAL_TASK_CREATED,
        AuditEventType.RUN_COMPLETED,
    ):
        assert events(world.session, event_type), event_type
    statements = [v.statement for v in world.versions.values()]
    for event in world.session.scalars(select(AuditEvent)):
        blob = json.dumps(event.payload)
        assert not any(s in blob for s in statements)
    assert AuditService(world.session).verify_project_chain(world.project_id) == (True, None)
    assert summary.status.value == "completed"
