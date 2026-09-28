# ReqPilot — P11 Guardrails Hardening

**Roadmap phase:** P11 (`docs/01-analysis.md` §P) · **Modules:** M11 (guardrails: masking, injection
heuristics, redaction), M4 (the LLM gateway's capability check and egress masking), M3 (the
Coordinator minting capability tokens), M6 / M9 (policy rule 14, approval-record guards), M10 (the
audit viewer and replay), M2 (sessions, deletion), M1 (the audit, history, deletion and session pages)

**Status: P11 COMPLETE — EXIT CRITERIA PASSED (offline suite and full PostgreSQL suite)**

*2026-09-28.* The six roadmap deliverables — sensitive-data masking, prompt-injection defence, agent
least privilege, session isolation, retention/deletion, and an audit viewer with replay — are
implemented, integrated into the existing P0–P10 paths, and tested through those paths. All three
approved exit criteria pass in `tests/workflow/test_p11_exit_test.py` (§16). The full suite passes
offline (SQLite) and against PostgreSQL 16 + pgvector with nothing skipped, both on the final tree
(§11).

> **The LLM proposes; deterministic code disposes — and P11 is about making that true under
> attack.** Nothing P11 adds depends on a model behaving. Masking runs before text is stored or
> sent; the gateway refuses a model call that does not carry the calling role's own capability
> token; the policy and the repositories refuse any agent read or write the token does not grant;
> the approval records themselves (ORM and PostgreSQL) refuse forged decisions and forged statuses;
> and the adversarial suite attacks all eight gates through the real paths — with **zero
> successful bypasses** out of 28 exercised attempts. Two real defects in earlier phases were found
> by that suite and fixed (§4.7). **No ninth ReqPilot gate exists**; G1–G8 are unchanged.

---

## 1. P11 scope and status

| Item | Result |
|---|---|
| **Deliverables** | Masking, injection defence, agent least privilege, session isolation, retention/deletion, audit viewer with replay — all implemented (§3, §4) |
| **Roadmap exit** | **PASSED** — 3/3 exit tests pass offline and on PostgreSQL — `tests/workflow/test_p11_exit_test.py` (§16) |
| **Offline suite** | **2,397 passed, 269 skipped (all PostgreSQL-only), 6 deselected (`llm`), 0 failed** |
| **PostgreSQL suite** | **2,666 passed, 0 skipped, 6 deselected (`llm`), 0 failed** (PostgreSQL 16.2 + pgvector 0.6.2, §11) |
| **Migration** | `0013_p11_guardrails_hardening`, additive, `down_revision = 0012_p10_workflow_generation` (§10) |
| **Gates** | G1–G8 unchanged in number and meaning; no ninth ReqPilot gate |
| **Benchmarks** | None created, none changed; E1–E9 not re-run (§13) |

