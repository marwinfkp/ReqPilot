"""The typed shape of the versioned SDLC ruleset (architecture L.1-L.4, M7; ``DQ-03``).

Candidates, factor weights, suitability coefficients, the declarative rules and
the derivation bands are *data* (``rules/data/sdlc_rules.yaml``), loaded by
:mod:`reqpilot.rules.sdlc` into these frozen objects. The objects check their own
invariants - every ``[PS §13]`` factor present exactly once, every coefficient an
integer in ``[-2, +2]`` (architecture L.3), every rule naming a factor and a
candidate attribute that exist - so a malformed ruleset is refused at load time,
never discovered in a ranking.

Every number in the ruleset is a **project/engineering decision** (``[PROJ]``)
unless the ruleset says otherwise: the problem statement fixes the factors, the
candidates and the hybrid approach, and architecture L fixes the formula shape,
but no approved source fixes a weight or a coefficient.
"""

from __future__ import annotations

import math
import operator
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from reqpilot.domain.errors import RuleConfigurationError
from reqpilot.domain.sdlc.factors import FACTOR_ORDER, FactorId, is_valid_score

COEFFICIENT_MIN = -2
COEFFICIENT_MAX = 2


def round_half_up(value: float) -> int:
    """Conventional rounding (2.5 -> 3), not banker's: stated so a hand check agrees."""
    return math.floor(value + 0.5)


@dataclass(frozen=True)
class Band:
    """``value`` applies when the measured quantity is at least ``minimum``."""

    minimum: float
    value: int


def band_value(bands: Sequence[Band], measure: float) -> int:
    """The value of the highest band whose minimum ``measure`` reaches.

    Bands are sorted ascending by minimum and the first starts at 0, which the
    loader checks; a negative measure therefore still falls in the first band.
    """
    chosen = bands[0].value
    for band in bands:
        if measure >= band.minimum:
            chosen = band.value
    return chosen


def _check_bands(name: str, bands: Sequence[Band]) -> None:
    if not bands:
        raise RuleConfigurationError(f"derivation band {name!r} is empty")
    if bands[0].minimum != 0:
        raise RuleConfigurationError(f"derivation band {name!r} must start at 0")
    minima = [b.minimum for b in bands]
    if minima != sorted(minima) or len(set(minima)) != len(minima):
        raise RuleConfigurationError(f"derivation band {name!r} must ascend strictly")
    for band in bands:
        if not is_valid_score(band.value):
            raise RuleConfigurationError(f"derivation band {name!r} has a value outside 1..5")


@dataclass(frozen=True)
class FactorDefinition:
    """One factor: its weight and the anchors that make a score defensible (L.2)."""

    id: FactorId
    label: str
    weight: float
    #: Anchor descriptions for scores 1, 3 and 5 (architecture L.2).
    anchors: Mapping[int, str]
    description: str = ""


@dataclass(frozen=True)
class CandidateDefinition:
    """One SDLC candidate (architecture L.1) and its suitability coefficients."""

    key: str
    label: str
    #: What the candidate *contains*, for rules: ``waterfall``, ``v_model``,
    #: ``devsecops``, ``agile``, ``spiral``, ``hybrid``, ``pure_waterfall``.
    attributes: frozenset[str]
    #: ``S[c][f]`` in ``[-2, +2]`` (architecture L.3): how strongly a *high*
    #: score on factor ``f`` favours (+) or disfavours (-) this candidate.
    coefficients: Mapping[FactorId, int]
    description: str = ""


class RuleEffect(StrEnum):
    """The three declarative effects of architecture L.4 (``[DESIGN] D18``)."""

    VETO = "veto"
    REQUIRE_TOP_N = "require_top_n"
    BOOST = "boost"


_OPERATORS: dict[str, Callable[[int, int], bool]] = {
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    ">": operator.gt,
    "<": operator.lt,
}


@dataclass(frozen=True)
class Condition:
    """``factor op value`` over the effective factor profile."""

    factor: FactorId
    op: str
    value: int

    def __post_init__(self) -> None:
        if self.op not in _OPERATORS:
            raise RuleConfigurationError(f"unknown rule operator {self.op!r}")
        if not is_valid_score(self.value):
            raise RuleConfigurationError(f"a rule compares against 1..5, not {self.value!r}")

    def holds(self, scores: Mapping[FactorId, int]) -> bool:
        return _OPERATORS[self.op](scores[self.factor], self.value)

    def render(self) -> str:
        return f"{self.factor} {self.op} {self.value}"


@dataclass(frozen=True)
class SdlcRule:
    """One declarative rule, applied after MCDA (architecture L.4).

    * ``veto`` - a candidate with ``target_attribute`` cannot rank 1st;
    * ``require_top_n`` - a candidate with ``target_attribute`` must appear in
      the top ``n``;
    * ``boost`` - candidates with ``target_attribute`` gain ``uplift`` points on
      the 0-100 scale, capped at 100 (a *bounded* uplift).

    All ``conditions`` must hold for the rule to trigger.
    """

    id: str
    effect: RuleEffect
    conditions: tuple[Condition, ...]
    target_attribute: str
    description: str
    provenance: str
    n: int | None = None
    uplift: float | None = None

    def __post_init__(self) -> None:
        if not self.conditions:
            raise RuleConfigurationError(f"rule {self.id} has no condition")
        if self.effect is RuleEffect.REQUIRE_TOP_N and (self.n is None or self.n < 1):
            raise RuleConfigurationError(f"rule {self.id} needs n >= 1")
        if self.effect is RuleEffect.BOOST and (self.uplift is None or not 0 < self.uplift <= 25):
            raise RuleConfigurationError(f"rule {self.id} needs a bounded uplift in (0, 25]")

    def triggered(self, scores: Mapping[FactorId, int]) -> bool:
        return all(c.holds(scores) for c in self.conditions)

    def trigger_values(self, scores: Mapping[FactorId, int]) -> dict[str, int]:
        return {str(c.factor): scores[c.factor] for c in self.conditions}


