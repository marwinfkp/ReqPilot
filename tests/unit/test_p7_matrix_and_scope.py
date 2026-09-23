"""The deterministic severity matrix and the FR-RSK-011 scope guard (P7).

The two pieces of P7 that carry the phase's authority, tested without a
database, a model or a graph:

* the matrix is the approved architecture I.3 table, total over all nine cells,
  pure, and the same in the ruleset, the code literal and the database;
* the scope guard refuses borrower credit risk and does **not** refuse the
  legitimate project risks a loan-origination project naturally has.
"""

from __future__ import annotations

import pytest
import yaml
from tests.p3_helpers import RULES_DIR

from reqpilot.domain.enums import (
    RiskCategory,
    RiskImpact,
    RiskLikelihood,
    RiskSeverity,
    Role,
)
from reqpilot.domain.errors import RuleConfigurationError
from reqpilot.domain.risk.matrix import (
    APPROVED_CELLS,
    ESCALATING_SEVERITY,
    RiskMatrix,
    compute_severity,
    parse_impact,
    parse_likelihood,
)
from reqpilot.domain.risk.scope import (
    SCOPE_REFUSAL_NOTICE,
    SCOPE_RULES_VERSION,
    check_fields,
    find_out_of_scope,
    rule_ids,
    why,
)
from reqpilot.rules.loader import RuleSet
from reqpilot.rules.risk import RiskRules, load_risk_rules

pytestmark = pytest.mark.unit

L1, L2, L3 = RiskLikelihood.L1, RiskLikelihood.L2, RiskLikelihood.L3
I1, I2, I3 = RiskImpact.I1, RiskImpact.I2, RiskImpact.I3
LOW, MEDIUM, HIGH = RiskSeverity.LOW, RiskSeverity.MEDIUM, RiskSeverity.HIGH


def rules() -> RiskRules:
    return load_risk_rules(RULES_DIR)


# --- the matrix: every cell, from architecture I.3 ------------------------------------------

#: The approved table, written out again here from architecture I.3 rather than
#: imported, so that this test fails if the literal, the ruleset and the
#: architecture ever diverge - which is the whole point of pinning it.
#:
#:          I1 Minor   I2 Moderate   I3 Major
#:   L3     Medium     High          High
#:   L2     Low        Medium        High
#:   L1     Low        Low           Medium
ARCHITECTURE_I3 = {
    (L3, I1): MEDIUM,
    (L3, I2): HIGH,
    (L3, I3): HIGH,
    (L2, I1): LOW,
    (L2, I2): MEDIUM,
    (L2, I3): HIGH,
    (L1, I1): LOW,
    (L1, I2): LOW,
    (L1, I3): MEDIUM,
}


@pytest.mark.parametrize(("cell", "expected"), sorted(ARCHITECTURE_I3.items(), key=str))
def test_every_matrix_cell_is_the_approved_one(
    cell: tuple[RiskLikelihood, RiskImpact], expected: RiskSeverity
) -> None:
    likelihood, impact = cell
    computation = compute_severity(rules().matrix, likelihood, impact)
    assert computation.severity is expected
    assert computation.likelihood is likelihood and computation.impact is impact
    assert computation.matrix_version == rules().matrix.version
    assert str(likelihood) in computation.explanation and str(impact) in computation.explanation


def test_the_code_literal_the_ruleset_and_the_architecture_agree() -> None:
    assert APPROVED_CELLS == ARCHITECTURE_I3
    assert dict(rules().matrix.cells) == ARCHITECTURE_I3


def test_the_matrix_is_total_and_has_exactly_nine_cells() -> None:
    matrix = rules().matrix
    assert len(matrix.cells) == 9
    for likelihood in RiskLikelihood:
        for impact in RiskImpact:
            assert matrix.severity(likelihood, impact) in set(RiskSeverity)


def test_the_boundary_cells_are_the_ones_that_change_the_outcome() -> None:
    """The three boundaries a reviewer will actually argue about."""
    matrix = rules().matrix
    # Raising impact from I2 to I3 at L2 crosses from MEDIUM into HIGH - the
    # only step that turns a merely-tracked risk into a blocking one.
    assert matrix.severity(L2, I2) is MEDIUM and matrix.severity(L2, I3) is HIGH
    # Raising likelihood from L2 to L3 at I2 does the same.
    assert matrix.severity(L2, I2) is MEDIUM and matrix.severity(L3, I2) is HIGH
    # A major impact is never LOW, however unlikely.
    assert matrix.severity(L1, I3) is MEDIUM
    # A likely risk is never LOW, however minor.
    assert matrix.severity(L3, I1) is MEDIUM


