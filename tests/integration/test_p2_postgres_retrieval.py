"""Retrieval against a live PostgreSQL + pgvector (architecture ADR-003, ADR-004, J.4).

These tests exercise what SQLite cannot: the ``<=>`` cosine operator, the HNSW
and full-text indexes, ``websearch_to_tsquery``, reciprocal rank fusion over real
candidates, array-overlap applicability, and the immutability triggers.

They skip - visibly - without ``REQPILOT_TEST_DATABASE_URL``. Every test runs in
a transaction that is rolled back, so nothing persists.

Embeddings come from the deterministic offline provider, whose similarity
reflects shared vocabulary. That makes "which chunk is the better match"
controllable and exact, which is what filtering and ranking tests need. The
approved model is exercised separately in ``test_p2_real_embeddings.py``.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import Enum as SAEnum
from sqlalchemy import select, text
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

from reqpilot.domain.enums import NormativeSourceType, Role, TextOrigin
from reqpilot.domain.errors import KnowledgeBaseError, ProjectIsolationError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models import Base
from reqpilot.domain.models.knowledge import SourceAllowlist
from reqpilot.repositories.knowledge import RetrievalRepository
from reqpilot.retrieval.contracts import (
    EmptyReason,
    QueryClassification,
    RetrievalOutcome,
    RetrievalQuery,
)
from reqpilot.retrieval.embeddings import HashingEmbeddingProvider, cosine_similarity
from reqpilot.services.knowledge import EvidenceService, ItemSpec, RetrievalService

pytestmark = pytest.mark.integration

#: Source Y - the semantically best match - is NOT on project A's allowlist.
BEST_MATCH_TEXT = "Customer data access is reviewed quarterly under least privilege approval."
#: Source X - a weaker match - IS on project A's allowlist.
WEAKER_TEXT = "Customer data is encrypted at rest."
QUERY = "customer data access reviewed quarterly least privilege approval"


@pytest.fixture
def world(pg_session: Session, retrieval_rules):
    session = pg_session
    a = make_project(session, "Project A")
    b = make_project(session, "Project B")
    kb_admin = actor(a.id, Role.KB_ADMIN)
    kb_admin.roles_by_project[b.id] = frozenset({Role.KB_ADMIN})
    admin = admin_service(session, kb_admin, retrieval_rules)
    x = synthetic_source(admin, "Source X encryption policy")
    y = synthetic_source(admin, "Source Y access policy")
    x_item = synthetic_item(admin, x, "SYN-X-1", WEAKER_TEXT)
    y_item = synthetic_item(admin, y, "SYN-Y-1", BEST_MATCH_TEXT)
    allowlist(session, kb_admin, a, x)  # project A: X only
    allowlist(session, kb_admin, b, x, y)  # project B: both - the control case
    return {
        "session": session,
        "rules": retrieval_rules,
        "a": a,
        "b": b,
        "kb_admin": kb_admin,
        "admin": admin,
        "x": x,
        "y": y,
        "x_item": x_item,
        "y_item": y_item,
        "analyst_a": actor(a.id, Role.ANALYST),
        "analyst_b": actor(b.id, Role.ANALYST),
    }


def retrieval(world, who: str = "analyst_a") -> RetrievalService:
    return RetrievalService(
        world["session"], world[who], embedder=HashingEmbeddingProvider(), rules=world["rules"]
    )


def run(world, project, query: str = QUERY, who: str = "analyst_a", **kwargs):
    return retrieval(world, who).retrieve(
        RetrievalQuery(project_id=project.id, text=query, **kwargs)
    )


def keys(result) -> list[str]:
    return [c.item_key for c in result.chunks]


# ---------------------------------------------------------------------------
# The critical adversarial case (P2 exit test: "a non-allowlisted source is rejected")
# ---------------------------------------------------------------------------


def test_control_case_y_really_is_the_better_match(world) -> None:
    """With both sources allowlisted (project B), Y outranks X - so Y is the stronger match."""
    result = run(world, world["b"], who="analyst_b")
    assert keys(result)[:2] == ["SYN-Y-1", "SYN-X-1"]
    y, x = result.chunks[0], result.chunks[1]
    assert y.vector_similarity > x.vector_similarity


def test_a_non_allowlisted_best_match_is_never_returned(world) -> None:
    """Project A allowlists X only. Y matches the query more strongly. Y must not appear."""
    result = run(world, world["a"])
    assert result.outcome is RetrievalOutcome.SUCCESS
    assert keys(result) == ["SYN-X-1"]
    assert all(c.normative_source_id != world["y"].id for c in result.chunks)


def test_exclusion_happens_before_limit_not_after(world) -> None:
    """With top_k=1, post-filtering would have spent the only slot on Y and returned nothing.

    Returning X proves the allowlist restricted the candidates *inside* the query,
    before LIMIT - not in Python afterwards.
    """
    result = run(world, world["a"], top_k=1)
    assert keys(result) == ["SYN-X-1"]


def test_the_database_itself_returns_no_row_for_the_non_allowlisted_source(world) -> None:
    """Inspect the raw rows the SQL returns: Y's chunks are not among them at all."""
    session = world["session"]
    repo = RetrievalRepository(session, world["analyst_a"])
    scope = repo.load_scope(ProjectId(world["a"].id), dt.date.today())
    embedder = HashingEmbeddingProvider()
    common = {"source_types": frozenset(), "applicability": frozenset(), "limit": 1}
    vector_rows = repo.vector_candidates(
        scope,
        embedder.embed_query(QUERY),
        embedding_model=embedder.model_id,
        min_similarity=-1.0,
        **common,
    )
    keyword_rows = repo.keyword_candidates(
        scope, "customer data", embedding_model=embedder.model_id, **common
    )
    y_chunks = {c.id for c in chunks_of(session, world["y_item"])}
    x_chunks = {c.id for c in chunks_of(session, world["x_item"])}
    assert {cid for cid, _ in vector_rows} == x_chunks
    assert {cid for cid, _ in keyword_rows} == x_chunks
    assert not ({cid for cid, _ in vector_rows} | {cid for cid, _ in keyword_rows}) & y_chunks


