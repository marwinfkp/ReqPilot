"""Relational schema (SQLAlchemy ORM).

One analysis run produces rows in: requirements, findings, compliance_mappings,
risks, sdlc_recommendations, workflows, trace_records and agent_runs.
Nested, display-only structures (e.g. a workflow's phases) are stored as JSON.
"""
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app import config
from app.database import IS_POSTGRES, Base

if IS_POSTGRES:
    from pgvector.sqlalchemy import Vector

    EmbeddingType = Vector(config.EMBEDDING_DIMENSIONS)
else:  # SQLite fallback used in tests
    EmbeddingType = JSON


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Users, sessions, audit
# --------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(40))  # see security.ROLE_PERMISSIONS
    password_hash: Mapped[str] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserSession(Base):
    """Server-side login session. Only a SHA-256 hash of the token is stored."""

    __tablename__ = "user_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    user_email: Mapped[str] = mapped_column(String(200), default="system")
    user_role: Mapped[str] = mapped_column(String(40), default="system")
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(60), default="")
    entity_id: Mapped[str] = mapped_column(String(60), default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    ip_address: Mapped[str] = mapped_column(String(64), default="")


class Setting(Base):
    """Small key/value store for admin-editable settings (SDLC weights, retention)."""

    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------
# Projects and analysis runs
# --------------------------------------------------------------------------
class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(Text, default="")
    jurisdiction: Mapped[str] = mapped_column(String(60), default="India")
    scale: Mapped[str] = mapped_column(String(30), default="medium")
    budget: Mapped[str] = mapped_column(String(200), default="")
    schedule: Mapped[str] = mapped_column(String(200), default="")
    stakeholder_availability: Mapped[str] = mapped_column(String(20), default="")
    owner_email: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    analyses: Mapped[list["Analysis"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Analysis.id.desc()"
    )


class Analysis(Base):
    __tablename__ = "analyses"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[str] = mapped_column(String(200))
    # queued | running | completed | completed_with_warnings | failed
    status: Mapped[str] = mapped_column(String(40), default="queued")
    mode: Mapped[str] = mapped_column(String(20))  # "openai" or "demo"
    model_name: Mapped[str] = mapped_column(String(80), default="")
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0..100
    current_stage: Mapped[str] = mapped_column(String(200), default="Queued")
    error: Mapped[str] = mapped_column(Text, default="")
    # Original submission is preserved exactly as provided.
    input_text: Mapped[str] = mapped_column(Text, default="")
    input_sources: Mapped[list] = mapped_column(JSON, default=list)  # [{name, chars}]
    submitted_count: Mapped[int] = mapped_column(Integer, default=0)
    # Results that are naturally nested / display-only are kept as JSON:
    summary: Mapped[str] = mapped_column(Text, default="")
    key_findings: Mapped[list] = mapped_column(JSON, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    security_coverage: Mapped[list] = mapped_column(JSON, default=list)
    applicable_sources: Mapped[list] = mapped_column(JSON, default=list)
    use_cases: Mapped[list] = mapped_column(JSON, default=list)
    validation_report: Mapped[list] = mapped_column(JSON, default=list)
    baseline_status: Mapped[str] = mapped_column(String(30), default="draft")  # draft | approved
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project] = relationship(back_populates="analyses")


class AgentRun(Base):
    """One row per agent execution: status, timing, mode and LLM usage."""

    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), index=True)
    order: Mapped[int] = mapped_column(Integer)
    agent: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20))  # completed | failed | partial
    mode: Mapped[str] = mapped_column(String(20))  # rules | llm+rules
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    llm_calls: Mapped[list] = mapped_column(JSON, default=list)