def test_only_high_escalates() -> None:
    matrix = rules().matrix
    assert ESCALATING_SEVERITY is HIGH
    escalating = {cell for cell in ARCHITECTURE_I3 if compute_severity(matrix, *cell).requires_gate}
    assert escalating == {(L2, I3), (L3, I2), (L3, I3)}


def test_the_lookup_is_pure_and_repeatable() -> None:
    matrix = rules().matrix
    first = [
        compute_severity(matrix, level, impact) for level in RiskLikelihood for impact in RiskImpact
    ]
    second = [
        compute_severity(matrix, level, impact) for level in RiskLikelihood for impact in RiskImpact
    ]
    assert first == second
    # And a second load of the ruleset gives the same answers.
    assert dict(load_risk_rules(RULES_DIR).matrix.cells) == dict(matrix.cells)


def test_a_partial_matrix_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="not total"):
        RiskMatrix(version="broken", cells={(L1, I1): LOW})


def test_a_ruleset_whose_matrix_differs_from_the_architecture_is_refused() -> None:
    """The matrix is versioned data, but its *values* are approved (I.3).

    Storing it as data without pinning it would have made the approved matrix
    editable by anyone who can edit a YAML file.
    """
    raw = yaml.safe_load((RULES_DIR / "risk_rules.yaml").read_text(encoding="utf-8"))
    for row in raw["rules"]["matrix"]:
        if row["likelihood"] == "L2" and row["impact"] == "I3":
            row["severity"] = "low"  # the downgrade an attacker would want
    with pytest.raises(RuleConfigurationError, match="does not match the approved"):
        RiskRules.from_ruleset(
            RuleSet(name="risk_rules", version="9.9.9", description="", data=raw["rules"])
        )


def test_a_duplicated_matrix_cell_is_refused() -> None:
    raw = yaml.safe_load((RULES_DIR / "risk_rules.yaml").read_text(encoding="utf-8"))
    raw["rules"]["matrix"].append({"likelihood": "L1", "impact": "I1", "severity": "high"})
    with pytest.raises(RuleConfigurationError, match="defined twice"):
        RiskRules.from_ruleset(
            RuleSet(name="risk_rules", version="9.9.9", description="", data=raw["rules"])
        )


# --- the scales ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("L1", L1),
        ("l2", L2),
        (" L3 ", L3),
        ("1", L1),
        ("low", L1),
        ("MEDIUM", L2),
        ("high", L3),
        (L2, L2),
    ],
)
def test_a_recognised_likelihood_is_parsed(value: object, expected: RiskLikelihood) -> None:
    assert parse_likelihood(value) is expected


@pytest.mark.parametrize(
    "value", ["", "L4", "L0", "very likely", None, 4, {"likelihood": "L2"}, []]
)
def test_an_unrecognised_likelihood_is_none_never_guessed(value: object) -> None:
    """Contrast I.7: a security level is normalised *upward*, because the finding
    exists either way. A rating is a judgement; there is no safe direction in
    which to invent one, so the proposal is dropped instead."""
    assert parse_likelihood(value) is None


@pytest.mark.parametrize(
    ("value", "expected"), [("I1", I1), ("i2", I2), ("3", I3), ("major", I3), ("minor", I1)]
)
def test_a_recognised_impact_is_parsed(value: object, expected: RiskImpact) -> None:
    assert parse_impact(value) is expected


@pytest.mark.parametrize("value", ["", "I4", "catastrophic", None, 9, True])
def test_an_unrecognised_impact_is_none_never_guessed(value: object) -> None:
    assert parse_impact(value) is None


# --- the ruleset -----------------------------------------------------------------------------


def test_every_category_has_an_owner_role_and_it_is_a_rule_not_a_choice() -> None:
    """Architecture I.4: "Assignment is a rule, not a model decision"."""
    owners = rules().owner_roles
    assert set(owners) == set(RiskCategory)
    assert owners[RiskCategory.SECURITY] is Role.SECURITY_REVIEWER
    assert owners[RiskCategory.PRIVACY] is Role.SECURITY_REVIEWER
    assert owners[RiskCategory.COMPLIANCE] is Role.COMPLIANCE_OFFICER
    for category in (RiskCategory.BUSINESS, RiskCategory.TECHNICAL, RiskCategory.OPERATIONAL):
        assert owners[category] is Role.PROJECT_MANAGER


