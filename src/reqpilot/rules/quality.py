"""The quality and conflict ruleset as a typed, validated object (P5; architecture M7).

The values live in ``rules/data/quality_heuristics.yaml`` - versioned data, per
DQ-03. A malformed file is a startup error, not a silently different policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from reqpilot.domain.enums import (
    ConflictClass,
    FindingSeverity,
    QualityFindingType,
    ReviewPriority,
)
from reqpilot.domain.errors import RuleConfigurationError
from reqpilot.rules.loader import RuleSet, load_ruleset

RULESET_FILE = "quality_heuristics.yaml"
#: The packaged rules directory (``RULES_DIR`` points here by default).
PACKAGED_RULES_DIR = Path(__file__).resolve().parent / "data"


def _terms(value: Any) -> tuple[str, ...]:
    terms = tuple(str(v).strip().lower() for v in value)
    if any(not t for t in terms):
        raise RuleConfigurationError("a term list contains an empty term")
    return terms


@dataclass(frozen=True)
class QualityRules:
    """Everything about quality and conflict detection that is policy, not code."""

    name: str
    version: str
    # ambiguity
    vague_terms: tuple[str, ...]
    loopholes: tuple[str, ...]
    timing_terms: tuple[str, ...]
    vague_subject_pronouns: tuple[str, ...]
    ambiguity_signal: float
    # incompleteness
    placeholders: tuple[str, ...]
    modal_verbs: tuple[str, ...]
    incompleteness_signal: float
    # testability
    quality_terms: tuple[str, ...]
    measurable_categories: tuple[str, ...]
    testability_signal: float
    # infeasibility
    absolute_terms: tuple[str, ...]
    infeasibility_signal: float
    # terminology
    acronym_min_length: int
    acronym_max_length: int
    common_acronyms: frozenset[str]
    terminology_signal: float
    # security / privacy signals
    security_data_terms: tuple[str, ...]
    privacy_data_terms: tuple[str, ...]
    protection_terms: tuple[str, ...]
    security_privacy_signal: float
    # source and duplication
    source_signal: float
    near_duplicate_similarity: float
    duplicate_signal_exact: float
    duplicate_signal_near: float
    # severity and priority
    severity_by_type: dict[QualityFindingType, FindingSeverity]
    priority_high_at: float
    priority_medium_at: float
    # semantic quality review
    semantic_quality_enabled: bool
    semantic_batch_size: int
    proposable_types: frozenset[QualityFindingType]
    max_findings_per_requirement: int
    max_quote_chars: int
    authority_phrases: tuple[str, ...]
    # conflicts
    shortlist_threshold: float
    top_k_per_requirement: int
    max_pairs_per_run: int
    topic_family_bonus: float
    unit_class_bonus: float
    numeric_rule_overlap: float
    negation_rule_overlap: float
    conflict_duplicate_similarity: float
    max_versions_per_run: int
    semantic_adjudication: bool
    conflict_signal_definite: float
    conflict_signal_potential: float
    severity_by_class: dict[ConflictClass, FindingSeverity]
    topic_families: dict[str, tuple[str, ...]]
    actor_terms: frozenset[str]
    condition_markers: tuple[str, ...]
    negation_markers: tuple[str, ...]

    @property
    def ruleset_ref(self) -> str:
        return f"{self.name}@{self.version}"

    def priority_of(self, review_signal: float | None) -> ReviewPriority:
        """A review-priority label for a heuristic signal - never a probability."""
        if review_signal is None:
            return ReviewPriority.MEDIUM
        if review_signal >= self.priority_high_at:
            return ReviewPriority.HIGH
        if review_signal >= self.priority_medium_at:
            return ReviewPriority.MEDIUM
        return ReviewPriority.LOW

    def severity_of(self, finding_type: QualityFindingType) -> FindingSeverity:
        """The authoritative severity of a finding type - never a model's choice."""
        return self.severity_by_type[finding_type]

    @classmethod
    def from_ruleset(cls, ruleset: RuleSet) -> QualityRules:
        try:
            amb = ruleset["ambiguity"]
            inc = ruleset["incompleteness"]
            tst = ruleset["testability"]
            inf = ruleset["infeasibility"]
            term = ruleset["terminology"]
            sec = ruleset["security_privacy"]
            dup = ruleset["duplication"]
            sem = ruleset["semantic_quality"]
            cnf = ruleset["conflict"]
            prio = ruleset["review_priority"]
            severity_by_type = {
                QualityFindingType(k): FindingSeverity(v)
                for k, v in ruleset["severity_by_type"].items()
            }
            rules = cls(
                name=ruleset.name,
                version=ruleset.version,
                vague_terms=_terms(amb["vague_terms"]),
                loopholes=_terms(amb["loopholes"]),
                timing_terms=_terms(amb["timing_terms"]),
                vague_subject_pronouns=_terms(amb["vague_subject_pronouns"]),
                ambiguity_signal=float(amb["review_signal"]),
                placeholders=_terms(inc["placeholders"]),
                modal_verbs=_terms(inc["modal_verbs"]),
                incompleteness_signal=float(inc["review_signal"]),
                quality_terms=_terms(tst["quality_terms"]),
                measurable_categories=_terms(tst["measurable_categories"]),
                testability_signal=float(tst["review_signal"]),
                absolute_terms=_terms(inf["absolute_terms"]),
                infeasibility_signal=float(inf["review_signal"]),
                acronym_min_length=int(term["acronym_min_length"]),
                acronym_max_length=int(term["acronym_max_length"]),
                common_acronyms=frozenset(str(a).upper() for a in term["common_acronyms"]),
                terminology_signal=float(term["review_signal"]),
                security_data_terms=_terms(sec["security_data_terms"]),
                privacy_data_terms=_terms(sec["privacy_data_terms"]),
                protection_terms=_terms(sec["protection_terms"]),
                security_privacy_signal=float(sec["review_signal"]),
                source_signal=float(ruleset["source"]["review_signal"]),
                near_duplicate_similarity=float(dup["near_duplicate_similarity"]),
                duplicate_signal_exact=float(dup["review_signal_exact"]),
                duplicate_signal_near=float(dup["review_signal_near"]),
                severity_by_type=severity_by_type,
                priority_high_at=float(prio["high_at"]),
                priority_medium_at=float(prio["medium_at"]),
                semantic_quality_enabled=bool(sem["enabled"]),
                semantic_batch_size=int(sem["batch_size"]),
                proposable_types=frozenset(QualityFindingType(t) for t in sem["proposable_types"]),
                max_findings_per_requirement=int(sem["max_findings_per_requirement"]),
                max_quote_chars=int(sem["max_quote_chars"]),
                authority_phrases=_terms(sem["authority_phrases"]),
                shortlist_threshold=float(cnf["shortlist_threshold"]),
                top_k_per_requirement=int(cnf["top_k_per_requirement"]),
                max_pairs_per_run=int(cnf["max_pairs_per_run"]),
                topic_family_bonus=float(cnf["topic_family_bonus"]),
                unit_class_bonus=float(cnf["unit_class_bonus"]),
                numeric_rule_overlap=float(cnf["numeric_rule_overlap"]),
                negation_rule_overlap=float(cnf["negation_rule_overlap"]),
                conflict_duplicate_similarity=float(cnf["duplicate_similarity"]),
                max_versions_per_run=int(cnf["max_versions_per_run"]),
                semantic_adjudication=bool(cnf["semantic_adjudication"]),
                conflict_signal_definite=float(cnf["review_signal_definite"]),
                conflict_signal_potential=float(cnf["review_signal_potential"]),
                severity_by_class={
                    ConflictClass(k): FindingSeverity(v)
                    for k, v in cnf["severity_by_class"].items()
                },
                topic_families={
                    str(name): _terms(stems) for name, stems in cnf["topic_families"].items()
                },
                actor_terms=frozenset(_terms(cnf["actor_terms"])),
                condition_markers=_terms(cnf["condition_markers"]),
                negation_markers=_terms(cnf["negation_markers"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuleConfigurationError(f"quality ruleset is malformed: {exc}") from exc

        missing = set(QualityFindingType) - set(rules.severity_by_type)
        if missing:
            raise RuleConfigurationError(
                f"severity_by_type must cover every finding type; missing {sorted(missing)}"
            )
        if set(rules.severity_by_class) != set(ConflictClass):
            raise RuleConfigurationError("severity_by_class must cover definite and potential")
        not_proposable = {
            QualityFindingType.MISSING_SOURCE,
            QualityFindingType.DUPLICATION,
            QualityFindingType.NEAR_DUPLICATE,
        }
        if rules.proposable_types & not_proposable:
            raise RuleConfigurationError(
                "missing source and duplication are decided by the rules, never proposed by a model"
            )
        for name in (
            "ambiguity_signal",
            "incompleteness_signal",
            "testability_signal",
            "infeasibility_signal",
            "terminology_signal",
            "security_privacy_signal",
            "source_signal",
            "near_duplicate_similarity",
            "duplicate_signal_exact",
            "duplicate_signal_near",
            "priority_high_at",
            "priority_medium_at",
            "shortlist_threshold",
            "numeric_rule_overlap",
            "negation_rule_overlap",
            "conflict_duplicate_similarity",
            "conflict_signal_definite",
            "conflict_signal_potential",
        ):
            value = getattr(rules, name)
            if not 0.0 <= value <= 1.0:
                raise RuleConfigurationError(f"{name} must lie in [0, 1], got {value}")
        if rules.priority_medium_at > rules.priority_high_at:
            raise RuleConfigurationError("review_priority.medium_at must not exceed high_at")
        for name in (
            "semantic_batch_size",
            "max_findings_per_requirement",
            "max_quote_chars",
            "top_k_per_requirement",
            "max_pairs_per_run",
            "max_versions_per_run",
            "acronym_min_length",
        ):
            if getattr(rules, name) < 1:
                raise RuleConfigurationError(f"{name} must be at least 1")
        if rules.acronym_max_length < rules.acronym_min_length:
            raise RuleConfigurationError("acronym_max_length must be >= acronym_min_length")
        return rules


def load_quality_rules(rules_dir: str | Path) -> QualityRules:
    return QualityRules.from_ruleset(load_ruleset(Path(rules_dir) / RULESET_FILE))


@lru_cache(maxsize=1)
def packaged_quality_rules() -> QualityRules:
    """The packaged ruleset, loaded once - the default when a caller supplies none."""
    return load_quality_rules(PACKAGED_RULES_DIR)
