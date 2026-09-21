"""Deterministic validation of classification proposals (FR-CLS-001, FR-CLS-002)."""

from __future__ import annotations

import pytest
from tests.p3_helpers import extraction_rules

from reqpilot.agents.contracts.classification import ClassificationOutput
from reqpilot.agents.validation import validate_classification
from reqpilot.domain.classification import display_label
from reqpilot.domain.enums import RequirementCategory as C

pytestmark = pytest.mark.unit

RULES = extraction_rules()


def output(*labels: tuple[str, float]) -> ClassificationOutput:
    return ClassificationOutput.model_validate(
        {"labels": [{"category": c, "review_signal": s, "rationale": "r"} for c, s in labels]}
    )


def test_multi_label_classification_keeps_every_valid_label() -> None:
    decision = validate_classification(
        output(("security", 0.9), ("privacy", 0.8), ("Data-Management", 0.7)), RULES
    )
    assert [label.category for label in decision.labels] == [
        C.SECURITY,
        C.PRIVACY,
        C.DATA_MANAGEMENT,
    ]
    assert not decision.failed and decision.unknown == ()


def test_all_thirteen_categories_are_accepted_together() -> None:
    decision = validate_classification(output(*[(display_label(c), 0.9) for c in C]), RULES)
    assert {label.category for label in decision.labels} == set(C)


def test_unknown_labels_are_rejected_and_reported_while_valid_ones_survive() -> None:
    decision = validate_classification(output(("security", 0.9), ("compliance", 0.9)), RULES)
    assert [label.category for label in decision.labels] == [C.SECURITY]
    assert decision.unknown == ("compliance",)


def test_a_repeated_category_is_kept_once() -> None:
    decision = validate_classification(output(("audit", 0.9), ("audit/reporting", 0.4)), RULES)
    assert len(decision.labels) == 1 and decision.labels[0].review_signal == 0.9
    assert decision.repeated == (C.AUDIT_REPORTING,)


def test_the_threshold_marks_labels_for_review_without_changing_the_signal() -> None:
    low = RULES.classification_review_threshold - 0.01
    decision = validate_classification(output(("security", low), ("privacy", 0.95)), RULES)
    assert [label.needs_review for label in decision.labels] == [True, False]
    assert decision.labels[0].review_signal == low, "not turned into a probability"
    assert [label.category for label in decision.low_signal] == [C.SECURITY]


def test_no_valid_label_is_a_failed_classification() -> None:
    assert validate_classification(output(("compliance", 0.9)), RULES).failed
    assert validate_classification(output(), RULES).failed


def test_a_signal_outside_the_unit_interval_is_refused_by_the_schema() -> None:
    with pytest.raises(ValueError):
        output(("security", 1.5))
