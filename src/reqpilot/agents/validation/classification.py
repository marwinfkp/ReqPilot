"""Deterministic validation of a classification proposal (architecture F.2, E #5).

* Every label is normalised onto the thirteen approved categories; one that
  does not normalise is **rejected** and reported, never mapped by guesswork.
* A category proposed twice is kept once (the first occurrence).
* Every valid label is kept, with the model's review signal attached, and marked
  ``needs_review`` when that signal is below the ruleset threshold
  (``FR-CLS-002``). The signal is not turned into a probability.
* A proposal with no valid label is a failed classification, for a human.

Pure functions: no database, no model.
"""

from __future__ import annotations

from dataclasses import dataclass

from reqpilot.agents.contracts.classification import ClassificationOutput
from reqpilot.domain.classification import ValidatedLabel, normalise_category
from reqpilot.domain.enums import RequirementCategory
from reqpilot.rules.extraction import ExtractionRules


@dataclass(frozen=True)
class ClassificationDecision:
    labels: tuple[ValidatedLabel, ...]
    #: Proposed labels outside the taxonomy, as proposed (truncated).
    unknown: tuple[str, ...]
    #: Categories proposed more than once.
    repeated: tuple[RequirementCategory, ...]
    ruleset_version: str

    @property
    def failed(self) -> bool:
        return not self.labels

    @property
    def low_signal(self) -> tuple[ValidatedLabel, ...]:
        return tuple(label for label in self.labels if label.needs_review)


def validate_classification(
    output: ClassificationOutput, rules: ExtractionRules
) -> ClassificationDecision:
    labels: list[ValidatedLabel] = []
    unknown: list[str] = []
    repeated: list[RequirementCategory] = []
    seen: set[RequirementCategory] = set()
    for proposed in output.labels:
        category = normalise_category(proposed.category)
        if category is None:
            unknown.append(proposed.category.strip()[:64])
            continue
        if category in seen:
            repeated.append(category)
            continue
        seen.add(category)
        rationale = " ".join(proposed.rationale.split()) or None
        labels.append(
            ValidatedLabel(
                category=category,
                review_signal=proposed.review_signal,
                rationale=rationale,
                needs_review=proposed.review_signal < rules.classification_review_threshold,
            )
        )
    return ClassificationDecision(
        labels=tuple(labels),
        unknown=tuple(unknown),
        repeated=tuple(repeated),
        ruleset_version=rules.version,
    )
