"""Relational schema (SQLAlchemy ORM).

One analysis run produces rows in: requirements, findings, compliance_mappings,
risks, sdlc_recommendations, workflows, trace_records and agent_runs.
Nested, display-only structures (e.g. a workflow's phases) are stored as JSON.
"""
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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
    # --- Two-stage analysis (see app/clarification.py and app/regeneration.py) -------------
    # Stage 1: preliminary | awaiting_clarification | analysing | completed | failed
    stage1_status: Mapped[str] = mapped_column(String(30), default="preliminary")
    # Stage 2 (optional refinement): not_started | in_progress | regenerating | regenerated | failed
    stage2_status: Mapped[str] = mapped_column(String(30), default="not_started")
    # Versions: a regeneration creates a NEW analysis row; earlier versions are never changed.
    version: Mapped[int] = mapped_column(Integer, default=1)
    parent_analysis_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # previous version
    root_analysis_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # version 1 (None = itself)
    preliminary: Mapped[dict] = mapped_column(JSON, default=dict)  # preliminary findings + question note
    submission_hash: Mapped[str] = mapped_column(String(64), default="")  # duplicate-submission guard

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
    stage: Mapped[str] = mapped_column(String(20), default="stage1")  # stage1 | stage2
    # Knowledge-base retrieval done by this agent: method, queries, passages retrieved / placed in
    # the prompt and citation checks (see rag.Knowledge)
    retrieval: Mapped[list] = mapped_column(JSON, default=list)


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
    status: Mapped[str] = mapped_column(String(20), default="pending_review")  # accepted | rejected | modified | obsolete
    # Information added AFTER submission (clarification answers, stakeholder responses, agreed
    # acceptance criteria), kept apart from the original text: [{kind, ref, text}]
    supplementary: Mapped[list] = mapped_column(JSON, default=list)
    # Knowledge-base evidence used for this requirement (e.g. domain terms from the glossary)
    citations: Mapped[list] = mapped_column(JSON, default=list)


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
    # After clarification: "" (not asked) | resolved | partially_resolved | unresolved | deferred
    resolution: Mapped[str] = mapped_column(String(30), default="")
    clarification_refs: Mapped[list] = mapped_column(JSON, default=list)  # e.g. ["Q-03"]
    # Knowledge-base evidence (see rag.Knowledge._citation) and its status:
    # "" (agent did not use the KB) | verified | unverified | none (no supporting passage) | unavailable
    citations: Mapped[list] = mapped_column(JSON, default=list)
    evidence_status: Mapped[str] = mapped_column(String(20), default="")


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
    status: Mapped[str] = mapped_column(String(30), default="open")  # open | accepted | rejected (human decision)
    # Evidence-backed (verified KB citation) vs inferred from the requirements (none)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    evidence_status: Mapped[str] = mapped_column(String(20), default="")


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
    # Supporting evidence from SDLC guidance (never changes the deterministic scores)
    citations: Mapped[list] = mapped_column(JSON, default=list)


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
    clarifications: Mapped[list] = mapped_column(JSON, default=list)  # supplementary info linked to the requirement
    citations: Mapped[list] = mapped_column(JSON, default=list)  # verified KB evidence linked to the requirement
    linked_findings: Mapped[list] = mapped_column(JSON, default=list)  # compliance/security findings on it


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
# Two-stage analysis: clarification, stakeholder interviews, change requests,
# selective regeneration (Stage 1 = app/clarification.py, Stage 2 = app/regeneration.py)
# --------------------------------------------------------------------------
class ClarificationQuestion(Base):
    """A question generated from the actual submission (Stage 1: at most 12;
    Stage 2: further batches). Requirement references made before the full
    analysis are *provisional* (rule extraction) and are mapped to the final IDs."""

    __tablename__ = "clarification_questions"
    __table_args__ = (UniqueConstraint("analysis_id", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(12))  # Q-01 ... (Stage 2: R2-01 ...)
    stage: Mapped[int] = mapped_column(Integer, default=1)
    batch: Mapped[int] = mapped_column(Integer, default=1)
    order: Mapped[int] = mapped_column(Integer, default=0)  # priority rank within the batch
    question: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(40))  # conflict | ambiguity | security | compliance | ...
    issue_type: Mapped[str] = mapped_column(String(40), default="")  # contradiction vs missing information
    priority: Mapped[str] = mapped_column(String(10))  # Critical | High | Medium | Low
    reason: Mapped[str] = mapped_column(Text, default="")
    impact: Mapped[str] = mapped_column(Text, default="")
    answer_format: Mapped[str] = mapped_column(String(20), default="short_text")
    options: Mapped[list] = mapped_column(JSON, default=list)
    stakeholder_role: Mapped[str] = mapped_column(String(60), default="")
    related_requirements: Mapped[list] = mapped_column(JSON, default=list)  # [{ref, line, text}]
    requirement_ids: Mapped[list] = mapped_column(JSON, default=list)  # final IDs once known
    topic: Mapped[str] = mapped_column(String(60), default="")  # compliance topic / security area / SDLC factor
    # open | answered | deferred | needs_stakeholder
    status: Mapped[str] = mapped_column(String(30), default="open")
    # after the analysis: resolved | partially_resolved | unresolved | deferred
    resolution: Mapped[str] = mapped_column(String(30), default="")
    parent_question_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # follow-up of
    generated_by: Mapped[str] = mapped_column(String(60), default="rules")
    applied_in_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    citations: Mapped[list] = mapped_column(JSON, default=list)  # KB passages the question is grounded in
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    response: Mapped["ClarificationResponse | None"] = relationship(
        back_populates="question", cascade="all, delete-orphan", uselist=False)