**Out of scope, and not implemented:** P12 (evaluation, packaging, demo), password login and MFA
(ADR-009's authentication remains a development identity claim, §15), application-level field
encryption, a retention *schedule* (no approved source defines one), and any lending or
credit-decision function.

## 2. Source hierarchy used

1. `Problem Statement.docx` (reference copy `docs/problem-statement.md`) — §3 (masking before LLM
   submission), §17 (the twelve platform controls), §20 (audit).
2. `docs/01-analysis.md` — roadmap row P11 and its exit criterion; `FR-ING-003`, `FR-ADM-003`,
   `FR-ADM-005`, `FR-ADM-006`, `FR-PRJ-004`, `FR-AUD-003`–`005`; G.18 (the §17 coverage map); J.1
   (governance lives outside the LLM); ET-12 (prompt-injection resistance, zero gate bypasses).
3. `docs/02-architecture.md` — ADR-009 (server-side sessions), ADR-010 (append-only, hash-chained
   audit), E.1 (role capability matrix), J.2 (masking before chunking; the unmasking map kept apart),
   O.1–O.5 (audit record, taxonomy, replay, integrity), P (security controls, P.1 capability tokens,
   P.2 deletion and audit), Q.1–Q.4 (trust classes, trust boundary, why each attack fails,
   detection), U.4 (security tests).
4. The completed phase reports `docs/03`–`docs/13` (P3's masking seam and egress rule; P7's G8;
   P8's governance; P10's G6 verification).
5. Existing implementation and tests, which P11 extends rather than replaces.

Where a literal reading of a higher source conflicts with another, the conflict and its resolution
are recorded in §15 (deviations D-P11-1 … D-P11-3).

## 3. Requirements and their implementation locations

| Requirement / control | Status | Where |
|---|---|---|
| **FR-ING-003** mask before any LLM submission; keep the unmasking map locally | **Implemented** | `security/masking.py` (`PatternMasker`, now the default); `services/guardrails/masking.py`; ingestion `services/extraction/sources.py`; utterances `services/elicitation/sessions.py`, `services/clarification/service.py`; gateway `llm/gateway.py`; map table `masking_map_entry` |
| **FR-ADM-005** untrusted content is data, never instructions | **Implemented** (structural since P3; hardened) | `llm/assembly.py` (fences, unchanged); `llm/gateway.py` (capability check, egress masking, injection flags); `security/injection.py` (Q.4 heuristics); `services/extraction/runs.py` (`INJECTION_SUSPECTED`) |
| **FR-ADM-003** agent least privilege | **Implemented** | `domain/capabilities.py` (static table, sealed tokens); `domain/policy/policy.py` rule 14; `graph/capabilities.py` + the 12 agent-role call sites; `repositories/base.py` (`resource_id` for run scoping) |
| **FR-PRJ-004** / §17 session isolation | **Implemented** | `services/guardrails/sessions.py`; `api/dependencies.py` (`get_actor`); `auth_session` table; project scoping unchanged in every repository, now tested exhaustively (§5) |
| **FR-ADM-006** retention / deletion | **Implemented** | `services/guardrails/deletion.py`; `project_purge` trigger (migration 0013); tombstone guards; `repositories/base.py` (`refuse_if_deleted`); `security/redaction.py` |
| **FR-AUD-003** audit viewer, filterable | **Implemented** | `services/audit/replay.py` (`AuditViewer`, `AuditFilter`); `GET /projects/{id}/audit` filters; `/ui/projects/{id}/audit` |
| **FR-AUD-004** replay of a requirement and a risk | **Implemented** | `services/audit/replay.py` (`ReplayService`); `GET /requirements/{id}/history`, `GET /risks/{id}/history`; `/ui/.../history` |
| **FR-AUD-005** append-only through the application | **Kept; strengthened** | unchanged `audit_event` guards; audit payload strings masked before hashing (`services/audit/service.py`); approval decisions now append-only in the database too (§4.7) |

## 4. Security controls implemented

### 4.1 Sensitive-data masking

- **Detection** (`security/masking.py`): deterministic regular expressions with a checksum wherever
  the identifier has one — card numbers (Luhn), IBAN (ISO 13616 mod-97), Aadhaar-format numbers
  (Verhoeff), GSTIN, PAN, IFSC, e-mail, UPI handles, Indian and international phone numbers, and
  account / loan-account references recognised by their context word. Replacements are stable
  tokens (`[MASKED_PAN_1]`); the same value gets the same token; masking is idempotent.
- **Where it runs** (J.2's order): (1) **ingestion** — every source document and every recorded
  utterance (interview answers, clarification answers, and the questions the interviewer role
  proposed) is masked *before* it is segmented, stored, cited or sent anywhere, so stored offsets,
  citations and prompts agree on the masked text; (2) **the gateway** — every data block and every
  operator parameter is masked again immediately before prompt assembly (defence in depth; the
  count is recorded as `masked_at_egress`); (3) **the audit log** — string values in audit payloads
  are masked before the row is hashed. No project text is embedded (nothing retrieves over project
  documents, J.1), so there is no vector to mask.
- **The unmasking map** (`masking_map_entry`) is stored per masked source, project-scoped, never
  selected into a prompt, a log, an audit payload or an API response (`repr` omits values), and is
  deleted with the project.
- **The egress rule is unchanged** (P3): content whose *source* was neither masked at ingestion nor
  declared synthetic does not leave the machine. Rows stored before P11 say `not_masked` and stay
  subject to it.

### 4.2 Prompt-injection defence

The structural defence (Q.1–Q.3) predates P11 and is unchanged: only registered templates instruct;
project content, retrieved knowledge and earlier model output are fenced, nonce-bound data blocks;
every model output is a typed proposal validated before it can touch state; authority fields do not
exist in model schemas. P11 adds:

- **The capability check at the gateway** (§4.3): an injected instruction cannot turn one role's
  call into another's, or into a call by a role that makes none.
- **Q.4 heuristics** (`security/injection.py`): instruction override, role play, system-prompt
  probes, approval/state manipulation (incl. "suppress the finding"), permission escalation,
  exfiltration, delimiter/chat-template spoofing, encoded blocks. A hit **tags, never blocks**: the
  segment or utterance records its signal codes, `INJECTION_SUSPECTED` is audited (codes and
  positions only), the source page shows the tag; at the gateway, flagged block labels are recorded
  on the agent run and audited.

### 4.3 Agent least privilege (architecture P.1, `[DESIGN] D10`)

- **Static table** `ROLE_CAPABILITIES` from E.1: per role, the entity types it may read, may write
  (only the Coordinator — runs — and Validation — review items), whether it retrieves (Compliance and
  Security & Privacy only), whether it calls a model (the ten LLM roles). `HUMAN_APPROVAL` is never
  minted; `NEVER_WRITABLE` (approval tasks, projects, memberships, audit, baselines, allowlists, the
  knowledge base) is re-checked at verification.
- **Tokens** are frozen dataclasses sealed with an HMAC under a process-local key that is never
  stored or logged; verification recomputes the seal *and* re-derives the grants from the table, so
  an altered, widened, re-typed, subclassed, expired, future-dated or data-built token is refused.
  Tokens are bound to one run and one project and live 15 minutes.
- **Minting**: the Coordinator mints a token per agent invocation (`graph/capabilities.py`) at all
  twelve places a node hands the gateway to an agent role; the gateway refuses a structured call
  without the calling role's own valid token (`CapabilityEgressError`, recorded by the nodes as a
  failed run plus `PERMISSION_DENIED`).
- **Policy rule 14**: an `AGENT_ROLE` actor is authorised by its token alone — not by roles, not by
  `is_superuser`; audit and administration actions are never an agent's. Repositories pass the run id
  so a token for one run cannot read another run.
- **Evidence at run time**: a spy on the gateway during a full P8 analysis shows every one of the
  model calls carried a token minted for exactly that role and project (§5).

### 4.4 Session and project isolation

- **Server-side sessions** (ADR-009): 32 random bytes, SHA-256 stored, 8-hour expiry, revocation,
  inactive users refused; `Authorization: Bearer` or an httpOnly, SameSite=Strict cookie. A token
  presented with a *different* `X-ReqPilot-Actor` claim, or an invalid/expired/revoked token, is a
  401 — never a fallback to another mechanism. Roles are read from `project_member` on every request.
- The development header path now also refuses inactive users.
- Project isolation is unchanged in design (mandatory project predicate, policy isolation before
  permission, 404 for other projects) and is now tested across every project-scoped repository and
  the audit/replay readers (§5).

### 4.5 Retention and deletion (P.2)

`DELETE /projects/{id}` (Project Manager only, human only, the name repeated): in one savepoint,
the project row becomes a **tombstone** (name and description replaced, scope cleared,
`deleted_at`/`deleted_by` set); every content row of that project is purged — on PostgreSQL by the
`project_purge` trigger, which runs nested so the earlier phases' append-only guards let it through
by their existing `pg_trigger_depth() > 1` branch, elsewhere by the same deletes directly; the
LangGraph checkpoints of the project's runs are deleted (PostgreSQL store in-transaction; the
in-memory saver via a hook); the absence of any remaining content row is **verified over the live
schema**; and `PROJECT_DELETED` is audited with counts only. Any failure rolls the whole savepoint
back. Retries return the earlier receipt. Afterwards every write to the project is refused (410);
reads find nothing. **Retained:** the tombstone, `project_member`, the audit trail. **Not
deleted:** the shared knowledge base. **No retention period is asserted** — none is defined by the
approved sources.

