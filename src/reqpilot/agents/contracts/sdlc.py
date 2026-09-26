"""Contract of role #10, SDLC Selection (architecture E #10, F.4, L; roadmap phase P9).

Role #10 is hybrid and advisory. It is called twice, and has authority in
neither call:

1. **Factor proposals** (architecture C.5 ``propose_factor_scores``). Shown the
   derived 13-factor profile with its rationale and evidence references, the
   model may propose an adjusted score for a factor, with a rationale and the
   supplied references that justify it. Deterministic validation accepts a
   proposal only for the nine factors that are *not* risk-derived, only within
   the ruleset's ``max_proposal_deviation`` of the derived score, and only when
   it cites evidence it was given. A human override (``FR-SDL-003``) always wins.
2. **The explanation** (architecture L.5). Shown the persisted ranking - which
   already exists - it writes a narrative and the counter-arguments
   (``FR-SDL-006``, ``-007``). Its ``asserted_top_candidate`` and
   ``asserted_scores`` are **non-authoritative**: they exist only so the
   consistency check can compare what the model *believes* with what was
   computed. They never replace a persisted value; a mismatch is stored and
   shown as a discrepancy.

Deliberately absent, so a model has nowhere to put them: a ranking, a rank, a
selection, a weight, a coefficient, a rule, a veto or boost, a G6 decision, an
approval, a run status, a project or run id. ``extra="forbid"`` makes an output
that adds any such field schema-invalid: one bounded repair, then a recorded
failure with nothing persisted.

Scores are carried as plain values and checked by validation, so one malformed
proposal is rejected on its own - with a reason an audit can read - and does not
invalidate the others.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

CONTRACT_VERSION = "1.0"


class SDLCFactorProposal(BaseModel):
    """One proposed factor adjustment. Validation decides whether it counts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: One of the 13 factor ids, copied exactly.
    factor: str = Field(min_length=1, max_length=60)
    #: An integer 1-5. Kept as given so a malformed value is rejected, not coerced.
    proposed_score: Any = None
    rationale: str = Field(default="", max_length=1200)
    #: References shown in the request (``kind:id``). Anything else is fabricated.
    evidence_refs: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _refs_as_text(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [str(v)[:120] for v in value]
        return value


class FactorProposalOutput(BaseModel):
    """One factor-proposal call's output (architecture E #10 ``SDLCFactorProposal``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    proposals: list[SDLCFactorProposal] = Field(default_factory=list, max_length=13)
    #: What the model felt it could not assess. Informational; never persisted.
    note: str | None = Field(default=None, max_length=500)


class CounterArgument(BaseModel):
    """Why a candidate was not selected, and what would reverse that (``FR-SDL-007``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate: str = Field(min_length=1, max_length=60)
    why_not_selected: str = Field(min_length=1, max_length=1500)
    reversal_condition: str = Field(default="", max_length=800)
    cited_factors: list[str] = Field(default_factory=list, max_length=13)


class ExplanationDraft(BaseModel):
    """The explanation of a ranking that already exists (architecture L.5)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    narrative: str = Field(min_length=1, max_length=4000)
    counter_arguments: list[CounterArgument] = Field(default_factory=list, max_length=6)
    cited_factors: list[str] = Field(default_factory=list, max_length=13)
    cited_evidence_refs: list[str] = Field(default_factory=list, max_length=40)
    #: NON-AUTHORITATIVE. What the model believes ranked first - compared with the
    #: persisted ranking by the consistency check, never used in its place.
    asserted_top_candidate: str = Field(min_length=1, max_length=60)
    #: NON-AUTHORITATIVE. The 0-100 scores the model believes were computed.
    asserted_scores: dict[str, float] = Field(default_factory=dict)

    @field_validator("asserted_scores")
    @classmethod
    def _bounded(cls, value: dict[str, float]) -> dict[str, float]:
        if len(value) > 10:
            raise ValueError("at most one asserted score per candidate")
        return value