class ClarificationResponse(Base):
    """The current answer to one question (one per question - edits update it and
    the previous value is kept in the audit log)."""

    __tablename__ = "clarification_responses"
    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("clarification_questions.id", ondelete="CASCADE"),
                                             unique=True)
    answer: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # accepted | pending | deferred
    affects_requirements: Mapped[bool] = mapped_column(Boolean, default=False)
    stage: Mapped[int] = mapped_column(Integer, default=1)  # stage in which it was answered
    responded_by: Mapped[str] = mapped_column(String(200))
    responder_role: Mapped[str] = mapped_column(String(40))
    responded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    question: Mapped[ClarificationQuestion] = relationship(back_populates="response")


class StakeholderInteraction(Base):
    """A structured stakeholder interview recorded by an authorised user. ReqPilot
    never contacts stakeholders itself - it only collects what the user records."""

    __tablename__ = "stakeholder_interactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), index=True)
    stakeholder_role: Mapped[str] = mapped_column(String(60))
    interviewee: Mapped[str] = mapped_column(String(200), default="")  # name given by the recorder
    topic: Mapped[str] = mapped_column(String(300), default="")
    requirement_ids: Mapped[list] = mapped_column(JSON, default=list)
    entries: Mapped[list] = mapped_column(JSON, default=list)  # [{question, answer}]
    recorded_by: Mapped[str] = mapped_column(String(200))
    recorder_role: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default="pending_review")  # accepted | rejected | changes_requested
    reviewed_by: Mapped[str] = mapped_column(String(200), default="")
    review_comment: Mapped[str] = mapped_column(Text, default="")
    applied_in_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RequirementChange(Base):
    """A proposed change to the requirement set. The original requirement is never
    overwritten: an approved change is applied in a NEW analysis version."""

    __tablename__ = "requirement_changes"
    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(12))  # RC-01 ...
    requirement_id: Mapped[str] = mapped_column(String(30), default="")  # empty for a new requirement
    # edit | acceptance_criteria | new | resolve_conflict | business_rule | constraint | obsolete
    change_type: Mapped[str] = mapped_column(String(30))
    new_kind: Mapped[str] = mapped_column(String(30), default="")  # kind of an added requirement
    original_text: Mapped[str] = mapped_column(Text, default="")
    proposed_text: Mapped[str] = mapped_column(Text, default="")
    approved_text: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(200), default="user")  # user | clarification Q-.. | interview #..
    proposed_by: Mapped[str] = mapped_column(String(200))
    proposer_role: Mapped[str] = mapped_column(String(40))
    # draft | pending_review | approved | rejected | changes_requested
    status: Mapped[str] = mapped_column(String(30), default="pending_review")
    reviewed_by: Mapped[str] = mapped_column(String(200), default="")
    review_comment: Mapped[str] = mapped_column(Text, default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_in_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RegenerationRun(Base):
    """One selective regeneration: which agents were re-run and why."""

    __tablename__ = "regeneration_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), index=True)
    new_analysis_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    previous_version: Mapped[int] = mapped_column(Integer)
    new_version: Mapped[int] = mapped_column(Integer)
    changed_requirements: Mapped[list] = mapped_column(JSON, default=list)
    inputs: Mapped[dict] = mapped_column(JSON, default=dict)  # change / question / interview ids applied
    affected_agents: Mapped[list] = mapped_column(JSON, default=list)  # plan: [{agent, reasons}]
    agents_rerun: Mapped[list] = mapped_column(JSON, default=list)
    outputs_retained: Mapped[list] = mapped_column(JSON, default=list)
    outputs_updated: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | completed | failed
    error: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    # regulation | organisational_policy | security_standard | reference | engineering_guideline | domain_glossary
    doc_type: Mapped[str] = mapped_column(String(40))
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
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)  # PDF page the chunk starts on
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
