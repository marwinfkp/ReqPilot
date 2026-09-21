"""Knowledge-base, allowlist, retrieval and evidence data access (architecture G.5, J).

The load-bearing function in this module is :func:`scoped_chunks`. Every query
that can *return* knowledge to a project - vector candidates, keyword
candidates, chunk details, and the eligibility check behind evidence - is built
on it, and it always carries:

* the **allowlist join** ``source_allowlist.project_id = :project`` (J.4:
  "a join, not a post-filter");
* the project's **jurisdiction** scope;
* the **effective date** as of the query date;
* **status or KB-version pin** - ``status = 'ACTIVE'``, or, for a pinned
  project, the items that were active in the pinned KB version (J.6).

The jurisdiction list and the pin are read from the project row by this layer,
never accepted from a caller, so nothing upstream - an API payload, or later a
model's output - can widen what a project may retrieve. Classification filters
may only *narrow* it further.

Evidence *resolution* deliberately does not use :func:`scoped_chunks`: evidence
is history. It must keep resolving after its source is superseded or removed
from the allowlist, so it is scoped by the evidence row's own ``project_id``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, String, and_, false, func, literal_column, or_, select, text
from sqlalchemy import type_coerce as coerce
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import ColumnClause

from reqpilot.domain.enums import Action, KnowledgeItemStatus, NormativeSourceType, ResourceType
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.identity import Project
from reqpilot.domain.models.knowledge import (
    Control,
    Evidence,
    KnowledgeChunk,
    KnowledgeItem,
    NormativeSource,
    SourceAllowlist,
)
from reqpilot.domain.models.runs import GraphRun
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.repositories.base import ProjectScopedRepository

#: Advisory-lock key serialising KB-version allocation on PostgreSQL, so two
#: concurrent curation actions cannot be given the same KB version.
_KB_VERSION_LOCK_KEY = 5_210_402_002

#: The text-search configuration. Written as a literal cast so the query's
#: expression is identical to the GIN index's, which is what lets it be used.
_TS_CONFIG: ColumnClause[Any] = literal_column("'english'::regconfig")


def _current_kb_version(session: Session, *, lock: bool = False) -> int:
    """The latest KB version: the highest version any curation action produced."""
    if lock and session.get_bind().dialect.name == "postgresql":
        session.execute(text(f"SELECT pg_advisory_xact_lock({_KB_VERSION_LOCK_KEY})"))
    created = session.scalar(select(func.max(KnowledgeItem.kb_version))) or 0
    retired = session.scalar(select(func.max(KnowledgeItem.superseded_in_kb_version))) or 0
    return max(created, retired)


# ---------------------------------------------------------------------------
# The shared corpus
# ---------------------------------------------------------------------------


class KnowledgeBaseRepository:
    """The curated corpus. Shared, so authorised by unscoped KB actions.

    Only a Knowledge-Base Administrator passes these checks. Every other role
    reaches knowledge exclusively through a project's retrieval scope.
    """

    def __init__(self, session: Session, actor: Actor) -> None:
        self._session = session
        self._actor = actor

    def authorize(self, action: Action) -> None:
        require(self._actor, action, ResourceRef(resource_type=ResourceType.KNOWLEDGE_ITEM))

    # -- reads (KB_READ) ------------------------------------------------------
    def current_kb_version(self, *, lock: bool = False) -> int:
        self.authorize(Action.KB_READ)
        return _current_kb_version(self._session, lock=lock)

    def get_source(self, source_id: uuid.UUID) -> NormativeSource | None:
        self.authorize(Action.KB_READ)
        return self._session.get(NormativeSource, source_id)

    def find_source(
        self, *, issuing_body: str, title: str, version: str, jurisdiction: str
    ) -> NormativeSource | None:
        self.authorize(Action.KB_READ)
        stmt = select(NormativeSource).where(
            NormativeSource.issuing_body == issuing_body,
            NormativeSource.title == title,
            NormativeSource.version == version,
            NormativeSource.jurisdiction == jurisdiction,
        )
        return self._session.scalars(stmt).first()

    def list_sources(self) -> list[NormativeSource]:
        self.authorize(Action.KB_READ)
        stmt = select(NormativeSource).order_by(NormativeSource.title, NormativeSource.version)
        return list(self._session.scalars(stmt))

    def get_control(self, control_id: uuid.UUID) -> Control | None:
        self.authorize(Action.KB_READ)
        return self._session.get(Control, control_id)

    def list_controls(self, source_id: uuid.UUID | None = None) -> list[Control]:
        self.authorize(Action.KB_READ)
        stmt = select(Control).order_by(Control.control_ref)
        if source_id is not None:
            stmt = stmt.where(Control.normative_source_id == source_id)
        return list(self._session.scalars(stmt))

    def get_item(self, item_id: uuid.UUID) -> KnowledgeItem | None:
        self.authorize(Action.KB_READ)
        return self._session.get(KnowledgeItem, item_id)

    def list_items(self, status: KnowledgeItemStatus | None = None) -> list[KnowledgeItem]:
        self.authorize(Action.KB_READ)
        stmt = select(KnowledgeItem).order_by(KnowledgeItem.item_key, KnowledgeItem.version_no)
        if status is not None:
            stmt = stmt.where(KnowledgeItem.status == status)
        return list(self._session.scalars(stmt))

    def versions_of(self, item_key: str) -> list[KnowledgeItem]:
        self.authorize(Action.KB_READ)
        stmt = (
            select(KnowledgeItem)
            .where(KnowledgeItem.item_key == item_key)
            .order_by(KnowledgeItem.version_no)
        )
        return list(self._session.scalars(stmt))

    def active_duplicate(self, source_id: uuid.UUID, content_hash: str) -> KnowledgeItem | None:
        self.authorize(Action.KB_READ)
        stmt = select(KnowledgeItem).where(
            KnowledgeItem.normative_source_id == source_id,
            KnowledgeItem.content_hash == content_hash,
            KnowledgeItem.status == KnowledgeItemStatus.ACTIVE,
        )
        return self._session.scalars(stmt).first()

    def chunks_for_item(self, item_id: uuid.UUID) -> list[KnowledgeChunk]:
        self.authorize(Action.KB_READ)
        stmt = (
            select(KnowledgeChunk)
            .where(KnowledgeChunk.knowledge_item_id == item_id)
            .order_by(KnowledgeChunk.ordinal)
        )
        return list(self._session.scalars(stmt))

    # -- writes (KB_ADMINISTER) -----------------------------------------------
    def add(self, entity: NormativeSource | Control | KnowledgeItem | KnowledgeChunk) -> None:
        """Persist a new corpus row. There is no update or delete method."""
        self.authorize(Action.KB_ADMINISTER)
        self._session.add(entity)
        self._session.flush()

    def add_all(self, entities: Sequence[KnowledgeChunk]) -> None:
        self.authorize(Action.KB_ADMINISTER)
        self._session.add_all(entities)
        self._session.flush()

    def mark_superseded(self, item: KnowledgeItem, **fields: Any) -> None:
        """Write the supersession columns - the only permitted change to an item."""
        self.authorize(Action.KB_ADMINISTER)
        unexpected = set(fields) - KnowledgeItem.SUPERSESSION_FIELDS
        if unexpected:
            raise ValueError(f"not supersession fields: {sorted(unexpected)}")
        for name, value in fields.items():
            setattr(item, name, value)
        self._session.flush()


# ---------------------------------------------------------------------------
# A project's knowledge scope
# ---------------------------------------------------------------------------


class KnowledgeScopeRepository(ProjectScopedRepository[SourceAllowlist]):
    """A project's allowlist, jurisdiction scope and KB-version pin."""

    resource_type = ResourceType.SOURCE_ALLOWLIST

    def project(self, project_id: ProjectId) -> Project | None:
        self.authorize(Action.KB_SCOPE_READ, project_id)
        return self._session.get(Project, project_id)

    def allowlisted_sources(self, project_id: ProjectId) -> list[NormativeSource]:
        self.authorize(Action.KB_SCOPE_READ, project_id)
        stmt = (
            select(NormativeSource)
            .join(SourceAllowlist, SourceAllowlist.normative_source_id == NormativeSource.id)
            .where(SourceAllowlist.project_id == project_id)
            .order_by(NormativeSource.title, NormativeSource.version)
        )
        return list(self._session.scalars(stmt))

    def entry(self, project_id: ProjectId, source_id: uuid.UUID) -> SourceAllowlist | None:
        self.authorize(Action.KB_SCOPE_READ, project_id)
        stmt = self.scoped(
            select(SourceAllowlist).where(SourceAllowlist.normative_source_id == source_id),
            SourceAllowlist.project_id,
            project_id,
        )
        return self._session.scalars(stmt).first()

    def source_exists(self, project_id: ProjectId, source_id: uuid.UUID) -> bool:
        """Whether a source exists - answered without returning any of its content."""
        self.authorize(Action.KB_SCOPE_MANAGE, project_id)
        stmt = select(NormativeSource.id).where(NormativeSource.id == source_id)
        return self._session.scalar(stmt) is not None

    def add(self, entry: SourceAllowlist) -> None:
        self.authorize(Action.KB_SCOPE_MANAGE, ProjectId(entry.project_id))
        self._session.add(entry)
        self._session.flush()

    def remove(self, project_id: ProjectId, entry: SourceAllowlist) -> None:
        self.authorize(Action.KB_SCOPE_MANAGE, project_id)
        if entry.project_id != project_id:
            raise ValueError("allowlist entry belongs to another project")
        self._session.delete(entry)
        self._session.flush()

    def set_scope(
        self, project_id: ProjectId, *, jurisdictions: list[str], kb_version_pin: int | None
    ) -> Project:
        self.authorize(Action.KB_SCOPE_MANAGE, project_id)
        project = self._session.get(Project, project_id)
        if project is None:
            raise ValueError("project not found")
        project.jurisdiction_scope = list(jurisdictions)
        project.kb_version_pin = kb_version_pin
        self._session.flush()
        return project

    def current_kb_version(self, project_id: ProjectId) -> int:
        self.authorize(Action.KB_SCOPE_READ, project_id)
        return _current_kb_version(self._session)


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetrievalScope:
    """What one project may retrieve, read from the database, never from a caller."""

    project_id: ProjectId
    jurisdictions: tuple[str, ...]
    kb_version_pin: int | None
    #: The KB version the retrieval sees: the pin, or the current version.
    kb_version: int
    as_of: dt.date
    allowlist_size: int


