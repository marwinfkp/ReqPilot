"""Duplicate detection, the conflict shortlist and the deterministic contradiction rules.

Architecture E #6 (hybrid) and ``FR-CNF-004``: pairs are shortlisted
*deterministically* - content-word overlap, embedding similarity supplied by the
caller, shared topic families, shared quantity classes - so the model is asked
about a bounded list of pairs rather than every pair in the repository.

The contradiction rules then settle the obvious cases and nothing more:

* two bounds on the same attribute that cannot both hold (``at least 500`` vs
  ``a maximum of 100`` sessions), and
* the same obligation stated and negated (``send ... by SMS`` vs ``not send ...
  by SMS``),

and only when the two statements are about the same thing (enough shared
content words), under the same stated conditions, for overlapping actors. Where
conditions, actors or wording differ the rules claim nothing definite: the pair
goes to the adjudicator, or - without one - is reported as a *potential*
conflict for a human. A compatible-looking pair is never declared compatible by
the rules alone.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from reqpilot.domain.enums import ConflictKind, ConflictVerdict
from reqpilot.domain.quality.text import (
    Quantity,
    content_stems,
    find_phrases,
    has_prefix_stem,
    overlap_coefficient,
    quantities,
    words,
)
from reqpilot.domain.similarity import is_exact_duplicate, token_jaccard
from reqpilot.rules.quality import QualityRules


@dataclass(frozen=True)
class PairItem:
    """One requirement version as the pair rules see it."""

    key: str
    statement: str
    #: Who the version traces to, from its sources (deterministic; ``FR-CNF-002``).
    stakeholder: str | None = None
    #: Versions of one requirement are never compared with each other.
    requirement_key: str | None = None


@dataclass(frozen=True)
class DuplicatePair:
    a: str
    b: str
    exact: bool
    similarity: float


@dataclass(frozen=True)
class Candidate:
    """A shortlisted pair, in canonical order (``a < b``)."""

    a: str
    b: str
    score: float
    reasons: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class RuleVerdict:
    """What the deterministic rules say about one shortlisted pair."""

    verdict: ConflictVerdict
    #: The rules could not settle the pair; there is a contradiction signal.
    signal: bool
    kind: ConflictKind
    rule_id: str
    rationale: str
    evidence_a: str
    evidence_b: str

    @property
    def definite(self) -> bool:
        return self.verdict is ConflictVerdict.DEFINITE_CONFLICT


def canonical(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


# ---------------------------------------------------------------------------
# Duplicates (FR-QAL-004)
# ---------------------------------------------------------------------------


def duplicates(items: Sequence[PairItem], rules: QualityRules) -> list[DuplicatePair]:
    """Every exact or near-duplicate pair, in canonical order. O(n^2) arithmetic only."""
    found: list[DuplicatePair] = []
    ordered = sorted(items, key=lambda i: i.key)
    for i, left in enumerate(ordered):
        for right in ordered[i + 1 :]:
            if left.requirement_key and left.requirement_key == right.requirement_key:
                continue
            if is_exact_duplicate(left.statement, right.statement):
                found.append(DuplicatePair(left.key, right.key, True, 1.0))
                continue
            # "X" and "not X" overlap almost entirely; they conflict, not duplicate.
            if negated(left.statement, rules) != negated(right.statement, rules):
                continue
            similarity = token_jaccard(left.statement, right.statement)
            if similarity >= rules.near_duplicate_similarity:
                found.append(DuplicatePair(left.key, right.key, False, round(similarity, 4)))
    return found


# ---------------------------------------------------------------------------
# Shortlist (FR-CNF-004)
# ---------------------------------------------------------------------------


def _families(statement: str, rules: QualityRules) -> frozenset[str]:
    return frozenset(
        name for name, stems in rules.topic_families.items() if has_prefix_stem(statement, stems)
    )


def _unit_classes(statement: str) -> frozenset[str]:
    return frozenset(q.unit_class for q in quantities(statement))


def shortlist(
    items: Sequence[PairItem],
    rules: QualityRules,
    *,
    similarity: Callable[[str, str], float] | None = None,
    focus: frozenset[str] | None = None,
) -> list[Candidate]:
    """The pairs worth adjudicating, best first, bounded by the rules.

    ``similarity(a_key, b_key)`` is the embedding cosine the caller computed
    (transiently; nothing is stored). ``focus`` limits pairs to those touching
    at least one of the given keys - a re-analysed version against the set.
    """
    stems = {i.key: content_stems(i.statement) for i in items}
    families = {i.key: _families(i.statement, rules) for i in items}
    units = {i.key: _unit_classes(i.statement) for i in items}
    scored: list[Candidate] = []
    ordered = sorted(items, key=lambda i: i.key)
    for i, left in enumerate(ordered):
        for right in ordered[i + 1 :]:
            if focus is not None and left.key not in focus and right.key not in focus:
                continue
            if left.requirement_key and left.requirement_key == right.requirement_key:
                continue
            lexical = overlap_coefficient(stems[left.key], stems[right.key])
            semantic = similarity(left.key, right.key) if similarity else 0.0
            score = max(lexical, semantic)
            reasons = [f"overlap={lexical:.2f}"]
            if similarity:
                reasons.append(f"embedding={semantic:.2f}")
            shared_family = families[left.key] & families[right.key]
            if shared_family:
                score += rules.topic_family_bonus
                reasons.append("family=" + ",".join(sorted(shared_family)))
            shared_units = units[left.key] & units[right.key]
            if shared_units:
                score += rules.unit_class_bonus
                reasons.append("units=" + ",".join(sorted(shared_units)))
            if score >= rules.shortlist_threshold:
                scored.append(Candidate(left.key, right.key, round(score, 4), tuple(reasons)))

    # Each requirement keeps its top-k partners; a pair survives if it is in the
    # top k of either side. Then the run's global cap, best first.
    by_key: dict[str, list[Candidate]] = {}
    for candidate in scored:
        by_key.setdefault(candidate.a, []).append(candidate)
        by_key.setdefault(candidate.b, []).append(candidate)
    kept: set[tuple[str, str]] = set()
    for partners in by_key.values():
        partners.sort(key=lambda c: (-c.score, c.a, c.b))
        kept.update((c.a, c.b) for c in partners[: rules.top_k_per_requirement])
    survivors = [c for c in scored if (c.a, c.b) in kept]
    survivors.sort(key=lambda c: (-c.score, c.a, c.b))
    return survivors[: rules.max_pairs_per_run]


# ---------------------------------------------------------------------------
# Deterministic contradiction rules
# ---------------------------------------------------------------------------


def conditions(statement: str, rules: QualityRules) -> frozenset[str]:
    """The scoping conditions a statement states: each marker and the words after it."""
    found: set[str] = set()
    lowered = statement.lower()
    for span in find_phrases(statement, rules.condition_markers):
        tail = words(lowered[span.end :])[:3]
        found.add(" ".join([span.quote.lower(), *tail]))
    return frozenset(found)


def actors(statement: str, rules: QualityRules) -> frozenset[str]:
    return frozenset(w.rstrip("s") for w in words(statement) if w in rules.actor_terms)


def negated(statement: str, rules: QualityRules) -> bool:
    return bool(find_phrases(statement, rules.negation_markers))


def _by_class(qs: Sequence[Quantity]) -> dict[str, list[Quantity]]:
    grouped: dict[str, list[Quantity]] = {}
    for q in qs:
        grouped.setdefault(q.unit_class, []).append(q)
    return grouped


def _none(
    rationale: str,
    *,
    signal: bool = False,
    kind: ConflictKind = ConflictKind.OTHER,
    evidence: tuple[str, str] = ("", ""),
    rule_id: str = "CNF-NONE",
) -> RuleVerdict:
    return RuleVerdict(
        ConflictVerdict.NO_CONFLICT, signal, kind, rule_id, rationale, evidence[0], evidence[1]
    )


def judge_pair(a: PairItem, b: PairItem, rules: QualityRules) -> RuleVerdict:
    """The deterministic verdict on one pair: duplicate, definite conflict, or undecided.

    Undecided pairs carry ``signal=True`` when the rules saw a possible
    contradiction they could not settle (different values of one attribute,
    or opposite polarity) - those become *potential* conflicts when no
    adjudicator is available.
    """
    same_polarity = negated(a.statement, rules) == negated(b.statement, rules)
    if is_exact_duplicate(a.statement, b.statement) or (
        same_polarity
        and token_jaccard(a.statement, b.statement) >= rules.conflict_duplicate_similarity
    ):
        return RuleVerdict(
            ConflictVerdict.DUPLICATE,
            False,
            ConflictKind.OTHER,
            "CNF-DUPLICATE",
            "the two statements say the same thing; a duplicate, not a conflict",
            "",
            "",
        )

    negators = [m for m in rules.negation_markers if " " not in m]
    stems_a = content_stems(a.statement, extra_stop=negators)
    stems_b = content_stems(b.statement, extra_stop=negators)
    about_same = overlap_coefficient(stems_a, stems_b)
    same_conditions = conditions(a.statement, rules) == conditions(b.statement, rules)
    actors_a, actors_b = actors(a.statement, rules), actors(b.statement, rules)
    actors_disjoint = bool(actors_a and actors_b and not actors_a & actors_b)
    settled_scope = same_conditions and not actors_disjoint

    # --- numeric / timing: two bounds on one attribute --------------------------
    qa, qb = _by_class(quantities(a.statement)), _by_class(quantities(b.statement))
    for unit_class in sorted(set(qa) & set(qb)):
        for left in qa[unit_class]:
            for right in qb[unit_class]:
                kind = ConflictKind.TIMING if unit_class == "time" else ConflictKind.NUMERIC
                evidence = (left.quote, right.quote)
                if left.disjoint_from(right) and about_same >= rules.numeric_rule_overlap:
                    if settled_scope:
                        return RuleVerdict(
                            ConflictVerdict.DEFINITE_CONFLICT,
                            False,
                            kind,
                            "CNF-NUMERIC-DISJOINT",
                            f'"{left.quote}" and "{right.quote}" bound the same '
                            f"{unit_class.split(':')[-1]} "
                            "attribute with ranges that cannot both hold, under the same "
                            "stated conditions",
                            *evidence,
                        )
                    return _none(
                        "incompatible bounds, but the conditions or actors differ",
                        signal=True,
                        kind=kind,
                        evidence=evidence,
                        rule_id="CNF-NUMERIC-SCOPED",
                    )
                if (
                    about_same >= rules.numeric_rule_overlap
                    and not math.isclose(left.point, right.point)
                    and settled_scope
                ):
                    return _none(
                        "different values for what may be the same attribute",
                        signal=True,
                        kind=kind,
                        evidence=evidence,
                        rule_id="CNF-NUMERIC-DIFFERENT",
                    )

    # --- logical: the same obligation stated and negated --------------------------
    if negated(a.statement, rules) != negated(b.statement, rules):
        evidence = (a.statement.strip(), b.statement.strip())
        if about_same >= rules.negation_rule_overlap and settled_scope:
            return RuleVerdict(
                ConflictVerdict.DEFINITE_CONFLICT,
                False,
                ConflictKind.LOGICAL,
                "CNF-NEGATION",
                "one statement requires what the other forbids, about the same subject and "
                "under the same stated conditions",
                *evidence,
            )
        if about_same >= rules.numeric_rule_overlap:
            return _none(
                "opposite polarity about related subjects",
                signal=True,
                kind=ConflictKind.LOGICAL,
                evidence=evidence,
                rule_id="CNF-NEGATION-UNSETTLED",
            )
    return _none("no contradiction the rules can see")
