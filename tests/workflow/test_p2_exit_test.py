"""P2 exit demonstration: the whole grounding chain, end to end, on PostgreSQL.

    curated source -> knowledge item -> knowledge version -> chunking with offsets
    -> embedding -> PostgreSQL + pgvector -> allowlist + jurisdiction + effective date
    -> hybrid vector + keyword retrieval -> ranked chunk -> evidence -> resolvable citation

and the security case the roadmap's exit test names ("a non-allowlisted source
is rejected")::

    Project A: Source X allowlisted, Source Y not.
    The query matches Y more strongly than X.
    Y must not be returned; X may be.

Runs against a live PostgreSQL; skips visibly without one. Uses the synthetic,
fictional development corpus and the deterministic offline embedding provider.
The recall@5 part of the exit gate (ET-06) needs the curated corpus and a probe
set, and is reported separately (``docs/05-p2-knowledge-base-rag.md``).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from tests.kb_helpers import (
    actor,
    admin_service,
    allowlist,
    chunks_of,
    make_project,
    synthetic_item,
    synthetic_source,
)

from reqpilot.domain.enums import ChunkStrategy, Role, TextOrigin
from reqpilot.domain.ids import ProjectId
from reqpilot.repositories.knowledge import KnowledgeBaseRepository
from reqpilot.retrieval.contracts import RetrievalOutcome, RetrievalQuery, require_grounding
from reqpilot.retrieval.embeddings import HashingEmbeddingProvider
from reqpilot.services.knowledge import (
    EvidenceService,
    ItemSpec,
    RetrievalService,
    seed_from_manifest,
)

pytestmark = pytest.mark.workflow

MANIFEST = Path(__file__).resolve().parents[2] / "data" / "dev" / "kb_synthetic" / "manifest.yaml"


def test_curated_source_to_resolvable_citation(pg_session: Session, retrieval_rules) -> None:
    session = pg_session
    project = make_project(session, "Loan Origination")
    kb_admin = actor(project.id, Role.KB_ADMIN)
    analyst = actor(project.id, Role.ANALYST)
    admin = admin_service(session, kb_admin, retrieval_rules)
    repo = KnowledgeBaseRepository(session, kb_admin)

    # 1. Curated source -> knowledge items (the fictional development corpus).
    seeded = seed_from_manifest(MANIFEST, admin, repo)
    assert seeded.items_added == 7

    # 2. Knowledge version: the access-review clause is tightened; v1 is superseded.
    v1 = repo.versions_of("SYN-ACME-AC-2")[0]
    v2 = admin.version_item(
        v1.id,
        ItemSpec(
            text=v1.text.replace("at least quarterly", "at least monthly"),
            text_origin=TextOrigin.SYNTHETIC,
            title=v1.title,
            clause_ref=v1.clause_ref,
            applicability=tuple(v1.applicability),
        ),
        reason="review cycle tightened (fictional)",
    )
    assert (v2.version_no, v1.status.value) == (2, "superseded")

    # 3. Chunking with offsets; 4. embedding; 5. stored in PostgreSQL + pgvector.
    chunks = chunks_of(session, v2)
    assert [c.structure_label for c in chunks] == ["2", "2.1", "2.2", "2.3"]
    assert all(c.strategy is ChunkStrategy.CLAUSE for c in chunks)
    assert all(v2.text[c.char_start : c.char_end] == c.text for c in chunks)
    stored_dim = session.scalar(
        text("SELECT vector_dims(embedding) FROM knowledge_chunk WHERE id = :i"),
        {"i": chunks[0].id},
    )
    assert stored_dim == 384

    # 6. Allowlist + jurisdiction + effective date: IN only; the SG branch policy
    #    is allowlisted but out of jurisdiction, so it must not appear.
    sources = {s.title: s for s in repo.list_sources()}
    allowlist(
        session,
        kb_admin,
        project,
        sources["Acme Bank Access Control Policy (fictional)"],
        sources["Acme Bank Data Retention Policy (fictional)"],
        sources["Acme Bank Singapore Branch Incident Logging Standard (fictional)"],
        jurisdictions=["IN"],
    )

    # 7. Hybrid vector + keyword retrieval -> 8. ranked chunk.
    result = RetrievalService(
        session, analyst, embedder=HashingEmbeddingProvider(), rules=retrieval_rules
    ).retrieve(
        RetrievalQuery(
            project_id=project.id, text="access rights loan origination system reviewed data owner"
        )
    )
    require_grounding(result)
    top = result.chunks[0]
    assert (top.item_key, top.item_version_no, top.structure_label) == ("SYN-ACME-AC-2", 2, "2.2")
    assert "monthly" in top.text, "the current version, not the superseded one"
    assert top.vector_rank is not None and top.keyword_rank is not None, "both rankings agreed"
    assert all(c.jurisdiction == "IN" for c in result.chunks)
    assert all(c.item_key != "SYN-ACME-SG-INC-1" for c in result.chunks)

    # 9. Evidence -> 10. resolvable citation, for every chunk supplied.
    rows = EvidenceService(session, analyst).record(result)
    allowed = {r.id for r in rows}
    for row in rows:
        citation = EvidenceService(session, analyst).resolve_citation(
            ProjectId(project.id), row.id, allowed_evidence_ids=allowed
        )
        item_text = repo.get_item(citation.knowledge_item_id).text  # type: ignore[union-attr]
        assert citation.quote == item_text[citation.char_start : citation.char_end]
        assert citation.source_type.value == "org_policy" and citation.binding
        assert citation.retrieved_at and citation.jurisdiction == "IN"

    # The KB moves on; the evidence still resolves to exactly what was supplied.
    admin.retire_item(v2.id, reason="policy withdrawn (fictional)")
    again = EvidenceService(session, analyst).describe(ProjectId(project.id), rows[0].id)
    assert again.quote == top.text and again.item_status_now == "superseded"


def test_security_demonstration_non_allowlisted_stronger_match_is_not_returned(
    pg_session: Session, retrieval_rules
) -> None:
    session = pg_session
    project_a = make_project(session, "Project A")
    kb_admin = actor(project_a.id, Role.KB_ADMIN)
    analyst = actor(project_a.id, Role.ANALYST)
    admin = admin_service(session, kb_admin, retrieval_rules)

    source_x = synthetic_source(admin, "Source X")
    source_y = synthetic_source(admin, "Source Y")
    synthetic_item(admin, source_x, "SYN-DEMO-X", "Customer data is encrypted at rest.")
    synthetic_item(
        admin, source_y, "SYN-DEMO-Y",
        "Customer data access is reviewed quarterly under least privilege approval.",
    )  # fmt: skip
    allowlist(session, kb_admin, project_a, source_x)  # X allowlisted, Y not

    query = "customer data access reviewed quarterly least privilege approval"
    service = RetrievalService(
        session, analyst, embedder=HashingEmbeddingProvider(), rules=retrieval_rules
    )
    result = service.retrieve(RetrievalQuery(project_id=project_a.id, text=query, top_k=1))

    assert result.outcome is RetrievalOutcome.SUCCESS
    assert all(c.normative_source_id != source_y.id for c in result.chunks), (
        "Y MUST NOT be returned"
    )
    assert [c.item_key for c in result.chunks] == ["SYN-DEMO-X"], "X may be returned; it is"