class Requirement(Base):
    __tablename__ = "requirements"
    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), index=True)
    req_id: Mapped[str] = mapped_column(String(30))  # e.g. FR-PAY-001
    statement: Mapped[str] = mapped_column(Text)  # normalised (or human-modified) text
    original_text: Mapped[str] = mapped_column(Text)  # exactly as submitted
    source: Mapped[str] = mapped_column(String(200))  # stakeholder / document
    kind: Mapped[str] = mapped_column(String(30))  # functional | non_functional | business_rule | constraint
    categories: Mapped[list] = mapped_column(JSON, default=list)
    priority: Mapped[str] = mapped_column(String(10), default="Medium")
    risk_level: Mapped[str] = mapped_column(String(10), default="Low")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    dependencies: Mapped[list] = mapped_column(JSON, default=list)
    user_story: Mapped[str] = mapped_column(Text, default="")
    acceptance_criteria: Mapped[list] = mapped_column(JSON, default=list)
    test_recommendations: Mapped[list] = mapped_column(JSON, default=list)
    generated_by: Mapped[str] = mapped_column(String(60), default="rules")
    status: Mapped[str] = mapped_column(String(20), default="pending_review")  # accepted | rejected | modified


class Finding(Base):
    """Any analysis finding: quality issue, compliance gap, security gap, threat,
    privacy consideration, assumption, open question or input warning."""

    __tablename__ = "findings"
    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    issue_type: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))  # Low | Medium | High | Critical
    title: Mapped[str] = mapped_column(String(300))
    explanation: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    suggestion: Mapped[str] = mapped_column(Text, default="")
    question: Mapped[str] = mapped_column(Text, default="")
    requirement_ids: Mapped[list] = mapped_column(JSON, default=list)
    generated_by: Mapped[str] = mapped_column(String(60), default="rules")


class ComplianceMapping(Base):
    __tablename__ = "compliance_mappings"
    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), index=True)
    requirement_id: Mapped[str] = mapped_column(String(30))
    document_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    document_name: Mapped[str] = mapped_column(String(300))
    doc_type: Mapped[str] = mapped_column(String(40))
    authenticity: Mapped[str] = mapped_column(String(20))  # official | illustrative | reference
    control_ref: Mapped[str] = mapped_column(String(60))
    control_title: Mapped[str] = mapped_column(String(300))
    jurisdiction: Mapped[str] = mapped_column(String(60))
    version: Mapped[str] = mapped_column(String(40))
    evidence_text: Mapped[str] = mapped_column(Text, default="")
    citation_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    rationale: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    needs_verification: Mapped[bool] = mapped_column(Boolean, default=True)
    generated_by: Mapped[str] = mapped_column(String(60), default="rules")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # approved | flagged


class Risk(Base):
    __tablename__ = "risks"
    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), index=True)
    risk_id: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(30))
    likelihood: Mapped[int] = mapped_column(Integer)  # 1..5
    impact: Mapped[int] = mapped_column(Integer)  # 1..5
    score: Mapped[int] = mapped_column(Integer)  # likelihood x impact
    severity: Mapped[str] = mapped_column(String(10))
    mitigation: Mapped[str] = mapped_column(Text, default="")
    requirement_ids: Mapped[list] = mapped_column(JSON, default=list)
    generated_by: Mapped[str] = mapped_column(String(60), default="rules")


class SdlcRecommendation(Base):
    __tablename__ = "sdlc_recommendations"
    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), unique=True)
    recommended_model: Mapped[str] = mapped_column(String(60))
    is_hybrid: Mapped[bool] = mapped_column(Boolean, default=False)
    factors: Mapped[list] = mapped_column(JSON)  # [{id,name,value,rule_value,adjustment,explanation,...}]
    ranking: Mapped[list] = mapped_column(JSON)  # [{model,score,rank,breakdown,...}]
    rules_applied: Mapped[list] = mapped_column(JSON, default=list)
    justification: Mapped[str] = mapped_column(Text, default="")
    trade_offs: Mapped[list] = mapped_column(JSON, default=list)
    hybrid_explanation: Mapped[str] = mapped_column(Text, default="")
    generated_by: Mapped[str] = mapped_column(String(60), default="rules")
    status: Mapped[str] = mapped_column(String(30), default="pending_approval")  # approved | overridden
    approved_model: Mapped[str] = mapped_column(String(60), default="")


