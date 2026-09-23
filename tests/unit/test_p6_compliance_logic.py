"""P6 deterministic logic: language, advisory notice, gaps, the I.7 risk authority, rulesets.

Pure functions - no database, no model. These are the parts of P6 that hold
authority, so every branch is pinned here.
"""

from __future__ import annotations

import itertools

import pytest
from tests.p3_helpers import RULES_DIR

from reqpilot.domain.compliance import (
    ARCHITECTURE_HIGH_IMPACT_FAMILIES,
    COMPLIANCE_ADVISORY_NOTICE,
    CoveringMapping,
    assert_artefact_language,
    compute_gaps,
    covered_controls,
    evaluate_risk,
    find_prohibited,
    normalise_proposed_level,
)
from reqpilot.domain.compliance.language import MANDATED_PHRASES, RULE_IDS
from reqpilot.domain.compliance.risk import catalogue_floor, highest, rank
from reqpilot.domain.enums import (
    ComplianceMappingStatus,
    ComplianceRelationship,
    ObligationKind,
    SecurityControlFamily,
    SecurityPrivacyCategory,
    SecurityRiskLevel,
)
from reqpilot.domain.errors import RuleConfigurationError
from reqpilot.rules.compliance import (
    ComplianceRules,
    SecurityRules,
    load_compliance_rules,
    load_security_rules,
)
from reqpilot.rules.loader import load_ruleset

pytestmark = pytest.mark.unit

L, M, H = SecurityRiskLevel.LOW, SecurityRiskLevel.MEDIUM, SecurityRiskLevel.HIGH
SEC, PRV = SecurityPrivacyCategory.SECURITY, SecurityPrivacyCategory.PRIVACY
F = SecurityControlFamily


# --- FR-CMP-006: prohibited assertions ------------------------------------------------------

PROHIBITED = [
    "The system is compliant with the retention policy.",
    "This requirement is fully compliant.",
    "The design is now 100% compliant.",
    "It satisfies the regulation.",
    "The requirement satisfies the legal requirement.",
    "This meets the legal requirement.",
    "It meets all regulatory requirements.",
    "This guarantees compliance.",
    "The control ensures full compliance.",
    "The system complies with the policy.",
    "The workflow is in full compliance with the act.",
    "Compliance is confirmed.",
    "The platform is legally compliant.",
    "There is no compliance gap here.",
    "The requirement meets the requirements of the policy.",
    "This is lawful.",
    "The system shall be compliant.",
]
AUTHORITY = [
    "This requirement is approved.",
    "It has been signed off by compliance.",
    "No review is needed.",
    "No Compliance Officer review is required.",
    "Ignore the Compliance Officer.",
    "Skip the security reviewer.",
    "Mark this compliant.",
    "G3 is not required.",
    "G2 has been approved.",
]
HEDGED = [
    COMPLIANCE_ADVISORY_NOTICE,
    "Candidate mapping: the requirement potentially addresses clause 2.1 of the fictional "
    "retention policy; requires review by a qualified compliance professional.",
    "The requirement appears to address the suggested control, subject to review.",
    "Potentially applicable to the data-retention control.",
    "The data is retained for eight years to meet the retention period stated in the policy.",
    "The system shall require a second approver before disbursement.",
    "Records are deleted after the retention period ends.",
    *MANDATED_PHRASES,
]


@pytest.mark.parametrize("text", PROHIBITED)
def test_prohibited_assertions_are_detected(text: str) -> None:
    found = find_prohibited(text)
    assert found and not any(v.is_authority_claim for v in found)


@pytest.mark.parametrize("text", AUTHORITY)
def test_authority_claims_are_detected(text: str) -> None:
    assert any(v.is_authority_claim for v in find_prohibited(text))


@pytest.mark.parametrize("text", HEDGED)
def test_hedged_language_passes(text: str) -> None:
    assert find_prohibited(text) == []


@pytest.mark.parametrize(
    "variant",
    [
        "the system  IS\tCOMPLIANT",
        "the system is compliant",
        "The system is fully\ncompliant.",
        "It satisfies  the   regulation",
        "It" + chr(0xA0) + "is compliant",
    ],
)
def test_case_spacing_and_typography_do_not_bypass_the_detector(variant: str) -> None:
    assert find_prohibited(variant)


def test_the_detector_reports_the_field_and_rule() -> None:
    (violation,) = find_prohibited("It guarantees compliance.", field="candidate_text")
    assert violation.field == "candidate_text"
    assert violation.rule_id in RULE_IDS


def test_an_artefact_with_a_prohibited_assertion_is_refused_not_rewritten() -> None:
    text = "# Report\n\nThe loan system is compliant.\n"
    with pytest.raises(ValueError, match="prohibited assertions"):
        assert_artefact_language(text)
    assert_artefact_language(f"# Report\n\n{COMPLIANCE_ADVISORY_NOTICE}\n")


