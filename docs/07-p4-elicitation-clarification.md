# ReqPilot — P4 Elicitation & Clarification

**Roadmap phase:** P4 (`docs/01-analysis.md` §P) · **Modules:** M3 (`elicitation_graph`; agent roles #2 Stakeholder
Interaction and #4 Clarification; the clarification mode of `analysis_graph`), M1 (interview console, open-issues
list), M6 (P1 repository integration), M4 (the P3 gateway, unchanged)

**Status: P4 COMPLETE — ROADMAP EXIT PASSED**

*2026-09-22.*

> **The LLM proposes; deterministic code disposes.** In P4 the model proposes three things: an interview question, an
> assessment of an answer, and a clarification question. Deterministic code makes every other decision:
>
> - which topic is asked about;
> - whether a follow-up is allowed;
> - when the interview ends;
> - what is persisted;
> - which lifecycle transition happens;
> - whether a new requirement version exists.
>
> A human answers the questions. An Analyst dismisses a clarification. G1 approval is unchanged: it still needs an
> Analyst and a Compliance Officer.

---

## 1. P4 status

| Level | Status |
|---|---|
| **P4 implementation** | **Complete.** FR-ELI-001..006 and FR-CLR-001..004 are implemented. FR-ELI-007 (secondary) is implemented deterministically |
| **P4 roadmap exit** (analysis §P) | **Passed.** Exit test: *"A scripted persona interview yields a usable requirement set; follow-ups trigger on seeded vague answers; answering a clarification creates a new version."* It is automated as `tests/workflow/test_p4_exit_test.py`, steps A–R (§19), and passes |
| **Offline suite** | 1,151 passed, 0 failed, 165 skipped (PostgreSQL-only), 4 deselected (`llm`) |
| **Live PostgreSQL suite** | 1,316 passed, 0 failed, 0 skipped, 4 deselected (`llm`) |
| **Real-model smoke test** | 1 opt-in test passed, with OpenAI `gpt-5.6-luna` and 18 model calls, on synthetic data only (§20.4) |
| **Quality gates** | ruff format and ruff check clean, mypy clean (155 files), import-linter 5 of 5 contracts kept (§21) |

## 2. Scope

These P4 scope items are implemented:

1. Stakeholder management (the minimum an interview needs).
2. Role-specific interview templates.
3. Interview sessions.
4. Persistent append-only utterances.
5. Deterministic topic coverage tracking.
6. Deterministic topic selection.
7. LLM-proposed interview questions.
8. Human answers, including answers entered on a stakeholder's behalf.
9. LLM answer assessment.
10. Adaptive follow-up questions.
11. Deterministic follow-up bounds.
12. Pause and resume.
13. The LangGraph `elicitation_graph`, with a real `interrupt`.
14. PostgreSQL checkpointing.
15. Clarification question generation.
16. The open-issues list.
17. Human clarification answers.
18. Re-analysis through the P3 path.
19. A new immutable requirement version after clarification.
20. Audit events.
21. A synthetic scripted-persona test and demonstration.
22. This report.

**Out of scope, and not started:**

- P5+: the quality engine, conflict detection, compliance mapping, risk, documents and SDLC;
- P11 hardening and masking;
- FR-CLS-004, ET-06, and E2–E9 results.

A `quality_finding` exists only as the smallest interface the clarification loop needs (§12.1). Nothing detects
defects automatically.

## 3. Source hierarchy

Where the sources disagreed or were silent, precedence was:

1. the Problem Statement;
2. `01-analysis`;
3. `02-architecture`;
4. docs/03, docs/04, docs/05 and docs/06;
5. the P4 implementation brief.

Each resulting decision is recorded where it applies, and collected in §23. `docs/02-architecture.md` was **not
modified**.

## 4. Requirements implemented

| FR | Requirement (01-analysis) | Implementation |
|---|---|---|
| FR-ELI-001 | Role-specific adaptive interviews using role templates | `rules/data/interview_templates.yaml` (v1.0.0, 7 templates). A session uses the template of its stakeholder's role, and a mismatched template is refused. Questions are proposed per turn from the session context, not read from a fixed list |
| FR-ELI-002 | Cover the §7 checklist and track coverage per topic | The 21 §7 topics are in `rules/data/elicitation.yaml`. `domain/coverage.py` tracks status, question and follow-up counts, and the last addressed sequence, per topic |
| FR-ELI-003 | Follow-ups for incomplete, vague or inconsistent answers, bounded per topic | `decide_after_assessment()` allows a follow-up only while `followups_this_topic < max_followups_per_topic` (rule data, value 2). At the bound, the topic closes as `unresolved` |
| FR-ELI-004 | Persist every utterance with speaker, role, timestamp and session | The `utterance` table is append-only (ORM guard + PostgreSQL trigger). It has `(session_id, seq)` unique, a speaker kind and reference, the stakeholder role, `created_at`, and `replies_to_id` |
| FR-ELI-005 | Pause and resume; record answers on a stakeholder's behalf | `/sessions/{id}/pause` and `/resume`. An answer by an Analyst is stored with `speaker_ref` = the stakeholder, `recorded_by` = the Analyst, and `on_behalf = true` |
| FR-ELI-006 | Live topic coverage and remaining topics | `GET /sessions/{id}/coverage` and the interview console |
| FR-ELI-007 (SEC) | Suggest the next stakeholder role from coverage gaps | `suggest_next_role()`: deterministic scoring (required gap = 2, optional gap = 1) that names the gap topics it is based on. `GET /projects/{id}/stakeholder-suggestion`. No LLM |
| FR-CLR-001 | A targeted clarification question bound to a requirement and a defect | Role #4 proposes the question. Code checks the `defect_id` echo, generic phrasing, length, answer shape, duplicates and authority claims. The clarification row binds the version, the finding and the stakeholder with composite FKs |
| FR-CLR-002 | An open-issues list with status, assignee and age | `GET /projects/{id}/clarifications` (open first, oldest first) and `/ui/projects/{id}/clarifications` |
| FR-CLR-003 | Re-run extraction and quality analysis on answer, and record the version change | The answer triggers an `analysis_graph` run in clarification mode (§12.3). The outcome is recorded on the clarification: `new_version`, `no_change` or `failed`, with the resulting version. The "quality analysis" half is P5: no automatic detection exists to re-run (§22) |
| FR-CLR-004 | An Analyst dismisses with a recorded reason | `POST /clarifications/{id}/dismiss`: Analyst only, a non-empty reason, open clarifications only |

