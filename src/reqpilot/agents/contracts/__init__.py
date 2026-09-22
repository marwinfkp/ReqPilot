"""Typed input/output contracts for the agent roles (architecture F).

P3: Requirement Extraction (#3) and Classification (#5). P4: Stakeholder
Interaction (#2) and Clarification (#4). P5: quality review (#3 support) and
Conflict Detection (#6). The other roles' contracts arrive with their phases.
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
from reqpilot.agents.contracts.quality import (
    ConflictAdjudication,
    ConflictPairView,
    ProposedQualityFinding,
    QualityReviewItem,
    QualityReviewOutput,
)

__all__ = [
    "AnswerAssessment",
    "AnswerAssessmentInput",
    "ClarificationInput",
    "ClarificationProposal",
    "ClassificationOutput",
    "ConflictAdjudication",
    "ConflictPairView",
    "EvidenceQuote",
    "ExtractedRequirement",
    "ExtractionOutput",
    "InterviewTurnInput",
    "ProposedCriterion",
    "ProposedLabel",
    "ProposedPriority",
    "ProposedQualityFinding",
    "QualityReviewItem",
    "QualityReviewOutput",
    "QuestionProposal",
    "SegmentView",
    "SupportedText",
    "TurnView",
]
