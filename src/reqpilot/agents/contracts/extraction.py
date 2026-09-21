"""The Requirement Extraction role's contracts (architecture E #3, F.4).

``ExtractionOutput`` is exactly what the model may emit, and it is the model's
whole vocabulary. What it deliberately cannot say (``[DESIGN] D4``, F.4):

* no requirement **identifier** - ``candidate_key`` is a temporary label; the
  ``FR-/NFR-<DOMAIN>-nnn`` id is allocated by code (``FR-EXT-004``);
* no **approval status** and no **lifecycle state**;
* no **risk level** and no **applicable regulations** - their producing roles
  (P6, P7) do not exist yet, and they will be evidence-based when they do;
* no **category** - classification is a separate role with its own contract;
* no **character offsets** - the model quotes; code finds the offsets.

Unknown fields are refused (``extra="forbid"``), so an output that tries to set
one of those fails schema validation visibly instead of being silently ignored.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "1.0"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceQuote(_Strict):
    """A verbatim quote from one numbered segment."""

    segment_id: str = Field(min_length=1, max_length=16)
    quote: str = Field(min_length=1, max_length=4000)


class SupportedText(_Strict):
    """An optional field's value together with the words that state it."""

    text: str = Field(min_length=1, max_length=2000)
    segment_id: str = Field(min_length=1, max_length=16)
    quote: str = Field(min_length=1, max_length=4000)


class ProposedPriority(_Strict):
    value: Literal["must", "should", "could", "wont"]
    segment_id: str = Field(min_length=1, max_length=16)
    quote: str = Field(min_length=1, max_length=4000)


class ProposedCriterion(_Strict):
    given: str = Field(min_length=1, max_length=4000)
    when: str = Field(min_length=1, max_length=4000)
    then: str = Field(min_length=1, max_length=4000)


class ExtractedRequirement(_Strict):
    """One proposed requirement. Everything here is a proposal."""

    candidate_key: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    statement: str = Field(min_length=1, max_length=4000)
    requirement_type: Literal["functional", "non_functional"]
    #: May be empty in the schema so that a missing source is caught by the
    #: provenance check - and routed to a human - rather than by a repair loop.
    evidence: list[EvidenceQuote] = Field(default_factory=list, max_length=20)
    justification: SupportedText | None = None
    priority: ProposedPriority | None = None
    assumptions: list[SupportedText] = Field(default_factory=list, max_length=10)
    depends_on: list[str] = Field(default_factory=list, max_length=20)
    duplicate_of: list[str] = Field(default_factory=list, max_length=20)
    acceptance_criteria: list[ProposedCriterion] = Field(default_factory=list, max_length=20)
    #: The model's own heuristic signal - not a probability (Phase 0 H.1).
    review_signal: float = Field(ge=0.0, le=1.0)


class ExtractionOutput(_Strict):
    requirements: list[ExtractedRequirement] = Field(max_length=200)


@dataclass(frozen=True)
class SegmentView:
    """One stored source segment, as offered to the model under a short id.

    ``segment_id`` ("S1", "S2", ...) is only meaningful within one call; the
    chunk id and offsets are what a resolved span records.

    From P4 a segment is either a chunk of a source document (``source_kind``
    ``"source_chunk"``: ``chunk_id`` is the chunk, ``document_id`` its document)
    or a stakeholder's answer in an interview (``"utterance"``: ``chunk_id`` is
    the utterance, ``document_id`` its session, offsets are within the
    utterance). ``context`` - the question an answer replies to - is shown to
    the model but is not part of the segment: nothing can be quoted from it, so
    a requirement always cites the stakeholder's own words.
    """

    segment_id: str
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    char_start: int
    text: str
    speaker: str | None
    masked: bool
    synthetic: bool
    source_kind: str = "source_chunk"
    context: str | None = None