### 4.6 Audit viewer and replay (O.3–O.5)

`AuditViewer` returns plain read models (never ORM rows) in chain order, filterable by requirement
(with all its versions, found from the record *and* from the trail), risk (with its mitigations),
subject, user, role (exercised, required, or the agent run's role), event type and time. For a
deleted project every payload is redacted on read. `ReplayService` applies each event to a
reconstruction, keeps actor/time/action/subject/change per step, then compares the result with the
current record; every disagreement, missing creation event, malformed event or chain divergence is
a **gap**, and a history is `complete` only with no gaps, a verified chain and an existing record.
Replay writes nothing. To make replay exact, two events now carry what replay needs (additive
payload keys): `REQUIREMENT_VERSION_CREATED` records the initial `state`, `RISK_RECORDED` the initial
`status` (and, on the human path, likelihood and impact).

### 4.7 The approval path's own records, and two defects found by the adversarial suite

- **Approval records** (ORM guard in `domain/models/approval.py`; PostgreSQL triggers in 0013):
  `approval_decision` is append-only; a new decision is accepted only against an `OPEN` task of the
  same project, in the task's role, by a member holding that role; a task's identity (project, gate,
  group, subject, role, `blocking`) never changes; a decided task never changes again; a task becomes
  `APPROVED` only with an `APPROVE` decision (`REJECTED` only with `REJECT`/`MODIFY`). The exact-version
  hash stays writable (the approval service re-checks it and refuses a mismatch; P1/P8 tests simulate
  that tampering). The approval service now refuses a non-human decider before loading the task.
- **Defect 1 — a G8 bypass (P7/P8 code).** The lifecycle guard counted open blocking tasks whose
  *subject is the version*; a G8 task's subject is the *risk*. So once a risk-owning role (e.g. the
  Analyst) accepted a HIGH risk in the register, its requirement could reach `VALIDATED` while the
  Security Reviewer's G8 task was still open. P7's own test did not notice because other G2/G3 items
  happened to be pending in its world. **Fix:** `RequirementService.build_context` also counts open
  blocking G8 tasks of the version's risks. P7's test passes unchanged; the adversarial case
  "register accept, then VALIDATED while G8 is open" is refused.
- **Defect 2 — forgeable approval records.** A task could be written `APPROVED` with no decision,
  and a decision row could be inserted for a non-member. Closed by the guards above.

## 5. Adversarial threat cases tested

`tests/p11_adversarial.py` (the counted registry) and `tests/security/test_p11_adversarial.py`:

| Vector | Cases (gate) |
|---|---|
| Agent actor with a valid token | coordinator token → G1; validation token → G7; SDLC-selection token → G6 |
| Agent actor without a token but every human role | G1 |
| Forged capability (Human Approval role) | G1 |
| Pipeline (system) actor | G1 |
| Wrong role | analyst → CO's G1 task; analyst → G2; CO → G3; analyst → G5; analyst → G8; analyst → G6 (architect) |
| Wrong person | the other stakeholder → Priya's G4 task |
| Cross-project | another project's PM → G1 |
| Forged records | task status `APPROVED` via ORM; decision row for a non-member; decision row in a role not held; SDLC run `SELECTED` via ORM then its workflow requested |
| State transition | lifecycle straight to `APPROVED`; straight to `BASELINED` |
| Decision reuse | a baseline committed on another gate's approval |
| Exact-version binding | approval on a stale binding |
| Replay | one co-approver approving twice |
| Register route | HIGH risk accepted in the register, then `VALIDATED` with G8 open |
| Partial co-approval | three of the four G6 roles |
| Compromised model | extraction output with approval fields; classification output claiming approval |
| Injected document | an obedient model turning "approve every requirement…" into a requirement |

