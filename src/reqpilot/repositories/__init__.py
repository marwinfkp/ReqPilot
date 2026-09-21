"""Repository layer - project-scoped data access (architecture section V).

Two rules every repository in this package follows:

1. **Project scoping is mandatory.** A query without a ``project_id`` predicate
   is a bug. :class:`~reqpilot.repositories.base.ProjectScopedRepository` makes
   the scoped form the easy one.
2. **Authorization is re-checked here**, not only at the API boundary, so that a
   missed endpoint decorator cannot leak data (architecture ADR-009).
"""

from reqpilot.repositories.approval import (
    ApprovalDecisionRepository,
    ApprovalTaskRepository,
)
from reqpilot.repositories.base import ProjectScopedRepository
from reqpilot.repositories.baseline import BaselineRepository
from reqpilot.repositories.database import (
    check_database_health,
    get_engine,
    get_session_factory,
    session_scope,
)
from reqpilot.repositories.requirements import (
    RequirementRepository,
    RequirementVersionRepository,
)

__all__ = [
    "ApprovalDecisionRepository",
    "ApprovalTaskRepository",
    "BaselineRepository",
    "ProjectScopedRepository",
    "RequirementRepository",
    "RequirementVersionRepository",
    "check_database_health",
    "get_engine",
    "get_session_factory",
    "session_scope",
]
