# ReqPilot – Implementation Status (2026-11-24)

## Two-stage analysis (latest extension) - implemented and verified

### Stage 1 (mandatory): preliminary analysis → clarification → complete 12-agent pipeline
- `app/clarification.py`: lightweight preliminary analysis that reuses existing agent logic (rule extraction,
  keyword classification, quality checks, security coverage, compliance topic gaps) plus checks for missing
  performance/availability targets, integration behaviour, transaction limits and SDLC-relevant unknowns.
  Prioritised questions (max 12; fewer when the requirements are clear - nothing invented), OpenAI-generated
  when configured (validated: invented IDs/topics dropped, duplicates removed, cap enforced), rule templates otherwise.
- Clarification Centre (UI): answer / edit / defer / "needs stakeholder input" / reopen, answer validation per
  format, progress counts, critical-question warning, previous/next navigation, resume at any time,
  **Continue to Full Analysis** (atomic - double clicks cannot start two runs).
- `app/agents/clarification_context.py`: answers become `supplementary` information on the final requirements
  (original text untouched); findings get resolution status (resolved / partially resolved / deferred / unresolved).
  All 12 agents keep their responsibilities, prompts and schemas; their input context was extended (see README).
- Non-interactive API clients (no `interactive_clarification` flag) keep the one-call behaviour.
- Duplicate-submission guard; interrupted clarified pipelines return to the Clarification Centre on restart;
  failed Stage 1 runs can be retried with the saved answers.

### Stage 2 (optional): Refinement Centre
- Explicit start ("Open Refinement Centre"); overview, outstanding issues, further question batches and
  follow-ups, stakeholder-interview suggestions and recording (ReqPilot contacts nobody), requirement change
  requests (7 change types, drafts, four-eyes approval, changes requested / resubmit), regeneration plan +
  confirmation, approval history, version comparison.
- `app/regeneration.py`: dependency-aware selective regeneration creating a new analysis version (direct
  triggers + output-fingerprint propagation; Validation/Summary always re-run; unaffected agents logged as
  skipped/retained). Earlier versions are never modified.
- Approval Centre: requirement changes, stakeholder interviews, high/critical risk acceptance (new).

### Data model / migrations
- New tables: `clarification_questions`, `clarification_responses`, `stakeholder_interactions`,
  `requirement_changes`, `regeneration_runs`.
- New columns (added to the existing PostgreSQL DB by `app/migrations.py`, idempotent, verified on the real
  database with 22 existing analyses / 678 requirements - no data lost): analyses (stage1/2 status, version,
  parent/root ids, preliminary, submission_hash), agent_runs.stage, requirements.supplementary,
  findings.resolution/clarification_refs, trace_records.clarifications, risks.status.

### Verification
- Backend: `pytest` → **269 passed** (254 existing - 3 assertions updated for intended changes - plus 15 new
  two-stage tests incl. OpenAI-mode question generation with a fake client and fallback on failure).
- Frontend: `tsc`, `eslint` and `npm run build` pass.
- Real OpenAI (gpt-4.1-mini, synthetic data): Stage 1 full pipeline 19/19 LLM calls succeeded, 0/266 invalid
  references; OpenAI clarification questions generated and grounded; Stage 2 regeneration with OpenAI created
  version 2 with 3 agents retained.
- Browser: Stage 1 submission → preliminary → Clarification Centre (screenshot verified); results pages,
  version 2, Refinement Centre overview, Approval Centre and Settings verified by page text.

## Known limitations / not verified
- The browser pane was hidden during the second half of testing, so the Refinement Centre tabs and Clarification
  answer widgets were verified through their API calls and page text, not by clicking every control.
- In OpenAI mode, re-running an LLM-backed agent (e.g. Quality) can produce slightly different findings even for
  unchanged requirements; such differences propagate to dependent agents (shown as "may re-run" in the plan).
  Rules mode is fully deterministic.
- Stage 1 log entries have no timestamp column (durations only); interviews are recorded, not sent.
- TLS: the backend uses the OS certificate store (`truststore`) because this machine's HTTPS inspection broke
  Python's default verification.

## Earlier work (unchanged)
Backend, nine-agent pipeline (12 stages), RAG with pgvector, exports, HITL approvals, RBAC, audit log,
evaluation module and the 16-page frontend - see README.md.
