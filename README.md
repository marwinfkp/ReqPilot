# ReqPilot

**From Financial Requirements to the Right Development Lifecycle.**

ReqPilot is an agentic-AI platform for financial-sector requirement engineering. A user submits the
complete requirement set of a financial application (pasted text and/or TXT, MD, PDF, DOCX, CSV documents),
clicks **ANALYSE REQUIREMENTS**, and ReqPilot automatically produces:

- structured, classified functional and non-functional requirements with IDs (e.g. `FR-PAY-001`)
- requirement quality analysis: ambiguity, incompleteness, conflicts, duplicates, infeasibility, missing acceptance criteria, undefined terms
- retrieval-grounded compliance mappings with verified citations, plus compliance gaps
- security and privacy coverage, threats (STRIDE) and privacy considerations
- a scored risk register (likelihood x impact)
- a **deterministic, explainable ranking of 7 SDLC models** and a justified recommendation
- a **project-specific SDLC workflow** (phases, activities, roles, deliverables, security/compliance checkpoints, entry/exit criteria, approval gates)
- user stories, acceptance criteria, use cases and a requirements traceability matrix
- 12 downloadable reports in DOCX and PDF

There is **no mandatory interaction** between submission and results. Unclear requirements are reported as
findings, assumptions and stakeholder questions. Human approval (requirement baseline, compliance
interpretations, SDLC adoption, workflow) happens **after** the analysis, in the Approval Centre.

> Academic prototype. Use synthetic data only. ReqPilot does not provide legal advice; the bundled "Demo Bank"
> policies are fictional and the regulatory catalogue contains document-level summaries, not regulatory text.

---

## 1. Architecture

```mermaid
flowchart LR
    UI["Next.js frontend<br/>(TypeScript, Tailwind, shadcn/ui, Recharts)"] -- "/api/* (same-origin proxy,<br/>HttpOnly session cookie)" --> API["FastAPI backend"]
    API --> ORCH["Orchestrator<br/>app/pipeline.py"]
    ORCH --> A1["1 Extraction"] --> A2["2 Classification"] --> A3["3 Quality & Conflict"] --> A4["4 Compliance (RAG)"]
    A4 --> A5["5 Security & Privacy"] --> A6["6 Risk"] --> A7["7 SDLC Selection<br/>(deterministic scoring)"] --> A8["8 Workflow Generation"]
    A8 --> A9["9 Documentation, Traceability,<br/>Validation, Summary"]
    A1 & A2 & A3 & A4 & A5 & A6 & A7 & A8 & A9 -. "structured outputs<br/>(Pydantic)" .-> OAI["OpenAI API<br/>(app/llm.py)"]
    A4 --> KB[("Knowledge base<br/>pgvector")]
    API --> DB[("PostgreSQL 16<br/>+ pgvector")]
```

* **One central orchestrator** (`backend/app/pipeline.py`) runs the agents in a fixed order on a shared
  context dict. Each agent reads what earlier agents produced and adds its own results — an explicit,
  easy-to-follow hand-over. The analysis runs in a background thread; the UI polls progress.
* **Hybrid agents.** Every agent has a deterministic rule-based baseline (keyword dictionaries in
  `app/rules.py`). With an OpenAI key, the agents additionally call OpenAI with structured outputs and the
  results are validated against the input (unknown requirement IDs, chunk IDs and unverifiable quotes are
  rejected and counted). Without a key the app runs in a clearly-labelled **demonstration mode** (rules only).
* **No fabricated success.** Each agent run is stored in `agent_runs` with status `completed`, `partial`
  (OpenAI failed, rule fallback used — stated in the message), `failed` or `skipped`.

### The agents

| # | Agent | File | Responsibility |
|---|-------|------|----------------|
| 1 | Requirement Extraction | `agents/extraction.py` | Split input into numbered lines, detect stakeholder tags, extract and normalise statements, kinds (functional / non-functional / business rule / constraint), IDs |
| 2 | Requirement Classification | `agents/classification.py` | Multi-label classification into 16 categories, priority, confidence, dependencies |
| 3 | Quality & Conflict | `agents/quality.py` | Weak words, TBDs, infeasible targets, missing acceptance criteria, duplicates (Jaccard), numeric conflicts (same topic, same unit, different value), stakeholder conflicts, undefined acronyms; OpenAI semantic review |
| 4 | Compliance & Regulatory | `agents/compliance.py` + `services/rag.py` | Retrieve allowlisted KB passages, map requirements to controls with verified evidence, detect domain-expected compliance gaps |
| 5 | Security & Privacy | `agents/security_privacy.py` | Coverage of 10 security areas, missing controls + safeguards, STRIDE threats, privacy considerations |
| 6 | Risk Analysis | `agents/risk.py` | Risk register, score = likelihood x impact, severity bands, per-requirement risk level |
| 7 | SDLC Selection | `agents/sdlc_selection.py` | 13 project factors -> weighted affinity scoring of 7 SDLC models -> ranking, justification, trade-offs |
| 8 | Workflow Generation | `agents/workflow.py` | Model template + project-specific customisation rules + traceability checkpoints |
| 9 | Documentation & Validation | `agents/documentation.py`, `agents/validation.py` | User stories, acceptance criteria, use cases, traceability matrix, consistency checks, executive summary |

