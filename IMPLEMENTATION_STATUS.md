# ReqPilot – Implementation Status (2026-11-24)

## Completed and verified
- **Backend** (FastAPI, SQLAlchemy, PostgreSQL 16 + pgvector via Docker on host port 5433): models, auth
  (PBKDF2 + HttpOnly sessions), RBAC, rate limiting, audit log, masking, prompt-injection checks.
- **Nine agents + orchestrator** (`backend/app/pipeline.py`), verified end to end in demonstration mode on all
  4 benchmark cases.
- **SDLC scoring engine** (deterministic, configurable weights) and **workflow generator**; an SDLC override (or
  re-approval) regenerates the workflow so it always matches the adopted model.
- **RAG**: chunking, topic tagging, pgvector semantic retrieval (only when every candidate chunk is embedded),
  lexical fallback, citation verification.
- **Exports**: 12 report types × PDF/DOCX (all 24 covered by automated tests).
- **HITL approvals** verified in the browser and by tests.
- **Automated tests**: `cd backend; .\.venv\Scripts\python.exe -m pytest -q` → **254 passed**. Writing them
  uncovered 13 app bugs (e.g. "login" counted as audit-logging coverage, invalid form values gave HTTP 500,
  stakeholder tags not masked before OpenAI, rejected settings requests partially applied); all fixed.
- **Frontend** (Next.js 15.5.x patched, shadcn/ui, Recharts): 16 required pages + Evaluation; `tsc`, `eslint`
  and `npm run build` succeed; verified in the browser (production build) at desktop and 375 px width.
- Fixed two UI bugs found during browser testing:
  - the example picker left `<body>` with `pointer-events:none`, swallowing the next click (now a non-modal menu);
  - Next's built-in `rewrites` proxy intermittently truncated large JSON responses at 128 KiB with ECONNRESET
    on Windows (4/30 in a stress test), leaving the results page stuck. Replaced by
    `frontend/app/api/[...path]/route.ts` (buffers the full body, retries idempotent GETs once): 180/180 OK.
- Fonts come from the `geist` npm package, so `npm run build` works offline.

## Remaining
1. **Real OpenAI mode has never run** (no API key was provided). Add `OPENAI_API_KEY` to `backend/.env`,
   restart, click *Re-index embeddings* in the Knowledge Base, run one analysis, and check the agent log for
   `partial` statuses and token usage. The integration is covered only by tests with a fake OpenAI client.
2. Optional: upload official regulatory documents to the Knowledge Base with authenticity "official".
3. The demo database contains ~20 analyses created while testing; `docker compose down -v` resets it.

## Key architecture decisions
- Hybrid agents: rule baseline always runs; OpenAI output is validated against input IDs/quotes; failures fall
  back to rules and are marked `partial` (never fabricated).
- The LLM may adjust an SDLC factor by at most ±1; suitability scores are always computed by code.
- `create_all` instead of a migration framework; SQLite for tests (embeddings stored as JSON).
- Same-origin `/api` proxy route so the session cookie works and the OpenAI key stays on the backend.
