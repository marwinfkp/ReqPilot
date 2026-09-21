"""The F.2 validation pipeline for the agent roles P3 implements.

Every model output passes schema validation in the gateway and then these
deterministic checks before anything is persisted (architecture F.2).
"""

from reqpilot.agents.validation.classification import (
    ClassificationDecision,
    validate_classification,
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
    "ClassificationDecision",
    "RecordedProposal",
    "decide",
    "deduplicate",
    "validate_classification",
    "validate_proposal",
]