def scoped_chunks(
    stmt: Select,
    scope: RetrievalScope,
    *,
    source_types: frozenset[NormativeSourceType] = frozenset(),
    applicability: frozenset[str] = frozenset(),
    embedding_model: str | None = None,
) -> Select:
    """Restrict a chunk query to exactly what ``scope`` permits (architecture J.4).

    The allowlist is an inner join on ``(source, project)``: a chunk whose source
    is not allowlisted for this project produces no row, whatever the rest of
    the query asks for. Every restriction is a SQL predicate in the same
    statement, so ``LIMIT`` applies only after all of them.
    """
    stmt = (
        stmt.select_from(KnowledgeChunk)
        .join(KnowledgeItem, KnowledgeItem.id == KnowledgeChunk.knowledge_item_id)
        .join(NormativeSource, NormativeSource.id == KnowledgeItem.normative_source_id)
        .join(
            SourceAllowlist,
            and_(
                SourceAllowlist.normative_source_id == NormativeSource.id,
                SourceAllowlist.project_id == scope.project_id,
            ),
        )
        .where(
            NormativeSource.jurisdiction.in_(scope.jurisdictions)
            if scope.jurisdictions
            else false()
        )
        .where(
            or_(
                NormativeSource.effective_date.is_(None),
                NormativeSource.effective_date <= scope.as_of,
            )
        )
    )
    if scope.kb_version_pin is None:
        stmt = stmt.where(KnowledgeItem.status == KnowledgeItemStatus.ACTIVE)
    else:
        stmt = stmt.where(
            KnowledgeItem.kb_version <= scope.kb_version_pin,
            or_(
                KnowledgeItem.superseded_in_kb_version.is_(None),
                KnowledgeItem.superseded_in_kb_version > scope.kb_version_pin,
            ),
        )
    if embedding_model is not None:
        stmt = stmt.where(KnowledgeChunk.embedding_model == embedding_model)
    if source_types:
        stmt = stmt.where(NormativeSource.source_type.in_(sorted(source_types)))
    if applicability:
        tags = coerce(KnowledgeItem.applicability, postgresql.ARRAY(String))
        stmt = stmt.where(
            or_(
                func.cardinality(tags) == 0,
                tags.overlap(postgresql.array(sorted(applicability), type_=String)),
            )
        )
    return stmt


