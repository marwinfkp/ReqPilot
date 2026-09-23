"""SQLAlchemy models (architecture section G).

The foundation tables (identity, projects, audit, runs) came with the
foundations phase. The requirements-repository phase adds the requirement,
approval and baseline tables. The knowledge-base phase adds the knowledge corpus
(``normative_source``, ``control``, ``knowledge_item``, ``knowledge_chunk``), the
per-project ``source_allowlist`` and ``evidence``. The extraction phase adds the
project corpus (``source_document``, ``source_chunk``), extraction proposals,
classification, acceptance criteria, the review queue, and prompt and model
provenance. The elicitation phase adds stakeholders, interview sessions,
utterances, quality findings and clarifications. The quality phase adds
conflicts and the project glossary. The compliance phase adds compliance
mappings and their evidence links, compliance gaps, and security/privacy
findings and their evidence links. The risk phase adds the versioned severity
matrix, risk items and their evidence links, and mitigation suggestions.

Tables belonging to later roadmap phases - SDLC, artefacts, evaluation - are
still deliberately absent. A test
asserts that the migrations create nothing beyond the tables named here.
"""

from reqpilot.domain.models.approval import ApprovalDecision, ApprovalTask
from reqpilot.domain.models.audit import AuditEvent
from reqpilot.domain.models.base import Base
from reqpilot.domain.models.baseline import Baseline, BaselineMember
from reqpilot.domain.models.compliance import (
    ComplianceGap,
    ComplianceMapping,
    ComplianceMappingEvidence,
    SecurityPrivacyFinding,
    SecurityPrivacyFindingEvidence,
)
from reqpilot.domain.models.elicitation import (
    Clarification,
    InterviewSession,
    QualityFinding,
    Stakeholder,
    Utterance,
)
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
from reqpilot.domain.models.quality import Conflict, GlossaryTerm
from reqpilot.domain.models.requirements import Requirement, RequirementVersion
from reqpilot.domain.models.risk import (
    Risk,
    RiskEvidence,
    RiskMatrixCell,
    RiskMitigation,
)
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
    "Clarification",
    "ComplianceGap",
    "ComplianceMapping",
    "ComplianceMappingEvidence",
    "Conflict",
    "Control",
    "Evidence",
    "ExtractionCandidate",
    "GlossaryTerm",
    "GraphRun",
    "InterviewSession",
    "KnowledgeChunk",
    "KnowledgeItem",
    "ModelVersion",
    "NormativeSource",
    "Project",
    "ProjectMember",
    "PromptTemplate",
    "QualityFinding",
    "Requirement",
    "RequirementClassification",
    "RequirementVersion",
    "ReviewItem",
    "Risk",
    "RiskEvidence",
    "RiskMatrixCell",
    "RiskMitigation",
    "SecurityPrivacyFinding",
    "SecurityPrivacyFindingEvidence",
    "SourceAllowlist",
    "SourceChunk",
    "SourceDocument",
    "Stakeholder",
    "User",
    "Utterance",
]
