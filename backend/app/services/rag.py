"""Knowledge base and Retrieval-Augmented Generation.

Documents (regulations, organisational policies, security standards) are split
into chunks (one per section/clause where possible), tagged with compliance
topics and embedded with an OpenAI embedding model into a pgvector column.

Retrieval:
* semantic  - cosine distance in pgvector (when embeddings exist and OpenAI is configured)
* lexical   - IDF-weighted keyword overlap + topic match (demonstration mode / no embeddings)
* exact     - a query that names a section/clause reference (e.g. "ISP-04") always
              retrieves that section first (hybrid boost for exact clause matching)
Only allowlisted documents of the project's jurisdiction are ever retrieved,
and chunks that look like prompt-injection attempts are excluded.

Agent-specific retrieval (retrieve_for_agent / Knowledge, bottom of this file)
is the single interface every agent uses: each agent sees only the document
types relevant to its job, with its own queries and passage limit, and every
citation an agent makes is checked against the passages it was actually given.
"""
import hashlib
import math
import re
import threading
import time
from collections import OrderedDict

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app import config, llm
from app.database import IS_POSTGRES
from app.models import DocumentChunk, KnowledgeDocument
from app.rules import detect_topics, tokens
from app.security import find_injection, mask_sensitive, wrap_untrusted

GLOBAL_JURISDICTIONS = {"international", "global", "all"}
_MAX_CHUNK_CHARS = 1500

# Kinds of knowledge. Regulatory documents, policies and security standards ground the
# compliance, security and risk agents; engineering guidelines (requirements, SDLC, testing
# and documentation practice) ground the engineering agents; the glossary grounds classification.
REGULATORY_DOC_TYPES = ("regulation", "organisational_policy", "security_standard", "reference")
ENGINEERING_DOC_TYPES = ("engineering_guideline",)
GLOSSARY_DOC_TYPES = ("domain_glossary",)
DOC_TYPES = REGULATORY_DOC_TYPES + ENGINEERING_DOC_TYPES + GLOSSARY_DOC_TYPES
# What a passage IS - shown with every citation, so an internal policy or a reference
# summary is never presented as a regulatory obligation.
AUTHORITY = {"regulation": "Regulatory obligation", "security_standard": "Security standard",
             "organisational_policy": "Internal policy", "reference": "Reference summary (not legal text)",
             "engineering_guideline": "Engineering guideline", "domain_glossary": "Domain glossary"}


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


def chunk_pdf_pages(pages: list[str]) -> list[dict]:
    """Chunk a PDF page by page so every chunk keeps the page number it starts on
    (shown with citations). Each page is chunked like plain text."""
    sections = []
    for number, page in enumerate(pages, start=1):
        for part in chunk_plain_text(page):
            sections.append({**part, "page": number})
    for index, section in enumerate(sections, start=1):
        section["ref"] = f"Part {index}"
    return sections


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
                                        suspicious=bool(suspicious), page=section.get("page")))
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
def _candidate_chunks(db: Session, jurisdiction: str, doc_types: tuple | list | None = None) -> list[DocumentChunk]:
    """Chunks an agent may see: allowlisted documents, not suspicious, of the project's
    jurisdiction (or international) and - when given - of the listed document types."""
    query = (select(DocumentChunk).join(KnowledgeDocument)
             .where(KnowledgeDocument.allowlisted.is_(True), DocumentChunk.suspicious.is_(False))
             .options(joinedload(DocumentChunk.document)))
    if doc_types:
        query = query.where(KnowledgeDocument.doc_type.in_(list(doc_types)))
    rows = db.scalars(query).all()
    allowed = {jurisdiction.lower()} | GLOBAL_JURISDICTIONS
    return [c for c in rows if c.document.jurisdiction.lower() in allowed]


def semantic_available(chunks: list[DocumentChunk]) -> bool:
    """Semantic search is used only when OpenAI is configured AND every candidate
    chunk has an embedding; otherwise un-embedded documents would silently never
    be retrieved, so lexical search is used for all of them instead."""
    return llm.enabled() and bool(chunks) and all(c.embedding is not None for c in chunks)


