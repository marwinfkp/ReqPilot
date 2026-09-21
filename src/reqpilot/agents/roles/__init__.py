"""Agent-role implementations (architecture E).

P3 implements two of the thirteen roles: #3 Requirement Extraction and #5
Classification. Both are propose-only: each makes one gateway call and returns
a typed proposal. Neither reads the database, writes anything, or can reach
another role. No role is added; the other eleven arrive with their phases.
"""

from reqpilot.agents.roles.classification import ClassificationRole
from reqpilot.agents.roles.extraction import RequirementExtractionRole, render_segments

__all__ = ["ClassificationRole", "RequirementExtractionRole", "render_segments"]
