"""Compliance mappings, gaps and security/privacy findings (roadmap phase P6; architecture G.6).

* ``compliance_mapping`` - a **candidate** mapping of one exact requirement
  version to one expected control, grounded in evidence supplied to the run that
  proposed it (``FR-CMP-001``). It survived deterministic validation: every
  citation resolved to that run's evidence, the jurisdiction and source type came
  from the cited sources, and no prohibited assertion was present. It is not a
  legal determination. A high-impact interpretation (``FR-CMP-004``) is
  ``PENDING_REVIEW`` until the Compliance Officer decides it at G2; a database
  check makes a high-impact mapping in ``CANDIDATE`` status impossible to store.
* ``compliance_mapping_evidence`` - which evidence rows a mapping cites. Composite
  foreign keys pin both sides to the mapping's project: evidence from another
  project cannot be linked, whatever wrote the row.
* ``compliance_gap`` - an expected control with no covering mapping, recorded by
  the rule engine for one run (``FR-CMP-002``; K.2), or recorded when G2 rejects
  the mapping that covered it. Never a model output.
* ``security_privacy_finding`` - a derived security or privacy requirement with
  **both** risk levels (``FR-SEC-001``..``003``; I.7): ``proposed_risk_level``
  exactly as the model suggested (kept for audit) and ``risk_level``, the
  authoritative ``max(normalised proposal, catalogue floor)``. Database checks
  enforce that the stored level is exactly that maximum, that a high-impact
  family has a HIGH floor, that a privacy finding has at least a MEDIUM floor,
  and that a HIGH finding is never outside G3 review (INV-G3).
* ``security_privacy_finding_evidence`` - optional supporting evidence, pinned to
  the project the same way.

Content is immutable: a later requirement version gets its own analysis, and
nothing is transferred across versions. Only the review status moves, once, and
only through a G2/G3 decision.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.types import Uuid

from reqpilot.domain.enums import (
    ComplianceGapOrigin,
    ComplianceMappingStatus,
    ComplianceRelationship,
    EvidenceStatus,
    FindingDetector,
    NormativeSourceType,
    ObligationKind,
    SecurityControlFamily,
    SecurityFindingStatus,
    SecurityPrivacyCategory,
    SecurityRiskLevel,
)
from reqpilot.domain.errors import ImmutableRecordError
from reqpilot.domain.models.base import Base, created_at_column, utc_now, uuid_pk

JsonType = JSON().with_variant(postgresql.JSONB(), "postgresql")


def _updated_at_column() -> Any:
    return mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


def _level_rank(column: str) -> str:
    return f"(CASE {column} WHEN 'LOW' THEN 1 WHEN 'MEDIUM' THEN 2 WHEN 'HIGH' THEN 3 END)"


#: I.7's high-impact families, as stored enum names. A database check requires
#: a HIGH floor for each, so no row can record a lower floor for one of them.
HIGH_IMPACT_FAMILY_NAMES = (
    "AUTHENTICATION",
    "AUTHORISATION",
    "CRYPTOGRAPHY",
    "AUDIT_LOGGING",
    "TRANSACTION_INTEGRITY",
    "CONSENT",
    "RETENTION",
    "SUBJECT_RIGHTS",
)
_HIGH_IMPACT_SQL = ", ".join(f"'{n}'" for n in HIGH_IMPACT_FAMILY_NAMES)

#: ``risk_level = max(normalised_proposed_level, catalogue_floor)``, exactly.
RISK_IS_MAX_SQL = (
    f"{_level_rank('risk_level')} = CASE WHEN {_level_rank('catalogue_floor')} >= "
    f"{_level_rank('normalised_proposed_level')} THEN {_level_rank('catalogue_floor')} "
    f"ELSE {_level_rank('normalised_proposed_level')} END"
)
HIGH_IMPACT_FLOOR_SQL = f"family NOT IN ({_HIGH_IMPACT_SQL}) OR catalogue_floor = 'HIGH'"
PRIVACY_FLOOR_SQL = "category <> 'PRIVACY' OR catalogue_floor IN ('MEDIUM', 'HIGH')"
HIGH_RISK_GATED_SQL = "risk_level <> 'HIGH' OR status <> 'PROPOSED'"
HIGH_IMPACT_GATED_SQL = "NOT is_high_impact OR status <> 'CANDIDATE'"


class ComplianceMapping(Base):
    """A validated candidate mapping: requirement version -> expected control (G.6)."""

    __tablename__ = "compliance_mapping"
    __table_args__ = (
        UniqueConstraint("id", "project_id", name="id_project"),
        ForeignKeyConstraint(
            ["requirement_version_id", "project_id"],
            ["requirement_version.id", "requirement_version.project_id"],
            name="fk_compliance_mapping_version_same_project",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["approval_task_id", "project_id"],
            ["approval_task.id", "approval_task.project_id"],
            name="fk_compliance_mapping_task_same_project",
        ),
        CheckConstraint("evidence_count >= 1", name="cites_evidence"),
        CheckConstraint("length(rationale) >= 1", name="rationale_not_empty"),
        CheckConstraint("length(jurisdiction) >= 2", name="has_jurisdiction"),
        CheckConstraint(HIGH_IMPACT_GATED_SQL, name="high_impact_is_gated"),
        # One active (not G2-rejected) mapping per version and control.
        Index(
            "uq_compliance_mapping_active",
            "requirement_version_id",
            "control_key",
            unique=True,
            postgresql_where=text("status <> 'REJECTED'"),
            sqlite_where=text("status <> 'REJECTED'"),
        ),
        Index("ix_compliance_mapping_project_status", "project_id", "status"),
    )

    #: Written once, when validation accepts the mapping.
    CONTENT_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "id",
            "project_id",
            "requirement_version_id",
            "graph_run_id",
            "agent_run_id",
            "control_key",
            "control_title",
            "obligation_kind",
            "checklist_ref",
            "checklist_domain",
            "checklist_jurisdiction",
            "relationship",
            "rationale",
            "candidate_text",
            "implied_obligation",
            "jurisdiction",
            "source_type",
            "jurisdictions",
            "source_types",
            "citations",
            "evidence_count",
            "is_high_impact",
            "high_impact_reasons",
            "review_signal",
            "language_rules_version",
            "content_hash",
            "recorded_by",
            "created_at",
        }
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    requirement_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    graph_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("graph_run.id", ondelete="SET NULL"), nullable=True, index=True
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agent_run.id", ondelete="SET NULL"), nullable=True
    )
    # --- the control (from the versioned checklist, never from the model) -----
    control_key: Mapped[str] = mapped_column(String(64), nullable=False)
    control_title: Mapped[str] = mapped_column(String(300), nullable=False)
    obligation_kind: Mapped[ObligationKind] = mapped_column(
        SAEnum(ObligationKind, name="obligation_kind_enum"), nullable=False
    )
    #: ``compliance_checklists@<version>`` that defined the control.
    checklist_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    checklist_domain: Mapped[str] = mapped_column(String(100), nullable=False)
    checklist_jurisdiction: Mapped[str] = mapped_column(String(20), nullable=False)
    # --- the candidate mapping ------------------------------------------------
    relationship: Mapped[ComplianceRelationship] = mapped_column(
        SAEnum(ComplianceRelationship, name="compliance_relationship_enum"), nullable=False
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    #: Hedged candidate compliance text; passed the language check (FR-CMP-006).
    candidate_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: An implied checkpoint or obligation the model pointed at (FR-CMP-003).
    implied_obligation: Mapped[str | None] = mapped_column(Text, nullable=True)
    # --- provenance, derived from the cited evidence (FR-CMP-005) -------------
    #: The jurisdiction and C.1 source type of the first cited source. Never
    #: model-supplied: the model's own claim is checked against these and a
    #: mismatch drops the mapping.
    jurisdiction: Mapped[str] = mapped_column(String(20), nullable=False)
    source_type: Mapped[NormativeSourceType] = mapped_column(
        SAEnum(NormativeSourceType, name="normative_source_type_enum", create_type=False),
        nullable=False,
    )
    #: Jurisdiction codes of every cited source, as a JSON list.
    jurisdictions: Mapped[list] = mapped_column(JsonType, nullable=False)
    #: C.1 source types of the cited sources, as a JSON list.
    source_types: Mapped[list] = mapped_column(JsonType, nullable=False)
    #: Snapshot of every citation: evidence id, source title, type, issuing
    #: body, jurisdiction, version, effective and curation dates, span, KB version.
    citations: Mapped[list] = mapped_column(JsonType, nullable=False)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # --- G2 ---------------------------------------------------------------------
    is_high_impact: Mapped[bool] = mapped_column(Boolean, nullable=False)
    #: Which deterministic rule(s) made it high-impact (checklist, source type,
    #: or the model's own flag, which can only add).
    high_impact_reasons: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    #: Heuristic review-prioritisation signal in [0, 1] - not a probability.
    review_signal: Mapped[float | None] = mapped_column(Float, nullable=True)
    language_rules_version: Mapped[str] = mapped_column(String(20), nullable=False)
    #: sha256 over the governed content and the requirement version's hash: what
    #: a G2 decision is bound to.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    # --- review (mutable, once) ----------------------------------------------
    status: Mapped[ComplianceMappingStatus] = mapped_column(
        SAEnum(ComplianceMappingStatus, name="compliance_mapping_status_enum"), nullable=False
    )
    approval_task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column()
    updated_at: Mapped[dt.datetime] = _updated_at_column()


class ComplianceMappingEvidence(Base):
    """One evidence row cited by one mapping. Append-only; same project on both sides."""

    __tablename__ = "compliance_mapping_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["mapping_id", "project_id"],
            ["compliance_mapping.id", "compliance_mapping.project_id"],
            name="fk_compliance_mapping_evidence_mapping",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["evidence_id", "project_id"],
            ["evidence.id", "evidence.project_id"],
            name="fk_compliance_mapping_evidence_evidence",
        ),
        UniqueConstraint("mapping_id", "evidence_id", name="mapping_evidence"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mapping_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    evidence_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()


class ComplianceGap(Base):
    """An expected control with no covering mapping (K.2). Append-only."""

    __tablename__ = "compliance_gap"
    __table_args__ = (
        # A rule-engine gap once per run and control; a G2-rejection gap once per mapping.
        Index(
            "uq_compliance_gap_rule_run_control",
            "graph_run_id",
            "control_key",
            "checklist_jurisdiction",
            unique=True,
            postgresql_where=text("origin = 'RULE_ENGINE'"),
            sqlite_where=text("origin = 'RULE_ENGINE'"),
        ),
        Index(
            "uq_compliance_gap_rejected_mapping",
            "related_mapping_id",
            unique=True,
            postgresql_where=text("origin = 'G2_REJECTION'"),
            sqlite_where=text("origin = 'G2_REJECTION'"),
        ),
        ForeignKeyConstraint(
            ["related_mapping_id", "project_id"],
            ["compliance_mapping.id", "compliance_mapping.project_id"],
            name="fk_compliance_gap_mapping_same_project",
            ondelete="CASCADE",
        ),
        CheckConstraint("length(reason) >= 1", name="reason_not_empty"),
        CheckConstraint(
            "origin <> 'G2_REJECTION' OR related_mapping_id IS NOT NULL",
            name="rejection_names_mapping",
        ),
        Index("ix_compliance_gap_project_run", "project_id", "graph_run_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    graph_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("graph_run.id", ondelete="CASCADE"), nullable=False
    )
    control_key: Mapped[str] = mapped_column(String(64), nullable=False)
    control_title: Mapped[str] = mapped_column(String(300), nullable=False)
    obligation_kind: Mapped[ObligationKind] = mapped_column(
        SAEnum(ObligationKind, name="obligation_kind_enum", create_type=False), nullable=False
    )
    is_high_impact: Mapped[bool] = mapped_column(Boolean, nullable=False)
    checklist_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    checklist_domain: Mapped[str] = mapped_column(String(100), nullable=False)
    checklist_jurisdiction: Mapped[str] = mapped_column(String(20), nullable=False)
    origin: Mapped[ComplianceGapOrigin] = mapped_column(
        SAEnum(ComplianceGapOrigin, name="compliance_gap_origin_enum"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    related_mapping_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    recorded_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()


class SecurityPrivacyFinding(Base):
    """A derived security or privacy requirement with its authoritative risk (G.6, I.7)."""

    __tablename__ = "security_privacy_finding"
    __table_args__ = (
        UniqueConstraint("id", "project_id", name="id_project"),
        ForeignKeyConstraint(
            ["requirement_version_id", "project_id"],
            ["requirement_version.id", "requirement_version.project_id"],
            name="fk_security_privacy_finding_version_same_project",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["approval_task_id", "project_id"],
            ["approval_task.id", "approval_task.project_id"],
            name="fk_security_privacy_finding_task_same_project",
        ),
        CheckConstraint("length(derived_requirement) >= 1", name="requirement_not_empty"),
        CheckConstraint("length(escalation_reason) >= 1", name="escalation_reason_not_empty"),
        # INV-G3 at the database: the authoritative level is exactly the maximum
        # of the normalised proposal and the floor, a high-impact family always
        # has a HIGH floor, privacy always at least MEDIUM, and a HIGH finding is
        # never outside G3 review.
        CheckConstraint(RISK_IS_MAX_SQL, name="risk_is_max_of_proposal_and_floor"),
        CheckConstraint(HIGH_IMPACT_FLOOR_SQL, name="high_impact_family_floor_high"),
        CheckConstraint(PRIVACY_FLOOR_SQL, name="privacy_floor_medium"),
        CheckConstraint(HIGH_RISK_GATED_SQL, name="high_risk_is_gated"),
        CheckConstraint(
            "evidence_status <> 'SUPPORTED' OR evidence_count >= 1",
            name="supported_cites_evidence",
        ),
        Index(
            "uq_security_privacy_finding_active",
            "requirement_version_id",
            "family",
            unique=True,
            postgresql_where=text("status <> 'REJECTED'"),
            sqlite_where=text("status <> 'REJECTED'"),
        ),
        Index("ix_security_privacy_finding_project_status", "project_id", "status"),
    )

    CONTENT_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "id",
            "project_id",
            "requirement_version_id",
            "graph_run_id",
            "agent_run_id",
            "category",
            "family",
            "derived_requirement",
            "rationale",
            "risk_rationale",
            "evidence_status",
            "evidence_count",
            "citations",
            "proposed_risk_level",
            "normalised_proposed_level",
            "catalogue_floor",
            "risk_level",
            "risk_rules_version",
            "escalation_reason",
            "detected_by",
            "source_signal_finding_id",
            "review_signal",
            "content_hash",
            "recorded_by",
            "created_at",
        }
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    requirement_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    graph_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("graph_run.id", ondelete="SET NULL"), nullable=True, index=True
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agent_run.id", ondelete="SET NULL"), nullable=True
    )
    category: Mapped[SecurityPrivacyCategory] = mapped_column(
        SAEnum(SecurityPrivacyCategory, name="security_privacy_category_enum"), nullable=False
    )
    family: Mapped[SecurityControlFamily] = mapped_column(
        SAEnum(SecurityControlFamily, name="security_control_family_enum"), nullable=False
    )
    #: The derived requirement proposal. Never baselined or approved by P6.
    derived_requirement: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    risk_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_status: Mapped[EvidenceStatus] = mapped_column(
        SAEnum(EvidenceStatus, name="evidence_status_enum"), nullable=False
    )
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    citations: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    # --- I.7: both levels, and why ----------------------------------------------
    #: Exactly what the model proposed (or nothing), kept for audit. Never read by G3.
    proposed_risk_level: Mapped[str | None] = mapped_column(String(50), nullable=True)
    normalised_proposed_level: Mapped[SecurityRiskLevel] = mapped_column(
        SAEnum(SecurityRiskLevel, name="security_risk_level_enum"), nullable=False
    )
    catalogue_floor: Mapped[SecurityRiskLevel] = mapped_column(
        SAEnum(SecurityRiskLevel, name="security_risk_level_enum", create_type=False),
        nullable=False,
    )
    #: **Authoritative.** Written only by the deterministic evaluator; G3 reads it.
    risk_level: Mapped[SecurityRiskLevel] = mapped_column(
        SAEnum(SecurityRiskLevel, name="security_risk_level_enum", create_type=False),
        nullable=False,
    )
    risk_rules_version: Mapped[str] = mapped_column(String(100), nullable=False)
    escalation_reason: Mapped[str] = mapped_column(Text, nullable=False)
    #: ``agent`` - a validated model proposal; ``rule`` - the catalogue baseline for
    #: an indicated family no validated proposal covered.
    detected_by: Mapped[FindingDetector] = mapped_column(
        SAEnum(FindingDetector, name="finding_detector_enum", create_type=False), nullable=False
    )
    #: The P5 security/privacy signal (a quality finding) that indicated it, if any.
    source_signal_finding_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "quality_finding.id",
            ondelete="SET NULL",
            # Explicit: the convention's name would exceed PostgreSQL's 63 characters.
            name="fk_security_privacy_finding_source_signal",
        ),
        nullable=True,
    )
    review_signal: Mapped[float | None] = mapped_column(Float, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[SecurityFindingStatus] = mapped_column(
        SAEnum(SecurityFindingStatus, name="security_finding_status_enum"), nullable=False
    )
    approval_task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column()
    updated_at: Mapped[dt.datetime] = _updated_at_column()


class SecurityPrivacyFindingEvidence(Base):
    """One evidence row supporting one finding. Append-only; same project on both sides."""

    __tablename__ = "security_privacy_finding_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["finding_id", "project_id"],
            ["security_privacy_finding.id", "security_privacy_finding.project_id"],
            name="fk_security_privacy_finding_evidence_finding",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["evidence_id", "project_id"],
            ["evidence.id", "evidence.project_id"],
            name="fk_security_privacy_finding_evidence_evidence",
        ),
        UniqueConstraint("finding_id", "evidence_id", name="finding_evidence"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    finding_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    evidence_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()


# ---------------------------------------------------------------------------
# Immutability, enforced in the ORM (and again by the database's triggers)
# ---------------------------------------------------------------------------

_MAPPING_MOVES: dict[ComplianceMappingStatus, frozenset[ComplianceMappingStatus]] = {
    ComplianceMappingStatus.CANDIDATE: frozenset(),
    ComplianceMappingStatus.PENDING_REVIEW: frozenset(
        {ComplianceMappingStatus.APPROVED, ComplianceMappingStatus.REJECTED}
    ),
    ComplianceMappingStatus.APPROVED: frozenset(),
    ComplianceMappingStatus.REJECTED: frozenset(),
}
_FINDING_MOVES: dict[SecurityFindingStatus, frozenset[SecurityFindingStatus]] = {
    SecurityFindingStatus.PROPOSED: frozenset(),
    SecurityFindingStatus.PENDING_REVIEW: frozenset(
        {SecurityFindingStatus.APPROVED, SecurityFindingStatus.REJECTED}
    ),
    SecurityFindingStatus.APPROVED: frozenset(),
    SecurityFindingStatus.REJECTED: frozenset(),
}

_APPEND_ONLY = (ComplianceMappingEvidence, ComplianceGap, SecurityPrivacyFindingEvidence)


def _changed(instance: Base) -> set[str]:
    state = inspect(instance)
    return {a.key for a in state.mapper.column_attrs if state.attrs[a.key].history.deleted}


def _previous(instance: Base, attr: str) -> Any:
    deleted = inspect(instance).attrs[attr].history.deleted
    return next(iter(deleted)) if deleted else None


@event.listens_for(Session, "before_flush")
def _guard_compliance_immutability(session: Session, _context: object, _instances: object) -> None:
    """Refuse a flush that rewrites P6 analysis history.

    Registered on the ``Session`` class, so no session can skip it. A project
    deletion removes these rows through the database's cascade, not here.
    """
    for instance in session.deleted:
        if isinstance(instance, (*_APPEND_ONLY, ComplianceMapping, SecurityPrivacyFinding)):
            raise ImmutableRecordError(f"{type(instance).__name__} rows are never deleted")

    for instance in session.dirty:
        changed = _changed(instance)
        if not changed:
            continue
        if isinstance(instance, _APPEND_ONLY):
            raise ImmutableRecordError(f"{type(instance).__name__} rows are append-only")
        if isinstance(instance, (ComplianceMapping, SecurityPrivacyFinding)):
            if changed & instance.CONTENT_FIELDS:
                raise ImmutableRecordError(
                    f"{type(instance).__name__} content is immutable "
                    f"(attempted: {sorted(changed & instance.CONTENT_FIELDS)})"
                )
            if "approval_task_id" in changed and _previous(instance, "approval_task_id"):
                raise ImmutableRecordError("a gate task is linked once and never replaced")
            if "status" in changed:
                moves: dict[Any, frozenset[Any]] = (
                    _MAPPING_MOVES  # type: ignore[assignment]
                    if isinstance(instance, ComplianceMapping)
                    else _FINDING_MOVES
                )
                previous = type(instance.status)(_previous(instance, "status"))
                if type(instance.status)(instance.status) not in moves[previous]:
                    raise ImmutableRecordError(
                        f"a {type(instance).__name__} cannot move from {previous} to "
                        f"{instance.status}"
                    )