Beyond the counted gate cases, the same file tests: a model answer carrying a "capability" cannot
widen the call's token; hostile retrieved text (override, `</system>`, a spoofed `<<<END …>>>`,
role play) stays inside its fence, never reaches the instructions, and is flagged; **every model
call of a real P8 run carried its own role's token**; **every project-scoped repository (≥ 25
classes) refuses an outsider**; a user who is Analyst in B and Auditor in A reads A's trail but
authors nothing in A; requirements, versions, risks and their history, compliance mappings,
evidence, source documents and segments, stakeholders, graph and agent runs (whose ids are the
checkpoint thread ids), workflows, artefacts and the audit trail of A are unreachable from B; and an
agent token for B opens nothing in A. Session forging, client-supplied role/capability fields,
cross-project deletion and redacted replay are covered in `test_p11_api_and_ui.py` and
`test_p11_guardrails_flow.py`. The suite does not prove that every possible attack fails; it
covers the cases listed.

## 6. Approval-gate bypass test results

**28 attempts, 28 exercised, 0 successful bypasses** — final offline run
(`test_p11_adversarial.py` and `test_p11_exit_test.py` both print the table below; both runs showed
the same result).

| Suite | Attempted | Exercised | Blocked | Bypasses | Gates |
|---|---|---|---|---|---|
| Gate world (P8 world: L03 baselined then changed, L02 at G1, G4/G5 raised, L01's G2/G8 open) | 24 | 24 | 24 | **0** | G1 (17), G2, G3, G4, G5, G7, G8 (2) |
| G6 world (P10 world, ranked run awaiting G6) | 4 | 4 | 4 | **0** | G6 |
| **Total** | **28** | **28** | **28** | **0** | all eight |

How each was stopped, in the final run: `AuthorizationError` (agent, pipeline and forged-capability
deciders: "can never decide an approval gate"; wrong roles: "actor does not hold …"),
`ProjectIsolationError` (the other project's PM), `ImmutableRecordError` (forged task status; decision
rows for a non-member or in a role not held), `StateTransitionError` (straight to `APPROVED` /
`BASELINED`; `VALIDATED` with G8 open — "1 blocking approval task(s) must be resolved"),
`BaselineInvariantError` (a baseline on another gate's approval), `StaleApprovalError` (a stale
binding), `ApprovalError` (a second decision on a decided task; the wrong stakeholder at G4); the
compromised-model and injected-document runs called the model and produced no approval, no gate
decision and no version past `CLASSIFIED`; the forged `SELECTED` column was not honoured (workflow
refused: `G6_NOT_PASSED`); three of four G6 approvals left the run `awaiting_g6`.

Each case runs in a savepoint rolled back afterwards; a case is **exercised** only if it met the
refusal it names (or returned evidence gathered from the path itself), and a **bypass** is any
forged decision, a task closed without its decision, a moved lifecycle state (or a new version past
`CLASSIFIED`), a new baseline or a newly selected SDLC run. The control case — both G1 co-approvers
signing legitimately — **is** reported as a gate passing, so the oracle is not vacuous.

## 7. Synthetic financial identifier masking test results

`test_criterion_2_synthetic_financial_identifiers_are_masked_before_egress`
(final offline run: **passed**):

- **Corpus:** **13 synthetic values of 11 kinds** — two card numbers (published test numbers), an
  IBAN (the ISO published example), two Aadhaar-format numbers (built with a computed Verhoeff
  digit), PAN, GSTIN, IFSC, e-mail, UPI handle, phone, an account number and a loan-account
  reference — each in an ordinary sentence of a synthetic workshop transcript.
- **The boundary:** the transcript is ingested as `UNCLASSIFIED` (not synthetic, so the egress rule
  would refuse it unmasked) and extracted by the real pipeline against a provider that *leaves the
  machine*. The test asserts the provider **was called** (the masked text was allowed out — the test
  cannot pass by the provider never being invoked), that **no raw value appears in any request it
  received**, and that the run completed.
- **Coverage:** the unmasking map holds exactly the 13 values, one entry per value, with the
  category counts of the corpus and **all 11 masker categories**; stored text, the resulting
  requirement and every audit payload contain no raw value.
- **Negative control:** the same leak check finds all 13 values in the unmasked text.
- Related (all passed): unmasked, non-synthetic legacy documents are still refused before any
  provider call (`test_without_masking_the_egress_rule_still_refuses_real_data`, and the P3/P9
  security tests); the gateway masks content that reached it by another path
  (`masked_at_egress = 13`); interview answers are masked before storage; each kind is masked in
  ordinary text, repeated values share a token, masking is idempotent, and the map never prints a
  value.

**What is not detected** (asserted in `test_p11_masking_and_injection.py`, not merely stated):
names, addresses, dates of birth and free-text descriptions of a person; identifiers in a format
the rules do not list (SWIFT/BIC codes are omitted on purpose — their shape matches ordinary
upper-case words); a bare digit run with no context word (an account number without "account");
identifiers split across lines or written in words; checksum-invalid (mistyped) card or
Aadhaar-format numbers. Known false positive: a ten-digit number starting 6–9 is masked as a phone
number. ReqPilot handles synthetic data only (Phase 0 C.3); masking is a control on top of that
rule, not a substitute for it.

## 8. Requirement and risk replay test results

`test_criterion_3_replay_reconstructs_a_requirements_and_a_risks_history`
(final offline run: **passed**):

- **Requirement:** L01 of the P8 world is validated, co-approved at G1 and baselined, then changed
  (version 2). Replay is compared with an expectation the test computes with its own queries: the
  version numbers, content hashes and states of both versions match the persisted rows; the current
  version is version 2; v1's transitions, reconstructed step by step in chain order, equal the
  persisted `STATE_TRANSITION` events and end `PENDING_APPROVAL → APPROVED → BASELINED`; the result
  is `complete`.
- **Risk:** L01's HIGH risk, decided at G8 by the Security Reviewer, with one mitigation accepted.
  Status, severity, likelihood, impact and matrix version match the persisted risk; the severity is
  the **matrix's** (`severity_source = matrix`), separate from anything a model proposed (O.4); the
  last decision is the G8 `APPROVE`; the accepted mitigation matches the register; `complete`.
- **Tampering:** one of the requirement's audit rows is rewritten beneath the application (possible
  on SQLite, which has no trigger; PostgreSQL refuses it); replay then reports the chain as broken
  and the history as **not complete**.
