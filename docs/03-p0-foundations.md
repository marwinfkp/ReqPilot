# ReqPilot — P0 Foundations

**Roadmap phase:** P0 Foundations — the first phase of the approved P0–P12
implementation roadmap in [`01-analysis.md`](01-analysis.md) §P.
**Status:** Complete and closed. Closure pass done: data-directory convention reconciled to the
architecture, `[DESIGN] D12` settled, ADR statuses reconciled.
**Preceded by:** the approved Phase 0 analysis and the approved architecture in
[`02-architecture.md`](02-architecture.md).

P0 builds the foundation the rest of the roadmap stands on: project structure,
configuration, database and migrations, the governance primitives, the
orchestration skeleton, and the test infrastructure.

**There is no AI functionality, no requirement CRUD, no retrieval and no
document generation.** Those belong to later roadmap phases.

---

## 1. What P0 implemented

### Governance primitives — the load-bearing part

These exist first, before any model output exists to be governed, because that
ordering is what makes the guarantees real rather than aspirational.

| Capability | Where | What it guarantees |
|---|---|---|
| **Centralised authorization** | `domain/policy/policy.py` | One `can(actor, action, resource)` function. Deny by default; project isolation evaluated *before* permission; a non-human actor can never decide an approval gate |
| **Append-only audit** | `services/audit/`, `domain/models/audit.py` | Three immutability layers: no mutation path in the application, a database trigger plus `REVOKE`, and a per-project hash chain |
| **Project isolation** | `project_id` FK on every scoped table, plus `repositories/base.py` | A repository call takes an actor and a project; the scoped query is the easy path |
| **Baseline vocabulary** | `domain/enums.py` | The 13 agent roles, their 4+5+1+3 implementation split, the 7 human roles, and **exactly 8** platform gates G1–G8 — pinned by tests so they cannot drift |

### Infrastructure

| Area | Delivered |
|---|---|
| Project structure | `src/reqpilot/` with the module boundaries from architecture §V |
| Configuration | Typed `pydantic-settings`, fail-fast validation, `.env.example`, secret redaction |
| Database | PostgreSQL 16 + pgvector via Docker Compose; SQLAlchemy 2.0; health check with a connect timeout |
| Migrations | Alembic, one migration creating the six foundation tables, reversible, tested |
| Orchestration | LangGraph checkpointer selection, thread-id convention, deterministic router conventions, and a smoke graph proving compile → route → **interrupt → resume** |
| LLM boundary | Gateway abstraction, offline stub, record/replay wrapper. **No network call, no credential** |
| Rules | Versioned YAML loader with immutability and mandatory versioning; one example file |
| Tests | 204 tests across five categories; 0 require an external API |
| CI | Format, lint, types, import contracts, tests, and a check that no provider SDK is installed |

### The six foundation tables

`app_user` · `project` · `project_member` · `audit_event` · `graph_run` ·
`agent_run`

A test asserts the migration creates **nothing beyond** these — in particular no
`requirement`, `requirement_version` or `approval_task`, which belong to P1 and
later.

---

## 2. Repository structure

```
ReqPilot/
├── src/reqpilot/
│   ├── domain/              ← no LangGraph, no LLM imports, ever
│   │   ├── models/          SQLAlchemy models for the foundation tables
│   │   ├── policy/          RBAC: the single can() entry point
│   │   ├── lifecycle/       (boundary only — requirement states are P1)
│   │   ├── enums.py         roles, gates, event types, statuses
│   │   ├── ids.py           typed identifiers, thread-id convention
│   │   ├── refs.py          evidence/provenance value objects
│   │   └── errors.py
│   ├── repositories/        data access; database engine + session + health
│   ├── services/
│   │   ├── audit/           append-only writer, hash chain
│   │   ├── approval/        (boundary only — gates are implemented later)
│   │   ├── baseline/        (boundary only)
│   │   └── evaluation/      (boundary only)
│   ├── graph/               ← LangGraph lives here and nowhere else
│   │   ├── graphs/smoke.py  P0-only graph proving interrupt/resume
│   │   ├── routers.py       deterministic routing conventions
│   │   ├── builder.py       checkpointer selection, run config
│   │   └── state.py         three-tier state rule + forbidden-field guard
│   ├── agents/              (boundary only — the 13 roles arrive later)
│   ├── llm/gateway.py       the single model choke point; stub only in P0
│   ├── rules/               versioned YAML loader + data/
│   ├── retrieval/ artifacts/ security/ web/   (boundaries only)
│   ├── api/                 FastAPI app + health routes
│   └── config.py
├── alembic/versions/        0001_p0_foundation.py
├── tests/                   unit · integration · workflow · security · llm
├── data/                    kb_seed · gold · dev · private (each with a README)
├── docs/                    01-analysis · 02-architecture · 03-p0-foundations
├── .github/workflows/ci.yml
├── docker-compose.yml
├── .env.example
└── pyproject.toml
```

