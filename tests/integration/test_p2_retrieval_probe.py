"""The ET-06 retrieval probe harness (approved Phase 0 H.2: recall@5 >= 0.80, 20 questions).

These tests check the *harness*: that recall@k is computed correctly through the
real retrieval path, and that a result which cannot honestly count toward ET-06
says so. They do not - and cannot - produce the ET-06 measurement, which needs
the curated corpus and a probe written against it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session
from tests.kb_helpers import actor, admin_service, allowlist, make_project

from reqpilot.domain.enums import Role
from reqpilot.repositories.knowledge import KnowledgeBaseRepository
from reqpilot.retrieval.embeddings import HashingEmbeddingProvider
from reqpilot.services.evaluation.retrieval_probe import (
    ET06_MIN_QUESTIONS,
    ET06_TARGET,
    ProbeQuestion,
    RetrievalProbe,
    run_probe,
)
from reqpilot.services.knowledge import RetrievalService, seed_from_manifest

MANIFEST = Path(__file__).resolve().parents[2] / "data" / "dev" / "kb_synthetic" / "manifest.yaml"

SYNTHETIC_PROBE = RetrievalProbe(
    name="synthetic-dev-probe",
    synthetic=True,
    questions=(
        ProbeQuestion(
            question_id="q1",
            query="access rights reviewed quarterly by the data owner",
            expected_item_keys=frozenset({"SYN-ACME-AC-2"}),
        ),
        ProbeQuestion(
            question_id="q2",
            query="administrator accounts multi-factor authentication",
            expected_item_keys=frozenset({"SYN-ACME-AC-3"}),
        ),
        ProbeQuestion(
            question_id="q3",
            query="fee summary annual percentage rate before sanction",
            expected_item_keys=frozenset({"SYN-ACME-COM-1"}),
        ),
        ProbeQuestion(
            question_id="q4",
            query="complaint acknowledged reference number",
            expected_item_keys=frozenset({"SYN-ACME-COM-2"}),
        ),
        ProbeQuestion(
            question_id="q5",
            query="retention period loan application records deleted anonymised",
            expected_item_keys=frozenset({"SYN-ACME-RET-1"}),
        ),
    ),
)


@pytest.mark.unit
def test_the_targets_are_the_approved_et06() -> None:
    assert (ET06_TARGET, ET06_MIN_QUESTIONS) == (0.80, 20)


@pytest.mark.unit
def test_the_probe_digest_ignores_set_ordering() -> None:
    a = ProbeQuestion(question_id="q", query="x", expected_item_keys=frozenset({"A", "B", "C"}))
    b = ProbeQuestion(question_id="q", query="x", expected_item_keys=frozenset({"C", "A", "B"}))
    assert (
        RetrievalProbe(name="p", synthetic=True, questions=(a,)).digest
        == RetrievalProbe(name="p", synthetic=True, questions=(b,)).digest
    )
    changed = ProbeQuestion(question_id="q", query="y", expected_item_keys=frozenset({"A"}))
    assert RetrievalProbe(name="p", synthetic=True, questions=(changed,)).digest != (
        RetrievalProbe(name="p", synthetic=True, questions=(a,)).digest
    )


@pytest.mark.unit
def test_a_probe_question_must_name_what_counts_as_a_hit() -> None:
    with pytest.raises(ValueError):
        ProbeQuestion(question_id="q", query="x", expected_item_keys=frozenset())


@pytest.fixture
def seeded(pg_session: Session, retrieval_rules):
    project = make_project(pg_session, "Probe project")
    kb_admin = actor(project.id, Role.KB_ADMIN)
    admin = admin_service(pg_session, kb_admin, retrieval_rules)
    repo = KnowledgeBaseRepository(pg_session, kb_admin)
    seed_from_manifest(MANIFEST, admin, repo)
    allowlist(
        pg_session, kb_admin, project, *repo.list_sources(), jurisdictions=["IN", "SG", "INTL"]
    )
    return project


@pytest.mark.integration
def test_recall_is_computed_through_real_retrieval(
    pg_session: Session, retrieval_rules, seeded
) -> None:
    service = RetrievalService(
        pg_session,
        actor(seeded.id, Role.ANALYST),
        embedder=HashingEmbeddingProvider(),
        rules=retrieval_rules,
    )
    report = run_probe(SYNTHETIC_PROBE, service, project_id=seeded.id)
    assert report.questions == 5
    assert report.hits == sum(r.hit for r in report.results)
    assert report.recall_at_k == pytest.approx(report.hits / 5)
    for result in report.results:
        assert len(result.retrieved_item_keys) <= 5
        if result.hit:
            assert result.first_hit_rank is not None and 1 <= result.first_hit_rank <= 5
    assert report.probe_digest == SYNTHETIC_PROBE.digest
    assert report.ruleset_version == retrieval_rules.version


@pytest.mark.integration
def test_a_synthetic_small_probe_on_the_test_provider_never_counts_toward_et06(
    pg_session: Session, retrieval_rules, seeded
) -> None:
    service = RetrievalService(
        pg_session,
        actor(seeded.id, Role.ANALYST),
        embedder=HashingEmbeddingProvider(),
        rules=retrieval_rules,
    )
    report = run_probe(SYNTHETIC_PROBE, service, project_id=seeded.id)
    assert report.counts_toward_et06 is False
    assert report.meets_target is False, "never claimed, whatever the recall"
    reasons = " | ".join(report.not_counted_because)
    assert "synthetic" in reasons
    assert "needs 20" in reasons
    assert "not the approved" in reasons


@pytest.mark.integration
def test_ranking_away_from_k5_is_flagged(pg_session: Session, retrieval_rules, seeded) -> None:
    service = RetrievalService(
        pg_session,
        actor(seeded.id, Role.ANALYST),
        embedder=HashingEmbeddingProvider(),
        rules=retrieval_rules,
    )
    report = run_probe(SYNTHETIC_PROBE, service, project_id=seeded.id, k=3)
    assert any("k=3" in r for r in report.not_counted_because)
