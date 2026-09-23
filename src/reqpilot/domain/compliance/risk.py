"""The deterministic security/privacy risk authority for G3 (architecture I.7, I.8).

::

    SecurityPrivacyProposal.proposed_risk_level     (a model's suggestion)
        -> normalise            missing / malformed / unrecognised -> MEDIUM, never LOW
        -> catalogue floor      high-impact family -> HIGH; other privacy -> MEDIUM
        -> max(normalised, floor)
        -> authoritative risk_level, persisted; G3 reads the persisted value

**The decisive property** (I.7): the model's proposal can only *raise* the
outcome, never lower it. No input the model can produce - omitting the field,
``"low"``, nonsense, a number, a nested object - yields a level below the floor.
Suppression is arithmetically impossible, not merely disallowed.

Pure functions, no I/O. The floors come from the versioned catalogue
(``rules/data/security_risk_rules.yaml``); the high-impact families named in I.7
are also fixed here, so a catalogue edit can *add* high-impact families but
cannot remove one of the architecture's (checked when the ruleset loads, and
again by a database check constraint).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from reqpilot.domain.enums import SecurityControlFamily, SecurityPrivacyCategory, SecurityRiskLevel

#: I.7: "authentication, authorisation, cryptography and key handling, audit
#: logging, transaction integrity, and privacy obligations touching consent,
#: retention, or data-subject rights" - a finding referencing one has a HIGH floor.
ARCHITECTURE_HIGH_IMPACT_FAMILIES: frozenset[SecurityControlFamily] = frozenset(
    {
        SecurityControlFamily.AUTHENTICATION,
        SecurityControlFamily.AUTHORISATION,
        SecurityControlFamily.CRYPTOGRAPHY,
        SecurityControlFamily.AUDIT_LOGGING,
        SecurityControlFamily.TRANSACTION_INTEGRITY,
        SecurityControlFamily.CONSENT,
        SecurityControlFamily.RETENTION,
        SecurityControlFamily.SUBJECT_RIGHTS,
    }
)

_ORDER: dict[SecurityRiskLevel, int] = {
    SecurityRiskLevel.LOW: 1,
    SecurityRiskLevel.MEDIUM: 2,
    SecurityRiskLevel.HIGH: 3,
}

#: Accepted spellings of each level. Anything else normalises to MEDIUM.
_SYNONYMS: dict[str, SecurityRiskLevel] = {
    "low": SecurityRiskLevel.LOW,
    "medium": SecurityRiskLevel.MEDIUM,
    "moderate": SecurityRiskLevel.MEDIUM,
    "med": SecurityRiskLevel.MEDIUM,
    "high": SecurityRiskLevel.HIGH,
}


def rank(level: SecurityRiskLevel) -> int:
    return _ORDER[level]


def highest(*levels: SecurityRiskLevel) -> SecurityRiskLevel:
    """The highest of ``levels`` - the monotonic combination of I.7 rule 3."""
    return max(levels, key=rank)


@dataclass(frozen=True)
class Normalised:
    level: SecurityRiskLevel
    #: Whether the proposal was usable as given. ``False``: missing, malformed or
    #: unrecognised, so it was raised to MEDIUM (I.7 rule 1).
    recognised: bool


def normalise_proposed_level(value: Any) -> Normalised:
    """I.7 rule 1. Only an exact ``low``/``medium``/``high`` (case and spacing aside)
    is taken as given; everything else - including ``None`` - is MEDIUM."""
    if isinstance(value, str):
        key = " ".join(value.split()).lower()
        if key in _SYNONYMS:
            return Normalised(_SYNONYMS[key], recognised=True)
    return Normalised(SecurityRiskLevel.MEDIUM, recognised=False)


def catalogue_floor(
    family: SecurityControlFamily,
    category: SecurityPrivacyCategory,
    *,
    high_impact_families: frozenset[SecurityControlFamily],
    privacy_floor: SecurityRiskLevel = SecurityRiskLevel.MEDIUM,
    default_floor: SecurityRiskLevel = SecurityRiskLevel.LOW,
) -> SecurityRiskLevel:
    """I.7 rule 2. High-impact family -> HIGH; other privacy -> MEDIUM; else the default.

    The architecture's high-impact families are always included, whatever the
    catalogue passes in.
    """
    if family in high_impact_families | ARCHITECTURE_HIGH_IMPACT_FAMILIES:
        return SecurityRiskLevel.HIGH
    if category is SecurityPrivacyCategory.PRIVACY:
        return highest(privacy_floor, SecurityRiskLevel.MEDIUM)
    return default_floor


@dataclass(frozen=True)
class RiskEvaluation:
    """The deterministic evaluation of one derived security/privacy requirement."""

    #: Exactly what the model proposed, as a string for the audit trail (or None).
    proposed_raw: str | None
    normalised_proposal: SecurityRiskLevel
    proposal_recognised: bool
    floor: SecurityRiskLevel
    #: ``max(normalised_proposal, floor)`` - the only value G3 reads.
    authoritative: SecurityRiskLevel
    rules_version: str
    #: Why the level is what it is: which rule set it. Never model text.
    escalation_reason: str

    @property
    def requires_g3(self) -> bool:
        return self.authoritative is SecurityRiskLevel.HIGH


def _raw(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)[:50]


def evaluate_risk(
    *,
    proposed_level: Any,
    family: SecurityControlFamily,
    category: SecurityPrivacyCategory,
    high_impact_families: frozenset[SecurityControlFamily],
    rules_version: str,
    privacy_floor: SecurityRiskLevel = SecurityRiskLevel.MEDIUM,
    default_floor: SecurityRiskLevel = SecurityRiskLevel.LOW,
) -> RiskEvaluation:
    """Normalise, apply the floor, combine monotonically (architecture I.7)."""
    normalised = normalise_proposed_level(proposed_level)
    floor = catalogue_floor(
        family,
        category,
        high_impact_families=high_impact_families,
        privacy_floor=privacy_floor,
        default_floor=default_floor,
    )
    authoritative = highest(normalised.level, floor)

    reasons: list[str] = []
    if not normalised.recognised:
        reasons.append("proposed level missing or unrecognised: normalised to medium")
    if floor is SecurityRiskLevel.HIGH:
        reasons.append(f"high-impact control family '{family.value}': catalogue floor high")
    elif floor is SecurityRiskLevel.MEDIUM:
        reasons.append(f"{category.value} finding: catalogue floor medium")
    if rank(floor) > rank(normalised.level):
        reasons.append(
            f"catalogue floor {floor.value} overrides the proposed {normalised.level.value}"
        )
    elif normalised.recognised and rank(normalised.level) > rank(floor):
        reasons.append(f"proposed level {normalised.level.value} is above the floor")
    if authoritative is SecurityRiskLevel.HIGH:
        reasons.append("authoritative level high: G3 required")
    return RiskEvaluation(
        proposed_raw=_raw(proposed_level),
        normalised_proposal=normalised.level,
        proposal_recognised=normalised.recognised,
        floor=floor,
        authoritative=authoritative,
        rules_version=rules_version,
        escalation_reason="; ".join(reasons) or "no floor applied; proposed level retained",
    )
