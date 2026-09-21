"""Typed input/output contracts for the agent roles (architecture F).

P3: Requirement Extraction (#3) and Classification (#5). P4: Stakeholder
Interaction (#2) and Clarification (#4). The other roles' contracts arrive with
their phases.
"""

from reqpilot.agents.contracts.clarification import ClarificationInput, ClarificationProposal
from reqpilot.agents.contracts.classification import ClassificationOutput, ProposedLabel
from reqpilot.agents.contracts.elicitation import (
    AnswerAssessment,
    AnswerAssessmentInput,
    InterviewTurnInput,
    QuestionProposal,
    TurnView,
)
from reqpilot.agents.contracts.extraction import (
    EvidenceQuote,
    ExtractedRequirement,
    ExtractionOutput,
    ProposedCriterion,
    ProposedPriority,
    SegmentView,
    SupportedText,
)

__all__ = [
    "AnswerAssessment",
    "AnswerAssessmentInput",
    "ClarificationInput",
    "ClarificationProposal",
    "ClassificationOutput",
    "EvidenceQuote",
    "ExtractedRequirement",
    "ExtractionOutput",
    "InterviewTurnInput",
    "ProposedCriterion",
    "ProposedLabel",
    "ProposedPriority",
    "QuestionProposal",
    "SegmentView",
    "SupportedText",
    "TurnView",
]