The frozen baseline is unchanged:

- 132 FRs, 20 groups, 117/13/2 (MVP/secondary/out of scope) and 99/33 (problem-statement/project-derived);
- 13 roles, 12 modules, P0–P12 and G1–G8;
- 14 lifecycle states, with no `CONFLICTED` state.

No FR was added.

## 5. Requirements deferred

- **The quality-analysis half of FR-CLR-003.** Re-analysis re-runs extraction and classification (the P3 path). There
  is no P5 quality engine to re-run. An open finding keeps blocking `VALIDATED` until P5 (or a human) resolves it.
- **Automatic defect detection.** Findings are recorded by an Analyst through the minimal P4 interface. The P5
  detectors will write the same table.
- **Masking (P11).** Only `SYNTHETIC` sessions may reach an external provider. The gateway's egress guard refuses
  anything else, and the session stalls with `egress_refused`.
- **Checkpoint retention pruning** (C.7, "pruned after N days"): the setting exists, pruning is P11 hardening.

## 6. Architecture used

- **Graph.** LangGraph 1.2.11, with `langgraph.types.interrupt` and `Command(resume=…)`.
- **Checkpointing.** langgraph-checkpoint-postgres 3.1.2, with a `PostgresSaver` on the application database
  (ADR-003). One thread per `graph_run`, with `thread_id` derived from the run id (C.7).
- **LLM access.** Every model call goes through the P3 `LLMGateway`: versioned prompts, trust-class fencing, egress
  guards, one bounded repair, and provenance on the `agent_run`.
- **Authorisation.** Every service call goes through `policy.require()` (rule 7 adds the P4 actions). Project isolation
  answers 404 without disclosing existence.
- **Audit.** Every event goes through the existing hash-chained `AuditService`. There is no second audit mechanism.

**State tiers (C.7 content rule).**

| Tier | What | Where |
|---|---|---|
| Persisted, authoritative | Session status, coverage, the current topic, the pending question id, `followups_this_topic`, the pending issue, the unassessed answer id, counters, and the stall reason | `interview_session` row |
| Persisted, append-only | Every question and answer | `utterance` |
| Checkpointed | Ids and counters only (`ElicitationState`: `session_id`, `run_id`, route markers, and a last-write-wins `failure`) | LangGraph checkpoint |
| Transient | Prompt assembly, the model response before validation | Memory, per request |

**Decision: the durable session row is the source of truth.** The checkpoint is a resumption aid. `load_session`
re-derives the resume point from the row on every entry:

1. the session is not `ACTIVE` → `stall`;
2. there is an unassessed answer → `assess_answer`;
3. a question is pending → `await_answer`;
4. the current topic is in progress → `generate_question`;
5. otherwise → `select_next_topic`.

As a result, a lost or stale checkpoint degrades to a safe restart and never to a duplicated or skipped question
(tested on SQLite and on PostgreSQL).

## 7. Elicitation graph

`graph/graphs/elicitation.py`, `GRAPH_NAME = "elicitation_graph"`:

```
START → load_session ─┬→ select_next_topic ─┬→ generate_question → await_answer ⟂ interrupt
                      ├→ generate_question  └→ end_interview → END
                      ├→ await_answer
                      ├→ assess_answer
                      └→ end_interview / stall → END

await_answer (resumed) → record_utterance → assess_answer ─┬→ generate_question   (follow-up; bound allowed it)
                                                           ├→ select_next_topic   (covered or unresolved; advance)
                                                           └→ stall
any node failure → stall → END
```

What each node does, and who decides:

| Node | Decides | Who |
|---|---|---|
| `load_session` | The resume point | Code, from the durable row |
| `select_next_topic` | The next topic: the in-progress topic first, else the minimum of (not required, priority, id) | Code (`domain/coverage.py`) |
| `generate_question` | The question text | Model proposes. Code validates the topic echo, the follow-up flag, the length, duplicates, authority claims and keyword relevance. It allows at most 2 attempts, then stalls with `question_rejected` |
| `await_answer` | Nothing: a real `interrupt()` | — |
| `record_utterance` | Links the persisted answer and records coverage | Code |
| `assess_answer` | `complete` / `vague` / `incomplete` / `inconsistent`, and the issue | Model proposes the status. Code checks the topic echo and requires an issue for non-complete answers |
| router (`decide_after_assessment`) | Follow-up, advance, or `unresolved` at the bound | Code |
| `end_interview` / `stall` | Completion, or a visible stall with a reason code | Code |

**Interrupt and resume (C.7, architecture M.2 principle).**

- **The answer is persisted before the graph resumes.** It is an append-only `utterance`.
- **The resume value is built by the server,** never by the caller: `{"answer_utterance_id", "question_utterance_id"}`.
- **`await_answer` accepts exactly those keys.** The question id must match the session's pending question. Anything
  else — extra keys such as `approve`, or a different question — stalls with `invalid_resume` and records nothing.
- **Before resuming, the runner checks the thread.** It must be suspended at `await_answer` with the matching pending
  id. If the thread is lost or stale, the runner invokes a fresh thread from START and `load_session` recovers from
  the row.

## 8. Interview session model

Migration `0006_p4_elicitation` adds these tables. Each has a PostgreSQL trigger as its second layer of protection:

| Table | Key fields | Mutability |
|---|---|---|
| `stakeholder` | project_id, name, stakeholder_role, authority_level, user_id (optional link to a project member holding Role.STAKEHOLDER) | Mutable. Never deleted directly |
| `interview_session` | project_id, stakeholder_id (composite FK with the project), kind (`INTERVIEW` / `CLARIFICATION`), template_id and version, sensitivity, status (`ACTIVE` / `PAUSED` / `COMPLETED` / `STALLED`), topic_coverage JSON, current_topic, pending_question_id, followups_this_topic, pending_issue, unassessed_answer_id, graph_run_id, stall_reason, counters, and timestamps | Progress is mutable. Identity (project, stakeholder, kind, template) is immutable. Never deleted directly |
| `utterance` | project_id + session_id (composite FK), seq (unique per session), speaker_kind (`SYSTEM` / `STAKEHOLDER`), speaker_ref, stakeholder_role, text, topic_id, is_followup, replies_to_id, recorded_by, on_behalf, and created_at | **Append-only** |
| `quality_finding` | project_id, requirement_version_id, finding_type, severity, rationale, span_quote, detector, status | Content immutable. Status only. Never deleted |
| `clarification` | project_id, requirement_version_id + quality_finding_id (composite FK), asked_of_stakeholder_id (composite FK with the project), session_id, question and answer utterance ids, question, expected_answer_shape, status, assignee, resolution fields, and re-analysis fields | Question and binding immutable. Resolved once. Never deleted. One `OPEN` clarification per finding (partial unique index) |