class RetrievalRepository(ProjectScopedRepository[KnowledgeChunk]):
    """Hybrid-search candidate queries. Every one is built on :func:`scoped_chunks`."""

    resource_type = ResourceType.KNOWLEDGE_CHUNK

    def load_scope(self, project_id: ProjectId, as_of: dt.date) -> RetrievalScope | None:
        """Read the project's retrieval scope from the database."""
        self.authorize(Action.KB_RETRIEVE, project_id)
        project = self._session.get(Project, project_id)
        if project is None:
            return None
        allowlist_size = self._session.scalar(
            select(func.count())
            .select_from(SourceAllowlist)
            .where(SourceAllowlist.project_id == project_id)
        )
        pin = project.kb_version_pin
        return RetrievalScope(
            project_id=project_id,
            jurisdictions=tuple(project.jurisdiction_scope or ()),
            kb_version_pin=pin,
            kb_version=pin if pin is not None else _current_kb_version(self._session),
            as_of=as_of,
            allowlist_size=int(allowlist_size or 0),
        )

    def _require_postgres(self) -> None:
        if self._session.get_bind().dialect.name != "postgresql":
            raise RuntimeError(
                "hybrid retrieval needs PostgreSQL with pgvector (architecture ADR-003/ADR-004)"
            )

    def vector_candidates(
        self,
        scope: RetrievalScope,
        query_vector: Sequence[float],
        *,
        embedding_model: str,
        min_similarity: float,
        source_types: frozenset[NormativeSourceType],
        applicability: frozenset[str],
        limit: int,
    ) -> list[tuple[uuid.UUID, float]]:
        """Nearest chunks by cosine distance, at or above the relevance threshold."""
        self.authorize(Action.KB_RETRIEVE, scope.project_id)
        self._require_postgres()
        # A recall knob, not a control: a higher ef_search keeps the HNSW scan
        # from returning fewer candidates than asked when the filters are tight.
        self._session.execute(text(f"SET LOCAL hnsw.ef_search = {max(100, 4 * int(limit))}"))
        distance = KnowledgeChunk.embedding.cosine_distance(list(query_vector))
        stmt = (
            scoped_chunks(
                select(KnowledgeChunk.id, (1 - distance).label("similarity")),
                scope,
                source_types=source_types,
                applicability=applicability,
                embedding_model=embedding_model,
            )
            .where(distance <= 1 - min_similarity)
            .order_by(distance, KnowledgeChunk.id)
            .limit(limit)
        )
        return [(row.id, float(row.similarity)) for row in self._session.execute(stmt)]

    def keyword_candidates(
        self,
        scope: RetrievalScope,
        query_text: str,
        *,
        embedding_model: str,
        source_types: frozenset[NormativeSourceType],
        applicability: frozenset[str],
        limit: int,
    ) -> list[tuple[uuid.UUID, float]]:
        """Chunks matching every query term, ranked by ``ts_rank_cd``.

        Restricted to chunks embedded by the active model too, so both halves of
        the hybrid rank the same candidate population.
        """
        self.authorize(Action.KB_RETRIEVE, scope.project_id)
        self._require_postgres()
        document = func.to_tsvector(_TS_CONFIG, KnowledgeChunk.text)
        query = func.websearch_to_tsquery(_TS_CONFIG, query_text)
        rank = func.ts_rank_cd(document, query).label("score")
        stmt = (
            scoped_chunks(
                select(KnowledgeChunk.id, rank),
                scope,
                source_types=source_types,
                applicability=applicability,
                embedding_model=embedding_model,
            )
            .where(document.bool_op("@@")(query))
            .order_by(rank.desc(), KnowledgeChunk.id)
            .limit(limit)
        )
        return [(row.id, float(row.score)) for row in self._session.execute(stmt)]

    def chunk_details(
        self,
        scope: RetrievalScope,
        chunk_ids: Sequence[uuid.UUID],
        query_vector: Sequence[float],
        *,
        embedding_model: str,
    ) -> dict[uuid.UUID, tuple[KnowledgeChunk, KnowledgeItem, NormativeSource, float]]:
        """Full provenance for the fused chunks - still through the scoped join."""
        self.authorize(Action.KB_RETRIEVE, scope.project_id)
        self._require_postgres()
        if not chunk_ids:
            return {}
        similarity = (1 - KnowledgeChunk.embedding.cosine_distance(list(query_vector))).label(
            "similarity"
        )
        stmt = scoped_chunks(
            select(KnowledgeChunk, KnowledgeItem, NormativeSource, similarity),
            scope,
            embedding_model=embedding_model,
        ).where(KnowledgeChunk.id.in_(list(chunk_ids)))
        return {
            row[0].id: (row[0], row[1], row[2], float(row[3]))
            for row in self._session.execute(stmt)
        }


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


