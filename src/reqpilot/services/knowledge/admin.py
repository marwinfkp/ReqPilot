"""Knowledge-base administration: add, version, retire (``FR-RAG-006``; architecture J.6).

Every mutation here is authorised by ``policy.can`` through the repository,
audited through the one append-only :class:`AuditService`, and written in the
caller's transaction, so an audited curation action always happened and an
unaudited one never did (architecture T.1).

The curation rules this service enforces deterministically:

* **Taxonomy** - every source has exactly one C.1 type.
* **Licence** - text is refused where the source's licence forbids it: a
  paraphrase-only source (a copyrighted standard) never stores a verbatim
  extract, and synthetic text is only ever filed under a synthetic source
  (approved Phase 0 D.2; architecture G.5).
* **No fake law** - a synthetic source may only be an organisational policy or a
  best-practice note. A synthetic statute or regulatory direction is refused.
* **Versioning** - content never changes in place. A new version supersedes its
  predecessor; retiring leaves no successor; every action allocates the next
  KB version, so a pinned project keeps seeing exactly what it saw (J.6).
* **Dedupe** - the same text is never active twice under one source (J.2).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    LICENCE_PERMITTED_ORIGINS,
    SYNTHETIC_PERMITTED_TYPES,
    AuditEventType,
    KnowledgeItemStatus,
    LicenceClass,
    NormativeSourceType,
    SupersessionKind,
    TextOrigin,
)
from reqpilot.domain.errors import KnowledgeBaseError, LicenceViolationError
from reqpilot.domain.ids import chunk_id_for
from reqpilot.domain.models.base import utc_now
from reqpilot.domain.models.knowledge import (
    EMBEDDING_DIMENSION,
    Control,
    KnowledgeChunk,
    KnowledgeItem,
    NormativeSource,
)
from reqpilot.domain.policy import Actor
from reqpilot.repositories.knowledge import KnowledgeBaseRepository
from reqpilot.retrieval.chunking import chunk_knowledge_item
from reqpilot.retrieval.embeddings import EmbeddingProvider
from reqpilot.retrieval.extraction import normalise_text
from reqpilot.retrieval.rules import RetrievalRules
from reqpilot.services.audit import AuditService

JURISDICTION_PATTERN = re.compile(r"^(?:[A-Z]{2}|INTL)$")
ITEM_KEY_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._:-]{2,99}$")
TAG_PATTERN = re.compile(r"^[a-z0-9_]{1,100}$")


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalise_jurisdiction(code: str) -> str:
    """Upper-case and validate a jurisdiction code: ISO 3166 alpha-2, or ``INTL``."""
    value = code.strip().upper()
    if not JURISDICTION_PATTERN.match(value):
        raise KnowledgeBaseError(
            f"jurisdiction {code!r} must be an ISO 3166 alpha-2 code (e.g. IN) or INTL"
        )
    return value


def normalise_tags(tags: Sequence[str]) -> list[str]:
    cleaned = sorted({t.strip().lower() for t in tags if t.strip()})
    for tag in cleaned:
        if not TAG_PATTERN.match(tag):
            raise KnowledgeBaseError(f"applicability tag {tag!r} must match [a-z0-9_]+")
    return cleaned


@dataclass(frozen=True)
class SourceSpec:
    """A normative source's provenance (``FR-RAG-001``)."""

    source_type: NormativeSourceType
    issuing_body: str
    title: str
    jurisdiction: str
    version: str
    retrieved_at: dt.date
    licence_class: LicenceClass
    licence_note: str
    effective_date: dt.date | None = None
    source_url: str | None = None


@dataclass(frozen=True)
class ItemSpec:
    """The curated content of one knowledge-item version."""

    text: str
    text_origin: TextOrigin
    title: str | None = None
    clause_ref: str | None = None
    applicability: Sequence[str] = field(default_factory=tuple)
    control_id: uuid.UUID | None = None


