"""The P6 rulesets as typed, validated objects (architecture M7, K.2, I.7).

* ``compliance_checklists.yaml`` -> :class:`ComplianceRules`: the expected-control
  checklist per (domain x jurisdiction) that gap detection subtracts from, and
  the source types whose interpretation is always high-impact.
* ``security_risk_rules.yaml`` -> :class:`SecurityRules`: the security/privacy
  control families, their deterministic triggers and the catalogue risk floors.

Both are versioned data (DQ-03). A malformed file is a startup error, never a
silently different policy. The security catalogue is also checked against the
architecture: it may add high-impact families but may not omit one of I.7's.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from reqpilot.domain.compliance.risk import ARCHITECTURE_HIGH_IMPACT_FAMILIES
from reqpilot.domain.enums import (
    NormativeSourceType,
    ObligationKind,
    RequirementCategory,
    SecurityControlFamily,
    SecurityPrivacyCategory,
    SecurityRiskLevel,
)
from reqpilot.domain.errors import RuleConfigurationError
from reqpilot.rules.loader import RuleSet, load_ruleset

CHECKLIST_FILE = "compliance_checklists.yaml"
SECURITY_FILE = "security_risk_rules.yaml"
PACKAGED_RULES_DIR = Path(__file__).resolve().parent / "data"

_KEY = re.compile(r"^[A-Z][A-Z0-9-]{2,63}$")


def _keyword_patterns(words: Iterable[Any]) -> tuple[re.Pattern[str], ...]:
    """Keywords match at a word start, case-insensitively (a stem like ``authenticat`` too)."""
    out = []
    for word in words:
        text = " ".join(str(word).split()).lower()
        if not text:
            raise RuleConfigurationError("a keyword list contains an empty keyword")
        out.append(re.compile(r"(?<![a-z0-9])" + re.escape(text), re.IGNORECASE))
    return tuple(out)


def _matches(patterns: Sequence[re.Pattern[str]], text: str) -> bool:
    normalised = " ".join(text.split())
    return any(p.search(normalised) for p in patterns)


def _categories(values: Iterable[Any], where: str) -> frozenset[RequirementCategory]:
    try:
        return frozenset(RequirementCategory(str(v)) for v in values)
    except ValueError as exc:
        raise RuleConfigurationError(f"{where}: unknown requirement category ({exc})") from exc


# ---------------------------------------------------------------------------
# compliance checklists
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChecklistControl:
    """One expected control of one checklist (K.2)."""

    key: str
    title: str
    family: SecurityControlFamily | None
    obligation_kind: ObligationKind
    high_impact: bool
    evidence_tags: frozenset[str]
    categories: frozenset[RequirementCategory]
    keywords: tuple[re.Pattern[str], ...]

    def indicated_for(self, statement: str, categories: Iterable[str]) -> bool:
        """Deterministic applicability: a hint and a retrieval narrowing, never coverage."""
        labels = {str(c) for c in categories}
        return bool(labels & {c.value for c in self.categories}) or _matches(
            self.keywords, statement
        )


@dataclass(frozen=True)
class Checklist:
    domain: str
    jurisdiction: str
    controls: tuple[ChecklistControl, ...]

    def get(self, key: str) -> ChecklistControl | None:
        return next((c for c in self.controls if c.key == key), None)


@dataclass(frozen=True)
class ComplianceRules:
    name: str
    version: str
    high_impact_source_types: frozenset[NormativeSourceType]
    retrieval_top_k: int
    max_versions_per_run: int
    max_mappings_per_requirement: int
    checklists: tuple[Checklist, ...]

    @property
    def ruleset_ref(self) -> str:
        return f"{self.name}@{self.version}"

    def checklists_for(self, domain: str | None, jurisdictions: Iterable[str]) -> list[Checklist]:
        """The checklists applicable to a project: its domain x each of its jurisdictions."""
        wanted = {str(j).upper() for j in jurisdictions}
        key = (domain or "").strip().lower()
        return [c for c in self.checklists if c.domain == key and c.jurisdiction in wanted]

    @classmethod
    def from_ruleset(cls, ruleset: RuleSet) -> ComplianceRules:
        try:
            source_types = frozenset(
                NormativeSourceType(str(v)) for v in ruleset["high_impact_source_types"]
            )
            checklists: list[Checklist] = []
            seen: set[tuple[str, str]] = set()
            for raw in ruleset["checklists"]:
                domain = str(raw["domain"]).strip().lower()
                jurisdiction = str(raw["jurisdiction"]).strip().upper()
                if (domain, jurisdiction) in seen:
                    raise RuleConfigurationError(
                        f"duplicate checklist for {domain} x {jurisdiction}"
                    )
                seen.add((domain, jurisdiction))
                controls: list[ChecklistControl] = []
                keys: set[str] = set()
                for item in raw["controls"]:
                    key = str(item["key"])
                    if not _KEY.match(key) or key in keys:
                        raise RuleConfigurationError(f"invalid or duplicate control key {key!r}")
                    keys.add(key)
                    tags = frozenset(str(t).strip().lower() for t in item["evidence_tags"])
                    if not tags:
                        raise RuleConfigurationError(f"control {key} names no evidence tags")
                    family = item.get("family")
                    controls.append(
                        ChecklistControl(
                            key=key,
                            title=str(item["title"]).strip(),
                            family=SecurityControlFamily(str(family)) if family else None,
                            obligation_kind=ObligationKind(str(item["obligation_kind"])),
                            high_impact=bool(item["high_impact"]),
                            evidence_tags=tags,
                            categories=_categories(item.get("categories", ()), key),
                            keywords=_keyword_patterns(item.get("keywords", ())),
                        )
                    )
                if not controls:
                    raise RuleConfigurationError(f"checklist {domain} x {jurisdiction} is empty")
                checklists.append(Checklist(domain, jurisdiction, tuple(controls)))
            rules = cls(
                name=ruleset.name,
                version=ruleset.version,
                high_impact_source_types=source_types,
                retrieval_top_k=int(ruleset["retrieval_top_k"]),
                max_versions_per_run=int(ruleset["max_versions_per_run"]),
                max_mappings_per_requirement=int(ruleset["max_mappings_per_requirement"]),
                checklists=tuple(checklists),
            )
        except RuleConfigurationError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise RuleConfigurationError(f"{CHECKLIST_FILE} is malformed: {exc}") from exc
        if not 1 <= rules.retrieval_top_k <= 50 or not 1 <= rules.max_versions_per_run <= 50:
            raise RuleConfigurationError("compliance bounds are out of range")
        return rules


# ---------------------------------------------------------------------------
# security / privacy catalogue
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FamilySpec:
    family: SecurityControlFamily
    category: SecurityPrivacyCategory
    title: str
    keywords: tuple[re.Pattern[str], ...]
    baseline_requirement: str

    def indicated_by(self, statement: str) -> bool:
        return _matches(self.keywords, statement)


@dataclass(frozen=True)
class SecurityRules:
    name: str
    version: str
    high_impact_families: frozenset[SecurityControlFamily]
    privacy_floor: SecurityRiskLevel
    default_floor: SecurityRiskLevel
    max_findings_per_call: int
    #: P5 signal rule id (e.g. ``SEC-DATA-UNPROTECTED``) -> indicated families.
    signals: dict[str, tuple[SecurityControlFamily, ...]]
    families: tuple[FamilySpec, ...]

    @property
    def ruleset_ref(self) -> str:
        return f"{self.name}@{self.version}"

    def spec(self, family: SecurityControlFamily) -> FamilySpec:
        return next(f for f in self.families if f.family is family)

    def families_of(self, category: SecurityPrivacyCategory) -> tuple[FamilySpec, ...]:
        return tuple(f for f in self.families if f.category is category)

    def family_from(self, value: str) -> SecurityControlFamily | None:
        """A proposed family string, if it names a catalogue family; else ``None``."""
        key = "_".join(str(value).strip().lower().replace("-", " ").split())
        try:
            family = SecurityControlFamily(key)
        except ValueError:
            return None
        return family if any(f.family is family for f in self.families) else None

    @classmethod
    def from_ruleset(cls, ruleset: RuleSet) -> SecurityRules:
        try:
            high = frozenset(SecurityControlFamily(str(v)) for v in ruleset["high_impact_families"])
            families: list[FamilySpec] = []
            for raw in ruleset["families"]:
                families.append(
                    FamilySpec(
                        family=SecurityControlFamily(str(raw["family"])),
                        category=SecurityPrivacyCategory(str(raw["category"])),
                        title=str(raw["title"]).strip(),
                        keywords=_keyword_patterns(raw.get("keywords", ())),
                        baseline_requirement=" ".join(str(raw["baseline_requirement"]).split()),
                    )
                )
            signals = {
                str(rule): tuple(SecurityControlFamily(str(f)) for f in fams)
                for rule, fams in (ruleset.get("signals") or {}).items()
            }
            rules = cls(
                name=ruleset.name,
                version=ruleset.version,
                high_impact_families=high,
                privacy_floor=SecurityRiskLevel(str(ruleset["privacy_floor"])),
                default_floor=SecurityRiskLevel(str(ruleset["default_floor"])),
                max_findings_per_call=int(ruleset["max_findings_per_call"]),
                signals=signals,
                families=tuple(families),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuleConfigurationError(f"{SECURITY_FILE} is malformed: {exc}") from exc
        missing = ARCHITECTURE_HIGH_IMPACT_FAMILIES - rules.high_impact_families
        if missing:
            raise RuleConfigurationError(
                "the security catalogue may add high-impact families but not remove the "
                f"architecture's (I.7); missing: {sorted(f.value for f in missing)}"
            )
        names = [f.family for f in rules.families]
        if len(set(names)) != len(names) or set(names) != set(SecurityControlFamily):
            raise RuleConfigurationError("the catalogue must name every control family once")
        if rules.privacy_floor is SecurityRiskLevel.LOW:
            raise RuleConfigurationError("I.7: other privacy findings have a floor of medium")
        for rule, fams in rules.signals.items():
            if not fams:
                raise RuleConfigurationError(f"signal {rule} indicates no family")
        return rules


def load_compliance_rules(rules_dir: str | Path) -> ComplianceRules:
    return ComplianceRules.from_ruleset(load_ruleset(Path(rules_dir) / CHECKLIST_FILE))


def load_security_rules(rules_dir: str | Path) -> SecurityRules:
    return SecurityRules.from_ruleset(load_ruleset(Path(rules_dir) / SECURITY_FILE))


@lru_cache(maxsize=1)
def packaged_compliance_rules() -> ComplianceRules:
    return load_compliance_rules(PACKAGED_RULES_DIR)


@lru_cache(maxsize=1)
def packaged_security_rules() -> SecurityRules:
    return load_security_rules(PACKAGED_RULES_DIR)
