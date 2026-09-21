"""Project source documents: the input to batch extraction (``FR-ING-001``, ``FR-ING-004``).

The architecture's ingestion pipeline (J.2), as far as P3 implements it::

    upload -> declared type + sensitivity -> parse (txt / md / pdf / docx)
      -> masking stage (NOT IMPLEMENTED: NoMasking, recorded as such)
      -> content-hash dedupe -> segment with exact offsets (J.3)
      -> persist document + segments -> audit SOURCE_INGESTED

Segments are speaker turns for a transcript (never split), and headings then
paragraphs for other documents. **No embedding is computed**: J.2 forbids
unmasked text in the vector store, and masking does not exist yet.
"""

from __future__ import annotations

import hashlib
import uuid
from collections import Counter
from dataclasses import dataclass

from sqlalchemy.orm import Session

from reqpilot.domain.enums import Action, AuditEventType, DataSensitivity, SourceDocumentType
from reqpilot.domain.errors import KnowledgeBaseError, SourceDocumentError
from reqpilot.domain.ids import ProjectId, source_chunk_id_for
from reqpilot.domain.models.extraction import SourceChunk, SourceDocument
from reqpilot.domain.policy import Actor
from reqpilot.repositories.extraction import SourceDocumentRepository
from reqpilot.retrieval.chunking import (
    TextChunk,
    chunk_project_document,
    segment_transcript_document,
)
from reqpilot.retrieval.extraction import extract_text_from_bytes, normalise_text
from reqpilot.retrieval.rules import RetrievalRules
from reqpilot.rules.extraction import ExtractionRules
from reqpilot.security.masking import Masker, default_masker
from reqpilot.services.audit import AuditService

#: Batch mode is for one document at a time; a larger text is refused rather
#: than silently truncated.
MAX_SOURCE_CHARS = 200_000

#: Types whose text is tried as ``Speaker: words`` turns first.
_SPEAKER_TYPES = frozenset({SourceDocumentType.TRANSCRIPT, SourceDocumentType.MEETING_NOTES})


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Segment:
    chunk: TextChunk
    speaker: str | None


class SourceDocumentService:
    def __init__(
        self,
        session: Session,
        actor: Actor,
        *,
        retrieval_rules: RetrievalRules,
        extraction_rules: ExtractionRules,
        masker: Masker | None = None,
    ) -> None:
        self._session = session
        self._actor = actor
        self._repo = SourceDocumentRepository(session, actor)
        self._retrieval_rules = retrieval_rules
        self._rules = extraction_rules
        self._masker = masker or default_masker()
        self._audit = AuditService(session)

    # -- adding sources ----------------------------------------------------
    def add_file(
        self,
        *,
        project_id: ProjectId,
        doc_type: SourceDocumentType,
        title: str,
        data: bytes,
        filename: str,
        sensitivity: DataSensitivity,
    ) -> tuple[SourceDocument, bool]:
        suffix = "." + filename.rsplit(".", 1)[-1] if "." in filename else ""
        try:
            text = extract_text_from_bytes(data, suffix)
        except KnowledgeBaseError as exc:
            raise SourceDocumentError(str(exc)) from exc
        return self.add_text(
            project_id=project_id,
            doc_type=doc_type,
            title=title,
            text=text,
            sensitivity=sensitivity,
            filename=filename,
        )

    def add_text(
        self,
        *,
        project_id: ProjectId,
        doc_type: SourceDocumentType,
        title: str,
        text: str,
        sensitivity: DataSensitivity,
        filename: str | None = None,
    ) -> tuple[SourceDocument, bool]:
        """Store a source once. Returns ``(document, created)``.

        The same text uploaded again to the same project returns the existing
        document: it is the same source, and requirements already cite it.
        """
        self._repo.authorize(Action.SOURCE_CREATE, project_id)
        title = " ".join(title.split())
        if not title:
            raise SourceDocumentError("a source document needs a title")
        text = normalise_text(text)
        if not text.strip():
            raise SourceDocumentError("a source document cannot be empty")
        if len(text) > MAX_SOURCE_CHARS:
            raise SourceDocumentError(
                f"a source document is limited to {MAX_SOURCE_CHARS} characters in batch mode"
            )

        masked = self._masker.mask(text)
        content_hash = text_hash(masked.text)
        existing = self._repo.get_by_hash(project_id, content_hash)
        if existing is not None:
            return existing, False

        segments = self.segment(doc_type, masked.text)
        if not segments:
            raise SourceDocumentError("the document has no text to segment")

        document = SourceDocument(
            id=uuid.uuid4(),
            project_id=project_id,
            doc_type=doc_type,
            title=title[:300],
            filename=(filename or None) and filename[:300],
            text=masked.text,
            content_hash=content_hash,
            char_count=len(masked.text),
            sensitivity=sensitivity,
            masking_status=masked.status,
            masker_id=masked.masker_id,
            uploaded_by=self._actor.actor_id,
        )
        chunks = [
            SourceChunk(
                id=source_chunk_id_for(
                    document.id,
                    segment.chunk.ordinal,
                    segment.chunk.char_start,
                    segment.chunk.char_end,
                    text_hash(segment.chunk.text),
                ),
                project_id=project_id,
                source_document_id=document.id,
                ordinal=segment.chunk.ordinal,
                char_start=segment.chunk.char_start,
                char_end=segment.chunk.char_end,
                text=segment.chunk.text,
                text_hash=text_hash(segment.chunk.text),
                strategy=segment.chunk.strategy,
                speaker=segment.speaker[:200] if segment.speaker else None,
                structure_label=segment.chunk.structure_label,
                token_count=segment.chunk.token_count,
            )
            for segment in segments
        ]
        for chunk in chunks:
            if masked.text[chunk.char_start : chunk.char_end] != chunk.text:
                raise SourceDocumentError(
                    "a segment does not match its source span"
                )  # pragma: no cover
        self._repo.add(document, chunks)

        strategies = Counter(str(c.strategy) for c in chunks)
        self._audit.append(
            event_type=AuditEventType.SOURCE_INGESTED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="source_document",
            subject_id=str(document.id),
            payload={
                "doc_type": str(doc_type),
                "sensitivity": str(sensitivity),
                "masking_status": str(masked.status),
                "masker_id": masked.masker_id,
                "content_hash": content_hash,
                "char_count": document.char_count,
                "chunk_count": len(chunks),
                "strategies": dict(sorted(strategies.items())),
                # Deliberately empty in P3 (architecture J.2).
                "embedded": False,
            },
        )
        return document, True

    def segment(self, doc_type: SourceDocumentType, text: str) -> list[Segment]:
        """Deterministic segmentation with exact offsets (J.3)."""
        if doc_type in _SPEAKER_TYPES:
            turns = segment_transcript_document(
                text, max_speaker_chars=self._rules.max_speaker_label_chars
            )
            if turns:
                return [Segment(chunk=t.chunk, speaker=t.speaker) for t in turns]
        chunks = chunk_project_document(text, self._retrieval_rules.project_document_window)
        return [Segment(chunk=c, speaker=None) for c in chunks]

    # -- reads -------------------------------------------------------------
    def get(self, project_id: ProjectId, document_id: uuid.UUID) -> SourceDocument | None:
        return self._repo.get(project_id, document_id)

    def list_documents(self, project_id: ProjectId) -> list[SourceDocument]:
        return self._repo.list_for_project(project_id)

    def chunks(self, project_id: ProjectId, document_id: uuid.UUID) -> list[SourceChunk]:
        return self._repo.chunks(project_id, document_id)