@dataclass(frozen=True)
class DerivationConfig:
    """The bands and parameters that turn approved facts into factor scores."""

    bands: Mapping[str, tuple[Band, ...]]
    params: Mapping[str, float] = field(default_factory=dict)

    def band(self, name: str) -> tuple[Band, ...]:
        try:
            return self.bands[name]
        except KeyError as exc:  # pragma: no cover - the loader checks the names
            raise RuleConfigurationError(f"derivation band {name!r} is not defined") from exc

    def param(self, name: str) -> float:
        try:
            return float(self.params[name])
        except KeyError as exc:  # pragma: no cover - the loader checks the names
            raise RuleConfigurationError(f"derivation parameter {name!r} is not defined") from exc


#: The band names the derivation reads (architecture L.2 via FR-SDL-001).
REQUIRED_BANDS: tuple[str, ...] = (
    "stability_churn",
    "normative_sources",
    "open_compliance_gaps",
    "system_size",
    "integration_requirements",
    "legacy_requirements",
    "change_ratio",
    "delivery_signals",
    "interviewed_ratio",
    "acceptance_criteria_ratio",
    "schedule_signals",
    "verification_signals",
)
REQUIRED_PARAMS: tuple[str, ...] = (
    "open_clarification_penalty_at",
    "documentation_bonus_at",
)


@dataclass(frozen=True)
class ConsistencyConfig:
    """How the explanation is compared with the computed result (``[DESIGN] D9``)."""

    #: How far an asserted suitability score may be from the computed one (0-100).
    score_tolerance: float
    #: Regenerations after a first mismatch (architecture L.5: one).
    max_regenerations: int
    #: Phrases that claim the model, not the rules, chose the SDLC.
    forbidden_claims: tuple[str, ...]


@dataclass(frozen=True)
class SdlcConfig:
    """One loaded, validated SDLC ruleset."""

    name: str
    version: str
    ruleset_ref: str
    weights_version: str
    weights_ref: str
    factors: Mapping[FactorId, FactorDefinition]
    candidates: tuple[CandidateDefinition, ...]
    rules: tuple[SdlcRule, ...]
    derivation: DerivationConfig
    consistency: ConsistencyConfig
    #: How far a model's factor proposal may move a derived score (0 = never).
    max_proposal_deviation: int

    def __post_init__(self) -> None:
        if set(self.factors) != set(FACTOR_ORDER):
            missing = sorted(str(f) for f in set(FACTOR_ORDER) - set(self.factors))
            raise RuleConfigurationError(
                f"the ruleset must define all 13 factors; missing {missing}"
            )
        for definition in self.factors.values():
            if not definition.weight > 0:
                raise RuleConfigurationError(f"factor {definition.id} needs a positive weight")
            if set(definition.anchors) != {1, 3, 5}:
                raise RuleConfigurationError(f"factor {definition.id} needs anchors for 1, 3, 5")
        keys = [c.key for c in self.candidates]
        if len(keys) < 2 or len(set(keys)) != len(keys):
            raise RuleConfigurationError("candidates must be at least two, with unique keys")
        for candidate in self.candidates:
            if set(candidate.coefficients) != set(FACTOR_ORDER):
                raise RuleConfigurationError(
                    f"candidate {candidate.key} needs a coefficient for every factor"
                )
            for f, s in candidate.coefficients.items():
                if (
                    not isinstance(s, int)
                    or isinstance(s, bool)
                    or not COEFFICIENT_MIN <= s <= COEFFICIENT_MAX
                ):
                    raise RuleConfigurationError(
                        f"coefficient {candidate.key}/{f} must be an integer in [-2, +2]"
                    )
        attributes = {a for c in self.candidates for a in c.attributes}
        rule_ids = [r.id for r in self.rules]
        if len(set(rule_ids)) != len(rule_ids):
            raise RuleConfigurationError("rule ids must be unique")
        for rule in self.rules:
            if rule.target_attribute not in attributes:
                raise RuleConfigurationError(
                    f"rule {rule.id} targets {rule.target_attribute!r}, which no candidate has"
                )
        for name in REQUIRED_BANDS:
            _check_bands(name, self.derivation.bands.get(name, ()))
        for name in REQUIRED_PARAMS:
            if name not in self.derivation.params:
                raise RuleConfigurationError(f"derivation parameter {name!r} is missing")
        if not 0 <= self.max_proposal_deviation <= 4:
            raise RuleConfigurationError("max_proposal_deviation must be within 0..4")
        if self.consistency.score_tolerance < 0 or self.consistency.max_regenerations < 0:
            raise RuleConfigurationError("consistency tolerances must be non-negative")

    @property
    def candidate_keys(self) -> tuple[str, ...]:
        return tuple(c.key for c in self.candidates)

    def candidate(self, key: str) -> CandidateDefinition | None:
        return next((c for c in self.candidates if c.key == key), None)

    def weight(self, factor: FactorId) -> float:
        return self.factors[factor].weight
