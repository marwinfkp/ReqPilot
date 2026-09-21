"""Typed input/output contracts for the agent roles (architecture F).

P3 implements the two roles it needs: Requirement Extraction (#3) and
Classification (#5). The other roles' contracts arrive with their phases.
"""

from reqpilot.agents.contracts.classification import ClassificationOutput, ProposedLabel
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
    "ClassificationOutput",
    "EvidenceQuote",
    "ExtractedRequirement",
    "ExtractionOutput",
    "ProposedCriterion",
    "ProposedLabel",
    "ProposedPriority",
    "SegmentView",
    "SupportedText",
]
