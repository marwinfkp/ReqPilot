"""SQLAlchemy models (architecture section G).

The foundation tables (identity, projects, audit, runs) came with the
foundations phase. The requirements-repository phase adds the requirement,
approval and baseline tables.

Tables belonging to later roadmap phases - knowledge base, compliance, risk,
SDLC, artefacts, evaluation - are still deliberately absent. A test asserts
that the migrations create nothing beyond the tables named here.
"""

from reqpilot.domain.models.approval import ApprovalDecision, ApprovalTask
from reqpilot.domain.models.audit import AuditEvent
from reqpilot.domain.models.base import Base
from reqpilot.domain.models.baseline import Baseline, BaselineMember
from reqpilot.domain.models.identity import Project, ProjectMember, User
from reqpilot.domain.models.requirements import Requirement, RequirementVersion
from reqpilot.domain.models.runs import AgentRun, GraphRun

__all__ = [
    "AgentRun",
    "ApprovalDecision",
    "ApprovalTask",
    "AuditEvent",
    "Base",
    "Baseline",
    "BaselineMember",
    "GraphRun",
    "Project",
    "ProjectMember",
    "Requirement",
    "RequirementVersion",
    "User",
]