def test_classification_cannot_widen_the_allowlist(world) -> None:
    """Naming Y's source type and applicability still cannot reach Y."""
    result = run(
        world,
        world["a"],
        classification=QueryClassification(
            source_types=frozenset({NormativeSourceType.ORG_POLICY})
        ),
    )
    assert "SYN-Y-1" not in keys(result)


# ---------------------------------------------------------------------------
# pgvector, full text and hybrid ranking
# ---------------------------------------------------------------------------


def test_the_embedding_column_and_indexes_exist(world) -> None:
    session = world["session"]
    column_type = session.scalar(
        text(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = 'knowledge_chunk'::regclass AND attname = 'embedding'"
        )
    )
    assert column_type == "vector(384)"
    indexes = dict(
        session.execute(
            text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'knowledge_chunk'")
        ).all()
    )
    assert (
        "USING hnsw (embedding vector_cosine_ops)" in indexes["ix_knowledge_chunk_embedding_hnsw"]
    )
    assert "to_tsvector('english'::regconfig, text)" in indexes["ix_knowledge_chunk_text_fts"]


def test_vector_ranking_orders_by_cosine_similarity(world) -> None:
    result = run(world, world["b"], who="analyst_b")
    sims = [c.vector_similarity for c in result.chunks]
    assert sims == sorted(sims, reverse=True)


