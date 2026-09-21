"""Hybrid retrieval with an explicit outcome (``FR-RAG-002``, ``FR-RAG-005``; J.4).

The flow, every step deterministic::

    project scope (from the database)       - allowlist, jurisdictions, KB pin
      -> fail closed if the scope is empty   - RETRIEVAL_EMPTY, with a reason
      -> embed the query (local model)
      -> vector candidates  \\  both queries carry the allowlist join and the
      -> keyword candidates /   jurisdiction, effective-date and status/pin predicates
      -> reciprocal rank fusion              - orders candidates, never adds one
      -> chunk details (scoped join again)
      -> RETRIEVAL_SUCCESS, or RETRIEVAL_EMPTY when nothing relevant was found

No model takes part in ranking, filtering or deciding relevance. The relevance
threshold is versioned rule data; the outcome names the ruleset version.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence

from sqlalchemy.orm import Session

from reqpilot.domain.errors import KnowledgeBaseError
from reqpilot.domain.ids import ProjectId, new_retrieval_id
from reqpilot.domain.policy import Actor
from reqpilot.repositories.knowledge import RetrievalRepository, RetrievalScope
from reqpilot.retrieval.contracts import (
    EmptyReason,
    RetrievalOutcome,
    RetrievalQuery,
    RetrievalResult,
    RetrievedChunk,
)
from reqpilot.retrieval.embeddings import EmbeddingProvider
from reqpilot.retrieval.fusion import reciprocal_rank_fusion
from reqpilot.retrieval.rules import RetrievalRules


def today_utc() -> dt.date:
    return dt.datetime.now(dt.UTC).date()


class RetrievalService:
    def __init__(
        self,
        session: Session,
        actor: Actor,
        *,
        embedder: EmbeddingProvider,
        rules: RetrievalRules,
        default_top_k: int = 8,
    ) -> None:
        self._repo = RetrievalRepository(session, actor)
        self._embedder = embedder
        self._rules = rules
        self._default_top_k = default_top_k

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        project_id = ProjectId(query.project_id)
        today = today_utc()
        as_of = query.as_of or today
        if as_of > today:
            raise KnowledgeBaseError(
                "as_of cannot be in the future: it would admit sources not yet in force"
            )

        scope = self._repo.load_scope(project_id, as_of)
        if scope is None:
            raise KnowledgeBaseError("project not found")
        # Fail closed on a model the ruleset has no threshold for.
        min_similarity = self._rules.min_similarity_for(self._embedder.model_id)

        base = {
            "retrieval_id": new_retrieval_id(),
            "project_id": project_id,
            "query_hash": query.query_hash,
            "as_of": as_of,
            "kb_version": scope.kb_version,
            "kb_version_pinned": scope.kb_version_pin is not None,
            "embedding_model": self._embedder.model_id,
            "ruleset_version": self._rules.version,
            "classification": query.classification,
        }
        if not scope.jurisdictions:
            return RetrievalResult.empty(EmptyReason.NO_JURISDICTION_SCOPE, **base)
        if scope.allowlist_size == 0:
            return RetrievalResult.empty(EmptyReason.NO_ALLOWLISTED_SOURCES, **base)

        query_vector = self._embedder.embed_query(query.text)
        vector = self._repo.vector_candidates(
            scope,
            query_vector,
            embedding_model=self._embedder.model_id,
            min_similarity=min_similarity,
            source_types=query.classification.source_types,
            applicability=query.classification.applicability,
            limit=self._rules.candidate_pool,
        )
        keyword = self._repo.keyword_candidates(
            scope,
            query.text,
            embedding_model=self._embedder.model_id,
            source_types=query.classification.source_types,
            applicability=query.classification.applicability,
            limit=self._rules.candidate_pool,
        )
        if not vector and not keyword:
            return RetrievalResult.empty(EmptyReason.NOTHING_RELEVANT, **base)

        chunks = self._ranked(scope, query, query_vector, vector, keyword)
        if not chunks:  # pragma: no cover - details use the same scope as the candidates
            return RetrievalResult.empty(EmptyReason.NOTHING_RELEVANT, **base)
        return RetrievalResult(
            outcome=RetrievalOutcome.SUCCESS,
            requires_human_review=False,
            chunks=tuple(chunks),
            **base,  # type: ignore[arg-type]
        )

    def _ranked(
        self,
        scope: RetrievalScope,
        query: RetrievalQuery,
        query_vector: list[float],
        vector: Sequence[tuple[uuid.UUID, float]],
        keyword: Sequence[tuple[uuid.UUID, float]],
    ) -> list[RetrievedChunk]:
        fused = reciprocal_rank_fusion(
            {"vector": [c for c, _ in vector], "keyword": [c for c, _ in keyword]},
            weights=self._rules.weights,
            k=self._rules.rrf_k,
        )[: query.top_k or self._default_top_k]
        details = self._repo.chunk_details(
            scope,
            [f.candidate_id for f in fused],
            query_vector,
            embedding_model=self._embedder.model_id,
        )
        out: list[RetrievedChunk] = []
        for candidate in fused:
            found = details.get(candidate.candidate_id)
            if found is None:  # pragma: no cover - same scope as the candidate queries
                continue
            chunk, item, source, similarity = found
            out.append(
                RetrievedChunk(
                    rank=len(out) + 1,
                    chunk_id=chunk.id,
                    knowledge_item_id=item.id,
                    item_key=item.item_key,
                    item_version_no=item.version_no,
                    item_title=item.title,
                    clause_ref=item.clause_ref,
                    kb_version=item.kb_version,
                    normative_source_id=source.id,
                    source_title=source.title,
                    source_type=source.source_type,
                    issuing_body=source.issuing_body,
                    jurisdiction=source.jurisdiction,
                    source_version=source.version,
                    effective_date=source.effective_date,
                    retrieved_at=source.retrieved_at,
                    source_url=source.source_url,
                    licence_class=source.licence_class,
                    text_origin=item.text_origin,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    text=chunk.text,
                    strategy=chunk.strategy,
                    structure_label=chunk.structure_label,
                    fused_score=candidate.score,
                    vector_similarity=similarity,
                    vector_rank=candidate.ranks.get("vector"),
                    keyword_rank=candidate.ranks.get("keyword"),
                )
            )
        return out
