"""Knowledge-base and evidence tables (architecture G.5, G.6, J).

Two corpora are kept apart on purpose (architecture J.1). This module holds the
**knowledge corpus** - curated normative material - plus the ``evidence`` rows
that record exactly which chunk was supplied to a later analysis step. The
project corpus (``source_document`` / ``source_chunk``) is not here: it belongs
with project-document ingestion, which must mask before it chunks (J.2).

Identity and versioning, kept as four distinct concepts:

* **Source version** - ``normative_source.version``: which edition of the
  external instrument this is. A new edition is a new source row.
* **Knowledge-item version** - ``(item_key, version_no)``: the curated item's
  own history. A new version supersedes its predecessor (``FR-RAG-006``).
* **KB version** - ``knowledge_item.kb_version``: a global counter that
  increments on every curation action. Projects pin it (J.6).
* **Chunk identity** - ``knowledge_chunk.id``: derived deterministically from
  the item, span and content (:func:`reqpilot.domain.ids.chunk_id_for`).

Mutability, enforced in the ORM here and again by database triggers:

* ``normative_source``, ``control``, ``knowledge_chunk``, ``evidence``: append-only.
* ``knowledge_item``: content is immutable; only the supersession columns may
  change, and only once, from ``active`` to ``superseded``.
* ``source_allowlist``: project configuration; rows are added and removed.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
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
    ChunkStrategy,
    KnowledgeItemStatus,
    LicenceClass,
    NormativeSourceType,
    SupersessionKind,
    TextOrigin,
)
from reqpilot.domain.errors import ImmutableRecordError
from reqpilot.domain.models.base import Base, created_at_column, uuid_pk
from reqpilot.domain.refs import EvidenceKind

#: Embedding dimension of the approved model, ``BAAI/bge-small-en-v1.5``
#: (architecture ADR-005). Fixed at index creation: changing the model requires a
#: re-embed migration (ADR-004), so this is a constant, not a setting.
EMBEDDING_DIMENSION = 384

#: A list of short strings. A real array on PostgreSQL - so that applicability
#: and jurisdiction filters are SQL predicates - and JSON elsewhere.
StringList = JSON().with_variant(postgresql.ARRAY(String(100)), "postgresql")


class NormativeSource(Base):
    """A normative source with its full provenance (G.5, ``FR-RAG-001``).

    Append-only. A corrected or amended source is a new row with its own
    ``version``, so evidence recorded against the old row keeps resolving to the
    metadata that was true when it was recorded.
    """

    __tablename__ = "normative_source"
    __table_args__ = (
        UniqueConstraint(
            "issuing_body", "title", "version", "jurisdiction", name="uq_normative_source_identity"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    source_type: Mapped[NormativeSourceType] = mapped_column(
        SAEnum(NormativeSourceType, name="normative_source_type_enum"), nullable=False
    )
    issuing_body: Mapped[str] = mapped_column(String(300), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    #: Uppercase jurisdiction code: ISO 3166 alpha-2 (``IN``), or ``INTL`` for the
    #: cross-cutting catalogues approved in Phase 0 D.2.
    jurisdiction: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    #: The edition of the external instrument (the *source version*).
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    effective_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    #: When a human consulted the source at curation time (approved Phase 0 D.2).
    #: The "retrieval date" of ``FR-RAG-001`` - nothing is fetched automatically.
    retrieved_at: Mapped[dt.date] = mapped_column(Date, nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    licence_class: Mapped[LicenceClass] = mapped_column(
        SAEnum(LicenceClass, name="licence_class_enum"), nullable=False
    )
    licence_note: Mapped[str] = mapped_column(String(2000), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"NormativeSource({self.title!r} {self.version}, {self.source_type})"


class Control(Base):
    """A control from a catalogue, stored as reference and paraphrase (G.5).

    ``paraphrase`` is team-written by definition: copyrighted catalogues such as
    ISO/IEC 27001 Annex A may contribute identifiers, never copied text (D.2).
    """

    __tablename__ = "control"
    __table_args__ = (
        UniqueConstraint("normative_source_id", "control_ref", name="uq_control_source_ref"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    normative_source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("normative_source.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    control_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    paraphrase: Mapped[str] = mapped_column(Text, nullable=False)
    applicability: Mapped[list[str]] = mapped_column(StringList, nullable=False, default=list)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column()


class KnowledgeItem(Base):
    """One curated, versioned knowledge item (G.5, J.6, ``FR-RAG-001``, ``FR-RAG-006``)."""

    __tablename__ = "knowledge_item"
    __table_args__ = (
        UniqueConstraint("item_key", "version_no", name="uq_knowledge_item_key_version"),
        CheckConstraint("version_no >= 1", name="version_no_positive"),
        CheckConstraint("kb_version >= 1", name="kb_version_positive"),
        CheckConstraint(
            "(status = 'ACTIVE' AND superseded_in_kb_version IS NULL "
            " AND supersession_kind IS NULL AND superseded_by_id IS NULL) OR "
            "(status = 'SUPERSEDED' AND superseded_in_kb_version IS NOT NULL "
            " AND supersession_kind IS NOT NULL)",
            name="supersession_coherent",
        ),
        # The one status "superseded" covers three operations; this keeps them
        # distinguishable in the data itself. A retirement has no successor; a new
        # version or a replacement always has one.
        CheckConstraint(
            "supersession_kind IS NULL"
            " OR (supersession_kind = 'RETIRED' AND superseded_by_id IS NULL)"
            " OR (supersession_kind IN ('VERSIONED', 'REPLACED')"
            " AND superseded_by_id IS NOT NULL)",
            name="supersession_kind_matches_successor",
        ),
        CheckConstraint(
            "superseded_in_kb_version IS NULL OR superseded_in_kb_version > kb_version",
            name="superseded_after_created",
        ),
        # Content-hash dedupe (J.2): the same text may not be active twice under
        # one source. Superseded rows are history and do not collide.
        Index(
            "uq_knowledge_item_active_content",
            "normative_source_id",
            "content_hash",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
            sqlite_where=text("status = 'ACTIVE'"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    #: Curator-assigned stable key, shared by every version of the item, e.g.
    #: ``SYN-ACME-AC-4.1``. What a probe question or a person refers to.
    item_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    normative_source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("normative_source.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    control_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("control.id", ondelete="RESTRICT"), nullable=True
    )
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: Where in the source this item sits, e.g. ``s.8(7)`` or ``A.5.15``.
    clause_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_origin: Mapped[TextOrigin] = mapped_column(
        SAEnum(TextOrigin, name="text_origin_enum"), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    applicability: Mapped[list[str]] = mapped_column(StringList, nullable=False, default=list)

    # --- versioning (J.6) -------------------------------------------------
    #: The KB version in which this item became active.
    kb_version: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[KnowledgeItemStatus] = mapped_column(
        SAEnum(KnowledgeItemStatus, name="knowledge_item_status_enum"),
        nullable=False,
        default=KnowledgeItemStatus.ACTIVE,
    )
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("knowledge_item.id", ondelete="RESTRICT"), nullable=True
    )
    supersession_kind: Mapped[SupersessionKind | None] = mapped_column(
        SAEnum(SupersessionKind, name="supersession_kind_enum"), nullable=True
    )
    #: The KB version in which this item stopped being active. Without it a
    #: project pinned to an earlier KB version could not see the item as it was,
    #: which is exactly what pinning exists to guarantee (J.6).
    superseded_in_kb_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    supersession_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    superseded_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column()

    #: Columns that may change after creation, and only in the one permitted
    #: transition ``active -> superseded``.
    SUPERSESSION_FIELDS: frozenset[str] = frozenset(
        {
            "status",
            "superseded_by_id",
            "supersession_kind",
            "superseded_in_kb_version",
            "supersession_reason",
            "superseded_at",
        }
    )

    def active_at(self, kb_version: int) -> bool:
        """Whether this item was active in ``kb_version`` (the J.6 pin semantics)."""
        return self.kb_version <= kb_version and (
            self.superseded_in_kb_version is None or self.superseded_in_kb_version > kb_version
        )

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"KnowledgeItem({self.item_key} v{self.version_no}, {self.status})"


class KnowledgeChunk(Base):
    """A retrievable span of a knowledge item, with its vector (G.5, J.3).

    ``text`` is always exactly ``item.text[char_start:char_end]``. That identity is
    what lets a citation resolve to the precise span a model was shown
    (``FR-RAG-003``), and it is checked again when a citation is resolved.
    """

    __tablename__ = "knowledge_chunk"
    __table_args__ = (
        UniqueConstraint("knowledge_item_id", "ordinal", name="uq_knowledge_chunk_item_ordinal"),
        CheckConstraint("char_start >= 0 AND char_end > char_start", name="span_ordered"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    knowledge_item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_item.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy: Mapped[ChunkStrategy] = mapped_column(
        SAEnum(ChunkStrategy, name="chunk_strategy_enum"), nullable=False
    )
    #: The clause or heading the chunk belongs to, where the source has one.
    structure_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIMENSION), nullable=False)
    #: The model that produced ``embedding``. Retrieval compares only vectors from
    #: the same model: mixing models would make distances meaningless.
    embedding_model: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[dt.datetime] = created_at_column()


class SourceAllowlist(Base):
    """A normative source a project may retrieve from (G.5, ``FR-RAG-002``).

    Used by retrieval as a **join, not a filter**: a chunk whose source has no row
    here for the project cannot be produced by the query at all.
    """

    __tablename__ = "source_allowlist"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "normative_source_id", name="uq_source_allowlist_project_source"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    normative_source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("normative_source.id", ondelete="RESTRICT"), nullable=False
    )
    added_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column()


class Evidence(Base):
    """A retrieved chunk as it was supplied for analysis (G.6, J.5, ``FR-RAG-003``).

    Append-only, and self-contained: ``quote`` is the exact text supplied, so
    history never depends on re-running retrieval against a knowledge base that
    has since changed. The chunk it came from is immutable and cannot be deleted
    while evidence references it.
    """

    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint("retrieval_id", "knowledge_chunk_id", name="uq_evidence_retrieval_chunk"),
        CheckConstraint("char_start >= 0 AND char_end > char_start", name="span_ordered"),
        CheckConstraint(
            "kind <> 'KNOWLEDGE_ITEM' OR knowledge_chunk_id IS NOT NULL",
            name="knowledge_evidence_names_chunk",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Architecture F.3 ``EvidenceRef.kind``. P2 records knowledge evidence only.
    kind: Mapped[EvidenceKind] = mapped_column(
        SAEnum(EvidenceKind, name="evidence_kind_enum"), nullable=False
    )
    #: For knowledge evidence, the knowledge item; the span is within its text.
    target_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    knowledge_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("knowledge_chunk.id", ondelete="RESTRICT"), nullable=True
    )
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    quote_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # --- the retrieval that produced it -----------------------------------
    retrieval_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    retrieval_score: Mapped[float] = mapped_column(Float, nullable=False)
    #: The KB version the retrieval saw (a project's pin, or the current version).
    kb_version: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(200), nullable=False)
    ruleset_version: Mapped[str] = mapped_column(String(50), nullable=False)
    #: sha256 of the query text. The query itself is not stored here.
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    graph_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("graph_run.id", ondelete="SET NULL"), nullable=True, index=True
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agent_run.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[dt.datetime] = created_at_column()


# ---------------------------------------------------------------------------
# ORM-level immutability (the database triggers are the second layer)
# ---------------------------------------------------------------------------

_APPEND_ONLY: tuple[type[Base], ...] = (NormativeSource, Control, KnowledgeChunk, Evidence)


def _changed_columns(instance: Base) -> set[str]:
    state = inspect(instance)
    return {attr.key for attr in state.mapper.column_attrs if state.attrs[attr.key].history.deleted}


@event.listens_for(Session, "before_flush")
def _guard_knowledge_immutability(session: Session, _context: object, _instances: object) -> None:
    """Refuse any flush that edits or deletes knowledge history.

    Registered on the ``Session`` class, so no session in the process can skip it.
    """
    for instance in session.deleted:
        if isinstance(instance, (*_APPEND_ONLY, KnowledgeItem)):
            raise ImmutableRecordError(
                f"{type(instance).__name__} rows are never deleted; "
                "supersede a knowledge item instead"
            )

    for instance in session.dirty:
        if isinstance(instance, _APPEND_ONLY):
            if _changed_columns(instance):
                raise ImmutableRecordError(
                    f"{type(instance).__name__} rows are append-only; record a new row instead"
                )
        elif isinstance(instance, KnowledgeItem):
            changed = _changed_columns(instance)
            content = changed - KnowledgeItem.SUPERSESSION_FIELDS
            if content:
                raise ImmutableRecordError(
                    f"knowledge item content is immutable (attempted: {sorted(content)}); "
                    "create a new version instead"
                )
            if changed:
                previous = inspect(instance).attrs["status"].history.deleted
                if not previous or previous[0] is not KnowledgeItemStatus.ACTIVE:
                    raise ImmutableRecordError(
                        "a knowledge item leaves 'active' once; a superseded item is history"
                    )
