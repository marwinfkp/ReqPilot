"""The OpenAI wrapper (app/llm.py) and retrieval (app/services/rag.py), with the fake client."""
import pytest
from pydantic import BaseModel

from app import config, llm
from app.models import DocumentChunk, KnowledgeDocument
from app.services import rag
from tests.fake_openai import FakeOpenAI


class Summary(BaseModel):  # same class name as the documentation agent's schema -> fake has a factory
    summary: str
    key_findings: list[str]


# --------------------------------------------------------------------------
# llm.structured / llm.embed
# --------------------------------------------------------------------------
def test_demo_mode_never_calls_openai():
    assert not llm.enabled()
    with pytest.raises(llm.LLMError, match="demonstration mode"):
        llm.structured("task", "instructions", "data", Summary)
    with pytest.raises(llm.LLMError):
        llm.embed(["text"])


def test_structured_returns_the_parsed_schema_and_logs_usage(openai_mode):
    log = []
    result = llm.structured("executive summary", "Summarise.", "some data", Summary, log)
    assert isinstance(result, Summary) and result.summary == "Fake executive summary."

    (call,) = openai_mode.calls
    assert call["model"] == config.OPENAI_MODEL
    system, user = call["input"]
    assert system["role"] == "system" and llm.GUARDRAILS in system["content"] and "Summarise." in system["content"]
    assert user == {"role": "user", "content": "some data"}

    (entry,) = log
    assert entry["ok"] and entry["task"] == "executive summary"
    assert entry["input_tokens"] > 0 and entry["output_tokens"] == 50
    assert len(entry["prompt_sha256"]) == 16  # only a hash of the prompt is logged


def test_api_error_is_not_retried_by_reqpilot(openai_mode):
    """Network/HTTP errors are already retried inside the SDK; ReqPilot raises LLMError at once."""
    openai_mode.fail_schemas = {"Summary"}
    log = []
    with pytest.raises(llm.LLMError, match="OpenAI API error"):
        llm.structured("summary", "x", "y", Summary, log)
    assert len(openai_mode.calls) == 1
    assert log[0]["ok"] is False and "simulated" in log[0]["error"]


def test_empty_output_is_retried_once(openai_mode):
    openai_mode.refuse_schemas = {"Summary"}
    log = []
    with pytest.raises(llm.LLMError, match="invalid output"):
        llm.structured("summary", "x", "y", Summary, log)
    assert len(openai_mode.calls) == 2 and [e["ok"] for e in log] == [False, False]


def test_embed_batches_of_100(openai_mode):
    vectors = llm.embed([f"text number {i}" for i in range(250)])
    assert len(vectors) == 250 and all(len(v) == config.EMBEDDING_DIMENSIONS for v in vectors)
    assert [c["count"] for c in openai_mode.embedding_calls] == [100, 100, 50]
    assert {c["model"] for c in openai_mode.embedding_calls} == {config.OPENAI_EMBEDDING_MODEL}


def test_map_parallel_returns_exceptions_instead_of_raising():
    def work(batch):
        if batch == 2:
            raise ValueError("bad batch")
        return batch * 10

    results = llm.map_parallel(work, [1, 2, 3])
    assert results[0] == 10 and results[2] == 30 and isinstance(results[1], ValueError)
    assert llm.batched(list(range(5)), 2) == [[0, 1], [2, 3], [4]]


# --------------------------------------------------------------------------
# Knowledge base parsing and retrieval
# --------------------------------------------------------------------------
KB_MARKDOWN = """---
title: Test Policy
jurisdiction: Testland
doc_type: organisational_policy   # comment is ignored
authenticity: illustrative
---
# Heading

## TP-01 Session security
Topics: authentication
Customer sessions shall expire after 10 minutes of inactivity.

## TP-02 Data retention
Transaction records shall be retained for 10 years.
"""


def test_parse_kb_markdown():
    meta, sections = rag.parse_kb_markdown(KB_MARKDOWN)
    assert meta["title"] == "Test Policy" and meta["doc_type"] == "organisational_policy"
    assert [s["ref"] for s in sections] == ["TP-01", "TP-02"]
    assert sections[0]["title"] == "Session security" and sections[0]["topics"] == ["authentication"]
    assert "Topics:" not in sections[0]["text"]


def test_chunk_plain_text_keeps_chunks_small():
    text = "\n\n".join(f"Paragraph {i}. " + "word " * 100 for i in range(20))
    chunks = rag.chunk_plain_text(text)
    assert len(chunks) > 1 and all(len(c["text"]) <= 1500 * 1.5 for c in chunks)
    assert chunks[0]["ref"] == "Part 1"