class Workflow(Base):
    __tablename__ = "workflows"
    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), unique=True)
    sdlc_model: Mapped[str] = mapped_column(String(60))
    phases: Mapped[list] = mapped_column(JSON)
    customisations: Mapped[list] = mapped_column(JSON, default=list)
    generated_by: Mapped[str] = mapped_column(String(60), default="rules")
    status: Mapped[str] = mapped_column(String(30), default="draft")  # approved | changes_requested


class TraceRecord(Base):
    """One row of the Requirements Traceability Matrix."""

    __tablename__ = "trace_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), index=True)
    requirement_id: Mapped[str] = mapped_column(String(30))
    source_statement: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(200))
    categories: Mapped[list] = mapped_column(JSON, default=list)
    regulatory_controls: Mapped[list] = mapped_column(JSON, default=list)
    security_controls: Mapped[list] = mapped_column(JSON, default=list)
    user_story: Mapped[str] = mapped_column(Text, default="")
    acceptance_criteria: Mapped[list] = mapped_column(JSON, default=list)
    tests: Mapped[list] = mapped_column(JSON, default=list)
    workflow_phases: Mapped[list] = mapped_column(JSON, default=list)
    missing_links: Mapped[list] = mapped_column(JSON, default=list)
    coverage: Mapped[float] = mapped_column(Float, default=0.0)


class Approval(Base):
    """Human-in-the-loop decision history (who, what, when, before/after)."""

    __tablename__ = "approvals"
    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), index=True)
    target_type: Mapped[str] = mapped_column(String(30))  # requirement | mapping | sdlc | workflow | baseline
    target_id: Mapped[str] = mapped_column(String(60))
    action: Mapped[str] = mapped_column(String(30))
    reviewer_email: Mapped[str] = mapped_column(String(200))
    reviewer_role: Mapped[str] = mapped_column(String(40))
    comment: Mapped[str] = mapped_column(Text, default="")
    previous_value: Mapped[dict] = mapped_column(JSON, default=dict)
    new_value: Mapped[dict] = mapped_column(JSON, default=dict)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------
# Knowledge base (RAG)
# --------------------------------------------------------------------------
class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    source_organisation: Mapped[str] = mapped_column(String(200))
    jurisdiction: Mapped[str] = mapped_column(String(60))
    version: Mapped[str] = mapped_column(String(40), default="")
    effective_date: Mapped[str] = mapped_column(String(40), default="")
    doc_type: Mapped[str] = mapped_column(String(40))  # regulation | organisational_policy | security_standard | reference
    authenticity: Mapped[str] = mapped_column(String(20))  # official | illustrative | reference
    applicability: Mapped[str] = mapped_column(Text, default="")
    filename: Mapped[str] = mapped_column(String(300), default="")
    allowlisted: Mapped[bool] = mapped_column(Boolean, default=False)  # only allowlisted docs are retrieved
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    uploaded_by: Mapped[str] = mapped_column(String(200), default="system")
    upload_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentChunk.chunk_index"
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    section_ref: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(300), default="")
    text: Mapped[str] = mapped_column(Text)
    topics: Mapped[list] = mapped_column(JSON, default=list)
    suspicious: Mapped[bool] = mapped_column(Boolean, default=False)  # possible prompt injection
    embedding = mapped_column(EmbeddingType, nullable=True)

    document: Mapped[KnowledgeDocument] = relationship(back_populates="chunks")


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")
    mode: Mapped[str] = mapped_column(String(20))
    model_name: Mapped[str] = mapped_column(String(80), default="")
    results: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    run_by: Mapped[str] = mapped_column(String(200), default="")
