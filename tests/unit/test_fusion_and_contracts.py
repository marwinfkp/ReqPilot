"""Reciprocal rank fusion, the retrieval contracts, and the retrieval ruleset.

The fusion expectations are computed by hand, so the tests pin the formula
rather than whatever the implementation happens to produce.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from types import MappingProxyType

import pytest

from reqpilot.domain.enums import NormativeSourceType, RequirementCategory
from reqpilot.domain.errors import RuleConfigurationError, UngroundedRetrievalError
from reqpilot.retrieval.contracts import (
    ClassificationOrigin,
    EmptyReason,
    QueryClassification,
    RetrievalOutcome,
    RetrievalQuery,
    RetrievalResult,
    require_grounding,
)
from reqpilot.retrieval.fusion import reciprocal_rank_fusion
from reqpilot.retrieval.rules import RetrievalRules
from reqpilot.rules.loader import RuleSet, _freeze

pytestmark = pytest.mark.unit

W = {"vector": 1.0, "keyword": 1.0}


# --- reciprocal rank fusion ---------------------------------------------------


def test_rrf_matches_the_formula_by_hand() -> None:
    fused = reciprocal_rank_fusion(
        {"vector": ["a", "b", "c"], "keyword": ["c", "a"]}, weights=W, k=60
    )
    scores = {f.candidate_id: f.score for f in fused}
    assert scores["a"] == pytest.approx(1 / 61 + 1 / 62)
    assert scores["b"] == pytest.approx(1 / 62)
    assert scores["c"] == pytest.approx(1 / 63 + 1 / 61)
    assert [f.candidate_id for f in fused] == ["a", "c", "b"]
    assert fused[0].ranks == {"vector": 1, "keyword": 2}


def test_rrf_weights_scale_each_ranking() -> None:
    fused = reciprocal_rank_fusion(
        {"vector": ["a"], "keyword": ["b"]}, weights={"vector": 1.0, "keyword": 3.0}, k=60
    )
    assert [f.candidate_id for f in fused] == ["b", "a"]
    assert fused[0].score == pytest.approx(3 / 61)


def test_rrf_ties_break_deterministically() -> None:
    """Equal scores and equal best ranks fall back to the id, never to set order."""
    fused = reciprocal_rank_fusion({"vector": ["y"], "keyword": ["x"]}, weights=W, k=60)
    assert [f.candidate_id for f in fused] == ["x", "y"]


def test_rrf_only_orders_what_it_was_given() -> None:
    fused = reciprocal_rank_fusion({"vector": ["a", "b"], "keyword": []}, weights=W, k=60)
    assert {f.candidate_id for f in fused} == {"a", "b"}


@pytest.mark.parametrize(
    ("rankings", "weights", "k", "message"),
    [
        ({"vector": ["a"]}, W, 0, "at least 1"),
        ({"vector": ["a", "a"]}, W, 60, "duplicate"),
        ({"semantic": ["a"]}, W, 60, "no fusion weight"),
    ],
)
def test_rrf_rejects_malformed_input(rankings, weights, k, message) -> None:
    with pytest.raises(ValueError, match=message):
        reciprocal_rank_fusion(rankings, weights=weights, k=k)


# --- contracts ---------------------------------------------------------------


def base_fields() -> dict:
    return {
        "retrieval_id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "query_hash": "0" * 64,
        "as_of": dt.date(2026, 6, 1),
        "kb_version": 3,
        "kb_version_pinned": False,
        "embedding_model": "m",
        "ruleset_version": "1.0.0",
        "classification": QueryClassification(),
    }


def test_an_empty_retrieval_states_why_and_requires_review() -> None:
    result = RetrievalResult.empty(EmptyReason.NOTHING_RELEVANT, **base_fields())
    assert result.outcome is RetrievalOutcome.EMPTY
    assert result.requires_human_review is True
    assert result.chunks == ()


def test_success_with_no_chunks_is_not_a_representable_state() -> None:
    """There is no "empty success" for later code to treat as licence to answer."""
    with pytest.raises(ValueError, match="at least one chunk"):
        RetrievalResult(
            outcome=RetrievalOutcome.SUCCESS, requires_human_review=False, **base_fields()
        )


def test_empty_without_a_reason_or_without_review_is_rejected() -> None:
    with pytest.raises(ValueError, match="state why"):
        RetrievalResult(outcome=RetrievalOutcome.EMPTY, requires_human_review=True, **base_fields())
    with pytest.raises(ValueError, match="state why"):
        RetrievalResult(
            outcome=RetrievalOutcome.EMPTY,
            empty_reason=EmptyReason.NOTHING_RELEVANT,
            requires_human_review=False,
            **base_fields(),
        )


def test_require_grounding_refuses_an_empty_retrieval() -> None:
    """FR-RAG-005: escalate, never answer from parametric memory."""
    empty = RetrievalResult.empty(EmptyReason.NO_ALLOWLISTED_SOURCES, **base_fields())
    with pytest.raises(UngroundedRetrievalError, match="human review"):
        require_grounding(empty)


def test_classification_origin_follows_its_content() -> None:
    assert QueryClassification().origin is ClassificationOrigin.UNCLASSIFIED
    classified = QueryClassification(
        source_types=frozenset({NormativeSourceType.ORG_POLICY}),
        requirement_category=RequirementCategory.PRIVACY,
    )
    assert classified.origin is ClassificationOrigin.CALLER_SUPPLIED


def test_a_query_carries_no_way_to_name_sources_or_jurisdictions() -> None:
    """The scope comes from the project inside the query (J.4), never from the caller."""
    fields = set(RetrievalQuery.model_fields) | set(QueryClassification.model_fields)
    for forbidden in (
        "source_ids",
        "normative_source_id",
        "jurisdiction",
        "jurisdictions",
        "kb_version",
        "allowlist",
    ):
        assert forbidden not in fields


def test_query_hash_is_the_sha256_of_the_text() -> None:
    query = RetrievalQuery(project_id=uuid.uuid4(), text="fee disclosure")
    assert query.query_hash == hashlib.sha256(b"fee disclosure").hexdigest()


# --- the ruleset ---------------------------------------------------------------


def test_the_ruleset_carries_the_architectures_chunk_sizes(retrieval_rules: RetrievalRules) -> None:
    """J.3: ~500/80 for knowledge items, ~700/100 for project documents."""
    assert (
        retrieval_rules.knowledge_item_window.max_tokens,
        retrieval_rules.knowledge_item_window.overlap_tokens,
    ) == (500, 80)
    assert (
        retrieval_rules.project_document_window.max_tokens,
        retrieval_rules.project_document_window.overlap_tokens,
    ) == (700, 100)
    assert retrieval_rules.rrf_k == 60
    assert retrieval_rules.name == "retrieval" and retrieval_rules.version


def test_an_undeclared_model_has_no_relevance_threshold(retrieval_rules: RetrievalRules) -> None:
    """Fail closed: a model the ruleset does not know cannot decide what is relevant."""
    with pytest.raises(RuleConfigurationError, match="no relevance threshold"):
        retrieval_rules.min_similarity_for("some/other-model")


def _ruleset(**overrides: object) -> RuleSet:
    rules: dict = {
        "chunking": {
            "knowledge_item": {"min_clause_boundaries": 2, "max_tokens": 500, "overlap_tokens": 80},
            "project_document": {"max_tokens": 700, "overlap_tokens": 100},
        },
        "fusion": {
            "method": "reciprocal_rank_fusion",
            "rrf_k": 60,
            "weights": {"vector": 1.0, "keyword": 1.0},
            "candidate_pool": 40,
        },
        "relevance": {"min_vector_similarity": {"m": 0.5}, "keyword_match": "all_terms"},
    }
    for path, value in overrides.items():
        section, key = path.split("__")
        rules[section][key] = value
    return RuleSet(
        name="retrieval", version="t", description="", data=MappingProxyType(_freeze(rules))
    )


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"fusion__method": "borda"}, "reciprocal_rank_fusion"),
        ({"fusion__weights": {"vector": 1.0}}, "vector/keyword"),
        ({"fusion__weights": {"vector": -1.0, "keyword": 1.0}}, "non-negative"),
        ({"fusion__rrf_k": 0}, ">= 1"),
        ({"relevance__keyword_match": "any_term"}, "all_terms"),
        ({"relevance__min_vector_similarity": {"m": 2.0}}, r"\[-1, 1\]"),
        ({"chunking__knowledge_item": {"max_tokens": 500}}, "malformed"),
    ],
)
def test_a_malformed_ruleset_is_refused_at_load(override: dict, message: str) -> None:
    with pytest.raises((RuleConfigurationError, ValueError), match=message):
        RetrievalRules.from_ruleset(_ruleset(**override))
