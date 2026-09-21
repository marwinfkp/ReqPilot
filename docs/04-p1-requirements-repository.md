# ReqPilot — P1 Requirements Repository

**Roadmap phase:** P1 Requirements repository — the second phase of the approved
P0–P12 roadmap in [`01-analysis.md`](01-analysis.md) §P.
**Status:** Complete. Exit test passes.
**Builds on:** [`03-p0-foundations.md`](03-p0-foundations.md).

P1 is the deterministic system of record. It proves that a requirement can be
created, versioned immutably, moved through a guarded lifecycle, approved at G1
by the right humans, and frozen into a baseline — **with no AI anywhere in the
picture**.

That ordering is the point. The governance guarantees exist, and are tested,
*before* there is any model output to govern.

---

## 1. Scope

### What P1 implemented

| Capability | Where |
|---|---|
| Requirement + immutable version model (`[DESIGN] D13`) | `domain/models/requirements.py` |
| 14-state lifecycle with guarded transitions | `domain/lifecycle/` |
| Human identifiers `FR-<DOMAIN>-nnn` | `domain/requirement_ids.py` |
| Exact-version content hashing | `domain/versioning.py` |
| Approval task + decision split (`[DESIGN] D6`) | `domain/models/approval.py` |
| **G1 enforcement, end to end** | `services/approval/` |
| Baseline + membership with the invariant | `services/baseline/`, `domain/models/baseline.py` |
| Project-scoped repositories | `repositories/` |
| REST API | `api/routes/` |
| Minimal demonstration UI | `web/` |
| Migration `0002` | `alembic/versions/` |

### What P1 deliberately did not implement

No LLM call, no provider SDK, no prompt, no agent, no RAG, no embeddings, no
knowledge base, no extraction, no classification, no clarification intelligence,
no conflict detection, no compliance analysis, no security/privacy analysis, no
risk analysis, no risk matrix, no SDLC scoring, no workflow generation, no
document generation, no evaluation harness, no real authentication, no MFA.

Each belongs to a later roadmap phase. The default test suite still makes **zero
external API calls**, and no provider SDK is installed.

---

## 2. Requirement and version model

```
requirement                          requirement_version
├─ id                                ├─ id
├─ project_id                        ├─ requirement_id, project_id
├─ human_id      "FR-LOAN-014"       ├─ version_no        1, 2, 3 …
├─ current_version_id   ─────────▶   ├─ state             ← lifecycle lives HERE
├─ baselined_version_id ─────────▶   ├─ statement, original_text, category,
├─ created_by                        │  priority, justification, dependencies,
└─ created_at                        │  assumptions, source_refs
                                     ├─ content_hash      ← the approval binding
     no state column, by design      ├─ review_signal     (null in P1)
                                     ├─ change_reason, created_by, created_at
                                     └─ superseded_by_id
```

**State lives on the version, not the requirement** (`[DESIGN] D13`). That is
load-bearing for G7: an approved version stays approved and baselined while a
successor is being worked on. Asking "what state is this *requirement* in?" would
be ambiguous the moment a successor exists, and D13 removes the ambiguity.

### Immutability

A version's governed content is fixed at creation. `IMMUTABLE_FIELDS` names the
fields that may never change, `assert_version_unmodified()` makes it provable at
any call site, and a test asserts the guard actually fires on an in-place edit.

The only fields that legitimately change after creation are `state` — through a
validated transition — and `superseded_by_id`, which records history.

### Fields later phases populate

Acceptance criteria, applicable regulations, risk level and the review signal are
part of the approved schema but are **not invented here**. `review_signal` is
nullable and null; the rest arrive with the phases that produce them. P1 does not
stub values it has no basis for.

---

## 3. Lifecycle

Fourteen states, exactly as architecture H.1 defines them:

```
CANDIDATE → EXTRACTED → CLASSIFIED → ANALYZED → VALIDATED
                                         ↕
                        CLARIFICATION_REQUIRED ↔ CLARIFIED
                                         ↓
                              PENDING_APPROVAL
                                    ↙        ↘
                             APPROVED      REJECTED → CLARIFICATION_REQUIRED
                                 ↓
                             BASELINED
                                 ↓
                            SUPERSEDED

  WITHDRAWN (from any pre-approval state, never from BASELINED)
  INVALID   (from CANDIDATE / EXTRACTED)
```

