"""Knowledge base and Retrieval-Augmented Generation.

Documents (regulations, organisational policies, security standards) are split
into chunks (one per section/clause where possible), tagged with compliance
topics and embedded with an OpenAI embedding model into a pgvector column.

Retrieval:
* semantic  - cosine distance in pgvector (when embeddings exist and OpenAI is configured)
* lexical   - IDF-weighted keyword overlap + topic match (demonstration mode / no embeddings)
Only allowlisted documents of the project's jurisdiction are ever retrieved,
and chunks that look like prompt-injection attempts are excluded.
"""
import math
import re

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app import config, llm
from app.database import IS_POSTGRES
from app.models import DocumentChunk, KnowledgeDocument
from app.rules import detect_topics, tokens
from app.security import find_injection

GLOBAL_JURISDICTIONS = {"international", "global", "all"}
_MAX_CHUNK_CHARS = 1500


# --------------------------------------------------------------------------
# Parsing and chunking
# --------------------------------------------------------------------------
def parse_kb_markdown(text: str) -> tuple[dict, list[dict]]:
    """Parse the knowledge_base/*.md format: front matter + '## REF Title' sections."""
    meta: dict = {}
    body = text
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if match:
        for line in match.group(1).splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip()] = value.split("#")[0].strip()
        body = text[match.end():]
    sections = []
    for part in re.split(r"^##\s+", body, flags=re.M)[1:]:
        heading, _, content = part.partition("\n")
        ref, _, title = heading.strip().partition(" ")
        topics_match = re.search(r"^Topics:\s*(.+)$", content, re.M | re.I)
        topics = [t.strip() for t in topics_match.group(1).split(",")] if topics_match else []
        content = re.sub(r"^Topics:.*$", "", content, flags=re.M | re.I).strip()
        sections.append({"ref": ref, "title": title.strip(), "topics": topics, "text": content})
    return meta, sections


