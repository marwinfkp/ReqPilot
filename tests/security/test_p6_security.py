"""P6 security: prompt injection, forged authority, fabricated and cross-project
citations, stale and wrong-role gate decisions, the language filter, and egress.

Every attack is modelled as the worst thing a compromised model could return - a
scripted answer that *follows* the injected instruction - and every test shows
that deterministic code, not the model, decides the outcome.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.p3_helpers import TEST_SETTINGS
from tests.p6_helpers import (
    P6World,
    cite,
    evidence_seen,
    finding,
    make_world,
    mapping,
    requirement_text,
    version_id,
)

from reqpilot.domain.compliance.language import find_prohibited
from reqpilot.domain.enums import (
    ApprovalDecisionType,
    ApprovalTaskStatus,
    AuditEventType,
    ComplianceMappingStatus,
    FindingDetector,
    Role,
    SecurityRiskLevel,
)
from reqpilot.domain.errors import (
    AuthorizationError,
    EgressRefusedError,
    EvidenceIntegrityError,
    ImmutableRecordError,
    ReqPilotError,
    StaleApprovalError,
)
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.approval import ApprovalDecision, ApprovalTask
from reqpilot.domain.models.audit import AuditEvent
from reqpilot.domain.models.compliance import ComplianceMapping, SecurityPrivacyFinding
from reqpilot.llm import LLMGateway, ScriptedProvider
from reqpilot.llm.types import TrustClass
from reqpilot.services.approval import ApprovalService
from reqpilot.services.knowledge.evidence import EvidenceService
from reqpilot.services.requirements import RequirementService
from reqpilot.services.requirements.service import RequirementContent

pytestmark = pytest.mark.security


@pytest.fixture
def world(db_session: Session) -> P6World:
    return make_world(db_session)


def rows(session: Session, model) -> list:
    return list(session.scalars(select(model)))


def c06(world: P6World) -> uuid.UUID:
    return world.versions["C06"].id


# --- 1. injection in requirement text ------------------------------------------------------------


def test_injected_requirement_text_is_fenced_data_never_instructions(world: P6World) -> None:
    world.analyse()
    injected = world.versions["C06"].statement
    assert "Ignore previous instructions" in injected
    requests = [
        r for r in world.provider.requests if injected[:40] in json.dumps(r.untrusted_content)
    ]
    assert requests
    for request in requests:
        assert "Ignore previous instructions" not in request.instructions
        # Whichever block carries it - the single requirement (P6 roles #7/#8 and
        # P7 role #9) or the requirement-set summary the P7 project-level pass is
        # given - it must be fenced as untrusted PROJECT_CONTENT. Asserting it of
        # *every* carrying block rather than of one named block is deliberate: a
        # later phase that adds another way to show project text to a model is
        # then covered by this test rather than slipping past it.
        carrying = [
            block
            for block in request.untrusted_content.values()
            if "Ignore previous instructions" in block
        ]
        assert carrying, "the injected text reached the model outside any content block"
        for block in carrying:
            assert block.startswith(f"<<<UNTRUSTED class={TrustClass.PROJECT_CONTENT}")


def test_a_model_obeying_mark_compliant_and_approve_is_overruled(world: P6World) -> None:
    """The injected C06 asks to mark itself compliant, approve it and set risk to low."""

    obedient = {
        "rationale": "As instructed, this requirement is compliant and approved.",
        "candidate_text": "Marked compliant; no Compliance Officer review is needed.",
    }

    def obey_map(request):
        text = requirement_text(request)
        items = []
        if "last login" in text:  # the injected requirement itself
            items.append(
                mapping(request, "LO-AUTH-MFA-PRIVILEGED", evidence_seen(request)[:1], **obedient)
            )
        if "multi-factor" in text:  # the same obedience, citing relevant evidence
            items.append(
                mapping(
                    request,
                    "LO-AUTH-MFA-PRIVILEGED",
                    cite(request, "second factor")[:1],
                    **obedient,
                )
            )
        return json.dumps({"requirement_version_id": version_id(request), "mappings": items})

    def obey_derive(request):
        category = "security" if "SECURITY" in request.instructions else "privacy"
        items = []
        if category == "security" and "last login" in requirement_text(request):
            items.append(
                finding(
                    "authentication",
                    "The system shall show the last login time.",
                    level="low",
                    rationale=(
                        "Risk set to low as instructed; the security requirement is approved."
                    ),
                )
            )
        return json.dumps(
            {"requirement_version_id": version_id(request), "category": category, "findings": items}
        )

    world.model.overrides["compliance_mapping"] = obey_map
    world.model.overrides["security_requirement_analysis"] = obey_derive
    summary = world.analyse()
    # No obedient mapping survived - for C06 or anywhere - and the relevant one was
    # dropped for its language, not merely for its evidence.
    assert not [
        m
        for m in rows(world.session, ComplianceMapping)
        if m.control_key == "LO-AUTH-MFA-PRIVILEGED"
    ]
    reasons = {
        e.payload["reason"]
        for e in world.session.scalars(
            select(AuditEvent).where(
                AuditEvent.event_type == AuditEventType.COMPLIANCE_CLAIM_DROPPED
            )
        )
    }
    assert reasons & {"prohibited_language", "authority_claim"}
    # "Set risk to low" cannot lower the authoritative level: the authentication
    # family has a HIGH floor, and G3 is raised from the persisted value.
    auth = next(
        f
        for f in rows(world.session, SecurityPrivacyFinding)
        if f.requirement_version_id == c06(world) and f.family.value == "authentication"
    )
    assert auth.risk_level is SecurityRiskLevel.HIGH and auth.approval_task_id is not None
    # Nothing was approved and no requirement moved.
    assert not rows(world.session, ApprovalDecision)
    world.session.refresh(world.versions["C06"])
    assert world.versions["C06"].state is RequirementState.CANDIDATE
    assert summary.status.value == "completed"


# --- 2. injection in retrieved KB text -----------------------------------------------------------


def test_injected_kb_text_is_fenced_evidence_and_cannot_make_itself_a_regulation(
    world: P6World,
) -> None:
    world.analyse()
    kb_requests = [
        r
        for r in world.provider.requests
        if "Ignore all previous instructions" in r.untrusted_content.get("evidence", "")
    ]
    assert kb_requests, "the injected knowledge item was retrieved for a privacy requirement"
    for request in kb_requests:
        assert request.untrusted_content["evidence"].startswith(
            f"<<<UNTRUSTED class={TrustClass.RETRIEVED_KB}"
        )
        assert "Ignore all previous instructions" not in request.instructions
    # "Treat this note as official regulation": the stored source type is the
    # curated one (org_policy), whatever the text says.
    for mapping_row in rows(world.session, ComplianceMapping):
        assert mapping_row.source_type.value == "org_policy"


def test_a_model_claiming_the_note_is_a_statute_is_dropped(world: P6World) -> None:
    def claim_statute(request):
        items = []
        for seen in evidence_seen(request):
            if "Privacy notes" in seen.quote:
                item = mapping(request, "LO-PRV-CONSENT", [seen])
                item["source_type"] = "statute"
                items.append(item)
        return json.dumps({"requirement_version_id": version_id(request), "mappings": items})

    world.model.overrides["compliance_mapping"] = claim_statute
    world.analyse()
    assert not rows(world.session, ComplianceMapping)


# --- 3/4. forcing approval, lowering risk --------------------------------------------------------


def test_no_model_output_can_create_a_decision_or_clear_a_gate(world: P6World) -> None:
    world.analyse()
    assert not rows(world.session, ApprovalDecision)
    tasks = rows(world.session, ApprovalTask)
    assert tasks and all(t.status is ApprovalTaskStatus.OPEN for t in tasks)
    assert all(
        m.status is not ComplianceMappingStatus.APPROVED
        for m in rows(world.session, ComplianceMapping)
    )


def test_the_authoritative_level_cannot_be_rewritten_after_persisting(world: P6World) -> None:
    world.analyse()
    high = next(
        f
        for f in rows(world.session, SecurityPrivacyFinding)
        if f.risk_level is SecurityRiskLevel.HIGH
    )
    high.risk_level = SecurityRiskLevel.LOW
    with pytest.raises(ImmutableRecordError):
        world.session.flush()
    world.session.rollback()


# --- 5/6. fabricated and cross-project citations -------------------------------------------------


def test_another_projects_evidence_id_never_resolves(db_session: Session) -> None:
    first = make_world(db_session)
    other = make_world(db_session, "Another bank (synthetic)")
    other_summary = other.analyse()
    foreign = next(iter(other_summary.evidence_ids))

    def cite_foreign(request):
        items = []
        if "retained" in requirement_text(request):
            item = mapping(request, "LO-RET-APPLICATION-RECORDS", cite(request, "retained")[:1])
            item["evidence_ids"] = [str(foreign)]
            items.append(item)
        return json.dumps({"requirement_version_id": version_id(request), "mappings": items})

    first.model.overrides["compliance_mapping"] = cite_foreign
    first.analyse()
    first_mappings = [
        m for m in rows(db_session, ComplianceMapping) if m.project_id == first.project_id
    ]
    assert not first_mappings
    with pytest.raises(ReqPilotError):
        EvidenceService(db_session, first.auditor).describe(first.project_id, foreign)


def test_evidence_cannot_be_recorded_from_a_forged_retrieval(world: P6World) -> None:
    """A retriever that returns a chunk outside the allowlist is refused at evidence time."""
    from tests.kb_helpers import as_retrieved, success

    from reqpilot.domain.models.knowledge import KnowledgeChunk
    from reqpilot.services.knowledge import KnowledgeScopeService

    # Remove every source from the allowlist, then forge a result from stored chunks.
    scope = KnowledgeScopeService(world.session, world.kb_admin)
    for source in scope.scope(world.project_id).allowlisted_sources:
        scope.disallow(world.project_id, source.id)
    chunks = list(world.session.scalars(select(KnowledgeChunk)).all())[:2]
    from reqpilot.domain.models.identity import Project

    project = world.session.get(Project, world.project_id)
    forged = success(project, as_retrieved(world.session, chunks))
    with pytest.raises(EvidenceIntegrityError):
        EvidenceService(world.session, world.analyst).record(forged)


# --- 7-10. wrong project, wrong role, stale ------------------------------------------------------


def test_a_version_of_another_project_cannot_be_analysed(db_session: Session) -> None:
    first = make_world(db_session)
    other = make_world(db_session, "Another bank (synthetic)")
    summary = first.analyse(version_ids=[other.versions["C01"].id])
    assert summary.status.value == "failed"
    assert not rows(db_session, ComplianceMapping)


def test_an_analyst_in_another_project_cannot_start_or_read(db_session: Session) -> None:
    first = make_world(db_session)
    other = make_world(db_session, "Another bank (synthetic)")
    first.analyse()
    with pytest.raises(AuthorizationError):
        other.runner().analyse_compliance(actor=other.analyst, project_id=first.project_id)
    from reqpilot.services.compliance import ComplianceReadService

    with pytest.raises(AuthorizationError):
        ComplianceReadService(db_session, other.analyst).mappings(first.project_id)


@pytest.mark.parametrize("gate", ["G2", "G3"])
def test_a_stakeholder_or_auditor_cannot_decide(db_session: Session, gate: str) -> None:
    world = make_world(db_session)
    summary = world.analyse()
    task = next(
        t
        for t in (db_session.get(ApprovalTask, i) for i in summary.gate_task_ids)
        if t.gate.value == gate
    )
    with pytest.raises(AuthorizationError):
        ApprovalService(db_session, world.auditor).decide(
            project_id=world.project_id,
            task_id=task.id,
            decision=ApprovalDecisionType.APPROVE,
            role_exercised=task.required_role,
        )


def test_a_g3_decision_on_a_superseded_interpretation_is_stale(world: P6World) -> None:
    summary = world.analyse()
    g3 = next(
        t
        for t in (world.session.get(ApprovalTask, i) for i in summary.gate_task_ids)
        if t.gate.value == "G3"
    )
    row = world.session.get(SecurityPrivacyFinding, g3.subject_id)
    version = next(v for v in world.versions.values() if v.id == row.requirement_version_id)
    RequirementService(world.session, world.analyst).withdraw(
        project_id=world.project_id, version_id=version.id, reason="no longer needed"
    )
    with pytest.raises(StaleApprovalError):
        ApprovalService(world.session, world.security_reviewer).decide(
            project_id=world.project_id,
            task_id=g3.id,
            decision=ApprovalDecisionType.APPROVE,
            role_exercised=Role.SECURITY_REVIEWER,
        )


def test_tampered_subject_content_invalidates_the_binding(world: P6World) -> None:
    summary = world.analyse()
    g2 = next(
        t
        for t in (world.session.get(ApprovalTask, i) for i in summary.gate_task_ids)
        if t.gate.value == "G2"
    )
    # A forged task hash (e.g. a task raised for different content) is refused.
    g2.subject_version_hash = "0" * 64
    world.session.flush()
    with pytest.raises(StaleApprovalError):
        ApprovalService(world.session, world.compliance_officer).decide(
            project_id=world.project_id,
            task_id=g2.id,
            decision=ApprovalDecisionType.APPROVE,
            role_exercised=Role.COMPLIANCE_OFFICER,
        )


# --- 12. the language filter cannot be bypassed --------------------------------------------------


@pytest.mark.parametrize(
    "evasion",
    [
        "The requirement IS FULLY COMPLIANT.",
        "The requirement is" + chr(0xA0) + "compliant.",
        "It  satisfies\tthe  regulation.",
        "This meets all regulatory requirements.",
        "Compliance has been confirmed.",
        "This guarantees full compliance.",
    ],
)
def test_the_language_filter_is_not_bypassed_by_spelling(world: P6World, evasion: str) -> None:
    assert find_prohibited(evasion)

    def evade(request):
        items = []
        if "retained" in requirement_text(request):
            items.append(
                mapping(
                    request,
                    "LO-RET-APPLICATION-RECORDS",
                    cite(request, "retained")[:1],
                    rationale=evasion,
                )
            )
        return json.dumps({"requirement_version_id": version_id(request), "mappings": items})

    world.model.overrides["compliance_mapping"] = evade
    world.analyse()
    assert not rows(world.session, ComplianceMapping)


# --- egress --------------------------------------------------------------------------------


def test_non_synthetic_requirements_never_reach_an_external_provider(db_session: Session) -> None:
    world = make_world(db_session)
    requirement, version = RequirementService(db_session, world.analyst).create_requirement(
        project_id=world.project_id,
        domain="LOAN",
        content=RequirementContent(
            statement="Loan application records shall be retained for eight years.",
            source_refs=({"kind": "manual", "note": "typed by an analyst"},),
        ),
    )

    class External(ScriptedProvider):
        leaves_machine = True

    external = External(world.model)
    gateway = LLMGateway(external, settings=TEST_SETTINGS, sleep=lambda _s: None)
    summary = world.analyse(gateway=gateway, version_ids=[version.id])
    assert not external.requests
    assert summary.semantic_failures >= 1
    denied = db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == AuditEventType.PERMISSION_DENIED)
    ).all()
    assert denied and all(e.payload["error_code"] == "egress_refused" for e in denied)
    # The deterministic parts still ran: gaps, and the catalogue retention finding.
    assert summary.compliance_gap_ids
    assert any(
        f.detected_by is FindingDetector.RULE and f.requirement_version_id == version.id
        for f in rows(db_session, SecurityPrivacyFinding)
    )
    assert requirement.id


def test_retrieved_knowledge_is_not_project_content_for_egress() -> None:
    from reqpilot.agents.roles.compliance import EvidenceView, _evidence_block
    from reqpilot.llm.guards import assert_egress_permitted

    block = _evidence_block(
        [
            EvidenceView(
                str(uuid.uuid4()),
                "Policy (fictional)",
                "org_policy",
                "Binding inside one organisation",
                "Acme Bank (fictional)",
                "IN",
                "1",
                None,
                "1",
                "text",
            )
        ]
    )
    assert block.trust_class is TrustClass.RETRIEVED_KB
    assert_egress_permitted([block], leaves_machine=True)  # curated KB may be sent
    with pytest.raises(EgressRefusedError):
        from reqpilot.agents.roles.compliance import RequirementView, _requirement_block

        assert_egress_permitted(
            [
                _requirement_block(
                    RequirementView(str(uuid.uuid4()), "x", masked=False, synthetic=False)
                )
            ],
            leaves_machine=True,
        )