### How the SDLC recommendation is calculated (`agents/sdlc_selection.py`)

1. **Factors.** 13 project characteristics are rated 1–5 by explicit rules from the analysis (requirement
   stability, regulatory criticality, security risk, complexity, size, legacy dependence, expected change,
   continuous delivery, stakeholder availability, testing/documentation needs, budget/schedule constraints,
   formal verification, failure consequences). In OpenAI mode the model may adjust a rating by **at most ±1**
   and must justify it; the rule value is always shown.
2. **Affinity table.** Each model has an affinity from −2 to +2 per factor (+2 = favoured when the factor is
   high, −2 = favoured when it is low). Example: Agile has +2 for expected change and −2 for formal verification.
3. **Score.** `x = (rating − 1) / 4`, `fit = x` if affinity > 0 else `1 − x`,
   `score = 100 × Σ(weight × |affinity| × fit) / Σ(weight × |affinity|)`.
4. **Decision rules** subtract points where a model is clearly unsuitable (e.g. R1: stability ≤ 2 → Waterfall −10).
5. **Ranking.** All 7 models (Agile, DevSecOps, Spiral, V-Model, Waterfall, Hybrid Agile+V-Model,
   Hybrid Agile+DevSecOps) are ranked; the top one is recommended; ≥ 60 counts as suitable. Hybrids are scored
   exactly like the others.

Scores are **suitability scores, not probabilities**, and the LLM never sets them. Weights are editable by an
administrator in **Settings**; the full matrix and rules are shown there and in the API (`GET /api/sdlc/config`).

### How the workflow is generated (`agents/workflow.py`)

The selected model defines ordered phases, each mapped to a generic stage (plan, design, build, verify,
accept, release, operate) that supplies default activities, roles, deliverables, entry/exit criteria and
human approval gates. About 20 customisation rules then add items driven by the project — e.g. transaction
integrity testing for payments, a DPIA when personal data is processed, DR drills for availability targets,
model validation for ML-based fraud detection, conflict resolution tasks listing the conflicting requirement
IDs. Traceability checkpoints list the actual requirement IDs. In OpenAI mode the model tailors phase
descriptions and may add up to three activities per phase; it cannot remove approval gates. If a project
manager overrides the SDLC recommendation, the workflow is regenerated for the chosen model.

---

## 2. Technology stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 15 (App Router), TypeScript, Tailwind CSS 4, shadcn/ui, Lucide icons, Recharts |
| Backend | Python 3.13, FastAPI, Pydantic 2, SQLAlchemy 2 |
| AI | Official OpenAI Python SDK (Responses API with structured outputs, embeddings `text-embedding-3-small`) |
| Database | PostgreSQL 16 + pgvector (Docker Compose); SQLite is used by the automated tests |
| Documents | PyMuPDF (PDF read + PDF export), python-docx (DOCX read + export), csv |

---

## 3. Project structure

```
ReqPilot2/
├── docker-compose.yml            PostgreSQL 16 + pgvector (host port 5433)
├── backend/
│   ├── requirements.txt, .env.example
│   └── app/
│       ├── main.py               FastAPI app, middleware, startup (create tables, seed)
│       ├── config.py             all settings from environment variables
│       ├── database.py, models.py, schemas.py, serializers.py
│       ├── security.py           hashing, sessions, RBAC, rate limiting, audit, masking, prompt-injection checks
│       ├── llm.py                OpenAI integration (structured outputs, retries, logging, embeddings)
│       ├── rules.py              rule-based financial RE knowledge (keywords, topics, security areas)
│       ├── pipeline.py           the central orchestrator
│       ├── agents/               the nine agents
│       ├── services/             rag.py, documents.py (upload validation/parsing), exports.py (DOCX/PDF)
│       ├── api/                  auth, projects/analyses, review (approvals), knowledge, admin
│       ├── evaluation.py         benchmark evaluation module
│       └── seed.py               demo users + knowledge base
│   └── tests/                    pytest suite
├── frontend/                     Next.js app (app/, components/, lib/)
├── knowledge_base/               demo KB documents (illustrative policies + regulatory reference catalogue)
└── evaluation/benchmark.json     synthetic 4-case benchmark with ground truth
```

