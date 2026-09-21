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

## Current status — roadmap phase P1 (Requirements repository)

| Stage | State |
|---|---|
| Phase 0 analysis — `docs/01-analysis.md` | Approved |
| Architecture & design — `docs/02-architecture.md` | Approved |
| P0 Foundations — `docs/03-p0-foundations.md` | Complete |
| **P1 Requirements repository — `docs/04-p1-requirements-repository.md`** | **Complete** |
| P2 Knowledge base & RAG | Not started |

P0 built the foundation: structure, configuration, database, the governance
primitives, the LangGraph skeleton and the test infrastructure. P1 built the
deterministic requirements repository on top of it: immutable requirement
versions, a guarded lifecycle, G1 human approval, and baselines.

**There is still no AI functionality**, no retrieval and no document generation.
Those belong to later roadmap phases, defined in `docs/01-analysis.md` §P.

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

Full setup, including PostgreSQL and migrations, is in
[docs/03-p0-foundations.md](docs/03-p0-foundations.md). What the repository does
and how its governance is enforced is in
[docs/04-p1-requirements-repository.md](docs/04-p1-requirements-repository.md).

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
`domain/`, `repositories/` and `services/` may not import `graph/`, `agents/`,
`llm/` or `langgraph`.

---

## Documentation

| Document | Contents |
|---|---|
| [`docs/01-analysis.md`](docs/01-analysis.md) | Approved Phase 0 analysis: scope, 132 requirements, evaluation plan, P0–P12 roadmap |
| [`docs/02-architecture.md`](docs/02-architecture.md) | Approved architecture: components, 12 ADRs, LangGraph design, data model, HITL, security |
| [`docs/03-p0-foundations.md`](docs/03-p0-foundations.md) | What P0 built, how to run it, and what it deliberately does not do |
| [`docs/04-p1-requirements-repository.md`](docs/04-p1-requirements-repository.md) | The requirements repository: versions, lifecycle, G1 approval, baselines |
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
