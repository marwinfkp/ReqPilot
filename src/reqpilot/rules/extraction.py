"""The extraction and classification ruleset as a typed, validated object.

The values live in ``rules/data/extraction.yaml`` - versioned data, per DQ-03.
This module turns a malformed file into a startup error rather than a silently
different review policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from reqpilot.domain.errors import RuleConfigurationError
from reqpilot.rules.loader import RuleSet, load_ruleset

RULESET_FILE = "extraction.yaml"


@dataclass(frozen=True)
class ExtractionRules:
    """Everything about extraction and classification that is policy, not code."""

    name: str
    version: str
    max_speaker_label_chars: int
    max_segments_per_call: int
    statement_prefix: str
    max_statement_chars: int
    max_quote_chars: int
    min_extraction_signal: float
    auto_merge: str
    duplicate_review_similarity: float
    classification_review_threshold: float
    max_criteria_per_requirement: int
    max_criterion_clause_chars: int

    @classmethod
    def from_ruleset(cls, ruleset: RuleSet) -> ExtractionRules:
        try:
            seg = ruleset["segmentation"]
            ext = ruleset["extraction"]
            dedupe = ruleset["dedupe"]
            cls_rules = ruleset["classification"]
            ac = ruleset["acceptance_criteria"]
            rules = cls(
                name=ruleset.name,
                version=ruleset.version,
                max_speaker_label_chars=int(seg["max_speaker_label_chars"]),
                max_segments_per_call=int(ext["max_segments_per_call"]),
                statement_prefix=str(ext["statement_prefix"]),
                max_statement_chars=int(ext["max_statement_chars"]),
                max_quote_chars=int(ext["max_quote_chars"]),
                min_extraction_signal=float(ext["min_review_signal"]),
                auto_merge=str(dedupe["auto_merge"]),
                duplicate_review_similarity=float(dedupe["review_similarity"]),
                classification_review_threshold=float(cls_rules["review_threshold"]),
                max_criteria_per_requirement=int(ac["max_per_requirement"]),
                max_criterion_clause_chars=int(ac["max_clause_chars"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuleConfigurationError(f"extraction ruleset is malformed: {exc}") from exc

        if rules.auto_merge != "exact_normalised":
            raise RuleConfigurationError(
                "dedupe.auto_merge must be 'exact_normalised': only identical statements "
                "are merged without a human (FR-EXT-005)"
            )
        for name in (
            "min_extraction_signal",
            "duplicate_review_similarity",
            "classification_review_threshold",
        ):
            value = getattr(rules, name)
            if not 0.0 <= value <= 1.0:
                raise RuleConfigurationError(f"{name} must lie in [0, 1], got {value}")
        for name in (
            "max_speaker_label_chars",
            "max_segments_per_call",
            "max_statement_chars",
            "max_quote_chars",
            "max_criteria_per_requirement",
            "max_criterion_clause_chars",
        ):
            if getattr(rules, name) < 1:
                raise RuleConfigurationError(f"{name} must be at least 1")
        if not rules.statement_prefix.strip():
            raise RuleConfigurationError("statement_prefix must not be empty (FR-EXT-003)")
        return rules


def load_extraction_rules(rules_dir: str | Path) -> ExtractionRules:
    return ExtractionRules.from_ruleset(load_ruleset(Path(rules_dir) / RULESET_FILE))