class KnowledgeAdminService:
    """Curates the shared corpus. Only a Knowledge-Base Administrator gets past the checks."""

    def __init__(
        self,
        session: Session,
        actor: Actor,
        *,
        embedder: EmbeddingProvider,
        rules: RetrievalRules,
    ) -> None:
        self._session = session
        self._actor = actor
        self._embedder = embedder
        self._rules = rules
        self._repo = KnowledgeBaseRepository(session, actor)
        self._audit = AuditService(session)

    # ------------------------------------------------------------------
    # Sources and controls
    # ------------------------------------------------------------------
    def add_source(self, spec: SourceSpec) -> NormativeSource:
        """Register a normative source with its full provenance."""
        today = dt.datetime.now(dt.UTC).date()
        jurisdiction = normalise_jurisdiction(spec.jurisdiction)
        for name in ("issuing_body", "title", "version", "licence_note"):
            if not str(getattr(spec, name)).strip():
                raise KnowledgeBaseError(f"a normative source needs a non-empty {name}")
        if spec.retrieved_at > today:
            raise KnowledgeBaseError(
                "retrieved_at records when a human consulted the source; it cannot be in the future"
            )
        if (
            spec.licence_class is LicenceClass.SYNTHETIC
            and spec.source_type not in SYNTHETIC_PERMITTED_TYPES
        ):
            raise LicenceViolationError(
                f"a synthetic source cannot be typed {spec.source_type}: only organisational "
                "policies and best-practice notes may be fictional (approved Phase 0 D.2). "
                "ReqPilot does not invent laws, regulations or standards."
            )
        if self._repo.find_source(
            issuing_body=spec.issuing_body.strip(),
            title=spec.title.strip(),
            version=spec.version.strip(),
            jurisdiction=jurisdiction,
        ):
            raise KnowledgeBaseError(
                "this source (issuing body, title, version, jurisdiction) already exists"
            )

        source = NormativeSource(
            source_type=spec.source_type,
            issuing_body=spec.issuing_body.strip(),
            title=spec.title.strip(),
            jurisdiction=jurisdiction,
            version=spec.version.strip(),
            effective_date=spec.effective_date,
            retrieved_at=spec.retrieved_at,
            source_url=spec.source_url.strip() if spec.source_url else None,
            licence_class=spec.licence_class,
            licence_note=spec.licence_note.strip(),
            created_by=self._actor.actor_id,
        )
        self._repo.add(source)
        self._record(
            AuditEventType.KB_SOURCE_ADDED,
            subject_type="normative_source",
            subject_id=source.id,
            subject_version=source.version,
            payload={
                "source_type": str(source.source_type),
                "jurisdiction": source.jurisdiction,
                "licence_class": str(source.licence_class),
            },
        )
        return source

    def add_control(
        self,
        source_id: uuid.UUID,
        *,
        control_ref: str,
        title: str,
        paraphrase: str,
        applicability: Sequence[str] = (),
    ) -> Control:
        """Add a control as reference plus team-written paraphrase (G.5)."""
        source = self._source(source_id)
        if not control_ref.strip() or not title.strip() or not paraphrase.strip():
            raise KnowledgeBaseError("a control needs a reference, a title and a paraphrase")
        control = Control(
            normative_source_id=source.id,
            control_ref=control_ref.strip(),
            title=title.strip(),
            paraphrase=normalise_text(paraphrase).strip(),
            applicability=normalise_tags(applicability),
            created_by=self._actor.actor_id,
        )
        self._repo.add(control)
        self._record(
            AuditEventType.KB_CONTROL_ADDED,
            subject_type="control",
            subject_id=control.id,
            payload={"normative_source_id": str(source.id), "control_ref": control.control_ref},
        )
        return control

    # ------------------------------------------------------------------
    # Items: add, version, retire, supersede
    # ------------------------------------------------------------------
    def add_item(self, source_id: uuid.UUID, item_key: str, spec: ItemSpec) -> KnowledgeItem:
        """Add version 1 of a new knowledge item and ingest it."""
        key = item_key.strip().upper()
        if not ITEM_KEY_PATTERN.match(key):
            raise KnowledgeBaseError(
                f"item key {item_key!r} must be 3-100 characters of A-Z, 0-9 and . _ : -"
            )
        if self._repo.versions_of(key):
            raise KnowledgeBaseError(
                f"item key {key} already exists; add a new version of it instead"
            )
        source = self._source(source_id)
        text, tags = self._checked_content(source, spec)
        kb_version = self._repo.current_kb_version(lock=True) + 1
        item = self._new_item(source, key, 1, kb_version, text, tags, spec)
        self._record_item_added(item, predecessor=None)
        self._ingest(item)
        return item

    def version_item(
        self,
        item_id: uuid.UUID,
        spec: ItemSpec,
        *,
        reason: str,
        normative_source_id: uuid.UUID | None = None,
    ) -> KnowledgeItem:
        """Create the next version of an item; the predecessor becomes superseded."""
        previous = self._active_item(item_id)
        source = self._source(normative_source_id or previous.normative_source_id)
        text, tags = self._checked_content(source, spec)
        if text_hash(text) == previous.content_hash and source.id == previous.normative_source_id:
            raise KnowledgeBaseError("the new version's text is identical to the current version")
        reason = self._reason(reason)

        kb_version = self._repo.current_kb_version(lock=True) + 1
        latest = max(v.version_no for v in self._repo.versions_of(previous.item_key))
        successor = self._new_item(
            source, previous.item_key, latest + 1, kb_version, text, tags, spec
        )
        self._supersede(previous, SupersessionKind.VERSIONED, kb_version, reason, successor)
        self._record_item_added(successor, predecessor=previous)
        self._ingest(successor)
        return successor

    def retire_item(self, item_id: uuid.UUID, *, reason: str) -> KnowledgeItem:
        """Withdraw an item with no successor. It stops being retrievable."""
        item = self._active_item(item_id)
        kb_version = self._repo.current_kb_version(lock=True) + 1
        self._supersede(item, SupersessionKind.RETIRED, kb_version, self._reason(reason), None)
        return item

    def supersede_item(
        self, item_id: uuid.UUID, *, successor_id: uuid.UUID, reason: str
    ) -> KnowledgeItem:
        """Mark an item superseded by a *different* existing item (architecture S).

        For a new edition of the same item, use :meth:`version_item`.
        """
        item = self._active_item(item_id)
        successor = self._active_item(successor_id)
        if successor.id == item.id:
            raise KnowledgeBaseError("an item cannot supersede itself")
        if successor.item_key == item.item_key:
            raise KnowledgeBaseError("a later version of the same item is added with version_item")
        kb_version = self._repo.current_kb_version(lock=True) + 1
        self._supersede(
            item, SupersessionKind.REPLACED, kb_version, self._reason(reason), successor
        )
        return item

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _source(self, source_id: uuid.UUID) -> NormativeSource:
        source = self._repo.get_source(source_id)
        if source is None:
            raise KnowledgeBaseError(f"normative source {source_id} not found")
        return source

    def _active_item(self, item_id: uuid.UUID) -> KnowledgeItem:
        item = self._repo.get_item(item_id)
        if item is None:
            raise KnowledgeBaseError(f"knowledge item {item_id} not found")
        if item.status is not KnowledgeItemStatus.ACTIVE:
            raise KnowledgeBaseError(
                f"knowledge item {item.item_key} v{item.version_no} is already {item.status}; "
                "a superseded item is history"
            )
        return item

    @staticmethod
    def _reason(reason: str) -> str:
        if not reason or not reason.strip():
            raise KnowledgeBaseError("a reason is required to supersede or retire an item")
        return reason.strip()[:1000]

    def _checked_content(self, source: NormativeSource, spec: ItemSpec) -> tuple[str, list[str]]:
        permitted = LICENCE_PERMITTED_ORIGINS[source.licence_class]
        if spec.text_origin not in permitted:
            raise LicenceViolationError(
                f"source '{source.title}' is licensed {source.licence_class}; it admits "
                f"{sorted(str(o) for o in permitted)}, not {spec.text_origin}. "
                "Copyrighted standards take team-written paraphrases only (approved Phase 0 D.2)."
            )
        text = normalise_text(spec.text).strip()
        if not text:
            raise KnowledgeBaseError("a knowledge item needs text")
        if spec.control_id is not None:
            control = self._repo.get_control(spec.control_id)
            if control is None or control.normative_source_id != source.id:
                raise KnowledgeBaseError("the control must belong to the item's source")
        if self._repo.active_duplicate(source.id, text_hash(text)):
            raise KnowledgeBaseError(
                "identical text is already active under this source (J.2 dedupe)"
            )
        return text, normalise_tags(spec.applicability)

    def _new_item(
        self,
        source: NormativeSource,
        key: str,
        version_no: int,
        kb_version: int,
        text: str,
        tags: list[str],
        spec: ItemSpec,
    ) -> KnowledgeItem:
        item = KnowledgeItem(
            item_key=key,
            version_no=version_no,
            normative_source_id=source.id,
            control_id=spec.control_id,
            title=spec.title.strip() if spec.title else None,
            clause_ref=spec.clause_ref.strip() if spec.clause_ref else None,
            text=text,
            text_origin=spec.text_origin,
            content_hash=text_hash(text),
            applicability=tags,
            kb_version=kb_version,
            status=KnowledgeItemStatus.ACTIVE,
            created_by=self._actor.actor_id,
        )
        self._repo.add(item)
        return item

    def _supersede(
        self,
        item: KnowledgeItem,
        kind: SupersessionKind,
        kb_version: int,
        reason: str,
        successor: KnowledgeItem | None,
    ) -> None:
        self._repo.mark_superseded(
            item,
            status=KnowledgeItemStatus.SUPERSEDED,
            superseded_by_id=successor.id if successor else None,
            supersession_kind=kind,
            superseded_in_kb_version=kb_version,
            supersession_reason=reason,
            superseded_at=utc_now(),
        )
        self._record(
            AuditEventType.KB_ITEM_SUPERSEDED,
            subject_type="knowledge_item",
            subject_id=item.id,
            subject_version=str(item.version_no),
            payload={
                "item_key": item.item_key,
                "kind": str(kind),
                "successor_id": str(successor.id) if successor else None,
                "superseded_in_kb_version": kb_version,
            },
        )

    def _record_item_added(self, item: KnowledgeItem, predecessor: KnowledgeItem | None) -> None:
        self._record(
            AuditEventType.KB_ITEM_ADDED,
            subject_type="knowledge_item",
            subject_id=item.id,
            subject_version=str(item.version_no),
            payload={
                "item_key": item.item_key,
                "kb_version": item.kb_version,
                "normative_source_id": str(item.normative_source_id),
                "content_hash": item.content_hash,
                "text_origin": str(item.text_origin),
                "predecessor_id": str(predecessor.id) if predecessor else None,
            },
        )

    def _ingest(self, item: KnowledgeItem) -> list[KnowledgeChunk]:
        """Chunk, embed and persist an item (J.2, J.3). The item's text is canonical."""
        if self._embedder.dimension != EMBEDDING_DIMENSION:
            raise KnowledgeBaseError(
                f"embedding provider dimension {self._embedder.dimension} does not match "
                f"the schema's {EMBEDDING_DIMENSION}"
            )
        chunks = chunk_knowledge_item(
            item.text,
            self._rules.knowledge_item_window,
            min_clause_boundaries=self._rules.min_clause_boundaries,
        )
        if not chunks:
            raise KnowledgeBaseError("the item produced no chunks")
        if not all(c.matches(item.text) for c in chunks):  # pragma: no cover - chunker invariant
            raise KnowledgeBaseError("a chunk does not match its span of the item text")
        vectors = self._embedder.embed_documents([c.text for c in chunks])
        rows = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            digest = text_hash(chunk.text)
            rows.append(
                KnowledgeChunk(
                    id=chunk_id_for(
                        item.id, chunk.ordinal, chunk.char_start, chunk.char_end, digest
                    ),
                    knowledge_item_id=item.id,
                    ordinal=chunk.ordinal,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    text=chunk.text,
                    text_hash=digest,
                    strategy=chunk.strategy,
                    structure_label=chunk.structure_label,
                    token_count=chunk.token_count,
                    embedding=vector,
                    embedding_model=self._embedder.model_id,
                )
            )
        self._repo.add_all(rows)
        self._record(
            AuditEventType.SOURCE_INGESTED,
            subject_type="knowledge_item",
            subject_id=item.id,
            subject_version=str(item.version_no),
            payload={
                "chunk_count": len(rows),
                "chunk_ids": [str(r.id) for r in rows],
                "embedding_model": self._embedder.model_id,
                "ruleset_version": self._rules.version,
            },
        )
        return rows

    def _record(
        self,
        event_type: AuditEventType,
        *,
        subject_type: str,
        subject_id: uuid.UUID,
        subject_version: str | None = None,
        payload: dict[str, object],
    ) -> None:
        """Audit a curation action. The shared corpus has no project, so neither does its chain."""
        self._audit.append(
            event_type=event_type,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=None,
            subject_type=subject_type,
            subject_id=str(subject_id),
            subject_version=subject_version,
            payload=payload,
        )
