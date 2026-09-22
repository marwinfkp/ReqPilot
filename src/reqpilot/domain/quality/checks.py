"""Deterministic quality checks over one requirement statement (P5; ``FR-QAL-001``..``009``).

Every check is a pure function of the statement, its classification labels, its
source-reference facts and the project glossary. Each returns
:class:`DetectedFinding` values naming the rule that fired and, where there is
one, the exact span of the statement it is about - so a reviewer can see *why*
without trusting the rule.

The rules are conservative heuristics, not judgements: they report what they can
point at, and they never rewrite a requirement or invent a missing value. The
LLM semantic review (role #3 support) adds what needs reading comprehension;
neither layer can close a finding - a human does (policy rule 8).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from reqpilot.domain.enums import QualityFindingType
from reqpilot.domain.quality.text import Span, find_phrases, quantities, words
from reqpilot.rules.quality import QualityRules

#: Findings of one rule on one statement are capped, so a pathological statement
#: produces a readable list rather than a wall.
MAX_SPANS_PER_RULE = 3

_ACRONYM = re.compile(r"(?<![\w-])([A-Z][A-Z0-9]{1,9})(?![a-z])")
_PASSIVE = re.compile(
    r"\b(shall|must|will|should|may)\s+(?:not\s+)?be\s+([a-z]+(?:ed|en)|sent|kept|held|made|"
    r"shown|given|built|put|set|run|paid|told|sold|read)\b",
    re.IGNORECASE,
)
#: Words before which "most", "key" or "long" are not vague ("at most", "API key").
_PRECISE_BEFORE = {"most": ("at",), "key": ("api", "private", "public", "primary", "foreign")}


@dataclass(frozen=True)
class DetectedFinding:
    """What a rule found. Not yet a finding: the service decides what is recorded."""

    finding_type: QualityFindingType
    rule_id: str
    rationale: str
    review_signal: float
    span: Span | None = None
    #: Duplicate rules: the key of the other requirement.
    related_key: str | None = None


def _precise_use(statement: str, span: Span) -> bool:
    before = words(statement[: span.start])[-1:] or [""]
    return before[0] in _PRECISE_BEFORE.get(span.quote.lower(), ())


def ambiguity(statement: str, rules: QualityRules) -> list[DetectedFinding]:
    """``FR-QAL-001`` - vague terms, loopholes and a pronoun for an actor, with their span."""
    found: list[DetectedFinding] = []
    has_time_measure = any(q.unit_class == "time" for q in quantities(statement))
    vague = [
        s
        for s in find_phrases(statement, rules.vague_terms)
        if not _precise_use(statement, s)
        and not (has_time_measure and s.quote.lower() in rules.timing_terms)
    ]
    for span in vague[:MAX_SPANS_PER_RULE]:
        found.append(
            DetectedFinding(
                QualityFindingType.AMBIGUITY,
                "AMB-VAGUE-TERM",
                f'"{span.quote}" is subjective or vague; the statement gives no criterion that '
                "fixes what it means",
                rules.ambiguity_signal,
                span,
            )
        )
    for span in find_phrases(statement, rules.loopholes)[:MAX_SPANS_PER_RULE]:
        found.append(
            DetectedFinding(
                QualityFindingType.AMBIGUITY,
                "AMB-LOOPHOLE",
                f'"{span.quote}" leaves the obligation open-ended; who decides when it applies is '
                "not stated",
                rules.ambiguity_signal,
                span,
            )
        )
    pronouns = "|".join(re.escape(p) for p in rules.vague_subject_pronouns)
    modals = "|".join(re.escape(m) for m in rules.modal_verbs)
    subject = re.search(
        rf"^\s*(?:[^,]{{0,80}},\s*)?(?P<p>{pronouns})\s+(?:{modals})\b",
        statement,
        flags=re.IGNORECASE,
    )
    if subject:
        span = Span(subject.start("p"), subject.end("p"), subject.group("p"))
        found.append(
            DetectedFinding(
                QualityFindingType.AMBIGUITY,
                "AMB-PRONOUN-SUBJECT",
                f'the subject is only the pronoun "{span.quote}"; the statement does not say '
                "what it refers to",
                rules.ambiguity_signal,
                span,
            )
        )
    return found


def incompleteness(statement: str, rules: QualityRules) -> list[DetectedFinding]:
    """``FR-QAL-002`` - placeholders, no stated obligation, a passive with no actor."""
    found = [
        DetectedFinding(
            QualityFindingType.INCOMPLETENESS,
            "INC-PLACEHOLDER",
            f'"{span.quote}" is a placeholder: the value it stands for is missing',
            rules.incompleteness_signal,
            span,
        )
        for span in find_phrases(statement, rules.placeholders)[:MAX_SPANS_PER_RULE]
    ]
    tokens = set(words(statement))
    if not tokens & set(rules.modal_verbs):
        found.append(
            DetectedFinding(
                QualityFindingType.INCOMPLETENESS,
                "INC-NO-OBLIGATION",
                "no modal verb (shall, must, ...): the statement does not say what is required",
                rules.incompleteness_signal,
            )
        )
    passive = _PASSIVE.search(statement)
    if passive and not re.search(r"\bby\b", statement[passive.end() :], flags=re.IGNORECASE):
        span = Span(passive.start(), passive.end(), statement[passive.start() : passive.end()])
        found.append(
            DetectedFinding(
                QualityFindingType.INCOMPLETENESS,
                "INC-PASSIVE-NO-ACTOR",
                f'"{span.quote}" is passive and names no actor: who performs it is missing',
                rules.incompleteness_signal,
                span,
            )
        )
    return found


def testability(
    statement: str, rules: QualityRules, categories: Iterable[str] = ()
) -> list[DetectedFinding]:
    """``FR-QAL-003`` - a quality claim, or a performance/availability/usability
    requirement, with no measurable quantity to test it against."""
    if quantities(statement) or re.search(r"\d", statement):
        return []
    terms = find_phrases(statement, rules.quality_terms)
    measurable = sorted(set(categories) & set(rules.measurable_categories))
    if not terms and not measurable:
        return []
    span = terms[0] if terms else None
    about = f'"{span.quote}"' if span else f"a {measurable[0]} requirement"
    return [
        DetectedFinding(
            QualityFindingType.UNTESTABILITY,
            "UNT-NO-MEASURE",
            f"{about} states no measurable criterion (a threshold, a count, a time); a test "
            "cannot decide whether it is met",
            rules.testability_signal,
            span,
        )
    ]


def infeasibility(statement: str, rules: QualityRules) -> list[DetectedFinding]:
    """``FR-QAL-009`` (secondary) - absolute targets no real system can meet."""
    return [
        DetectedFinding(
            QualityFindingType.INFEASIBILITY,
            "INF-ABSOLUTE",
            f'"{span.quote}" is an absolute target; no real system can guarantee it, so the '
            "achievable level must be stated",
            rules.infeasibility_signal,
            span,
        )
        for span in find_phrases(statement, rules.absolute_terms)[:MAX_SPANS_PER_RULE]
    ]


def terminology(
    statement: str, rules: QualityRules, glossary_keys: frozenset[str] = frozenset()
) -> list[DetectedFinding]:
    """``FR-QAL-005`` - acronyms the project glossary does not define."""
    found: list[DetectedFinding] = []
    seen: set[str] = set()
    for match in _ACRONYM.finditer(statement):
        token = match.group(1)
        letters = sum(c.isalpha() for c in token)
        if not rules.acronym_min_length <= len(token) <= rules.acronym_max_length or letters < 2:
            continue
        if (
            token in rules.common_acronyms
            or token.lower() in glossary_keys
            or token.lower() in rules.placeholders
            or token in seen
        ):
            continue
        seen.add(token)
        found.append(
            DetectedFinding(
                QualityFindingType.UNDEFINED_TERM,
                "TERM-UNDEFINED-ACRONYM",
                f'"{token}" is not defined in the project glossary',
                rules.terminology_signal,
                Span(match.start(1), match.end(1), token),
            )
        )
        if len(found) >= MAX_SPANS_PER_RULE:
            break
    return found


def security_privacy(statement: str, rules: QualityRules) -> list[DetectedFinding]:
    """``FR-QAL-008`` - a *signal* for P6: sensitive data with no protection stated."""
    lowered = statement.lower()
    if any(term in lowered for term in rules.protection_terms):
        return []
    found: list[DetectedFinding] = []
    for terms, finding_type, rule_id, what in (
        (
            rules.security_data_terms,
            QualityFindingType.MISSING_SECURITY_CONSIDERATION,
            "SEC-DATA-UNPROTECTED",
            "security",
        ),
        (
            rules.privacy_data_terms,
            QualityFindingType.MISSING_PRIVACY_CONSIDERATION,
            "PRV-DATA-UNPROTECTED",
            "privacy",
        ),
    ):
        spans = find_phrases(statement, terms)
        if spans:
            found.append(
                DetectedFinding(
                    finding_type,
                    rule_id,
                    f'mentions "{spans[0].quote}" but states no {what} consideration '
                    "(protection, access, retention). A signal for the P6 analysis, not a "
                    "determination",
                    rules.security_privacy_signal,
                    spans[0],
                )
            )
    return found


def missing_source(
    source_ref_count: int, unresolved_ref_count: int, rules: QualityRules
) -> list[DetectedFinding]:
    """``FR-QAL-006`` - a version with no source, or with sources that do not resolve."""
    if source_ref_count == 0:
        return [
            DetectedFinding(
                QualityFindingType.MISSING_SOURCE,
                "SRC-NONE",
                "the version cites no source: nothing traces it to a stakeholder or document",
                rules.source_signal,
            )
        ]
    if unresolved_ref_count:
        return [
            DetectedFinding(
                QualityFindingType.MISSING_SOURCE,
                "SRC-UNRESOLVED",
                f"{unresolved_ref_count} of the version's {source_ref_count} source reference(s) "
                "do not resolve to a record in this project",
                rules.source_signal,
            )
        ]
    return []


def check_statement(
    statement: str,
    rules: QualityRules,
    *,
    categories: Iterable[str] = (),
    glossary_keys: frozenset[str] = frozenset(),
    source_ref_count: int = 1,
    unresolved_ref_count: int = 0,
) -> list[DetectedFinding]:
    """Every deterministic check on one statement, in a stable order."""
    return [
        *ambiguity(statement, rules),
        *incompleteness(statement, rules),
        *testability(statement, rules, categories),
        *infeasibility(statement, rules),
        *terminology(statement, rules, glossary_keys),
        *security_privacy(statement, rules),
        *missing_source(source_ref_count, unresolved_ref_count, rules),
    ]