A session belongs to one project, and its stakeholder belongs to the same project (composite FK). An utterance's
project matches its session's project (composite FK). A clarification's finding belongs to the same version (composite
FK). Completed and paused sessions refuse answers. Dismissed or answered clarifications refuse answers.

**Session state transitions:**

- `ACTIVE` → `PAUSED` (Analyst);
- `PAUSED` → `ACTIVE` (Analyst resume);
- `ACTIVE` → `STALLED` (a failure, with a reason code);
- `STALLED` → `ACTIVE` (Analyst resume, which retries);
- `ACTIVE` → `COMPLETED` (coverage complete).

## 9. Stakeholder templates

`interview_templates.yaml` v1.0.0 has seven templates, one per case-study role: `product_owner`, `customer`,
`operations`, `security`, `compliance`, `risk` and `technology`. Each template lists its applicable topics, each with a
priority, a `required` flag and an `expected_answer_shape`. Together the templates cover all 21 §7 topics. This is
checked at load, along with unknown topics, duplicates, and the requirement for at least one required topic.

`elicitation.yaml` v1.0.0 holds:

- the topic taxonomy (21 ids, each with keywords);
- the interview bounds: `max_followups_per_topic: 2`, `recent_turns: 6`, `max_question_chars: 600`,
  `min_question_words: 4`, `max_answer_chars: 4000`, `max_question_attempts: 2`;
- the clarification bounds: minimum words, a generic-question list, and attempts;
- the authority phrases that a proposed question may not contain.

The follow-up bound is an implementation parameter, not a research metric. No new numerical target was invented.

## 10. Topic coverage mechanism

`domain/coverage.py` is pure and deterministic. The model never writes coverage.

- **Topic statuses:** `not_started`, then `in_progress`, then `covered` or `unresolved`.
- **Per topic it records:** questions, follow-ups, the last addressed sequence, and the last assessment.
- **Selection** is independent of dict order (tested).
- **The bound.** With 0 follow-ups so far, a follow-up is allowed. At the maximum, no further follow-up is asked and
  the topic closes as `unresolved`. The counter is on the durable row, so a resume or restart cannot reset it (tested
  across pause/resume and a PostgreSQL restart). A model that always answers `vague` cannot extend it: every topic ends
  at exactly 1 + max questions (security test).
- **The question-generation context** includes the coverage summary. The role receives it but cannot change it.

## 11. LLM contracts

| Role | Prompt (versioned, hash-locked in `registry.yaml`) | Input (least privilege) | Output contract (`extra="forbid"`) |
|---|---|---|---|
| #2 Stakeholder Interaction: question | `stakeholder_interview_question@1.0.0` | The template role, the topic id and label, required/priority, the expected answer shape, the coverage summary, the follow-up depth, recent turns (6, fenced as project content), and the follow-up issue (fenced as model output) | `QuestionProposal{question, topic_id, is_followup, rationale?}` |
| #2 Stakeholder Interaction: assessment | `stakeholder_answer_assessment@1.0.0` | The topic, its expected shape, the question, and the answer (fenced as project content) | `AnswerAssessment{topic_id, status ∈ {complete, vague, incomplete, inconsistent}, rationale?, detected_issue?}` |
| #4 Clarification | `clarification_question@1.0.0` | The target requirement statement, the defect (id, type, rationale, span), prior clarification questions, and the stakeholder role | `ClarificationProposal{question, expected_answer_shape, defect_id, rationale?}` |

Each prompt records its role, version, contract version, hash, trust class and purpose.

Each prompt tells the model to:

- use only the supplied context;
- invent no facts;
- make no compliance, legal or approval decision;
- not change coverage, routing or lifecycle;
- return structured output only.

Topic metadata is passed as regex-validated parameters. Stakeholder text reaches the model **only** as fenced,
labelled data blocks, never interpolated into instructions (tested with an injection utterance). Schema-invalid
output gets the gateway's one repair. A second failure stalls the session and does not advance it.

None of the roles has any access to:

- the requirement repository;
- approvals;
- risk;
- compliance mappings;
- other projects;
- the KB.

## 12. Clarification loop

### 12.1 Findings (the minimal P4 interface)

An Analyst records a `quality_finding` on a requirement version. It has a type, a severity and a rationale. An
optional span must be words of the statement. Findings are audited. An open finding counts in the P1 `build_context`,
so it blocks `VALIDATED` (the existing G.4 guard). P5 will populate the same table automatically.

### 12.2 Raising (FR-CLR-001)

`ClarificationRunner.raise_for_finding` runs as an `analysis_graph` run with the node `generate_clarifications`. It:

1. requires the finding to be open, and its version to be current and in a raisable state (`RAISE_PATHS`);
2. has role #4 propose a question, with up to 2 attempts;
3. validates the proposal.

A generic question ("Can you clarify this?") is never asked. The run fails visibly, and nothing is created.

On success, in one transaction, it:

- creates a `CLARIFICATION`-kind session, with the system question utterance at seq 1;
- creates the `clarification` row, with the assignee = the raising Analyst;
- has the Analyst transition the version to `CLARIFICATION_REQUIRED`, via `ANALYZED` where needed.

### 12.3 Answering and re-analysis (FR-CLR-003)

1. **The answer is persisted first.** It is a `STAKEHOLDER` utterance replying to the question. The clarification
   becomes `ANSWERED`. An answer is accepted once. An Analyst may answer on the stakeholder's behalf.
2. **Re-analysis starts automatically.** It is an `analysis_graph` run in clarification mode, started with trigger
   `CLARIFICATION_ANSWER`. That trigger is a whitelisted `TRIGGERING_ACTIONS` entry, so a stakeholder's answer can
   start the run without holding `RUN_START`. A manual retry does need `RUN_START` (Analyst).
