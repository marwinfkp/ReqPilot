"""The classification taxonomy and its deterministic normalisation (``FR-CLS-001``).

The thirteen categories are those of the problem statement, section 9, as
approved in Phase 0 G.5 and stored in :class:`RequirementCategory`. A model may
spell a category the way the problem statement does ("availability/reliability",
"data-management") or in any case and separator; this module maps every such
spelling onto the one stored value and **rejects everything else**.

Nothing is inferred. A label the table does not know - "compliance", "legal",
"non-functional" - is unknown, and an unknown label goes to a human.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from reqpilot.domain.enums import RequirementCategory as C

#: The problem statement's own spelling of each category (Phase 0 G.5).
PROBLEM_STATEMENT_LABELS: dict[C, str] = {
    C.BUSINESS: "business",
    C.STAKEHOLDER: "stakeholder",
    C.FUNCTIONAL: "functional",
    C.SECURITY: "security",
    C.PRIVACY: "privacy",
    C.REGULATORY: "regulatory",
    C.PERFORMANCE: "performance",
    C.AVAILABILITY: "availability/reliability",
    C.USABILITY: "usability",
    C.DATA_MANAGEMENT: "data-management",
    C.INTEGRATION: "integration",
    C.AUDIT_REPORTING: "audit/reporting",
    C.OPERATIONAL: "operational/maintenance",
}


def _key(raw: str) -> str:
    return re.sub(r"[\s_\-]+", "_", raw.strip().lower())


def _build_aliases() -> dict[str, C]:
    aliases: dict[str, C] = {}
    for category, label in PROBLEM_STATEMENT_LABELS.items():
        aliases[_key(category.value)] = category
        aliases[_key(label)] = category
        # "availability/reliability" is one category; either half names it.
        for half in label.split("/"):
            aliases[_key(half)] = category
        aliases[_key(label.replace("/", " and "))] = category
    return aliases


_ALIASES = _build_aliases()


def normalise_category(raw: str) -> C | None:
    """Map a proposed label onto the approved taxonomy, or return ``None``."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    return _ALIASES.get(_key(raw))


def display_label(category: C) -> str:
    """The problem statement's spelling, for display."""
    return PROBLEM_STATEMENT_LABELS[category]


@dataclass(frozen=True)
class ValidatedLabel:
    """One label that passed deterministic validation."""

    category: C
    review_signal: float
    rationale: str | None
    needs_review: bool

    def __post_init__(self) -> None:
        if not 0.0 <= self.review_signal <= 1.0:
            raise ValueError("a review signal lies in [0, 1]")