Structural facts, each asserted by a test:

- `APPROVED` is reachable **only** from `PENDING_APPROVAL`, and only with a
  recorded approval decision.
- `BASELINED` is reachable **only** from `APPROVED`.
- Terminal states have no outgoing transitions.
- A pair absent from the table is refused — there is no permissive fallthrough.
- **There is no `CONFLICTED` state.**

### D12 — conflict is a guard, not a state

Settled at P0 closure and honoured here. `TransitionContext.open_conflict_count`
blocks both `ANALYZED → VALIDATED` and `VALIDATED → PENDING_APPROVAL`.

Conflict *detection* belongs to a later phase, so P1 builds no detector. What it
builds is the guard the detector will feed, and it tests that guard with
synthetic context values — which is exactly what the approved design asks for.
The same `TransitionContext` carries fields for quality defects, unreviewed
high-severity risks and blocking gate tasks, each documented with the phase that
will populate it. They default to zero, which is accurate rather than permissive:
nothing can raise a defect yet because nothing that produces one exists.

---

## 4. G1 approval

### G1 is a co-approval gate

> **G1 requires BOTH an Analyst approval AND a Compliance Officer approval.**
> These are co-approvals. One role's signature is never sufficient, and one role
> alone can never produce a baseline.

That is the approved Phase 0 reading — analysis F.1 records *"Gates G2, and G1
co-approval"* — and architecture M.3's G1 row now annotates it explicitly.

`GATE_REQUIRED_ROLES[G1] = {ANALYST, COMPLIANCE_OFFICER}` and
`GATE_REQUIRES_ALL_ROLES[G1] = True`. Both live in `domain/enums.py`, and the
approval service reads them rather than keeping a second copy of the policy.

### Task cardinality

Architecture M.3 specifies how a multi-role gate is represented: **one task per
required role, all sharing a `task_group_id`**, with `required_role` singular
(architecture G.7). G1 uses exactly that mechanism.

Submitting *n* versions therefore raises *2n* tasks:

```
G1 task group (one task_group_id per submission)
├── FR-LOAN-001 v2  ├── required_role = analyst             → OPEN
│                   └── required_role = compliance_officer  → OPEN
└── FR-LOAN-007 v1  ├── required_role = analyst             → OPEN
                    └── required_role = compliance_officer  → OPEN
```

Each task carries its own copy of the subject's `content_hash`, so every
signature is bound to the exact version it was given.

### How completion is determined

Three nested conditions, evaluated deterministically in `ApprovalService`:

| Level | Condition | Effect |
|---|---|---|
| **Task** | A decision exists whose `role_exercised` equals this task's `required_role` | That task → `APPROVED` |
| **Subject** | *Every* task for this version is `APPROVED` (`_subject_complete`) | The version → `APPROVED`, `GATE_PASSED` audited |
| **Group** | *Every* task in the `task_group_id` is `APPROVED` (`_group_complete`) | **Only now** is the baseline materialised |

So an Analyst approval closes the Analyst task and nothing more: the Compliance
Officer task stays `OPEN`, the version stays `PENDING_APPROVAL`, and no baseline
row exists. The mirror case holds for a lone Compliance Officer approval. Both
are asserted by dedicated tests.

A task also refuses a decision whose role is not *its own* role, so holding both
roles does not let one person collapse co-approval into a single signature.

### Rejection

A rejection on either task sends the version to `REJECTED` and **cancels its
sibling task**, so a rejected version cannot later be approved through the other
role. No baseline is created.

### The five checks

`ApprovalService.decide()` refuses unless all five hold:

| # | Check | Failure |
|---|---|---|
| 1 | The task is open (and not cancelled) | `ApprovalError` — a decided task cannot be reused |
| 2 | `policy.can(APPROVAL_DECIDE)` allows it, given the task's gate and the role exercised: the actor is human, belongs to this project, and holds a role the gate requires | `AuthorizationError` (`ProjectIsolationError` across projects) — the service enforces the policy's answer via `require()` |
| 3 | The role exercised **is this task's own `required_role`** | `ApprovalError` |
| 4 | No self-approval | `SelfApprovalError` |
| 5 | The version binding is exact | `StaleApprovalError` |