def test_full_text_finds_exact_terms_the_vectors_blur(world) -> None:
    """A chunk far away in vector space is still found by keyword (the ADR-004 rationale)."""
    query = "grievance escalation matrix"
    filler = " ".join(f"filler{i}" for i in range(480))
    body = f"The grievance escalation matrix is attached. {filler}"
    embedder = HashingEmbeddingProvider()
    similarity = cosine_similarity(embedder.embed_query(query), embedder.embed_documents([body])[0])
    threshold = world["rules"].min_similarity_for(embedder.model_id)
    assert similarity < threshold, "precondition: the vector ranking alone would not find it"

    long_item = synthetic_item(world["admin"], world["x"], "SYN-X-2", body)
    assert len(chunks_of(world["session"], long_item)) == 1
    result = run(world, world["a"], query)
    assert result.outcome is RetrievalOutcome.SUCCESS
    hit = next(c for c in result.chunks if c.knowledge_item_id == long_item.id)
    assert hit.keyword_rank == 1
    assert hit.vector_rank is None, "below the vector threshold: found by keyword alone"


def test_hybrid_fusion_is_reciprocal_rank_fusion_over_both_rankings(world) -> None:
    """Every fused score is exactly the RRF sum of the ranks the chunk held (J.4)."""
    result = run(world, world["b"], "customer data access", who="analyst_b")
    k = world["rules"].rrf_k
    weights = world["rules"].weights
    assert any(c.vector_rank and c.keyword_rank for c in result.chunks), "both rankings contributed"
    for chunk in result.chunks:
        expected = sum(
            weights[name] / (k + rank)
            for name, rank in (("vector", chunk.vector_rank), ("keyword", chunk.keyword_rank))
            if rank is not None
        )
        assert chunk.fused_score == pytest.approx(expected)
    scores = [c.fused_score for c in result.chunks]
    assert scores == sorted(scores, reverse=True)
    # The chunk matching every query term outranks the one that matches only in vector space.
    assert result.chunks[0].knowledge_item_id == world["y_item"].id


def test_every_returned_chunk_carries_full_provenance(world) -> None:
    chunk = run(world, world["a"]).chunks[0]
    assert chunk.source_title == "Source X encryption policy (fictional)"
    assert chunk.source_type is NormativeSourceType.ORG_POLICY
    assert chunk.binding == "Binding inside one organisation"
    assert chunk.issuing_body and chunk.jurisdiction == "IN" and chunk.source_version == "2026.1"
    assert chunk.effective_date == dt.date(2025, 1, 1) and chunk.retrieved_at
    item_text = world["x_item"].text
    assert item_text[chunk.char_start : chunk.char_end] == chunk.text


# ---------------------------------------------------------------------------
# Jurisdiction, effective date, status, pin, model
# ---------------------------------------------------------------------------


def test_a_source_outside_the_projects_jurisdictions_is_excluded(world) -> None:
    sg = synthetic_source(world["admin"], "Singapore branch policy", jurisdiction="SG")
    synthetic_item(world["admin"], sg, "SYN-SG-1", BEST_MATCH_TEXT + " Singapore.")
    allowlist(world["session"], world["kb_admin"], world["a"], sg, jurisdictions=["IN"])
    assert "SYN-SG-1" not in keys(run(world, world["a"]))
    world["session"].execute(
        text("UPDATE project SET jurisdiction_scope = ARRAY['IN','SG'] WHERE id = :p"),
        {"p": world["a"].id},
    )
    world["session"].expire_all()
    assert keys(run(world, world["a"]))[0] == "SYN-SG-1"


def test_a_source_not_yet_in_force_is_excluded(world) -> None:
    future = synthetic_source(
        world["admin"], "Future policy", effective_date=dt.date.today() + dt.timedelta(days=30)
    )
    synthetic_item(world["admin"], future, "SYN-FUT-1", BEST_MATCH_TEXT + " Future.")
    world["session"].add(SourceAllowlist(project_id=world["a"].id, normative_source_id=future.id))
    world["session"].flush()
    assert "SYN-FUT-1" not in keys(run(world, world["a"]))