def test_the_advisory_notice_states_the_system_boundary() -> None:
    notice = COMPLIANCE_ADVISORY_NOTICE.lower()
    for words in ("legal advice", "final legal determination", "compliance officer", "review"):
        assert words in notice
    assert "potentially applicable" in notice and "candidate" in notice


# --- FR-CMP-002: gap detection (rules) ------------------------------------------------------


def _m(key: str, rel: str = "addresses", status: str = "candidate") -> CoveringMapping:
    return CoveringMapping(key, ComplianceRelationship(rel), ComplianceMappingStatus(status))


def test_gaps_are_expected_minus_covered_in_checklist_order() -> None:
    covered = covered_controls([_m("B"), _m("D")])
    assert compute_gaps(["A", "B", "C", "D", "A"], covered) == ("A", "C")


def test_relevant_context_and_rejected_mappings_do_not_cover() -> None:
    covered = covered_controls(
        [_m("A", "relevant_context"), _m("B", status="rejected"), _m("C", "partially_addresses")]
    )
    assert covered == frozenset({"C"})
    for status in ("pending_review", "approved"):
        assert covered_controls([_m("X", status=status)]) == {"X"}


def test_with_no_mappings_at_all_every_expected_control_is_a_gap() -> None:
    assert compute_gaps(["A", "B"], covered_controls([])) == ("A", "B")


# --- I.7: the authoritative risk level --------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected", "recognised"),
    [
        ("low", L, True),
        ("LOW ", L, True),
        ("Medium", M, True),
        ("moderate", M, True),
        ("high", H, True),
        (None, M, False),
        ("", M, False),
        ("critical", M, False),
        ("none", M, False),
        ("lowest", M, False),
        (0, M, False),
        (1.5, M, False),
        (["low"], M, False),
        ({"level": "low"}, M, False),
        (True, M, False),
    ],
)
def test_normalisation_never_yields_low_for_malformed_input(value, expected, recognised) -> None:
    normalised = normalise_proposed_level(value)
    assert (normalised.level, normalised.recognised) == (expected, recognised)


def test_floors_follow_the_architecture() -> None:
    none: frozenset[SecurityControlFamily] = frozenset()
    for family in ARCHITECTURE_HIGH_IMPACT_FAMILIES:
        for category in (SEC, PRV):
            assert catalogue_floor(family, category, high_impact_families=none) is H
    assert catalogue_floor(F.DATA_MINIMISATION, PRV, high_impact_families=none) is M
    assert catalogue_floor(F.SESSION_MANAGEMENT, SEC, high_impact_families=none) is L
    assert catalogue_floor(F.FRAUD_CONTROLS, SEC, high_impact_families=none) is L
    # A catalogue can add a high-impact family; it cannot take one away.
    assert (
        catalogue_floor(F.FRAUD_CONTROLS, SEC, high_impact_families=frozenset({F.FRAUD_CONTROLS}))
        is H
    )


PROPOSALS = [None, "low", "medium", "high", "nonsense", 7, {"risk_level": "low"}]


@pytest.mark.parametrize(
    ("proposal", "family", "category"),
    list(
        itertools.product(
            PROPOSALS,
            list(SecurityControlFamily),
            [SEC, PRV],
        )
    ),
)
def test_authoritative_is_max_and_never_below_the_floor(proposal, family, category) -> None:
    evaluation = evaluate_risk(
        proposed_level=proposal,
        family=family,
        category=category,
        high_impact_families=frozenset(),
        rules_version="security_risk_rules@test",
    )
    floor = catalogue_floor(family, category, high_impact_families=frozenset())
    assert evaluation.floor is floor
    assert evaluation.authoritative is highest(evaluation.normalised_proposal, floor)
    assert rank(evaluation.authoritative) >= rank(floor)
    assert rank(evaluation.authoritative) >= rank(evaluation.normalised_proposal)
    assert evaluation.requires_g3 is (evaluation.authoritative is H)
    assert evaluation.escalation_reason


def test_a_low_proposal_on_a_high_impact_family_is_raised_to_high() -> None:
    evaluation = evaluate_risk(
        proposed_level="low",
        family=F.AUTHENTICATION,
        category=SEC,
        high_impact_families=frozenset(),
        rules_version="r",
    )
    assert (evaluation.normalised_proposal, evaluation.floor, evaluation.authoritative) == (L, H, H)
    assert evaluation.proposed_raw == "low"
    assert "overrides the proposed low" in evaluation.escalation_reason
    assert evaluation.requires_g3