def retrieve_many(db: Session, queries: list[str], jurisdiction: str, k: int = 4,
                  doc_types: tuple | list | None = None) -> tuple[list[list[dict]], str]:
    """Top-k chunks for every query. Returns (results, method)."""
    chunks = _candidate_chunks(db, jurisdiction, doc_types)
    if not chunks or not queries:
        return [[] for _ in queries], "none"
    results, method = None, "lexical"
    if semantic_available(chunks) and time.monotonic() >= _semantic_paused_until:
        try:
            results, method = _semantic(db, chunks, queries, k), "semantic (pgvector)" if IS_POSTGRES else "semantic"
        except Exception:
            # Embedding service or vector query failed: lexical retrieval now, and for the next few
            # minutes, instead of every agent waiting for the same timeouts and retries again.
            _pause_semantic()
            results = None
    if results is None:
        results = _lexical(chunks, queries, k)
    return [_with_exact_matches(chunks, q, hits, k) for q, hits in zip(queries, results)], method


_CLAUSE_REF = re.compile(r"^[A-Za-z]{2,}(?:-[A-Za-z]+)*-\d+[A-Za-z0-9.]*$")  # e.g. ISP-04, PAY-03, SEC-REF-01


def _with_exact_matches(chunks: list[DocumentChunk], query: str, hits: list[dict], k: int) -> list[dict]:
    """Hybrid boost: a query naming a clause reference retrieves that clause first,
    whatever its embedding or keyword score (exact regulatory clause matching)."""
    exact = [c for c in chunks if _CLAUSE_REF.match(c.section_ref or "")
             and re.search(rf"(?<![\w-]){re.escape(c.section_ref)}(?![\w-])", query, re.I)]
    if not exact:
        return hits
    ids = {c.id for c in exact}
    return ([_hit(c, 1.0) | {"match": "exact clause reference"} for c in exact]
            + [h for h in hits if h["chunk_id"] not in ids])[:max(k, len(exact))]


def _hit(chunk: DocumentChunk, score: float) -> dict:
    doc = chunk.document
    return {"chunk_id": chunk.id, "document_id": doc.id, "document_name": doc.name, "doc_type": doc.doc_type,
            "authority": AUTHORITY.get(doc.doc_type, doc.doc_type), "authenticity": doc.authenticity,
            "jurisdiction": doc.jurisdiction, "version": doc.version, "section_ref": chunk.section_ref,
            "title": chunk.title, "page": chunk.page, "text": chunk.text, "topics": chunk.topics,
            "score": round(score, 3)}


# Query embeddings are reused within the process: several agents retrieve with the same
# requirement text, and the embedding of a text never changes for a given model.
_EMBED_CACHE: "OrderedDict[str, list[float]]" = OrderedDict()
_EMBED_CACHE_SIZE = 4096
_embed_lock = threading.Lock()


def embed_queries(queries: list[str]) -> list[list[float]]:
    def key(text: str) -> str:
        return hashlib.sha256(f"{config.OPENAI_EMBEDDING_MODEL}|{text}".encode()).hexdigest()

    keys = [key(q) for q in queries]
    with _embed_lock:
        found = {kk: _EMBED_CACHE[kk] for kk in keys if kk in _EMBED_CACHE}
    missing = list(dict.fromkeys(q for q, kk in zip(queries, keys) if kk not in found))
    if missing:
        vectors = llm.embed(missing)  # an error here makes retrieve_many fall back to lexical search
        with _embed_lock:
            for text, vector in zip(missing, vectors):
                found[key(text)] = _EMBED_CACHE[key(text)] = vector
            while len(_EMBED_CACHE) > _EMBED_CACHE_SIZE:
                _EMBED_CACHE.popitem(last=False)
    return [found[kk] for kk in keys]


# After a failed semantic search, retrieval is lexical for this long (seconds).
SEMANTIC_PAUSE_SECONDS = 300
_semantic_paused_until = 0.0


