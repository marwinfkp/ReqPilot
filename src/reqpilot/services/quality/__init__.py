"""Quality and conflict detection services (roadmap phase P5; architecture C.3 nodes 6-8).

:class:`QualityEngine` records what the deterministic rules detect and what the
model proposed once validation accepted it. :class:`FindingReviewService`,
:class:`ConflictService` and :class:`GlossaryService` are the human side: only a
person closes a finding, resolves or dismisses a conflict, or defines a term.
"""

from reqpilot.services.quality.engine import (
    NOT_ANALYSED,
    ProposedConflict,
    QualityEngine,
    VersionView,
    stakeholder_label,
)
from reqpilot.services.quality.review import (
    ConflictService,
    FindingReviewService,
    GlossaryService,
    term_key,
)

__all__ = [
    "NOT_ANALYSED",
    "ConflictService",
    "FindingReviewService",
    "GlossaryService",
    "ProposedConflict",
    "QualityEngine",
    "VersionView",
    "stakeholder_label",
    "term_key",
]