3. **The scope** is the predecessor version's own source segments, plus the answer utterance. The question is attached
   as non-quotable context.
4. **The path** is `extract_requirements` (P3), then `validate_extraction` (P3), then the new `persist_revision` node,
   then `classify` (P3).
5. **`persist_revision`** moves the version `CLARIFICATION_REQUIRED` → `CLARIFIED`. This is a pipeline transition,
   guarded by "an answer is present", and `CLARIFIED` was added to `AUTOMATED_TRANSITION_TARGETS`. It then calls
   `ExtractionService.apply_revision`.
6. **`apply_revision`** picks the accepted candidate closest to the predecessor by token Jaccard.
   - **Identical statement:** `no_change`. No version is created, and a `CONFIRMS_CURRENT_VERSION` finding code is
     recorded.
   - **Otherwise:** P1 `create_version` with the change reason, `supersedes` the predecessor, and source refs = the
     predecessor's refs ∪ the new evidence ∪ a provenance ref to the clarification answer utterance. The new version is
     `EXTRACTED`, and it is then classified by the P3 `classify` node.
7. **On failure** (bad model output, validation), the clarification stays `ANSWERED` with `reanalysis_status = failed`,
   and no version is fabricated. An Analyst may retry.

### 12.4 Dismissal (FR-CLR-004)

Analyst only, with a non-empty reason, and only while open. Dismissing does not approve, does not fix, and does not
move the version. The finding stays open.

## 13. Versioning behaviour

- **Versions are never edited in place.** They stay immutable (P1 ORM guard + PostgreSQL trigger). A clarification
  that changes the content creates version n+1 through P1 `create_version`. The exit test checks the predecessor with
  `assert_version_unmodified`: same statement and same content hash.
- **Traceability is kept.** The predecessor's history and source refs remain. The new version's refs include the
  original stakeholder utterance and the clarification answer.
- **The new version is not approved.** It starts its own lifecycle at `EXTRACTED` and is classified to `CLASSIFIED`.
  No approval task, no baseline, and no G1/G7 bypass exist anywhere in P4.

## 14. HITL behaviour

- **A human answers every question.** The persona in tests is a scripted *human* stand-in, not a model.
- **Humans decide:** only a human can create stakeholders and sessions, answer, pause, resume, record findings, raise,
  answer and dismiss (policy rule 7, human-only).
- **Pipeline actions are limited:** the pipeline acts only for extraction and classification, and for the single
  guarded `CLARIFIED` transition.
- **Stalls are visible.** A stall shows its reason code in the UI and API. An Analyst resumes it, which retries it.
- **Answering approves nothing.** Every turn and clarification response carries the "approves nothing" notice. G1
  still requires an Analyst **and** a Compliance Officer (unchanged P1 code, still tested).

## 15. Authorisation

Policy rule 7 adds these actions:

- `STAKEHOLDER_CREATE` and `STAKEHOLDER_READ`;
- `SESSION_CREATE`, `SESSION_READ`, `SESSION_ANSWER` and `SESSION_MANAGE`;
- `UTTERANCE_RECORD`;
- `QUALITY_FINDING_CREATE` and `QUALITY_FINDING_READ`;
- `CLARIFICATION_RAISE`, `CLARIFICATION_READ`, `CLARIFICATION_ANSWER` and `CLARIFICATION_DISMISS`.

| Actor | May |
|---|---|
| Analyst | Everything above. Answers on a stakeholder's behalf (recorded as such) |
| Stakeholder | Read and answer **only their own** sessions and clarifications (`stakeholder.user_id` link). Anything else is 404 |
| Auditor | Read-only: sessions, utterances, findings and clarifications |
| Agent / pipeline | No P4 human action. Re-analysis runs are started as a trigger of a human answer |
| Another project's member | Nothing. 404, with no existence disclosure |

Every endpoint authenticates through the existing actor dependency, enforces the project scope and `require()`, and
uses typed, `extra="forbid"` request models. No request model can carry a topic, coverage, a follow-up count, a route,
a state or an approval (tested: 422).

## 16. Audit

Every event goes through the existing hash-chained `AuditService`. The payloads carry ids, sequence numbers, topic ids,
flags and reason codes, **never** answer text (tested).

| Required event | Audit event |
|---|---|
| Stakeholder created | `STAKEHOLDER_CREATED` |
| Session created / interview started | `INTERVIEW_SESSION_CREATED` + `RUN_STARTED` (`elicitation_graph`) |
| Question generated / follow-up generated | `QUESTION_GENERATED` (`is_followup` flag, `agent_run_id`) |
| Utterance recorded | `UTTERANCE_RECORDED` (with `recorded_by` and `on_behalf`) |
| Paused / resumed | `INTERVIEW_SESSION_PAUSED` / `INTERVIEW_SESSION_RESUMED` (+ `RUN_SUSPENDED` / `RUN_RESUMED` per turn) |
| Answer assessed | `ANSWER_ASSESSED` (status and topic) |
| Interview completed | `INTERVIEW_SESSION_COMPLETED` |
| Stalled | `INTERVIEW_SESSION_STALLED` (reason code) |
| Finding recorded | `QUALITY_FINDING_RAISED` |
| Clarification raised / answered / dismissed | `CLARIFICATION_RAISED` / `CLARIFICATION_ANSWERED` / `CLARIFICATION_DISMISSED` |
| Version created due to clarification | `CLARIFICATION_REANALYSED` (outcome and resulting version) + the P1 `REQUIREMENT_VERSION_CREATED` + `STATE_TRANSITION` |

A diagnostic scripted interview, extracted with P3, emitted 1 `STAKEHOLDER_CREATED`, 1 `INTERVIEW_SESSION_CREATED`, 14
each of `QUESTION_GENERATED`, `RUN_SUSPENDED`, `UTTERANCE_RECORDED`, `RUN_RESUMED` and `ANSWER_ASSESSED`, and 1
`INTERVIEW_SESSION_COMPLETED`. The project chain verifies in the exit test (step Q).

## 17. Security boundaries

