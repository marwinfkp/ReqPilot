"""Deterministic quality and conflict rules (roadmap phase P5; architecture M7, E #6).

Pure functions over statements and versioned rule data: no database, no model,
no I/O. The services record what they find; the LLM layer adds semantic
proposals that deterministic validation checks; humans close findings.
"""

from reqpilot.domain.quality.checks import DetectedFinding, check_statement
from reqpilot.domain.quality.conflicts import (
    Candidate,
    DuplicatePair,
    PairItem,
    RuleVerdict,
    canonical,
    duplicates,
    judge_pair,
    shortlist,
)
from reqpilot.domain.quality.text import Span

__all__ = [
    "Candidate",
    "DetectedFinding",
    "DuplicatePair",
    "PairItem",
    "RuleVerdict",
    "Span",
    "canonical",
    "check_statement",
    "duplicates",
    "judge_pair",
    "shortlist",
]
