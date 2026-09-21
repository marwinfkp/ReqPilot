"""Evidence persistence and citation resolution (``FR-RAG-003``; architecture J.5).

Runs on SQLite: recording evidence re-checks every chunk through the same
allowlist join retrieval uses, and that join needs no vector operator. The
retrieval results are built from stored chunks exactly as the retrieval service
builds them (``tests.kb_helpers.as_retrieved``).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from tests.kb_helpers import (
    actor,
    admin_service,
    allowlist,
    as_retrieved,
    chunks_of,
    make_project,
    success,
    synthetic_item,
    synthetic_source,
)

from reqpilot.domain.enums import Role, TextOrigin
from reqpilot.domain.errors import (
    CitationError,
    EvidenceIntegrityError,
    KnowledgeBaseError,
    ProjectIsolationError,
    UngroundedRetrievalError,
)
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.runs import GraphRun
from reqpilot.domain.refs import EvidenceKind
from reqpilot.retrieval.contracts import EmptyReason, RetrievalResult
from reqpilot.services.knowledge import EvidenceService, ItemSpec, KnowledgeScopeService
from reqpilot.services.knowledge.admin import text_hash

pytestmark = pytest.mark.integration

POLICY = """1. Access
1.1 Access to customer data is granted on a least-privilege basis.
1.2 Access rights are reviewed quarterly.
"""


@pytest.fixture
def world(db_session: Session, retrieval_rules):
    project = make_project(db_session, "Loan Origination")
    other = make_project(db_session, "Payments")
    kb_admin = actor(project.id, Role.KB_ADMIN)
    kb_admin.roles_by_project[other.id] = frozenset({Role.KB_ADMIN})
    admin = admin_service(db_session, kb_admin, retrieval_rules)
    allowed = synthetic_source(admin, "Access policy")
    blocked = synthetic_source(admin, "Unapproved policy")
    item = synthetic_item(admin, allowed, "SYN-AC-1", POLICY)
    blocked_item = synthetic_item(admin, blocked, "SYN-X-1", "1. Other\n1.1 Other text.\n1.2 More.")
    allowlist(db_session, kb_admin, project, allowed)
    return {
        "session": db_session,
        "project": project,
        "other": other,
        "kb_admin": kb_admin,
        "admin": admin,
        "item": item,
        "chunks": chunks_of(db_session, item),
        "blocked_chunks": chunks_of(db_session, blocked_item),
        "analyst": actor(project.id, Role.ANALYST),
        "auditor": actor(project.id, Role.AUDITOR),
        "outsider": actor(other.id, Role.ANALYST),
    }


def record(world, chunks=None, **kwargs):
    chunks = world["chunks"] if chunks is None else chunks
    result = success(world["project"], as_retrieved(world["session"], chunks))
    return result, EvidenceService(world["session"], world["analyst"]).record(result, **kwargs)


def pid(world) -> ProjectId:
    return ProjectId(world["project"].id)


# --- recording -----------------------------------------------------------------


def test_evidence_records_exactly_what_was_retrieved(world) -> None:
    result, rows = record(world)
    assert len(rows) == 3
    for chunk, row, retrieved in zip(world["chunks"], rows, result.chunks, strict=True):
        assert row.kind is EvidenceKind.KNOWLEDGE_ITEM
        assert row.target_id == world["item"].id
        assert row.knowledge_chunk_id == chunk.id
        assert (row.char_start, row.char_end) == (chunk.char_start, chunk.char_end)
        assert row.quote == chunk.text == world["item"].text[row.char_start : row.char_end]
        assert row.quote_hash == text_hash(row.quote)
        assert row.retrieval_id == result.retrieval_id
        assert row.rank == retrieved.rank
        assert row.kb_version == result.kb_version
        assert row.embedding_model == result.embedding_model
        assert row.ruleset_version == result.ruleset_version
        assert row.query_hash == result.query_hash
        assert row.created_by == world["analyst"].actor_id


def test_an_empty_retrieval_cannot_become_evidence(world) -> None:
    """FR-RAG-005: nothing to ground on means escalate, not record."""
    empty = RetrievalResult.empty(
        EmptyReason.NOTHING_RELEVANT,
        **success(world["project"], as_retrieved(world["session"], world["chunks"][:1])).model_dump(
            include={
                "retrieval_id",
                "project_id",
                "query_hash",
                "as_of",
                "kb_version",
                "kb_version_pinned",
                "embedding_model",
                "ruleset_version",
                "classification",
            }
        ),
    )
    with pytest.raises(UngroundedRetrievalError):
        EvidenceService(world["session"], world["analyst"]).record(empty)


def test_a_non_allowlisted_chunk_can_never_become_evidence(world) -> None:
    """Even a hand-built result naming a real chunk is refused at the database join."""
    with pytest.raises(EvidenceIntegrityError, match="not retrievable by this project"):
        record(world, chunks=world["blocked_chunks"])


def test_a_forged_chunk_id_is_refused(world) -> None:
    result = success(world["project"], as_retrieved(world["session"], world["chunks"][:1]))
    forged = result.model_copy(
        update={"chunks": (result.chunks[0].model_copy(update={"chunk_id": uuid.uuid4()}),)}
    )
    with pytest.raises(EvidenceIntegrityError):
        EvidenceService(world["session"], world["analyst"]).record(forged)


def test_a_tampered_quote_in_the_result_is_refused(world) -> None:
    result = success(world["project"], as_retrieved(world["session"], world["chunks"][:1]))
    tampered = result.model_copy(
        update={
            "chunks": (
                result.chunks[0].model_copy(update={"text": "The bank guarantees compliance."}),
            )
        }
    )
    with pytest.raises(EvidenceIntegrityError, match="does not match"):
        EvidenceService(world["session"], world["analyst"]).record(tampered)


def test_a_superseded_chunk_can_no_longer_become_evidence(world) -> None:
    world["admin"].version_item(
        world["item"].id,
        ItemSpec(text=POLICY + "1.3 New clause.", text_origin=TextOrigin.SYNTHETIC),
        reason="added 1.3",
    )
    with pytest.raises(EvidenceIntegrityError):
        record(world)


def test_evidence_is_recorded_once_per_retrieval(world) -> None:
    result, _rows = record(world)
    with pytest.raises(KnowledgeBaseError, match="already been recorded"):
        EvidenceService(world["session"], world["analyst"]).record(result)


def test_the_graph_run_must_belong_to_the_project(world) -> None:
    run = GraphRun(project_id=world["other"].id, graph_name="analysis", thread_id="t")
    world["session"].add(run)
    world["session"].flush()
    with pytest.raises(KnowledgeBaseError, match="graph run"):
        record(world, graph_run_id=run.id)


def test_evidence_can_be_tied_to_a_run_and_listed_as_its_evidence_ids(world) -> None:
    run = GraphRun(project_id=world["project"].id, graph_name="analysis", thread_id="t")
    world["session"].add(run)
    world["session"].flush()
    _result, rows = record(world, graph_run_id=run.id)
    ids = EvidenceService(world["session"], world["analyst"]).evidence_ids_for_run(
        pid(world), run.id
    )
    assert ids == {r.id for r in rows}


def test_evidence_of_another_project_cannot_be_recorded(world) -> None:
    result = success(world["project"], as_retrieved(world["session"], world["chunks"]))
    with pytest.raises(ProjectIsolationError):
        EvidenceService(world["session"], world["outsider"]).record(result)


def test_an_auditor_cannot_create_evidence(world) -> None:
    from reqpilot.domain.errors import AuthorizationError

    result = success(world["project"], as_retrieved(world["session"], world["chunks"]))
    with pytest.raises(AuthorizationError):
        EvidenceService(world["session"], world["auditor"]).record(result)


# --- resolving -------------------------------------------------------------------


def test_a_citation_resolves_to_the_exact_span_and_its_provenance(world) -> None:
    _result, rows = record(world)
    citation = EvidenceService(world["session"], world["auditor"]).resolve_citation(
        pid(world), rows[1].id, allowed_evidence_ids={r.id for r in rows}
    )
    assert citation.quote == world["item"].text[citation.char_start : citation.char_end]
    assert citation.knowledge_chunk_id == world["chunks"][1].id
    assert citation.item_key == "SYN-AC-1" and citation.item_version_no == 1
    assert citation.source_title == "Access policy (fictional)"
    assert citation.binding == "Binding inside one organisation"
    assert citation.jurisdiction == "IN"
    assert citation.source_version == "2026.1"
    assert citation.retrieved_at is not None


def test_a_citation_outside_the_runs_evidence_set_does_not_resolve(world) -> None:
    """The model may cite only evidence the run was given (J.5)."""
    _result, rows = record(world)
    with pytest.raises(CitationError, match="does not resolve"):
        EvidenceService(world["session"], world["analyst"]).resolve_citation(
            pid(world), rows[0].id, allowed_evidence_ids={rows[1].id}
        )


def test_a_fabricated_citation_does_not_resolve(world) -> None:
    fabricated = uuid.uuid4()
    with pytest.raises(CitationError, match="does not resolve"):
        EvidenceService(world["session"], world["analyst"]).resolve_citation(
            pid(world), fabricated, allowed_evidence_ids={fabricated}
        )


def test_another_projects_evidence_does_not_resolve_here(world) -> None:
    _result, rows = record(world)
    other_admin = world["kb_admin"]
    allowlist(world["session"], other_admin, world["other"])
    outsider = world["outsider"]
    with pytest.raises(ProjectIsolationError):
        EvidenceService(world["session"], outsider).describe(pid(world), rows[0].id)
    with pytest.raises(CitationError):
        EvidenceService(world["session"], outsider).describe(
            ProjectId(world["other"].id), rows[0].id
        )


def test_check_citations_separates_valid_from_invalid(world) -> None:
    _result, rows = record(world)
    allowed = {r.id for r in rows[:2]}
    check = EvidenceService(world["session"], world["analyst"]).check_citations(
        pid(world),
        [str(rows[0].id), str(rows[2].id), "not-a-uuid", str(uuid.uuid4())],
        allowed_evidence_ids=allowed,
    )
    assert [c.evidence_id for c in check.resolved] == [rows[0].id]
    assert [raw for raw, _ in check.rejected] == [
        str(rows[2].id),
        "not-a-uuid",
        check.rejected[2][0],
    ]
    assert not check.all_resolved


def test_historical_evidence_resolves_after_the_kb_moves_on(world) -> None:
    """Superseding the item and removing the source from the allowlist change nothing past."""
    _result, rows = record(world)
    world["admin"].version_item(
        world["item"].id,
        ItemSpec(text=POLICY.replace("quarterly", "monthly"), text_origin=TextOrigin.SYNTHETIC),
        reason="tightened",
    )
    KnowledgeScopeService(world["session"], world["kb_admin"]).disallow(
        pid(world), world["item"].normative_source_id
    )
    citation = EvidenceService(world["session"], world["analyst"]).describe(pid(world), rows[2].id)
    assert "quarterly" in citation.quote
    assert citation.item_status_now == "superseded"
    assert citation.kb_version == 1


def test_tampered_stored_evidence_is_detected_not_resolved(world) -> None:
    """SQLite has no trigger, so the row can be edited - and resolution notices."""
    _result, rows = record(world)
    world["session"].execute(
        text("UPDATE evidence SET quote = 'forged' WHERE id = :i"), {"i": rows[0].id.hex}
    )
    world["session"].expire_all()
    with pytest.raises(EvidenceIntegrityError, match="no longer matches"):
        EvidenceService(world["session"], world["analyst"]).describe(pid(world), rows[0].id)
    check = EvidenceService(world["session"], world["analyst"]).check_citations(
        pid(world), [str(rows[0].id)], allowed_evidence_ids={rows[0].id}
    )
    assert check.rejected and "no longer matches" in check.rejected[0][1]


def test_evidence_rows_are_append_only_in_the_orm(world) -> None:
    from reqpilot.domain.errors import ImmutableRecordError

    _result, rows = record(world)
    rows[0].quote = "rewritten"
    with pytest.raises(ImmutableRecordError):
        world["session"].flush()
