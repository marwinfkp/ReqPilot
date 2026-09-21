"""SQLAlchemy models (architecture section G).

The foundation tables (identity, projects, audit, runs) came with the
foundations phase. The requirements-repository phase adds the requirement,
approval and baseline tables. The knowledge-base phase adds the knowledge corpus
(``normative_source``, ``control``, ``knowledge_item``, ``knowledge_chunk``), the
per-project ``source_allowlist`` and ``evidence``. The extraction phase adds the
project corpus (``source_document``, ``source_chunk``), extraction proposals,
classification, acceptance criteria, the review queue, and prompt and model
provenance.

Tables belonging to later roadmap phases - utterances, glossary, quality
findings, conflicts, compliance, risk, SDLC, artefacts, evaluation - are still
deliberately absent. A test asserts that the migrations create nothing beyond
the tables named here.
"""

from reqpilot.domain.models.approval import ApprovalDecision, ApprovalTask
from reqpilot.domain.models.audit import AuditEvent
from reqpilot.domain.models.base import Base
from reqpilot.domain.models.baseline import Baseline, BaselineMember
from reqpilot.domain.models.extraction import (
    AcceptanceCriterion,
    ExtractionCandidate,
    ModelVersion,
    PromptTemplate,
    RequirementClassification,
    ReviewItem,
    SourceChunk,
    SourceDocument,
)
from reqpilot.domain.models.identity import Project, ProjectMember, User
from reqpilot.domain.models.knowledge import (
    Control,
    Evidence,
    KnowledgeChunk,
    KnowledgeItem,
    NormativeSource,
    SourceAllowlist,
)
from reqpilot.domain.models.requirements import Requirement, RequirementVersion
from reqpilot.domain.models.runs import AgentRun, GraphRun

__all__ = [
    "AcceptanceCriterion",
    "AgentRun",
    "ApprovalDecision",
    "ApprovalTask",
    "AuditEvent",
    "Base",
    "Baseline",
    "BaselineMember",
    "Control",
    "Evidence",
    "ExtractionCandidate",
    "GraphRun",
    "KnowledgeChunk",
    "KnowledgeItem",
    "ModelVersion",
    "NormativeSource",
    "Project",
    "ProjectMember",
    "PromptTemplate",
    "Requirement",
    "RequirementClassification",
    "RequirementVersion",
    "ReviewItem",
    "SourceAllowlist",
    "SourceChunk",
    "SourceDocument",
    "User",
]
