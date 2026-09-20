"""SQLAlchemy models for the foundational tables (architecture section G).

P0 defines only the tables the foundation itself needs: identity and project
membership, the append-only audit log, and run records.

The requirement lifecycle tables - ``requirement``, ``requirement_version`` and
everything that hangs off them - belong to the Requirements Repository phase and
are deliberately absent. Defining them here would be speculative schema.
"""

from reqpilot.domain.models.audit import AuditEvent
from reqpilot.domain.models.base import Base
from reqpilot.domain.models.identity import Project, ProjectMember, User
from reqpilot.domain.models.runs import AgentRun, GraphRun

__all__ = [
    "AgentRun",
    "AuditEvent",
    "Base",
    "GraphRun",
    "Project",
    "ProjectMember",
    "User",
]
