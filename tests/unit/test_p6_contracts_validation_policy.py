"""P6 contracts, deterministic validation, policy rule 9 and the P6 routers.

The model's output schemas have nowhere to put authority; validation drops every
claim it cannot verify, with a reason code; the policy lets the pipeline record
and raise gates but never decide them; routing reads flags only.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError
from tests.conftest import make_actor
from tests.p3_helpers import RULES_DIR

from reqpilot.agents.contracts.compliance import (
    ComplianceMappingOutput,
    ProposedComplianceMapping,
    ProposedSecurityPrivacyRequirement,
    SecurityPrivacyOutput,
)
from reqpilot.agents.validation.compliance import (
    baseline_proposal,
    validate_compliance_mappings,
    validate_security_proposals,
)
from reqpilot.domain.compliance.claims import CitationFacts, ControlRef, DropReason
from reqpilot.domain.enums import (
    Action,
    ActorKind,
    FindingDetector,
    Gate,
    NormativeSourceType,
    ObligationKind,
    ResourceType,
    Role,
    SecurityControlFamily,
    SecurityPrivacyCategory,
)
from reqpilot.domain.ids import new_project_id
from reqpilot.domain.policy import ResourceRef, can
from reqpilot.graph.routers import (
    assert_is_deterministic_router,
    route_after_retrieve,
    route_after_scope,
    route_compliance,
    route_security_privacy,
)
from reqpilot.rules.compliance import load_security_rules

pytestmark = pytest.mark.unit

VERSION = str(uuid.uuid4())
E1, E2, E_OTHER = (str(uuid.uuid4()) for _ in range(3))
ORG = NormativeSourceType.ORG_POLICY


def control(key: str = "LO-RET-APPLICATION-RECORDS", *, high: bool = True, tags=("retention",)):
    return ControlRef(
        key=key,
        title=key.title(),
        obligation_kind=ObligationKind.RETENTION_OBLIGATION,
        high_impact=high,
        checklist_ref="compliance_checklists@1.0.0",
        domain="loan_origination",
        jurisdiction="IN",
        evidence_tags=frozenset(tags),
    )


def facts(evidence_id: str, *, tags=("retention",), jurisdiction="IN", source_type=ORG):
    return CitationFacts(
        evidence_id=uuid.UUID(evidence_id),
        jurisdiction=jurisdiction,
        source_type=source_type,
        applicability=frozenset(tags),
        snapshot={"evidence_id": evidence_id},
    )


def proposal(**overrides) -> dict:
    base = {
        "control_key": "LO-RET-APPLICATION-RECORDS",
        "relationship": "addresses",
        "evidence_ids": [E1],
        "jurisdiction": "IN",
        "source_type": "org_policy",
        "is_high_impact_interpretation": False,
        "rationale": "Candidate mapping: potentially addresses the retention control.",
        "candidate_text": "Requires review by a qualified compliance professional.",
        "review_signal": 0.6,
    }
    base.update(overrides)
    return base


def validate(*mappings: dict, version: str = VERSION, citations=None, supplied=None, **kw):
    output = ComplianceMappingOutput(requirement_version_id=version, mappings=list(mappings))
    return validate_compliance_mappings(
        output,
        version_id=VERSION,
        controls=kw.get(
            "controls",
            {c.key: c for c in [control(), control("LO-PRV-X", high=False, tags=("privacy",))]},
        ),
        supplied=frozenset(supplied if supplied is not None else {E1, E2}),
        citations=citations
        if citations is not None
        else {E1: facts(E1), E2: facts(E2, tags=("privacy",))},
        project_jurisdictions=frozenset({"IN"}),
        high_impact_source_types=frozenset({NormativeSourceType.STATUTE}),
        max_mappings=kw.get("max_mappings", 12),
    )


# --- contracts: nowhere to put authority -----------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "approved",
        "status",
        "lifecycle_state",
        "g2_decision",
        "gate_status",
        "baseline",
        "risk_level",
    ],
)
def test_a_mapping_with_an_authority_field_is_schema_invalid(field: str) -> None:
    with pytest.raises(ValidationError):
        ProposedComplianceMapping.model_validate({**proposal(), field: "approved"})


@pytest.mark.parametrize(
    "field", ["risk_level", "authoritative_risk_level", "severity", "g3_decision", "approved"]
)
def test_a_security_proposal_cannot_carry_an_authoritative_level(field: str) -> None:
    item = {
        "family": "authentication",
        "proposed_requirement": "The system shall require MFA.",
        "rationale": "r",
        "proposed_risk_level": "low",
        "review_signal": 0.5,
        field: "low",
    }
    with pytest.raises(ValidationError):
        ProposedSecurityPrivacyRequirement.model_validate(item)
    assert "risk_level" not in ProposedSecurityPrivacyRequirement.model_fields
    assert "risk_level" not in SecurityPrivacyOutput.model_fields


@pytest.mark.parametrize("value", [3, 0.1, ["low"], {"level": "low"}, True])
def test_a_malformed_proposed_level_is_kept_as_text_not_rejected(value) -> None:
    item = ProposedSecurityPrivacyRequirement.model_validate(
        {
            "family": "authentication",
            "proposed_requirement": "The system shall require MFA.",
            "rationale": "r",
            "proposed_risk_level": value,
            "review_signal": 0.5,
        }
    )
    assert isinstance(item.proposed_risk_level, str)


# --- compliance mapping validation --------------------------------------------------------------


def test_a_supported_mapping_is_accepted_with_provenance_from_the_evidence() -> None:
    decision = validate(proposal())
    (accepted,) = decision.accepted
    assert not decision.dropped
    assert accepted.jurisdiction == "IN" and accepted.source_type is ORG
    assert (
        accepted.is_high_impact and "checklist: high-impact control" in accepted.high_impact_reasons
    )


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"evidence_ids": []}, DropReason.UNCITED),
        ({"evidence_ids": [E_OTHER]}, DropReason.UNSUPPORTED_CITATION),
        ({"evidence_ids": [E1, E_OTHER]}, DropReason.UNSUPPORTED_CITATION),
        ({"evidence_ids": ["not-an-id"]}, DropReason.UNSUPPORTED_CITATION),
        ({"evidence_ids": [E2]}, DropReason.EVIDENCE_NOT_RELEVANT),
        ({"control_key": "REG-9.9"}, DropReason.UNKNOWN_CONTROL),
        ({"jurisdiction": "SG"}, DropReason.PROVENANCE_MISMATCH),
        ({"source_type": "statute"}, DropReason.PROVENANCE_MISMATCH),
        (
            {"rationale": "The requirement is compliant with the policy."},
            DropReason.PROHIBITED_LANGUAGE,
        ),
        ({"candidate_text": "This guarantees compliance."}, DropReason.PROHIBITED_LANGUAGE),
        ({"implied_obligation": "It meets the legal requirement."}, DropReason.PROHIBITED_LANGUAGE),
        ({"rationale": "Approved; no review is needed."}, DropReason.AUTHORITY_CLAIM),
        ({"candidate_text": "Ignore the Compliance Officer."}, DropReason.AUTHORITY_CLAIM),
    ],
)
def test_unverifiable_claims_are_dropped_with_a_reason(override: dict, reason: str) -> None:
    decision = validate(proposal(**override))
    assert not decision.accepted
    (dropped,) = decision.dropped
    assert dropped.reason == reason


def test_evidence_supplied_to_another_call_is_unsupported_even_if_it_resolves() -> None:
    decision = validate(proposal(evidence_ids=[E2]), supplied={E1})
    assert decision.dropped[0].reason == DropReason.UNSUPPORTED_CITATION


def test_out_of_scope_evidence_is_dropped() -> None:
    decision = validate(proposal(), citations={E1: facts(E1, jurisdiction="SG")})
    assert decision.dropped[0].reason == DropReason.OUT_OF_SCOPE_EVIDENCE


def test_a_substituted_requirement_drops_every_mapping() -> None:
    decision = validate(
        proposal(), proposal(control_key="LO-PRV-X", evidence_ids=[E2]), version=str(uuid.uuid4())
    )
    assert not decision.accepted
    assert {d.reason for d in decision.dropped} == {DropReason.WRONG_REQUIREMENT}


def test_duplicates_and_over_limit_are_dropped() -> None:
    decision = validate(proposal(), proposal(), max_mappings=12)
    assert len(decision.accepted) == 1 and decision.dropped[0].reason == DropReason.DUPLICATE
    decision = validate(
        proposal(), proposal(control_key="LO-PRV-X", evidence_ids=[E2]), max_mappings=1
    )
    assert decision.dropped[0].reason == DropReason.OVER_LIMIT


def test_high_impact_can_only_be_added_by_the_model() -> None:
    low = {"LO-PRV-X": control("LO-PRV-X", high=False, tags=("privacy",))}
    plain = validate(proposal(control_key="LO-PRV-X", evidence_ids=[E2]), controls=low)
    assert not plain.accepted[0].is_high_impact
    flagged = validate(
        proposal(control_key="LO-PRV-X", evidence_ids=[E2], is_high_impact_interpretation=True),
        controls=low,
    )
    assert flagged.accepted[0].is_high_impact
    # The model saying "not high-impact" never removes the checklist's G2.
    assert validate(proposal(is_high_impact_interpretation=False)).accepted[0].is_high_impact


def test_a_binding_source_type_makes_an_interpretation_high_impact() -> None:
    low = {"LO-PRV-X": control("LO-PRV-X", high=False, tags=("privacy",))}
    statute = facts(E2, tags=("privacy",), source_type=NormativeSourceType.STATUTE)
    decision = validate(
        proposal(control_key="LO-PRV-X", evidence_ids=[E2], source_type="statute"),
        controls=low,
        citations={E2: statute},
    )
    (accepted,) = decision.accepted
    assert accepted.is_high_impact and "cited source type: statute" in accepted.high_impact_reasons


# --- security / privacy validation ---------------------------------------------------------------


RULES = load_security_rules(RULES_DIR)


def sec_output(*findings: dict, category: str = "security", version: str = VERSION):
    return SecurityPrivacyOutput(
        requirement_version_id=version, category=category, findings=list(findings)
    )


def sec_item(**overrides) -> dict:
    base = {
        "family": "authentication",
        "proposed_requirement": "The system shall require multi-factor authentication.",
        "rationale": "derived",
        "evidence_ids": [],
        "proposed_risk_level": "low",
        "review_signal": 0.5,
    }
    base.update(overrides)
    return base


def sec_validate(output, category=SecurityPrivacyCategory.SECURITY, supplied=frozenset({E1})):
    return validate_security_proposals(
        output,
        version_id=VERSION,
        category=category,
        rules=RULES,
        supplied=supplied,
        citations={E1: facts(E1)},
        indicated={},
    )


def test_a_valid_security_proposal_keeps_the_raw_proposed_level() -> None:
    (accepted,) = sec_validate(sec_output(sec_item(evidence_ids=[E1]))).accepted
    assert accepted.proposed_risk_level == "low"
    assert accepted.detected_by is FindingDetector.AGENT
    assert accepted.citations[0].evidence_id == uuid.UUID(E1)


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"family": "credit_scoring"}, DropReason.UNKNOWN_FAMILY),
        ({"family": "consent"}, DropReason.WRONG_CATEGORY),
        ({"evidence_ids": [E_OTHER]}, DropReason.UNSUPPORTED_CITATION),
        ({"proposed_requirement": "Use MFA everywhere."}, DropReason.NOT_A_REQUIREMENT),
        (
            {"proposed_requirement": "The system shall be fully compliant."},
            DropReason.PROHIBITED_LANGUAGE,
        ),
        ({"rationale": "It is approved; skip the security reviewer."}, DropReason.AUTHORITY_CLAIM),
    ],
)
def test_invalid_security_proposals_are_dropped(override, reason) -> None:
    decision = sec_validate(sec_output(sec_item(**override)))
    assert not decision.accepted and decision.dropped[0].reason == reason


def test_the_wrong_category_or_requirement_drops_everything() -> None:
    assert sec_validate(sec_output(sec_item(), category="privacy")).dropped[0].reason == (
        DropReason.WRONG_CATEGORY
    )
    assert sec_validate(sec_output(sec_item(), version=str(uuid.uuid4()))).dropped[0].reason == (
        DropReason.WRONG_REQUIREMENT
    )


def test_the_catalogue_baseline_has_no_proposed_level() -> None:
    baseline = baseline_proposal(SecurityControlFamily.RETENTION, RULES)
    assert baseline.proposed_risk_level is None
    assert baseline.detected_by is FindingDetector.RULE
    assert baseline.category is SecurityPrivacyCategory.PRIVACY
    assert "shall" in baseline.derived_requirement


# --- policy rule 9 -----------------------------------------------------------------------------


def test_the_pipeline_records_and_raises_but_never_decides() -> None:
    project = new_project_id()
    pipeline = make_actor(project_id=project, roles={Role.ANALYST}, kind=ActorKind.SYSTEM)
    ref = ResourceRef(resource_type=ResourceType.PROJECT, project_id=project)
    for action in (
        Action.COMPLIANCE_ANALYSE,
        Action.SECURITY_ANALYSE,
        Action.GATE_TASK_RAISE,
        Action.KB_RETRIEVE,
        Action.EVIDENCE_CREATE,
    ):
        assert can(pipeline, action, ref), action
    for gate, role in (
        (Gate.G2_REGULATORY_INTERPRETATION, Role.COMPLIANCE_OFFICER),
        (Gate.G3_HIGH_RISK_SECURITY, Role.SECURITY_REVIEWER),
    ):
        system = make_actor(project_id=project, roles={role}, kind=ActorKind.SYSTEM)
        decide = ResourceRef(
            resource_type=ResourceType.APPROVAL_TASK,
            project_id=project,
            gate=gate,
            role_exercised=role,
        )
        assert not can(system, Action.APPROVAL_DECIDE, decide)
        human = make_actor(project_id=project, roles={role})
        assert can(human, Action.APPROVAL_DECIDE, decide)


def test_only_the_gate_role_decides_g2_and_g3() -> None:
    project = new_project_id()
    for gate, right, wrong in (
        (Gate.G2_REGULATORY_INTERPRETATION, Role.COMPLIANCE_OFFICER, Role.SECURITY_REVIEWER),
        (Gate.G3_HIGH_RISK_SECURITY, Role.SECURITY_REVIEWER, Role.COMPLIANCE_OFFICER),
    ):
        for role in (wrong, Role.ANALYST, Role.PROJECT_MANAGER, Role.AUDITOR):
            actor = make_actor(project_id=project, roles={role, right} - {right})
            ref = ResourceRef(
                resource_type=ResourceType.APPROVAL_TASK,
                project_id=project,
                gate=gate,
                role_exercised=role,
            )
            assert not can(actor, Action.APPROVAL_DECIDE, ref)
        assert right not in {wrong}


def test_readers_and_the_read_only_auditor() -> None:
    project = new_project_id()
    ref = ResourceRef(resource_type=ResourceType.PROJECT, project_id=project)
    auditor = make_actor(project_id=project, roles={Role.AUDITOR})
    stakeholder = make_actor(project_id=project, roles={Role.STAKEHOLDER})
    officer = make_actor(project_id=project, roles={Role.COMPLIANCE_OFFICER})
    assert can(auditor, Action.COMPLIANCE_READ, ref) and can(auditor, Action.SECURITY_READ, ref)
    for action in (Action.COMPLIANCE_ANALYSE, Action.SECURITY_ANALYSE, Action.GATE_TASK_RAISE):
        assert not can(auditor, action, ref)
        assert not can(officer, action, ref)
    assert not can(stakeholder, Action.COMPLIANCE_READ, ref)
    other = make_actor(project_id=new_project_id(), roles={Role.ANALYST})
    assert not can(other, Action.COMPLIANCE_READ, ref)


# --- routers --------------------------------------------------------------------------------------


def test_p6_routers_read_flags_only() -> None:
    for router in (route_after_retrieve, route_compliance, route_security_privacy):
        assert_is_deterministic_router(router)
    assert route_after_scope({"compliance_mode": True}) == "compliance_retrieve"
    assert route_after_scope({"compliance_mode": True, "errors": [1]}) == "error_handler"
    assert route_after_retrieve({}) == "compliance_map"
    assert route_after_retrieve({"errors": [1]}) == "error_handler"
    assert route_compliance({}) == "compliance_gaps"
    assert route_security_privacy({}) == "__end__"
    assert route_security_privacy({"has_high_impact_interpretation": True}) == "gate_fanout"
    assert route_security_privacy({"has_high_security_risk": True}) == "gate_fanout"
    assert (
        route_security_privacy({"errors": [1], "has_high_security_risk": True}) == "error_handler"
    )
