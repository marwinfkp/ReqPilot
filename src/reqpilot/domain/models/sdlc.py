"""SDLC recommendation records (architecture G.8, L; roadmap phase P9).

Four tables, all project-scoped, all pinned to their project by composite
foreign keys:

* ``sdlc_run`` - one recommendation: the approved baseline it was derived from,
  the ruleset and weights versions (and the ruleset's content hash) that scored
  it, the authoritative ranking's hash, the LLM explanation and its consistency
  outcome, the G6 task group and, once all four approvers signed, the selection;
* ``sdlc_factor`` - the thirteen ``[PS §13]`` factors of one run: the effective
  score, the derived score, the model's proposal and what validation made of
  it, the evidence references, and - for a human override (``FR-SDL-003``) - the
  previous score, the reason, who, in which role and when;
* ``sdlc_candidate`` - each candidate's raw MCDA score, its 0-100 suitability,
  its rank and the rule effects on it (``FR-SDL-004``/``-005``);
* ``sdlc_rule_application`` - every triggered rule, its effect and the factor
  values that triggered it (architecture L.4).

**What is immutable.** Factors, candidates and rule applications are
append-only: a factor override never edits a run, it creates a *new* run that
supersedes it (architecture L.6: "overrides ... trigger a full recompute"), so a
recommendation that was explained or approved stays reproducible exactly as it
was. On ``sdlc_run`` only the lifecycle columns move - the status, the
explanation (written once), the recommendation hash and the G6 task group
(written once), and the selection (written once); the inputs, versions, hashes
and ranking never change. The ORM guard below and PostgreSQL triggers installed
by migration 0011 both enforce this.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
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
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.types import Uuid

from reqpilot.domain.enums import ExplanationStatus, SdlcRunStatus
from reqpilot.domain.errors import ImmutableRecordError
from reqpilot.domain.models.audit import JsonType
from reqpilot.domain.models.base import Base, created_at_column, utc_now, uuid_pk


class SdlcRun(Base):
    """One SDLC recommendation for one approved baseline."""

    __tablename__ = "sdlc_run"
    __table_args__ = (
        UniqueConstraint("id", "project_id", name="id_project"),
        ForeignKeyConstraint(
            ["baseline_id", "project_id"],
            ["baseline.id", "baseline.project_id"],
            name="fk_sdlc_run_baseline",
        ),
        ForeignKeyConstraint(
            ["supersedes_run_id", "project_id"],
            ["sdlc_run.id", "sdlc_run.project_id"],
            name="fk_sdlc_run_supersedes",
        ),
        CheckConstraint("length(profile_hash) = 64", name="profile_hash_present"),
        CheckConstraint("length(ranking_hash) = 64", name="ranking_hash_present"),
        CheckConstraint(
            "(status <> 'SELECTED') OR "
            "(selected_candidate IS NOT NULL AND selected_at IS NOT NULL)",
            name="selection_recorded",
        ),
        Index("ix_sdlc_run_project", "project_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    baseline_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    #: The analysis-graph run that produced it (C.5 ``sdlc_graph``).
    graph_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("graph_run.id", ondelete="SET NULL"), nullable=True
    )
    #: The run this one replaces, when it was created by a factor override.
    supersedes_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    status: Mapped[SdlcRunStatus] = mapped_column(
        SAEnum(SdlcRunStatus, name="sdlc_run_status_enum"), nullable=False
    )
    #: ``sdlc_rules@<version>#<sha12>`` and the full content sha256 of the file.
    ruleset_version: Mapped[str] = mapped_column(String(40), nullable=False)
    ruleset_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    rules_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    weights_version: Mapped[str] = mapped_column(String(40), nullable=False)
    #: sha256 of the approved facts the factors were derived from (counts + refs).
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    #: sha256 of the effective factor profile (13 factor ids and scores).
    profile_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: sha256 of the authoritative ranking (keys, scores, ranks, rule effects).
    ranking_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    top_candidate: Mapped[str] = mapped_column(String(60), nullable=False)
    runner_up_candidate: Mapped[str | None] = mapped_column(String(60), nullable=True)
    #: FR-SDL-007: the single-factor changes that would put the runner-up first.
    reversal_conditions: Mapped[list[dict[str, Any]]] = mapped_column(
        JsonType, nullable=False, default=list
    )
    #: Counts behind the profile (no requirement text), for the reader.
    facts_summary: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)

    # -- the explanation (architecture L.5; written once) ------------------
    explanation_status: Mapped[ExplanationStatus] = mapped_column(
        SAEnum(ExplanationStatus, name="sdlc_explanation_status_enum"), nullable=False
    )
    explanation_narrative: Mapped[str | None] = mapped_column(Text, nullable=True)
    explanation_counter_arguments: Mapped[list[dict[str, Any]]] = mapped_column(
        JsonType, nullable=False, default=list
    )
    #: Non-authoritative: what the model *believed* was first and scored.
    asserted_top_candidate: Mapped[str | None] = mapped_column(String(60), nullable=True)
    asserted_scores: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    explanation_discrepancies: Mapped[list[dict[str, Any]]] = mapped_column(
        JsonType, nullable=False, default=list
    )
    explanation_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    explanation_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    explanation_prompt: Mapped[str | None] = mapped_column(String(100), nullable=True)
    explanation_agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    explained_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # -- G6 and the final selection ----------------------------------------
    #: sha256 over the ranking and the explanation - what every G6 task binds to.
    recommendation_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    g6_task_group_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    selected_candidate: Mapped[str | None] = mapped_column(String(60), nullable=True)
    selected_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"SdlcRun(id={self.id}, status={self.status}, top={self.top_candidate})"


class SdlcFactor(Base):
    """One of the thirteen factors of one run. Append-only."""

    __tablename__ = "sdlc_factor"
    __table_args__ = (
        UniqueConstraint("sdlc_run_id", "factor_id", name="run_factor"),
        UniqueConstraint("id", "project_id", name="id_project"),
        ForeignKeyConstraint(
            ["sdlc_run_id", "project_id"],
            ["sdlc_run.id", "sdlc_run.project_id"],
            name="fk_sdlc_factor_run",
            ondelete="CASCADE",
        ),
        CheckConstraint("score BETWEEN 1 AND 5", name="score_on_scale"),
        CheckConstraint("derived_score BETWEEN 1 AND 5", name="derived_score_on_scale"),
        CheckConstraint(
            "proposed_score IS NULL OR proposed_score BETWEEN 1 AND 5", name="proposal_on_scale"
        ),
        CheckConstraint(
            "(NOT is_overridden) OR (override_reason IS NOT NULL AND overridden_by IS NOT NULL "
            "AND override_role IS NOT NULL AND overridden_at IS NOT NULL "
            "AND previous_score IS NOT NULL)",
            name="override_recorded",
        ),
        Index("ix_sdlc_factor_run", "sdlc_run_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    sdlc_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    factor_id: Mapped[str] = mapped_column(String(60), nullable=False)
    #: The effective score the MCDA used.
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    #: ``derived`` | ``risk_aggregate`` | ``model_proposal`` | ``human_override``.
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    derived_score: Mapped[int] = mapped_column(Integer, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    #: ``supported`` | ``absent_in_scope`` | ``not_recorded``.
    evidence_state: Mapped[str] = mapped_column(String(30), nullable=False)
    basis: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    # -- role #10's proposal, as validated -------------------------------
    proposal_status: Mapped[str] = mapped_column(String(20), nullable=False)
    proposed_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proposal_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposal_evidence_refs: Mapped[list[str]] = mapped_column(
        JsonType, nullable=False, default=list
    )
    proposal_rejection_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # -- FR-SDL-003: a human override ------------------------------------
    is_overridden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    previous_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    overridden_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    override_role: Mapped[str | None] = mapped_column(String(40), nullable=True)
    overridden_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[dt.datetime] = created_at_column()


class SdlcCandidate(Base):
    """One candidate's MCDA result and rank in one run. Append-only."""

    __tablename__ = "sdlc_candidate"
    __table_args__ = (
        UniqueConstraint("sdlc_run_id", "candidate_key", name="run_candidate"),
        UniqueConstraint("sdlc_run_id", "rank", name="run_rank"),
        UniqueConstraint("id", "project_id", name="id_project"),
        ForeignKeyConstraint(
            ["sdlc_run_id", "project_id"],
            ["sdlc_run.id", "sdlc_run.project_id"],
            name="fk_sdlc_candidate_run",
            ondelete="CASCADE",
        ),
        CheckConstraint("rank >= 1", name="rank_positive"),
        CheckConstraint("normalised_score BETWEEN 0 AND 100", name="score_bounded"),
        Index("ix_sdlc_candidate_run", "sdlc_run_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    sdlc_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_key: Mapped[str] = mapped_column(String(60), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    raw_score: Mapped[float] = mapped_column(Float, nullable=False)
    max_raw: Mapped[float] = mapped_column(Float, nullable=False)
    #: The MCDA suitability before any boost (0-100).
    mcda_score: Mapped[float] = mapped_column(Float, nullable=False)
    #: The suitability after the rule pass (0-100) - the published score.
    normalised_score: Mapped[float] = mapped_column(Float, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    vetoed_by: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    boosted_by: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    required_by: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    #: w[f] * S[c][f] * norm(score[f]) per factor - the arithmetic, shown.
    contributions: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = created_at_column()


class SdlcRuleApplication(Base):
    """One triggered declarative rule and its effect (architecture L.4). Append-only."""

    __tablename__ = "sdlc_rule_application"
    __table_args__ = (
        ForeignKeyConstraint(
            ["sdlc_run_id", "project_id"],
            ["sdlc_run.id", "sdlc_run.project_id"],
            name="fk_sdlc_rule_application_run",
            ondelete="CASCADE",
        ),
        Index("ix_sdlc_rule_application_run", "sdlc_run_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    sdlc_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_id: Mapped[str] = mapped_column(String(80), nullable=False)
    effect: Mapped[str] = mapped_column(String(20), nullable=False)
    affected_candidate: Mapped[str | None] = mapped_column(String(60), nullable=True)
    changed_ranking: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_values: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = created_at_column()


#: ``sdlc_run`` columns that may move after insertion, and nothing else.
RUN_MUTABLE_COLUMNS: frozenset[str] = frozenset(
    {
        "status",
        "explanation_status",
        "explanation_narrative",
        "explanation_counter_arguments",
        "asserted_top_candidate",
        "asserted_scores",
        "explanation_discrepancies",
        "explanation_attempts",
        "explanation_model",
        "explanation_prompt",
        "explanation_agent_run_id",
        "explained_at",
        "recommendation_hash",
        "g6_task_group_id",
        "selected_candidate",
        "selected_at",
        "updated_at",
    }
)

#: Written once: a set value is never replaced.
RUN_WRITE_ONCE_COLUMNS: frozenset[str] = frozenset(
    {
        "explanation_narrative",
        "recommendation_hash",
        "g6_task_group_id",
        "selected_candidate",
        "selected_at",
        "explained_at",
    }
)


@event.listens_for(Session, "before_flush")
def _guard_sdlc(session: Session, _context: object, _instances: object) -> None:
    """Factors, candidates and rule applications are append-only; a run moves its lifecycle only."""
    append_only = (SdlcFactor, SdlcCandidate, SdlcRuleApplication)
    for instance in session.deleted:
        if isinstance(instance, (*append_only, SdlcRun)):
            raise ImmutableRecordError(
                f"{type(instance).__name__} rows are never deleted; a recommendation's history "
                "is preserved"
            )
    for instance in session.dirty:
        if not isinstance(instance, (*append_only, SdlcRun)):
            continue
        state: Any = inspect(instance)
        changed = {a.key for a in state.mapper.column_attrs if state.attrs[a.key].history.deleted}
        if isinstance(instance, SdlcRun):
            illegal = changed - RUN_MUTABLE_COLUMNS
            rewritten = {
                key
                for key in changed & RUN_WRITE_ONCE_COLUMNS
                if any(v is not None for v in state.attrs[key].history.deleted)
            }
            if illegal or rewritten:
                raise ImmutableRecordError(
                    "an SDLC run's inputs, versions and ranking are immutable, and its "
                    f"explanation and selection are written once (attempted: "
                    f"{sorted(illegal | rewritten)}); override a factor to create a new run"
                )
        elif changed:
            raise ImmutableRecordError(
                f"{type(instance).__name__} is append-only (attempted: {sorted(changed)}); "
                "a factor override creates a new run"
            )