- Related (`test_p11_guardrails_flow.py`, all passed): a version written around the service is
  reported as having no creation event; a malformed event is reported and skipped; replay writes
  nothing; after deletion the history survives from the redacted trail and is explicitly marked
  incomplete; outsiders get `ProjectIsolationError`.

**Limitations:** replay is exact for requirement lifecycle and risk status, severity and
mitigations — the facts the events record. Content is shown by hash (payloads carry references
only, O.1); the text of a version is read from the current record, not from the trail. Events
written before P11 lack the new initial-state keys; for such a version replay takes the initial
state from the first recorded transition, or — if it never moved — from the record, and says so
(`initial_state_from_record`).

## 9. API and UI changes

**API** (`api/routes/guardrails.py`, `api/routes/governance.py`):

| Method | Path | Who | What |
|---|---|---|---|
| POST | `/api/v1/auth/sessions` | the identified user | opens a session; token returned once and set as an httpOnly, SameSite=Strict cookie |
| DELETE | `/api/v1/auth/sessions/current` | the session holder | revokes it |
| GET | `/api/v1/auth/whoami` | any | the user and the roles read now from `project_member` |
| DELETE | `/api/v1/projects/{id}` | Project Manager | deletion (body: `confirm_name`); 200 with a receipt only after commit; retry returns `already_deleted` |
| GET | `/api/v1/projects/{id}/audit` | audit readers | now filterable: `requirement_id`, `risk_id`, `subject_type`, `subject_id`, `actor_ref`, `role`, `event_type`, `since`, `until`; redacted for a deleted project |
| GET | `/api/v1/projects/{id}/audit/verify` | Auditor | hash-chain verification |
| GET | `/api/v1/requirements/{id}/history`, `/api/v1/risks/{id}/history` | audit readers | replay: `complete`, `gaps`, `current`, `reconstructed`, `steps` |

New error mapping: `ProjectDeletedError` → 410; `CapabilityError` → 403 (it is an
`AuthorizationError`). No endpoint decides a gate — `POST /approval-tasks/{id}/decide` is still the
only decision path — and none returns the unmasking map. Request models forbid extra fields (422).

**UI** (`web/guardrails.py`, templates): the audit trail page gains filters, a redaction banner and
replay links; `/ui/requirements/{id}/history` and `/ui/risks/{id}/history` show the **current
record** and the **reconstructed history** separately, with a banner listing every gap when the
history cannot be shown as complete; `/ui/projects/{id}/delete` (confirmation by name; the result
page is rendered only after the deletion is verified and committed; the Project Manager link
appears on the project page); `/ui/login` and `/ui/logout` (development session cookie); source
pages show injection tags per segment. Every page reads through the same services as the API.

## 10. Database migration details

`alembic/versions/0013_p11_guardrails_hardening.py`, additive, `down_revision =
0012_p10_workflow_generation`:

- tables `auth_session`, `masking_map_entry`, `project_purge` (unique per project, append-only);
- columns `project.deleted_at`, `project.deleted_by`; `utterance.masking_status` (existing
  `masking_status_enum`, default `NOT_MASKED`), `utterance.masker_id`, `utterance.injection_signals`;
  `source_chunk.injection_signals`;