---

## 4. Installation

Prerequisites: **Python 3.11–3.13**, **Node.js 20+**, **Docker Desktop**.

```powershell
docker compose up -d
```

```powershell
cd backend; py -3.13 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt; Copy-Item .env.example .env
```

```powershell
cd frontend; npm install
```

### OpenAI configuration

Edit `backend/.env`:

```
OPENAI_API_KEY=sk-...           # leave empty for demonstration mode
OPENAI_MODEL=gpt-4.1-mini       # any model that supports structured outputs, e.g. gpt-4.1, gpt-5-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
```

The key is read only by the backend and is never returned by any endpoint. After adding a key, restart the
backend and click **Re-index embeddings** in the Knowledge Base (or call `POST /api/knowledge/reindex`) so the
seeded documents get pgvector embeddings for semantic retrieval.

### Environment variables (`backend/.env`)

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | `postgresql+psycopg://reqpilot:reqpilot@localhost:5433/reqpilot` | Database connection |
| `OPENAI_API_KEY` | empty | Enables real AI mode |
| `OPENAI_MODEL` | `gpt-4.1-mini` | Chat model for all agents |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | Embeddings (1536 dimensions) |
| `OPENAI_TIMEOUT_SECONDS`, `OPENAI_MAX_RETRIES` | 120, 3 | SDK timeout and automatic retries (429/5xx/timeouts) |
| `LLM_CONCURRENCY`, `LLM_BATCH_SIZE` | 4, 20 | Parallel LLM calls; requirements per batch |
| `SESSION_HOURS`, `COOKIE_SECURE` | 12, false | Session lifetime; set `true` behind HTTPS |
| `MAX_UPLOAD_MB`, `MAX_REQUIREMENTS_CHARS` | 10, 300000 | Input limits |
| `DEFAULT_RETENTION_DAYS` | 365 | Data-retention period (editable in Settings) |
| `FRONTEND_ORIGIN` | `http://localhost:3000` | CORS origin |
| `DEMO_USER_PASSWORD` | `ReqPilot@2026` | Password of the seeded demo accounts |

Frontend: optional `BACKEND_URL` (default `http://127.0.0.1:8000`) for the `/api` proxy.

### Database setup and "migrations"

`docker compose up -d` starts PostgreSQL 16 with pgvector on host port **5433** (container `reqpilot2-db`).
ReqPilot deliberately has no migration framework: on startup `init_db()` runs `CREATE EXTENSION IF NOT EXISTS
vector` and SQLAlchemy `create_all`, which creates any missing tables. To run this step on its own:

```powershell
cd backend; .\.venv\Scripts\python.exe -m app.seed
```

(This also loads the demo users and the knowledge base; it never duplicates data.) To start from an empty
database: `docker compose down -v` then `docker compose up -d`.

---

## 5. Running