def test_a_ruleset_missing_an_owner_role_is_refused() -> None:
    raw = yaml.safe_load((RULES_DIR / "risk_rules.yaml").read_text(encoding="utf-8"))
    del raw["rules"]["owner_roles"]["security"]
    with pytest.raises(RuleConfigurationError, match="no owner role"):
        RiskRules.from_ruleset(
            RuleSet(name="risk_rules", version="9.9.9", description="", data=raw["rules"])
        )


def test_indicators_hint_at_categories_without_deciding_anything() -> None:
    indicated = rules().indicated_categories(
        "The system shall encrypt personal data at rest.", ["security"]
    )
    assert RiskCategory.SECURITY in indicated and RiskCategory.PRIVACY in indicated
    # An indication is not a rating, a severity or a gate: nothing here produces one.
    assert all(isinstance(c, RiskCategory) for c in indicated)


def test_the_six_categories_are_exactly_the_approved_ones() -> None:
    """``FR-RSK-002`` ``[PS §4]``. No financial, market, credit or borrower category."""
    assert {c.value for c in RiskCategory} == {
        "business",
        "technical",
        "security",
        "privacy",
        "compliance",
        "operational",
    }


# --- FR-RSK-011: the scope guard ---------------------------------------------------------------

#: Text that is borrower-level scoring and must be refused.
OUT_OF_SCOPE = [
    "The model shall compute the borrower's credit risk before disbursement.",
    "Risk that the credit scoring engine mis-ranks applicants.",
    "The probability of default may be understated for thin-file applicants.",
    "A customer risk rating must be assigned at onboarding.",
    "Assign a risk score for the borrower at application time.",
    "The fraud score for each applicant should exceed 0.8.",
    "Creditworthiness assessment may be inconsistent between channels.",
    "Loss given default is not modelled for the new product.",
    "Delinquency trends are not captured in the reporting pack.",
    "Underwriting decisions may be inconsistent between reviewers.",
    "Credit decisioning rules may drift from policy.",
    "Risk-based pricing could disadvantage some segments.",
    "Loan default rates may rise in the pilot cohort.",
    "The credit eligibility model needs retraining.",
]

#: Legitimate project, engineering and security risks in a loan-origination
#: project. These mention credit systems and must **not** be refused: a guard
#: that blocked them would be useless, so the false-alarm cases are part of the
#: test rather than an afterthought.
IN_SCOPE = [
    "The credit bureau integration may exceed its latency budget under peak load.",
    "A defect in the eligibility rules engine could cause valid applications to be rejected.",
    "Insufficient audit logging would make a disputed decision impossible to reconstruct.",
    "The fraud controls specified may be insufficient for the new channel.",
    "Fraud risk is not addressed by any requirement in the set.",
    "The loan application service may lose data during the storage migration.",
    "Consent records may be incomplete for applicants migrated from the legacy system.",
    "The default risk level for new findings is set too low in configuration.",
    "Credit bureau downtime has no documented fallback.",
    "The disbursement workflow may stall when only one officer is available.",
    "Retention obligations may not survive a change of storage provider.",
    "The team has no experience with the payments interface.",
]


@pytest.mark.parametrize("text", OUT_OF_SCOPE)
def test_borrower_credit_risk_text_is_refused(text: str) -> None:
    hits = find_out_of_scope(text)
    assert hits, f"the scope guard let borrower-level scoring through: {text!r}"
    assert all(h.rule_id in rule_ids() for h in hits)
    assert why(h.rule_id for h in hits)


@pytest.mark.parametrize("text", IN_SCOPE)
def test_legitimate_project_risk_text_is_not_refused(text: str) -> None:
    assert find_out_of_scope(text) == [], (
        f"the scope guard wrongly refused a legitimate project risk: {text!r}"
    )


def test_the_guard_reports_which_field_offended() -> None:
    hits = check_fields(
        {
            "title": "Latency of the bureau call",
            "description": "The system shall compute a credit score for the applicant.",
            "impact_rationale": None,
        }
    )
    assert [h.field for h in hits] == ["description"]
    assert hits[0].rule_id == "credit_scoring"


def test_the_guard_is_code_not_configuration() -> None:
    """There is no setting that disables it, and no threshold to tune it down.

    Asserted structurally: the module exposes a version and the rule ids, and
    nothing that takes a configuration argument.
    """
    assert SCOPE_RULES_VERSION and rule_ids()
    assert "borrower credit risk" in SCOPE_REFUSAL_NOTICE
    assert find_out_of_scope.__defaults__ in (None, ())


def test_empty_text_is_in_scope() -> None:
    assert find_out_of_scope(None) == [] and find_out_of_scope("") == []
