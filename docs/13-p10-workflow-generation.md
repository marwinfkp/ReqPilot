# ReqPilot — P10 Workflow Generation

**Roadmap phase:** P10 (`docs/01-analysis.md` §P) · **Modules:** M3 (`sdlc_graph`'s workflow mode:
`generate_workflow` → `emit_artefacts`), M8 (the workflow's Markdown and DOCX export through the P8
renderers), M7 (`workflow_templates.yaml`, the versioned phase structures and placements), M5 (trace
edges N.2 #24–#26), M6 (policy rule 13), M1 (the workflow pages)

**Status: P10 COMPLETE — ROADMAP EXIT PASSED**

*2026-09-26.* All eight `FR-WFL` requirements are implemented (§2). The approved exit criterion —
*"A generated workflow for a high-regulation project contains every mandatory compliance checkpoint
derived from that project's own mapping and every high-risk mitigation from its register"* — is
demonstrated by `tests/workflow/test_p10_exit_test.py` (§16), which computes the expectation from
the database with its own queries rather than the generator's code, and shows the same check
failing when one checkpoint or one mitigation verification is missing.

> **The LLM proposes; deterministic code disposes — and here the LLM is not asked at all.** The
> architecture assigns `FR-WFL` to the `generate_workflow` node and M8, with no agent role, and
> the workflow can be derived completely from versioned templates and the project's approved
> records. So P10 makes **no model call** (the exit test asserts `provider_calls == 0`):
>
> - the **SDLC** is the one that passed G6, verified from the four persisted `APPROVE` decisions —
>   never the runner-up, never a substitute, never a client's say-so;
> - the **structure** is the selected candidate's versioned template, whose composition must equal
>   the SDLC models P9's candidate names (a hybrid keeps its parts);
> - every **compliance checkpoint** comes from an eligible mapping of the project, every **risk
>   activity** from a HIGH risk of its register and that risk's recorded mitigation, every
>   **security activity** from a derived security requirement — each linked to its record;
> - the **production-readiness approval** is a gate *inside* the generated workflow (`[PS §16]`
>   category 8; architecture M.4). **No ninth ReqPilot gate exists**; G1–G8 are unchanged.

---

## 1. Scope and status

| Item | Result |
|---|---|
| **Requirements** | `FR-WFL-001` … `FR-WFL-008`, all MVP, all implemented (§2) |
| **Roadmap exit** | **Passed** — `tests/workflow/test_p10_exit_test.py` (§16) |
| **Offline suite** | **2,260 passed**, 263 skipped (PostgreSQL-only), 6 deselected (`llm`), **0 failed** |
| **PostgreSQL suite** | **2,523 passed**, **0 failed**, **0 skipped**, 6 deselected |
| **Migration** | `0012_p10_workflow_generation`, additive, `down_revision = 0011_p9_sdlc_recommendation` (§9) |
| **Model calls** | None. Workflow generation is deterministic (§4, decision D-P10-1) |
| **New benchmark** | None. The P10 exit is a functional completeness test; no accuracy target is invented, and E1–E9 and their benchmarks are untouched (§15) |

**Out of scope, and not implemented:** P11 (masking, prompt-injection defence, agent least
privilege, session isolation, retention/deletion, audit replay UI), P12, process-workflow
diagrams (`FR-DOC-011`, secondary), and P9's deferred weight sensitivity (`FR-SDL-009`).

## 2. Requirements and their implementation

| Requirement | Status | Where |
|---|---|---|
| **FR-WFL-001** Workflow customised to the selected SDLC: phases, activities, roles, deliverables | **Implemented** | `workflow_templates.yaml` (seven distinct phase structures, §5); `domain/workflow/derivation.py` |
| **FR-WFL-002** Security activities from the project's security requirements and risk register | **Implemented** | threat modelling and static security testing whenever a derived security requirement exists; one activity per control family present; implement + verify activities per HIGH-risk mitigation (§7) |
| **FR-WFL-003** Compliance checkpoints and approval gates from the project's own mapping, incl. production readiness | **Implemented** | one checkpoint per mapped checklist control, placed by obligation kind; template phase approvals; the production-readiness gate (§6, §8) |
| **FR-WFL-004** Testing requirements per phase | **Implemented** | template testing per phase, plus the families' requirements (e.g. transaction-integrity testing during validation) and mitigation verification tests |
| **FR-WFL-005** Entry and exit criteria per phase | **Implemented** | template criteria, plus exit criteria naming the phase's checkpoints and risk activities |
| **FR-WFL-006** Traceability requirements per phase | **Implemented** | template requirements, plus requirements naming the provenance of the phase's derived elements |
| **FR-WFL-007** Project Manager edits with a change log | **Implemented** | `services/workflow/service.py` `edit`; `workflow_change` (append-only); §13 |
| **FR-WFL-008** Export to Markdown and DOCX | **Implemented** | the P8 `Document` model and renderers; §14 |

## 3. Architecture and module integration

```
                         (P9)  G6: four APPROVE decisions via POST /approval-tasks/{id}/decide
                                                      │
 POST /sdlc-runs/{id}/workflow ─► SdlcRunner.generate_workflow   (WORKFLOW_GENERATE, human)
                                                      │ RunRecorder (triggered run, system actor)
                                                      ▼
   sdlc_graph   START ─route_sdlc_start─► generate_workflow ─route_after_workflow─► emit_artefacts ─► END
                  │ (P9 modes unchanged)          │ refused ──────────────────────────────────────► END
                  └─► collect_factor_evidence …   ▼
                                     WorkflowService.generate
                                     ├─ WorkflowSourceLoader.verify_g6   (persisted tasks + decisions)
                                     ├─ WorkflowSourceLoader.load        (P9 eligibility; G8/G3 pending → refuse)
                                     ├─ derive_workflow                  (template + records; pure)
                                     ├─ validate_workflow                (fail closed; pure)
                                     ├─ render check                     (Document, language guard)
                                     └─ WorkflowRepository.add           (rows + provenance, one flush)
   runner, after the graph: TraceGraphSync.workflow_edges → record   (as the human, if TRACE_SYNC)
```

Nothing restructures the 12-module architecture. The pure core is `domain/workflow/` (plan,
inputs, templates, derivation, validation, edits); the templates load through M7's versioned
ruleset loader (`rules/workflow.py`); persistence is `domain/models/workflow.py` and
`repositories/workflow.py`; orchestration reuses `sdlc_graph` and `SdlcRunner`; export reuses P8's
`artifacts` package. The import contracts are unchanged and kept (§15).

**Why a separate graph run rather than a node after `await_g6`.** Architecture C.5 places
`generate_workflow → emit_artefacts` after an `await_g6` interrupt. P9 implemented G6 through the
one approval service without a graph interrupt (`raise_g6 → END`), so there is no suspended run to
resume. P10 keeps P9 intact and adds a `workflow` mode to the same graph, started by a human once
G6 has passed. The node verifies G6 itself from the persisted records, so no resume payload — and
nothing a client sends — can stand in for a G6 decision (decision D-P10-2).

## 4. The generation pipeline

1. **Verify G6** (`services/workflow/sources.py`): the run exists in the project; its status is
   `SELECTED`; its selection is its computed first candidate; its G6 group holds exactly one task
   per G6 role, each `APPROVED`, each bound to the run's recommendation hash (which must still match
   the run), each with an `APPROVE` decision recorded in that role; the selected candidate row is
   rank 1. Any failure refuses with a code (`G6_NOT_PASSED`, `G6_TASKS_INCOMPLETE`,
   `G6_BINDING_STALE`, `G6_DECISION_MISSING`, …).