def _pause_semantic() -> None:
    global _semantic_paused_until
    _semantic_paused_until = time.monotonic() + SEMANTIC_PAUSE_SECONDS


def clear_embedding_cache() -> None:
    """Forget cached query embeddings and any semantic-search pause (used by the tests)."""
    global _semantic_paused_until
    with _embed_lock:
        _EMBED_CACHE.clear()
    _semantic_paused_until = 0.0


def _semantic(db: Session, chunks: list[DocumentChunk], queries: list[str], k: int) -> list[list[dict]]:
    allowed_ids = {c.id: c for c in chunks}
    vectors = embed_queries(queries)
    results = []
    for vector in vectors:
        if IS_POSTGRES:  # nearest neighbours computed inside PostgreSQL by pgvector
            distance = DocumentChunk.embedding.cosine_distance(vector)
            # In a savepoint: a failing vector query must not abort the analysis's database transaction.
            with db.begin_nested():
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


_TYPOGRAPHY = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                              " ": " "})


def quote_in_text(quote: str, text: str) -> bool:
    """Citation check: is the quoted evidence really in the source chunk? Only whitespace, case and
    typographic quotes/dashes may differ. A quote shortened with "..." is accepted only if EVERY
    fragment is found word-for-word in the passage, in the same order."""
    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", s.translate(_TYPOGRAPHY)).strip().lower().strip(".,;:\"' ")

    source = norm(text)
    fragments = [f for f in (norm(part) for part in re.split(r"\.\.\.|…", quote or "")) if len(f) >= 3]
    if sum(len(f) for f in fragments) < 15:
        return False
    position = 0
    for fragment in fragments:
        found = source.find(fragment, position)
        if found < 0:
            return False
        position = found + len(fragment)
    return True


# --------------------------------------------------------------------------
# Agent-specific retrieval (the one interface every agent uses)
# --------------------------------------------------------------------------
# For each agent: the document types it may see, what it looks for (prefixed to its own
# queries) and its limits - `per_query` passages per query, `k` passages per prompt.
AGENT_PROFILES = {
    "clarification": {"label": "Clarification Question Generator", "k": 5, "per_query": 2,
                      "doc_types": REGULATORY_DOC_TYPES + GLOSSARY_DOC_TYPES,
                      "focus": "Obligations and controls a financial application may need to address"},
    "classification": {"label": "Requirement Classification Agent", "k": 5, "per_query": 2,
                       "doc_types": GLOSSARY_DOC_TYPES,
                       "focus": "Financial-sector domain term and the requirement categories it implies"},
    "quality": {"label": "Requirement Quality & Conflict Agent", "k": 8, "per_query": 1,
                "doc_types": ENGINEERING_DOC_TYPES, "focus": "Requirements quality guideline"},
    "compliance": {"label": "Compliance & Regulatory Agent", "k": 4, "per_query": 4,
                   "doc_types": REGULATORY_DOC_TYPES, "focus": "Regulatory obligation or control"},
    "security": {"label": "Security & Privacy Agent", "k": 6, "per_query": 2,
                 "doc_types": ("security_standard", "organisational_policy", "regulation", "reference"),
                 "focus": "Security and privacy control"},
    "risk": {"label": "Risk Analysis Agent", "k": 5, "per_query": 2, "doc_types": REGULATORY_DOC_TYPES,
             "focus": "Operational, technology, cyber-security and third-party risk management"},
    "sdlc": {"label": "SDLC Selection Agent", "k": 4, "per_query": 2, "doc_types": ENGINEERING_DOC_TYPES,
             "focus": "SDLC delivery model guidance"},
    "workflow": {"label": "SDLC Workflow Generation Agent", "k": 6, "per_query": 1,
                 "doc_types": ENGINEERING_DOC_TYPES + ("organisational_policy", "security_standard"),
                 "focus": "Engineering practice for the SDLC phase"},
    "documentation": {"label": "Documentation Agent", "k": 3, "per_query": 3, "doc_types": ENGINEERING_DOC_TYPES,
                      "focus": "Documentation standard for user stories, acceptance criteria and use cases"},
    "validation": {"label": "Validation Agent", "k": 5, "per_query": 1, "doc_types": ENGINEERING_DOC_TYPES,
                   "focus": "Testing and validation standard"},
}
_AUTHENTICITY_RANK = {"official": 0, "illustrative": 1, "reference": 2}  # tie-break: most authoritative first