| Boundary | Enforcement | Test |
|---|---|---|
| Stakeholder text is data | Fenced `project_content` blocks only | An injection answer changes no topic, bound, state or approval, and appears only in the fenced block |
| The model cannot choose the topic | The topic echo must match the selected topic, else the proposal is rejected and coverage is unchanged | `test_the_model_cannot_choose_the_topic` |
| The model cannot extend the bound | The router uses durable counters | An always-`vague` model ends every topic at 1 + max |
| Authority fields are impossible | `extra="forbid"` contracts | `approved: true` in an assessment → the session stalls without advancing |
| A forged resume is refused | The resume keys and the pending id are validated | Extra keys or a wrong question id → `invalid_resume` stall, nothing recorded |
| Egress | The gateway guard: only `SYNTHETIC` may leave the machine (no masking until P11) | An `UNCLASSIFIED` session with an external provider → `egress_refused` before any provider call |
| Secrets | The gateway's secret scan on outbound content | A configured secret in an answer → refused before any call |
| Checkpoints hold no content | Ids and counters only | Memory and PostgreSQL checkpoints contain no question or answer text |
| Isolation | `require()` + composite FKs | Cross-project read, answer and dismiss → 404. A stakeholder answering another stakeholder's session → 404. An utterance pointing at another project's session → FK violation (PostgreSQL) |

This is **not** P11. The full adversarial suite, masking and retention pruning remain P11 work.

## 18. Synthetic persona

`data/dev/personas/p4_product_owner_synthetic.yaml`: **Dana Reyes (fictional)**, product owner at **Harbourside
Lending (fictional)**, `decision_maker`, for a fictional personal-loan portal. Every answer is marked synthetic.

It seeds:

- clear answers;
- `inputs`: incomplete, then complete after one follow-up;
- `business_rules`: vague three times, so it reaches the bound and ends `unresolved`;
- `performance`: vague, then complete;
- an injection line under `exceptional_conditions`;
- the ambiguous `outputs` answer ("decision letter quickly");
- a 7-item extraction map;
- a clarification block: the finding span "quickly", the question, the answer "…within one working day of the
  underwriter's decision", and the expected revised statement.

The file has no real names, identifiers, credentials or financial data.

`tests/p4_helpers.ScriptedPersonaModel` answers the gateway's prompts from this file. Every response still passes
through the real gateway, the schema, deterministic validation, the graph and the database.

## 19. End-to-end test

`tests/workflow/test_p4_exit_test.py::test_p4_exit_scripted_persona_interview_to_clarified_new_version`:

| Step | Asserted |
|---|---|
| A | The interview starts from the product-owner template |
| B | The first question is on `business_objectives` (the first required topic) |
| C | Answers are recorded as `STAKEHOLDER` utterances replying to their questions |
| D | The seeded incomplete (`inputs`) and vague (`performance`, `business_rules`) answers are assessed as such |
| E | A follow-up is asked on the same topic, with the counter incremented |
| F | The follow-up addresses the assessed issue (and the issue reaches the prompt: `test_the_follow_up_is_targeted_to_the_assessed_issue`) |
| G | `business_rules`: 1 + `max_followups` questions, then `unresolved`, with no further follow-up |
| H | The interview continues to the next topic |
| I | Coverage is complete: every applicable topic is `covered` or `unresolved` |
| J | P3 extraction over the session → a usable requirement set: 7 accepted, `CLASSIFIED`, with utterance source refs |
| K | A finding on "decision letter quickly" → a targeted clarification. The version is `CLARIFICATION_REQUIRED` |
| L | The stakeholder answers |
| M | Re-analysis → version 2 (`new_version`), with the statement naming "one working day" |
| N | Version 1 is unchanged (`assert_version_unmodified`) and remains in history |
| O | No approval task. Version 2 is `CLASSIFIED`, not approved and not baselined |
| P | Version 2's source refs = the original utterance + the clarification answer (provenance) |
| Q | The full audit trail is present, and `verify_project_chain` passes |
| R | Another project's stakeholder cannot see or answer anything (`ProjectIsolationError`) |

The same flow runs over HTTP (`test_p4_api_and_ui.py`) and, for the interview, on the PostgreSQL checkpointer with a
simulated restart (`test_p4_postgres.py`).

## 20. Test results

### 20.1 P4 tests (129 + 1 opt-in)

| File | Kind | Tests |
|---|---|---|
| `tests/unit/test_coverage_tracker.py` | unit | 14 |
| `tests/unit/test_elicitation_rules_and_validation.py` | unit | 28 |
| `tests/unit/test_p4_policy_and_routers.py` | unit | 21 |
| `tests/workflow/test_p4_elicitation_graph.py` | workflow | 5 |
| `tests/workflow/test_p4_exit_test.py` | workflow (exit) | 2 |
| `tests/integration/test_p4_sessions.py` | integration | 15 |
| `tests/integration/test_p4_clarification.py` | integration | 15 |
| `tests/integration/test_p4_api_and_ui.py` | integration (HTTP + UI) | 10 |
| `tests/security/test_p4_security.py` | security | 10 |
| `tests/integration/test_p4_postgres.py` | PostgreSQL only | 9 |
| `tests/llm/test_p4_openai_live.py` | opt-in `llm` | 1 |

The phase guards were updated to name the P4 tables as present rather than future:

- `test_migrations`;
- `test_p1_persistence` (its future-phase table list);
- `test_postgres_specific` (the P4 and checkpoint tables are permitted);
- `test_prompt_registry` (five prompts);
- `test_api_health` (the P4 prefixes are now present).

No existing assertion was weakened or deleted.

### 20.2 Offline suite (default: `pytest -q`, network blocked by proxy)

1,320 collected: **1,151 passed, 0 failed, 165 skipped** (PostgreSQL-only, no `REQPILOT_TEST_DATABASE_URL`), **4
deselected** (`llm`). One third-party `DeprecationWarning` (starlette `TestClient`) was already present before P4.

### 20.3 Live PostgreSQL suite

PostgreSQL 16.2 + pgvector 0.6.2 (local pgserver), on a fresh `reqpilot_test` migrated to head by the suite.

1,320 collected: **1,316 passed, 0 failed, 0 skipped, 4 deselected** (`llm`).

This includes the 9 P4 PostgreSQL tests:

- the checkpoint tables;
- the persona interview on `PostgresSaver`;
- a restart mid-interview that resumes the same pending question;
- the raw-SQL trigger attacks on utterance, session, stakeholder, finding and clarification;
- the composite FK;
- the partial unique index.