def chunk_plain_text(text: str) -> list[dict]:
    """Chunk an uploaded document without known structure into ~1500-char pieces
    on paragraph boundaries."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, current = [], ""
    for paragraph in paragraphs:
        if current and len(current) + len(paragraph) > _MAX_CHUNK_CHARS:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{paragraph}".strip()
        while len(current) > _MAX_CHUNK_CHARS * 1.5:  # very long paragraph
            chunks.append(current[:_MAX_CHUNK_CHARS])
            current = current[_MAX_CHUNK_CHARS:]
    if current:
        chunks.append(current)
    return [{"ref": f"Part {i + 1}", "title": chunk.split("\n")[0][:120], "topics": [], "text": chunk}
            for i, chunk in enumerate(chunks)]


def add_document(db: Session, meta: dict, sections: list[dict], uploaded_by: str, allowlisted: bool,
                 filename: str = "") -> KnowledgeDocument:
    doc = KnowledgeDocument(
        name=meta.get("title") or filename, source_organisation=meta.get("source_organisation", "Unknown"),
        jurisdiction=meta.get("jurisdiction", "India"), version=meta.get("version", ""),
        effective_date=meta.get("effective_date", ""), doc_type=meta.get("doc_type", "organisational_policy"),
        authenticity=meta.get("authenticity", "illustrative"), applicability=meta.get("applicability", ""),
        filename=filename, allowlisted=allowlisted, uploaded_by=uploaded_by,
    )
    warnings = []
    for index, section in enumerate(sections):
        suspicious = find_injection(section["text"])
        if suspicious:
            warnings.append(f"Section {section['ref']} contains instruction-like text "
                            f"({suspicious[0]!r}); it is excluded from retrieval.")
        topics = sorted(set(section["topics"]) | set(detect_topics(section["title"] + " " + section["text"])))
        doc.chunks.append(DocumentChunk(chunk_index=index, section_ref=section["ref"][:60],
                                        title=section["title"][:300], text=section["text"], topics=topics,
                                        suspicious=bool(suspicious)))
    if llm.enabled():
        try:
            embed_chunks(doc.chunks)
        except Exception as exc:  # the document stays usable with lexical retrieval
            warnings.append(f"Embedding failed ({exc}); lexical retrieval will be used.")
    # Assign a new list (not .append on the stored one) so SQLAlchemy saves the JSON change.
    doc.warnings = warnings
    db.add(doc)
    db.commit()
    return doc


def embed_chunks(chunks: list[DocumentChunk]) -> int:
    vectors = llm.embed([f"{c.title}\n{c.text}" for c in chunks])
    for chunk, vector in zip(chunks, vectors):
        chunk.embedding = vector
    return len(chunks)


def reindex(db: Session) -> int:
    """Embed every chunk that has no embedding yet (after an API key is added)."""
    chunks = db.scalars(select(DocumentChunk).where(DocumentChunk.embedding.is_(None))).all()
    count = embed_chunks(chunks) if chunks else 0
    db.commit()
    return count


def load_seed_documents(db: Session) -> int:
    """Load knowledge_base/*.md once (skips files already loaded)."""
    loaded = 0
    existing = set(db.scalars(select(KnowledgeDocument.filename)).all())
    for path in sorted(config.KNOWLEDGE_BASE_DIR.glob("*.md")):
        if path.name.lower() == "readme.md" or path.name in existing:
            continue
        meta, sections = parse_kb_markdown(path.read_text(encoding="utf-8"))
        if sections:
            add_document(db, meta, sections, "system (seed)", allowlisted=True, filename=path.name)
            loaded += 1
    return loaded


# --------------------------------------------------------------------------
# Retrieval
# --------------------------------------------------------------------------
def _candidate_chunks(db: Session, jurisdiction: str) -> list[DocumentChunk]:
    rows = db.scalars(
        select(DocumentChunk).join(KnowledgeDocument)
        .where(KnowledgeDocument.allowlisted.is_(True), DocumentChunk.suspicious.is_(False))
        .options(joinedload(DocumentChunk.document))
    ).all()
    allowed = {jurisdiction.lower()} | GLOBAL_JURISDICTIONS
    return [c for c in rows if c.document.jurisdiction.lower() in allowed]


def semantic_available(chunks: list[DocumentChunk]) -> bool:
    """Semantic search is used only when OpenAI is configured AND every candidate
    chunk has an embedding; otherwise un-embedded documents would silently never
    be retrieved, so lexical search is used for all of them instead."""
    return llm.enabled() and bool(chunks) and all(c.embedding is not None for c in chunks)


def retrieve_many(db: Session, queries: list[str], jurisdiction: str, k: int = 4) -> tuple[list[list[dict]], str]:
    """Top-k chunks for every query. Returns (results, method)."""
    chunks = _candidate_chunks(db, jurisdiction)
    if not chunks or not queries:
        return [[] for _ in queries], "none"
    if semantic_available(chunks):
        try:
            return _semantic(db, chunks, queries, k), "semantic (pgvector)" if IS_POSTGRES else "semantic"
        except Exception:
            pass  # fall back to lexical retrieval
    return _lexical(chunks, queries, k), "lexical"


def _hit(chunk: DocumentChunk, score: float) -> dict:
    doc = chunk.document
    return {"chunk_id": chunk.id, "document_id": doc.id, "document_name": doc.name, "doc_type": doc.doc_type,
            "authenticity": doc.authenticity, "jurisdiction": doc.jurisdiction, "version": doc.version,
            "section_ref": chunk.section_ref, "title": chunk.title, "text": chunk.text, "topics": chunk.topics,
            "score": round(score, 3)}


def _semantic(db: Session, chunks: list[DocumentChunk], queries: list[str], k: int) -> list[list[dict]]:
    allowed_ids = {c.id: c for c in chunks}
    vectors = llm.embed(queries)
    results = []
    for vector in vectors:
        if IS_POSTGRES:  # nearest neighbours computed inside PostgreSQL by pgvector
            distance = DocumentChunk.embedding.cosine_distance(vector)
            rows = db.execute(select(DocumentChunk.id, distance).where(DocumentChunk.id.in_(list(allowed_ids)))
                              .where(DocumentChunk.embedding.is_not(None)).order_by(distance).limit(k)).all()
            results.append([_hit(allowed_ids[cid], 1 - float(dist)) for cid, dist in rows])
        else:
            scored = [(_cosine(vector, c.embedding), c) for c in chunks if c.embedding is not None]
            scored.sort(key=lambda pair: pair[0], reverse=True)
            results.append([_hit(c, s) for s, c in scored[:k]])
    return results


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def _lexical(chunks: list[DocumentChunk], queries: list[str], k: int) -> list[list[dict]]:
    """IDF-weighted share of the query's words found in the chunk, plus a bonus
    when the query and chunk share a compliance topic."""
    chunk_tokens = [tokens(f"{c.title} {c.text}") for c in chunks]
    doc_freq: dict[str, int] = {}
    for toks in chunk_tokens:
        for t in toks:
            doc_freq[t] = doc_freq.get(t, 0) + 1
    n = len(chunks)

    def idf(term: str) -> float:
        return math.log(1 + n / (1 + doc_freq.get(term, 0)))

    results = []
    for query in queries:
        q_tokens = tokens(query)
        q_topics = set(detect_topics(query))
        total = sum(idf(t) for t in q_tokens) or 1.0
        scored = []
        for chunk, toks in zip(chunks, chunk_tokens):
            overlap = sum(idf(t) for t in q_tokens & toks) / total
            topic_bonus = 0.25 if q_topics & set(chunk.topics) else 0.0
            score = min(1.0, overlap + topic_bonus)
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        results.append([_hit(c, s) for s, c in scored[:k]])
    return results


def best_sentence(chunk_text: str, query: str) -> str:
    """The sentence of a chunk that overlaps most with the query (verbatim)."""
    sentences = [s.strip() for s in re.split(r"(?<=[.;])\s+", chunk_text) if len(s.strip()) > 20]
    if not sentences:
        return chunk_text[:300]
    q = tokens(query)
    return max(sentences, key=lambda s: len(q & tokens(s)))


def quote_in_text(quote: str, text: str) -> bool:
    """Citation check: is the quoted evidence really in the source chunk?"""
    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", s).strip().lower().strip(".\"'")
    q = norm(quote)
    return len(q) >= 15 and q in norm(text)