Only after all five does a decision row exist, and **only a decision row can move
a version to APPROVED** — the lifecycle guard requires one.

### Segregation of duties

An actor may not approve a version they authored. With co-approval this means a
realistic G1 needs three people: an authoring analyst, a *different* analyst to
sign the Analyst task, and a compliance officer to sign the Compliance Officer
task. Tested explicitly.

### Gate-consequent transitions

When a gate passes, the subject moves `PENDING_APPROVAL → APPROVED`. That
transition is applied by the approval service, **not** routed through
`RequirementService.transition()`, which is the analyst's authoring operation and
is gated on `requirement.transition` — a permission a compliance officer rightly
does not hold.

This mattered in practice: the first implementation did route through it, and the
policy correctly refused the officer's approval. The fix was to model the
transition as authorised by the decision, not by the decider's general standing.
Widening the analyst permission to make it work would have been exactly the wrong
answer.

---

## 5. Exact-version binding

`content_hash` covers everything a reviewer would have read: statement, category,
priority, justification, dependencies, assumptions, source refs, version number
and human id. It deliberately excludes state, timestamps, creator and review
signal — an approval must survive the very transition it causes.

- The task records the hash **when it is raised**.
- The decision records the hash **at decision time**.
- They must match, or `StaleApprovalError`.
- A successor version produces a different hash, so no earlier decision covers it.

A test proves the invariant end to end: V1 is submitted and bound; V2 is created;
the V1 task still points at V1 with V1's hash; and no G1 task covers V2, so V2
cannot be approved through the old one.

---

## 6. Baseline invariant

> **No unapproved requirement version may enter a baseline** (`FR-HIL-004`).

Three **complementary** enforcement layers, as architecture H.4 requires. They are complementary rather than strictly independent: layers 1 and 3 both rest on the same `APPROVED` state, and layer 2 is what puts that state there. Layer 2 is the only one that can be verified today, because the database trigger has not been executed against a live server (see §14).

1. **Service** — `BaselineService._collect_and_verify()` refuses unless every
   member is `APPROVED`, has an approval decision bound to its exact hash, and
   belongs to this project. It also refuses an empty set, duplicates, and an
   authorising decision that was a rejection.
2. **Database (PostgreSQL)** — migration `0002` installs
   `baseline_member_requires_approval`, a `BEFORE INSERT` trigger that refuses a
   member whose version is not `APPROVED`/`BASELINED` or whose project does not
   match the baseline's.
3. **Approval path** — a version reaches `APPROVED` only through a recorded,
   role-appropriate, exactly-bound decision.

Producing an unapproved baseline would require defeating the service check and
the database trigger and the approval path. That is defence in depth, not three
independent proofs of the same fact.

`POST /projects/{id}/baselines` returns **202 with approval tasks**, not 201 with
a baseline. The baseline resource does not exist until the gate passes — which is
why there is no "create baseline" endpoint at all.

---

## 7. RBAC and project isolation

No second authorization mechanism was created. The foundation's
`policy.can(actor, action, resource)` is still the single entry point; P1 added
its actions to the existing `_ACTION_GRANTS` table.

| Role | Author | Transition | Submit | Decide G1 | Read | Baseline |
|---|---|---|---|---|---|---|
| Analyst | ✓ | ✓ | ✓ | ✓ (not own work) | ✓ | ✓ via gate |
| Compliance Officer | ✗ | ✗ | ✗ | ✓ | ✓ | ✓ via gate |
| Security Reviewer | ✗ | ✗ | ✗ | ✗ (G1) | ✓ | ✗ |
| Stakeholder | ✗ | ✗ | ✗ | ✗ | ✓ | ✗ |
| Auditor | ✗ | ✗ | ✗ | ✗ | ✓ read-only | ✗ |