def test_as_of_evaluates_effective_dates_historically(world) -> None:
    assert keys(run(world, world["a"], as_of=dt.date(2024, 12, 31))) == []
    assert keys(run(world, world["a"], as_of=dt.date(2025, 1, 1))) == ["SYN-X-1"]


def test_as_of_in_the_future_is_refused(world) -> None:
    with pytest.raises(KnowledgeBaseError, match="future"):
        run(world, world["a"], as_of=dt.date.today() + dt.timedelta(days=1))


def test_retired_items_are_not_retrieved(world) -> None:
    world["admin"].retire_item(world["x_item"].id, reason="withdrawn")
    result = run(world, world["a"])
    assert result.outcome is RetrievalOutcome.EMPTY
    assert result.empty_reason is EmptyReason.NOTHING_RELEVANT


def test_only_the_current_version_is_retrieved(world) -> None:
    v2 = world["admin"].version_item(
        world["x_item"].id,
        ItemSpec(
            text="Customer data access is encrypted and reviewed.", text_origin=TextOrigin.SYNTHETIC
        ),
        reason="clarified",
    )
    result = run(world, world["a"])
    assert {c.knowledge_item_id for c in result.chunks} == {v2.id}
    assert result.chunks[0].item_version_no == 2


def test_a_pinned_project_keeps_seeing_what_it_saw(world) -> None:
    """J.6: a mid-project KB update does not silently change a pinned project's retrieval."""
    pin = world["x_item"].kb_version
    world["session"].execute(
        text("UPDATE project SET kb_version_pin = :v WHERE id = :p"),
        {"v": pin + 1, "p": world["a"].id},
    )
    world["session"].expire_all()
    before = run(world, world["a"])
    v2 = world["admin"].version_item(
        world["x_item"].id,
        ItemSpec(
            text="Customer data access is encrypted and reviewed.", text_origin=TextOrigin.SYNTHETIC
        ),
        reason="clarified",
    )
    added = synthetic_item(
        world["admin"], world["x"], "SYN-X-3", "Customer data access log retention."
    )
    after = run(world, world["a"])
    assert after.kb_version_pinned and after.kb_version == pin + 1
    assert {c.knowledge_item_id for c in after.chunks} == {
        c.knowledge_item_id for c in before.chunks
    }
    assert v2.id not in {c.knowledge_item_id for c in after.chunks}
    assert added.id not in {c.knowledge_item_id for c in after.chunks}
    # Unpinned, the same project sees the new versions.
    world["session"].execute(
        text("UPDATE project SET kb_version_pin = NULL WHERE id = :p"), {"p": world["a"].id}
    )
    world["session"].expire_all()
    current = {c.knowledge_item_id for c in run(world, world["a"]).chunks}
    assert v2.id in current and world["x_item"].id not in current


def test_chunks_from_another_embedding_model_are_never_compared(world) -> None:
    class OtherModel(HashingEmbeddingProvider):
        @property
        def model_id(self) -> str:
            return "other/model-384"

    other_admin = admin_service(
        world["session"], world["kb_admin"], world["rules"], embedder=OtherModel()
    )
    synthetic_item(other_admin, world["x"], "SYN-X-OTHER", BEST_MATCH_TEXT + " Other model.")
    assert "SYN-X-OTHER" not in keys(run(world, world["a"]))


def test_applicability_narrows_and_untagged_items_stay_general(world) -> None:
    tagged = synthetic_item(
        world["admin"],
        world["x"],
        "SYN-X-PRIV",
        "Customer data access for privacy review.",
        applicability=["privacy"],
    )
    narrowed = run(
        world,
        world["a"],
        "customer data access",
        classification=QueryClassification(applicability=frozenset({"security"})),
    )
    assert tagged.id not in {c.knowledge_item_id for c in narrowed.chunks}
    assert "SYN-X-1" in keys(narrowed), "an item with no applicability tags applies generally"
    matched = run(
        world,
        world["a"],
        "customer data access",
        classification=QueryClassification(applicability=frozenset({"privacy"})),
    )
    assert tagged.id in {c.knowledge_item_id for c in matched.chunks}