- audit event values `INJECTION_SUSPECTED`, `AUTH_SESSION_ISSUED`, `AUTH_SESSION_REVOKED` (kept on
  downgrade, as every phase's audit values are);
- PostgreSQL functions/triggers: `reqpilot_purge_project` (`project_purge_cascade`, refuses unless
  the project is tombstoned, deletes that project's rows only, never `audit_event`,
  `project_member` or the knowledge base); `project_tombstone_guard`; `approval_task_guard`;
  `approval_decision_append_only`; `approval_decision_guard`;
- **downgrade refuses** while any tombstone exists (it would resurrect deleted projects as live,
  empty ones).

No earlier migration and no earlier trigger is modified. Verified: upgrade / downgrade / re-upgrade
on SQLite (`test_migrations.py`) and on PostgreSQL (§11).

## 11. Tests run and exact results

**New tests: 77 test functions in 7 new files (+2 in existing phase-gate files), plus 2 shared helper modules.**

| File | Tests | Covers |
|---|---|---|
| `tests/unit/test_p11_masking_and_injection.py` | 15 functions (45 cases) | each identifier kind, many per text, idempotence, map privacy, non-matches, documented false negatives/positive, malformed input, checksums, injection signals and non-signals, redaction |
| `tests/unit/test_p11_capabilities.py` | 18 functions (44 cases) | the E.1 table, never-minted roles, immutability, forged/widened/data-built/expired tokens, the full per-role × resource × access matrix through the policy, gates and human decisions, run and project binding, the gateway |
| `tests/integration/test_p11_guardrails_flow.py` | 20 | masking through ingestion → extraction → external provider; unchanged egress refusal; gateway defence in depth; interview answers; injection tagging; deletion (content, isolation, retention, redaction, authorisation, idempotence, rollback, coverage of every table); replay (two versions, G8 risk, read-only, missing / malformed / tampered events, after deletion); viewer filters and scoping; sessions |
| `tests/integration/test_p11_api_and_ui.py` | 6 | sessions over HTTP; session scoping; forged role/capability fields; deletion over HTTP (403/404/400/200/410); filters, verify, replay endpoints; UI pages |
| `tests/security/test_p11_adversarial.py` | 10 | the counted gate suites (§6), the oracle control, injection cases, run-time least privilege, isolation across every repository |
| `tests/integration/test_p11_postgres.py` | 5 | the purge trigger incl. checkpoints and another project; guards still refuse direct deletes; purge needs a tombstone; tombstone is final; approval records unforgeable; masking columns, enum values, sessions |
| `tests/workflow/test_p11_exit_test.py` | 3 | the three exit criteria (§16) |
| `tests/integration/test_migrations.py` | +1 | P11 downgrade and re-upgrade |
| `tests/integration/test_api_health.py` | +1 | P11 endpoints present; one decision path |

**Results** (2026-09-28, final working tree):

| Gate | Result |
|---|---|
| **Offline suite** (`pytest -q`, default marks) | **2,397 passed, 269 skipped (all PostgreSQL-only), 6 deselected (`llm`), 0 failed** — the 269 skips are the PostgreSQL-only tests (they skip visibly without `REQPILOT_TEST_DATABASE_URL`) |
| **PostgreSQL suite** (`REQPILOT_TEST_DATABASE_URL=… pytest -q`, default marks) | **2,666 passed, 0 skipped, 6 deselected (`llm`), 0 failed**, exit 0, 24 min 40 s — every one of the 269 PostgreSQL-only tests ran; 2,397 + 269 = 2,666 |
| P11 tests | all 77 new test functions and the 2 new phase-gate functions pass; the 5 in `test_p11_postgres.py` ran and passed on PostgreSQL |
| Exit test | `tests/workflow/test_p11_exit_test.py` — **3 passed** offline and **3 passed** on PostgreSQL |
| Adversarial suite | `tests/security/test_p11_adversarial.py` — **10 passed** in both runs; 28 attempted, 28 exercised, 0 bypasses |
| Earlier-phase regressions | every P0–P10 test, including the P7/P8 gate suites (`test_p7_gates.py`, `test_p8_governance.py`, `test_p7_security.py`, `test_p8_security.py`), every phase exit test and every earlier phase's PostgreSQL tests (`test_p1_workflow_on_postgres.py`, `test_p2_postgres_retrieval.py`, `test_p3`…`test_p10_postgres.py`, `test_postgres_specific.py`), **passed** in both runs; 0 failed |
| Migration 0013 | up / down / re-up on SQLite (`test_migrations.py`, 16 passed); on PostgreSQL the suite migrated an empty database 0001 → 0013, and a separate scratch database was taken 0013 → 0012 → 0013 (the three tables, four functions and six triggers removed and restored; the three audit enum values kept, as documented in §10) |

The one warning in both runs is a third-party `DeprecationWarning` from `starlette.testclient`
(an `anyio` alias); it is not raised by ReqPilot code.

**PostgreSQL environment.** The cluster the earlier phases used (a scratch `pgserver` install under
the session's temporary folder) was found damaged on 2026-09-28 — most of its data directory and
bundled binaries had been removed from disk, most likely by temporary-folder cleanup; it held test
data only. With the author's approval it was replaced by the same method: `pgserver` 0.1.4
(`pgserver-0.1.4-cp311-cp311-win_amd64.whl`, SHA-256 verified against PyPI before installation),
in its own Python 3.11 environment outside the temporary folder and outside the repository. It
bundles **PostgreSQL 16.2** and **pgvector 0.6.2**, listens on 127.0.0.1 only, and served a fresh,
empty database created for this run. Nothing in the repository or the project's environment was
changed to install it. Before the loss, targeted PostgreSQL runs earlier in the P11 work had also
passed (0013 up/down/up twice; `test_p11_postgres.py` 5 passed; `test_postgres_specific.py` 10
passed); the full run above supersedes them.

**Earlier-phase tests changed** (each keeps its behavioural assertion; the change is the minimum P11
requires):

| Test | Why it changed | What is still asserted |
|---|---|---|
| `test_p3_sources.py` (2 assertions), `test_p3_api_and_ui.py` (1) | they pinned P3's "nothing was masked" stage | the record states exactly what ran — now `masked` / `pattern-masker@1` — and the synthetic workshop text is unchanged |
| `test_p3_security.py`, `test_p9_security.py` | ingestion now masks, so their "unmasked real data" premise needed data that is really unmasked | the fixture rows are marked `not_masked` (as pre-P11 rows are); the refusal before any provider call is asserted exactly as before |
| `test_llm_gateway.py`, `test_openai_provider.py`, `tests/llm/test_openai_live.py` | the gateway now requires the Coordinator's token for the calling role | the helpers mint that role's token; every assertion about assembly, retries, repair, accounting and egress is unchanged |
| `test_postgres_specific.py` | the P1 baseline-trigger test inserted a decision by a random non-member as setup, which P11's decision guard refuses; P11 tables permitted | the setup's decider is a real analyst of the project; the baseline-member trigger refusal is asserted as before |
| `test_migrations.py`, `test_api_health.py` | phase gates | P11 tables and endpoints permitted; P12 (evaluation) still absent; the one decision path unchanged |

The P2 test that an agent holding the KB-administrator role cannot curate still passes unchanged:
the agent is now refused one check earlier, and the refusal states it is a human decision.

## 12. Quality-gate results

All run against the final working tree:

| Check | Result |
|---|---|
| `ruff check .` | **All checks passed** |
| `ruff format --check .` | **492 files already formatted** |
| `mypy` (packages `reqpilot`) | **Success: no issues found in 302 source files** |
| `lint-imports` | **6 contracts kept, 0 broken** |
| `scripts/check_provider_sdks.py` | **`openai` (all approved)** |

## 13. Files created and modified

Inventory from read-only `git status --porcelain --untracked-files=all` against `HEAD` 5e56d05,
which was clean when P11 began — so every change below is P11's. No tracked file was deleted.

**Created (28): 18 source, migration and template files, 9 test files, this report.**
- `alembic/versions/0013_p11_guardrails_hardening.py`
- `src/reqpilot/domain/capabilities.py`, `src/reqpilot/domain/models/guardrails.py`
- `src/reqpilot/security/injection.py`, `src/reqpilot/security/redaction.py`
- `src/reqpilot/services/guardrails/{__init__,masking,sessions,deletion}.py`,
  `src/reqpilot/services/audit/replay.py`
- `src/reqpilot/graph/capabilities.py`
- `src/reqpilot/api/guardrails_schemas.py`, `src/reqpilot/api/routes/guardrails.py`
- `src/reqpilot/web/guardrails.py`,
  `src/reqpilot/web/templates/{history,login,project_delete,project_deleted}.html`
- tests: `tests/p11_helpers.py`, `tests/p11_adversarial.py`,
  `tests/unit/test_p11_{masking_and_injection,capabilities}.py`,
  `tests/integration/test_p11_{guardrails_flow,api_and_ui,postgres}.py`,
  `tests/security/test_p11_adversarial.py`, `tests/workflow/test_p11_exit_test.py`
- `docs/14-p11-guardrails-hardening.md` (this report)

**Modified (54): 43 source and template files, 10 test files, `README.md`.**
- Domain: `enums.py` (audit events, `MaskCategory`, `InjectionSignal`), `errors.py` (capability,
  deletion, session errors), `policy/policy.py` (rule 14, `PROJECT_DELETE` human-only),
  `provenance.py` (egress facts on `ModelMeta`), `models/__init__.py`, `models/approval.py`
  (approval-record guard), `models/elicitation.py` and `models/extraction.py` (masking and injection
  columns), `models/identity.py` (tombstone and its guard).
- LLM and graph: `llm/gateway.py` (capability check, egress masking, flags); `graph/builder.py`
  (memory-thread hook), `graph/clarification_runner.py`,
  `graph/nodes/{analysis,compliance,elicitation,quality,risk,sdlc}.py` (the Coordinator mints a
  token per agent invocation; utterance masking facts).
- Repositories: `base.py` (tombstone refusal, `resource_id`), `extraction.py` (run scoping).
- Services: `approval/service.py` (non-human refusal first), `audit/service.py` (payload masking),
  `clarification/service.py`, `elicitation/sessions.py`, `elicitation/provenance.py`,
  `extraction/sources.py`, `extraction/runs.py` (masking, map, injection tagging and audit),
  `requirements/service.py` (the G8 guard fix; initial state recorded), `risk/engine.py` and
  `risk/service.py` (initial status recorded).
- Security: `security/masking.py` (the protective masker, now the default).
- API: `app.py`, `dependencies.py` (sessions), `errors.py` (410), `routes/governance.py` (audit
  filters); `main.py`.
- Web: `router.py` (audit filters); templates `audit.html`, `base.html`, `project.html`,
  `requirement.html`, `risk.html`, `source.html`.
- Tests (§11): `tests/integration/{test_api_health,test_migrations,test_p3_api_and_ui,
  test_p3_sources,test_postgres_specific}.py`, `tests/security/{test_p3_security,
  test_p9_security}.py`, `tests/unit/{test_llm_gateway,test_openai_provider}.py`,
  `tests/llm/test_openai_live.py`.
- Documentation: `README.md`.

**Deleted:** none. **Not in the archive:** `deliverables/` itself, and the scratch scripts used during
the work (kept outside the repository).

**Unchanged:** every earlier migration and trigger; the eight gates and their required roles; the
requirement lifecycle's transitions; P2's allowlist-in-the-query retrieval; `docs/01`, `docs/02` and
every earlier phase report; all frozen benchmarks, manifests and evaluation results.


**Benchmark integrity:** nothing under `data/` or `docs/evaluation/` changed; `tests/p8_helpers.py`
(hash-pinned in the P8 benchmark manifest) and `src/reqpilot/rules/data/sdlc_rules.yaml` (pinned in
the P9 manifest) are untouched; the evaluation harnesses are untouched; E1–E9 were not re-run; no
earlier phase report, `docs/01` or `docs/02` was edited.

## 14. ZIP path and verification results

`deliverables/P11_Guardrails_Hardening.zip` contains every P11-created and P11-modified file listed
in §13 — this report included — at its repository-relative path, plus
`P11_MANIFEST.txt` listing each archived file with its SHA-256 and whether it was created or
modified. Excluded: `.git`, virtual environments, caches and compiled files, secrets and `.env`
files, scratch scripts, and the archive itself. After building, the archive was opened, its entry
list compared with the inventory (no missing, extra or duplicate entries), and every file's SHA-256
recomputed from the archive and matched against the manifest and against the working tree.

**Result (final build, after this report's PostgreSQL update):** 82 files (28 created, 54 modified)
plus the manifest; `testzip` clean; no duplicate, absolute or `..` paths; archive entries = inventory
= manifest; every SHA-256 matches both the manifest and the working tree; no excluded path present.
The archive's own size and hash are reported with the completion report, since this report is inside
it.

## 15. Known limitations and deferred work

**Deviations from a literal reading of the approved design:**

- **D-P11-1 — Audit redaction on read, not in place.** P.2 says deletion retains audit events "with
  content-bearing payload fields redacted". ADR-010 makes `audit_event` append-only at three levels
  and every row hash covers its payload; rewriting payloads would both bypass that immutability and
  break the chain an auditor verifies. P11 keeps stored rows byte-for-byte and redacts every
  free-text-shaped value, and every value under a content-bearing key, on **every** read path (API,
  viewer, replay). What limits the stored bytes is the O.1 references-only rule (enforced at append)
  and P11's masking of payload strings before hashing. The stored bytes of a deleted project's trail
  are therefore not rewritten; a database administrator could read them.
- **D-P11-2 — The project row is a tombstone.** Deleting the row would null or break the per-project
  hash chain (the `audit_event` foreign key is `SET NULL`, which the audit trigger refuses). The row
  stays with its name replaced; memberships stay as the accountability record.
- **D-P11-3 — Authentication stays a development mechanism.** ADR-009's sessions are implemented,
  but obtaining one still rests on the development `X-ReqPilot-Actor` identity claim (disabled in
  production). **There is no password login and no MFA**; this is not production authentication.

**Limitations:**

1. Masking is pattern- and checksum-based; its coverage and blind spots are in §7.
2. Injection detection is heuristic and incomplete by design (paraphrase, other languages,
   instructions split across segments); it tags, it does not block. The structural controls are the
   defence.
3. `requirement` / `requirement_version` rows have **no PostgreSQL delete trigger and no ORM delete
   guard** (pre-existing since P1). They are protected by the absence of any application delete
   path, the policy, and — for baselined versions — the `RESTRICT` foreign key from
   `baseline_member`. P11 did not widen its scope to change this.
4. A task's `subject_version_hash` is still writable below the service (see §4.7); a changed hash
   can only make the approval service refuse.
5. Deduplication is by the hash of the *masked* text, so two uploads that differ only in their
   identifiers are one source (the second upload's map is not stored).
6. Capability tokens are process-local and transient by design; they are not persisted or shared
   across processes. Deterministic nodes still act as the pipeline actor on the analyst's delegated,
   policy-limited grants (rules 6–13); tokens bind the agent-role invocations and any `AGENT_ROLE`
   actor.
7. The unmasking map is stored in plain text in the application database (at-rest encryption is a
   deployment assumption, G.18); no API exposes it.
8. Deletion is synchronous and returns 200 with a receipt (the architecture's table says 202); the
   answer is sent only after commit.
9. The adversarial suite is a functional demonstration on synthetic worlds; it is not a
   certification that every attack fails.

**Deferred:** password authentication and MFA (ADR-009, Phase 0 E.2 — secondary); a retention
schedule (no approved source defines one); field-level encryption; P12.

## 16. P11 roadmap exit-criterion assessment

> *Zero approval-gate bypasses under the adversarial suite; masking test on synthetic financial
> identifiers; replay reconstructs a requirement's and a risk's history.* — `docs/01-analysis.md` §P

`tests/workflow/test_p11_exit_test.py`:

1. **`test_criterion_1_zero_approval_gate_bypasses`** — runs every case of the registry against the
   real approval, lifecycle, baseline, persistence and graph paths (§5, §6) and asserts: at least 28
   attempted, all exercised, **zero bypasses**, all eight gates covered, and the attack vectors
   include model output, injected documents, forged records, agent capabilities, cross-project and
   wrong-role attempts.
2. **`test_criterion_2_synthetic_financial_identifiers_are_masked_before_egress`** — §7.
3. **`test_criterion_3_replay_reconstructs_a_requirements_and_a_risks_history`** — §8.

**Result:** all three pass in the final offline run (SQLite) and in the final full PostgreSQL run:
criterion 1 — 28 attempted, 28 exercised, 0 bypasses across G1–G8; criterion 2 — 13 values of 11
kinds, none reaching the external provider, which was called; criterion 3 — both histories
reconstructed and matched, tampering reported as incomplete.

The database-level half of several guarantees — the purge trigger, the tombstone and
approval-record triggers, and the audit trigger that makes tampering impossible rather than only
detectable — ran in the full PostgreSQL suite (`test_p11_postgres.py` and every earlier phase's
PostgreSQL tests) and passed.

**Assessment: P11 exit — PASSED.** The three exit criteria pass, the full offline and PostgreSQL
suites pass on the final tree with 0 failures, and the quality gates are clean (§12). The
limitations in §15 stand and are not closed by this result; the adversarial suite demonstrates the
controls on synthetic worlds and is not a certification.