### 20.4 Opt-in real-model smoke test

`pytest -m llm tests/llm/test_p4_openai_live.py -s` was run once. Provider OpenAI, model `gpt-5.6-luna` (from the
developer's untracked configuration; the key was never printed or stored).

**1 passed**, with **18 model calls**:

- 9 stakeholder-interaction calls (5 questions and 4 assessments);
- 2 extraction calls (the session, and the re-analysis);
- 6 classification calls;
- 1 clarification call.

Observed:

- 4 answered turns;
- a real follow-up, twice on `current_workflow`, within the bound;
- 6 requirement versions extracted;
- a targeted clarification ("What measurable acceptance criterion should verify that…");
- re-analysis `no_change`.

`no_change` is correct here: the smoke test's canned answer does not bear on the chosen requirement, so the pipeline
confirmed the current version rather than inventing one.

The data was the synthetic persona only, in a `SYNTHETIC` session. This is a smoke test of the integration, not a
quality measurement.

### 20.5 Diagnostics (not research metrics)

These are from the scripted persona run, and are engineering diagnostics only:

- the template has 10 applicable topics;
- 14 questions, 4 of them follow-ups (on `inputs`, `performance`, and 2 on `business_rules`);
- 9 topics covered and 1 unresolved;
- 14 question calls and 14 assessment calls, 1 extraction call and 7 classification calls;
- 7 requirements accepted, 0 merged and 0 rejected.

One scripted persona is not a benchmark. No E-metric is claimed.

## 21. Quality-gate results

| Gate | Result |
|---|---|
| `ruff format --check .` | 254 files already formatted |
| `ruff check .` | All checks passed |
| `mypy` | Success: no issues found in 155 source files |
| `lint-imports` | 5 contracts kept, 0 broken |
| No secrets | No key in any tracked-to-be file. The only key-shaped string is the pre-existing `FAKE_KEY` in `tests/unit/test_openai_provider.py` |
| `.env` ignored | `.gitignore` lists `.env` and `.env.*` (checked by reading the file; no Git command was run) |
| Default tests offline | Run with the network proxied to a closed port. `tests/hermetic.py` removes `LLM_*` variables and disables `.env` |
| E1 benchmark untouched | `load_gold_set(data/gold/e1_synthetic_v1)` verifies, manifest sha256 `6bce9173…0f3a870` |

## 22. Known limitations

- **No automatic quality analysis.** Findings are recorded by hand until P5. Re-analysis re-runs extraction and
  classification only.
- **Revision matching is lexical.** Re-analysis takes the accepted candidate closest to the predecessor by token
  Jaccard, even when that candidate is lexically distant. The other candidates are rejected with
  `REVISION_NOT_SELECTED`. The outcome is `failed` only when no candidate survives validation. The resulting version is
  never approved, so a poor revision is caught at human review, not silently accepted.
- **Keyword relevance is lenient.** It is a lexical check with a follow-up exemption, not semantic. The primary
  safeguards are the topic echo and the human answering.
- **Egress is limited to synthetic data.** Only `SYNTHETIC` sessions may use an external model until masking (P11).
- **Checkpoints are not pruned yet.** Retention pruning is P11.
- **The UI is minimal.** It is server-rendered and has no live push. A page refresh shows progress.
- **One persona.** The scripted persona is a single product owner. Multi-stakeholder coverage is exercised only through
  FR-ELI-007's deterministic suggestion.
- **The real-model check is a smoke test.** It is a single run of 18 calls and says nothing about question quality.

## 23. Deviations from approved architecture

None changes an approved ADR, the baseline, or `docs/02-architecture.md`. Each is the smallest reading consistent with
the sources.

1. **The clarification wait is database status, not an `analysis_graph` interrupt.** An open clarification waits as
   `clarification.status = OPEN`. The answer triggers a new `analysis_graph` run in clarification mode.
   - *Reason:* a clarification can wait days or be dismissed. Holding a graph thread open for it would couple
     checkpoint retention to issue age.
   - The C.7 principle ("the resume value is not supplied by the caller") is preserved: the re-analysis reads the
     persisted answer.
2. **The analysis-run scope is `session_ids`, not `utterance_ids`.**
   - `POST /analysis-runs` accepts `session_ids` (the stakeholder answers of the named interview sessions).
   - *Reason:* a session is the unit a human finishes. Hand-picking utterances would let a caller drop the questions
     that give answers their context.
3. **The `stakeholder.user_id` link.** This column is not in G.3. It is required to let "the stakeholder" answer only
   their own session (brief §15 and §27).
4. **The `CLARIFICATION` session kind.** A clarification's question and answer are utterances in a session of kind
   `CLARIFICATION`. This keeps a single append-only utterance store (no second conversation table), and utterance
   provenance works for clarification answers unchanged.
5. **Triggered runs.** `RunRecorder.start(trigger=CLARIFICATION_ANSWER)` lets a stakeholder's answer start the
   re-analysis run without `RUN_START`. The trigger list is a closed whitelist, and a retry needs `RUN_START`.
6. **P1 additions (additive).**
   - `CLARIFIED` joined the pipeline's `AUTOMATED_TRANSITION_TARGETS`, guarded by "an answer is present".
   - `build_context` now counts open quality findings, which block `VALIDATED` per G.4, and whether a clarification
     answer is present.
7. **The minimal `quality_finding`.** It is recorded by an Analyst (`detector = HUMAN`) rather than a P5 detector. It is
   the brief's "smallest clean interface".
8. **Extra endpoints** beyond the architecture's API table, all additive:
   - `GET /projects/{id}/stakeholders` and `GET /projects/{id}/sessions`;
   - `GET /sessions/{id}`, plus `/utterances`, `/pause` and `/resume` under it;
   - `GET /projects/{id}/stakeholder-suggestion`;
   - `POST` and `GET /requirement-versions/{id}/quality-findings`;
   - `POST /quality-findings/{id}/clarifications`;
   - `GET /clarifications/{id}` and `POST /clarifications/{id}/reanalyse`.
9. **Checkpointer builder.** `checkpointer_scope()` opens a `PostgresSaver` on its own autocommit connection, which
   creates the `checkpoint*` tables through the library's `setup()`, not through an Alembic migration. `memory`
   remains the offline default.
10. **FR-ELI-007 is implemented,** although it is secondary: it was deterministic and small.

## 24. P4 exit criteria

| Criterion | Result |
|---|---|
| A scripted persona interview yields a usable requirement set | **Met.** 7 requirements, classified, with utterance provenance (steps A–J) |
| Follow-ups trigger on seeded vague answers | **Met.** Targeted, counted and bounded (steps D–G) |
| Answering a clarification creates a new version | **Met.** Version 2, with the predecessor immutable and not approved (steps K–P) |
| Quality, security and regression tests pass | **Met** (§20, §21) |

**P4 COMPLETE — ROADMAP EXIT PASSED.**

## 25. P5 readiness

P5 inherits these interfaces:

- **`quality_finding`** (table, service, audit, and the open-finding guard on `VALIDATED`): the P5 detectors write it
  with `detector` set.
- **`ClarificationRunner.raise_for_finding`**: P5 can raise clarifications for its findings unchanged.
- **The clarification-mode `analysis_graph`**, extended by the P5 quality nodes.
- **Utterance-sourced requirements**, with provenance that resolves.

Nothing of P5 was started.

---

## P4 closure audit

| # | Check | Result |
|---|---|---|
| 1 | P4 requirements in Phase 0 match the implementation | Yes: FR-ELI-001..007 and FR-CLR-001..004 (§4). The FR-CLR-003 quality half is deferred to P5 (§5) |
| 2 | The architecture graph matches the implementation | Yes: C.4 node sequence, a real interrupt, and a deterministic router (§7) |
| 3 | API paths match the implementation | Yes: all 7 architecture P4 paths exist, and the extra endpoints are additive (§23.8) |
| 4 | The data model matches the implementation | Yes: G.3 tables, plus the documented additions (§8, §23) |
| 5 | No duplicate session or utterance systems | Yes: one `utterance` store. Clarifications use a session kind |
| 6 | P1 remains the system of record | Yes: versions only through P1 `create_version` and `transition` |
| 7 | P2 remains unchanged | Yes: no P2 file modified |
| 8 | P3 is unchanged except for intentional integration points | Yes: `SegmentView.source_kind` and `context`, utterance source refs, `apply_revision`, the `persist_revision` node, the `session_ids` scope, and non-document refs skipped in E1 predictions. The P3 tests are all green |
| 9 | No future-phase implementation has leaked into P4 | Yes: no detectors, conflicts, compliance, risk, documents or SDLC |
| 10 | No `CONFLICTED` state | Yes: still 14 states |
| 11 | G1 still requires an Analyst + a Compliance Officer | Yes: P1 code unchanged, and its tests pass |
| 12 | The LLM cannot control graph topology | Yes: routers read durable counters and validated statuses only |
| 13 | The LLM cannot approve or baseline | Yes: there is no field for it, and a forged resume stalls |
| 14 | Follow-up depth is deterministic | Yes (§10, security test) |
| 15 | Topic coverage is deterministic | Yes (`domain/coverage.py`) |
| 16 | Utterances are append-only | Yes: ORM guard + PostgreSQL trigger (tested with raw SQL) |
| 17 | Requirement versions remain immutable | Yes (step N; P1 trigger) |
| 18 | A clarification answer produces a new version | Yes (step M) |
| 19 | The audit chain remains valid | Yes (step Q) |
| 20 | Cross-project access is denied | Yes (step R, API, security, PostgreSQL FK) |
| 21 | Synthetic data only | Yes: fictional persona, and the `SYNTHETIC` sensitivity enforced for egress |
| 22 | No secret leakage | Yes: audit payloads, checkpoints and output checked. The key was never printed |
| 23 | The E1 benchmark remains untouched | Yes: manifest sha256 `6bce9173…` verifies |
| 24 | No Git operations were performed | Yes: no Git command was run during P4 |

---

## Final P4 Status

- **Implementation status:** complete (§2, §4).
- **Roadmap exit status:** **P4 COMPLETE — ROADMAP EXIT PASSED.**
- **Exact exit-test result:** `tests/workflow/test_p4_exit_test.py` — 2 passed (the A–R acceptance test and the
  targeted-follow-up test), in the offline and PostgreSQL runs.
- **Tests:**
  - offline: 1,151 passed, 0 failed, 165 skipped, 4 deselected;
  - live PostgreSQL: 1,316 passed, 0 failed, 0 skipped, 4 deselected.
- **Quality gates:** ruff format and check clean, mypy clean, and import-linter 5/5 kept.
- **Real-model smoke test:** 1 passed, `gpt-5.6-luna`, 18 calls, synthetic only.
- **Database verification:** migration `0006_p4_elicitation` is verified as follows:
  - up/down on SQLite;
  - upgrade on PostgreSQL 16;
  - the triggers, composite FKs and partial unique index attacked with raw SQL;
  - the checkpoint tables present.
- **Security verification:** §17. Every row is tested.
- **Audit verification:** §16. The chain verifies, and the payloads contain no answer text.
- **Known limitations:** §22.
- **Deviations:** §23. None alters an ADR, the baseline, or docs/02.
- **P5 readiness:** §25.

### Files Changed

**New**

- The migration: `alembic/versions/0006_p4_elicitation.py`.
- Domain:
  - `src/reqpilot/domain/coverage.py`;
  - `src/reqpilot/domain/models/elicitation.py`.
- Rules:
  - `src/reqpilot/rules/elicitation.py`;
  - `src/reqpilot/rules/data/elicitation.yaml`;
  - `src/reqpilot/rules/data/interview_templates.yaml`.
- Agents:
  - `src/reqpilot/agents/contracts/elicitation.py` and `clarification.py`;
  - `src/reqpilot/agents/roles/interview.py` and `clarification.py`;
  - `src/reqpilot/agents/validation/elicitation.py` and `clarification.py`.
- Prompts:
  - `src/reqpilot/llm/prompts/stakeholder_interview_question-1.0.0.yaml`;
  - `stakeholder_answer_assessment-1.0.0.yaml`;
  - `clarification_question-1.0.0.yaml`.
- Repositories: `src/reqpilot/repositories/elicitation.py`.
- Services:
  - `src/reqpilot/services/elicitation/` (`stakeholders`, `sessions`, `provenance`);
  - `src/reqpilot/services/clarification/` (`findings`, `service`).
- Graph:
  - `src/reqpilot/graph/nodes/elicitation.py`;
  - `src/reqpilot/graph/graphs/elicitation.py`;
  - `src/reqpilot/graph/elicitation_runner.py`;
  - `src/reqpilot/graph/clarification_runner.py`.
- API:
  - `src/reqpilot/api/elicitation_schemas.py`;
  - `src/reqpilot/api/routes/elicitation.py`.
- Web:
  - `src/reqpilot/web/elicitation.py`;
  - `templates/interviews.html`, `interview.html` and `clarifications.html`.
- Data: `data/dev/personas/p4_product_owner_synthetic.yaml`.
- Tests:
  - `tests/p4_helpers.py`;
  - the 10 P4 test files and the opt-in live test (§20.1).
- This document.

**Modified (additive integration)**

- `domain/enums.py`, `domain/errors.py`, `domain/models/__init__.py`, `domain/policy/policy.py` (rule 7),
  `domain/source_spans.py` and `domain/proposals.py`.
- The agent contracts, roles and validation package exports: `contracts/extraction.py` (`SegmentView.source_kind` and
  `context`) and `roles/extraction.py` (question context rendering).
- `llm/prompts/registry.yaml` (three entries, hash-locked).
- `repositories/extraction.py`.
- `services/requirements/service.py`, `services/extraction/pipeline.py` (`apply_revision`),
  `services/extraction/runs.py` (triggers, suspend/resume/stall), `services/extraction/__init__.py` and
  `services/evaluation/extraction_predictions.py`.
- `graph/state.py`, `graph/routers.py`, `graph/builder.py` (`checkpointer_scope`), `graph/nodes/analysis.py`,
  `graph/graphs/analysis.py` and `graph/runner.py`.
- `api/app.py`, `api/dependencies.py`, `api/errors.py`, `api/extraction_schemas.py` and `api/routes/extraction.py`.
- `main.py`, `web/templates/base.html` and `web/templates/project.html`.
- `pyproject.toml` (a ruff per-file exemption).
- The five phase-guard tests (§20.1).
- `README.md` and `data/dev/README.md` (status and contents).

### Database Changes

Migration **`0006_p4_elicitation`** (down-revision `0005_p3_extraction`), purely additive.

- **Tables:** `stakeholder`, `interview_session`, `utterance`, `quality_finding` and `clarification`.
- **Enum types:** 10 new, and the existing `data_sensitivity_enum` is reused. The audit event enum gains 14 values.
- **Constraints:**
  - composite FKs for project consistency: session↔stakeholder, utterance↔session, clarification↔finding/version and
    clarification↔stakeholder;
  - `(session_id, seq)` unique;
  - a speaker-consistency check;
  - the partial unique index `uq_clarification_open_per_finding` (`WHERE status = 'OPEN'`);
  - query indexes.
- **Triggers (PostgreSQL):**
  - `utterance_append_only`;
  - `reqpilot_no_direct_delete` on session and stakeholder;
  - `reqpilot_interview_session_guard`;
  - `reqpilot_quality_finding_guard`;
  - `reqpilot_clarification_guard`.
- **Checkpointer tables** `checkpoints`, `checkpoint_blobs`, `checkpoint_writes` and `checkpoint_migrations` are
  created by `PostgresSaver.setup()` on first use.

No earlier migration was modified.

### LLM Changes

- **Prompts:** `stakeholder_interview_question@1.0.0`, `stakeholder_answer_assessment@1.0.0` and
  `clarification_question@1.0.0`. They are registered active and hash-locked, and each records its role, version,
  contract version, trust class and purpose.
- **Contracts:** `QuestionProposal`, `AnswerAssessment` and `ClarificationProposal`, all `extra="forbid"`, with no
  authority fields.
- **Provider:** the existing P3 OpenAI provider behind `LLMGateway`. There is no new integration.
- **Model:** `gpt-5.6-luna` via `LLM_MODEL_DEFAULT`.
- **Test mode:**
  - the default suite uses `ScriptedProvider` with the scripted persona, and is offline;
  - the real model runs only with `-m llm` and the developer's own configuration.

### End-to-End Demonstration

1. **Persona.** An Analyst creates *Dana Reyes (fictional)*, product owner, linked to a Stakeholder-role user, and
   starts a `SYNTHETIC` session. The product-owner template gives 10 applicable topics.
2. **Interview.** `elicitation_graph` selects `business_objectives`. Role #2 proposes a question, and code validates
   it. The graph interrupts at `await_answer`, and the thread is checkpointed.
3. **Vague answer.** Dana's answer is persisted, the graph resumes, and role #2 assesses the answer. On `inputs` it
   returns `incomplete` with the issue "which documents, which formats".
4. **Follow-up.** The deterministic router allows a follow-up (0 < 2). The follow-up asks which documents and formats,
   and Dana answers completely → `covered`. On `business_rules`, three vague answers reach the bound → `unresolved`.
   The interview continues to completion.
5. **Requirement extraction.** An Analyst runs P3 extraction over the session. This yields 7 requirements,
   `FR-LOAN-…`, `CLASSIFIED`, each citing Dana's utterance.
6. **Clarification.** An Analyst records an ambiguity finding on "…a decision letter quickly…" (span "quickly"). Role
   #4 proposes "Within how many working days…", and code binds it to the requirement and the finding. The version
   becomes `CLARIFICATION_REQUIRED`.
7. **Answer.** Dana answers: "…within one working day of the underwriter's decision."
8. **New version.** Re-analysis (P3 extract → validate → `persist_revision` → classify) moves version 1 to
   `CLARIFIED`. It creates **version 2**, whose statement names one working day, citing the original utterance and
   the clarification answer. Version 2 is `CLASSIFIED`, not approved. Version 1 is unchanged, and the audit chain
   verifies.

The demonstration UI runs the same flow at `/ui/projects/{id}/interviews` → `/ui/sessions/{id}` →
`/ui/projects/{id}/clarifications`.

### P5 Boundary

**P5 (quality engine and conflict detection) is not implemented.** No defect detector, conflict detection, quality
scoring or automatic re-run of quality analysis exists. `quality_finding` rows are created only by an Analyst through
the minimal P4 interface. P4 stops here.
