# ReqPilot

An agentic requirements-engineering and SDLC-recommendation assistant for the
financial sector. University Software Engineering course project.

ReqPilot turns unstructured stakeholder input into a validated, classified,
risk-assessed, compliance-mapped and fully traceable requirement baseline — and
then, from that baseline, produces a justified SDLC recommendation and a
project-specific workflow.

**The framing that constrains everything:** the system automates information
collection and analysis, but responsibility for regulatory interpretation,
requirement approval and SDLC adoption stays with authorised humans. ReqPilot is
advisory. It does not give legal advice, and it does not make lending decisions.

---

## Current status — roadmap phase P2 (Knowledge base & RAG)

| Stage | State |
|---|---|
| Phase 0 analysis — `docs/01-analysis.md` | Approved |
| Architecture & design — `docs/02-architecture.md` | Approved |
| P0 Foundations — `docs/03-p0-foundations.md` | Complete |
| P1 Requirements repository — `docs/04-p1-requirements-repository.md` | Complete |
| **P2 Knowledge base & RAG — `docs/05-p2-knowledge-base-rag.md`** | **P2 IMPLEMENTATION COMPLETE — ET-06 PENDING**: the implementation checklist is complete; the roadmap exit waits on ET-06, which needs the team-curated corpus and 20-question probe and has not been measured |

P0 built the foundation. P1 built the deterministic requirements repository:
immutable requirement versions, a guarded lifecycle, G1 human approval, and
baselines. P2 built the grounding layer: a typed, versioned knowledge base
(C.1 taxonomy), structure-aware chunking with exact offsets, local embeddings,
PostgreSQL + pgvector hybrid retrieval with the source allowlist enforced
**inside the query**, immutable evidence, and exact citation resolution.

**There is still no LLM functionality** and no document generation. Retrieval is
deterministic; the agents that will consume it belong to later roadmap phases,
defined in `docs/01-analysis.md` §P.

**No external LLM API key is needed** to install, migrate, run or test.

---

## Quick start

```bash
python -m venv .venv
.venv/Scripts/activate      # Windows;  source .venv/bin/activate on Unix
pip install -e ".[dev]"
cp .env.example .env
pytest -m "unit or security"
```

That runs the offline suite with no database and no network. To see the
workflow, start the app and open `/ui`:

```bash
uvicorn reqpilot.main:app --reload
```

Retrieval uses the approved local embedding model (`BAAI/bge-small-en-v1.5`,
ADR-005), which is an optional extra. It never downloads at runtime unless
`EMBEDDING_ALLOW_DOWNLOAD=true` is set once:

```bash
pip install -e ".[embeddings]"
```

To load the synthetic, fictional development knowledge base (the actor is a user
holding the Knowledge-Base Administrator role):

```bash
python scripts/seed_kb.py --manifest data/dev/kb_synthetic/manifest.yaml --actor <user id>
```

Retrieval, pgvector and the database triggers need PostgreSQL; their tests skip
visibly without it. With a migrated PostgreSQL 16 + pgvector available:

```bash
REQPILOT_TEST_DATABASE_URL=postgresql+psycopg://reqpilot:reqpilot_local_dev_only@localhost:5432/reqpilot pytest
```

Full setup, including PostgreSQL and migrations, is in
[docs/03-p0-foundations.md](docs/03-p0-foundations.md). What the repository does
and how its governance is enforced is in
[docs/04-p1-requirements-repository.md](docs/04-p1-requirements-repository.md);
the knowledge base and retrieval are in
[docs/05-p2-knowledge-base-rag.md](docs/05-p2-knowledge-base-rag.md).

---

## Architecture in one paragraph

A deterministic application owns all state, policy, approval and audit.
LangGraph orchestrates analysis *inside* it. Thirteen conceptual agent roles do
the analytical work; ten of them invoke or use LLM capabilities and three are
fully deterministic. Every model output is a typed proposal that must pass a
validation pipeline before it touches persistent state.

Three rules shape the whole system:

1. **The LLM proposes; deterministic code disposes.**
2. **The LLM never controls graph topology** — routers are plain Python.
3. **Authority fields do not exist in LLM schemas** — a model cannot set a risk
   severity or an SDLC score because those fields are absent from what it emits.

The structural expression of all three is one import rule, enforced in CI:
`domain/`, `repositories/`, `services/` and `retrieval/` may not import
`graph/`, `agents/`, `llm/` or `langgraph`.

---

## Documentation

| Document | Contents |
|---|---|
| [`docs/01-analysis.md`](docs/01-analysis.md) | Approved Phase 0 analysis: scope, 132 requirements, evaluation plan, P0–P12 roadmap |
| [`docs/02-architecture.md`](docs/02-architecture.md) | Approved architecture: components, 12 ADRs, LangGraph design, data model, HITL, security |
| [`docs/03-p0-foundations.md`](docs/03-p0-foundations.md) | What P0 built, how to run it, and what it deliberately does not do |
| [`docs/04-p1-requirements-repository.md`](docs/04-p1-requirements-repository.md) | The requirements repository: versions, lifecycle, G1 approval, baselines |
| [`docs/05-p2-knowledge-base-rag.md`](docs/05-p2-knowledge-base-rag.md) | The knowledge base and retrieval: taxonomy, versioning, chunking, hybrid search, allowlist, evidence, citations |
| [`docs/problem-statement.md`](docs/problem-statement.md) | Reference copy of the original problem statement |

`Problem Statement.docx` remains the authoritative specification.

---

## Data and safety

- **Synthetic and anonymised data only.** No real customer or financial data
  belongs in this repository.
- **No secrets in version control.** Configuration is environment-driven;
  `.env.example` contains placeholders only.
- The knowledge base is an educational, evidence-based reference corpus — not an
  authoritative legal decision engine.

See the README in each `data/` subdirectory for what may be committed where.
