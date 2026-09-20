"""Provenance and reference value objects (architecture F.3).

These are the types that carry traceability. Every generated claim in ReqPilot
eventually points back to something through one of them, so they are defined
once, in the domain, and reused everywhere.

P0 defines the shared shapes only; the entities they will reference are created
by later roadmap phases.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceKind(StrEnum):
    """What an evidence reference points at (architecture F.3).

    The project/knowledge split matters: project content is untrusted input,
    knowledge items are curated reference material. They are never
    interchangeable as the basis for a normative claim (architecture J.1).
    """

    UTTERANCE = "utterance"
    SOURCE_CHUNK = "source_chunk"
    KNOWLEDGE_ITEM = "knowledge_item"


class EvidenceRef(BaseModel):
    """A pointer to the evidence supporting a claim.

    ``char_start``/``char_end`` are what make span-level traceability possible;
    they are preserved end to end from ingestion (architecture J.3).
    """

    model_config = ConfigDict(frozen=True)

    kind: EvidenceKind
    target_id: UUID
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    quote: str | None = None

    @model_validator(mode="after")
    def _span_is_coherent(self) -> EvidenceRef:
        if (self.char_start is None) != (self.char_end is None):
            raise ValueError("char_start and char_end must be provided together")
        if (
            self.char_start is not None
            and self.char_end is not None
            and self.char_end <= self.char_start
        ):
            raise ValueError("char_end must be greater than char_start")
        return self


class SourceSpan(BaseModel):
    """A character range within a source document (architecture F.3)."""

    model_config = ConfigDict(frozen=True)

    source_id: UUID
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> SourceSpan:
        if self.char_end <= self.char_start:
            raise ValueError("char_end must be greater than char_start")
        return self


class ReviewSignal(BaseModel):
    """A heuristic review-prioritisation signal - **not** a calibrated probability.

    The approved Phase 0 analysis is explicit that confidence values are review
    signals until calibration is implemented and validated. The mandatory
    :attr:`interpretation` string exists so that a value can never be rendered
    in a UI or an artefact without its caveat travelling with it
    (approved Phase 0 H.1; architecture F.3, risk R13).
    """

    model_config = ConfigDict(frozen=True)

    value: float = Field(ge=0.0, le=1.0)
    interpretation: str = Field(
        default="review-prioritisation signal, not a calibrated probability",
        frozen=True,
    )