2. **Load the approved records**, with P9's eligibility (constants imported from
   `services/sdlc/evidence.py`, not restated): the baseline scope, refused if an authority blocker
   has appeared since P9 ran (`SOURCES_NOT_GOVERNED`); mappings of in-scope versions that are
   `candidate` or G2-`approved`; requirement-level risks of in-scope versions and project-level
   risks in `proposed`/`accepted`/`mitigated`; derived security requirements of in-scope versions
   that are `proposed` or G3-`approved`; the latest compliance run's gaps. In addition, generation
   is refused while an in-scope HIGH risk awaits G8 (`G8_PENDING`) or a derived security requirement
   awaits G3 (`G3_PENDING`) — otherwise a HIGH risk the register holds could be silently omitted.
3. **Derive** (`domain/workflow/derivation.py`, pure): §5–§8.
4. **Validate** (`domain/workflow/validation.py`, pure, fail closed): §11.
5. **Idempotency**: the input fingerprint (template hash, run, candidate, baseline, every eligible
   source row's id, content hash and status, every mitigation's status, the gaps) is looked up; the
   same inputs return the stored workflow (`reused`), and the database's unique key
   `(project_id, sdlc_run_id, input_fingerprint)` makes a duplicate impossible.
6. **Render check**: the workflow is built as a P8 `Document`, validated and passed through the
   artefact language guard *before* it is stored — what cannot be exported is not stored.
7. **Persist**: the project's live workflow (if any) becomes `superseded` and is kept; the new one
   is stored with its ordered children and provenance in one flush; `WORKFLOW_GENERATED` is audited
   with references only.
8. **Emit** (`emit_artefacts`): the *stored* workflow is rendered once as Markdown and DOCX and the
   hashes are logged — proof, at generation time, that the stored content is what an export
   contains. Nothing extra is stored.