```powershell
cd backend; .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

```powershell
cd frontend; npm run dev
```

Open <http://localhost:3000>. API docs: <http://localhost:8000/docs>. For the demonstration, a production
build is faster and closer to a real deployment:

```powershell
cd frontend; npm run build; npm run start
```

### Demo accounts (password = `DEMO_USER_PASSWORD`)

| Role | E-mail | Can |
|------|--------|-----|
| Business Analyst | analyst@reqpilot.local | create projects, run analyses, review requirements, approve baseline |
| Project Manager | pm@reqpilot.local | the above + approve/override SDLC, approve workflow |
| Compliance Officer | compliance@reqpilot.local | approve/flag compliance mappings, manage the knowledge base, review security/privacy requirements |
| Security Officer | security@reqpilot.local | review security/privacy requirements, upload KB documents |
| Auditor | auditor@reqpilot.local | read everything, view audit logs |
| Administrator | admin@reqpilot.local | everything, users, settings, retention |

### Demonstration data

* **Knowledge base** (`knowledge_base/`, loaded automatically): six *illustrative* fictional "Demo Bank"
  policies (information security, data protection, payments, KYC, lending, IT operations) and a *reference*
  catalogue of real Indian regulations/standards (DPDP Act 2023, RBI directions, CERT-In directions, PCI DSS,
  ...) at document level only. To add authentic evidence, download official documents from the issuers and
  upload them in **Knowledge Base** with authenticity "official".
* **Example requirement sets**: on **Analyse Requirements**, use *Load an example requirement set*
  (digital banking — 41 requirements, loan processing, payments hub, fraud detection).

---

## 6. Demonstrating the complete workflow

1. Sign in as **analyst@reqpilot.local** → **Analyse Requirements**.
2. *Load an example requirement set* → **Digital Banking Application** (or paste your own list / upload files).
3. Click **ANALYSE REQUIREMENTS**. The progress panel shows the 12 agent stages; no questions are asked.
4. Results open automatically: Overview (executive summary, KPIs, agent log, validation report),
   Requirements, Quality, Compliance, Security & Risk, **SDLC Recommendation**, **SDLC Workflow**,
   Traceability and Open Issues.
5. **Reports & Exports**: download any of the 12 reports as PDF or DOCX.
6. Sign in as **pm@reqpilot.local** → **Approval Centre** → approve (or override) the SDLC recommendation, then
   approve the workflow (blocked until the SDLC decision is made).
7. Sign in as **compliance@reqpilot.local** → approve or flag compliance mappings.
8. Sign in as **auditor@reqpilot.local** → **Audit Logs** shows every login, analysis, approval and export.
9. **Evaluation** → *Run evaluation* runs the 4-case synthetic benchmark and shows the calculated metrics.

---

## 7. Tests and evaluation

```powershell
cd backend; .\.venv\Scripts\python.exe -m pytest -q
```

254 tests (SDLC scoring maths, every agent, the full pipeline on all benchmark cases, security helpers, file
validation, RAG, every API endpoint and role, approvals, all 12 exports in both formats, evaluation) run in about
20 seconds. The suite uses a temporary SQLite database and never calls the real OpenAI API: OpenAI-mode behaviour
is tested with a fake OpenAI client (verifying the client is called, invented IDs/quotes are rejected, sensitive
data is masked before it reaches the model, and API failures fall back to rules with a `partial` status).

```powershell
cd backend; .\.venv\Scripts\python.exe -m app.evaluation
```

runs the benchmark from the command line and writes `evaluation/results_<mode>.json`.
The benchmark is **synthetic** and its ground truth was written by the developer; see the limitations below.

---

## 8. Security controls implemented

* PBKDF2-SHA256 password hashing (390k iterations); server-side sessions in an HttpOnly, SameSite=Lax cookie
  (only a SHA-256 hash of the token is stored); logout revokes the session.
* Role-based access control (`ROLE_PERMISSIONS` in `security.py`), enforced on every endpoint.
* OpenAI key kept on the backend only; the browser talks to the backend through a same-origin proxy route
  (`frontend/app/api/[...path]/route.ts`) that forwards only the needed headers.
* Pydantic input validation; file validation (extension allowlist, size limit, magic-byte check, in-memory
  parsing, no files written to disk); input size limits.
* Sensitive-data masking (e-mail, card, Aadhaar, PAN, IFSC, phone, account numbers) before any text reaches
  the LLM; a finding reports how many values were masked.
* Prompt-injection protection: untrusted text is wrapped in tagged data blocks, the system prompt forbids
  following embedded instructions, suspicious phrases are detected and reported, suspicious KB chunks are
  excluded from retrieval, and all model references are validated.
* Retrieval-source allowlisting (only allowlisted documents of the project's jurisdiction are retrieved;
  uploads by non-managers need approval).
* Audit log of logins, analyses, approvals, uploads, exports and settings changes; approval history with
  previous/new values.
* In-memory rate limiting (global, login, analysis); security headers; generic error responses.
* Configurable data retention with an admin "apply retention" action.

Not implemented (would be needed in production): MFA, encryption at rest beyond what the database host
provides, TLS termination, SSO, distributed rate limiting. No security certification is claimed.

---

## 9. Known limitations

* Without an OpenAI key the analysis is rule-based (keyword rules + templates) and is labelled as such
  everywhere; semantic conflicts and paraphrased duplicates are mostly found only in OpenAI mode.
* The regulatory knowledge base ships no official regulatory text; mappings to illustrative or reference
  sources always need compliance-officer verification.
* The evaluation benchmark is small and synthetic, and its ground truth (including expected SDLC models) was
  written by the same developer who wrote the rule keywords, so rule-mode results are likely optimistic.
* Analyses run in a background thread of the API process; a server restart marks running analyses as failed.
* Stakeholder satisfaction and time saved versus manual analysis are not measured.
