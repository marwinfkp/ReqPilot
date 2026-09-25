"""The typed trace graph: materialising it, reading it, the RTM and coverage (P8).

* :mod:`~reqpilot.services.traceability.sync` - derives typed edges from
  persisted facts, idempotently and append-only (``FR-TRC-001``, ``-004``).
* :mod:`~reqpilot.services.traceability.graph` - the read model.
* :mod:`~reqpilot.services.traceability.scope` - baseline and project scopes.
* :mod:`~reqpilot.services.traceability.rtm` - the RTM from the graph (``FR-TRC-002``).
* :mod:`~reqpilot.services.traceability.coverage` - coverage and E6 (``FR-TRC-003``).
"""

from reqpilot.services.traceability.coverage import (
    E6_DEFINITION_VERSION,
    CoverageReport,
    CoverageService,
    VersionCoverage,
)
from reqpilot.services.traceability.graph import TraceGraph, TraceQueryService
from reqpilot.services.traceability.rtm import NOT_LINKED, RTM_COLUMNS, RtmBuilder, RtmRow, rtm_csv
from reqpilot.services.traceability.scope import RequirementScope, ScopedVersion, ScopeService
from reqpilot.services.traceability.sync import Edge, SyncResult, TraceGraphSync

__all__ = [
    "E6_DEFINITION_VERSION",
    "NOT_LINKED",
    "RTM_COLUMNS",
    "CoverageReport",
    "CoverageService",
    "Edge",
    "RequirementScope",
    "RtmBuilder",
    "RtmRow",
    "ScopeService",
    "ScopedVersion",
    "SyncResult",
    "TraceGraph",
    "TraceGraphSync",
    "TraceQueryService",
    "VersionCoverage",
    "rtm_csv",
]
