"""The F.2 validation pipeline for the agent roles P3 and P4 implement.

Every model output passes schema validation in the gateway and then these
deterministic checks before anything is persisted (architecture F.2).
"""

from reqpilot.agents.validation.clarification import (
    ClarificationDecision as ClarificationQuestionDecision,
)
from reqpilot.agents.validation.clarification import validate_clarification
from reqpilot.agents.validation.classification import (
    ClassificationDecision,
    validate_classification,
)
from reqpilot.agents.validation.elicitation import (
    AssessmentDecision,
    QuestionDecision,
    normalise_question,
    validate_assessment,
    validate_question,
)
from reqpilot.agents.validation.extraction import (
    MIN_QUOTE_WORDS,
    RecordedProposal,
    decide,
    deduplicate,
    validate_proposal,
)

__all__ = [
    "MIN_QUOTE_WORDS",
    "AssessmentDecision",
    "ClarificationQuestionDecision",
    "ClassificationDecision",
    "QuestionDecision",
    "RecordedProposal",
    "decide",
    "deduplicate",
    "normalise_question",
    "validate_assessment",
    "validate_clarification",
    "validate_classification",
    "validate_proposal",
    "validate_question",
]
