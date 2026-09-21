"""The approved embedding model, for real: ``BAAI/bge-small-en-v1.5`` on the local CPU.

Offline by construction (ADR-005, ET-10): the model is loaded only from the
local cache, with the network disabled. If the ``embeddings`` extra is not
installed or the model is not cached, every test here **skips** with the
reason - it never downloads, and never silently substitutes another provider.

The probe at the end runs the whole retrieval path with the real model over the
synthetic development corpus. It is a harness check reported as synthetic, not
the ET-06 measurement.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session
from tests.integration.test_p2_retrieval_probe import SYNTHETIC_PROBE
from tests.kb_helpers import actor, admin_service, allowlist, make_project

from reqpilot.domain.enums import Role
from reqpilot.domain.errors import EmbeddingUnavailableError
from reqpilot.repositories.knowledge import KnowledgeBaseRepository
from reqpilot.retrieval.contracts import EmptyReason, RetrievalOutcome, RetrievalQuery
from reqpilot.retrieval.embeddings import (
    APPROVED_EMBEDDING_MODEL,
    SentenceTransformerEmbeddingProvider,
    cosine_similarity,
)
from reqpilot.services.evaluation.retrieval_probe import run_probe
from reqpilot.services.knowledge import RetrievalService, seed_from_manifest

pytestmark = pytest.mark.integration

MANIFEST = Path(__file__).resolve().parents[2] / "data" / "dev" / "kb_synthetic" / "manifest.yaml"


@pytest.fixture(scope="module")
def model() -> SentenceTransformerEmbeddingProvider:
    provider = SentenceTransformerEmbeddingProvider(allow_download=False)
    try:
        provider.embed_query("warm-up")
    except EmbeddingUnavailableError as exc:
        pytest.skip(f"approved embedding model not available offline: {exc}")
    return provider


def test_the_approved_model_loads_offline_with_the_schema_dimension(model) -> None:
    vector = model.embed_query("customer data access review")
    assert model.model_id == APPROVED_EMBEDDING_MODEL
    assert len(vector) == 384
    assert sum(x * x for x in vector) == pytest.approx(1.0, abs=1e-4)


def test_queries_carry_the_bge_instruction_and_passages_do_not(model) -> None:
    text = "access rights are reviewed quarterly"
    assert model.embed_query(text) != model.embed_documents([text])[0]


def test_the_model_is_semantic_where_the_test_provider_is_not(model) -> None:
    """A paraphrase with no shared content words is closer than an unrelated sentence."""
    query = model.embed_query("who is allowed to see client information")
    paraphrase = model.embed_documents(
        ["Access to customer personal data is restricted to authorised staff."]
    )[0]
    unrelated = model.embed_documents(["The cafeteria menu changes every Tuesday."])[0]
    assert cosine_similarity(query, paraphrase) > cosine_similarity(query, unrelated)


@pytest.fixture
def real_seeded(pg_session: Session, retrieval_rules, model):
    project = make_project(pg_session, "Real-model project")
    kb_admin = actor(project.id, Role.KB_ADMIN)
    admin = admin_service(pg_session, kb_admin, retrieval_rules, embedder=model)
    repo = KnowledgeBaseRepository(pg_session, kb_admin)
    seed_from_manifest(MANIFEST, admin, repo)
    allowlist(
        pg_session, kb_admin, project, *repo.list_sources(), jurisdictions=["IN", "SG", "INTL"]
    )
    return project


def test_retrieval_with_the_real_model_on_postgres(
    pg_session: Session, retrieval_rules, model, real_seeded
) -> None:
    service = RetrievalService(
        pg_session, actor(real_seeded.id, Role.ANALYST), embedder=model, rules=retrieval_rules
    )
    result = service.retrieve(
        RetrievalQuery(project_id=real_seeded.id, text="how long are loan applications kept")
    )
    assert result.outcome is RetrievalOutcome.SUCCESS
    assert result.embedding_model == APPROVED_EMBEDDING_MODEL
    assert result.chunks[0].item_key == "SYN-ACME-RET-1"


def test_an_unrelated_question_is_escalated_with_the_real_model(
    pg_session: Session, retrieval_rules, model, real_seeded
) -> None:
    """FR-RAG-005 with the model's own similarity range and the ruleset's threshold."""
    service = RetrievalService(
        pg_session, actor(real_seeded.id, Role.ANALYST), embedder=model, rules=retrieval_rules
    )
    result = service.retrieve(
        RetrievalQuery(
            project_id=real_seeded.id, text="gluon colour confinement in quantum chromodynamics"
        )
    )
    assert (result.outcome, result.empty_reason) == (
        RetrievalOutcome.EMPTY,
        EmptyReason.NOTHING_RELEVANT,
    )


def test_synthetic_probe_with_the_real_model(
    pg_session: Session, retrieval_rules, model, real_seeded
) -> None:
    """Harness check with the approved model. Synthetic and 5 questions: never counts as ET-06."""
    service = RetrievalService(
        pg_session, actor(real_seeded.id, Role.ANALYST), embedder=model, rules=retrieval_rules
    )
    report = run_probe(SYNTHETIC_PROBE, service, project_id=real_seeded.id)
    assert report.embedding_model == APPROVED_EMBEDDING_MODEL
    assert report.counts_toward_et06 is False and report.meets_target is False
    assert not any("not the approved" in r for r in report.not_counted_because)
    assert report.recall_at_k == 1.0, [
        (r.question_id, r.retrieved_item_keys) for r in report.results if not r.hit
    ]
