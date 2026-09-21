"""Project source ingestion (FR-ING-001, FR-ING-004; architecture G.3, J.2, J.3)."""

from __future__ import annotations

import io
import json

import pytest
from docx import Document
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.p3_helpers import extraction_rules, ingest, member, retrieval_rules, workshop_text
from tests.workflow.test_p1_exit_test import make_project

from reqpilot.domain.enums import (
    AuditEventType,
    ChunkStrategy,
    DataSensitivity,
    MaskingStatus,
    Role,
    SourceDocumentType,
)
from reqpilot.domain.errors import (
    AuthorizationError,
    ImmutableRecordError,
    ProjectIsolationError,
    SourceDocumentError,
)
from reqpilot.domain.ids import ProjectId, source_chunk_id_for
from reqpilot.domain.models.extraction import SourceChunk
from reqpilot.services.audit import AuditService
from reqpilot.services.extraction import SourceDocumentService
from reqpilot.services.extraction.sources import text_hash

pytestmark = pytest.mark.integration


@pytest.fixture
def world(db_session: Session):
    project = make_project(db_session)
    return {
        "session": db_session,
        "project": project,
        "analyst": member(db_session, project, Role.ANALYST, "a@example.test"),
        "officer": member(db_session, project, Role.COMPLIANCE_OFFICER, "c@example.test"),
    }


def service(world, who: str = "analyst") -> SourceDocumentService:
    return SourceDocumentService(
        world["session"],
        world[who],
        retrieval_rules=retrieval_rules(),
        extraction_rules=extraction_rules(),
    )


def test_a_transcript_is_stored_once_and_segmented_by_speaker_turn(world) -> None:
    document = ingest(world["session"], world["analyst"], world["project"].id)
    chunks = service(world).chunks(ProjectId(world["project"].id), document.id)
    assert document.text == workshop_text().replace("\r\n", "\n")
    assert len(chunks) == 11
    assert all(c.strategy is ChunkStrategy.UTTERANCE for c in chunks)
    for chunk in chunks:
        assert document.text[chunk.char_start : chunk.char_end] == chunk.text
        assert chunk.id == source_chunk_id_for(
            document.id, chunk.ordinal, chunk.char_start, chunk.char_end, text_hash(chunk.text)
        )
        assert chunk.embedding is None, "no unmasked project text reaches the vector store (J.2)"


def test_the_masking_stage_says_honestly_that_nothing_was_masked(world) -> None:
    document = ingest(world["session"], world["analyst"], world["project"].id)
    assert document.masking_status is MaskingStatus.NOT_MASKED and document.masker_id == "none"
    assert document.sensitivity is DataSensitivity.SYNTHETIC


def test_the_same_text_is_the_same_source(world) -> None:
    first, created = service(world).add_text(
        project_id=ProjectId(world["project"].id),
        doc_type=SourceDocumentType.MEETING_NOTES,
        title="Notes",
        text="Priya: We need exports.\r\nSam: Fast pages.",
        sensitivity=DataSensitivity.SYNTHETIC,
    )
    again, created_again = service(world).add_text(
        project_id=ProjectId(world["project"].id),
        doc_type=SourceDocumentType.MEETING_NOTES,
        title="Notes, again",
        text="Priya: We need exports.\nSam: Fast pages.",
        sensitivity=DataSensitivity.SYNTHETIC,
    )
    assert created and not created_again and again.id == first.id


def test_a_document_without_speakers_is_segmented_by_section(world) -> None:
    document, _ = service(world).add_text(
        project_id=ProjectId(world["project"].id),
        doc_type=SourceDocumentType.POLICY,
        title="Policy",
        text="# Access\n\nAccess is reviewed quarterly.\n\n# Retention\n\nRecords are kept.",
        sensitivity=DataSensitivity.UNCLASSIFIED,
    )
    chunks = service(world).chunks(ProjectId(world["project"].id), document.id)
    assert {c.strategy for c in chunks} <= {ChunkStrategy.SECTION, ChunkStrategy.WINDOW}
    assert all(c.speaker is None for c in chunks)


def test_a_docx_upload_is_parsed(world) -> None:
    buffer = io.BytesIO()
    doc = Document()
    doc.add_paragraph("Priya: Applicants must be able to upload documents.")
    doc.save(buffer)
    document, created = service(world).add_file(
        project_id=ProjectId(world["project"].id),
        doc_type=SourceDocumentType.TRANSCRIPT,
        title="Upload",
        data=buffer.getvalue(),
        filename="notes.docx",
        sensitivity=DataSensitivity.SYNTHETIC,
    )
    assert created and "upload documents" in document.text and document.filename == "notes.docx"


@pytest.mark.parametrize(("text", "filename"), [("   ", None), ("x", "virus.exe")])
def test_empty_or_unsupported_sources_are_refused(world, text, filename) -> None:
    with pytest.raises(SourceDocumentError):
        if filename:
            service(world).add_file(
                project_id=ProjectId(world["project"].id),
                doc_type=SourceDocumentType.TRANSCRIPT,
                title="t",
                data=text.encode(),
                filename=filename,
                sensitivity=DataSensitivity.SYNTHETIC,
            )
        else:
            ingest(world["session"], world["analyst"], world["project"].id, text)


def test_only_an_analyst_adds_sources_and_only_in_their_project(world) -> None:
    with pytest.raises(AuthorizationError):
        ingest(world["session"], world["officer"], world["project"].id)
    other = make_project(world["session"], "Payments")
    with pytest.raises(ProjectIsolationError):
        ingest(world["session"], world["analyst"], other.id)


def test_ingestion_is_audited_without_content(world) -> None:
    ingest(world["session"], world["analyst"], world["project"].id)
    (event,) = [
        e
        for e in AuditService(world["session"]).list_for_project(world["project"].id)
        if e.event_type is AuditEventType.SOURCE_INGESTED
    ]
    assert event.payload["chunk_count"] == 11 and event.payload["embedded"] is False
    assert event.payload["masking_status"] == "not_masked"
    assert "income documents" not in json.dumps(event.payload)


def test_a_stored_segment_cannot_be_edited(world) -> None:
    ingest(world["session"], world["analyst"], world["project"].id)
    chunk = world["session"].scalars(select(SourceChunk)).first()
    chunk.text = "rewritten"
    with pytest.raises(ImmutableRecordError):
        world["session"].flush()
    world["session"].rollback()