def test_lexical_retrieval_finds_relevant_seeded_passages(db):
    results, method = rag.retrieve_many(db, ["Idempotency keys shall prevent duplicate debits."], "India", k=3)
    assert method == "lexical"
    top = results[0][0]
    assert "idempotency" in top["text"].lower()
    assert 0 < top["score"] <= 1 and top["jurisdiction"] == "India"


def test_retrieval_respects_jurisdiction(db):
    # All seeded documents are for India, so nothing may be retrieved for another jurisdiction.
    results, method = rag.retrieve_many(db, ["encryption of customer data"], "Atlantis", k=3)
    assert method == "none" and results == [[]]
    results, method = rag.retrieve_many(db, ["encryption of customer data"], "india", k=3)  # case-insensitive
    assert method == "lexical" and results[0]


def test_quote_check():
    passage = "Every payment request shall carry a unique client-generated idempotency key."
    assert rag.quote_in_text("every payment request shall carry a unique  client-generated", passage)
    assert not rag.quote_in_text("every payment request shall be free", passage)
    assert not rag.quote_in_text("payment", passage)  # too short to count as evidence


def test_semantic_retrieval_uses_embeddings(openai_mode):
    """In-memory chunks with fake embeddings (nothing is written to the database)."""
    doc = KnowledgeDocument(id=1, name="Doc", doc_type="regulation", authenticity="official",
                            jurisdiction="India", version="1")
    texts = {10: "Customer sessions shall expire after inactivity timeout",
             11: "Transaction records shall be retained for ten years"}
    chunks = [DocumentChunk(id=cid, section_ref=f"S{cid}", title="", text=t, topics=[], document=doc,
                            embedding=llm.embed([t])[0]) for cid, t in texts.items()]
    results = rag._semantic(None, chunks, ["How long are transaction records retained?"], k=2)
    assert [hit["chunk_id"] for hit in results[0]] == [11, 10]
    assert results[0][0]["score"] > results[0][1]["score"]


def add_embedded_policy(db, jurisdiction: str = "Embedland") -> KnowledgeDocument:
    meta = {"title": "Embedded test policy", "jurisdiction": jurisdiction, "doc_type": "organisational_policy",
            "authenticity": "illustrative"}
    sections = [{"ref": "EP-01", "title": "Payments", "topics": [],
                 "text": "Every payment request shall carry a unique idempotency key."}]
    return rag.add_document(db, meta, sections, "pytest", allowlisted=True, filename="embedded_policy.md")


def remove(db, doc: KnowledgeDocument) -> None:
    """Delete a test document again so other tests see the original knowledge base."""
    db.rollback()
    db.delete(db.get(KnowledgeDocument, doc.id))
    db.commit()


def test_uploaded_documents_are_embedded_and_searched_semantically(db, openai_mode):
    doc = add_embedded_policy(db)
    try:
        assert [len(c.embedding) for c in doc.chunks] == [config.EMBEDDING_DIMENSIONS]
        assert openai_mode.embedding_calls[0]["model"] == config.OPENAI_EMBEDDING_MODEL
        results, method = rag.retrieve_many(db, ["idempotency key for every payment"], "Embedland", k=2)
        assert method == "semantic" and results[0][0]["section_ref"] == "EP-01"
    finally:
        remove(db, doc)


def test_documents_without_embeddings_stay_retrievable(db, openai_mode):
    doc = add_embedded_policy(db)  # one embedded document in another jurisdiction
    try:
        results, method = rag.retrieve_many(db, ["Idempotency keys shall prevent duplicate debits."], "India", k=3)
        assert results[0], f"nothing retrieved for India (method={method})"
    finally:
        remove(db, doc)


def test_embedding_failure_warning_is_saved(db, openai_mode):
    openai_mode.fail_embeddings = True
    doc = add_embedded_policy(db)
    try:
        assert any("Embedding failed" in w for w in doc.warnings)  # shown in the upload response ...
        db.expire_all()
        stored = db.get(KnowledgeDocument, doc.id)
        assert any("Embedding failed" in w for w in stored.warnings)  # ... but is it stored?
    finally:
        remove(db, doc)


def test_fake_client_is_isolated_per_test():
    """Sanity check of the test setup itself: no fake is installed outside openai_mode."""
    assert config.DEMO_MODE is True
    assert not isinstance(llm._client, FakeOpenAI)
