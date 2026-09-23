"""The P7 ruleset as a typed, validated object (architecture M7, I.2-I.6).

``risk_rules.yaml`` -> :class:`RiskRules`: the approved severity matrix, the
default owner role per category, the deterministic category indicators, which
prior-phase signals feed risk analysis, and the I.6 SDLC aggregation formulas.

Two checks make the matrix trustworthy rather than merely present:

* the loaded matrix must be **total** - all nine cells - which
  :class:`~reqpilot.domain.risk.matrix.RiskMatrix` enforces;
* it must **equal the approved I.3 table** held as a literal in
  :data:`~reqpilot.domain.risk.matrix.APPROVED_CELLS`. A data edit that changed
  a cell would be refused at load time, not discovered later in a rating.

That second check is the point. Architecture I.3 requires the matrix to be
versioned data (``DQ-03``) *and* requires it to be the approved matrix; storing
it as data without pinning it to the approved values would have made the
approved matrix editable by anyone who could edit a YAML file.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from reqpilot.domain.enums import (
    RequirementCategory,
    RiskCategory,
    RiskImpact,
    RiskLikelihood,
    RiskSeverity,
    Role,
)
from reqpilot.domain.errors import RuleConfigurationError
from reqpilot.domain.risk.matrix import APPROVED_CELLS, RiskMatrix
from reqpilot.rules.loader import RuleSet, load_ruleset

RISK_FILE = "risk_rules.yaml"
PACKAGED_RULES_DIR = Path(__file__).resolve().parent / "data"


def _keyword_patterns(words: Iterable[Any]) -> tuple[re.Pattern[str], ...]:
    out = []
    for word in words:
        text = " ".join(str(word).split()).lower()
        if not text:
            raise RuleConfigurationError("a risk indicator has an empty keyword")
        out.append(re.compile(r"(?<![a-z0-9])" + re.escape(text), re.IGNORECASE))
    return tuple(out)


def _matches(patterns: Sequence[re.Pattern[str]], text: str) -> bool:
    normalised = " ".join(text.split())
    return any(p.search(normalised) for p in patterns)


@dataclass(frozen=True)
class RiskIndicator:
    """Which risk category a requirement is deterministically examined for.

    An indicator is a *hint*: it decides what the analysis looks at and what
    context it is given. It never decides a rating, a severity or a gate.
    """

    category: RiskCategory
    keywords: tuple[re.Pattern[str], ...]
    requirement_categories: frozenset[RequirementCategory]

    def indicated_for(self, statement: str, categories: Sequence[str]) -> bool:
        if _matches(self.keywords, statement):
            return True
        labels = {str(c).lower() for c in categories}
        return any(str(c) in labels for c in self.requirement_categories)


@dataclass(frozen=True)
class FactorFormula:
    """One I.6 aggregation formula (``FR-RSK-009``), as versioned data."""

    key: str
    description: str
    categories: frozenset[RiskCategory]
    thresholds: tuple[dict[str, int], ...] = ()
    impact_scores: dict[str, int] | None = None


@dataclass(frozen=True)
class RiskRules:
    """The P7 ruleset: the matrix, ownership, indicators and aggregation."""

    ruleset_ref: str
    matrix: RiskMatrix
    owner_roles: dict[RiskCategory, Role]
    indicators: tuple[RiskIndicator, ...]
    prior_signals: dict[str, RiskCategory]
    factors: tuple[FactorFormula, ...]
    max_risks_per_requirement: int
    max_project_risks_per_run: int
    max_mitigations_per_risk: int
    max_versions_per_run: int

    @classmethod
    def from_ruleset(cls, ruleset: RuleSet) -> RiskRules:
        ref = f"{ruleset.name}@{ruleset.version}"
        matrix_version = str(ruleset.get("matrix_version") or ruleset.version)
        cells: dict[tuple[RiskLikelihood, RiskImpact], RiskSeverity] = {}
        for row in ruleset["matrix"]:
            try:
                likelihood = RiskLikelihood(str(row["likelihood"]))
                impact = RiskImpact(str(row["impact"]))
                severity = RiskSeverity(str(row["severity"]))
            except (KeyError, ValueError) as exc:
                raise RuleConfigurationError(
                    f"{ref}: malformed matrix row {dict(row)} ({exc})"
                ) from exc
            if (likelihood, impact) in cells:
                raise RuleConfigurationError(
                    f"{ref}: matrix cell ({likelihood}, {impact}) is defined twice"
                )
            cells[(likelihood, impact)] = severity
        try:
            matrix = RiskMatrix(version=matrix_version, cells=cells)
        except ValueError as exc:
            raise RuleConfigurationError(f"{ref}: {exc}") from exc

        # The approved matrix is not editable by editing data (architecture I.3).
        differences = [
            f"({likelihood}, {impact}): {cells[(likelihood, impact)]} != {expected}"
            for (likelihood, impact), expected in APPROVED_CELLS.items()
            if cells[(likelihood, impact)] is not expected
        ]
        if differences:
            raise RuleConfigurationError(
                f"{ref}: the matrix does not match the approved architecture I.3 table; "
                f"differences: {differences}. The matrix is versioned data, but its "
                "values are approved - change the architecture first."
            )

        owner_roles: dict[RiskCategory, Role] = {}
        raw_owners = ruleset.get("owner_roles") or {}
        for category in RiskCategory:
            value = raw_owners.get(category.value)
            if value is None:
                raise RuleConfigurationError(f"{ref}: no owner role for risk category {category}")
            try:
                owner_roles[category] = Role(str(value))
            except ValueError as exc:
                raise RuleConfigurationError(f"{ref}: unknown owner role {value!r}") from exc

        indicators = []
        for raw in ruleset.get("indicators") or ():
            try:
                category = RiskCategory(str(raw["category"]))
            except (KeyError, ValueError) as exc:
                raise RuleConfigurationError(f"{ref}: unknown indicator category ({exc})") from exc
            try:
                requirement_categories = frozenset(
                    RequirementCategory(str(c)) for c in raw.get("requirement_categories") or ()
                )
            except ValueError as exc:
                raise RuleConfigurationError(
                    f"{ref}: indicator {category} names an unknown requirement category ({exc})"
                ) from exc
            indicators.append(
                RiskIndicator(
                    category=category,
                    keywords=_keyword_patterns(raw.get("keywords") or ()),
                    requirement_categories=requirement_categories,
                )
            )

        prior_signals: dict[str, RiskCategory] = {}
        for key, value in (ruleset.get("prior_signals") or {}).items():
            try:
                prior_signals[str(key)] = RiskCategory(str(value))
            except ValueError as exc:
                raise RuleConfigurationError(
                    f"{ref}: prior signal {key!r} names an unknown risk category ({exc})"
                ) from exc

        factors = []
        for key, raw in (ruleset.get("sdlc_factors") or {}).items():
            try:
                categories = frozenset(RiskCategory(str(c)) for c in raw.get("categories") or ())
            except ValueError as exc:
                raise RuleConfigurationError(
                    f"{ref}: SDLC factor {key!r} names an unknown risk category ({exc})"
                ) from exc
            factors.append(
                FactorFormula(
                    key=str(key),
                    description=" ".join(str(raw.get("description", "")).split()),
                    categories=categories,
                    thresholds=tuple(dict(t) for t in raw.get("thresholds") or ()),
                    impact_scores=(
                        {str(k): int(v) for k, v in raw["impact_scores"].items()}
                        if raw.get("impact_scores")
                        else None
                    ),
                )
            )

        def _positive(key: str, default: int) -> int:
            value = int(ruleset.get(key, default))
            if value < 1:
                raise RuleConfigurationError(f"{ref}: {key} must be at least 1")
            return value

        return cls(
            ruleset_ref=ref,
            matrix=matrix,
            owner_roles=owner_roles,
            indicators=tuple(indicators),
            prior_signals=prior_signals,
            factors=tuple(sorted(factors, key=lambda f: f.key)),
            max_risks_per_requirement=_positive("max_risks_per_requirement", 6),
            max_project_risks_per_run=_positive("max_project_risks_per_run", 6),
            max_mitigations_per_risk=_positive("max_mitigations_per_risk", 4),
            max_versions_per_run=_positive("max_versions_per_run", 60),
        )

    def owner_for(self, category: RiskCategory) -> Role:
        """The default owner role for a category (I.4). A rule, not a model decision."""
        return self.owner_roles[category]

    def indicated_categories(
        self, statement: str, categories: Sequence[str]
    ) -> tuple[RiskCategory, ...]:
        """The risk categories this requirement is deterministically examined for."""
        found = [i.category for i in self.indicators if i.indicated_for(statement, categories)]
        return tuple(dict.fromkeys(found))

    def factor(self, key: str) -> FactorFormula | None:
        return next((f for f in self.factors if f.key == key), None)


def load_risk_rules(rules_dir: str | Path) -> RiskRules:
    return RiskRules.from_ruleset(load_ruleset(Path(rules_dir) / RISK_FILE))


@lru_cache(maxsize=1)
def packaged_risk_rules() -> RiskRules:
    return load_risk_rules(PACKAGED_RULES_DIR)
