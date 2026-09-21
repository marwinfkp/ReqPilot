"""The retrieval ruleset as a typed, validated object (architecture J.3, J.4).

The values live in ``rules/data/retrieval.yaml`` - versioned data, per DQ-03 -
and are read through the shared loader. This module adds the validation that
turns a malformed file into a startup error rather than a strange ranking.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from reqpilot.domain.errors import RuleConfigurationError
from reqpilot.retrieval.chunking import WindowSpec
from reqpilot.rules import RuleSet, load_ruleset

RULESET_FILE = "retrieval.yaml"


@dataclass(frozen=True)
class RetrievalRules:
    """Everything about retrieval that is policy rather than code."""

    name: str
    version: str
    knowledge_item_window: WindowSpec
    min_clause_boundaries: int
    project_document_window: WindowSpec
    rrf_k: int
    weights: Mapping[str, float]
    candidate_pool: int
    min_vector_similarity: Mapping[str, float]

    def min_similarity_for(self, model_id: str) -> float:
        """The relevance threshold for ``model_id``.

        Fails closed: a model without a declared threshold cannot decide what is
        relevant, so it cannot be used for retrieval at all.
        """
        try:
            return self.min_vector_similarity[model_id]
        except KeyError:
            raise RuleConfigurationError(
                f"retrieval ruleset v{self.version} declares no relevance threshold for "
                f"embedding model {model_id!r}"
            ) from None

    @classmethod
    def from_ruleset(cls, ruleset: RuleSet) -> RetrievalRules:
        try:
            chunking = ruleset["chunking"]
            kb = chunking["knowledge_item"]
            doc = chunking["project_document"]
            fusion = ruleset["fusion"]
            relevance = ruleset["relevance"]
            if fusion["method"] != "reciprocal_rank_fusion":
                raise RuleConfigurationError("only reciprocal_rank_fusion is supported (J.4)")
            if relevance["keyword_match"] != "all_terms":
                raise RuleConfigurationError("keyword_match must be 'all_terms'")
            weights = {str(k): float(v) for k, v in fusion["weights"].items()}
            if set(weights) != {"vector", "keyword"} or any(w < 0 for w in weights.values()):
                raise RuleConfigurationError("fusion weights must be non-negative vector/keyword")
            thresholds = {str(k): float(v) for k, v in relevance["min_vector_similarity"].items()}
            if any(not -1.0 <= t <= 1.0 for t in thresholds.values()):
                raise RuleConfigurationError("similarity thresholds must lie in [-1, 1]")
            rules = cls(
                name=ruleset.name,
                version=ruleset.version,
                knowledge_item_window=WindowSpec(int(kb["max_tokens"]), int(kb["overlap_tokens"])),
                min_clause_boundaries=int(kb["min_clause_boundaries"]),
                project_document_window=WindowSpec(
                    int(doc["max_tokens"]), int(doc["overlap_tokens"])
                ),
                rrf_k=int(fusion["rrf_k"]),
                weights=MappingProxyType(weights),
                candidate_pool=int(fusion["candidate_pool"]),
                min_vector_similarity=MappingProxyType(thresholds),
            )
        except RuleConfigurationError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise RuleConfigurationError(f"retrieval ruleset is malformed: {exc}") from exc
        if rules.rrf_k < 1 or rules.candidate_pool < 1 or rules.min_clause_boundaries < 1:
            raise RuleConfigurationError(
                "rrf_k, candidate_pool and min_clause_boundaries must be >= 1"
            )
        return rules


def load_retrieval_rules(rules_dir: str | Path) -> RetrievalRules:
    return RetrievalRules.from_ruleset(load_ruleset(Path(rules_dir) / RULESET_FILE))
