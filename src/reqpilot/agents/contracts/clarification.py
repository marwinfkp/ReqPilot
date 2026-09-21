"""Contracts of role #4, Clarification (architecture E #4, F.4, F.5).

In: :class:`ClarificationInput` ``{requirement, defect, prior_questions}`` - the
one requirement and its one defect, nothing else (least privilege). Out:
:class:`ClarificationProposal` ``{question, expected_answer_shape, defect_id}``.

The model proposes the question only. Binding to the version and the finding,
duplicate prevention, status, assignment, the answer and any new version are
deterministic code's (E #4: "LLM question generation + deterministic
defect->question routing").
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class ClarificationInput:
    requirement_ref: str
    statement: str
    original_wording: str | None
    defect_id: str
    defect_type: str
    severity: str
    defect_rationale: str
    defect_span: str | None
    prior_questions: tuple[str, ...]
    synthetic: bool
    masked: bool


class ClarificationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1000)
    expected_answer_shape: str = Field(min_length=1, max_length=300)
    #: Must echo the defect it was given; code checks it.
    defect_id: str = Field(min_length=1, max_length=64)
