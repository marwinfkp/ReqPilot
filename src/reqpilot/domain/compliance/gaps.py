"""Compliance gap detection - a rule-engine output, never a model output (K.2, ``FR-CMP-002``).

::

    compliance_gaps = expected_controls(domain x jurisdiction) - covered_controls

``expected_controls`` comes from the versioned checklist (M7). ``covered_controls``
are the controls a *validated* mapping with a covering relationship
(``addresses`` / ``partially_addresses``) points at, and that a human has not
rejected at G2. A model can only propose mappings; whether a gap exists is this
set difference, so a model cannot hallucinate a gap away, and gap detection still
works when the model produces nothing at all.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from reqpilot.domain.enums import (
    COVERING_RELATIONSHIPS,
    ComplianceMappingStatus,
    ComplianceRelationship,
)

#: Mapping statuses that cover a control. A G2-rejected mapping covers nothing.
COVERING_STATUSES: frozenset[ComplianceMappingStatus] = frozenset(
    {
        ComplianceMappingStatus.CANDIDATE,
        ComplianceMappingStatus.PENDING_REVIEW,
        ComplianceMappingStatus.APPROVED,
    }
)


@dataclass(frozen=True)
class CoveringMapping:
    """What gap detection needs to know about one persisted mapping."""

    control_key: str
    relationship: ComplianceRelationship
    status: ComplianceMappingStatus


def covered_controls(mappings: Iterable[CoveringMapping]) -> frozenset[str]:
    """Controls with at least one covering, non-rejected mapping."""
    return frozenset(
        m.control_key
        for m in mappings
        if m.relationship in COVERING_RELATIONSHIPS and m.status in COVERING_STATUSES
    )


def compute_gaps(expected: Iterable[str], covered: Iterable[str]) -> tuple[str, ...]:
    """``expected - covered``, in the checklist's order. Pure set arithmetic."""
    covered_set = frozenset(covered)
    seen: set[str] = set()
    out: list[str] = []
    for key in expected:
        if key in covered_set or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return tuple(out)
