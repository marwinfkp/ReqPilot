# ReqPilot — P2 Knowledge Base & RAG

**Roadmap phase:** P2 (`docs/01-analysis.md` §P) · **Module:** M5 Knowledge & retrieval
**Status: P2 IMPLEMENTATION COMPLETE — ET-06 PENDING**

| Level | Status |
|---|---|
| **P2 implementation checklist** (brief §34) | **Complete.** Every item met, verified offline and against a live PostgreSQL + pgvector (§18, §19); closure audit passed (§24) |
| **P2 roadmap exit** (analysis §P) | **Not yet passed.** Two of three criteria met. The third, **ET-06** (recall@5 ≥ 0.80 on a 20-question probe), is **pending external/team curation** of the corpus and the probe set, and has **not been measured** (§22) |

P2 does not claim to have passed its roadmap exit. The ET-06 scoring harness is implemented and
tested; what it has not had is the approved, curated corpus to score. The status becomes
`P2 COMPLETE` only when ET-06 is measured against that corpus and passes. No regulatory content has
been invented or fetched to force it.

**Tagging:** `[PS §n]` problem statement · `[P0 §x]` approved Phase 0 analysis · `[DESIGN]`
architecture decision · **P2 decision** = an implementation decision made here, listed in §23.

> **The LLM proposes; deterministic code disposes.** P2 contains no LLM call, no agent and no graph
> node. Everything it adds is deterministic: which chunks a project may see, how they rank, what
> becomes evidence, and whether a citation resolves.

---

## 1. Scope

