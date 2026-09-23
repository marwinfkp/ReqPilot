"""What deterministic validation hands to persistence (P6; architecture F.2, K.1).

Validation (``agents/validation/compliance.py``) reads a model's typed proposal
and produces these value objects; the compliance and security services record
them. Nothing here comes from the model unchecked:

* a :class:`CitationFacts` is built from the database - a resolved evidence row
  of *this run*, its knowledge item and its source - never from model text;
* an :class:`AcceptedMapping` names a control from the versioned checklist and
  carries the jurisdiction and source type of its citations, not the model's
  claims about them;
* a :class:`DroppedClaim` records why a claim did not survive (a reason code and
  the rule ids), for the ``COMPLIANCE_CLAIM_DROPPED`` audit event.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from reqpilot.domain.enums import (
    ComplianceRelationship,
    FindingDetector,
    NormativeSourceType,
    ObligationKind,
    SecurityControlFamily,
    SecurityPrivacyCategory,
)


class DropReason:
    """Stable reason codes for a dropped claim. Safe to store and to audit."""

    UNCITED = "uncited"
    UNSUPPORTED_CITATION = "unsupported_citation"
    EVIDENCE_NOT_RELEVANT = "evidence_does_not_support_control"
    OUT_OF_SCOPE_EVIDENCE = "out_of_scope_evidence"
    PROVENANCE_MISMATCH = "provenance_mismatch"
    PROHIBITED_LANGUAGE = "prohibited_language"
    AUTHORITY_CLAIM = "authority_claim"
    UNKNOWN_CONTROL = "unknown_control"
    UNKNOWN_FAMILY = "unknown_family"
    WRONG_CATEGORY = "wrong_category"
    WRONG_REQUIREMENT = "wrong_requirement"
    DUPLICATE = "duplicate"
    OVER_LIMIT = "over_limit"
    EMPTY = "empty"
    NOT_A_REQUIREMENT = "not_a_requirement"

    ALL: frozenset[str] = frozenset(
        {
            UNCITED,
            UNSUPPORTED_CITATION,
            EVIDENCE_NOT_RELEVANT,
            OUT_OF_SCOPE_EVIDENCE,
            PROVENANCE_MISMATCH,
            PROHIBITED_LANGUAGE,
            AUTHORITY_CLAIM,
            UNKNOWN_CONTROL,
            UNKNOWN_FAMILY,
            WRONG_CATEGORY,
            WRONG_REQUIREMENT,
            DUPLICATE,
            OVER_LIMIT,
            EMPTY,
            NOT_A_REQUIREMENT,
        }
    )


@dataclass(frozen=True)
class CitationFacts:
    """One resolved citation: evidence supplied to this run, with full provenance (J.5)."""

    evidence_id: uuid.UUID
    jurisdiction: str
    source_type: NormativeSourceType
    #: The cited knowledge item's applicability tags (what the chunk is about).
    applicability: frozenset[str]
    #: JSON-ready provenance for storage and display: source title, type,
    #: binding, issuing body, jurisdiction, version, effective and curation
    #: dates, item key and clause, span, KB version, quote hash.
    snapshot: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ControlRef:
    """A checklist control, copied from the versioned ruleset."""

    key: str
    title: str
    obligation_kind: ObligationKind
    high_impact: bool
    checklist_ref: str
    domain: str
    jurisdiction: str
    evidence_tags: frozenset[str]


@dataclass(frozen=True)
class AcceptedMapping:
    """A mapping that passed every deterministic check (F.2 stages 1-4)."""

    control: ControlRef
    relationship: ComplianceRelationship
    rationale: str
    candidate_text: str | None
    implied_obligation: str | None
    citations: tuple[CitationFacts, ...]
    #: G2 (FR-CMP-004): the checklist, a binding source type, or the model's own
    #: flag - which can only add - made it high-impact.
    is_high_impact: bool
    high_impact_reasons: tuple[str, ...]
    review_signal: float | None

    @property
    def jurisdiction(self) -> str:
        return self.citations[0].jurisdiction

    @property
    def source_type(self) -> NormativeSourceType:
        return self.citations[0].source_type


@dataclass(frozen=True)
class DroppedClaim:
    """A claim that did not survive validation, and why. Never persisted as a mapping."""

    reason: str
    detail: str
    control_key: str | None = None
    family: str | None = None
    cited: int = 0
    rule_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class AcceptedSecurityProposal:
    """A derived security/privacy requirement that passed validation.

    ``proposed_risk_level`` is exactly what the model said - anything, including
    nothing. The authoritative level is computed from it by the deterministic
    evaluator (I.7), never taken from it.
    """

    family: SecurityControlFamily
    category: SecurityPrivacyCategory
    derived_requirement: str
    rationale: str
    risk_rationale: str | None
    proposed_risk_level: Any
    citations: tuple[CitationFacts, ...]
    review_signal: float | None
    detected_by: FindingDetector
    source_signal_finding_id: uuid.UUID | None = None
