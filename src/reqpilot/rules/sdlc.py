"""The P9 SDLC ruleset as a typed, validated object (architecture M7, L.1-L.5).

``sdlc_rules.yaml`` -> :class:`SdlcRules`: the domain-level
:class:`~reqpilot.domain.sdlc.config.SdlcConfig` (candidates, weights,
coefficients, rules, derivation bands, consistency settings) plus the evidence
signals - the keywords and classification categories the evidence collector
uses to decide which approved requirement versions count towards a signal.

The domain object validates its own invariants (all 13 factors, coefficients in
``[-2, +2]``, rules naming real attributes); this loader adds the parsing and a
``ruleset_ref`` that pins the file's content, so a run can prove which bytes
produced its ranking.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from reqpilot.domain.enums import RequirementCategory
from reqpilot.domain.errors import RuleConfigurationError
from reqpilot.domain.integrity import file_canonical_sha256
from reqpilot.domain.sdlc.config import (
    Band,
    CandidateDefinition,
    Condition,
    ConsistencyConfig,
    DerivationConfig,
    FactorDefinition,
    RuleEffect,
    SdlcConfig,
    SdlcRule,
)
from reqpilot.domain.sdlc.factors import FactorId
from reqpilot.rules.loader import RuleSet, load_ruleset

SDLC_FILE = "sdlc_rules.yaml"
PACKAGED_RULES_DIR = Path(__file__).resolve().parent / "data"

#: The evidence signals the collector counts; a ruleset must define each.
SIGNAL_NAMES: tuple[str, ...] = (
    "integration",
    "legacy",
    "change",
    "delivery",
    "verification",
    "documentation",
    "schedule",
)


def _patterns(words: Iterable[Any], ref: str, name: str) -> tuple[re.Pattern[str], ...]:
    out = []
    for word in words:
        text = " ".join(str(word).split()).lower()
        if not text:
            raise RuleConfigurationError(f"{ref}: signal {name!r} has an empty keyword")
        out.append(re.compile(r"(?<![a-z0-9])" + re.escape(text), re.IGNORECASE))
    return tuple(out)


@dataclass(frozen=True)
class SignalRule:
    """Which approved requirement versions count towards one evidence signal."""

    name: str
    keywords: tuple[re.Pattern[str], ...]
    categories: frozenset[RequirementCategory]

    def matches(self, statement: str, categories: Sequence[str]) -> bool:
        normalised = " ".join(statement.split())
        if any(p.search(normalised) for p in self.keywords):
            return True
        labels = {str(c).lower() for c in categories}
        return any(c.value in labels for c in self.categories)


@dataclass(frozen=True)
class SdlcRules:
    config: SdlcConfig
    signals: dict[str, SignalRule]
    #: sha256 of the ruleset file (canonical line endings) - what a run pins.
    content_sha256: str

    @property
    def ruleset_ref(self) -> str:
        return self.config.ruleset_ref

    @property
    def weights_ref(self) -> str:
        return self.config.weights_ref

    def signal(self, name: str) -> SignalRule:
        return self.signals[name]

    @classmethod
    def from_ruleset(cls, ruleset: RuleSet, content_sha256: str) -> SdlcRules:
        ref = f"{ruleset.name}@{ruleset.version}"
        try:
            factors = {}
            for key, raw in (ruleset["factors"] or {}).items():
                fid = FactorId(str(key))
                anchors = {int(k): " ".join(str(v).split()) for k, v in raw["anchors"].items()}
                factors[fid] = FactorDefinition(
                    id=fid,
                    label=str(raw["label"]),
                    weight=float(raw["weight"]),
                    anchors=anchors,
                    description=str(raw.get("description", "")),
                )
            candidates = tuple(
                CandidateDefinition(
                    key=str(raw["key"]),
                    label=str(raw["label"]),
                    attributes=frozenset(str(a) for a in raw.get("attributes") or ()),
                    coefficients={
                        FactorId(str(k)): _integer(v, ref) for k, v in raw["coefficients"].items()
                    },
                    description=" ".join(str(raw.get("description", "")).split()),
                )
                for raw in ruleset["candidates"]
            )
            rules = tuple(
                SdlcRule(
                    id=str(raw["id"]),
                    effect=RuleEffect(str(raw["effect"])),
                    conditions=tuple(
                        Condition(
                            FactorId(str(c["factor"])), str(c["op"]), _integer(c["value"], ref)
                        )
                        for c in raw["when"]
                    ),
                    target_attribute=str(raw["target_attribute"]),
                    description=" ".join(str(raw["description"]).split()),
                    provenance=str(raw.get("provenance", "[PROJ]")),
                    n=_integer(raw["n"], ref) if raw.get("n") is not None else None,
                    uplift=float(raw["uplift"]) if raw.get("uplift") is not None else None,
                )
                for raw in ruleset["sdlc_rules"]
            )
            derivation_raw = ruleset["derivation"]
            derivation = DerivationConfig(
                bands={
                    str(name): tuple(
                        Band(minimum=float(b["min"]), value=_integer(b["value"], ref))
                        for b in bands
                    )
                    for name, bands in derivation_raw["bands"].items()
                },
                params={str(k): float(v) for k, v in (derivation_raw.get("params") or {}).items()},
            )
            consistency_raw = ruleset["consistency"]
            forbidden = tuple(str(p) for p in consistency_raw.get("forbidden_claims") or ())
            for pattern in forbidden:
                re.compile(pattern)
            consistency = ConsistencyConfig(
                score_tolerance=float(consistency_raw["score_tolerance"]),
                max_regenerations=_integer(consistency_raw["max_regenerations"], ref),
                forbidden_claims=forbidden,
            )
            weights_version = str((ruleset.get("weights") or {}).get("version") or ruleset.version)
            config = SdlcConfig(
                name=ruleset.name,
                version=ruleset.version,
                ruleset_ref=f"{ref}#{content_sha256[:12]}",
                weights_version=weights_version,
                weights_ref=f"{ruleset.name}.weights@{weights_version}",
                factors=factors,
                candidates=candidates,
                rules=rules,
                derivation=derivation,
                consistency=consistency,
                max_proposal_deviation=_integer(ruleset.get("max_proposal_deviation", 1), ref),
            )
        except (KeyError, TypeError, ValueError, re.error) as exc:
            if isinstance(exc, RuleConfigurationError):
                raise
            raise RuleConfigurationError(f"{ref}: malformed SDLC ruleset ({exc})") from exc

        signals: dict[str, SignalRule] = {}
        raw_signals = ruleset.get("signals") or {}
        for name in SIGNAL_NAMES:
            raw = raw_signals.get(name)
            if raw is None:
                raise RuleConfigurationError(f"{ref}: evidence signal {name!r} is not defined")
            try:
                categories = frozenset(
                    RequirementCategory(str(c)) for c in raw.get("categories") or ()
                )
            except ValueError as exc:
                raise RuleConfigurationError(
                    f"{ref}: signal {name!r} names an unknown requirement category ({exc})"
                ) from exc
            signals[name] = SignalRule(
                name=name,
                keywords=_patterns(raw.get("keywords") or (), ref, name),
                categories=categories,
            )
        return cls(config=config, signals=signals, content_sha256=content_sha256)


def _integer(value: Any, ref: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float) or int(value) != value:
        raise RuleConfigurationError(f"{ref}: expected an integer, got {value!r}")
    return int(value)


def load_sdlc_rules(rules_dir: str | Path) -> SdlcRules:
    path = Path(rules_dir) / SDLC_FILE
    return SdlcRules.from_ruleset(load_ruleset(path), file_canonical_sha256(path))


@lru_cache(maxsize=1)
def packaged_sdlc_rules() -> SdlcRules:
    return load_sdlc_rules(PACKAGED_RULES_DIR)
