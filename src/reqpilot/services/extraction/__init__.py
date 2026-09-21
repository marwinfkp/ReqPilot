"""Deterministic services for batch extraction (roadmap phase P3).

Sources in, validated requirements out - with no model anywhere in this package.
The model's part happens in the agent layer (M3); this package records what it
proposed and applies what deterministic validation decided.
"""

from reqpilot.services.extraction.merge import MERGEABLE_STATES, RequirementMergeService
from reqpilot.services.extraction.pipeline import ExtractionService, PersistOutcome
from reqpilot.services.extraction.record import RequirementRecord, RequirementRecordService
from reqpilot.services.extraction.runs import (
    ANALYSIS_GRAPH,
    RunLog,
    RunRecorder,
    pipeline_actor,
    run_actor,
)
from reqpilot.services.extraction.sources import MAX_SOURCE_CHARS, SourceDocumentService

__all__ = [
    "ANALYSIS_GRAPH",
    "MAX_SOURCE_CHARS",
    "MERGEABLE_STATES",
    "ExtractionService",
    "PersistOutcome",
    "RequirementMergeService",
    "RequirementRecord",
    "RequirementRecordService",
    "RunLog",
    "RunRecorder",
    "SourceDocumentService",
    "pipeline_actor",
    "run_actor",
]
