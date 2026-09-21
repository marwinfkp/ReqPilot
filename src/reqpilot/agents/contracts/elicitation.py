"""Contracts of role #2, Stakeholder Interaction (architecture E #2, F.4, F.5).

In: :class:`InterviewTurnInput` - role template, covered topics, recent turns,
follow-up depth. Out: :class:`QuestionProposal` ``{question, topic_id,
is_followup, rationale}``. The answer-assessment step of ``elicitation_graph``
(C.4: "LLM: complete / vague / inconsistent") uses the same role with
:class:`AnswerAssessmentInput` -> :class:`AnswerAssessment`.

Deliberately absent from both outputs, so a model cannot set them (``[DESIGN]
D4``): coverage, the next topic, the follow-up counter, completion, lifecycle
state, approval, risk, compliance and any graph route. ``extra="forbid"`` makes
an attempt to add one a schema failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class TurnView:
    """One earlier turn of this session, as the role may see it."""

    speaker: Literal["interviewer", "stakeholder"]
    text: str
    topic_id: str | None


@dataclass(frozen=True)
class InterviewTurnInput:
    """Everything the interviewer role may see (least privilege, E #2).

    This session's turns and its template's topic framework - no requirement,
    no approval, no other session, no retrieval.
    """

    template_title: str
    topic_id: str
    topic_title: str
    topic_description: str
    expected_answer_shape: str
    covered_topics: tuple[str, ...]
    remaining_topics: tuple[str, ...]
    recent_turns: tuple[TurnView, ...]
    is_followup: bool
    followup_depth: int
    max_followups: int
    #: The issue a follow-up must address (model-derived, treated as data).
    issue: str | None
    synthetic: bool
    masked: bool


class QuestionProposal(BaseModel):
    """One proposed interview question. A proposal; code decides what it becomes."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1000)
    topic_id: str = Field(min_length=1, max_length=64)
    is_followup: bool
    rationale: str = Field(default="", max_length=1000)


@dataclass(frozen=True)
class AnswerAssessmentInput:
    topic_id: str
    topic_title: str
    topic_description: str
    expected_answer_shape: str
    question: str
    answer: str
    #: Earlier answers on the same topic, so an inconsistency can be noticed.
    earlier_answers: tuple[str, ...]
    synthetic: bool
    masked: bool


class AnswerAssessment(BaseModel):
    """The model's view of one answer. The router, not the model, acts on it."""

    model_config = ConfigDict(extra="forbid")

    topic_id: str = Field(min_length=1, max_length=64)
    status: Literal["complete", "vague", "incomplete", "inconsistent"]
    rationale: str = Field(default="", max_length=1000)
    detected_issue: str | None = Field(default=None, max_length=500)
