"""Weighted MCDA, the deterministic rule pass and the ranking (architecture L.3, L.4).

**The formula** (architecture L.3), for each candidate ``c``::

    raw(c)   = sum over factors f of  w[f] * S[c][f] * norm(score[f])
    max(c)   = sum over factors f of  w[f] * |S[c][f]|
    mcda(c)  = 50 * (1 + raw(c) / max(c))            # 0..100; 50 when max(c) = 0

``norm`` maps the 1-5 score onto -1..+1. ``mcda`` is the candidate's
suitability on an absolute 0-100 scale: 100 would mean every factor was at the
extreme that favours it most, 0 the opposite, 50 a neutral profile. (Architecture
L.3 says "normalise raw to 0-100" without fixing how; normalising by the
candidate's own attainable range is the P9 choice, recorded in the report, and
it keeps a score comparable across projects rather than relative to the others.)

**The rule pass** (architecture L.4, ``[DESIGN] D18``), after MCDA and in this
fixed order, each triggered rule recorded whether or not it changed anything:

1. ``boost`` - a bounded uplift on the 0-100 score, capped at 100;
2. sort by score (descending), ties broken by the candidate's position in the
   versioned candidate file - never by anything a model said;
3. ``veto`` - a vetoed candidate cannot rank 1st: if it is first, the best
   candidate no triggered veto covers is moved to the top;
4. ``require_top_n`` - if no candidate with the attribute is in the top ``n``,
   the best such candidate is moved to position ``n`` (never above a veto).

The rank is what the rule pass leaves. A candidate's score is never altered by a
veto or a top-``n`` rule, so the MCDA result and the rule effect both stay
visible rather than entangled.

**Reversal analysis** (``FR-SDL-007``): for each factor, the smallest single
change of its score that would put the runner-up first, holding every other
factor fixed - computed by re-running this same pipeline, not by estimation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from reqpilot.domain.sdlc.config import CandidateDefinition, RuleEffect, SdlcConfig
from reqpilot.domain.sdlc.factors import (
    FACTOR_ORDER,
    SCORE_MAX,
    SCORE_MIN,
    FactorId,
    is_valid_score,
    norm,
)

_PRECISION = 6


@dataclass(frozen=True)
class CandidateScore:
    key: str
    label: str
    raw: float
    max_raw: float
    mcda_score: float
    score: float
    rank: int
    contributions: Mapping[str, float]
    vetoed_by: tuple[str, ...] = ()
    boosted_by: tuple[str, ...] = ()
    required_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuleApplication:
    rule_id: str
    effect: RuleEffect
    affected_candidate: str | None
    changed_ranking: bool
    reason: str
    trigger_values: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoringResult:
    profile: Mapping[FactorId, int]
    candidates: tuple[CandidateScore, ...]
    rules: tuple[RuleApplication, ...]

    @property
    def ranking(self) -> tuple[str, ...]:
        return tuple(c.key for c in self.candidates)

    @property
    def top(self) -> CandidateScore:
        return self.candidates[0]

    @property
    def runner_up(self) -> CandidateScore | None:
        return self.candidates[1] if len(self.candidates) > 1 else None

    def by_key(self, key: str) -> CandidateScore | None:
        return next((c for c in self.candidates if c.key == key), None)


@dataclass(frozen=True)
class ReversalCondition:
    """A single-factor change that would put the runner-up first (``FR-SDL-007``)."""

    factor: FactorId
    from_score: int
    to_score: int
    new_top: str

    def render(self) -> str:
        return f"{self.factor} {self.from_score} -> {self.to_score} makes {self.new_top} first"


def _check_profile(profile: Mapping[FactorId, int]) -> None:
    if set(profile) != set(FACTOR_ORDER):
        missing = sorted(str(f) for f in set(FACTOR_ORDER) - set(profile))
        raise ValueError(f"a profile scores all 13 factors; missing {missing}")
    for f, value in profile.items():
        if not is_valid_score(value):
            raise ValueError(f"factor {f} has a score outside 1..5: {value!r}")


def mcda(
    candidate: CandidateDefinition, profile: Mapping[FactorId, int], config: SdlcConfig
) -> tuple[float, float, float, dict[str, float]]:
    """``(raw, max_raw, mcda_score, contributions)`` for one candidate (L.3)."""
    contributions: dict[str, float] = {}
    raw = 0.0
    max_raw = 0.0
    for f in FACTOR_ORDER:
        weight = config.weight(f)
        coefficient = candidate.coefficients[f]
        part = round(weight * coefficient * norm(profile[f]), _PRECISION)
        contributions[str(f)] = part
        raw += part
        max_raw += weight * abs(coefficient)
    raw = round(raw, _PRECISION)
    max_raw = round(max_raw, _PRECISION)
    score = 50.0 if max_raw == 0 else round(50.0 * (1.0 + raw / max_raw), 2)
    return raw, max_raw, score, contributions


def score_candidates(profile: Mapping[FactorId, int], config: SdlcConfig) -> ScoringResult:
    """MCDA, then the rule pass, then the ranking. Pure; the same input, the same output."""
    _check_profile(profile)
    order_index = {c.key: i for i, c in enumerate(config.candidates)}
    base: dict[str, tuple[float, float, float, dict[str, float]]] = {
        c.key: mcda(c, profile, config) for c in config.candidates
    }
    score = {key: values[2] for key, values in base.items()}
    applications: list[RuleApplication] = []
    boosted: dict[str, list[str]] = {k: [] for k in score}
    vetoed: dict[str, list[str]] = {k: [] for k in score}
    required: dict[str, list[str]] = {k: [] for k in score}

    def holders(attribute: str) -> list[str]:
        return [c.key for c in config.candidates if attribute in c.attributes]

    # 1. boosts - a bounded uplift, before the sort.
    for rule in config.rules:
        if rule.effect is not RuleEffect.BOOST or not rule.triggered(profile):
            continue
        for key in holders(rule.target_attribute):
            before = score[key]
            score[key] = round(min(100.0, before + float(rule.uplift or 0)), 2)
            boosted[key].append(rule.id)
            applications.append(
                RuleApplication(
                    rule.id,
                    rule.effect,
                    key,
                    score[key] != before,
                    f"{rule.description} ({key}: {before:g} -> {score[key]:g})",
                    rule.trigger_values(profile),
                )
            )

    # 2. sort: score descending, then the versioned candidate order.
    ranking = sorted(score, key=lambda k: (-score[k], order_index[k]))

    # 3. vetoes - a vetoed candidate cannot be first.
    veto_rules = [r for r in config.rules if r.effect is RuleEffect.VETO and r.triggered(profile)]
    covered: set[str] = set()
    for rule in veto_rules:
        for key in holders(rule.target_attribute):
            covered.add(key)
            vetoed[key].append(rule.id)
    moved_from: str | None = None
    if veto_rules and ranking[0] in covered:
        replacement = next((k for k in ranking if k not in covered), None)
        if replacement is not None:
            moved_from = ranking[0]
            ranking.remove(replacement)
            ranking.insert(0, replacement)
    for rule in veto_rules:
        targets = holders(rule.target_attribute)
        changed = moved_from is not None and moved_from in targets
        applications.append(
            RuleApplication(
                rule.id,
                rule.effect,
                moved_from if changed else (targets[0] if len(targets) == 1 else None),
                changed,
                rule.description
                + (
                    f" ({moved_from} scored highest but may not rank first; "
                    f"{ranking[0]} ranks first)"
                    if changed
                    else " (no vetoed candidate was first; ranking unchanged)"
                ),
                rule.trigger_values(profile),
            )
        )

    # 4. require_top_n - the attribute must appear within the top n.
    for rule in config.rules:
        if rule.effect is not RuleEffect.REQUIRE_TOP_N or not rule.triggered(profile):
            continue
        n = int(rule.n or 1)
        targets = holders(rule.target_attribute)
        for key in targets:
            required[key].append(rule.id)
        if any(k in targets for k in ranking[:n]):
            applications.append(
                RuleApplication(
                    rule.id,
                    rule.effect,
                    None,
                    False,
                    f"{rule.description} (already satisfied; ranking unchanged)",
                    rule.trigger_values(profile),
                )
            )
            continue
        best = next(k for k in ranking if k in targets)
        position = n - 1
        if position == 0 and best in covered:
            position = 1
        ranking.remove(best)
        ranking.insert(position, best)
        applications.append(
            RuleApplication(
                rule.id,
                rule.effect,
                best,
                True,
                f"{rule.description} ({best} moved to position {position + 1})",
                rule.trigger_values(profile),
            )
        )

    labels = {c.key: c.label for c in config.candidates}
    candidates = tuple(
        CandidateScore(
            key=key,
            label=labels[key],
            raw=base[key][0],
            max_raw=base[key][1],
            mcda_score=base[key][2],
            score=score[key],
            rank=position + 1,
            contributions=base[key][3],
            vetoed_by=tuple(vetoed[key]),
            boosted_by=tuple(boosted[key]),
            required_by=tuple(required[key]),
        )
        for position, key in enumerate(ranking)
    )
    return ScoringResult(profile=dict(profile), candidates=candidates, rules=tuple(applications))


def reversal_conditions(
    profile: Mapping[FactorId, int], config: SdlcConfig, result: ScoringResult | None = None
) -> tuple[ReversalCondition, ...]:
    """For each factor, the smallest single score change that puts the runner-up first."""
    result = result or score_candidates(profile, config)
    runner_up = result.runner_up
    if runner_up is None:
        return ()
    out: list[ReversalCondition] = []
    for f in FACTOR_ORDER:
        current = profile[f]
        found: ReversalCondition | None = None
        for delta in range(1, SCORE_MAX - SCORE_MIN + 1):
            for candidate_value in (current + delta, current - delta):
                if not SCORE_MIN <= candidate_value <= SCORE_MAX:
                    continue
                trial = dict(profile)
                trial[f] = candidate_value
                if score_candidates(trial, config).top.key == runner_up.key:
                    found = ReversalCondition(f, current, candidate_value, runner_up.key)
                    break
            if found is not None:
                break
        if found is not None:
            out.append(found)
    return tuple(out)