9. **Trace**: the runner records N.2 #24–#26 as the human who asked, if that human holds
   `TRACE_SYNC` (policy rule 11); otherwise they appear at the next trace sync, derived from the
   same persisted provenance.

A refusal stores nothing but the graph run (`failed`) and a `WORKFLOW_GENERATION_REFUSED` audit
event carrying the codes; the API answers 409 with the findings.

## 5. SDLC-specific phases

`workflow_templates.yaml` (`workflow_templates@1.0.0`) holds one template per P9 candidate. They
are **different structures**, not one sequence renamed (a test asserts every candidate has a
distinct phase sequence):

| Candidate | Phases | What makes it that model |
|---|---|---|
| Waterfall | requirements → design → implementation → system testing → acceptance → deployment | sequential phases with sign-off gates |
| V-Model | requirements → system design → architecture design → module design → coding → unit testing → integration testing → system testing → acceptance testing → release | each test phase **`verifies`** its specification phase (unit↔module, integration↔architecture, system↔system design, acceptance↔requirements); stored and exported |
| Spiral | objectives → risk analysis and prototyping → engineering → evaluation → release | four phases marked as **repeating per spiral cycle**; a cycle-review gate decides continue / adjust / stop |
| Agile | inception → iterative delivery (sprints) → release validation → release | the delivery phase **repeats every sprint** |
| DevSecOps | plan and threat model → code and build with pipeline security checks → continuous testing → pre-production validation → release → operate and monitor | continuous phases with security checks as pipeline steps |
| Agile–V-Model hybrid | requirements and acceptance plan → system and architecture design → sprints → system verification → acceptance validation → release | the V's paired levels frame the release; implementation is in sprints |
| Agile–DevSecOps hybrid | inception, backlog and threat model → secure iterative delivery → continuous validation → release → operate | sprints delivered through a secure pipeline |

**Hybrid composition is preserved.** Each template declares its `composition`, and the loader
refuses a file in which it differs from the SDLC models the P9 candidate's attributes name
(`agile_v_model_hybrid` → agile + V-Model). A selected candidate with no template, or a mismatched
one, refuses generation — a different model is never substituted (`TEMPLATE_MISSING`,
`COMPOSITION_MISMATCH`).

Every phase carries stages, responsible roles, deliverables, entry and exit criteria, testing and
traceability requirements and at least one activity; the loader refuses a template phase lacking
any of them. Roles are the **target project's** roles (a closed vocabulary in the file: project
manager, product owner, business analyst, software architect, developer, QA engineer, security
engineer, compliance officer, data protection officer, operations engineer, release manager) —
not ReqPilot's RBAC roles.

Each template also names, for each **stage** (requirements, design, implementation, verification,
validation, release), the phase that hosts project-derived elements of that stage (`placement`).

## 6. Compliance checkpoint derivation

* **Source:** the project's own eligible compliance mappings (§4 step 2). A knowledge-base item
  that no mapping of the project names produces nothing; a rejected or pending interpretation
  produces nothing (P6 semantics). Nothing is assumed from the sector.