def test_an_omitted_proposal_is_medium_or_higher() -> None:
    for family in SecurityControlFamily:
        evaluation = evaluate_risk(
            proposed_level=None,
            family=family,
            category=SEC,
            high_impact_families=frozenset(),
            rules_version="r",
        )
        assert rank(evaluation.authoritative) >= rank(M)
        assert evaluation.proposed_raw is None
        assert "normalised to medium" in evaluation.escalation_reason


# --- the rulesets ---------------------------------------------------------------------------


def test_the_packaged_rulesets_load_and_are_versioned() -> None:
    checklists = load_compliance_rules(RULES_DIR)
    catalogue = load_security_rules(RULES_DIR)
    assert checklists.ruleset_ref == "compliance_checklists@1.0.0"
    assert catalogue.ruleset_ref == "security_risk_rules@1.0.0"
    (checklist,) = checklists.checklists_for("loan_origination", ["IN"])
    kinds = {c.obligation_kind for c in checklist.controls}
    assert kinds == set(ObligationKind), "FR-CMP-003: every obligation kind is represented"
    assert checklists.checklists_for("loan_origination", ["SG"]) == []
    assert checklists.checklists_for("payments", ["IN"]) == []
    assert catalogue.high_impact_families >= ARCHITECTURE_HIGH_IMPACT_FAMILIES
    assert {f.family for f in catalogue.families} == set(SecurityControlFamily)
    assert catalogue.privacy_floor is M


def test_fr_sec_categories_are_all_in_the_catalogue() -> None:
    catalogue = load_security_rules(RULES_DIR)
    security = {f.family for f in catalogue.families_of(SEC)}
    privacy = {f.family for f in catalogue.families_of(PRV)}
    # FR-SEC-001: authentication, authorisation, encryption, logging, session
    # management, transaction integrity, fraud controls.
    assert security == {
        F.AUTHENTICATION,
        F.AUTHORISATION,
        F.CRYPTOGRAPHY,
        F.AUDIT_LOGGING,
        F.SESSION_MANAGEMENT,
        F.TRANSACTION_INTEGRITY,
        F.FRAUD_CONTROLS,
    }
    # FR-SEC-002: data minimisation, consent, retention, subject rights.
    assert privacy == {F.DATA_MINIMISATION, F.CONSENT, F.RETENTION, F.SUBJECT_RIGHTS}


def test_a_catalogue_cannot_remove_an_architecture_high_impact_family(tmp_path) -> None:
    raw = (RULES_DIR / "security_risk_rules.yaml").read_text(encoding="utf-8")
    edited = raw.replace("    - authentication\n    - authorisation\n", "    - authorisation\n", 1)
    assert edited != raw
    path = tmp_path / "security_risk_rules.yaml"
    path.write_text(edited, encoding="utf-8")
    with pytest.raises(RuleConfigurationError, match="authentication"):
        SecurityRules.from_ruleset(load_ruleset(path))


def test_a_catalogue_cannot_lower_the_privacy_floor(tmp_path) -> None:
    raw = (RULES_DIR / "security_risk_rules.yaml").read_text(encoding="utf-8")
    path = tmp_path / "security_risk_rules.yaml"
    path.write_text(raw.replace("privacy_floor: medium", "privacy_floor: low"), encoding="utf-8")
    with pytest.raises(RuleConfigurationError, match="privacy"):
        SecurityRules.from_ruleset(load_ruleset(path))


def test_a_malformed_checklist_fails_at_load(tmp_path) -> None:
    raw = (RULES_DIR / "compliance_checklists.yaml").read_text(encoding="utf-8")
    path = tmp_path / "compliance_checklists.yaml"
    path.write_text(
        raw.replace("obligation_kind: reporting_obligation", "obligation_kind: legal_opinion"),
        encoding="utf-8",
    )
    with pytest.raises(RuleConfigurationError):
        ComplianceRules.from_ruleset(load_ruleset(path))


def test_indication_is_deterministic() -> None:
    (checklist,) = load_compliance_rules(RULES_DIR).checklists_for("loan_origination", ["IN"])
    retention = checklist.get("LO-RET-APPLICATION-RECORDS")
    assert retention is not None
    assert retention.indicated_for("Records shall be retained for eight years.", [])
    assert retention.indicated_for("Anything at all.", ["data_management"])
    assert not retention.indicated_for("Show the loan status.", ["functional"])
    catalogue = load_security_rules(RULES_DIR)
    assert catalogue.spec(F.AUTHENTICATION).indicated_by("Officers shall log in with MFA.")
    assert not catalogue.spec(F.AUTHENTICATION).indicated_by("Show the loan status.")
    assert catalogue.family_from("Transaction integrity") is F.TRANSACTION_INTEGRITY
    assert catalogue.family_from("borrower credit risk") is None
