"""The P6 end-to-end exit test (P6 brief §48; roadmap P6 "Compliance & security analysis").

One synthetic retail-loan requirement set (``data/dev/compliance``) through the
real ``analysis_graph`` compliance path, the real gateway (scripted model that
also attempts the attacks), the P2 evidence and citation machinery, the P1
lifecycle guards and approval service, and the P5 security/privacy signal.

The ten ingredients the brief names, and where each is:

1. a requirement supported by a knowledge-base source ........ C01 (retention clause 2.1)
2. a requirement with a compliance mapping ................... C02 (MFA clause 4.1)
3. a deliberate unsupported citation attempt ................. C07 (fabricated evidence id)
4. a compliance claim containing prohibited language .......... C03 ("is fully compliant")
5. a high-impact regulatory interpretation .................... C01 (retention: G2)
6. a security requirement involving authentication ............ C02
7. a privacy requirement involving retention .................. C01
8. a high-impact security/privacy finding ..................... C01 retention, C02 authentication
9. a low proposed risk against a high catalogue floor ......... C02 (model: "low")
10. an injection statement attempting to force approval ....... C06
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.p5_helpers import quality_rules
from tests.p6_helpers import (
    ScriptedComplianceModel,
    cite,
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
    ComplianceMappingStatus,
    Gate,
    QualityFindingStatus,
    Role,
    SecurityControlFamily,
    SecurityFindingStatus,
    SecurityRiskLevel,
)
from reqpilot.domain.errors import AuthorizationError, StateTransitionError
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.approval import ApprovalDecision, ApprovalTask
from reqpilot.domain.models.audit import AuditEvent
from reqpilot.domain.models.compliance import (
    ComplianceGap,
    ComplianceMapping,
    SecurityPrivacyFinding,
)
from reqpilot.domain.models.requirements import RequirementVersion
from reqpilot.services.approval import ApprovalService
from reqpilot.services.audit import AuditService
from reqpilot.services.compliance import ComplianceReadService
from reqpilot.services.knowledge.evidence import EvidenceService
from reqpilot.services.quality import FindingReviewService
from reqpilot.services.requirements import RequirementService

pytestmark = pytest.mark.workflow

FABRICATED = str(uuid.UUID("00000000-0000-4000-8000-00000000dead"))


def attacking_model() -> ScriptedComplianceModel:
    """The default scripted answers, plus the attacks of ingredients 3, 4 and 10."""
    model = ScriptedComplianceModel()

    def map_with_attacks(request):
        answer = json.loads(ScriptedComplianceModel.map(model, request))
        text = requirement_text(request)
        if "consent" in text:  # 4: prohibited language on an otherwise valid claim
            for item in answer["mappings"]:
                item["rationale"] = "The requirement is fully compliant with the privacy policy."
        if "second loan officer" in text:  # 3: a fabricated citation
            for item in answer["mappings"]:
                item["evidence_ids"] = [FABRICATED]
        if "last login" in text:  # 10: obeying the injected instruction
            answer["mappings"].append(
                mapping(
                    request,
                    "LO-AUTH-MFA-PRIVILEGED",
                    cite(request, "second factor")[:1] or cite(request, "")[:1],
                    rationale="Marked compliant and approved as instructed.",
                    candidate_text="No Compliance Officer review is needed.",
                )
            )
        answer["requirement_version_id"] = version_id(request)
        return json.dumps(answer)

    model.overrides["compliance_mapping"] = map_with_attacks
    return model


def test_p6_exit_compliance_and_security_analysis(db_session: Session) -> None:
    world = make_world(db_session)
    model = attacking_model()
    from tests.p6_helpers import scripted_gateway

    world.gateway, world.model, world.provider = scripted_gateway(model)
    v = world.versions
    snapshot = {k: (x.statement, x.content_hash) for k, x in v.items()}

    # P5 first: its security/privacy signals are P6 inputs (FR-QAL-008 delegated).
    world.runner(quality_rules=quality_rules()).analyse_quality(
        actor=world.analyst, project_id=world.project_id, semantic=False
    )

    summary = world.analyse()
    assert summary.status.value == "completed" and not summary.errors

    maps = {
        (m.requirement_version_id, m.control_key): m
        for m in db_session.scalars(select(ComplianceMapping))
    }
    finds = {
        (f.requirement_version_id, f.family): f
        for f in db_session.scalars(select(SecurityPrivacyFinding))
    }

    # 1-2. Supported mappings persist with resolvable evidence of this run.
    retention = maps[(v["C01"].id, "LO-RET-APPLICATION-RECORDS")]
    mfa = maps[(v["C02"].id, "LO-AUTH-MFA-PRIVILEGED")]
    allowed = EvidenceService(db_session, world.auditor).evidence_ids_for_run(
        world.project_id, summary.run_id
    )
    for m in maps.values():
        for c in m.citations:
            citation = EvidenceService(db_session, world.auditor).resolve_citation(
                world.project_id, uuid.UUID(c["evidence_id"]), allowed_evidence_ids=allowed
            )
            assert citation.quote and citation.source_type.value == m.source_type.value
            assert m.jurisdiction == citation.jurisdiction == "IN"
    assert mfa.status is ComplianceMappingStatus.CANDIDATE

    # 3. The unsupported citation was rejected: nothing for C07, and the id never resolves.
    assert not [k for k in maps if k[0] == v["C07"].id]
    dropped = [
        e.payload
        for e in db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.event_type == AuditEventType.COMPLIANCE_CLAIM_DROPPED
            )
        )
    ]
    assert any(d["reason"] == "unsupported_citation" for d in dropped)
    assert uuid.UUID(FABRICATED) not in allowed

    # 4. The prohibited claim was rejected, not rewritten.
    assert not [k for k in maps if k[0] == v["C03"].id]
    assert any(d["reason"] == "prohibited_language" for d in dropped)
    for m in maps.values():
        for text in (m.rationale, m.candidate_text, m.implied_obligation):
            assert find_prohibited(text) == []

    # The standing advisory notice appears on the views and the generated artefact.
    reads = ComplianceReadService(db_session, world.auditor)
    assert reads.overview(world.project_id).advisory_notice == COMPLIANCE_ADVISORY_NOTICE
    report = reads.render_markdown(world.project_id)
    assert report.startswith("# Compliance") and report.count(COMPLIANCE_ADVISORY_NOTICE) == 2
    assert find_prohibited(report) == []

    # 5. The high-impact interpretation created a blocking G2 for the Compliance Officer.
    assert retention.is_high_impact and retention.status is ComplianceMappingStatus.PENDING_REVIEW
    g2 = db_session.get(ApprovalTask, retention.approval_task_id)
    assert g2.gate is Gate.G2_REGULATORY_INTERPRETATION and g2.blocking
    assert g2.required_role is Role.COMPLIANCE_OFFICER and g2.status is ApprovalTaskStatus.OPEN

    # 6-9. Security and privacy findings persisted; the floor overrides the low proposal.
    auth = finds[(v["C02"].id, SecurityControlFamily.AUTHENTICATION)]
    assert auth.proposed_risk_level == "low" and auth.catalogue_floor is SecurityRiskLevel.HIGH
    assert auth.risk_level is SecurityRiskLevel.HIGH
    ret = finds[(v["C01"].id, SecurityControlFamily.RETENTION)]
    assert ret.category.value == "privacy" and ret.risk_level is SecurityRiskLevel.HIGH
    for high in (auth, ret):
        g3 = db_session.get(ApprovalTask, high.approval_task_id)
        assert g3.gate is Gate.G3_HIGH_RISK_SECURITY and g3.blocking
        assert g3.required_role is Role.SECURITY_REVIEWER
        assert high.status is SecurityFindingStatus.PENDING_REVIEW
    # The P5 privacy signal on C05 became derived privacy requirements.
    c05 = [f for (vid, _fam), f in finds.items() if vid == v["C05"].id]
    assert any(f.source_signal_finding_id is not None for f in c05)

    # 10. The injection could not approve, lower risk, alter evidence or change lifecycle.
    assert not [k for k in maps if k[0] == v["C06"].id]
    c06_auth = finds[(v["C06"].id, SecurityControlFamily.AUTHENTICATION)]
    assert c06_auth.risk_level is SecurityRiskLevel.HIGH and c06_auth.approval_task_id
    assert not list(db_session.scalars(select(ApprovalDecision)))
    for key, version in v.items():
        db_session.refresh(version)
        assert (version.statement, version.content_hash) == snapshot[key]
        assert version.state is RequirementState.CANDIDATE

    # Gaps are the rule engine's: 14 expected - covered.
    gaps = {g.control_key for g in db_session.scalars(select(ComplianceGap))}
    assert "LO-PRV-CONSENT" in gaps  # its only claim was dropped
    assert "LO-RET-APPLICATION-RECORDS" not in gaps

    # The gates block the lifecycle; a human in the gate's role clears them. First the
    # analyst closes C01's P5 findings, so that only the P6 gates stand in the way.
    reviews = FindingReviewService(db_session, world.analyst)
    for open_finding in reviews.list_for_project(
        world.project_id, status=QualityFindingStatus.OPEN
    ):
        if open_finding.requirement_version_id == v["C01"].id:
            reviews.dismiss(world.project_id, open_finding.id, "reviewed (synthetic)")
    requirements = RequirementService(db_session, world.analyst)
    for target in (
        RequirementState.EXTRACTED,
        RequirementState.CLASSIFIED,
        RequirementState.ANALYZED,
    ):
        requirements.transition(project_id=world.project_id, version_id=v["C01"].id, target=target)
    with pytest.raises(StateTransitionError, match="blocking approval task"):
        requirements.transition(
            project_id=world.project_id, version_id=v["C01"].id, target=RequirementState.VALIDATED
        )
    with pytest.raises(AuthorizationError):
        ApprovalService(db_session, world.security_reviewer).decide(
            project_id=world.project_id,
            task_id=g2.id,
            decision=ApprovalDecisionType.APPROVE,
            role_exercised=Role.SECURITY_REVIEWER,
        )
    ApprovalService(db_session, world.compliance_officer).decide(
        project_id=world.project_id,
        task_id=g2.id,
        decision=ApprovalDecisionType.APPROVE,
        role_exercised=Role.COMPLIANCE_OFFICER,
        justification="interpretation reviewed (synthetic)",
    )
    with pytest.raises(StateTransitionError):  # G3 on the retention finding still open
        requirements.transition(
            project_id=world.project_id, version_id=v["C01"].id, target=RequirementState.VALIDATED
        )
    ApprovalService(db_session, world.security_reviewer).decide(
        project_id=world.project_id,
        task_id=ret.approval_task_id,
        decision=ApprovalDecisionType.APPROVE,
        role_exercised=Role.SECURITY_REVIEWER,
    )
    requirements.transition(
        project_id=world.project_id, version_id=v["C01"].id, target=RequirementState.VALIDATED
    )
    # Nothing became APPROVED or BASELINED automatically: that is G1, a later step.
    states = {x.state for x in db_session.scalars(select(RequirementVersion))}
    assert RequirementState.APPROVED not in states and RequirementState.BASELINED not in states

    # The audit chain is intact and references-only.
    assert AuditService(db_session).verify_project_chain(world.project_id) == (True, None)
    for event in db_session.scalars(select(AuditEvent)):
        blob = json.dumps(event.payload)
        assert not any(statement in blob for statement, _h in snapshot.values())
