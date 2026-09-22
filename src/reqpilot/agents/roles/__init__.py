"""Agent-role implementations (architecture E).

P3 implements #3 Requirement Extraction and #5 Classification; P4 adds #2
Stakeholder Interaction and #4 Clarification; P5 adds the quality review (#3
support) and #6 Conflict Detection. All four are propose-only: each
call is one gateway call returning a typed proposal. None reads the database,
writes anything, or can reach another role. The other nine arrive with their
phases.
"""

from reqpilot.agents.roles.clarification import ClarificationRole
from reqpilot.agents.roles.classification import ClassificationRole
from reqpilot.agents.roles.extraction import RequirementExtractionRole, render_segments
from reqpilot.agents.roles.interview import StakeholderInteractionRole
from reqpilot.agents.roles.quality import ConflictDetectionRole, RequirementQualityRole

__all__ = [
    "ClarificationRole",
    "ClassificationRole",
    "ConflictDetectionRole",
    "RequirementExtractionRole",
    "RequirementQualityRole",
    "StakeholderInteractionRole",
    "render_segments",
]