class EvidenceRepository(ProjectScopedRepository[Evidence]):
    """Evidence rows: appended once, read by project, never updated or deleted."""

    resource_type = ResourceType.EVIDENCE

    def load_scope(self, project_id: ProjectId, as_of: dt.date) -> RetrievalScope | None:
        """The same scope retrieval uses, read for evidence creation."""
        self.authorize(Action.EVIDENCE_CREATE, project_id)
        project = self._session.get(Project, project_id)
        if project is None:
            return None
        pin = project.kb_version_pin
        return RetrievalScope(
            project_id=project_id,
            jurisdictions=tuple(project.jurisdiction_scope or ()),
            kb_version_pin=pin,
            kb_version=pin if pin is not None else _current_kb_version(self._session),
            as_of=as_of,
            allowlist_size=0,
        )

    def eligible_chunks(
        self, scope: RetrievalScope, chunk_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[KnowledgeChunk, KnowledgeItem, NormativeSource]]:
        """Which of ``chunk_ids`` this project may be given as evidence right now.

        The same allowlist join and predicates as retrieval, so a chunk that
        retrieval could not have returned can never become evidence either.
        """
        self.authorize(Action.EVIDENCE_CREATE, scope.project_id)
        if not chunk_ids:
            return {}
        stmt = scoped_chunks(select(KnowledgeChunk, KnowledgeItem, NormativeSource), scope).where(
            KnowledgeChunk.id.in_(list(chunk_ids))
        )
        return {row[0].id: (row[0], row[1], row[2]) for row in self._session.execute(stmt)}

    def graph_run_in_project(self, project_id: ProjectId, run_id: uuid.UUID) -> bool:
        self.authorize(Action.EVIDENCE_CREATE, project_id)
        stmt = select(GraphRun.id).where(GraphRun.id == run_id, GraphRun.project_id == project_id)
        return self._session.scalar(stmt) is not None

    def append(self, project_id: ProjectId, rows: Sequence[Evidence]) -> None:
        self.authorize(Action.EVIDENCE_CREATE, project_id)
        if any(row.project_id != project_id for row in rows):
            raise ValueError("evidence rows must belong to the project they are recorded in")
        self._session.add_all(rows)
        self._session.flush()

    def resolve(
        self, project_id: ProjectId, evidence_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[Evidence, KnowledgeChunk, KnowledgeItem, NormativeSource]]:
        """Evidence with the chunk, item and source it names, scoped to the project.

        Not filtered by today's allowlist or status: historical evidence must
        keep resolving after the knowledge base moves on.
        """
        self.authorize(Action.EVIDENCE_READ, project_id)
        if not evidence_ids:
            return {}
        stmt = (
            select(Evidence, KnowledgeChunk, KnowledgeItem, NormativeSource)
            .join(KnowledgeChunk, KnowledgeChunk.id == Evidence.knowledge_chunk_id)
            .join(KnowledgeItem, KnowledgeItem.id == KnowledgeChunk.knowledge_item_id)
            .join(NormativeSource, NormativeSource.id == KnowledgeItem.normative_source_id)
            .where(Evidence.project_id == project_id, Evidence.id.in_(list(evidence_ids)))
        )
        return {row[0].id: (row[0], row[1], row[2], row[3]) for row in self._session.execute(stmt)}

    def get(self, project_id: ProjectId, evidence_id: uuid.UUID) -> Evidence | None:
        self.authorize(Action.EVIDENCE_READ, project_id)
        stmt = self.scoped(
            select(Evidence).where(Evidence.id == evidence_id), Evidence.project_id, project_id
        )
        return self._session.scalars(stmt).first()

    def list_for_project(
        self,
        project_id: ProjectId,
        *,
        retrieval_id: uuid.UUID | None = None,
        graph_run_id: uuid.UUID | None = None,
    ) -> list[Evidence]:
        self.authorize(Action.EVIDENCE_READ, project_id)
        stmt = self.scoped(select(Evidence), Evidence.project_id, project_id)
        if retrieval_id is not None:
            stmt = stmt.where(Evidence.retrieval_id == retrieval_id)
        if graph_run_id is not None:
            stmt = stmt.where(Evidence.graph_run_id == graph_run_id)
        stmt = stmt.order_by(Evidence.created_at, Evidence.retrieval_id, Evidence.rank)
        return list(self._session.scalars(stmt))
