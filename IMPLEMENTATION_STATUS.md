# ReqPilot – Implementation Status (2026-11-24)

## RAG across the agents (latest extension) - implemented and verified

### What exists now
- **One shared interface** in `app/services/rag.py`: `retrieve_for_agent(ctx, agent, items, requirements)` returns a
  `Knowledge` object (passages + metadata, prompt block as untrusted data, citation checks, retrieval log). It reuses
  the existing chunking, OpenAI embeddings, pgvector cosine search, lexical fallback, jurisdiction filter, allowlist
  and prompt-injection exclusion. New: per-agent document-type filter (`AGENT_PROFILES`), merge/dedupe of hits,
  relevance thresholds, exact clause-reference boost (e.g. `ISP-04`), query-embedding cache, failure isolation.
- **Agents using it** (passages in the prompt in OpenAI mode; verified links for rule findings in demo mode):
  Classification (glossary), Quality (requirements guideline), Compliance (unchanged mapping logic; now filtered to
  regulatory document types, logged, gaps carry structured citations), Security & Privacy (policies/standards;
  "policy controls" only with a verified citation), Risk (risk/operational guidance; evidence-backed vs inferred),
  SDLC (guidance after the fixed ranking, narrative only), Workflow (practice per phase), Documentation (standard in
  the story/use-case prompts), Validation (testing standard: coverage check + OpenAI missing-test review),
  Stage 1/2 clarification questions. Traceability reuses the verified citations; Extraction and Summary do not
  retrieve (no external knowledge needed).
- **Citation verification**: only passages placed in the agent's prompt can be cited (others rejected and counted);
  quotes must be found word-for-word (typographic quotes/dashes tolerated; a quote shortened with "..." only if
  every fragment matches in order); failed quotes are kept as *unverified* with the model's quote and the closest
  real sentence. `evidence_status` on findings/risks: verified / unverified / none / unavailable.
- **Knowledge base**: 7 new synthetic seed documents (requirements engineering guideline, SDLC handbook, testing
  standard, documentation standard, domain glossary, technology-risk guideline, OWASP reference catalogue); new
  doc types `engineering_guideline`, `domain_glossary`; PDF uploads chunked per page (page shown in citations).
- **Data model** (additive migration, verified on the real PostgreSQL DB): `agent_runs.retrieval`,
  `requirements.citations`, `findings.citations/evidence_status`, `risks.citations/evidence_status`,
  `sdlc_recommendations.citations`, `trace_records.citations/linked_findings`, `clarification_questions.citations`,
  `document_chunks.page`. Workflow phases and validation checks carry citations inside their JSON.
- **API**: `GET /api/knowledge/search` accepts `agent=` (that agent's document-type filter); upload accepts the new
  doc types; all evidence fields are returned by the existing analysis endpoints. No endpoint removed or changed.
- **Frontend**: shared `components/evidence.tsx` (status badge, collapsible sources with document, version, section,
  page, authority, authenticity, excerpt, verification, agent); used on finding cards, security/privacy/threat/risk
  views, SDLC guidance, workflow phases, traceability (findings + evidence column), requirement detail (glossary
  terms), validation report, agent log ("Knowledge base" column), Clarification Centre; KB page: new doc types,
  page numbers, "retrieve as agent" filter. Exports add evidence notes to findings, risks, SDLC and workflow.

### Verification
- Backend `pytest` (first pass): **296 passed** (269 existing - 3 expectations updated for intended changes: 14 seed documents,
  Validation now calls OpenAI, so it is no longer rule-only - plus 26 in `tests/test_rag_agents.py` and 1 in
  `tests/test_pgvector.py`, which runs the pgvector SQL path against PostgreSQL in a temporary schema).
  Mocked: OpenAI (fake client with valid, invented and misquoted citations); SQLite for everything except the
  pgvector test.
- Frontend: `tsc`, `eslint`, `npm run build` pass.
- **Real OpenAI (gpt-4.1-mini) + PostgreSQL/pgvector, synthetic data**:
  - Analysis #26 (digital banking, interactive Stage 1): all 12 agents completed; every RAG agent used
    `semantic (pgvector)`; 11 questions, 4 citing passages; 102 agent citations, 93 verified, 9 flagged; 38
    findings and 8 risks evidence-backed; 27 compliance mappings all verified; 12 invalid LLM references rejected.
  - Stage 2 on #26: approved wording change -> version 2 (#27); 7 planned agents re-ran (plus SDLC through
    propagation) and each retrieved again with the new requirement text; version 1 unchanged.
  - Analysis #28 (loan processing, non-interactive, final code): all 12 agents completed; 81 agent citations,
    70 verified, 11 flagged. Re-checking the 11 with the final quote rule: 4 verify (shortened quotes), 7 stay
    flagged (the model quoted the passage header) - fixed by a guardrail; a targeted real run of the
    Classification Agent afterwards gave 5 verified, 0 unverified citations.
  - UI checked on #26 in the browser pane by page text: evidence on findings, risks, SDLC, traceability, validation
    report, agent log; KB search with an agent filter.

### Final QA pass (verification after the final code)
- **Real run #29 (payments, interactive Stage 1)**: all 12 agents completed; all 10 retrieving steps used
  `semantic (pgvector)` within their document types; 82/84 agent citations quote-verified; 2/239 LLM references
  invalid (rejected). **Stage 2 #30**: approved edit -> version 2; 7 planned + SDLC (propagation) re-ran with fresh
  retrieval of the new text; version 1 unchanged.
- **Database**: 14 KB documents identical to their seed files (original 7 untouched); no test data/schemas; no NULLs
  in new columns; all 30 analyses load via the API (pre-RAG ones show no evidence); #1-#22 still hold 678 requirements.
- **Citation quality** (runs #26-#30): unverified citations are quotation errors (inner ellipsis, header text, reversed
  fragments, sentence attributed to the wrong passage) - never invented references (those are rejected, never stored);
  most cited passages were still relevant. Spot-checks of 45 quote-verified citations: 38 clearly supporting,
  6 loosely related, 1 wrong (glossary term "PA" matched inside "payment" - fixed).
- **Fixes in this pass**: whole-word glossary-term matching; savepoint around the pgvector query (a vector-query
  error no longer aborts the analysis transaction - proven against PostgreSQL); 5-minute semantic pause after an
  embedding/vector failure; Compliance prompt passages filtered by the shared semantic minimum (no existing mapping
  came from below it); guardrail: illustrative/reference passages are never law; UI wording "KB quote verified"
  (a quotation check, not proof of support) plus the relevance score; authority label "Security standard".
- Backend `pytest`: **298 passed**; frontend `tsc`, `eslint`, `npm run build` pass.

### Known limitations
- The engineering guidelines, glossary and risk guideline are fictional demo documents; the OWASP/RBI catalogues
  are document-level summaries. Evidence illustrates the mechanism, not authoritative obligations.
- No ANN index on the embedding column (exact search; fine for the ~130 chunks). Thresholds were calibrated on the
  bundled documents only.
- Model citations are quote-checked; whether the passage really supports the claim remains a human review step.
  Rule-based links depend on retrieval relevance.
- The full end-to-end runs (#26-#28) used the code before the final guardrail line; that line was verified with the
  targeted Classification run and the automated tests, not with another full real analysis.

## Two-stage analysis (previous extension) - implemented and verified

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
