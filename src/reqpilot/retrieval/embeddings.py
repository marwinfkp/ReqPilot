"""Embedding providers behind one interface (architecture ADR-005).

The approved implementation is **local CPU** ``sentence-transformers`` with
``BAAI/bge-small-en-v1.5`` (384 dimensions). No hosted embedding API exists here,
so no document text leaves the machine for embedding (ADR-005: ET-10, QA-PRV).

Retrieval code depends on :class:`EmbeddingProvider` only. Nothing outside this
module imports ``sentence_transformers``, and even here the import is deferred
until a model is first used, so importing the retrieval package never loads a
deep-learning runtime.

:class:`HashingEmbeddingProvider` is a deterministic, dependency-free stand-in
used by the offline test suite (ET-10). It is **not semantic** - it measures
shared vocabulary - and the configuration refuses it in production and the
retrieval probe refuses it for ET-06.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from reqpilot.config import EmbeddingProviderKind, Settings
from reqpilot.domain.errors import EmbeddingUnavailableError
from reqpilot.domain.models.knowledge import EMBEDDING_DIMENSION

#: The approved model (ADR-005). Changing it requires a re-embed migration
#: (ADR-004), so it is named once, here.
APPROVED_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

#: bge models are trained to embed short *queries* with this instruction and
#: passages without it. Documented on the model card; applied to queries only.
BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

HASHING_MODEL_ID = "reqpilot/hashing-384-v1"


@runtime_checkable
class EmbeddingProvider(Protocol):
    """What retrieval needs from an embedding model, and nothing more."""

    @property
    def model_id(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed passages for storage. Vectors are L2-normalised."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed a search query. The vector is L2-normalised."""
        ...


def _check(vectors: list[list[float]], dimension: int, model_id: str) -> list[list[float]]:
    for vector in vectors:
        if len(vector) != dimension:
            raise EmbeddingUnavailableError(
                f"{model_id} produced a {len(vector)}-dimensional vector; the schema "
                f"stores {EMBEDDING_DIMENSION} (architecture ADR-004)"
            )
    return vectors


class SentenceTransformerEmbeddingProvider:
    """The approved provider: ``bge-small-en-v1.5`` on the local CPU (ADR-005).

    ``allow_download`` is off by default, so an uncached model fails with a clear
    error instead of reaching the network mid-request. Enable it once, at setup,
    to fetch the ~120 MB model into the local cache.
    """

    def __init__(
        self,
        model_name: str = APPROVED_EMBEDDING_MODEL,
        *,
        allow_download: bool = False,
        cache_folder: str | None = None,
    ) -> None:
        if model_name != APPROVED_EMBEDDING_MODEL:
            raise EmbeddingUnavailableError(
                f"embedding model {model_name!r} is not the approved model "
                f"{APPROVED_EMBEDDING_MODEL!r} (architecture ADR-005); changing models "
                "requires a re-embed migration (ADR-004)"
            )
        self._model_name = model_name
        self._allow_download = allow_download
        self._cache_folder = cache_folder
        self._model: Any = None

    @property
    def model_id(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return EMBEDDING_DIMENSION

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingUnavailableError(
                "sentence-transformers is not installed; install the project's "
                "'embeddings' extra to use the approved local embedding model"
            ) from exc
        if not self._allow_download:
            # Belt and braces: the library honours this even for files it would
            # otherwise try to refresh.
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
        try:
            model = SentenceTransformer(
                self._model_name,
                device="cpu",
                cache_folder=self._cache_folder,
                local_files_only=not self._allow_download,
            )
        except Exception as exc:
            raise EmbeddingUnavailableError(
                f"could not load {self._model_name} from the local cache "
                f"({type(exc).__name__}). Set EMBEDDING_ALLOW_DOWNLOAD=true once to "
                "fetch it, or pre-populate the Hugging Face cache."
            ) from exc
        # Renamed in sentence-transformers 6; the old name remains for 3.x-5.x.
        dimension_of = getattr(model, "get_embedding_dimension", None) or (
            model.get_sentence_embedding_dimension
        )
        actual = dimension_of()
        if actual != EMBEDDING_DIMENSION:
            raise EmbeddingUnavailableError(
                f"{self._model_name} has dimension {actual}; the schema stores "
                f"{EMBEDDING_DIMENSION} (architecture ADR-004)"
            )
        self._model = model
        return model

    def _encode(self, texts: Sequence[str]) -> list[list[float]]:
        model = self._load()
        vectors = model.encode(
            list(texts),
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return _check([[float(x) for x in row] for row in vectors], self.dimension, self.model_id)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._encode(texts) if texts else []

    def embed_query(self, text: str) -> list[float]:
        return self._encode([BGE_QUERY_INSTRUCTION + text])[0]


_WORD = re.compile(r"[a-z0-9]+")

#: Function words carry no topical signal; dropping them keeps the hashing
#: provider's similarity about shared *content* vocabulary.
_STOPWORDS = frozenset(
    {
        "a", "all", "an", "and", "any", "are", "as", "at", "be", "by", "for", "from",
        "has", "have", "in", "is", "it", "its", "may", "must", "no", "not", "of", "on",
        "or", "shall", "that", "the", "their", "this", "to", "was", "were", "which",
        "will", "with",
    }
)  # fmt: skip


class HashingEmbeddingProvider:
    """A deterministic, offline, **non-semantic** provider for tests (ET-10).

    Each content word is hashed to one of 384 signed buckets; the vector is the
    normalised bucket sum. Cosine similarity then reflects shared vocabulary.
    That is enough to make retrieval tests deterministic and meaningful about
    ranking and filtering; it says nothing about retrieval *quality*.
    """

    @property
    def model_id(self) -> str:
        return HASHING_MODEL_ID

    @property
    def dimension(self) -> int:
        return EMBEDDING_DIMENSION

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * EMBEDDING_DIMENSION
        for word in _WORD.findall(text.lower()):
            if word in _STOPWORDS:
                continue
            digest = hashlib.sha256(word.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % EMBEDDING_DIMENSION
            vector[bucket] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in vector))
        if norm == 0.0:
            # An all-stopword or empty text still needs a valid unit vector.
            vector[0] = 1.0
            return vector
        return [x / norm for x in vector]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two vectors. Used by tests and the probe, not retrieval."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def provider_for(
    kind: EmbeddingProviderKind, model_name: str, *, allow_download: bool
) -> EmbeddingProvider:
    """Build a provider from plain configuration values. No silent fallback between them."""
    if kind is EmbeddingProviderKind.HASHING:
        return HashingEmbeddingProvider()
    return SentenceTransformerEmbeddingProvider(model_name, allow_download=allow_download)


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """The provider the configuration selects."""
    return provider_for(
        settings.embedding_provider,
        settings.embedding_model,
        allow_download=settings.embedding_allow_download,
    )