P2 builds the grounding layer that later agents will consume (analysis §P: *"Typed KB per C.1 with
full metadata, chunking, embeddings, hybrid retrieval, citation resolution, allowlisting, KB admin"*):

- a typed, curated, versioned **knowledge corpus** (`normative_source`, `control`, `knowledge_item`,
  `knowledge_chunk`) — architecture G.5;
- a per-project **retrieval scope**: `source_allowlist`, `project.jurisdiction_scope`,
  `project.kb_version_pin` — G.3, G.5;
- **ingestion**: curated text or `.txt/.md/.pdf/.docx` → structure-aware chunks with exact offsets →
  local embeddings → PostgreSQL + pgvector — J.2, J.3, ADR-005;
- **hybrid retrieval**: pgvector cosine + PostgreSQL full text, fused by reciprocal rank fusion, with
  the allowlist as a **join inside the query** — J.4, ADR-004;
- an explicit **`RETRIEVAL_SUCCESS` / `RETRIEVAL_EMPTY`** outcome — `FR-RAG-005`;
- immutable **evidence** and exact **citation resolution** — G.6, J.5, `FR-RAG-003`;
- **KB administration**: add, version, retire, supersede — audited — `FR-RAG-006`;
- the **ET-06 probe harness** (recall@k) — the P2 exit gate's measuring instrument.

Out of scope, and absent: every agent, the LLM gateway beyond its P0 stub, every graph, project-
document ingestion, masking, compliance mapping, supersession flagging (`FR-RAG-007`), and any real
regulatory content (§21).

## 2. Source hierarchy used

1. `Problem Statement.docx` / `docs/problem-statement.md`
2. `docs/01-analysis.md` — C.1 taxonomy, D.2 jurisdiction and copyright constraints, G.9 `FR-RAG-*`,
   H.2 ET-06, §P roadmap and P2 exit test
3. `docs/02-architecture.md` — ADR-003/004/005, G.3, G.5, G.6, J.1–J.6, O.2, P, S, U, V
4. `docs/03-p0-foundations.md`, `docs/04-p1-requirements-repository.md`
5. The P2 implementation brief

Where the brief and the roadmap differ, the roadmap wins: its P2 exit test includes ET-06, which the
brief's checklist omits. That criterion is reported, not dropped (§22).

## 3. Requirements implemented

| ID | Requirement (abridged) | Tier | P2 status |
|---|---|---|---|
| `FR-RAG-001` | Curated, versioned KB; every item records source, **type (C.1)**, jurisdiction, effective date, version, applicability, retrieval date | MVP | **Implemented** — §5 |
| `FR-RAG-002` | Classify the query, then retrieve restricted to an allowlist | MVP | **Implemented** — allowlist as a join (§12); the *classification contract* is typed and applied, inference is P3/P6 (§11) |
| `FR-RAG-003` | Evidence to the LLM; citations resolvable to the exact chunk | MVP | **Foundation implemented** — evidence, run evidence sets, resolution, deterministic citation checks (§14). Supplying evidence *to a model* is P6 |
| `FR-RAG-004` | Confidence signal on generated items | MVP | **Not P2** — there are no generated items yet. Retrieval scores are relevance data, not confidence (§21) |
| `FR-RAG-005` | Escalate when retrieval returns nothing relevant | MVP | **Implemented** — explicit `RETRIEVAL_EMPTY` + `requires_human_review`, and `require_grounding()` refuses to proceed (§11) |
| `FR-RAG-006` | KB administration: add, version, retire, audited | MVP | **Implemented** — §15 |
| `FR-RAG-007` | Flag requirements whose supporting item was superseded | SEC | **Deferred** (as approved). The schema carries what it needs: supersession kind, successor, and the KB version it happened in (§6) |

## 4. Architecture components implemented

```
src/reqpilot/
├── retrieval/                 M5, pure and deterministic — imports only the domain and rules
│   ├── chunking.py            J.3: clause / window / section / utterance strategies, exact offsets
│   ├── embeddings.py          ADR-005: EmbeddingProvider; bge-small (local CPU); offline test provider
│   ├── fusion.py              J.4: reciprocal rank fusion
│   ├── contracts.py           FR-RAG-002/003/005: query, classification, result, citation types
│   ├── extraction.py          J.2: txt / md / pdf (pypdf) / docx (python-docx) only
│   └── rules.py               the versioned retrieval ruleset, validated
├── repositories/knowledge.py  all KB SQL; scoped_chunks() carries the allowlist join
├── services/knowledge/        admin · scope · retrieval · evidence · seed
├── services/evaluation/retrieval_probe.py   ET-06 recall@k harness
├── rules/data/retrieval.yaml  chunk sizes, fusion weights, relevance thresholds (versioned)
├── api/routes/knowledge.py    §16
└── web/knowledge.py           §16
```

**Layering.** `retrieval` sits below `repositories` and above `domain` in the import-linter layer
contract, and is added to the "no LLM, no orchestration" contract beside `domain`, `repositories` and
`services`. The SQL lives in the repository layer on purpose: it is where authorisation happens, so
retrieval cannot be reached around it.

## 5. Knowledge-item model

| Table | Holds | Mutability |
|---|---|---|
| `normative_source` | C.1 `source_type`, `issuing_body`, `title`, `jurisdiction`, `version` (**source version**), `effective_date`, `retrieved_at` (curation date), `source_url`, `licence_class`, `licence_note` | **Append-only.** An amended instrument is a new row |
| `control` | `control_ref`, `title`, team-written `paraphrase`, `applicability[]` | Append-only |
| `knowledge_item` | `item_key` + `version_no` (**item version**), source, optional control, `title`, `clause_ref`, `text`, `text_origin`, `content_hash`, `applicability[]`, `kb_version`, status and supersession columns | Content immutable; supersession columns written **once** |
| `knowledge_chunk` | deterministic `id`, `ordinal`, `char_start`/`char_end`, `text`, `text_hash`, `strategy`, `structure_label`, `token_count`, `embedding vector(384)`, `embedding_model` | Append-only |
| `source_allowlist` | `(project_id, normative_source_id)` | Rows added and removed; every change audited |
| `evidence` | §14 | Append-only |

**Taxonomy.** `NormativeSourceType` has exactly the eight C.1 types, spelled as G.5 spells them. There
is no generic "regulation". Every type carries its C.1 binding description (`SOURCE_TYPE_BINDING`),
which every retrieved chunk and citation exposes, so a standard is never presented as law.

**Licence** (G.5: *"the ingestion path refuses full text where the note forbids it"*). The free-text
`licence_note` is kept, and a machine-checkable `licence_class` beside it makes the rule enforceable:

| `licence_class` | Admits `text_origin` | For |
|---|---|---|
| `extract_permitted` | `verbatim_extract`, `team_paraphrase` | Statutes, regulator texts, public-domain frameworks [P0 §D.2] |
| `paraphrase_only` | `team_paraphrase` only | Copyrighted standards (ISO/IEC): identifiers + team paraphrase [P0 §D.2] |
| `synthetic` | `synthetic` only | Fictional material — and only for `org_policy` or `best_practice` |

That last rule means **the code refuses to create a synthetic statute, direction, guidance, scheme,
standard or framework.** ReqPilot cannot be used to invent law.

## 6. Versioning and supersession

Four identities, kept distinct:

| Concept | Where | Changes when |
|---|---|---|
| **Source version** | `normative_source.version` | The external instrument is amended → a new source row |
| **Item version** | `(item_key, version_no)` | A curator revises the item → `version_no + 1` |
| **KB version** | `knowledge_item.kb_version`, `superseded_in_kb_version` | **Every** curation action allocates the next number (serialised by a PostgreSQL advisory lock) |
| **Chunk identity** | `knowledge_chunk.id` = `uuid5(item, ordinal, span, text hash)` | Never — derived from what the chunk is |

Status stays two-valued, as G.5 specifies (`active → superseded`). G.5 has no `retired` status and
O.2 has no retire event, so neither is added. Every operation allocates the next KB version, *n+1*.
Two further columns carry what the status alone cannot, and the database makes them agree:

| Operation | `FR-RAG-006` / S | Item afterwards | `supersession_kind` | `superseded_by_id` | Audit event and payload |
|---|---|---|---|---|---|
| `add_item` | *add* / `POST /kb/items` | v1 `active` | — | — | `KB_ITEM_ADDED` |
| `version_item` | *version* | predecessor `superseded` | `versioned` | the new version, **same** `item_key` | `KB_ITEM_SUPERSEDED`, `kind: versioned`, `successor_id` |
| `supersede_item` | *supersede* / `POST /kb/items/{id}/supersede` | `superseded` | `replaced` | a **different** item, e.g. under an amended instrument | `KB_ITEM_SUPERSEDED`, `kind: replaced`, `successor_id` |
| `retire_item` | *retire* | `superseded` | `retired` | **none** | `KB_ITEM_SUPERSEDED`, `kind: retired`, `successor_id: null` |

**How an administrative retirement differs from a replacement.**

- **Meaning.** *Retired* means withdrawn by an administrator, with nothing in its place. *Versioned*
  and *replaced* mean that something took its place.
- **Where the difference lives.** The status is the same `superseded` in all three cases.
  `supersession_kind` and `superseded_by_id` carry the difference.
- **The database keeps them consistent.** The check constraint
  `ck_knowledge_item_supersession_kind_matches_successor` enforces *retired ⇔ no successor*. A row
  that says `retired` but has a successor is refused. So is a row that says `versioned` or `replaced`
  with no successor. Both are tested for all three kinds.
- **The audit trail.**
  - The event is O.2's `KB_ITEM_SUPERSEDED`. It names the **status transition**.
  - The payload's `kind` names the **operation**, and `successor_id` sits beside it.
  - To list retirements, an auditor filters on `KB_ITEM_SUPERSEDED` with `kind = retired`.
  - Every such event carries exactly one of the three kinds, and `kind = retired` exactly when
    `successor_id` is null. Both are tested.
- **The reason.** It is required and stored on the item as `supersession_reason`. It is not in the
  audit payload, because payloads carry references only (P0 rule).
- **Effect on retrieval and evidence.** This is the same for all three kinds:
  - the item stops being retrievable from its KB version onwards;
  - a project pinned to an earlier KB version still sees it;
  - evidence that cites it still resolves.
- **Where it is shown.** The API and UI display `supersession_kind` beside the status.
- **Deferred.** `FR-RAG-007` flagging is deferred. When it is built, the kind lets it treat
  `retired` (nothing to move to) differently from `versioned` or `replaced` (a successor to review).

**Pinning (J.6).** A project pinned to KB version *p* sees exactly the items with
`kb_version ≤ p` and (`superseded_in_kb_version` is null or `> p`). Without
`superseded_in_kb_version` that is impossible: a status column alone cannot say what was active in an
earlier version. Tested: a pinned project's retrieval is unchanged by a later version and a later
addition; unpinning it shows the new ones.

## 7. Ingestion

Manual curation only; no live feeds (J.6). Two paths, both through `KnowledgeAdminService`, so the
same rules apply everywhere:

- **API/UI** — `POST /kb/items` with the item's text (architecture S).
- **Manifest** — `scripts/seed_kb.py` loads a curated `manifest.yaml`, with inline text or a
  `text_file` extracted by the J.2 parsers. Paths cannot escape the manifest's directory. Idempotent:
  existing sources are reused, existing item keys skipped.

For each item: licence check → normalise (`\r\n` → `\n`, so offsets are platform-independent) →
content-hash dedupe (the same text is never active twice under one source, J.2) → allocate KB
version → persist → chunk → embed → persist chunks → audit `KB_ITEM_ADDED` then `SOURCE_INGESTED`.
One transaction: an item that exists was ingested and audited.

The J.2 masking step is not applied to curated normative text: it exists for uploaded *project*
content, whose ingestion is deferred (§21).

## 8. Chunking

Architecture J.3, verbatim, with sizes in the versioned ruleset:

| Corpus | Strategy | P2 |
|---|---|---|
| Knowledge items | One chunk per clause/control where the source has that structure; otherwise ~500 tokens with 80 overlap | Implemented and wired |
| Project documents | ~700 tokens, 100 overlap, headings and paragraphs first | Implemented, unit-tested, not wired (project corpus deferred) |
| Transcripts | One chunk per utterance; never split a speaker turn | Implemented, unit-tested, not wired (utterances are P4) |

- **Clause markers** at the start of a line: `Section 8`, `Article 3`, `A.5.15`, dotted numbers
  (`1.`, `3.1`), Markdown headings. A bare number is not a clause, so "5 days…" never splits one.
  Two or more markers switch clause mode on; a clause longer than 500 tokens is windowed inside
  itself and keeps its label.
- **Tokens** are counted deterministically (word runs and punctuation marks), so chunk boundaries do
  not move if a model's tokenizer changes. The 500-token size stays under bge-small's 512-token input.
- **The invariant** `chunk.text == source[char_start:char_end]` is enforced in the `TextChunk` value
  object, checked again before persisting, and checked again when a citation resolves. A property test
  over 25 generated documents confirms it for every strategy.

## 9. Embedding provider

`EmbeddingProvider` (a `Protocol`: `model_id`, `dimension`, `embed_documents`, `embed_query`) is all
that retrieval knows about. Two implementations:

- **`SentenceTransformerEmbeddingProvider`** — the approved `BAAI/bge-small-en-v1.5`, local CPU,
  384 dimensions, normalised; the bge query instruction is applied to queries only. Any other model
  name is refused (ADR-004: changing models needs a re-embed migration). It loads lazily — importing
  the retrieval package never loads torch — and **only from the local cache** unless
  `EMBEDDING_ALLOW_DOWNLOAD=true`. The dependency is an optional extra, `pip install -e ".[embeddings]"`.
- **`HashingEmbeddingProvider`** — deterministic, dependency-free, **non-semantic** (shared-vocabulary
  similarity). It is what the offline suite uses (ET-10), which makes "Y matches better than X"
  exactly controllable in the adversarial tests. The configuration refuses it in production, and the
  ET-06 probe refuses to count its results.

Every chunk records its `embedding_model`, and retrieval compares only vectors from the active model.

What the closure audit verified, and the test behind each claim:

| Property | Evidence |
|---|---|
| `sentence-transformers` is optional | It appears only under `[project.optional-dependencies] embeddings`. A subprocess test makes it unimportable and checks four things: the app starts, `/health` answers, the offline provider works, and the approved provider fails with `EmbeddingUnavailableError` naming the extra |
| Importing the application does not load the model | After importing `reqpilot.main`, the API, the UI and the probe harness, neither `torch` nor `sentence_transformers` is in `sys.modules` (subprocess test) |
| Offline tests never download | Unless `EMBEDDING_ALLOW_DOWNLOAD=true`, the provider passes `local_files_only=True` and sets `HF_HUB_OFFLINE=1`; a test asserts this against a stand-in library. The real-model tests load from the local cache or skip. Both full runs pass with all HTTP(S) routed to a dead proxy |
| The offline provider is deterministic | The same text always gives the same vector, across calls and between `embed_query` and `embed_documents` |
| bge-small stays behind `EmbeddingProvider` | `sentence_transformers` is imported in one method of `retrieval/embeddings.py` and nowhere else. Retrieval, ingestion, evidence and the API depend only on the protocol |
| The selected model is unchanged | `BAAI/bge-small-en-v1.5`; any other name is refused |

## 10. pgvector storage

`knowledge_chunk.embedding vector(384)` with an **HNSW** index (`vector_cosine_ops`), and a **GIN**
index on the expression `to_tsvector('english'::regconfig, text)` — the query uses the identical
expression so the index is usable. Both are created by migration `0004` on PostgreSQL only; SQLite
stores the vector as text and is never used for retrieval.

## 11. Hybrid retrieval

```
project scope from the database  (allowlist, jurisdictions, KB pin — never from the caller)
  → empty scope?          → RETRIEVAL_EMPTY (no_jurisdiction_scope | no_allowlisted_sources)
  → embed the query locally
  → vector candidates     ⎫ both built on scoped_chunks(): allowlist JOIN + jurisdiction +
  → keyword candidates    ⎭ effective date + status/pin + embedding model, all before LIMIT
  → nothing relevant?     → RETRIEVAL_EMPTY (nothing_relevant)
  → reciprocal rank fusion  (orders candidates; never adds one)
  → chunk details          (scoped join again)
  → RETRIEVAL_SUCCESS with ranked, fully-provenanced chunks
```

- **Vector:** `1 − (embedding <=> q)` ≥ the model's threshold from the ruleset.
- **Keyword:** `websearch_to_tsquery` (every query term must match), ranked by `ts_rank_cd`. This is the
  exact-term half ADR-004 calls for: control identifiers and defined terms that embeddings blur. A
  chunk far from the query in vector space is still found by keyword (tested).
- **Fusion:** `Σ weightᵣ / (k + rankᵣ)`, `k = 60`, both weights 1.0, ties broken by best rank then id.
- **Relevance thresholds are per model** (versioned rule data, because they decide `FR-RAG-005`):
  bge-small 0.55 (provisional — measured on this machine, bge-small puts on-topic pairs around 0.64–
  0.67 and unrelated ones around 0.33–0.40), test provider 0.15. A model with no declared threshold
  cannot retrieve at all.
- **`FR-RAG-002` classification** is a typed contract (`QueryClassification`: `source_types`,
  `applicability`, `requirement_category`) whose fields can only **narrow**. There is no field that
  names a source, a jurisdiction or a KB version. P2 does not infer a classification; P3's
  Classification role and P6's applicable-source step will populate it.
- **Result.** Each chunk carries rank, fused score, vector similarity, both ranks, span, text,
  structure label, and the full J.5 provenance (source title, C.1 type and binding, issuing body,
  jurisdiction, source version, effective date, curation date). The result carries the KB version
  seen, whether it was pinned, the embedding model, the ruleset version and the query hash.
- **`FR-RAG-005`: the P2 boundary.** P2's share of "escalate" is to make the empty outcome
  explicit, deterministic and impossible to mistake for success:
  `retrieval → RETRIEVAL_EMPTY (reason, requires_human_review = true) → a later phase escalates`.
  - `RetrievalResult` cannot represent "success with nothing in it". `SUCCESS` requires chunks.
    `EMPTY` requires a reason and `requires_human_review=True`.
  - The reason is one of three, decided by rules only: `no_jurisdiction_scope`,
    `no_allowlisted_sources`, `nothing_relevant`.
  - The same inputs give the same outcome. This is tested for all three empty reasons and for
    success; only the per-call `retrieval_id` differs.
  - `require_grounding()` raises on `EMPTY`, and evidence cannot be recorded from it.
  - **P2 does not implement the review workflow.** An empty retrieval creates no approval task, no
    decision, no graph or agent run, no evidence and no audit event. A test counts those tables
    before and after to prove it.
  - Architecture C.8 puts the reaction ("no claim generated, review task created") in the graph.
    It belongs to the orchestration phases that consume retrieval, starting with P6's compliance
    analysis.

## 12. Allowlist enforcement

The allowlist is an inner join on `(source, project)` inside `scoped_chunks()`, the one function every
knowledge-returning query is built on. `scoped_chunks()` has no parameter that removes it (a test
pins its signature). The critical case is proved **three independent ways** against live PostgreSQL:

1. **Control case.** In project B, where both are allowlisted, Y outranks X — so Y really is the
   better match.
2. **Behavioural proof of in-query filtering.** In project A (X only), with `top_k = 1`: X is returned.
   Had Y been removed *after* `LIMIT 1`, the only slot would have gone to Y and the result would be
   empty.
3. **Raw rows.** The repository's vector and keyword statements are executed directly: Y's chunks are
   not among the rows the database returns at all.

Plus: classification naming Y's source type still cannot reach Y; evidence cannot be recorded for a
non-allowlisted chunk (§14); the same holds over HTTP, including a request carrying extra
`normative_source_ids` / `jurisdiction_scope` fields, which are ignored.

## 13. Jurisdiction and effective-date filtering

- `normative_source.jurisdiction` is an ISO 3166 alpha-2 code or `INTL` (the cross-cutting catalogues
  of D.2). A project retrieves only from `project.jurisdiction_scope`. **An empty scope fails closed**:
  `RETRIEVAL_EMPTY (no_jurisdiction_scope)`, never "all jurisdictions".
- `effective_date IS NULL OR effective_date ≤ as_of`. `as_of` defaults to today and may be historical;
  a future `as_of` is refused, because it would admit instruments not yet in force.
- The jurisdiction question (Q2) is still open, so the infrastructure is generic. No real regulatory
  corpus is loaded (§21).

## 14. Evidence and citations

```
retrieved chunk → evidence row → evidence id → (P6) a model cites it
               → deterministic validation → citation resolves to the exact source span
```

- **Evidence comes only from retrieval.** `EvidenceService.record()` takes a `RetrievalResult`, never
  a list of ids. It re-checks every chunk through the **same** `scoped_chunks()` join, requires the
  stored chunk to match the result's text and span, and takes the quote from the database. A forged,
  tampered, superseded or non-allowlisted chunk cannot become evidence. `RETRIEVAL_EMPTY` is refused.
- **Evidence row** (G.6 plus provenance): `kind = knowledge_item` (F.3), `target_id` = item,
  `knowledge_chunk_id`, span, `quote`, `quote_hash`, `retrieval_id`, rank, fused score, `kb_version`,
  `embedding_model`, `ruleset_version`, `query_hash`, optional `graph_run_id` / `agent_run_id`
  (the run must belong to the project), `created_by`. One row per chunk per retrieval.
- **Self-contained history.** Evidence resolves after the item is superseded or retired and after the
  source leaves the allowlist; resolution is scoped by the evidence row's own project, not by today's
  allowlist. Chunks cannot be deleted while evidence references them.
- **Resolution** verifies `quote == chunk.text == item.text[span]`, the hash, the target and the span.
  A mismatch raises `EvidenceIntegrityError`; it is never resolved to text nobody saw.
- **Run evidence sets (J.5).** `resolve_citation(..., allowed_evidence_ids=…)` refuses any id outside
  the run's `evidence_ids` with the same answer as for a nonexistent or cross-project id.
  `check_citations()` is the non-raising verdict the P6 validation stage will consume: every cited id
  is resolved or rejected with a reason, including non-UUID strings a model might emit.

## 15. Administration

`FR-RAG-006` through `KnowledgeAdminService`, authorised by `policy.can` in the repository layer,
audited through the one append-only `AuditService`, in the caller's transaction:

| Event | Chain | When | Payload (references only) |
|---|---|---|---|
| `KB_SOURCE_ADDED` | global | source registered | type, jurisdiction, licence class |
| `KB_CONTROL_ADDED` | global | control added | source id, control ref |
| `KB_ITEM_ADDED` *(O.2)* | global | item or version added | item key, KB version, source id, content hash, origin, predecessor |
| `SOURCE_INGESTED` *(O.2)* | global | chunks persisted | chunk count and ids, model, ruleset version |
| `KB_ITEM_SUPERSEDED` *(O.2)* | global | versioned / retired / replaced | item key, kind, successor, KB version |
| `KB_SCOPE_CHANGED` | project | allowlist or scope changed | change, source id / jurisdictions / pin |

The shared corpus has no project, so its events form the null-project chain, which verifies like any
other. No payload contains knowledge text (tested).

**"KB admin" is an existing human role, not a new one.** It is the **Knowledge-Base
Administrator** of approved Phase 0 F.1: *"Keep the KB correct, typed, and versioned · Add, version,
retire knowledge items · approval authority: none"*. In the code it is `Role.KB_ADMIN`.

- It has been one of the **seven human roles** since P0. `test_domain_invariants` pins that count at
  7, and P0 already granted it `project.read`.
- It is **not** one of the **thirteen conceptual (agent) roles** of architecture E. That count is
  pinned at 13 and unchanged.
- P2 adds **no role of any kind**. It adds seven *actions* (capabilities) to the single policy and
  grants them to existing roles, as below. "The project's KB admin" in this document always means a
  member holding `Role.KB_ADMIN` in that project.

**Who may do what:** the same single `policy.can`, extended.

| Action | Scoped | Granted to |
|---|---|---|
| `kb.read`, `kb.administer` | unscoped (shared corpus) | Knowledge-Base Administrator (`KB_ADMIN`) in any project |
| `kb.scope.manage` | project | Knowledge-Base Administrator **of that project** |
| `kb.scope.read` | project | analyst, compliance, security, PM, auditor, Knowledge-Base Administrator |
| `kb.retrieve`, `evidence.create` | project | analyst, compliance officer, security reviewer |
| `evidence.read` | project | analyst, compliance, security, PM, auditor |

Two consequences worth stating: the analyst who runs an analysis **cannot widen the sources that
ground it**; and **no non-human actor may curate or change scope, whatever roles it holds** — a new
policy rule, beside the gate rule, taken from architecture E.1 (agent roles write proposals only).

## 16. API and UI

`/api/v1` — shared corpus (Knowledge-Base Administrator only; others get **403**):
`GET /kb/version` · `GET|POST /kb/sources` · `POST /kb/controls` · `GET|POST /kb/items` ·
`GET /kb/items/{id}` · `POST /kb/items/{id}/versions` · `POST /kb/items/{id}/retire` ·
`POST /kb/items/{id}/supersede`

Project view (non-members get **404**, identical to "does not exist"):
`GET|PUT /projects/{id}/kb-scope` · `POST /projects/{id}/kb-allowlist` ·
`DELETE /projects/{id}/kb-allowlist/{source}` · `POST /projects/{id}/retrievals`
(`record_evidence` records server-side in the same transaction) · `GET /projects/{id}/evidence` ·
`GET /evidence/{id}` (resolved citation) · `POST /projects/{id}/citations/check`

Status codes: licence/taxonomy refusal, ungrounded evidence, tampered evidence → 409; unresolvable
citation → 404; embedding model unavailable → 503. No response ever contains a vector.

UI (`/ui`, Jinja2, server-side authorisation): `/ui/kb` (sources, items, add forms, with the
"educational reference corpus, not legal advice" notice), `/ui/kb/items/{id}` (metadata, text, chunks
with spans, versions, version/retire forms), `/ui/projects/{id}/kb` (scope, allowlist, retrieval with
ranked provenance, evidence), `/ui/evidence/{id}` (the citation). Scope controls are shown only to
a member holding the Knowledge-Base Administrator role in that project; the service refuses
everyone else regardless.

## 17. Security controls

| Control | Enforced in | Proved by |
|---|---|---|
| Allowlist as a join (P #9) | SQL, `scoped_chunks()` | 3 independent PostgreSQL proofs, §12 |
| Project isolation (P #10, #14) | policy (isolation before permission) + repository | cross-project read / scope / evidence / retrieval / HTTP tests |
| Single authorisation mechanism | `policy.can` only | policy matrix tests; import contracts |
| Human-only curation and scope | policy | agent with `KB_ADMIN` refused, superuser flag included |
| No fake law, no copied standard | admin service | licence and taxonomy refusal tests (service + HTTP) |
| Immutable corpus and evidence | ORM `before_flush` guard **and** PostgreSQL triggers | ORM tests (SQLite) + trigger tests (PostgreSQL) |
| Evidence integrity | evidence service | forged, tampered, superseded, removed-source, cross-project tests |
| Audit, references only | `AuditService` | payload scan; chain tamper detected on a KB event |
| Scope from the database only | repository reads the project row | no scope fields in the query types; ignored over HTTP |
| Offline, no external calls | provider design | full suite passes with all HTTP(S) routed to a dead proxy (§18) |

## 18. Test strategy and results

Final run after the closure audit (§24), this machine (Windows 11, Python 3.13.7). Both runs had
every HTTP/HTTPS route (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`) pointed at a dead proxy:

| Run | Collected | Passed | Failed | Skipped | Deselected |
|---|---|---|---|---|---|
| **Offline** (default; no PostgreSQL configured) | 804 | 682 | 0 | 121 | 1 |
| **With live PostgreSQL** (`REQPILOT_TEST_DATABASE_URL` set) | 804 | 803 | 0 | 0 | 1 |

- **Offline skips.** All 121 are exactly the PostgreSQL-only tests. They are reported as skipped,
  never as passed:

  | PostgreSQL-only tests | Skipped |
  |---|---|
  | P0/P1 PostgreSQL-specific | 10 |
  | P1 on PostgreSQL, including the migration tests | 43 |
  | P2 retrieval | 55 |
  | P2 HTTP retrieval | 5 |
  | P2 exit demonstration | 2 |
  | Probe | 3 |
  | Real model on PostgreSQL | 3 |

- **The live PostgreSQL run** migrated a database that had been dropped and recreated **empty**
  immediately beforehand. The migration tests create and drop their own databases as well.
- **The deselected test** is the opt-in `llm` placeholder.
- **External API calls: zero.** No provider SDK is installed, the embedding model loads from the
  local cache only, and both runs pass with the network routed nowhere.

| Mark | P1 end | P2 report | After closure audit | Added in P2 |
|---|---|---|---|---|
| unit | 193 | 359 | 362 | +169 |
| integration | 89 | 233 | 282 | +193 |
| workflow | 20 | 22 | 22 | +2 |
| security | 97 | 137 | 137 | +40 |
| llm (opt-in) | 1 | 1 | 1 | — |
| **total** | **400** | **752** | **804** | **+404** |

What the new tests cover: taxonomy and licence rules; chunk offsets (including property tests);
windows and overlap; embeddings and configuration; RRF by hand; result invariants; the ruleset;
compiled-SQL shape (**generated SQL only**, labelled as such); extraction of all four formats;
the KB policy matrix; persistence, versioning, KB-version allocation, pinning, audit and seeding;
evidence and citations; the API and UI; the security suite (§17); and on PostgreSQL: pgvector,
full text, fusion, every filter, empty outcomes, triggers, enum agreement, and the approved model.

The closure audit (§24) added 52 tests:

- 40 P1 exit and governance tests re-run unchanged on PostgreSQL, plus a guard that they were found;
- 2 fresh-database tests for migration `0003`;
- 4 tests for retirement vs replacement;
- 2 tests for empty-outcome determinism and side effects;
- 3 tests for embedding isolation.

Quality gates: `ruff format --check` 146 files already formatted (ruff 0.16 also checks Markdown) ·
`ruff check` all passed · `mypy` no issues in 90 source files · `lint-imports` 3 contracts kept,
0 broken. One warning remains in the suite: starlette's own `BlockingPortal` deprecation, present
since P0.

## 19. PostgreSQL runtime verification status

**A live PostgreSQL was available for P2**, but not the Docker image. Docker is not installed, so the
server is PostgreSQL **16.2** with **pgvector 0.6.2**, from the `pgserver` Python package, run from a
throwaway virtual environment outside the repository. It is **not** a project dependency. The approved
deployment remains `pgvector/pgvector:pg16` via `docker-compose.yml`. Everything P2 uses — `vector(n)`,
`<=>`, HNSW `vector_cosine_ops`, `vector_dims`, `websearch_to_tsquery` — is available in both.

| Verified against live PostgreSQL | Verified offline only | Not verified |
|---|---|---|
| Migrations `0001`–`0004` up, down and up again | SQL shape of the retrieval statements (compiled) | The exact `pgvector/pgvector:pg16` image |
| pgvector cosine search, HNSW and GIN indexes, `vector(384)` | Chunking, fusion, contracts, policy, extraction | Retrieval quality on a real curated corpus (ET-06) |
| Full-text search and hybrid RRF over real candidates | Evidence/citation logic (SQLite) | HNSW recall under large, tightly filtered corpora |
| Allowlist, jurisdiction, effective date, status, pin, model filters | | Concurrency of KB-version allocation under load |
| Evidence and citations end to end; HTTP retrieval | | |
| KB, evidence, audit and baseline triggers | | |
| The approved bge-small model, stored and retrieved | | |
| P1 workflow end to end, and 40 of the 41 P1 exit and governance tests, unchanged | | |
| Migration `0003` on fresh databases: defects reproduced at `0002`, repaired, schema otherwise identical, reversed | | |

**Pre-existing defects found by this first live run** (P0/P1, not P2; repaired separately, see the
addenda in `docs/03` §8 and `docs/04` §14):

1. **P1 could not write audit events on PostgreSQL.** `audit_event_type_enum` had only the twelve P0
   values. Every P1 service action fails there.
2. **P1 could not create approval tasks on PostgreSQL.** `gate_enum` had labels `G1`…`G8`; the ORM
   writes member names.
3. Three PostgreSQL tests could never pass: two audit-immutability tests attacked an empty table
   (the row trigger never fires), and one P0 test asserted the P1 tables were *absent*.
4. The baseline-trigger test's raw SQL used labels the schema does not have.

**Fixes:**

- the migration `0003_p1_postgres_enum_repair`, which touches both enum types and nothing else
  (`0002` is untouched);
- the four test corrections;
- regression guards, audited in §24:
  - every Python enum value must be accepted by its PostgreSQL type;
  - the P1 exit and governance tests run unchanged on PostgreSQL;
  - `0003` is proved on a fresh database to reproduce both defects at `0002`, repair them, leave
    every trigger, function, constraint, index, grant and column identical, and reverse.

Found in passing and confirmed at runtime, but **not fixed**: it isn't P2, and project deletion belongs
to P11. `audit_event.project_id` is `ON DELETE SET NULL`, and the append-only trigger refuses that
`UPDATE` (*"audit_event is append-only; UPDATE is not permitted"*). So deleting a project that has
audit events fails on PostgreSQL. Architecture P.2 intends deletion to keep the audit trail with
content redacted. That design needs settling when `FR-ADM-006` is built. Evidence's own cascade is
tested and works.

## 20. Files created and modified

**Created**

| Area | Files |
|---|---|
| Domain | `domain/models/knowledge.py` |
| M5 | `retrieval/{chunking,contracts,embeddings,extraction,fusion,rules}.py` |
| Repositories | `repositories/knowledge.py` |
| Services | `services/knowledge/{__init__,admin,scope,retrieval,evidence,seed}.py`, `services/evaluation/retrieval_probe.py` |
| API / UI | `api/knowledge_schemas.py`, `api/routes/knowledge.py`, `web/knowledge.py`, `web/templates/{kb,kb_item,project_kb,evidence}.html` |
| Rules | `rules/data/retrieval.yaml` |
| Migrations | `alembic/versions/0003_p1_postgres_enum_repair.py`, `0004_p2_knowledge_base.py` |
| Scripts / data | `scripts/seed_kb.py`, `data/dev/kb_synthetic/{manifest.yaml,acme_retention_policy.md}` |
| Tests | `tests/kb_helpers.py`; unit `test_{kb_taxonomy,chunking,embeddings,fusion_and_contracts,retrieval_sql,extraction,kb_policy}.py`; integration `test_p2_{knowledge_persistence,evidence,postgres_retrieval,api_and_ui,retrieval_probe,real_embeddings}.py`, `test_p1_workflow_on_postgres.py`; security `test_p2_security.py`; workflow `test_p2_exit_test.py` |
| Docs | `docs/05-p2-knowledge-base-rag.md` |

**Modified**: `domain/{enums,errors,ids}.py`, `domain/models/{__init__,identity}.py`,
`domain/policy/policy.py`, `retrieval/__init__.py`, `config.py`, `api/{app,dependencies,errors}.py`,
`main.py`, `web/templates/{base,project}.html`, `pyproject.toml`, `.env.example`, `README.md`,
`data/{README,dev/README,kb_seed/README}.md`, `docs/03-p0-foundations.md` (addendum),
`docs/04-p1-requirements-repository.md` (addendum); tests `conftest.py`,
`integration/{test_api_health,test_migrations,test_p1_persistence,test_postgres_specific}.py`,
`security/test_architecture_boundaries.py` (phase guards advanced one phase; PostgreSQL defects
corrected; M5 added to the boundary check). `01-analysis.md` and `02-architecture.md` are unchanged.

## 21. Explicitly deferred

| Item | Why | Where it goes |
|---|---|---|
| Curated regulatory corpus in `data/kb_seed/` | Human curation, verified against issuing bodies [P0 §D.2]; jurisdiction (Q2) still open | Team curation |
| ET-06 20-question probe (frozen in `data/gold/`) | Written against the curated corpus | Team, with the corpus |
| `FR-RAG-007` supersession flagging | `[SEC]`, deferred in analysis and architecture J.6 | Later phase; schema ready |
| `FR-RAG-004` confidence signals | No generated items exist yet | P3 onwards |
| Supplying evidence to a model; claim validation stage 3 | Needs the gateway and agents | P6 (`compliance_retrieve`, `compliance_validate`) |
| Query classification *inference* | Classification role | P3; applicable-source step P6 (K.1) |
| `COMPLIANCE_RETRIEVED` audit | Raised by the compliance node | P6 |
| Project corpus (`source_document`, `source_chunk`) and uploads | J.2 requires masking (`FR-ING-003`) before chunking and embedding | The ingestion/extraction phase with masking |
| `glossary_term` (G.5) | Serves undefined-term detection | P5 |
| Capability tokens for agent retrieval (P.1) | No agents yet | With the first retrieving agent |
| Reranking | "Not in the MVP" (J.4) | Only if ET-06 is missed |

## 22. P2 exit criteria

Roadmap exit test (analysis §P): *"recall@5 ≥ ET-06 on a 20-question probe; every citation resolves;
a non-allowlisted source is rejected."*

Two separate questions, kept separate:

**1. P2 implementation checklist (brief §34): COMPLETE.** Every item is met, with the PostgreSQL
tests run live (§19):

| Area | Met |
|---|---|
| Knowledge base | 6/6 |
| Ingestion | 5/5 |
| Embeddings | 4/4 |
| Retrieval | 9/9 |
| Evidence and citations | 5/5 |
| Security | 6/6 |
| Architecture | 5/5 |
| Verification | 8/8 |

**2. P2 roadmap exit (analysis §P): NOT YET PASSED. ET-06 is pending.**

| Criterion | Status |
|---|---|
| A non-allowlisted source is rejected | **Met.** §12, live PostgreSQL |
| Every citation resolves | **Met.** Every evidence row in the exit demonstration resolves to its exact span, including after the KB changes |
| recall@5 ≥ 0.80 on a 20-question probe (ET-06) | **Pending, not measured.** See below |

**What is in place for ET-06:**

- The scoring harness is implemented and tested (`services/evaluation/retrieval_probe.py`).
- It refuses to count a result unless four conditions hold: at least 20 questions, the approved
  model, a non-synthetic probe, and k = 5.
- With the approved model it scores 1.0 on a 5-question **synthetic** probe. That run checks the
  harness only. It correctly reports itself as not counting toward ET-06, and **it is not an ET-06
  result.**

**What ET-06 still needs, which is external/team work:**

- The curated corpus in `data/kb_seed/`, verified by a person against the issuing bodies [P0 §D.2].
- A 20-question probe written against that corpus and frozen in `data/gold/`.
- A decision on the jurisdiction question, Q2, which is still open.
- H.2 also marks ET-06 "re-baseline after P2".

No regulatory content has been invented or fetched to produce a number. The phase status becomes
`P2 COMPLETE` only after ET-06 is measured on the approved curated corpus and passes.

## 23. Deviations and P2 decisions

No `[DESIGN]` decision is changed and no module is added. These are the places where P2 goes
beyond, or interprets, what the architecture spells out:

1. **Columns beyond the G.5/G.6 field lists**, each needed for behaviour the architecture specifies:
   `licence_class` (G.5's refusal rule); `item_key`, `version_no` (item versions, `FR-RAG-006`);
   `superseded_in_kb_version`, `supersession_kind`, `supersession_reason`, `superseded_at` (J.6
   pinning; retire vs version); `text_origin`, `content_hash`, `title`, `clause_ref`; chunk `ordinal`,
   `text_hash`, `strategy`, `structure_label`, `token_count`, `embedding_model`; evidence
   `knowledge_chunk_id`, `quote_hash`, `retrieval_id`, `rank`, `kb_version`, `embedding_model`,
   `ruleset_version`, `query_hash`, run ids, `created_by`.
2. **Retire shares the `superseded` status.** G.5 has two statuses and O.2 has no retire event. So
   `FR-RAG-006` *retire* is recorded as `superseded`, with `supersession_kind = retired` and no
   successor.
   - A database constraint enforces *retired ⇔ no successor*.
   - The audit payload's `kind` names the operation.
   - How it differs from replacement is set out in §6.
3. **Three audit event types beyond O.2** — `KB_SOURCE_ADDED`, `KB_CONTROL_ADDED`,
   `KB_SCOPE_CHANGED` — following P1's precedent of a phase adding the events it raises.
4. **API beyond S:** `/kb/items/{id}/versions`, `/kb/items/{id}/retire`, and the project scope,
   allowlist, retrieval, evidence and citation endpoints. `POST /kb/items` additionally requires
   `item_key` and `text_origin`.
5. **Scope governance is assigned to an existing role.** The architecture names no role for the
   allowlist. P2 grants `kb.scope.manage` to the existing Knowledge-Base Administrator human role
   (Phase 0 F.1, `Role.KB_ADMIN`) of that project.
   - That role also sets `jurisdiction_scope`. Architecture S puts it on `POST /projects`, which does
     not exist yet.
   - This is a capability given to an existing role. No role is added, and the 13-role baseline is
     untouched (§15).
6. **New policy rule:** curation and scope changes are human-only (from E.1).
7. **Relevance thresholds per model in the ruleset**, and the P0 placeholder `RETRIEVAL_MIN_SCORE`
   removed (it would have been a second, model-blind source of truth).
8. **sentence-transformers is an optional extra**, not a core dependency (ADR-005's choice is
   unchanged; CI and offline installs stay light).
9. **Keyword half uses every-term matching** (`websearch_to_tsquery`), the design choice that makes a
   keyword hit meaningful as relevance evidence.
10. **No masking step for curated KB text** — J.2's masking belongs to uploaded project content (§7).
11. **`/kb/*` answers 403 to non-admins**, while project endpoints answer 404 for non-members, as P1
    established. The shared corpus's existence is not a project secret.
12. **Verification ran on `pgserver`'s PostgreSQL 16.2 + pgvector 0.6.2**, not the Docker image (§19).
13. **P1 repair migration and test corrections** (§19). These are not architecture deviations, but they
    change P0/P1 test files and add a migration, so they are listed here.

## 24. Closure audit

This was the final audit before P2 is frozen. Nothing was redesigned. The findings and the changes
they led to:

| # | Question | Finding | Change |
|---|---|---|---|
| 1 | ET-06 status | Correctly unmeasured, but the wording ("Blocked") ran together the implementation checklist and the roadmap exit | Status is now **P2 IMPLEMENTATION COMPLETE — ET-06 PENDING**, with the two levels separated (header, §22). The harness is unchanged |
| 2 | Is "KB admin" a new role? | **No.** It is the existing Phase 0 F.1 human role, Knowledge-Base Administrator (`Role.KB_ADMIN`). It is one of the 7 human roles pinned since P0, not one of the 13 agent roles. P2 added capabilities, not a role | Wording made precise (§15, §16, §23 item 5, seed script, READMEs). No code change |
| 3 | Migration `0003` | Sound; details below | Regression tests widened (below) |
| 4 | Retire vs supersede | The representation is right (G.5 two statuses, O.2 one event), and the service always wrote kind and successor consistently. **But the database did not enforce it**: a row could say `retired` and still have a successor | Check constraint `ck_knowledge_item_supersession_kind_matches_successor` (model and `0004`). Tests: the audit trail distinguishes all three kinds, and the schema refuses each contradiction. §6 documents how retirement differs from replacement |
| 5 | `RETRIEVAL_EMPTY` | Deterministic, explicit, and no review workflow implemented; `requires_human_review` is a flag, not a task | Tests: every empty reason, and success, are reproducible; an empty outcome writes no task, decision, run, evidence or audit row. Boundary documented (§11) |
| 6 | Embedding dependency | All five properties hold. Only the retrieval package's import isolation had been tested, not the whole application's | Tests: whole-app import loads no model; the app runs without the extra; the approved provider loads from the local cache only (§9) |
| 7 | Frozen baseline | Unchanged; below | None |
| 8 | Verification | §18, §19 | — |

**Migration `0003` in detail.**

- **`0002` is untouched.** It is not among the modified files in the working tree, its timestamp
  predates P2, and its trigger bodies contain no gate label that a rename could break.
- **Additive or reversible.**
  - The audit enum values are *added*. PostgreSQL cannot drop an enum value, so a downgrade leaves
    them in place; they are inert there.
  - The gate labels are *renamed*, and the downgrade renames them back.
  - A fresh-database test goes up to `0002`, then `0003`, then back down to `0002`, then up to head.
- **It does not weaken audit immutability, RBAC or project isolation.** On a fresh database, the
  test snapshots everything a migration could use to weaken them, at `0002` and again after
  `0003`. Apart from the enum labels, **the snapshots are identical**. The snapshot covers:
  - every trigger, including the audit append-only trigger and the baseline-member guard;
  - every function body;
  - every constraint and foreign key;
  - every index;
  - every table grant, including the audit `REVOKE`;
  - every column;
  - every row-level-security policy.

  Authorisation lives in `policy.can` (Python), which `0003` cannot touch.
- **It does not change G1 co-approval or the P1 lifecycle.**
  - G1's required roles (Analyst + Compliance Officer) are defined in Python, not in the database.
    The rename changes only the text stored for a gate.
  - `requirement_state_enum` is not touched.
  - 40 of the 41 P1 exit and governance tests now run **unchanged on PostgreSQL** and pass. They
    include:
    - either approval alone cannot baseline;
    - one task per role; the same role cannot approve twice; the author cannot approve their own
      version;
    - rejection and cancellation;
    - exact-version binding;
    - the lifecycle guards;
    - two-project isolation.

    The 41st test is a pure `policy.can` check with no database.
- **A fresh database runs the full P1 workflow.** A database migrated from empty to head runs the
  P1 exit scenario:
  - create;
  - version;
  - validate;
  - G1 co-approval;
  - baseline;
  - audit-chain verification.

  The shared test database was also recreated empty before the final run.

**Frozen baseline, checked this audit.**

| Item | Value | Checked against |
|---|---|---|
| Functional requirements | 132, in 20 groups | Counted from `docs/01-analysis.md` |
| MVP / secondary / out of scope | 117 / 13 / 2 | Counted from `docs/01-analysis.md` |
| [PS] / [PROJ] | 99 / 33 | Counted from `docs/01-analysis.md` |
| Modules | M1–M12 | Counted from `docs/01-analysis.md` |
| Phases | P0–P12 | Counted from `docs/01-analysis.md` |
| Conceptual roles | 13 | `AgentRole`, pinned by test |
| Human roles | 7 | `Role`, pinned by test |
| Gates | G1–G8 | `Gate` |
| G1 approvers | Analyst + Compliance Officer | `GATE_REQUIRED_ROLES` |
| Lifecycle | 14 states, no `CONFLICTED` | `RequirementState` |
| Orchestration | LangGraph, deterministic governance | Import contracts: 3 kept |

`01-analysis.md` and `02-architecture.md` are unmodified.

**Files changed by this audit:**

- Code:
  - `domain/models/knowledge.py` and `alembic/versions/0004_p2_knowledge_base.py`: the one new check
    constraint. `0004` is P2's own migration, not yet frozen.
- Tests:
  - `integration/test_p1_workflow_on_postgres.py`: the P1 exit and governance tests on PostgreSQL,
    and the `0003` fresh-database tests;
  - `integration/test_p2_knowledge_persistence.py`: retirement vs replacement;
  - `integration/test_p2_postgres_retrieval.py`: empty-outcome determinism and no side effects;
  - `unit/test_embeddings.py`: application import, running without the extra, cache-only loading.
- Wording:
  - `scripts/seed_kb.py` (help text);
  - `data/dev/kb_synthetic/manifest.yaml` and `data/kb_seed/README.md`;
  - `README.md`, `docs/04-p1-requirements-repository.md` (addendum), and this document.

## Traceability

| P2 implementation | Source |
|---|---|
| C.1 taxonomy, binding descriptions | `FR-RAG-001` · [P0 §C.1] · G.5 |
| Source/item metadata, curation date | `FR-RAG-001` `[PS §5]` · G.5 · [P0 §D.2] |
| Licence class and refusals; no synthetic law | G.5 · [P0 §D.2] copyright constraint |
| Item versioning, retire, supersede | `FR-RAG-006` · J.6 · S |
| KB version and project pin | J.6 · G.3 |
| Ingestion pipeline and parsers | J.2 |
| Chunking strategies and offsets | J.3 · `FR-EXT-001`, `FR-RAG-003` (span traceability) |
| `EmbeddingProvider`, bge-small, offline | ADR-005 · ET-10 |
| pgvector, HNSW, full text, RRF | ADR-004 · J.4 · ADR-003 |
| Allowlist as a join | `FR-RAG-002` `[PS §6, §17]` · J.4 · P #9 |
| Query classification contract | `FR-RAG-002` · K.1 |
| Explicit empty retrieval | `FR-RAG-005` `[PS §6]` |
| Evidence and citation resolution | `FR-RAG-003` `[PS §6]` · G.6 · J.5 · F.3 |
| Project isolation | `FR-PRJ-004` · P #10, #14 · ADR-009 |
| Single policy, new actions | ADR-009 · E.1 |
| Audit of curation and scope | `FR-RAG-006` `[PS §5, §17]` · ADR-010 · O.1, O.2 |
| Immutability triggers | ADR-010 (pattern) · J.5 · J.6 |
| ET-06 probe harness | [P0 §H.2] ET-06 · §P P2 exit test |
| Data directories | V · R.3 `[DESIGN] D16` |
