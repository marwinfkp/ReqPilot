"""The embedding abstraction (architecture ADR-005).

Offline and deterministic: nothing here loads a model or touches the network.
The real model is exercised separately, and only when it is already cached
(``tests/integration/test_p2_real_embeddings.py``).
"""

from __future__ import annotations

import os
import subprocess
import sys
import types

import pytest

from reqpilot.config import AppEnv, EmbeddingProviderKind, Settings
from reqpilot.domain.errors import EmbeddingUnavailableError
from reqpilot.domain.models.knowledge import EMBEDDING_DIMENSION
from reqpilot.retrieval.embeddings import (
    APPROVED_EMBEDDING_MODEL,
    BGE_QUERY_INSTRUCTION,
    HASHING_MODEL_ID,
    EmbeddingProvider,
    HashingEmbeddingProvider,
    SentenceTransformerEmbeddingProvider,
    build_embedding_provider,
    cosine_similarity,
)

pytestmark = pytest.mark.unit


def test_the_approved_model_is_bge_small() -> None:
    assert APPROVED_EMBEDDING_MODEL == "BAAI/bge-small-en-v1.5"


def test_both_providers_satisfy_the_interface() -> None:
    assert isinstance(HashingEmbeddingProvider(), EmbeddingProvider)
    assert isinstance(SentenceTransformerEmbeddingProvider(), EmbeddingProvider)


def test_the_real_provider_declares_the_schema_dimension_without_loading() -> None:
    provider = SentenceTransformerEmbeddingProvider()
    assert provider.dimension == EMBEDDING_DIMENSION
    assert provider.model_id == APPROVED_EMBEDDING_MODEL
    assert provider._model is None, "constructing the provider must not load the model"


def test_another_embedding_model_is_refused() -> None:
    """ADR-005/ADR-004: changing models needs a re-embed migration, not a setting."""
    with pytest.raises(EmbeddingUnavailableError, match="not the approved model"):
        SentenceTransformerEmbeddingProvider("BAAI/bge-base-en-v1.5")


def test_importing_retrieval_does_not_load_a_deep_learning_runtime() -> None:
    code = (
        "import sys, reqpilot.retrieval, reqpilot.services.knowledge;"
        "print('torch' in sys.modules, 'sentence_transformers' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert out == "False False"


def test_importing_the_whole_application_does_not_load_the_model() -> None:
    """The app, its API, its UI and the probe harness all import without the model."""
    code = (
        "import sys, reqpilot.main, reqpilot.api.app, reqpilot.web.knowledge;"
        "import reqpilot.api.routes.knowledge, reqpilot.services.evaluation.retrieval_probe;"
        "print('torch' in sys.modules, 'sentence_transformers' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert out == "False False"


def test_the_approved_provider_loads_from_the_local_cache_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With downloads off (the default), the library is told not to use the network.

    A stand-in for ``sentence_transformers`` records how the model is loaded, so
    this runs without the library, the model or a network.
    """
    loads: list[tuple[str, dict]] = []
    encoded: list[str] = []

    class FakeSentenceTransformer:
        def __init__(self, name: str, **kwargs) -> None:
            loads.append((name, kwargs))

        def get_embedding_dimension(self) -> int:
            return EMBEDDING_DIMENSION

        def encode(self, texts: list[str], **kwargs) -> list[list[float]]:
            assert kwargs["normalize_embeddings"] is True
            encoded.extend(texts)
            return [[1.0] + [0.0] * (EMBEDDING_DIMENSION - 1) for _ in texts]

    fake = types.ModuleType("sentence_transformers")
    fake.SentenceTransformer = FakeSentenceTransformer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)

    provider = SentenceTransformerEmbeddingProvider()
    provider.embed_query("access review")
    provider.embed_documents(["Access is reviewed."])

    assert len(loads) == 1, "the model is loaded once and reused"
    name, kwargs = loads[0]
    assert name == APPROVED_EMBEDDING_MODEL
    assert kwargs["local_files_only"] is True
    assert kwargs["device"] == "cpu"
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert encoded == [BGE_QUERY_INSTRUCTION + "access review", "Access is reviewed."]


def test_the_application_runs_without_the_optional_embeddings_extra() -> None:
    """sentence-transformers is an optional extra: without it the app still starts,
    the offline provider still works, and the approved provider fails with a clear
    ``EmbeddingUnavailableError`` - never an ImportError at start-up, never a download.
    """
    code = """
import sys
sys.modules["sentence_transformers"] = None  # as if the extra were not installed
from fastapi.testclient import TestClient
from reqpilot.domain.errors import EmbeddingUnavailableError
from reqpilot.main import app
from reqpilot.retrieval.embeddings import (
    HashingEmbeddingProvider, SentenceTransformerEmbeddingProvider,
)
assert TestClient(app).get("/health").status_code == 200
assert len(HashingEmbeddingProvider().embed_query("offline")) == 384
try:
    SentenceTransformerEmbeddingProvider().embed_query("x")
except EmbeddingUnavailableError as exc:
    print("refused:", "embeddings" in str(exc))
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "refused: True"


def test_hashing_vectors_are_deterministic_normalised_and_384_wide() -> None:
    provider = HashingEmbeddingProvider()
    a = provider.embed_query("customer data access review")
    assert a == provider.embed_query("customer data access review")
    assert len(a) == EMBEDDING_DIMENSION
    assert sum(x * x for x in a) == pytest.approx(1.0)
    assert provider.embed_documents(["x", "y"]) == [
        provider.embed_query("x"),
        provider.embed_query("y"),
    ]


def test_hashing_similarity_reflects_shared_vocabulary() -> None:
    provider = HashingEmbeddingProvider()
    query = provider.embed_query("quarterly access review of customer data")
    close = provider.embed_documents(["Access to customer data is reviewed quarterly."])[0]
    far = provider.embed_documents(["The cafeteria menu changes on Tuesdays."])[0]
    assert cosine_similarity(query, close) > cosine_similarity(query, far)


def test_hashing_handles_text_with_no_content_words() -> None:
    vector = HashingEmbeddingProvider().embed_query("the of and")
    assert sum(x * x for x in vector) == pytest.approx(1.0)


def test_configuration_selects_the_provider() -> None:
    hashing = build_embedding_provider(
        Settings(_env_file=None, EMBEDDING_PROVIDER="hashing")  # type: ignore[call-arg]
    )
    assert hashing.model_id == HASHING_MODEL_ID
    real = build_embedding_provider(Settings(_env_file=None))  # type: ignore[call-arg]
    assert real.model_id == APPROVED_EMBEDDING_MODEL


def test_the_default_provider_is_the_approved_model_and_never_downloads() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.embedding_provider is EmbeddingProviderKind.SENTENCE_TRANSFORMERS
    assert settings.embedding_model == APPROVED_EMBEDDING_MODEL
    assert settings.embedding_allow_download is False


def test_the_non_semantic_provider_is_refused_in_production() -> None:
    with pytest.raises(ValueError, match="refused in production"):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            REQPILOT_ENV=AppEnv.PRODUCTION,
            REQPILOT_SECRET_KEY="a-real-secret-for-this-test-only",
            EMBEDDING_PROVIDER="hashing",
        )