**Packages marked "boundary only" contain a docstring and nothing else.** They
name their architecture module and the roadmap phase that fills them. They exist
so the import contracts have something to constrain and so the structure matches
the approved architecture — not as placeholders for speculative code.

---

## 3. Local setup

### Prerequisites

Python 3.12 or later, and Docker (for PostgreSQL). **The offline test suite runs
without Docker.**

### Install

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -e ".[dev]"
cp .env.example .env
```

The defaults work offline. **No API key is required** to install, migrate, run,
or test.

### Database

```bash
docker compose up -d db
```

```bash
alembic upgrade head
```

### Run

```bash
uvicorn reqpilot.api.app:app --reload
```

Then `GET /health` (liveness, no database) and `GET /health/db` (readiness,
reports pgvector availability).

### Test

```bash
pytest
```

```bash
pytest -m unit
```

Useful variants:

| Command | What it runs |
|---|---|
| `pytest` | Everything except the `llm` mark |
| `pytest -m "unit or security"` | Fast, no database |
| `pytest -m llm` | The opt-in category — never in CI |
| `REQPILOT_TEST_DATABASE_URL=... pytest -m integration` | Includes the PostgreSQL-specific tests |

### Quality gates

```bash
ruff format --check . && ruff check . && mypy && lint-imports && pytest -q
```

---

## 4. Configuration

All settings are documented in [`.env.example`](../.env.example), which contains
**placeholders only** and is safe to commit. `.env` is gitignored.

Three rules the configuration follows:

1. **Fail-fast.** A missing required value raises at startup. Discovering a
   missing key three nodes into a graph run wastes tokens and leaves a
   half-finished run.
2. **Secrets are never rendered.** `Settings.safe_dump()` redacts the secret key,
   the API key, and the database URL — which counts as a secret because it
   carries a password. `__repr__` uses it, so tracebacks and log lines are safe.
   Tested.
3. **Rules are not configuration.** The risk matrix, MCDA weights and control
   checklists live in versioned YAML under `src/reqpilot/rules/data/`, never in
   the environment, so changing a rule is a data change (architecture `DQ-03`).

A test asserts `.env.example` documents **every** settings field, so the contract
cannot drift behind the code.

---

## 5. Architecture boundaries

### The rule that matters most

> `domain/`, `repositories/` and `services/` may not import `graph/`, `agents/`,
> `llm/` or `langgraph`.

This is the structural expression of **governance lives outside the LLM**. It is
enforced three ways:

1. `lint-imports` contracts in `pyproject.toml` — run in CI.
2. A static AST check over every domain-side file.
3. A subprocess test that imports the domain **with `langgraph` blocked at the
   meta-path**, catching a hidden dependency that static analysis would miss.

### Other enforced boundaries

| Boundary | Enforcement |
|---|---|
| `api → services → repositories → domain`, one way | `lint-imports` layered contract |
| Only `llm/` may name a provider SDK | AST scan over the source tree |
| Routers cannot see model output | Structural guard rejecting a router that closes over a gateway |
| Graph state carries no content | `assert_state_shape` rejects forbidden fields |
| Audit payloads carry references only | `AuditService` rejects content-bearing keys |

---

## 6. What P0 deliberately does NOT implement

Recorded rather than silently omitted. Roadmap phases are those in
[`01-analysis.md`](01-analysis.md) §P — consult it rather than this list for
phase definitions.

| Not implemented | Belongs to |
|---|---|
| Requirement CRUD, versioning, lifecycle state machine | **P1** Requirements repository |
| Approval task/decision records and gate enforcement | P1 onwards; gates are exercised by the phases that create gated subjects |
| Knowledge base, chunking, embeddings, retrieval, citations | **P2** Knowledge base & RAG |
| Requirement extraction and classification | **P3** |
| Adaptive elicitation and clarification | **P4** |
| Quality analysis and conflict detection | **P5** |
| Compliance and security/privacy analysis | **P6** |
| Risk analysis, the 3×3 matrix data, the risk register | **P7** Risk analysis & register |
| Traceability matrix and document generation | **P8** |
| SDLC factor profile, MCDA scoring, ranking | **P9** |
| Project-specific workflow generation | **P10** |
| Masking, injection detection, output filtering | **P11** Guardrails hardening |
| Evaluation harness and metric computation | **P12** |

Also not implemented, by architectural decision rather than phasing: any real
model invocation, authentication flows, MFA, the web UI, and the four real
graphs (elicitation, analysis, documentation, SDLC).

### Boundaries that exist but are empty

`agents/`, `retrieval/`, `artifacts/`, `security/`, `web/`,
`services/approval/`, `services/baseline/`, `services/evaluation/`,
`domain/lifecycle/`, `graph/nodes/` — each holds a docstring naming its module
and phase. Nothing more.

---

## 7. How P1 begins

P1 is **Requirements repository, with no AI at all**. It builds on P0 as follows:

| P1 needs | P0 provides |
|---|---|
| `requirement` / `requirement_version` tables | Alembic setup, naming conventions, model base, a migration to follow |
| Lifecycle state machine | `domain/lifecycle/` boundary and `StateTransitionError` |
| Role-appropriate approval | `policy.can()`, the `Gate` enum, `GATE_REQUIRED_ROLES` |
| Every transition audited | `AuditService.append()` with chaining; add the new event types to `AuditEventType` |
| Project-scoped queries | `ProjectScopedRepository` |
| Tests for all of it | Five categories, fixtures, and the offline discipline already in place |

The P1 exit test from the approved roadmap — *create → edit → approve through G1
→ baseline, by hand, with every transition audited and every unauthorised path
refused* — is reachable with no new infrastructure.

---

## 8. Environment deviations

Two differences between the approved architecture and this machine. Both are
recorded rather than resolved by quietly editing the architecture.

| Architecture says | Reality | Handling |
|---|---|---|
| **Python 3.12** (ADR-002) | 3.12 is not installed; 3.11, 3.13, 3.14 are | `requires-python = ">=3.12"` is a floor, not a pin. Developed and tested on **3.13.7**. CI pins 3.12. No 3.13-only syntax is used except PEP 695 generics, which are 3.12+ |
| **PostgreSQL 16 via Docker** (ADR-003) | No container runtime and no PostgreSQL on this machine — re-confirmed at P0 closure | `docker-compose.yml` is delivered as specified. Dialect-independent behaviour is tested on in-memory SQLite; PostgreSQL-only guarantees (append-only trigger, `REVOKE`, pgvector) are in `test_postgres_specific.py` and **skip visibly** rather than silently passing |

**Consequence to be honest about:** the append-only trigger and the `REVOKE` are
written and reviewed but **not yet executed against a live PostgreSQL instance**.

At P0 closure this was re-checked exhaustively: no `docker` / `docker compose` /
`podman` / `nerdctl` binary exists anywhere on the machine (only an orphaned,
stopped `com.docker.service` registration left by a previous uninstall), no
`psql` or `postgres`, nothing listening on 5432, and the WSL2 Ubuntu distribution
has no PostgreSQL packages installed. Installing a database server was out of
scope for a closure pass, so the result is reported as **BLOCKED**, not as a pass.

What *was* verified without a server: `alembic upgrade head --sql` against a
PostgreSQL URL generates the complete DDL, including `CREATE EXTENSION IF NOT
EXISTS vector`, `JSONB` columns, the `audit_event_append_only` trigger and its
function, and `REVOKE UPDATE, DELETE ON audit_event FROM CURRENT_USER`. That
proves the statements are emitted and the dialect branch works; it does **not**
prove the trigger blocks a mutation at runtime.

Verifying that is the first task of whoever has Docker available:

```bash
docker compose up -d db && alembic upgrade head && REQPILOT_TEST_DATABASE_URL=postgresql+psycopg://reqpilot:reqpilot_local_dev_only@localhost:5432/reqpilot pytest -m integration
```

### Data-directory convention — resolved at closure

P0 initially used `data/corpus` and `data/fixtures` (from the P0 brief) while
architecture §V and §R.3 specified `kb_seed` and `dev`. Under the source
hierarchy the approved architecture outranks the implementation, so the
**architecture's names are canonical**:

```
data/
├── kb_seed/   curated knowledge-base source material   (committed)
├── gold/      frozen evaluation datasets + manifest     (committed, hash-frozen)
├── dev/       synthetic development fixtures            (committed)
└── private/   local-only scratch                        (gitignored)
```

`corpus/` and `fixtures/` no longer exist. Settings were renamed to match
(`KB_SEED_DIR`, `DEV_DATA_DIR`), and four tests in
`tests/security/test_architecture_boundaries.py` now pin the layout so the two
conventions cannot drift apart again. `02-architecture.md` was **not** edited —
the implementation moved to it, not the other way round.

---

## 9. Unresolved decisions

Carried forward; none blocks P1.

| Decision | Status | Blocks |
|---|---|---|
| LLM provider and model tier | **TBD** — depends on the open budget question | The first phase that needs model output |
| Deployment target | **TBD** | Nothing before deployment |
| Jurisdiction / KB content scope | Open | The knowledge-base phase |
| Team size and timeline | Open | Scheduling only |
| Stack familiarity (could revisit ADR-002/ADR-008) | Open | Cheapest to change now |
| `[DESIGN] D12` — conflict as a transition guard rather than a lifecycle state | **Settled — Selected** (architecture H.2) | Nothing |

**All twelve ADRs are now Selected** (reconciled at closure). What remains open
is narrower: the LLM provider and model tier inside ADR-006, and the deployment
target. Neither blocks P1, which contains no AI functionality.

---

## 10. Traceability

Every P0 decision points back to an approved source.

| P0 artefact | Traces to |
|---|---|
| `pyproject.toml` dependencies | ADR-001, 002, 003, 004, 011, 012 |
| `config.py` | ADR-011; `DQ-03` for what is excluded |
| `domain/policy/policy.py` | ADR-009; architecture J.1, M.2; `FR-ADM-002`, `FR-ADM-003`, `FR-HIL-003` |
| `domain/models/audit.py`, `services/audit/` | ADR-010; architecture O; `FR-AUD-001`…`005` |
| `domain/enums.py` | Architecture E.0 (13 roles, 4+5+1+3), M.1/M.3 (G1–G8), Phase 0 F.1 (7 roles) |
| `domain/refs.py` | Architecture F.3; Phase 0 H.1 (review signal, not a probability) |
| `repositories/database.py`, `base.py` | ADR-003, ADR-009; `FR-PRJ-004` |
| `graph/` | ADR-001; architecture C.3 (deterministic routers), C.7 (thread ids, checkpoints), D (three-tier state) |
| `llm/gateway.py` | ADR-006; architecture Q.1 (trust classes); P0 instruction 5 |
| `rules/` | Architecture M7; `DQ-03` |
| `api/` | ADR-002; architecture S |
| `alembic/versions/0001_p0_foundation.py` | ADR-003, ADR-010; architecture G.2, G.3, G.7 |
| Test layout and marks | ADR-012; ET-10 |
| `.github/workflows/ci.yml` | ADR-012; architecture V (import contracts) |
| `data/*/README.md` | Architecture V, R.3 (layout); Phase 0 C.1 (corpus framing), D.2 (licence constraint) |

---

**P0 complete. P1 not started.**