def requirement_query(req: dict) -> str:
    """A requirement with its clarification/stakeholder answers and categories (sensitive values masked)."""
    from app.agents.clarification_context import supplementary_text

    categories = ", ".join(c.replace("_", " ") for c in req.get("categories", []))
    text = supplementary_text(req) + (f" ({categories})" if categories else "")
    return mask_sensitive(text)[0][:1000]


def project_query(ctx: dict, agent: str) -> str:
    """Agent-level context: what the agent looks for in THIS project (domain, jurisdiction, description)."""
    p = ctx["project"]
    text = (f"{AGENT_PROFILES[agent]['focus']} for a {p['domain'].replace('_', ' ')} application in "
            f"{p['jurisdiction']}: {p.get('name', '')}. {p.get('description', '')}")
    return mask_sensitive(text)[0][:1000]


def retrieve_for_agent(ctx: dict, agent: str, items: list[str] | None = None,
                       requirements: list[dict] | None = None, include_project: bool = True) -> "Knowledge":
    """Retrieve the knowledge ONE agent needs and return it as a Knowledge object.

    items           - agent-specific queries (e.g. one per security gap or SDLC phase); the hits
                      of each item are kept so evidence can be linked to that item. The document-type
                      filter (not extra words in the query) keeps them within the agent's knowledge.
    requirements    - one query per requirement (statement + clarifications + categories)
    include_project - one query from the project description, domain and jurisdiction
    Never raises: if retrieval fails the agent continues without knowledge (recorded in the log)."""
    profile = AGENT_PROFILES[agent]
    knowledge = Knowledge(ctx, agent)
    item_queries = [mask_sensitive(q)[0][:1000] for q in items or []]
    req_queries = [requirement_query(r) for r in requirements or []]
    queries = item_queries + req_queries + ([project_query(ctx, agent)] if include_project else [])
    if not queries:
        return knowledge
    try:
        results, method = retrieve_many(ctx["db"], queries, ctx["project"]["jurisdiction"], k=profile["per_query"],
                                        doc_types=profile["doc_types"])
    except Exception as exc:  # e.g. a database error - the analysis goes on without knowledge
        knowledge.method = knowledge.log["method"] = "failed"
        knowledge.log["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return knowledge
    knowledge.load(queries, results, method, len(item_queries), [r["req_id"] for r in requirements or []])
    return knowledge


class Knowledge:
    """The passages retrieved for one agent run, and the checks on what the agent cites.

    Everything is recorded in ctx["retrieval_log"] (stored with the agent's run): the
    retrieval method, the queries, the passages retrieved and those placed in prompts, and
    how many citations were verified, linked, flagged or rejected."""

    def __init__(self, ctx: dict, agent: str):
        self.ctx, self.agent = ctx, agent
        self.profile = AGENT_PROFILES[agent]
        self.method = "none"
        self.items: list[list[dict]] = []
        self.by_requirement: dict[str, list[dict]] = {}
        self.general: list[dict] = []
        self.shown: dict[int, dict] = {}  # chunk_id -> passage actually placed in a prompt
        self.log = {"agent": self.profile["label"], "key": agent, "method": "none",
                    "doc_types": list(self.profile["doc_types"]), "queries": 0, "query_examples": [],
                    "retrieved": [], "prompted": [], "verified": 0, "unverified": 0, "rejected": 0, "linked": 0,
                    "error": ""}
        ctx.setdefault("retrieval_log", []).append(self.log)

    # -- retrieval results ------------------------------------------------
    @property
    def available(self) -> bool:
        """True when the knowledge base could be searched for this agent."""
        return self.method not in ("none", "failed")

    @property
    def min_score(self) -> float:
        return config.RAG_MIN_SEMANTIC_SCORE if self.method.startswith("semantic") else config.RAG_MIN_LEXICAL_SCORE

    @property
    def link_score(self) -> float:
        """Evidence linked WITHOUT a model quote (rule-based findings) needs a clearly relevant passage."""
        return config.RAG_LINK_SEMANTIC_SCORE if self.method.startswith("semantic") else config.RAG_LINK_LEXICAL_SCORE

    def load(self, queries: list[str], results: list[list[dict]], method: str, n_items: int,
             requirement_ids: list[str]) -> None:
        self.method = method
        relevant = [[h for h in hits if h["score"] >= self.min_score] for hits in results]
        self.items = relevant[:n_items]
        for rid, hits in zip(requirement_ids, relevant[n_items:n_items + len(requirement_ids)]):
            self.by_requirement[rid] = hits
        self.general = [h for hits in relevant[n_items + len(requirement_ids):] for h in hits]
        self.log.update({"method": method, "queries": len(queries), "query_examples": [q[:160] for q in queries[:3]],
                         "retrieved": [brief_hit(h) for h in merge_hits(relevant)[:25]]})

    def passages(self, limit: int | None = None) -> list[dict]:
        """The most relevant passages across all of this agent's queries."""
        return merge_hits([*self.items, *self.by_requirement.values(), self.general], self._limit(limit))

    def for_requirements(self, ids, limit: int | None = None) -> list[dict]:
        """Passages retrieved for these requirements only (e.g. one LLM batch)."""
        return merge_hits([self.by_requirement.get(i, []) for i in ids], self._limit(limit))

    def for_items(self, indexes=None, limit: int | None = None) -> list[dict]:
        chosen = self.items if indexes is None else [self.items[i] for i in indexes if i < len(self.items)]
        return merge_hits(chosen, self._limit(limit))

    def item_hits(self, index: int) -> list[dict]:
        return self.items[index] if index < len(self.items) else []

    def _limit(self, limit: int | None) -> int:
        return min(limit or self.profile["k"], config.RAG_MAX_PASSAGES)

    # -- prompts ------------------------------------------------------------
    def block(self, passages: list[dict]) -> str:
        """Passages formatted for a prompt, wrapped as untrusted reference DATA (never instructions)."""
        if not passages:
            return ""
        for h in passages:
            if h["chunk_id"] not in self.shown:
                self.shown[h["chunk_id"]] = h
                self.log["prompted"].append(brief_hit(h))
        text = "\n\n".join(
            f"[chunk_id={h['chunk_id']}] {h['document_name']} - {h['section_ref']} {h['title']} "
            f"(type={h['authority']}, authenticity={h['authenticity']}, jurisdiction={h['jurisdiction']}, "
            f"version={h['version'] or 'n/a'}" + (f", page={h['page']}" if h.get("page") else "") + ")\n"
            + h["text"][:config.RAG_PASSAGE_CHARS] for h in passages)
        return wrap_untrusted("knowledge_base_passages", text)

    # -- citations ----------------------------------------------------------
    def cite(self, chunk_id: int, quote: str, claim: str = "") -> dict | None:
        """Check a citation made by the model. Only passages placed in this agent's prompt can be
        cited (anything else is rejected and counted as an invalid reference); the quote must be
        found word-for-word in that passage, otherwise the citation is kept but flagged as
        unverified and the closest real sentence of the passage is shown instead."""
        from app.agents import check_reference

        hit = self.shown.get(chunk_id)
        if not check_reference(self.ctx, self.agent, hit is not None):
            self.log["rejected"] += 1
            return None
        verified = check_reference(self.ctx, self.agent, quote_in_text(quote, hit["text"]))
        self.log["verified" if verified else "unverified"] += 1
        excerpt = quote if verified else best_sentence(hit["text"], claim or quote)
        citation = self._citation(hit, excerpt, verified, "quote_verified" if verified else "quote_not_found", claim)
        if not verified:  # kept for the reviewer: what the model claimed the passage says
            citation["model_quote"] = (quote or "")[:300]
        return citation

    def cite_all(self, citations, claim: str = "") -> list[dict]:
        """`citations`: model output items with .chunk_id and .quote."""
        return [c for c in (self.cite(x.chunk_id, x.quote, claim) for x in citations or []) if c]

    def link(self, hits: list[dict], claim: str, topics: set[str] | None = None,
             min_score: float | None = None) -> dict | None:
        """Evidence for a rule-based finding (no model quote): the best clearly relevant passage,
        with its sentence most related to the finding copied verbatim. `min_score` overrides the
        relevance threshold when relevance was already established another way (e.g. a shared topic)."""
        threshold = self.link_score if min_score is None else min_score
        for hit in hits:
            if hit["score"] < threshold or (topics and not topics & set(hit.get("topics") or [])):
                continue
            excerpt = best_sentence(hit["text"], claim)
            if quote_in_text(excerpt, hit["text"]):
                self.log["linked"] += 1
                return self._citation(hit, excerpt, True, "retrieval_link", claim)
        return None

    def status(self, citations: list[dict]) -> str:
        """verified | unverified (cited, but no quote could be verified) | none (no supporting passage:
        derived from the requirements and the analysis) | unavailable (knowledge base not searchable)."""
        if any(c["verified"] for c in citations):
            return "verified"
        if citations:
            return "unverified"
        return "none" if self.available else "unavailable"

    def summary(self) -> str:
        log = self.log
        if not self.available:
            return " Knowledge base not available for this agent." if (log["error"] or log["queries"]) else ""
        return (f" Knowledge base: {len(log['retrieved'])} relevant passage(s) via {self.method} retrieval, "
                f"{len(log['prompted'])} given to the model; citations: {log['verified']} verified, "
                f"{log['linked']} linked, {log['unverified']} unverified, {log['rejected']} rejected.")

    def _citation(self, hit: dict, excerpt: str, verified: bool, verification: str, claim: str) -> dict:
        return {"agent": self.profile["label"], "chunk_id": hit["chunk_id"], "document_id": hit["document_id"],
                "document_name": hit["document_name"], "doc_type": hit["doc_type"], "authority": hit["authority"],
                "authenticity": hit["authenticity"], "jurisdiction": hit["jurisdiction"], "version": hit["version"],
                "section_ref": hit["section_ref"], "title": hit["title"], "page": hit.get("page"),
                "excerpt": excerpt[:600], "verified": verified, "verification": verification,
                "retrieval": self.method, "score": hit["score"], "claim": (claim or "")[:200]}


def brief_hit(hit: dict) -> dict:
    return {"chunk_id": hit["chunk_id"], "document": hit["document_name"], "section_ref": hit["section_ref"],
            "title": hit["title"], "doc_type": hit["doc_type"], "authenticity": hit["authenticity"],
            "score": hit["score"]}


def merge_hits(hit_lists, limit: int | None = None) -> list[dict]:
    """Combine the hits of several queries: one entry per chunk (best score), identical texts
    removed, most relevant first (most authoritative first on equal scores)."""
    best: dict[int, dict] = {}
    for hits in hit_lists:
        for h in hits:
            if h["chunk_id"] not in best or h["score"] > best[h["chunk_id"]]["score"]:
                best[h["chunk_id"]] = h
    ranked = sorted(best.values(), key=lambda h: (-h["score"], _AUTHENTICITY_RANK.get(h["authenticity"], 3)))
    seen, result = set(), []
    for h in ranked:
        key = " ".join(h["text"].split()).lower()
        if key not in seen:
            seen.add(key)
            result.append(h)
    return result[:limit] if limit else result


def citation_text(citations: list[dict]) -> str:
    """Short plain-text form of citations (the `evidence` text field and the exports)."""
    return "; ".join(f"{c['document_name']} {c['section_ref']} ({c['authenticity']}"
                     + ("" if c["verified"] else ", unverified") + ")" for c in citations)