def test_source_type_classification_narrows(world) -> None:
    result = run(
        world,
        world["a"],
        classification=QueryClassification(
            source_types=frozenset({NormativeSourceType.BEST_PRACTICE})
        ),
    )
    assert result.outcome is RetrievalOutcome.EMPTY


# ---------------------------------------------------------------------------
# Explicit empty outcomes (FR-RAG-005) and isolation
# ---------------------------------------------------------------------------


def test_no_jurisdiction_scope_is_an_explicit_empty_outcome(world) -> None:
    world["session"].execute(
        text("UPDATE project SET jurisdiction_scope = '{}' WHERE id = :p"), {"p": world["a"].id}
    )
    world["session"].expire_all()
    result = run(world, world["a"])
    assert (result.outcome, result.empty_reason) == (
        RetrievalOutcome.EMPTY,
        EmptyReason.NO_JURISDICTION_SCOPE,
    )
    assert result.requires_human_review


def test_no_allowlist_is_an_explicit_empty_outcome(world) -> None:
    c = make_project(world["session"], "Project C", jurisdictions=["IN"])
    result = RetrievalService(
        world["session"],
        actor(c.id, Role.ANALYST),
        embedder=HashingEmbeddingProvider(),
        rules=world["rules"],
    ).retrieve(RetrievalQuery(project_id=c.id, text=QUERY))
    assert (result.outcome, result.empty_reason) == (
        RetrievalOutcome.EMPTY,
        EmptyReason.NO_ALLOWLISTED_SOURCES,
    )


def test_nothing_relevant_is_an_explicit_empty_outcome(world) -> None:
    result = run(world, world["a"], "helicopter rotor maintenance schedule")
    assert (result.outcome, result.empty_reason) == (
        RetrievalOutcome.EMPTY,
        EmptyReason.NOTHING_RELEVANT,
    )
    assert result.chunks == () and result.requires_human_review


#: Tables that a human-review workflow would write to. An empty retrieval must
#: touch none of them: P2 *reports* the outcome, and the escalation itself belongs
#: to the later orchestration phase that consumes it (retrieval -> explicit empty
#: outcome -> later phase escalates).
WORKFLOW_TABLES = (
    "approval_task",
    "approval_decision",
    "audit_event",
    "graph_run",
    "agent_run",
    "evidence",
)


def workflow_row_counts(session: Session) -> dict[str, int]:
    return {
        table: session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
        for table in WORKFLOW_TABLES
    }


def reproducible(result) -> dict:
    """Everything about a result except its fresh per-call id."""
    return result.model_dump(exclude={"retrieval_id"})


def test_each_empty_outcome_is_deterministic_and_starts_no_workflow(world) -> None:
    c = make_project(world["session"], "Project C", jurisdictions=["IN"])
    world["analyst_c"] = actor(c.id, Role.ANALYST)

    def empty_cases():
        yield EmptyReason.NOTHING_RELEVANT, run(world, world["a"], "helicopter rotor maintenance")
        yield EmptyReason.NO_ALLOWLISTED_SOURCES, run(world, c, who="analyst_c")

    before = workflow_row_counts(world["session"])
    first = list(empty_cases())
    second = list(empty_cases())

    world["session"].execute(
        text("UPDATE project SET jurisdiction_scope = '{}' WHERE id = :p"), {"p": world["a"].id}
    )
    world["session"].expire_all()
    unscoped = [run(world, world["a"]), run(world, world["a"])]

    for (reason, one), (_, two) in zip(first, second, strict=True):
        assert (one.outcome, one.empty_reason) == (RetrievalOutcome.EMPTY, reason)
        assert reproducible(one) == reproducible(two), "same inputs, same outcome"
    assert unscoped[0].empty_reason is EmptyReason.NO_JURISDICTION_SCOPE
    assert reproducible(unscoped[0]) == reproducible(unscoped[1])

    # The flag is the whole of P2's escalation: no task, decision, run,
    # evidence or audit record is created by an empty outcome.
    assert all(r.requires_human_review for r in [*(r for _, r in first), *unscoped])
    assert workflow_row_counts(world["session"]) == before


