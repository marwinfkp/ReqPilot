"""Validated proposals: what deterministic validation hands to persistence.

The extraction role's raw output is a schema-valid *proposal*; the validation
pipeline (``reqpilot.agents.validation``) turns it into the types below. They
live in the domain so that the persistence services - which may not import the
agent layer - can accept them, and so that the invariants that matter are
properties of the type rather than of a caller's care:

* a :class:`ValidatedCandidate` cannot exist without at least one resolved
  source span (``FR-EXT-007``);
* its statement is in declarative form (``FR-EXT-003``);
* it carries no identifier, no approval status and no risk level - those are
  not the model's to set (architecture F.4, ``[DESIGN] D4``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from reqpilot.domain.enums import RequirementPriority, ReviewReason
from reqpilot.domain.requirement_ids import RequirementKind
from reqpilot.domain.source_spans import ResolvedSpan


class FindingCode(StrEnum):
    """Stable codes for what validation found. Safe to store and to audit."""

    # --- FR-EXT-007: provenance -----------------------------------------
    NO_EVIDENCE = "no_evidence"
    UNKNOWN_SEGMENT = "unknown_segment"
    QUOTE_NOT_FOUND = "quote_not_found"
    QUOTE_TOO_SHORT = "quote_too_short"
    # --- the statement --------------------------------------------------
    STATEMENT_NOT_DECLARATIVE = "statement_not_declarative"
    STATEMENT_TOO_LONG = "statement_too_long"
    DUPLICATE_CANDIDATE_KEY = "duplicate_candidate_key"
    # --- optional fields: dropped, never invented -----------------------
    UNSUPPORTED_JUSTIFICATION = "unsupported_justification"
    UNSUPPORTED_PRIORITY = "unsupported_priority"
    UNSUPPORTED_ASSUMPTION = "unsupported_assumption"
    UNKNOWN_DEPENDENCY = "unknown_dependency"
    UNKNOWN_DUPLICATE_REFERENCE = "unknown_duplicate_reference"
    # --- acceptance criteria ----------------------------------------------
    CRITERION_INVALID = "criterion_invalid"
    TOO_MANY_CRITERIA = "too_many_criteria"
    # --- review signals and duplicates --------------------------------------
    LOW_REVIEW_SIGNAL = "low_review_signal"
    EXACT_DUPLICATE_MERGED = "exact_duplicate_merged"
    SIMILAR_TO_OTHER = "similar_to_other"
    MODEL_PROPOSED_DUPLICATE = "model_proposed_duplicate"


@dataclass(frozen=True)
class Finding:
    code: FindingCode
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": str(self.code), "message": self.message}


@dataclass(frozen=True)
class CriterionDraft:
    """A Given/When/Then acceptance-criterion proposal that passed validation."""

    given: str
    when: str
    then: str


@dataclass(frozen=True)
class ValidatedCandidate:
    """An extraction proposal that passed every deterministic check."""

    candidate_id: uuid.UUID
    candidate_key: str
    ordinal: int
    kind: RequirementKind
    statement: str
    spans: tuple[ResolvedSpan, ...]
    original_text: str
    review_signal: float
    justification: str | None = None
    priority: RequirementPriority | None = None
    assumptions: tuple[str, ...] = ()
    depends_on_keys: tuple[str, ...] = ()
    criteria: tuple[CriterionDraft, ...] = ()
    #: Proposed criteria were present but failed validation, so none are stored.
    criteria_rejected: bool = False
    #: Keys of other accepted candidates the model said this duplicates.
    duplicate_of_keys: tuple[str, ...] = ()
    #: Exact duplicates in the batch whose spans were folded into this one.
    merged_candidate_ids: tuple[uuid.UUID, ...] = ()
    findings: tuple[Finding, ...] = ()
    #: The model's own review signal was below the threshold.
    low_signal: bool = False
    #: Some cited evidence did not resolve and was dropped. The requirement has
    #: other, resolved sources - but a human should see what was dropped.
    dropped_evidence: bool = False

    def __post_init__(self) -> None:
        if not self.spans:
            raise ValueError("a requirement is never emitted without a source link (FR-EXT-007)")
        if not self.statement.strip():
            raise ValueError("a validated statement cannot be empty")
        if not 0.0 <= self.review_signal <= 1.0:
            raise ValueError("a review signal lies in [0, 1]")

    def source_refs(self) -> tuple[dict[str, Any], ...]:
        return tuple(span.as_source_ref() for span in self.spans)


@dataclass(frozen=True)
class RejectedCandidate:
    """An extraction proposal that failed validation. Never a requirement."""

    candidate_id: uuid.UUID
    candidate_key: str
    reason: ReviewReason
    findings: tuple[Finding, ...]
    #: Spans that did resolve, kept for the reviewer even though the whole
    #: proposal failed.
    spans: tuple[ResolvedSpan, ...] = ()


@dataclass(frozen=True)
class NearDuplicate:
    """Two accepted candidates a human should compare (never merged by code)."""

    key_a: str
    key_b: str
    similarity: float
    #: ``similarity`` (lexical) or ``model`` (the model proposed the relation).
    basis: str


@dataclass(frozen=True)
class ExtractionDecision:
    """Everything deterministic validation decided about one batch of proposals."""

    accepted: tuple[ValidatedCandidate, ...]
    rejected: tuple[RejectedCandidate, ...]
    #: ``(merged candidate id, candidate id it was folded into)``.
    merged: tuple[tuple[uuid.UUID, uuid.UUID], ...]
    near_duplicates: tuple[NearDuplicate, ...]
    ruleset_version: str
    findings: tuple[Finding, ...] = field(default=())


@dataclass(frozen=True)
class ProposalRecord:
    """One schema-valid proposal, as the persistence service records it.

    Plain data, so the service need not know the agent contract: ``proposal`` is
    the proposal exactly as the model returned it.
    """

    model_key: str
    statement: str
    kind: RequirementKind
    review_signal: float
    proposal: dict[str, Any]


@dataclass(frozen=True)
class RecordedCandidate:
    """What recording a proposal produced: its row id and its run-unique key."""

    candidate_id: uuid.UUID
    run_key: str
    model_key: str
    ordinal: int
    #: The model reused a key already used earlier in the same output.
    key_collision: bool
