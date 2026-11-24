"""Run by test_pgvector.py in a separate process whose DATABASE_URL points to PostgreSQL.

Creates every table inside a temporary schema (SQLAlchemy schema_translate_map, so
nothing is ever written to the application's own tables), stores three chunks with
hand-made embeddings, runs the agent retrieval interface (rag.retrieve_for_agent ->
rag._semantic -> pgvector cosine distance in PostgreSQL) and prints the result as
JSON. The schema is dropped afterwards. No OpenAI call is made: the query embedding
is a fixed vector.
"""
import json
import sys

from sqlalchemy import text
from sqlalchemy.orm import Session

from app import config, database


def unit(*pairs: tuple[int, float]) -> list[float]:
    vector = [0.0] * config.EMBEDDING_DIMENSIONS
    for index, value in pairs:
        vector[index] = value
    return vector


def main(schema: str) -> None:
    from app.models import DocumentChunk, KnowledgeDocument
    from app.services import rag

    assert database.IS_POSTGRES, "DATABASE_URL must point to PostgreSQL"
    # Every unqualified table name is rendered as "<schema>.<table>" - also by create_all.
    engine = database.engine.execution_options(schema_translate_map={None: schema})
    database.Base.metadata.create_all(engine)
    with engine.connect() as conn:
        created = conn.execute(text("SELECT count(*) FROM information_schema.tables WHERE table_schema = :s "
                                    "AND table_name IN ('knowledge_documents', 'document_chunks')"), {"s": schema})
        if created.scalar() != 2:  # never fall back to the application's tables
            raise SystemExit(f"tables were not created in the temporary schema {schema}")
    db = Session(bind=engine)
    try:
        def document(name: str, doc_type: str, chunks: list[tuple[str, list[float]]]) -> None:
            doc = KnowledgeDocument(name=name, source_organisation="pytest", jurisdiction="India", version="1",
                                    doc_type=doc_type, authenticity="illustrative", allowlisted=True)
            for index, (ref, vector) in enumerate(chunks):
                doc.chunks.append(DocumentChunk(chunk_index=index, section_ref=ref, title=ref, topics=[],
                                                text=f"Text of {ref} used by the pgvector test.", embedding=vector,
                                                page=index + 1))
            db.add(doc)

        document("Security policy", "security_standard",
                 [("PG-SEC-1", unit((0, 1.0))), ("PG-SEC-2", unit((0, 0.5), (1, 0.866)))])
        # Closest to the query of all, but an engineering guideline: the security agent must not see it.
        document("Engineering guideline", "engineering_guideline", [("PG-ENG-1", unit((0, 0.9), (1, 0.1)))])
        db.commit()

        query = unit((0, 0.9), (1, 0.1))
        rag.llm.enabled = lambda: True  # semantic search on stored embeddings, without calling OpenAI
        rag.embed_queries = lambda queries: [query for _ in queries]
        ctx = {"db": db, "project": {"name": "pg", "domain": "payments", "description": "", "jurisdiction": "India"},
               "retrieval_log": [], "reference_checks": []}
        security = rag.retrieve_for_agent(ctx, "security", items=["anything"], include_project=False)
        quality = rag.retrieve_for_agent(ctx, "quality", items=["anything"], include_project=False)
        distance = DocumentChunk.embedding.cosine_distance(query)  # the SQL the agents use, checked directly
        sql = str(db.query(DocumentChunk.id).order_by(distance).statement.compile(dialect=engine.dialect))
        # A failing vector query (NaN is rejected by PostgreSQL) falls back to lexical search, and the
        # session stays usable for the rest of the analysis (the query runs in a savepoint).
        rag.clear_embedding_cache()
        rag.embed_queries = lambda queries: [[float("nan")] * config.EMBEDDING_DIMENSIONS for _ in queries]
        broken = rag.retrieve_for_agent(ctx, "security", items=["Text of PG-SEC-2 used by the pgvector test"],
                                        include_project=False)
        session_usable = db.query(DocumentChunk).count() == 3
        paused = rag.retrieve_many(db, ["anything"], "India", k=1, doc_types=["security_standard"])[1]
        print(json.dumps({
            "failure_method": broken.method, "failure_refs": [h["section_ref"] for h in broken.item_hits(0)],
            "session_usable_after_failure": session_usable, "method_while_paused": paused,
            "security_method": security.method,
            "security_refs": [h["section_ref"] for h in security.item_hits(0)],
            "security_scores": [h["score"] for h in security.item_hits(0)],
            "quality_refs": [h["section_ref"] for h in quality.item_hits(0)],
            "page": security.item_hits(0)[0]["page"],
            "uses_cosine_operator": "<=>" in sql,
        }))
    finally:
        db.close()


if __name__ == "__main__":
    main(sys.argv[1])