* **One checkpoint per mapped checklist control**, with **every** eligible mapping of that control
  linked to it (`workflow_source` `checkpoint_for`; trace edge N.2 #25).
* **Placement by obligation kind** (versioned): a `control` or `retention_obligation` is checked in
  the verification phase; an `audit_checkpoint` in validation; an `approval_checkpoint` or
  `reporting_obligation` in release — *before deployment*, as the problem statement's example has it.
* **Content:** approver roles, required evidence (including "the evidence cited by the mapping(s)"),
  entry and exit criteria, and a purpose that states the mapping's relationship and the
  requirements it covers, and that *a candidate mapping records a potentially applicable control;
  it is not a legal determination* (the P6 language constraint).
* **Mandatory:** a checkpoint cannot be removed or have its provenance changed (§13); the phase's
  exit criteria name its checkpoints.
* **What is not a checkpoint:** a compliance **gap** (an expected control with no covering mapping)
  becomes an **open item** on the workflow, never a checkpoint — a checkpoint for a mapping that does
  not exist would be fabricated. A mapping that cites no evidence is also an open item.

## 7. Security activities and high-risk mitigations

**From the derived security/privacy requirements** (P6 findings, `FR-WFL-002`): when the project
has any, *threat modelling* (design) and *static application security testing* (implementation)
are added and linked to all of them; then one activity per control family present, placed by the
family's stage and linked to that family's requirements, with the family's testing requirement
added to the phase of its testing stage (transaction integrity → validation, authentication →
verification, …).

**From the risk register** (`FR-WFL-002`; N.2 #26): for every HIGH risk that is eligible, each
**recorded, not-rejected mitigation** becomes two activities — *implement* (implementation phase)
and *verify* (verification phase) — both linked to the risk (`treats`) and to the mitigation
(`implements` / `verifies`). The link is provenance, not copied text: the trace edges
`risk REQUIRES_ACTIVITY workflow_activity` (N.2 #26) and, added by P10,
`risk_mitigation REQUIRES_ACTIVITY workflow_activity`. An AI-suggested mitigation no human has
accepted is labelled so and listed as an open item (`FR-RSK-005`). A HIGH risk with **no**
mitigation gets a *treatment-definition* activity in design and an open item — no mitigation is
invented. Nothing here changes a risk's severity, status or owner, and the P7 scope guard is
untouched (a workflow risk is a project risk, never a borrower's).

## 8. Production readiness

`[PS §16]` category 8 is satisfied **inside the generated workflow** (`FR-WFL-003`; architecture
M.1, M.4): exactly one `production_readiness` gate, in the phase that releases, with approver roles
(project manager, product owner, security engineer, compliance officer, operations engineer),
required evidence (acceptance and security test reports, runbook, plus this project's checkpoint
decisions, HIGH-risk activity completion and open-item resolution), and entry and exit criteria.
Validation requires exactly one, in a releasing phase; a database index allows no second. It is
**data ReqPilot produces**: ReqPilot does not operate the target project's deployment and does not
enforce the gate. **No platform gate was added** — `Gate` still has eight members, no approval task
is raised by P10 (tested), and every API response and page labels workflow gates as the generated
project's own.

## 9. Data model and migration `0012_p10_workflow_generation`

Additive; no earlier migration touched. Six tables (architecture G.8):

| Table | Holds | Moves after insert |
|---|---|---|
| `workflow` | run, selected candidate row and baseline (composite FKs), template ref and hash, input fingerprint, **generated structure** (the original, kept forever), revision, current content hash, open items, status | status (one way, to `superseded`), revision, content hash, open items |
| `workflow_phase` | ordered phases with stages, cycle, V-Model pairing, roles, deliverables, criteria, testing and traceability requirements | wording only |
| `workflow_activity` | ordered activities with kind, roles, deliverables, `mandatory` | wording only; a non-mandatory one may be removed |
| `workflow_gate` | ordered gates with kind, approvers, evidence, criteria | wording only; never removed |
| `workflow_source` | provenance: element → source record, relation, source content hash | append-only |
| `workflow_change` | the change log (§13) | append-only |

Constraints: composite project FKs throughout (a workflow can reference only its own project's run,
candidate and baseline; an element only its own workflow's phase); unique `(project, run,
fingerprint)` (idempotency); partial unique indexes **one live workflow per project** and **one
production-readiness gate per workflow**; checks on kinds, origins, positions, hashes, a derived
element being mandatory, and a change-log reason being present. PostgreSQL triggers mirror the ORM
guard: provenance and change log append-only; the workflow's identity, inputs and generated
structure immutable and a superseded workflow frozen; elements' identity, parent, ordering, kind
and mandatory flag fixed; phases and gates never deleted, a mandatory activity never deleted. The
migration also adds the five P10 audit event types (kept on downgrade: PostgreSQL cannot drop enum
values) and widens the trace allowlist check.

**Round trip:** upgrade → downgrade → upgrade tested on SQLite
(`tests/integration/test_migrations.py`) and PostgreSQL (`tests/integration/test_p10_postgres.py`),
including the exact allowlist at 0011 and at 0010.

**One change to a helper a historical migration imports.** Migrations 0010/0011 build the trace
`CHECK` from the live allowlist (P9's precedent). P10's triples N.2 #25/#26 touch no SDLC node, so
P9's `is_p9_triple` (used by 0011's downgrade to restore the P8 allowlist) now means "arrived with
P9 **or later**", and `allowed_triple_check_sql` gains `before_p10` for 0012's downgrade. The result
of 0011's downgrade is unchanged — exactly the P8 allowlist — and is tested (decision D-P10-10).

## 10. Traceability

| # | Edge | Built from |
|---|---|---|
| N.2 #24 | `sdlc_candidate REALISED_AS workflow` | the workflow's `realises` provenance |
| N.2 #25 | `compliance_mapping REQUIRES_CHECKPOINT workflow_gate` | each checkpoint's `checkpoint_for` rows |
| N.2 #26 | `risk REQUIRES_ACTIVITY workflow_activity` | each risk activity's `treats` rows |
| P10 | `risk_mitigation REQUIRES_ACTIVITY workflow_activity` | `implements` / `verifies` rows |
| P10 | `security_privacy_finding REQUIRES_ACTIVITY workflow_activity` | security activities' `derived_from` rows |

All go through the closed allowlist and `TraceGraphSync` (`workflow_edges`, also part of every full
sync); a client can name none of them — edit requests have no provenance fields and 422 on any.
Edges are append-only and survive supersession and edits, because mandatory elements are never
deleted. The workflow API also returns each element's provenance directly with display labels.

## 11. Validation and failure behaviour

`validate_workflow` returns every error; any error refuses. It checks structure (phases present
and uniquely keyed; each phase's name, stages, roles, deliverables, entry/exit criteria, testing and
traceability requirements and activities; each activity's name, kind, roles and deliverable; each
gate's name, purpose, approvers, evidence and criteria; unique element keys; roles in the
vocabulary; V-Model pairing to an earlier phase), the candidate and its `realises` link, exactly one
production-readiness gate in a releasing phase, that every source link resolves to an **eligible
record of this project and run** (`SOURCE_UNRESOLVED`), that a derived element is mandatory, and
**coverage** — the exit criterion as a check: each required mapping has a checkpoint
(`CHECKPOINT_MISSING`), each HIGH risk a risk activity (`RISK_ACTIVITY_MISSING`), each mitigation an
implementation *and* a verification activity (`MITIGATION_NOT_IMPLEMENTED`,
`MITIGATION_NOT_VERIFIED`), each derived security requirement a security activity
(`SECURITY_ACTIVITY_MISSING`). `required` is computed from the records independently of the
derivation. Nothing is repaired or dropped to make a workflow pass.

**Open items are not errors.** What the records leave unresolved (`COMPLIANCE_GAP_OPEN`,
`HIGH_RISK_WITHOUT_MITIGATION`, `MITIGATION_AWAITS_VALIDATION`, `MAPPING_WITHOUT_EVIDENCE`) is stored on
the workflow, sets its status to `open_items`, and is shown on every page and export — never filled
in, never hidden.

## 12. API and UI

| Method | Path | Who |
|---|---|---|
| POST | `/api/v1/sdlc-runs/{run_id}/workflow` — generate; 201 new, 200 reused, **409 + findings** refused | Analyst, Project Manager |
| GET | `/api/v1/sdlc-runs/{run_id}/workflow` (architecture S) | workflow readers |
| GET | `/api/v1/projects/{project_id}/workflows` | workflow readers |
| GET | `/api/v1/workflows/{workflow_id}` — phases, activities, gates, provenance, open items | workflow readers |
| PATCH | `/api/v1/workflows/{workflow_id}/phases/{phase_id}` · `/activities/{id}` · `/gates/{id}` | Project Manager |
| POST | `/api/v1/workflows/{workflow_id}/phases/{phase_id}/activities` (add) · `/activities/{id}/remove` | Project Manager |
| GET | `/api/v1/workflows/{workflow_id}/changes` | workflow readers |
| GET | `/api/v1/workflows/{workflow_id}/export?format=markdown\|docx` | workflow exporters |

Every handler authorises through the policy; a workflow or run of another project is a 404. Request
models are `extra="forbid"`: sending `sources`, `mandatory`, `kind`, `key`, `project_id` or `stages`
is a 422. `WorkflowError` maps to 409 and carries its findings.

UI: `/ui/projects/{id}/workflows` (generate from a G6 selection, list workflows, refusal codes) and
`/ui/workflows/{id}` (selected SDLC and G6, status, open items, ordered phases with activities,
gates and provenance, the Project Manager's edit forms, the change log, export links). Workflow gates
are headed "Gates of this project's process (not ReqPilot gates)". Links were added to the project
page and to a selected P9 run's page; the base template's phase marker now reads P10. Refusal
redirects carry finding codes only, never free text.

## 13. Editing and change history (`FR-WFL-007`)

* **Who:** only the Project Manager (`WORKFLOW_EDIT`, human-only). An analyst, compliance officer,
  auditor, agent or system actor, or anyone from another project, is refused.
* **What:** the wording and assignments of phases (name, description, roles, deliverables,
  criteria, testing and traceability requirements), activities (name, description, roles,
  deliverables) and gates (name, purpose, approvers, evidence, criteria); adding an activity to a
  phase; removing a template or manual activity. **Never:** an element's key, kind, `mandatory`
  flag, phase or provenance; removing a mandatory activity, a gate or a phase; the SDLC selection,
  the baseline, a risk or a mapping.
* **Fail closed:** the edit is applied to the current content and the **whole workflow is validated
  again** against the provenance it was generated with; an invalid result (an emptied exit
  criterion, a gate without approvers, an unknown role) is refused and nothing changes.
* **Log:** each accepted edit writes one append-only `workflow_change` row — revision produced,
  operation, element type, id and key, **each changed field's previous and new value**, reason
  (required), actor, role exercised, content hash before and after — and a `WORKFLOW_EDITED` audit
  event that carries **references only** (change id, revision, element, field names, hashes; never
  the reason or content, following P7–P9's reference-only audit).
* **History:** the generated workflow is kept unchanged on the `workflow` row
  (`generated_structure`, `generated_hash`), so the original survives any number of edits; every
  intermediate revision is reconstructable from the change log. Elements record their origin —
  `generated`, `edited`, or `manual` — so generated content and later human edits stay distinct.
* **Regeneration:** when the approved inputs change (or a new G6 selection is made), generating
  again creates a new workflow that **supersedes** the live one; the superseded workflow, its edits
  and its change log are kept and frozen. Edits do not carry over to the new workflow (§17).

## 14. Markdown and DOCX export (`FR-WFL-008`)

The persisted workflow — its current revision, with every edit — is built as P8's structured
`Document` (`services/workflow/document.py`) and rendered by P8's `render_markdown` and
`render_docx`: overview (project, selected SDLC and composition, baseline, run, G6 decisions
recorded, workflow id, revision, status, template, content hash), open items, one section per phase
in order (description, stages, cycle, V-Model pairing, roles, deliverables, criteria, testing and
traceability requirements, activities and gates with approvers, evidence, criteria and the record
each was derived from), then compliance-checkpoint, HIGH-risk-treatment and security-activity
summaries and the change log. Standing notices (from code) state that workflow gates are not
ReqPilot gates and that AI-suggested mitigations need human validation. Before rendering, the
service checks the rows still match the recorded content hash; the Markdown passes P6's artefact
language guard; DOCX is byte-reproducible (stamped with the workflow's last change). Responses carry
`X-Content-SHA256`; each export is audited (`WORKFLOW_EXPORTED`). CSV is refused. An export of an
`open_items` workflow shows its open items first — it is never presented as complete.

The export is rendered on demand, not stored as an `artifact_version`: the workflow is already
versioned (revision, generated structure, change log), and a stored copy would duplicate it
(decision D-P10-9). No `ArtifactType` or PostgreSQL enum value was added.

## 15. Tests and verification

68 new tests:

| File | Tests | Covers |
|---|---|---|
| `tests/unit/test_p10_domain.py` | 29 | templates (every candidate, distinct structures, V-Model pairing, loader refusals), derivation per candidate and by hand (checkpoints, placement, mitigations incl. rejected, missing mitigation, AI suggestions, security families, gaps), validation (every coverage and structure code, forged provenance), edits, row round trip, Markdown/DOCX |
| `tests/integration/test_p10_workflow_flow.py` | 14 | real P3–P9 world: generation, G6 refusals (none / three of four / rejected), selected-not-runner-up, idempotency, trace links, PM generation + sync, supersession, pending-G8 refusal, edits and change log, refusals, add/remove, exports |
| `tests/integration/test_p10_api_and_ui.py` | 5 | HTTP: generate/read/edit/export, 409 findings, forged fields 422, PM-only, cross-project 404, UI pages and forms |
| `tests/security/test_p10_security.py` | 10 | policy matrix, human-only, isolation at the repository, forged `SELECTED` status without decisions, no resume payload, no ninth gate / no approval task, provenance and history immutability |
| `tests/integration/test_p10_postgres.py` | 8 | PostgreSQL: the flow, triggers, partial unique indexes, idempotency key, composite FKs, allowlist, migration round trip |
| `tests/workflow/test_p10_exit_test.py` | 1 | the roadmap exit story (§16) |
| `tests/integration/test_migrations.py` | +1 | P10 downgrade, exact allowlists at 0011 and 0010 |

**Results** (2026-09-26, final state):

| Gate | Result |
|---|---|
| Offline suite | **2,260 passed**, 263 skipped (PostgreSQL-only), 6 deselected (`llm`), **0 failed** |
| PostgreSQL suite | **2,523 passed**, **0 failed**, **0 skipped**, 6 deselected — PostgreSQL 16.2 + pgvector 0.6.2, an existing local test database, migrated to head by the suite's fixture |
| P10 tests | 68; offline 60 passed + 8 PostgreSQL-only skipped; on PostgreSQL all 68 pass |
| Exit test | `tests/workflow/test_p10_exit_test.py` — 1 passed (offline and on PostgreSQL) |
| Migration 0012 | up / down / up on SQLite (`test_migrations.py`) and PostgreSQL (`test_p10_postgres.py`); exact allowlists at 0011 and 0010 |
| `ruff format --check` / `ruff check` | 468 files clean / clean |
| `mypy` | 289 source files, clean |
| `lint-imports` | 6 contracts kept, 0 broken |
| Provider SDK guard | `openai` (all approved) |

The first full offline run found one regression, in a phase-gate assertion: `test_p9_exit_test.py`
asserted that no `workflow` table exists (below). It was advanced; both suites above were run after
that change.

**Phase-gate tests advanced** (the established per-phase pattern): `test_api_health.py` (P10
endpoints now present; `/api/v1/workflows` no longer "future"), `test_migrations.py` and
`test_postgres_specific.py` (P10 tables permitted), `test_p1_persistence.py` (`workflow` no longer
future), `test_p8_domain.py` (the allowlist now holds exactly the five workflow triples, none
pointing out of a workflow node; origin `P10` accepted), and `test_p9_exit_test.py` (its "no P10"
check now asserts the P9 story generates no workflow, instead of asserting the tables do not exist).
No assertion about P0–P9 behaviour was weakened.

**Benchmark integrity:** no frozen benchmark, manifest or evaluation result was modified (§19);
E1–E9 were not re-run.

## 16. Roadmap exit criterion

> *A generated workflow for a high-regulation project contains every mandatory compliance
> checkpoint derived from that project's own mapping and every high-risk mitigation from its
> register.* — `docs/01-analysis.md` §P

`tests/workflow/test_p10_exit_test.py`:

1. **High-regulation project, through G6.** The synthetic loan-origination world baselines every
   governed requirement (L01–L06, L08) through P3–P8's real gates; P9 ranks it, the analyst's
   recorded override makes the regulated V-Model first, and all four roles approve at G6. The run's
   `regulatory_criticality` factor is asserted ≥ 4; four G6 tasks are `APPROVED` with four
   `APPROVE` decisions.
2. **The expectation, independently.** Queries in the test compute the mandatory checkpoints (eligible
   mappings of baseline versions: here four — a retention obligation, an approval checkpoint and two
   controls) and the high-risk mitigations (not-rejected mitigations of in-scope, governed HIGH risks:
   here two HIGH risks, one mitigation each); the test requires at least three mappings and at least
   one HIGH risk with a mitigation, so it cannot pass vacuously.
3. **Generation** from that selection, with `provider_calls == 0`, for the selected candidate.
4. **The criterion:** every expected mapping has a compliance checkpoint (`checkpoint_for`), every
   expected mitigation an implementation *and* a verification activity, every HIGH risk a risk
   activity — **no violations**.
5. **Traceability:** every provenance row has its trace edge (N.2 #25, #26, and #24 for the candidate)
   to an element that exists, from a record of the project.
6. **Production readiness** is one gate, with approvers and evidence, in the release phase.
7. **Persisted and retrievable** by another role with the same content hash; **project-scoped** — a
   Project Manager of another project is refused, and every row carries the project.
8. **Not vacuous:** removing one checkpoint link, or one verification, makes the same check report
   exactly that element.

**Result: passed** (§15). **P10 roadmap exit: PASSED.**

## 17. Limitations and deferred work

1. **Template content is `[PROJ]`.** No approved source fixes a phase list, a role or a criterion;
   the templates follow each model's standard shape and are reviewable data, but they are not
   expert-validated, and no evaluation measures workflow quality. None is claimed.
2. **Workflow gates are data.** ReqPilot records the production-readiness and other workflow gates
   but does not track their decisions — by design (architecture M.4).
3. **Editing is wording-level.** No reordering, no moving an element between phases, no adding
   phases or gates, no removing gates (mandatory or not). Removing a template activity is allowed.
4. **Regeneration does not carry edits.** A superseding workflow starts from generated content; the
   superseded one keeps its edits and log. A workflow whose exact inputs were later superseded is not
   revived (`INPUTS_MATCH_SUPERSEDED`).
5. **Staleness is recorded, not shown.** Each provenance row stores its source's content hash, and
   the fingerprint changes when a source changes; there is no staleness banner yet.
6. **MEDIUM and LOW risks produce no activity.** The mandatory set is HIGH risks (the exit criterion)
   and derived security requirements; lower risks remain in the register.
7. **Placement is one phase per stage.** In iterative templates several stages share a phase (e.g.
   Agile's sprints), which is intended; a finer per-family placement is data, not code.
8. **The exit world is one synthetic project.** Real-project behaviour is not measured.
9. **An observation about P9, not changed.** P10's ORM guard initially detected a changed column
   only from its removed previous value; a test showed an attribute set after the instance was
   expired (a savepoint rollback) was missed. P10's guard now uses `has_changes()`. P9's
   `_guard_sdlc` uses the same `history.deleted` pattern; its PostgreSQL trigger is the backstop.
   It is recorded here and left for a separate change, since P10 does not modify P9's behaviour.
10. **Deferred:** process-workflow diagrams (`FR-DOC-011`, secondary); P11 hardening; P12.

## 18. Decisions made during P10

| Id | Decision | Provenance |
|---|---|---|
| D-P10-1 | Deterministic generation; no model call | architecture traceability matrix (`FR-WFL` → `generate_workflow`, M8), no role contract for workflows; J.1 |
| D-P10-2 | A `workflow` mode of `sdlc_graph`, started by a human after G6; G6 verified from persisted tasks and decisions | C.5; P9 decides G6 via the approval service (no interrupt); M.2 |
| D-P10-3 | Inputs use P9's eligibility; additionally refuse while a HIGH risk awaits G8 or a security requirement awaits G3 | `services/sdlc/evidence.py`; `FR-WFL-002`/`-003` |
| D-P10-4 | "Mandatory checkpoint" = every eligible mapping of an in-scope version (any relationship), one checkpoint per control, placed by obligation kind | P6 statuses; exit criterion; `[PS §15]` example |
| D-P10-5 | "High-risk mitigation" = every not-rejected mitigation of an eligible HIGH risk; implement + verify; no mitigation → definition activity + open item | P7 register semantics; `FR-RSK-005` |
| D-P10-6 | Open items stored and shown (status `open_items`), never filled in; errors refuse | prompt §5.2, §7 |
| D-P10-7 | Workflow roles are target-project roles (closed vocabulary), not RBAC roles | architecture M.4 ("target project's approvers") |
| D-P10-8 | History: immutable generated structure + wording-level edits + append-only change log + reference-only audit; regeneration supersedes | `FR-WFL-007`; architecture O (audit) |
| D-P10-9 | Export rendered on demand through P8's `Document`/renderers; not stored as an artefact version; no new `ArtifactType` | ADR-007; avoid duplicate storage |
| D-P10-10 | Two P10 trace triples; exact per-migration allowlist cuts (`is_p9_triple` = P9 or later; `before_p10`) | N.1, N.2; P9's migration precedent |
| D-P10-11 | `WORKFLOW_GENERATE` for Analyst and Project Manager (human-only, a triggering action); `WORKFLOW_EDIT` Project Manager only; Architect reads and exports | F.1 (PM owns and edits the workflow); policy rules 11–12 |
| D-P10-12 | No ReqPilot approval of the workflow itself | F.1 mentions PM/Architect approving the generated workflow, but no `FR-WFL` requires it and G1–G8 contain no workflow gate; adding one would be a ninth platform gate |

## 19. Files changed

**Created (30): 22 source, migration and template files, 7 test files, this report.**
- `alembic/versions/0012_p10_workflow_generation.py`
- `src/reqpilot/domain/workflow/{__init__,plan,templates,inputs,derivation,validation,edits}.py`
- `src/reqpilot/domain/models/workflow.py`
- `src/reqpilot/rules/workflow.py`, `src/reqpilot/rules/data/workflow_templates.yaml`
- `src/reqpilot/repositories/workflow.py`
- `src/reqpilot/services/workflow/{__init__,sources,rows,document,service}.py`
- `src/reqpilot/api/workflow_schemas.py`, `src/reqpilot/api/routes/workflow.py`
- `src/reqpilot/web/workflow.py`, `src/reqpilot/web/templates/{workflows,workflow}.html`
- tests: `tests/p10_helpers.py`, `tests/unit/test_p10_domain.py`,
  `tests/integration/test_p10_{workflow_flow,api_and_ui,postgres}.py`,
  `tests/security/test_p10_security.py`, `tests/workflow/test_p10_exit_test.py`
- `docs/13-p10-workflow-generation.md` (this report)

**Modified (25): 18 source and template files, 6 test files, `README.md`.** Source (additive): `domain/enums.py` (audit events, actions, resource type, workflow
enums), `domain/errors.py` (`WorkflowError`), `domain/models/__init__.py`, `domain/policy/policy.py`
(rule 13, grants), `domain/traceability.py` (§9–§10), `services/traceability/sync.py`
(`workflow_edges`), `services/extraction/runs.py` (trigger action), `graph/{state,routers}.py`,
`graph/graphs/sdlc.py`, `graph/nodes/sdlc.py`, `graph/sdlc_runner.py`, `api/app.py`, `api/errors.py`,
`main.py`, templates `base.html`, `project.html`, `sdlc_run.html`.

**Modified — tests (phase gates, §15):** `tests/integration/{test_api_health,test_migrations,
test_p1_persistence,test_postgres_specific}.py`, `tests/unit/test_p8_domain.py`,
`tests/workflow/test_p9_exit_test.py`.

**Modified — documentation:** `README.md`.

**Unchanged:** every earlier migration; P9's scoring, weights, rules, ranking and G6; the eight
gates and the requirement lifecycle; all frozen benchmarks, manifests and evaluation results;
`docs/01`, `docs/02` and every earlier phase report.