def test_a_successful_retrieval_is_deterministic(world) -> None:
    one, two = run(world, world["b"], who="analyst_b"), run(world, world["b"], who="analyst_b")
    assert one.outcome is RetrievalOutcome.SUCCESS
    assert reproducible(one) == reproducible(two)
    assert one.retrieval_id != two.retrieval_id, "each call is still its own retrieval"


def test_retrieving_in_another_project_is_refused(world) -> None:
    with pytest.raises(ProjectIsolationError):
        retrieval(world, "analyst_a").retrieve(RetrievalQuery(project_id=world["b"].id, text=QUERY))


def test_a_retrieval_names_everything_needed_to_reproduce_it(world) -> None:
    result = run(world, world["a"])
    assert result.embedding_model == "reqpilot/hashing-384-v1"
    assert result.ruleset_version == world["rules"].version
    assert result.kb_version == world["admin"]._repo.current_kb_version()
    assert len(result.query_hash) == 64 and not result.kb_version_pinned


# ---------------------------------------------------------------------------
# Evidence end to end, on PostgreSQL
# ---------------------------------------------------------------------------


def test_retrieved_chunks_become_evidence_and_resolve(world) -> None:
    result = run(world, world["a"])
    rows = EvidenceService(world["session"], world["analyst_a"]).record(result)
    citation = EvidenceService(world["session"], world["analyst_a"]).resolve_citation(
        ProjectId(world["a"].id), rows[0].id, allowed_evidence_ids={r.id for r in rows}
    )
    assert citation.quote == result.chunks[0].text
    assert citation.retrieval_id == result.retrieval_id


# ---------------------------------------------------------------------------
# Database-level immutability (the second layer)
# ---------------------------------------------------------------------------


@pytest.fixture
def evidence_row(world):
    result = run(world, world["a"])
    return EvidenceService(world["session"], world["analyst_a"]).record(result)[0]


def refused(session: Session, sql: str, params: dict) -> str:
    """Run a statement that must fail, in a savepoint so the test can continue."""
    with pytest.raises(Exception) as excinfo, session.begin_nested():
        session.execute(text(sql), params)
    return str(excinfo.value).lower()


def test_evidence_cannot_be_updated_or_deleted(world, evidence_row) -> None:
    s = world["session"]
    assert "append-only" in refused(
        s, "UPDATE evidence SET quote = 'x' WHERE id = :i", {"i": evidence_row.id}
    )
    assert "append-only" in refused(s, "DELETE FROM evidence WHERE id = :i", {"i": evidence_row.id})


def test_deleting_a_project_cascades_to_its_evidence(world) -> None:
    """FR-ADM-006's project-level cascade is the one permitted removal path.

    Scope rows are written directly so the project has no audit events: the
    audit trail's own retention on project deletion is a later phase's concern.
    """
    s = world["session"]
    doomed = make_project(s, "Doomed", jurisdictions=["IN"])
    s.add(SourceAllowlist(project_id=doomed.id, normative_source_id=world["x"].id))
    s.flush()
    result = RetrievalService(
        s, actor(doomed.id, Role.ANALYST), embedder=HashingEmbeddingProvider(), rules=world["rules"]
    ).retrieve(RetrievalQuery(project_id=doomed.id, text=QUERY))
    rows = EvidenceService(s, actor(doomed.id, Role.ANALYST)).record(result)
    s.execute(text("DELETE FROM project WHERE id = :p"), {"p": doomed.id})
    remaining = s.scalar(text("SELECT count(*) FROM evidence WHERE id = :i"), {"i": rows[0].id})
    assert remaining == 0