`APPROVAL_DECIDE` still has **no blanket action grant**. `can()` authorises it
per gate: the `ResourceRef` must carry the `gate` and the `role_exercised`, and
the decision is allowed only for a human who belongs to the project and holds
that role there, where the role is one `GATE_REQUIRED_ROLES` names for the gate.
A bare `APPROVAL_DECIDE` is refused, and `is_superuser` does not grant it.
`ApprovalService` enforces that answer; its only check of its own is that the
role exercised is the task's `required_role`.

**Isolation** is enforced at the repository layer, not the UI. Every P1 table
carries `project_id`; every repository method authorises then scopes. Resources
addressed by bare id are found by scanning *the actor's own projects*
(`api/lookup.py`), so no unscoped query is ever issued.

Cross-project access returns **404, not 403** — answering "forbidden" would
confirm the resource exists. A test asserts a foreign id and a nonexistent id
produce byte-identical responses.

---

## 8. Audit

The foundation's append-only, hash-chained `AuditService` was extended, not
replaced. No second audit table exists.

Events added: `REQUIREMENT_CREATED`, `REQUIREMENT_VERSION_CREATED`,
`STATE_TRANSITION`, `REQUIREMENT_WITHDRAWN`, `REQUIREMENT_SUPERSEDED`,
`APPROVAL_TASK_CREATED`, `APPROVAL_GRANTED`, `APPROVAL_REJECTED`,
`APPROVAL_MODIFIED`, `GATE_PASSED`, `BASELINE_COMMITTED`,
`BASELINE_MEMBER_ADDED`.

**Payloads carry references only.** The foundation already rejects payload keys
like `statement`, `text` and `api_key`; a P1 test asserts no requirement text
appears anywhere in any payload. An auditor can reconstruct who created the
requirement, who created each version, every transition, who decided, **which
role they exercised**, which exact hash was approved, and what entered the
baseline — all without reading a single requirement statement.

Domain writes and their audit events share one transaction, so an audited action
always happened and an unaudited one never did. Tested by rollback.

---

## 9. API

| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/projects/{id}/requirements` | Create; 201 |
| GET | `/api/v1/projects/{id}/requirements` | List |
| GET | `/api/v1/requirements/{id}` | Detail + history + available transitions |
| PATCH | `/api/v1/requirements/{id}` | **Creates a successor version** |
| POST | `/api/v1/requirements/{id}/withdraw` | |
| POST | `/api/v1/requirement-versions/{id}/transition` | One guarded transition; **cannot reach APPROVED** |
| GET | `/api/v1/projects/{id}/approval-tasks` | Queue |
| GET | `/api/v1/approval-tasks/{id}/decisions` | |
| POST | `/api/v1/approval-tasks/{id}/decide` | **The only approval path** |
| POST | `/api/v1/projects/{id}/baselines` | **202 + tasks**, not a baseline |
| GET | `/api/v1/projects/{id}/baselines` · `/api/v1/baselines/{id}` | |
| GET | `/api/v1/projects/{id}/audit` | |

No write schema carries a `state` field, and a stray one is ignored — tested.

**Error mapping:** isolation → 404 (no existence disclosure) · authorization →
403 · governance refusal → 409 · bad input → 400.

### Actor mechanism — development only

`X-ReqPilot-Actor: <user id>`. Real authentication is out of scope for this
phase, as it was for the foundation. The header carries an **identity claim
only**: roles are read from `project_member`, so no header value can grant a role
the user does not hold. The mechanism refuses to operate when `REQPILOT_ENV` is
production. This is a weak *authentication* stand-in, not a weak *authorization*
path.

---

## 10. Minimal UI

Server-rendered Jinja2 at `/ui`, per ADR-008. Project picker → requirements
list → requirement detail with version history and available transitions →
approval queue → baseline view → audit viewer.

Deliberately plain: no dashboards, no analytics, no charts. The UI calls the same
services the API calls — there is no test-only implementation of the workflow.

It hides actions the actor cannot take (an author sees *why* they may not approve
their own version), but hiding a button is a convenience. The service refuses the
action regardless, and the tests drive both paths.

Run it:

```bash
uvicorn reqpilot.main:app --reload
```

`reqpilot.main` is the composition root. `reqpilot.api.app` builds the API alone,
so the API never imports the web layer and the dependency direction
`web → api → services → repositories → domain` stays one-way and enforceable.

---

## 11. Migration

`0002_p1_requirements_repository` — additive. The foundation migration is
untouched.

Adds `requirement`, `requirement_version`, `approval_task`, `approval_decision`,
`baseline`, `baseline_member`, plus the PostgreSQL baseline-membership trigger.

Tested: upgrade creates the six tables and leaves the foundation intact; the
migration matches the ORM models column for column; downgrade to
`0001_p0_foundation` removes only the P1 tables; upgrade → downgrade → upgrade
round-trips; and **no table from a later roadmap phase appears**.

---

## 12. Tests

**400 collected · 389 passed · 0 failed · 10 skipped · 1 deselected · 1 warning**

| Category | Count |
|---|---|
| unit | 193 |
| integration | 89 |
| workflow | 20 |
| security | 97 |
| llm (opt-in, excluded by default) | 1 |

P1 added **196 tests** (the foundation phase left 204). **Tests requiring an external API: 0.**

The 10 skips are the PostgreSQL-only tests (append-only `REVOKE`, pgvector, and
now the baseline trigger). They skip visibly rather than passing quietly. See §14.

The warning is a pre-existing `DeprecationWarning` from Starlette's test client,
unrelated to P1.

### Quality gates

| Gate | Result |
|---|---|
| `ruff format --check .` | 106 files already formatted |
| `ruff check .` | All checks passed |
| `mypy` | No issues in 72 source files |
| `lint-imports` | **3 contracts kept, 0 broken** |
| `pytest -q` | 389 passed, 10 skipped |

The domain still imports no LangGraph and no LLM code, proved both statically and
by importing it in a subprocess with `langgraph` blocked at the meta-path.

### Two P0 boundary tests advanced, not weakened

`test_migration_creates_nothing_beyond_the_foundation` and
`test_no_domain_endpoints_are_exposed_yet` asserted that P1 tables and endpoints
did not exist. They now assert the *current* phase boundary — foundation ∪ P1
tables, and no endpoint from any later phase — plus a companion test that the
current phase's endpoints **are** present, so the check cannot pass vacuously.

---

## 13. Traceability

Three statuses, kept honest: **Implemented** (working and tested) · **Boundary
prepared** (the structure exists; the behaviour belongs to a later phase) ·
**Deferred**.

| Requirement / decision | Status | Evidence |
|---|---|---|
| `FR-PRJ-001` project with domain and jurisdiction | Implemented (foundation) | `project` table |
| `FR-PRJ-002` project lifecycle state machine | **Partial** — advanced to BASELINED by the baseline service only | `BaselineService._advance_project`; earlier transitions belong to the phases that drive them |
| `FR-PRJ-004` project isolation | **Implemented** | Repository scoping, policy, 404 semantics, 12 isolation tests |
| `FR-EXT-002` the §8 requirement schema | **Partial** — structural fields implemented; acceptance criteria, applicable regulations and risk level deferred to their phases | `RequirementVersion` |
| `FR-EXT-004` stable `FR-<DOMAIN>-nnn` ids | **Implemented** | `domain/requirement_ids.py`; covered by `tests/unit/test_requirement_ids_and_versioning.py` |
| `FR-EXT-007` never emit a requirement without a source link | **Implemented** | `CANDIDATE → EXTRACTED` guard |
| `FR-HIL-001` enforce gates in the application layer | **G1 implemented; G7 task raised** | `ApprovalService`; G2–G6, G8 belong to the phases that create their subjects |
| `FR-HIL-002` accept / reject / modify | **Implemented** for repository operations; regenerate is not applicable (no AI output) | `ApprovalDecisionType` |
| `FR-HIL-003` role-appropriate approval | **Implemented** | `GATE_REQUIRED_ROLES`, per-task `required_role`, `role_exercised`; covered by `tests/security/test_p1_governance.py` |
| `FR-HIL-004` nothing unapproved in a baseline | **Implemented** | Three-layer invariant, §6 |
| `FR-HIL-005` record approver, role, time, version | **Implemented** | `approval_decision` |
| `FR-AUD-001` agent-run records | Boundary prepared (foundation) | `agent_run`; no agent runs yet |
| `FR-AUD-002` record every human action | **Implemented** | 12 new event types |
| `FR-AUD-003` audit viewer | **Implemented** | `/ui/.../audit`, `GET /audit` |
| `FR-AUD-004` replay a requirement's history | **Implemented** | Reconstruction test |
| `FR-AUD-005` append-only | **Implemented** (foundation, preserved) | No mutation path; chain verified after the full flow |
| `FR-ADM-002` server-side RBAC | **Implemented** | Policy at API *and* repository |
| `FR-ADM-003` agent least privilege | Boundary prepared (foundation) | No agents exist |
| `NFR-TST-001/002` offline, deterministic tests | **Implemented** | 0 external API calls |
| `QA-AUD`, `QA-TRC`, `QA-HUM`, `QA-ACC` | **Implemented** | §8, §5, §4 |
| `[DESIGN] D6` task/decision split | **Implemented** | `domain/models/approval.py` |
| `[DESIGN] D8` hash-chained audit | **Implemented** (foundation, extended) | Chain verifies after the P1 flow |
| `[DESIGN] D10` capability tokens | **Deferred** | No agent invocations exist to scope |
| `[DESIGN] D12` conflict as a guard | **Implemented** | No `CONFLICTED` state; guard tested synthetically |
| `[DESIGN] D13` version-level lifecycle | **Implemented** | `requirement_version.state` |
| `ADR-009` centralised policy | **Implemented** | One `can()`; no second mechanism |
| `ADR-010` append-only audit | **Implemented** | Extended, not replaced |

**Not claimed:** no later-phase FR is marked implemented merely because a table
or interface exists.

---

## 14. Environment limitations

**PostgreSQL runtime verification remains BLOCKED**, unchanged from P0 closure
and honestly recorded rather than worked around. There is no container runtime
and no PostgreSQL on this machine.

Consequence: the PostgreSQL-specific guarantees — the audit `REVOKE`, pgvector,
and now **the baseline-membership trigger** — are written and generate correct
DDL but have not executed against a live server. The service-layer baseline
invariant *is* fully tested and is what currently enforces the rule.

One command on any machine with Docker closes this:

```bash
docker compose up -d db && alembic upgrade head && REQPILOT_TEST_DATABASE_URL=postgresql+psycopg://reqpilot:reqpilot_local_dev_only@localhost:5432/reqpilot pytest -m integration
```

Python remains 3.13.7 locally against a `>=3.12` floor; CI pins 3.12.

---

## 15. Deferred to P2 and later

| Item | Phase |
|---|---|
| Knowledge base, chunking, embeddings, retrieval, citations | P2 |
| Requirement extraction and classification | P3 |
| Adaptive elicitation, clarification intelligence | P4 |
| Quality analysis, **conflict detection** (the D12 guard's producer) | P5 |
| Compliance and security/privacy analysis; gates G2, G3 | P6 |
| Risk analysis, 3×3 matrix, risk register; gate G8 | P7 |
| Traceability matrix, document generation; G5 | P8 |
| SDLC factors, MCDA, ranking; G6 | P9 |
| Workflow generation, production-readiness gate **in the generated workflow** | P10 |
| Masking, injection detection, output filtering | P11 |
| Evaluation harness and metrics | P12 |

Also deferred by decision rather than phasing: real authentication, MFA, the
capability-token mechanism, and full G7 change-management (P1 raises the G7 task
and establishes the version model it needs; deciding G7 comes with the phase that
needs it).

---

## 16. P1 exit test

> create → version/edit → validate → submit → G1 approval task → authorised
> approver approves → exact version APPROVED → baseline committed → baseline
> contains only approved versions → version history correct → every meaningful
> action audited → unauthorised paths refused

**PASSES.** Proved twice, independently:

- `tests/workflow/test_p1_exit_test.py::test_p1_exit_scenario` — service level
- `tests/integration/test_p1_api_and_ui.py::test_full_workflow_through_the_api`
  and `::test_ui_full_workflow` — over HTTP and through the UI

with 97 security tests covering the refusals.
