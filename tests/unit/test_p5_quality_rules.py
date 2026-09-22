"""P5 deterministic quality and conflict rules (FR-QAL-001..009, FR-CNF-004; E #6).

Pure functions over statements and versioned rule data. Development sentences
only - never the frozen P5 benchmark.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml
from tests.p3_helpers import RULES_DIR

from reqpilot.domain.enums import (
    ConflictClass,
    ConflictKind,
    ConflictVerdict,
    FindingSeverity,
    QualityFindingType,
    ReviewPriority,
)
from reqpilot.domain.errors import RuleConfigurationError
from reqpilot.domain.quality import PairItem, check_statement, duplicates, judge_pair, shortlist
from reqpilot.domain.quality.checks import (
    ambiguity,
    incompleteness,
    infeasibility,
    missing_source,
    security_privacy,
    terminology,
)
from reqpilot.domain.quality.checks import (
    testability as check_testability,
)
from reqpilot.domain.quality.text import (
    content_stems,
    find_phrases,
    overlap_coefficient,
    quantities,
    stem,
)
from reqpilot.rules.quality import load_quality_rules

pytestmark = pytest.mark.unit

RULES = load_quality_rules(RULES_DIR)


def types(findings) -> list[QualityFindingType]:  # type: ignore[no-untyped-def]
    return [f.finding_type for f in findings]


# --- the ruleset ------------------------------------------------------------------------


def test_the_ruleset_is_versioned_and_complete() -> None:
    assert RULES.ruleset_ref == "quality_heuristics@1.0.0"
    assert set(RULES.severity_by_type) == set(QualityFindingType)
    assert set(RULES.severity_by_class) == set(ConflictClass)


def _copy(tmp_path: Path) -> Path:
    target = tmp_path / "quality_heuristics.yaml"
    target.write_text(
        (RULES_DIR / "quality_heuristics.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return target


def _rewrite(path: Path, change) -> None:  # type: ignore[no-untyped-def]
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(raw)
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")


def test_a_model_may_never_propose_a_rule_only_type(tmp_path: Path) -> None:
    path = _copy(tmp_path)
    _rewrite(
        path,
        lambda raw: raw["rules"]["semantic_quality"]["proposable_types"].append("missing_source"),
    )
    with pytest.raises(RuleConfigurationError, match="decided by the rules"):
        load_quality_rules(tmp_path)


def test_every_finding_type_needs_a_severity(tmp_path: Path) -> None:
    path = _copy(tmp_path)
    _rewrite(path, lambda raw: raw["rules"]["severity_by_type"].pop("ambiguity"))
    with pytest.raises(RuleConfigurationError, match="severity_by_type"):
        load_quality_rules(tmp_path)


def test_signals_must_lie_in_the_unit_interval(tmp_path: Path) -> None:
    path = _copy(tmp_path)
    _rewrite(path, lambda raw: raw["rules"]["conflict"].update({"shortlist_threshold": 1.5}))
    with pytest.raises(RuleConfigurationError, match=r"\[0, 1\]"):
        load_quality_rules(tmp_path)


def test_severity_is_the_rulesets_and_priority_is_a_label() -> None:
    assert RULES.severity_of(QualityFindingType.MISSING_SOURCE) is FindingSeverity.HIGH
    assert RULES.severity_of(QualityFindingType.UNDEFINED_TERM) is FindingSeverity.LOW
    assert RULES.priority_of(0.95) is ReviewPriority.HIGH
    assert RULES.priority_of(0.5) is ReviewPriority.MEDIUM
    assert RULES.priority_of(0.1) is ReviewPriority.LOW
    assert RULES.priority_of(None) is ReviewPriority.MEDIUM


# --- text primitives ----------------------------------------------------------------------


def test_phrases_match_on_word_boundaries_and_keep_the_longest() -> None:
    spans = find_phrases("It shall be easy to use, and easygoing.", ["easy", "easy to use"])
    assert [s.quote for s in spans] == ["easy to use"]
    assert find_phrases("Use the fastest route.", ["fast"]) == []


def test_stems_and_overlap() -> None:
    assert stem("deleted") == stem("delete") == stem("deletion")
    a = content_stems("The system shall delete the records.")
    b = content_stems("Records shall never be deleted.")
    assert overlap_coefficient(a, b) == 1.0


@pytest.mark.parametrize(
    ("text", "unit", "lower", "upper"),
    [
        ("support at least 200 users", "count:user", 200, math.inf),
        ("a maximum of 40 users", "count:user", -math.inf, 40),
        ("within 3 seconds", "time", -math.inf, 3),
        ("after 10 minutes of idle time", "time", 600, 600),
        ("within two working days", "time", -math.inf, 172800),
        ("files up to 8 MB", "size", -math.inf, 8),
        ("amounts above 1,500 EUR", "money", 1500, math.inf),
    ],
)
def test_quantities_are_parsed_with_their_bounds(text, unit, lower, upper) -> None:  # type: ignore[no-untyped-def]
    (q,) = quantities(text)
    assert (q.unit_class, q.lower, q.upper) == (unit, lower, upper)
    assert q.quote in text


def test_clock_times_and_bare_numbers_are_not_quantities() -> None:
    assert quantities("from 02:00 to 04:00") == []
    assert quantities("section 7 of the form") == []


# --- per-statement checks -----------------------------------------------------------------


def test_ambiguity_reports_the_vague_span() -> None:
    (finding,) = ambiguity("The report shall be generated quickly.", RULES)
    assert finding.finding_type is QualityFindingType.AMBIGUITY
    assert finding.span is not None and finding.span.quote == "quickly"


def test_a_timing_word_with_a_measure_is_not_ambiguous() -> None:
    assert ambiguity("The page shall load quickly, within 2 seconds.", RULES) == []


def test_precise_uses_are_not_flagged() -> None:
    assert ambiguity("The system shall allow at most 3 retries.", RULES) == []
    assert ambiguity("The system shall rotate the API key every 90 days.", RULES) == []


def test_loopholes_and_pronoun_subjects() -> None:
    assert types(ambiguity("Notifications shall be sent as needed.", RULES)) == [
        QualityFindingType.AMBIGUITY
    ]
    (finding,) = ambiguity("Once reviewed, it shall be archived by the clerk.", RULES)
    assert finding.rule_id == "AMB-PRONOUN-SUBJECT" and finding.span.quote == "it"


def test_incompleteness_says_what_is_missing_and_invents_nothing() -> None:
    placeholder = incompleteness("The limit shall be TBD euros per day.", RULES)
    assert [f.rule_id for f in placeholder] == ["INC-PLACEHOLDER"]
    assert [f.rule_id for f in incompleteness("A monthly report.", RULES)] == ["INC-NO-OBLIGATION"]
    passive = incompleteness("The form shall be validated before submission.", RULES)
    assert [f.rule_id for f in passive] == ["INC-PASSIVE-NO-ACTOR"]
    assert incompleteness("The form shall be validated by the clerk.", RULES) == []


def test_testability_needs_a_measure() -> None:
    assert types(check_testability("The system shall be highly secure.", RULES)) == [
        QualityFindingType.UNTESTABILITY
    ]
    assert check_testability("The system shall be available 99.5% of each month.", RULES) == []
    by_category = check_testability("The system shall handle month-end.", RULES, ["performance"])
    assert types(by_category) == [QualityFindingType.UNTESTABILITY]


def test_infeasibility_flags_absolute_targets() -> None:
    assert types(infeasibility("The service shall have zero downtime.", RULES)) == [
        QualityFindingType.INFEASIBILITY
    ]


def test_undefined_acronyms_against_the_glossary() -> None:
    (finding,) = terminology("The system shall check the AML status.", RULES)
    assert finding.span.quote == "AML"
    assert terminology("The system shall check the AML status.", RULES, frozenset({"aml"})) == []
    assert terminology("The system shall export a CSV and a PDF.", RULES) == []


def test_security_and_privacy_signals_are_signals_only() -> None:
    (sec,) = security_privacy("The system shall keep each card number.", RULES)
    assert sec.finding_type is QualityFindingType.MISSING_SECURITY_CONSIDERATION
    assert "not a determination" in sec.rationale
    (prv,) = security_privacy("The form shall collect the date of birth.", RULES)
    assert prv.finding_type is QualityFindingType.MISSING_PRIVACY_CONSIDERATION
    assert security_privacy("The system shall encrypt each card number.", RULES) == []


def test_missing_source() -> None:
    assert [f.rule_id for f in missing_source(0, 0, RULES)] == ["SRC-NONE"]
    assert [f.rule_id for f in missing_source(2, 1, RULES)] == ["SRC-UNRESOLVED"]
    assert missing_source(1, 0, RULES) == []


def test_a_clear_statement_has_no_findings() -> None:
    assert check_statement("The system shall lock an account after 5 failed sign-ins.", RULES) == []


def test_every_span_is_words_of_the_statement() -> None:
    statement = "It shall respond quickly and store the password as needed TBD."
    for finding in check_statement(statement, RULES):
        if finding.span is not None:
            assert statement[finding.span.start : finding.span.end] == finding.span.quote


# --- duplicates and the shortlist -----------------------------------------------------------


def test_duplicates_exact_and_near() -> None:
    items = [
        PairItem(
            "a", "The system shall email the monthly account summary to each retail customer."
        ),
        PairItem(
            "b", "The system shall email the monthly account summary to each retail customer!"
        ),
        PairItem(
            "c", "The system shall email the monthly account summary to every retail customer."
        ),
        PairItem("d", "The system shall print labels."),
    ]
    found = {(d.a, d.b): d.exact for d in duplicates(items, RULES)}
    assert found[("a", "b")] is True
    assert found[("a", "c")] is False
    assert not any("d" in pair for pair in found)


def test_a_statement_and_its_negation_are_not_duplicates() -> None:
    items = [
        PairItem("a", "The system shall send payment reminders by post."),
        PairItem("b", "The system shall not send payment reminders by post."),
    ]
    assert duplicates(items, RULES) == []


def test_versions_of_one_requirement_are_never_paired() -> None:
    items = [
        PairItem("a", "The system shall email the summary.", requirement_key="R"),
        PairItem("b", "The system shall email the summary.", requirement_key="R"),
    ]
    assert duplicates(items, RULES) == [] and shortlist(items, RULES) == []


def test_the_shortlist_is_bounded_and_ordered() -> None:
    items = [PairItem(f"k{i:02}", f"The system shall send report {i} by email.") for i in range(30)]
    pairs = shortlist(items, RULES)
    assert 0 < len(pairs) <= RULES.max_pairs_per_run
    assert all(p.a < p.b for p in pairs)
    assert [p.score for p in pairs] == sorted((p.score for p in pairs), reverse=True)
    partners: dict[str, int] = {}
    for p in pairs:
        partners[p.a] = partners.get(p.a, 0) + 1
    # Each pair is in the top k of at least one side, so no key is unbounded.
    assert len(pairs) <= len(items) * RULES.top_k_per_requirement


def test_the_shortlist_uses_supplied_similarity_and_focus() -> None:
    items = [
        PairItem("a", "Alpha widgets."),
        PairItem("b", "Beta gadgets."),
        PairItem("c", "Gamma."),
    ]
    assert shortlist(items, RULES) == []
    close = shortlist(items, RULES, similarity=lambda x, y: 0.9 if {x, y} == {"a", "b"} else 0.0)
    assert [(p.a, p.b) for p in close] == [("a", "b")]
    focused = shortlist(items, RULES, similarity=lambda x, y: 0.9, focus=frozenset({"c"}))
    assert all("c" in (p.a, p.b) for p in focused)


# --- deterministic contradiction rules --------------------------------------------------------


def judge(a: str, b: str):  # type: ignore[no-untyped-def]
    return judge_pair(PairItem("a", a), PairItem("b", b), RULES)


def test_disjoint_bounds_on_one_attribute_are_a_definite_conflict() -> None:
    verdict = judge(
        "The portal shall support at least 300 concurrent users.",
        "The portal shall be sized for a maximum of 50 concurrent users.",
    )
    assert verdict.verdict is ConflictVerdict.DEFINITE_CONFLICT
    assert verdict.kind is ConflictKind.NUMERIC and verdict.rule_id == "CNF-NUMERIC-DISJOINT"
    assert verdict.evidence_a and verdict.evidence_b


def test_different_conditions_are_never_definite() -> None:
    verdict = judge(
        "The dashboard shall load within 1 seconds.",
        "During the quarterly close, the dashboard shall load within 8 seconds.",
    )
    assert verdict.verdict is not ConflictVerdict.DEFINITE_CONFLICT


def test_different_actors_are_never_definite() -> None:
    verdict = judge(
        "Customer sessions shall expire after 10 minutes of inactivity.",
        "Operator sessions shall expire after 45 minutes of inactivity.",
    )
    assert verdict.verdict is not ConflictVerdict.DEFINITE_CONFLICT
    assert verdict.signal, "a human (or the adjudicator) should still look"


def test_negation_of_the_same_obligation_is_definite() -> None:
    verdict = judge(
        "The system shall send payment reminders by post.",
        "The system shall not send payment reminders by post.",
    )
    assert verdict.verdict is ConflictVerdict.DEFINITE_CONFLICT
    assert verdict.kind is ConflictKind.LOGICAL


def test_a_duplicate_is_not_a_conflict() -> None:
    verdict = judge(
        "The system shall send payment reminders by post.",
        "The system shall send payment reminders by post.",
    )
    assert verdict.verdict is ConflictVerdict.DUPLICATE and not verdict.signal


def test_unrelated_statements_have_no_signal() -> None:
    verdict = judge("The system shall print labels.", "Managers shall approve refunds.")
    assert verdict.verdict is ConflictVerdict.NO_CONFLICT and not verdict.signal