def test_chunks_sources_and_controls_are_append_only(world) -> None:
    s = world["session"]
    chunk = chunks_of(s, world["x_item"])[0]
    assert "append-only" in refused(
        s, "UPDATE knowledge_chunk SET text = 'x' WHERE id = :i", {"i": chunk.id}
    )
    assert "append-only" in refused(s, "DELETE FROM knowledge_chunk WHERE id = :i", {"i": chunk.id})
    assert "append-only" in refused(
        s, "UPDATE normative_source SET title = 'x' WHERE id = :i", {"i": world["x"].id}
    )
    control = world["admin"].add_control(
        world["x"].id, control_ref="C-1", title="t", paraphrase="p"
    )
    assert "append-only" in refused(
        s, "UPDATE control SET title = 'x' WHERE id = :i", {"i": control.id}
    )


def test_item_content_is_immutable_and_supersession_happens_once(world) -> None:
    s = world["session"]
    item_id = world["x_item"].id
    assert "immutable" in refused(
        s, "UPDATE knowledge_item SET text = 'x' WHERE id = :i", {"i": item_id}
    )
    assert "never deleted" in refused(s, "DELETE FROM knowledge_item WHERE id = :i", {"i": item_id})
    world["admin"].retire_item(item_id, reason="withdrawn")
    assert "only move once" in refused(
        s,
        "UPDATE knowledge_item SET status = 'ACTIVE', superseded_in_kb_version = NULL, "
        "supersession_kind = NULL WHERE id = :i",
        {"i": item_id},
    )


# ---------------------------------------------------------------------------
# Schema/enum agreement - the check that would have caught the P1 audit defect
# ---------------------------------------------------------------------------


def _enum_columns() -> list[tuple[str, str, SAEnum]]:
    out = []
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, SAEnum) and column.type.enum_class is not None:
                out.append((table.name, column.name, column.type))
    return out


@pytest.mark.parametrize(
    ("table", "column", "enum_type"), _enum_columns(), ids=lambda v: str(getattr(v, "name", v))
)
def test_every_python_enum_value_is_accepted_by_its_postgres_type(
    world, table, column, enum_type
) -> None:
    labels = set(
        world["session"].scalars(
            text(
                "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                "WHERE t.typname = :n"
            ),
            {"n": enum_type.name},
        )
    )
    # SQLAlchemy stores enum member *names* (no values_callable anywhere).
    expected = {member.name for member in enum_type.enum_class}
    assert expected <= labels, (
        f"{table}.{column}: missing in PostgreSQL {sorted(expected - labels)}"
    )


def test_the_p1_audit_event_types_are_registered(world) -> None:
    """Regression for the P1 defect repaired by migration 0003."""
    labels = set(
        world["session"].scalars(
            text("SELECT unnest(enum_range(NULL::audit_event_type_enum))::text")
        )
    )
    assert {
        "REQUIREMENT_CREATED",
        "APPROVAL_GRANTED",
        "BASELINE_COMMITTED",
        "KB_ITEM_ADDED",
    } <= labels
    gates = set(world["session"].scalars(text("SELECT unnest(enum_range(NULL::gate_enum))::text")))
    assert "G1_REQUIREMENT_BASELINE" in gates and "G1" not in gates


def test_the_fixture_allowlists_are_what_the_adversarial_tests_assume(world) -> None:
    """Guard the premise: Y is allowlisted for B and not for A."""
    rows = set(
        world["session"]
        .execute(select(SourceAllowlist.project_id, SourceAllowlist.normative_source_id))
        .tuples()
    )
    assert (world["a"].id, world["y"].id) not in rows
    assert (world["a"].id, world["x"].id) in rows
    assert {(world["b"].id, world["x"].id), (world["b"].id, world["y"].id)} <= rows
