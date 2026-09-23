"""The risk register's tables (roadmap phase P7; architecture G.6, I.3-I.5).

* ``risk_matrix`` - the versioned 3x3 severity matrix as **data** (``DQ-03``;
  architecture I.3: "stored as a versioned data table ... not as code"). Nine
  rows per matrix version, seeded from ``rules/data/risk_rules.yaml``.
* ``risk`` - one risk item. Either a **requirement-level** risk naming the exact
  requirement version it was analysed from, or a **project-level** risk arising
  from the requirement set as a whole and naming no version (``FR-RSK-001``).
  It carries the category, the two ordinal ratings with their written
  rationales, the **computed** severity, the matrix version, the owner role, the
  status and the evidence it is grounded in.
* ``risk_evidence`` - which evidence rows a risk cites (``FR-RSK-006``).
  Composite foreign keys pin both sides to the risk's project: evidence from
  another project cannot be linked, whatever wrote the row.
* ``risk_mitigation`` - mitigation considerations, always stored as suggestions
  requiring human validation until a human accepts one (``FR-RSK-005``).

**How the severity is made unforgeable.** ``risk`` does not merely store a
severity next to two ratings; it carries ``(matrix_version, likelihood, impact,
severity)`` as a composite **foreign key into ``risk_matrix``**. A row whose
severity is not the matrix's value for its own cell does not exist, at the
database, whatever wrote it - a direct ``INSERT``, a future service, or a bug.
Architecture G.6 asks for "a DB CHECK against ``risk_matrix``"; a SQL ``CHECK``
cannot reference another table, so the constraint is expressed as the foreign
key that *can*, which is strictly stronger than a check against a copied value.

Two further database checks complete ``FR-RSK-007``: a ``HIGH`` risk can never
sit in ``PROPOSED`` (it is ``UNDER_REVIEW`` with a blocking G8 task from the
moment it is written), and a risk is either requirement-scoped with a version or
project-scoped without one - never both and never neither.

Content is immutable. A later requirement version gets its own analysis and
nothing transfers; only the status moves, and only along the approved path.
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
    FindingDetector,
    MitigationStatus,
    RiskCategory,
    RiskImpact,
    RiskLikelihood,
    RiskScope,
    RiskSeverity,
    RiskStatus,
    Role,
)
from reqpilot.domain.errors import ImmutableRecordError
from reqpilot.domain.models.base import Base, created_at_column, utc_now, uuid_pk

JsonType = JSON().with_variant(postgresql.JSONB(), "postgresql")


def _updated_at_column() -> Any:
    return mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


#: ``FR-RSK-007`` at the database: a HIGH risk is never merely PROPOSED. It is
#: written straight into UNDER_REVIEW, which is what the baseline guard counts.
HIGH_RISK_GATED_SQL = "severity <> 'HIGH' OR status <> 'PROPOSED'"

#: ``FR-RSK-001``: requirement-scoped means exactly one version; project-scoped
#: means none. A project risk is not forced into a fake requirement link.
SCOPE_VERSION_SQL = (
    "(scope = 'REQUIREMENT' AND requirement_version_id IS NOT NULL) OR "
    "(scope = 'PROJECT' AND requirement_version_id IS NULL)"
)


class RiskMatrixCell(Base):
    """One cell of one version of the approved severity matrix (I.3, ``DQ-03``).

    Seeded from the versioned ruleset - by the migration for a real database and
    by an ``after_create`` hook for a freshly created schema - so the foreign key
    that pins ``risk.severity`` always has its target.
    """

    __tablename__ = "risk_matrix"
    __table_args__ = (
        # The target of risk's composite foreign key: a (version, cell, severity)
        # tuple exists only if it is this matrix's own value for that cell.
        UniqueConstraint(
            "matrix_version", "likelihood", "impact", "severity", name="matrix_cell_severity"
        ),
    )

    matrix_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    likelihood: Mapped[RiskLikelihood] = mapped_column(
        SAEnum(RiskLikelihood, name="risk_likelihood_enum"), primary_key=True
    )
    impact: Mapped[RiskImpact] = mapped_column(
        SAEnum(RiskImpact, name="risk_impact_enum"), primary_key=True
    )
    severity: Mapped[RiskSeverity] = mapped_column(
        SAEnum(RiskSeverity, name="risk_severity_enum"), nullable=False
    )


class Risk(Base):
    """One risk item in the register (G.6 ``risk``; ``FR-RSK-001``..``008``)."""

    __tablename__ = "risk"
    __table_args__ = (
        UniqueConstraint("id", "project_id", name="id_project"),
        # The severity is the matrix's, or the row does not exist.
        ForeignKeyConstraint(
            ["matrix_version", "likelihood", "impact", "severity"],
            [
                "risk_matrix.matrix_version",
                "risk_matrix.likelihood",
                "risk_matrix.impact",
                "risk_matrix.severity",
            ],
            name="fk_risk_severity_from_matrix",
        ),
        ForeignKeyConstraint(
            ["requirement_version_id", "project_id"],
            ["requirement_version.id", "requirement_version.project_id"],
            name="fk_risk_version_same_project",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["approval_task_id", "project_id"],
            ["approval_task.id", "approval_task.project_id"],
            name="fk_risk_task_same_project",
        ),
        CheckConstraint("evidence_count >= 1", name="cites_evidence"),
        CheckConstraint("length(title) >= 1", name="title_not_empty"),
        CheckConstraint("length(description) >= 1", name="description_not_empty"),
        CheckConstraint("length(likelihood_rationale) >= 1", name="likelihood_rationale_present"),
        CheckConstraint("length(impact_rationale) >= 1", name="impact_rationale_present"),
        CheckConstraint(HIGH_RISK_GATED_SQL, name="high_risk_is_gated"),
        CheckConstraint(SCOPE_VERSION_SQL, name="scope_matches_version"),
        # One live risk per version and title; a rejected one does not block a
        # later re-analysis from recording the same risk again.
        Index(
            "uq_risk_active_version_title",
            "requirement_version_id",
            "title_key",
            unique=True,
            postgresql_where=text("status <> 'REJECTED' AND scope = 'REQUIREMENT'"),
            sqlite_where=text("status <> 'REJECTED' AND scope = 'REQUIREMENT'"),
        ),
        Index(
            "uq_risk_active_project_title",
            "project_id",
            "title_key",
            unique=True,
            postgresql_where=text("status <> 'REJECTED' AND scope = 'PROJECT'"),
            sqlite_where=text("status <> 'REJECTED' AND scope = 'PROJECT'"),
        ),
        Index("ix_risk_project_status", "project_id", "status"),
        Index("ix_risk_project_severity", "project_id", "severity"),
    )

    #: Written once, when the engine records the risk. The status, the owner's
    #: decision fields and the gate link are the only mutable columns.
    CONTENT_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "id",
            "project_id",
            "scope",
            "requirement_version_id",
            "graph_run_id",
            "agent_run_id",
            "category",
            "title",
            "title_key",
            "description",
            "likelihood",
            "impact",
            "severity",
            "matrix_version",
            "likelihood_rationale",
            "impact_rationale",
            "citations",
            "evidence_count",
            "detected_by",
            "source_signal_kind",
            "source_signal_id",
            "scope_rules_version",
            "rules_version",
            "content_hash",
            "recorded_by",
            "created_at",
        }
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    scope: Mapped[RiskScope] = mapped_column(
        SAEnum(RiskScope, name="risk_scope_enum"), nullable=False
    )
    #: The exact requirement version, for a requirement-level risk; ``None`` for
    #: a project-level one (``FR-RSK-001``). Never the mutable requirement.
    requirement_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, nullable=True, index=True
    )
    graph_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("graph_run.id", ondelete="SET NULL"), nullable=True, index=True
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agent_run.id", ondelete="SET NULL"), nullable=True
    )
    # --- the risk ------------------------------------------------------------
    category: Mapped[RiskCategory] = mapped_column(
        SAEnum(RiskCategory, name="risk_category_enum"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    #: Normalised title, for the one-live-risk-per-subject index.
    title_key: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # --- the ratings and the computed severity (I.2, I.3) --------------------
    likelihood: Mapped[RiskLikelihood] = mapped_column(
        SAEnum(RiskLikelihood, name="risk_likelihood_enum", create_type=False), nullable=False
    )
    impact: Mapped[RiskImpact] = mapped_column(
        SAEnum(RiskImpact, name="risk_impact_enum", create_type=False), nullable=False
    )
    #: **Authoritative.** Written only by the rule engine, from the matrix, and
    #: pinned to the matrix row by the composite foreign key above.
    severity: Mapped[RiskSeverity] = mapped_column(
        SAEnum(RiskSeverity, name="risk_severity_enum", create_type=False), nullable=False
    )
    matrix_version: Mapped[str] = mapped_column(String(32), nullable=False)
    likelihood_rationale: Mapped[str] = mapped_column(Text, nullable=False)
    impact_rationale: Mapped[str] = mapped_column(Text, nullable=False)
    # --- provenance (FR-RSK-006) ---------------------------------------------
    #: Snapshot of every citation: evidence id, source title, type, issuing
    #: body, jurisdiction, version, span, KB version.
    citations: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detected_by: Mapped[FindingDetector] = mapped_column(
        SAEnum(FindingDetector, name="finding_detector_enum", create_type=False), nullable=False
    )
    #: Which prior-phase signal indicated this risk, if any: a P5 quality
    #: finding or conflict, a P6 gap, mapping or security/privacy finding.
    source_signal_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_signal_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    scope_rules_version: Mapped[str] = mapped_column(String(20), nullable=False)
    rules_version: Mapped[str] = mapped_column(String(100), nullable=False)
    #: sha256 over the governed content, the ratings, the severity and (for a
    #: requirement risk) the version's own hash: what a G8 decision is bound to.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    # --- review (mutable) ----------------------------------------------------
    status: Mapped[RiskStatus] = mapped_column(
        SAEnum(RiskStatus, name="risk_status_enum"), nullable=False
    )
    #: Who owns the register entry (I.4). A rule by category, not a model choice.
    #: It is not who decides G8 - that is the gate's own required role.
    owner_role: Mapped[Role] = mapped_column(
        SAEnum(Role, name="role_enum", create_type=False), nullable=False
    )
    approval_task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    #: The human's recorded rationale for accepting, mitigating, rejecting or
    #: closing the risk (``FR-RSK-010``).
    decision_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Heuristic review-prioritisation signal in [0, 1] - not a probability, and
    #: never consulted by severity or gating.
    review_signal: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column()
    updated_at: Mapped[dt.datetime] = _updated_at_column()


class RiskEvidence(Base):
    """One evidence row cited by one risk. Append-only; same project on both sides."""

    __tablename__ = "risk_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["risk_id", "project_id"],
            ["risk.id", "risk.project_id"],
            name="fk_risk_evidence_risk",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["evidence_id", "project_id"],
            ["evidence.id", "evidence.project_id"],
            name="fk_risk_evidence_evidence",
        ),
        UniqueConstraint("risk_id", "evidence_id", name="risk_evidence_link"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    risk_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    evidence_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()


class RiskMitigation(Base):
    """A mitigation consideration (G.6 ``risk_mitigation``; ``FR-RSK-005``).

    Always stored with ``is_ai_generated = true`` when a model proposed it, and
    always ``SUGGESTED`` until a human accepts it. The suggestion text itself is
    immutable; only the acceptance fields move.
    """

    __tablename__ = "risk_mitigation"
    __table_args__ = (
        ForeignKeyConstraint(
            ["risk_id", "project_id"],
            ["risk.id", "risk.project_id"],
            name="fk_risk_mitigation_risk",
            ondelete="CASCADE",
        ),
        CheckConstraint("length(suggestion) >= 1", name="suggestion_not_empty"),
        CheckConstraint(
            "status <> 'ACCEPTED' OR accepted_by IS NOT NULL", name="acceptance_names_a_human"
        ),
        Index("ix_risk_mitigation_risk", "risk_id", "status"),
    )

    CONTENT_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"id", "project_id", "risk_id", "suggestion", "is_ai_generated", "created_at"}
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    risk_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    suggestion: Mapped[str] = mapped_column(Text, nullable=False)
    #: True for anything a model proposed. Set by code, never read from output.
    is_ai_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[MitigationStatus] = mapped_column(
        SAEnum(MitigationStatus, name="mitigation_status_enum"), nullable=False
    )
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    accepted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column()


# ---------------------------------------------------------------------------
# Seeding the matrix, so the foreign key always has its target
# ---------------------------------------------------------------------------


@event.listens_for(RiskMatrixCell.__table__, "after_create")
def _seed_risk_matrix(target: Any, connection: Any, **_kw: Any) -> None:
    """Populate ``risk_matrix`` from the versioned ruleset as soon as it exists.

    A real database is seeded by the migration; this covers a schema built with
    ``create_all`` (the offline test suite), so the same constraint holds in
    both. Importing the ruleset here keeps one source of truth for the values.
    """
    from reqpilot.rules.risk import packaged_risk_rules

    matrix = packaged_risk_rules().matrix
    connection.execute(
        target.insert(),
        [
            {
                "matrix_version": matrix.version,
                "likelihood": likelihood.name,
                "impact": impact.name,
                "severity": severity.name,
            }
            for (likelihood, impact), severity in sorted(
                matrix.cells.items(), key=lambda item: (item[0][0].value, item[0][1].value)
            )
        ],
    )


# ---------------------------------------------------------------------------
# Immutability, enforced in the ORM (and again by the database's triggers)
# ---------------------------------------------------------------------------

#: The approved status path (architecture I.4). Only a human moves a risk out of
#: UNDER_REVIEW (``FR-RSK-010``), and nothing returns to PROPOSED.
_RISK_MOVES: dict[RiskStatus, frozenset[RiskStatus]] = {
    RiskStatus.PROPOSED: frozenset({RiskStatus.UNDER_REVIEW}),
    RiskStatus.UNDER_REVIEW: frozenset(
        {RiskStatus.ACCEPTED, RiskStatus.MITIGATED, RiskStatus.REJECTED}
    ),
    RiskStatus.ACCEPTED: frozenset({RiskStatus.MITIGATED, RiskStatus.CLOSED}),
    RiskStatus.MITIGATED: frozenset({RiskStatus.CLOSED}),
    RiskStatus.REJECTED: frozenset({RiskStatus.CLOSED}),
    RiskStatus.CLOSED: frozenset(),
}

_MITIGATION_MOVES: dict[MitigationStatus, frozenset[MitigationStatus]] = {
    MitigationStatus.SUGGESTED: frozenset({MitigationStatus.ACCEPTED, MitigationStatus.REJECTED}),
    MitigationStatus.ACCEPTED: frozenset(),
    MitigationStatus.REJECTED: frozenset(),
}

_APPEND_ONLY = (RiskEvidence,)


def _changed(instance: Base) -> set[str]:
    state = inspect(instance)
    return {a.key for a in state.mapper.column_attrs if state.attrs[a.key].history.deleted}


def _previous(instance: Base, attr: str) -> Any:
    deleted = inspect(instance).attrs[attr].history.deleted
    return next(iter(deleted)) if deleted else None


@event.listens_for(Session, "before_flush")
def _guard_risk_immutability(session: Session, _context: object, _instances: object) -> None:
    """Refuse a flush that rewrites P7 risk history or the approved matrix.

    Registered on the ``Session`` class, so no session can skip it. A project
    deletion removes these rows through the database's cascade, not here.
    """
    for instance in session.deleted:
        if isinstance(instance, (*_APPEND_ONLY, Risk, RiskMitigation, RiskMatrixCell)):
            raise ImmutableRecordError(f"{type(instance).__name__} rows are never deleted")

    for instance in session.dirty:
        changed = _changed(instance)
        if not changed:
            continue
        if isinstance(instance, _APPEND_ONLY):
            raise ImmutableRecordError(f"{type(instance).__name__} rows are append-only")
        if isinstance(instance, RiskMatrixCell):
            # A matrix version is fixed once written: re-rating history by
            # editing the matrix in place is exactly what versioning prevents.
            raise ImmutableRecordError(
                "a risk_matrix version is immutable; publish a new matrix_version instead"
            )
        if isinstance(instance, RiskMitigation):
            if changed & instance.CONTENT_FIELDS:
                raise ImmutableRecordError(
                    "a mitigation suggestion's text is immutable "
                    f"(attempted: {sorted(changed & instance.CONTENT_FIELDS)})"
                )
            if "status" in changed:
                previous = MitigationStatus(_previous(instance, "status"))
                if MitigationStatus(instance.status) not in _MITIGATION_MOVES[previous]:
                    raise ImmutableRecordError(
                        f"a mitigation cannot move from {previous} to {instance.status}"
                    )
            continue
        if isinstance(instance, Risk):
            if changed & instance.CONTENT_FIELDS:
                raise ImmutableRecordError(
                    "Risk content is immutable - including its ratings and its computed "
                    f"severity (attempted: {sorted(changed & instance.CONTENT_FIELDS)})"
                )
            if "approval_task_id" in changed and _previous(instance, "approval_task_id"):
                raise ImmutableRecordError("a gate task is linked once and never replaced")
            if "status" in changed:
                previous_status = RiskStatus(_previous(instance, "status"))
                if RiskStatus(instance.status) not in _RISK_MOVES[previous_status]:
                    raise ImmutableRecordError(
                        f"a Risk cannot move from {previous_status} to {instance.status}"
                    )
