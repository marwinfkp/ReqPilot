"""Module M5 - knowledge and retrieval (architecture section J).

The deterministic parts of M5 that need no database: chunking (J.3), the
embedding abstraction (ADR-005), reciprocal rank fusion (J.4), the typed query
and result contracts (``FR-RAG-002``, ``FR-RAG-005``), text extraction (J.2) and
the retrieval ruleset.

The SQL - where the allowlist join lives - is in
:mod:`reqpilot.repositories.knowledge`, so that it sits behind the repository
layer's authorization like every other query. Orchestration of ingestion,
retrieval, evidence and citations is in :mod:`reqpilot.services.knowledge`.

This package imports only the domain and the rules loader. It must never import
the graph, the agents or the LLM gateway: retrieval stays deterministic, and M5
is not a route by which governance comes to depend on a model.
"""

from reqpilot.retrieval.chunking import (
    TextChunk,
    UtteranceChunk,
    WindowSpec,
    chunk_knowledge_item,
    chunk_project_document,
    chunk_transcript,
    count_tokens,
)
from reqpilot.retrieval.contracts import (
    ClassificationOrigin,
    EmptyReason,
    QueryClassification,
    RetrievalOutcome,
    RetrievalQuery,
    RetrievalResult,
    RetrievedChunk,
    require_grounding,
)
from reqpilot.retrieval.embeddings import (
    APPROVED_EMBEDDING_MODEL,
    HASHING_MODEL_ID,
    EmbeddingProvider,
    HashingEmbeddingProvider,
    SentenceTransformerEmbeddingProvider,
)
from reqpilot.retrieval.fusion import FusedCandidate, reciprocal_rank_fusion
from reqpilot.retrieval.rules import RetrievalRules, load_retrieval_rules

__all__ = [
    "APPROVED_EMBEDDING_MODEL",
    "HASHING_MODEL_ID",
    "ClassificationOrigin",
    "EmbeddingProvider",
    "EmptyReason",
    "FusedCandidate",
    "HashingEmbeddingProvider",
    "QueryClassification",
    "RetrievalOutcome",
    "RetrievalQuery",
    "RetrievalResult",
    "RetrievalRules",
    "RetrievedChunk",
    "SentenceTransformerEmbeddingProvider",
    "TextChunk",
    "UtteranceChunk",
    "WindowSpec",
    "chunk_knowledge_item",
    "chunk_project_document",
    "chunk_transcript",
    "count_tokens",
    "load_retrieval_rules",
    "reciprocal_rank_fusion",
    "require_grounding",
]
