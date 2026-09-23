# ReqPilot — P7 Risk Analysis & Register

**Roadmap phase:** P7 (`docs/01-analysis.md` §P) · **Modules:** M3 (`analysis_graph` nodes 18–19 and the
G8 part of 20; role #9 Risk Analysis), M6 (P1 approval service and lifecycle guards), M7
(`risk_rules.yaml`, the versioned severity matrix), M9 (gate G8), M1 (the risk-register page),
M12 (the P7 evaluation harness)

**Status: P7 COMPLETE — ROADMAP EXIT PASSED — PROJECT-AUTHOR REVIEW COMPLETED**

*2026-09-23.* All four approved P7 exit criteria (`docs/01-analysis.md` §P) are demonstrated by the
end-to-end exit test and the suites below: the severity is computed by the matrix and not by the
LLM, every risk links to a requirement (or the project) **and** to evidence, a high-severity risk
blocks baseline approval, and the `FR-RSK-011` scope-guard test passes. No numeric accuracy target
exists for risk analysis and none is claimed (§20).

> **The LLM proposes; deterministic code disposes.** In P7 a model proposes a risk's category, its
> **ordinal likelihood and impact** with a written rationale for each, mitigation considerations,
> and the supplied evidence that grounds it.
>
> Everything with authority is decided by code or by a human:
>
> - the **severity** is a lookup in the approved 3×3 matrix (I.3), pinned at the database to the
>   matrix row it claims, and there is **no severity field anywhere a model could reach**;
> - whether **G8** fires is read from the persisted severity column, never from model text;
> - whether a requirement may be **baselined** is decided by the lifecycle guard, from persisted
>   rows, and it fails closed;
> - the **Security Reviewer** decides G8, through the one approval path;
> - accepting, mitigating, rejecting or closing a risk is a **human** action with a recorded
>   rationale (`FR-RSK-010`), and no agent actor can perform it.
>
> ReqPilot's risk is **never** a borrower's credit risk, a customer risk rating, a probability of
> default or a fraud score. That boundary is enforced in the category enum, in a code-level scope
> guard, and structurally by a schema that has no customer or applicant entity to point at.

---

## 1. P7 status

| Gate | Result |
|---|---|
| **Offline suite** | **1,961 passed**, 234 skipped (the PostgreSQL-only set), 6 deselected (`llm`), **0 failed** |
| **Live PostgreSQL suite** | **2,195 passed**, **0 failed**, **0 skipped**, 6 deselected — PostgreSQL 16.2 + pgvector 0.6.2, fresh database migrated to head |
| **P7 exit test** | `tests/workflow/test_p7_exit_test.py` — **16 passed** (14 test functions, one of them parametrised over three roles) |
| `ruff format --check` | 335 files, clean |
| `ruff check` | clean |
| `mypy` | 214 source files, clean |
| `lint-imports` | 5 contracts kept, 0 broken |
| Provider SDK guard | unchanged and passing |
| Migration | `0009_p7_risk_register`, up / down / re-up on SQLite **and** PostgreSQL |

P0–P6 are unaffected: no earlier migration, benchmark, result or frozen hash was changed (§22).

## 2. Scope

P7 implements `FR-RSK-001`…`FR-RSK-011`. `FR-RSK-012` (quantitative risk modelling, risk-appetite
frameworks, KRI dashboards) is **out of scope and stays out of scope** — nothing in this phase
computes a quantitative risk measure, a risk appetite or a KRI.

**In:** requirement-level and project-level risk identification; the six approved categories; the
1–3 ordinal likelihood and impact scales with written rationales; the deterministic severity
matrix; mitigation considerations; requirement-version and evidence linkage; the risk register and
its aggregate measures; gate G8 and the baseline block; risk-related audit; RBAC and project
isolation; consumption of P5 and P6 outputs.

**Out:** everything the P7 brief lists for P8–P12. In particular there is no approval fan-out
beyond G8, no traceability matrix, no document generation, no SDLC scoring, no masking
implementation, and no P12 evaluation (§24).

## 3. Source hierarchy

Followed in the order the brief sets: the problem statement, then `docs/01-analysis.md` (the
approved Phase 0 analysis and P0–P12 roadmap), then `docs/02-architecture.md`, then the earlier
phase reports, then the P7 brief. Where the brief and a higher source could be read differently,
the higher source won; the two places that mattered are recorded in §22.

Provenance tags are used as Phase 0 defines them: `[PS §n]` problem statement, `[P0 §x]` approved
Phase 0 analysis, `[DESIGN]` approved architecture decision, `[PROJ]` a project-defined engineering
choice, and **P7** for a decision made in this phase.

## 4. FR-RSK requirement coverage

| Requirement | Provenance | Status | Where |
|---|---|---|---|
| **FR-RSK-001** Identify risks per requirement, and project-level risks from the set as a whole | `[PS §4]` | **Implemented** | `graph/nodes/risk.py` (both passes); `risk.scope` with a database check that a requirement risk names a version and a project risk names none |
| **FR-RSK-002** Categorise as business, technical, security, privacy, compliance or operational | `[PS §4]` | **Implemented** | `RiskCategory` (exactly six, closed); validation drops any other value with `unknown_category` |
| **FR-RSK-003** Ordinal likelihood and impact, 1–3, with a written rationale for each | `[PROJ]` | **Implemented** | `RiskLikelihood` / `RiskImpact` (architecture I.2); a rating without its rationale is dropped, never stored unexplained |
| **FR-RSK-004** Derive the level deterministically from a published matrix; the LLM proposes the ratings | `[PROJ]` | **Implemented** | `domain/risk/matrix.py` + `rules/data/risk_rules.yaml` + the `risk_matrix` table; `GET /api/v1/risk-matrix` publishes it |
| **FR-RSK-005** Suggest mitigations, labelled as suggestions requiring human validation | `[PS §12]` | **Implemented** | `risk_mitigation.is_ai_generated` set by code; the register and the UI label each one; a human acceptance is recorded |
| **FR-RSK-006** Link every risk to its requirement **and** its supporting evidence | `[PS §12]` | **Implemented** | `risk.requirement_version_id` (exact version), `risk_evidence` with composite FKs, `evidence_count >= 1`, and a deferred constraint trigger |
| **FR-RSK-007** Flag high-severity risks; a high-severity risk blocks baseline approval (G8) | `[PS §16]` | **Implemented** | G8 raised from the persisted severity; `unreviewed_high_risk_count` in the `ANALYZED → VALIDATED` guard; a database check makes a HIGH risk in `PROPOSED` impossible |
| **FR-RSK-008** Generate a Risk Register artefact with ratings, level, mitigations, owner, status and links | `[PS §12]` | **Implemented** | `services/risk/register.py` — a query over `risk` plus a deterministic rendering (`[DESIGN] D7`), as JSON and as Markdown |
| **FR-RSK-009** Expose aggregate risk measures as inputs to the SDLC factor profile | `[PS §13]` | **Implemented** | `RiskRegisterService.factor_inputs` — the four I.6 formulas, each carrying the risk ids that produced it. Inputs only; the scoring is P9's |
| **FR-RSK-010** A human may add, edit, accept or close a risk with a recorded rationale | `[PS §16]` | **Implemented** | `services/risk/service.py`; `RISK_MANAGE` is human-only in the policy; a decision without a rationale is refused |
| **FR-RSK-011** Scope guard: project/engineering risk only; never borrower credit risk | `[PROJ]` | **Implemented** | `domain/risk/scope.py` (code, not configuration), the closed category enum, and a schema with no customer entity |
| **FR-RSK-012** Quantitative risk modelling, risk appetite, KRI dashboards | `[PROJ]` | **OUT OF SCOPE — and remains so** | Nothing in P7 implements any of these |

`FR-HIL-001` (enforce gates G1–G8 in the application layer, not by prompt instruction) is extended
to G8 by this phase: `[PS §16]` for G1–G7, `[PROJ]` for G8.

## 5. Architecture integration

P7 adds two nodes to the existing `analysis_graph` and one gate to the existing approval service.
It creates no second repository, audit system, RBAC system, evidence system, RAG system, gateway,
conflict engine, compliance engine or orchestration framework.

```
… → security_privacy_evaluate → risk_identify → risk_compute_severity ─┬→ gate_fanout → END
                                                                       └→ END
```

| Node | C.3 | Kind | Role | Writes |
|---|---|---|---|---|
| `risk_identify` | 18 | LLM (grounded) | Risk Analysis (#9) | nothing — proposals only |
| `risk_compute_severity` | 19 | **deterministic** | Risk Analysis (#9) | `risk` rows **with** their severity |
| `gate_fanout` | 20 | deterministic | Coordinator (#1) | G2 / G3 (P6) **and G8** (P7) |

**Routing** is `route_risk` in `graph/routers.py`: an ordinary Python function over typed state that
returns a value from a closed `Literal`. It reads the flags a deterministic node set *after*
writing the rows, so a flag can only cause a check — the fan-out re-reads the persisted columns
itself. A model response cannot say "skip risk validation", "route to approval" or "do not create
G8": there is no field for it in the state, and the router never sees model text.

**What P7 reuses unchanged:** the P1 requirement/version repository, lifecycle and approval
service; the P2 retrieval, evidence and citation machinery; the P3 gateway, prompt registry and
review queue; the P4 clarification structures; the P5 quality and conflict outputs; the P6
compliance and security outputs, retrieval path and analysis views. P2 retrieval semantics and
ET-06 are untouched.

**State** (`AnalysisState`, architecture D.2): P7 adds `risk_mode`, `risk_version_ids`, `risk_ids`,
`risks_out_of_scope` and the D.2-named flag `has_high_severity_risk`. Ids, counts and flags only —
proposals live in the run's transient context and are cleared by the node that consumes them (D.4),
so no risk text ever reaches a checkpoint.

## 6. Risk data model

Migration `0009_p7_risk_register`, additive (§18).

| Table | Key fields | Notes |
|---|---|---|
| `risk_matrix` | `matrix_version`, `likelihood`, `impact`, `severity` | The approved I.3 matrix **as versioned data** (`DQ-03`). Nine rows, seeded from `risk_rules.yaml` |
| `risk` | id, project_id, `scope`, `requirement_version_id?`, category, title, description, `likelihood`, `impact`, **`severity`**, `matrix_version`, both rationales, citations, `evidence_count`, `detected_by`, status, `owner_role`, `approval_task_id?`, decision fields, `content_hash` | The register's rows |
| `risk_evidence` | risk_id, evidence_id, project_id | Composite FKs pin both sides to the risk's project |
| `risk_mitigation` | risk_id, suggestion, `is_ai_generated`, status, accepted_by | Always a suggestion until a human accepts it |

**How the severity is made unforgeable.** `risk` does not merely store a severity beside two
ratings: it carries `(matrix_version, likelihood, impact, severity)` as a composite **foreign key
into `risk_matrix`**. A row whose severity is not the matrix's value for its own cell does not
exist — at the database, whatever wrote it. Architecture G.6 asks for "a DB CHECK against
`risk_matrix`"; a SQL `CHECK` cannot reference another table, so the constraint is expressed as the
foreign key that can, which is strictly stronger than a check against a copied value (§22, D-1).

Two further checks complete the picture: a HIGH risk can never sit in `PROPOSED`
(`high_risk_is_gated`), and a risk is either requirement-scoped with a version or project-scoped
without one (`scope_matches_version`).

Content — including both ratings and the computed severity — is immutable, in the ORM guard and
again in a PostgreSQL trigger. A risk is never deleted; one that should not stand is *rejected* by
a human with a recorded rationale. A published matrix version is immutable too: editing a cell in
place would silently re-rate every historical risk that cites that version, which is exactly what
recording `matrix_version` on every row exists to prevent.

## 7. The six risk categories

`business` · `technical` · `security` · `privacy` · `compliance` · `operational` — exactly the six
of `FR-RSK-002` `[PS §4]`, as a closed enum. There is deliberately **no** `financial`, `market`,
`credit`, `borrower`, `legal` or `reputational` category. A proposal naming one is dropped with
`unknown_category`, audited, and raised as a review item; it is never coerced into a neighbouring
category, because silently re-categorising a borrower-credit proposal as "business" is precisely
the drift `FR-RSK-011` exists to prevent.

The **owner role** follows from the category by rule, not by model choice (architecture I.4):
security and privacy → Security Reviewer, compliance → Compliance Officer, business, technical and
operational → Project Manager. The owner of the register entry is not the decider of the gate.

## 8. Likelihood and impact (architecture I.2)

| Likelihood | Meaning |
|---|---|
| **L1 Unlikely** | Would require an unusual combination of circumstances |
| **L2 Possible** | Plausible within this project's normal course |
| **L3 Likely** | Expected unless specifically prevented |

| Impact | Meaning |
|---|---|
| **I1 Minor** | Local rework; no compliance, security or schedule consequence |
| **I2 Moderate** | Significant rework, schedule slip, or a control weakness needing remediation |
| **I3 Major** | Regulatory exposure, security compromise, or project-level failure |

Both are **explainable ordinal judgements, not probabilities**, and `FR-RSK-003` requires a written
rationale for each. A rating without its rationale is dropped. A rating the scales do not recognise
is dropped too — never guessed.

That last point is a deliberate asymmetry with P6. I.7 normalises a malformed security level
*upward* to MEDIUM, because the finding exists either way and only its level is in question. A
risk is different: an unrated proposal is not a risk assessment at all, and there is no safe
direction in which to invent a judgement. So P7 drops it, records why, and the run's counters show
what was lost.

## 9. The deterministic matrix (`FR-RSK-004`)

|  | **I1 Minor** | **I2 Moderate** | **I3 Major** |
|---|---|---|---|
| **L3 Likely** | Medium | **High** | **High** |
| **L2 Possible** | Low | Medium | **High** |
| **L1 Unlikely** | Low | Low | Medium |

Architecture I.3, unchanged. Held in **three places that must agree**, and a test asserts all
three against the table above written out again by hand:

1. `rules/data/risk_rules.yaml` — versioned data (`DQ-03`), so a rating can always name the matrix
   that produced it;
2. `domain/risk/matrix.APPROVED_CELLS` — the approved table as a code literal. The loader **refuses
   a ruleset whose matrix differs from it**, so the approved matrix is not editable by editing a
   YAML file;
3. the `risk_matrix` table, seeded from the ruleset, to which every stored severity is pinned.

The lookup is total over all nine cells, pure, and has no threshold, no default branch and no
configuration. `compute_severity` takes the two ratings and nothing else — no proposed severity, no
free text, no confidence, no override.

**Only HIGH escalates**, and exactly three cells produce it: L2×I3, L3×I2, L3×I3.

## 10. The LLM proposal schema

`agents/contracts/risk.py`. `ProposedRisk` carries the category, title, description, the two
ratings, both rationales, evidence ids, mitigation considerations and a review signal.

**It has no severity field** (`[DESIGN] D4`; architecture E #9: "the model cannot set severity
because there is nowhere to put it"). Also absent, so that a model has nowhere to put them: a risk
level or priority, a status, an approval, a gate decision, a G8 status, a lifecycle state, a
baseline, an owner role, a matrix version, a project or a risk id. `extra="forbid"` makes an output
that adds any of them schema-invalid — one bounded repair, then a recorded failure with nothing
persisted. A mitigation cannot mark itself accepted either.

The severity's absence is carried all the way through: `AcceptedRisk` (what validation hands to
persistence) has no severity, and `RiskEngine.record_risk` has **no parameter** through which a
caller — or a model behind one — could pass one. The same shape as P6's `record_finding`, for the
same reason.

## 11. Deterministic validation

`agents/validation/risk.py`, in the order architecture E #9 states, per proposal:

1. the output names the requirement it was asked about — else every risk in it is dropped
   (substitution);
2. **the `FR-RSK-011` scope guard**, checked first among the content checks so that an out-of-scope
   proposal is reported *as* out of scope rather than dropped for some incidental reason — the
   audit must be able to say the boundary held;
3. the category is one of the six;
4. title and description are non-empty;
5. likelihood is L1/L2/L3 and impact is I1/I2/I3;
6. each rating carries its written rationale;
7. at least one evidence id (`FR-RSK-006`);
8. **every** cited id was supplied to *this* call and resolves in the database — a fabricated id,
   another run's evidence or another project's evidence cannot resolve, and the claim is never
   retried (a retry invites a better-looking citation, F.2);
9. duplicates and over-limit proposals are dropped, never merged or silently truncated.

Every drop carries a stable reason code, is audited with references only, and raises a review item.
Nothing is rewritten: a claim that does not survive is dropped, not repaired.

### The scope guard (`FR-RSK-011`, architecture I.1)

Architecture I.1 puts the boundary in three places, and P7 implements all three:

1. **the category enum** admits only the six values, so there is no `credit` category;
2. **`domain/risk/scope.py`** — seven pattern families (credit scoring, creditworthiness, default
   probability, credit-loss modelling, borrower risk rating, fraud scoring, lending eligibility),
   versioned as `SCOPE_RULES_VERSION` and recorded on every risk row. It is **code, not
   configuration**: no setting disables it and no threshold tunes it down;
3. **the schema has no customer or applicant entity**, so a borrower-level risk is not merely
   refused — it is unrepresentable. A test asserts that `risk` has no `customer_id`,
   `applicant_id`, `borrower_id`, `account_id` or `loan_id` column and that no such table exists.

What it deliberately does **not** refuse matters as much: "the credit bureau integration may exceed
its latency budget", "a defect could cause valid applications to be rejected", "the fraud controls
may be insufficient", "the default risk level is set too low in configuration". Those are
legitimate technical, operational and security risks in a loan-origination project, and a guard
that blocked them would be useless. Twenty such cases are in the frozen benchmark precisely so that
over-blocking is measured, not assumed (§20).

A human-entered risk goes through the same guard: `RiskService.add_risk` raises `ScopeGuardError`
and audits the refusal.

## 12. Evidence integration (`FR-RSK-006`)

P7 creates no retrieval path. It reuses the evidence that P6's allowlisted retrieval already
recorded for the project in this run, and resolves citations through the same deterministic
evidence service, so validation checks a model's citations against exactly the evidence the call
was given.

A requirement with no evidence is **not sent to a model at all**: a proposal made without evidence
could only be dropped, and asking for one invites a fabricated citation.

The chain `requirement version → risk → evidence → mitigation → G8 task → decision` is walkable in
the database: `risk.requirement_version_id`, `risk_evidence`, `risk_mitigation`, `risk.approval_task_id`,
and the `approval_decision` the task carries.

## 13. P5 / P6 integration

Risk analysis **consumes** what earlier phases persisted; it does not re-derive them and it does
not replace them. `RiskEngine.signals_for` reads, for the exact version:

| Signal | From | Indicates |
|---|---|---|
| open quality finding | P5 | technical |
| open or under-review conflict | P5 | business |
| high-impact compliance mapping | P6 | compliance |
| compliance gap (project-wide) | P6 | compliance |
| derived security finding, with its **authoritative P6 risk level** | P6 | security |
| derived privacy finding, with its authoritative level | P6 | privacy |

These are **context, never authority**. A HIGH P6 security finding tells the analysis that security
risk is worth examining; it does not make any P7 risk HIGH, because only the matrix rates a risk,
from that risk's own two ratings.

**P6's mechanism is untouched.** `security_privacy_finding.risk_level` is still written only by
node 17, still drives G3, and P7 never writes to it. The two are related but distinct:
`SecurityRiskLevel` is the authoritative level of a *derived security/privacy requirement* (I.7);
`RiskSeverity` is the authoritative severity of a *risk item* (I.3). They are separate enums, on
separate tables, with separate gates — which is why no duplicate or contradictory record arises
from both phases running.

## 14. G8

| Property | Value |
|---|---|
| **Trigger** | The **persisted** `risk.severity = HIGH`, read from the database column by `gate_fanout` |
| **Required role** | Security Reviewer (`GATE_REQUIRED_ROLES`, from approved Phase 0 F.1's G8 row) |
| **Blocking** | Always |
| **Subject** | The risk row; the task is bound to its `content_hash` |
| **Decided at** | `POST /api/v1/approval-tasks/{id}/decide` — the one approval path |

G8 is an additional `[PROJ]` gate with **no problem-statement counterpart** (approved Phase 0
C14/C19). §16 categories 1–7 map to G1–G7; §16 category 8 (production-readiness) is emitted into
the *generated project's* SDLC workflow (`FR-WFL-003`) and is deliberately not a ReqPilot gate.
That distinction is preserved, and `Gate` still has exactly eight members.

**What the model cannot do.** It cannot trigger G8 (the gate reads a column it cannot write), cannot
suppress it (omitting a risk removes a proposal, not a gate a persisted value earns — and the P6
gates are wholly unaffected), cannot downgrade a risk to avoid it (there is no severity field, and
the matrix rates whatever ratings arrive), cannot bypass it, and cannot mark a risk approved.

**Who cannot decide it.** A wrong role, a role held only in another project, a non-human actor of
any kind, the author of the requirement the risk was identified from, and anyone presenting a task
whose subject has moved on — each is refused, by the same five checks the approval service applies
to G1, G2 and G3. A decision about a requirement version that is no longer current is refused as
`StaleApprovalError`: a later version is a different subject with its own analysis and its own gate.

Settling G8 moves the risk to `ACCEPTED` (approve) or `REJECTED` (reject) — either way a human has
looked, which is what `FR-RSK-007` requires. It **never** moves a requirement's lifecycle state:
approving a risk is not approving a requirement, and that is G1's.

## 15. The baseline block (`FR-RSK-007`, architecture I.5)

Enforced in deterministic application code, in the lifecycle guard:

```
ANALYZED → VALIDATED  refuses while  unreviewed_high_risk_count > 0
```

`RequirementService.build_context` fills that count from `RiskRepository.unreviewed_high_count`,
which reads the **persisted severity and status** — not the G8 task. That is deliberate and it
fails closed: a high risk blocks even if its task were missing or cancelled, so the block is a
property of the risk rather than of the workflow around it. A test cancels the task and asserts the
transition still fails.

The UI is **not** the enforcement point. Every one of these guarantees holds with the risk page
removed, and the tests exercise all of them without it.

Two related behaviours, both tested: accepting a risk in the *register* does not clear the *gate* —
the blocking task still stands and the transition still fails, now naming the task rather than the
risk; and after the G8 decision the same transition succeeds, with the decision as the only change.

## 16. Audit

Every meaningful P7 action is audited into the existing append-only, hash-chained log, with
**reference-only payloads** — ids, counts, codes, versions. No requirement text, no risk
description, no secret.

| Event | When |
|---|---|
| `RISK_ANALYSIS_STARTED` | a risk run begins, with the rules and matrix versions |
| `RISK_PROPOSED` | a model call's output arrives, with counts and the prompt reference |
| `RISK_DROPPED` | a proposal did not survive validation — with the reason code, the rule ids, and `scope_guard_refusal` plus the refusal notice when it was `FR-RSK-011` |
| `RISK_SEVERITY_COMPUTED` | the matrix rated a risk — both ratings, the severity, the matrix version and a deterministic one-line explanation |
| `RISK_RECORDED` | the row was persisted |
| `RISK_ESCALATED` | G8 was raised, with the severity that caused it and the required role |
| `RISK_DECISION_RECORDED` | a G8 decision, or a human register decision |
| `RISK_MITIGATION_DECIDED` | a human accepted or rejected a mitigation |

A blocked baseline transition surfaces as a `StateTransitionError` from the guard, which is what
makes it enforcement rather than advice. The chain still verifies after a full run and a G8
decision (tested on SQLite and PostgreSQL).

## 17. API and UI

Nine endpoints, the minimum P7 needs. **No request body carries a severity** — a human supplies the
two ratings exactly as a model does, and the matrix rates them.

| Endpoint | Purpose |
|---|---|
| `POST /projects/{id}/risk-runs` | run risk analysis (Analyst, `RUN_START`) |
| `GET /projects/{id}/risks`, `GET /risks/{id}` | the register entries |
| `GET /projects/{id}/risk-register` | the register plus the `FR-RSK-009` aggregate measures |
| `GET /projects/{id}/risk-register.md` | the register artefact, rendered deterministically |
| `GET /risk-matrix` | the published matrix and both scales — what makes a rating explainable |
| `POST /projects/{id}/risks` | a human adding a risk (`FR-RSK-010`) |
| `POST /risks/{id}/decision` | accept / mitigate / reject / close, with a rationale |
| `POST /risks/{id}/mitigations`, `POST /risk-mitigations/{id}/decision` | human mitigations (`FR-RSK-005`) |

G8 is decided only at `POST /approval-tasks/{id}/decide`; a test asserts that it remains the only
path ending in `/decide`.

**UI:** one page, `/ui/projects/{id}/risks` — the published matrix, the register with every entry's
ratings, rationales, computed severity, mitigations (labelled by provenance) and evidence, what
currently blocks a baseline, and the aggregate measures. Deliberately unpolished; no document
export (P8).

## 18. Migration `0009_p7_risk_register`

Additive only. No earlier migration is touched, no existing column changes, no data is destroyed,
and there is no second database. Creates `risk_matrix` (seeded with the nine approved cells),
`risk`, `risk_evidence` and `risk_mitigation`; adds the P7 audit event types and review reasons to
the existing enums; and installs four PostgreSQL trigger guards plus one deferred constraint
trigger for the evidence requirement.

Verified: up, down and up again on SQLite **and** on PostgreSQL 16.2; fresh-database migration;
project-scoped composite foreign keys; uniqueness; the severity pin; the scope/version check; the
gating check; and the G8 task relationship.

For a schema built with `create_all` rather than by migration (the offline suite), an
`after_create` hook seeds `risk_matrix` from the same ruleset — so the foreign key that pins the
severity always has its target, and the constraint holds identically in both paths.

## 19. Tests

**1,961 offline** (234 skipped — the PostgreSQL-only set — and 6 deselected `llm`). P7 adds **296
tests**: 276 that run offline, plus 20 that need a real PostgreSQL:

| File | Count | What it covers |
|---|---|---|
| `tests/unit/test_p7_matrix_and_scope.py` | 77 | every matrix cell against a hand-written copy of architecture I.3; boundaries; totality; purity; a ruleset whose matrix differs is refused; the scales; the owner rules; 23 out-of-scope and 20 in-scope guard cases |
| `tests/unit/test_p7_contracts_validation_policy.py` | 79 | the schema has nowhere to put authority; the ordered validation checks with their reason codes; policy rule 10; the routers |
| `tests/integration/test_p7_register.py` | 38 | what a run records; the database's refusals; immutability; the register views; the aggregate measures; the human half; project isolation |
| `tests/integration/test_p7_gates.py` | 27 | G8 threshold, task, roles, staleness, cross-project, self-approval, non-human actors; the baseline block and its release; version isolation |
| `tests/integration/test_p7_postgres.py` | 20 | the same guards against the real engine, by raw SQL that bypasses the ORM |
| `tests/security/test_p7_security.py` | 26 | injection; the scope guard under attack; citations; RBAC; audit; the egress-guard regression of §21 |
| `tests/integration/test_p7_evaluation.py` | 13 | the frozen benchmark's integrity on either platform, its honesty, and that it sets no target |
| `tests/workflow/test_p7_exit_test.py` | 16 | the end-to-end exit story (§23) |

The brief's §27 matrix is covered point by point: categories (A), likelihood (B), impact (C), the
matrix including every cell and the boundaries (D), proposals and forged authority (E), requirement
linkage and staleness (F), project-level risks (G), G8 in all eight listed forms (H), the baseline
in all four (I), P5/P6 integration (J), versioning (K), audit (L), authorization (M), prompt
injection (N) and the scope guard (O).

**Live PostgreSQL: 2,195 passed, 0 failed, 0 skipped.**

## 20. Evaluation

### What is and is not measured

**There is no approved numeric target for risk analysis, and P7 invents none.** Approved Phase 0
O.1 lists nine core metrics; none measures risk identification. E4 (citation / evidence
correctness) is defined as a *manual audit of a 50-claim sample* and is not computable by a
harness. The roadmap's P7 exit criteria are behavioural, not numeric.

So, following the O.1 convention established at P3 — "targets for E2–E9 will be set from measured
behaviour after P3–P7, not guessed in Phase 0" — P7 records a **first measurement** and sets no
threshold. Nothing below is a target that was met.

### The benchmark

**P7-RISK-SYNTHETIC-v1**, `data/gold/p7_risk_synthetic_v1`, manifest canonical sha256
`4c93e887b1359e7d6a351255bf06ba85d0181124a95e86c861c3deaf0715ea94`, verified by the harness before
each run. Frozen 2026-09-23 **before any evaluation run over it**. Written by the AI coding
assistant that implemented P7, **after** the implementation. Synthetic and fictional throughout; no
real person, institution, secret or key; no statement about what any law requires.

**Project-author review completed** *after* the runs below — see §25. The benchmark remains
synthetic and is not an independently validated expert gold standard. One label correction (S-21)
was identified during that post-evaluation review; **v1 remains frozen and unedited**, the
correction is reserved for a future `_v2`, and every figure below is exactly as first measured
against v1.

9 matrix cells · 43 scope cases (23 out-of-scope, 20 in-scope) · 20 adversarial cases.

### Results

| Figure | deterministic | adversarial | model (`gpt-5.6-luna`) |
|---|---|---|---|
| Matrix severity agreement | **1.00** (9/9) | 1.00 | 1.00 |
| Matrix gate agreement | **1.00** (9/9) | 1.00 | 1.00 |
| Scope guard precision / recall | **1.00 / 1.00** | same | same |
| Scope guard false-alarm rate | **0.00** (0/20) | same | same |
| Scope rule agreement | 1.00 (23/23) | same | same |
| Adversarial cases as expected | – | **20 / 20** | – |
| Risks recorded | 0 | 9 | **39** (35 requirement-level, 4 project-level) |
| Severity from the matrix | – | 9/9 | **39 / 39 = 1.00** |
| Citation resolution | – | 9/9 = 1.00 | **51 / 51 = 1.00** |
| G8 routing correct | – | 2/2 high gated, 0 non-high gated | **15/15 high gated, 0 non-high gated** |
| Proposals dropped / scope refusals | 0 / 0 | 19 / 1 | **0 / 0** |
| Blocking tasks per requirement | 0.45 | 0.55 | **4.35** (87 for 20) |

Model run: 81 provider calls, 180,660 in / 57,790 out tokens, 0 semantic failures, synthetic data
only. Severity distribution: 15 high, 24 medium, 0 low. Categories: security 14, privacy 11,
compliance 6, operational 4, business 3, technical 1.

- **The matrix figures are a correctness check, not a score.** 1.00 is the only acceptable value;
  the harness reports `correct: true` rather than treating it as performance.
- **The scope-guard figures are optimistic and are stated as such.** The cases were written by the
  author of the patterns they test, after those patterns existed. They are not an estimate of
  performance on unseen text. The 20 in-scope cases are the honest half — a guard that refused
  everything would score 1.00 on recall and be useless — and 0/20 false alarms is the figure worth
  carrying forward. The post-evaluation author review (§25) relabels one of the 23 out-of-scope
  cases as in-scope; the figures here are the v1 figures and are **not** restated for that, because
  v1 is frozen and tuning a frozen set after seeing results is the thing the freeze exists to
  prevent. §25 states what the correction would mean.
- **The deterministic run records no risk, by design.** With no model there is no rated judgement,
  and P7 does not invent one. This is a deliberate asymmetry with P6, which *does* emit a catalogue
  baseline finding so that removing the model cannot suppress a G3 gate. A risk needs a rating with
  a written rationale; fabricating one would fabricate the judgement the rating exists to record.
  The P6 gates are unaffected — 9 blocking tasks were still raised in that run.
- **Adversarial 20/20**, with three distinct schema refusals and ten distinct drop reasons
  observed, including the `FR-RSK-011` refusal. Both "obeyed injection" attacks produced a HIGH
  risk with a blocking G8 task, because the matrix rates the ratings and prose has no authority.
- **In the model run every severity was the matrix's** (39/39) and every citation resolved
  (51/51), with no proposal dropped. The model rated generously: **15 of 39 risks came out HIGH**,
  and none came out LOW, which drove the reviewer load below.
- **Reviewer load is the main finding.** 87 blocking gate tasks for 20 requirements — 4.35 per
  requirement — of which 15 are P7's G8 and the rest P6's G2/G3. Nothing is suppressed and every
  one is a real unreviewed item, but a reviewer facing 87 blocking tasks on a 20-requirement
  project is the practical problem P8's triage design has to answer. P6 measured 74 for the same
  project; P7 adds 13 on top.

Reports: `docs/evaluation/p7-risk-synthetic-v1/{deterministic,adversarial,model}.json`.

### What a reader must not conclude

Not that ReqPilot identifies real project risks well — that is not measured anywhere, because
there is no expert-produced reference risk list and inventing one would fabricate the judgement
being measured. Not that the scope guard would hold on unseen text. Not that any number here is an
approved threshold.

## 21. Known limitations

1. **No measure of risk quality.** Whether the risks identified are the *right* risks is not
   assessed. Only what deterministic code does with whatever is proposed is measured.
2. **The scope guard is lexical.** It matches phrase patterns, not meaning. A borrower-credit
   proposal phrased in words no pattern covers would pass the guard — though it would still have to
   fit one of the six categories, still have to cite this project's evidence, and still be
   reviewable in the register. Conversely a legitimate risk phrased unusually could be refused; the
   20 in-scope cases bound that risk on the cases tested and no further.
3. **The deterministic run records nothing.** A project that has never had a model run has an empty
   register. That is honest rather than degraded, but it does mean P7 contributes no baseline
   blocking of its own without a model.
4. **Reviewer load.** In the adversarial run 11 blocking tasks were raised for 20 requirements
   (0.55 per requirement), most of them P6's. P6 measured 74 tasks for 20 requirements with a real
   model; P7 adds G8 on top of that. Triage design remains P7's main input to P8.
5. **Project-level risks are one pass.** The project-level analysis sees a deterministic summary of
   the set, capped at the run's requirements; it is not an exhaustive cross-requirement analysis.
6. **The model rates generously, and never LOW.** In the live run 15 of 39 risks were HIGH and 24
   MEDIUM, with no LOW at all. Whether that reflects the requirement set or a tendency of the model
   is not determinable from one run over one synthetic project, and it is not measured here. It is
   the direct cause of the reviewer load in §20.
7. **An egress defect found by the live run, and fixed.** The `prior_findings` content block was
   project content that did not declare its masking and synthetic facts, so the gateway's egress
   guard correctly refused to send it to a real provider — which the offline suite could not catch,
   because the stub provider never leaves the machine. The guard did its job: nothing left the
   machine, the refusal was recorded, and the deterministic half of the run continued. Fixed by
   threading the facts through, with a regression test that asserts every project-content block the
   role sends declares them *and* that the guard still refuses one that does not.

## 22. Deviations

Two, both recorded rather than silently taken. Neither changes the approved architecture.

**D-1 — "a DB CHECK against `risk_matrix`" is implemented as a composite foreign key.**
Architecture G.6 says `severity` "has a DB CHECK against `risk_matrix`". A SQL `CHECK` constraint
cannot reference another table, so the literal reading is not implementable. The constraint is
expressed as the foreign key that *can* — `(matrix_version, likelihood, impact, severity)` into
`risk_matrix` — which is strictly stronger than a check against a copied value: it makes a row
whose severity is not the matrix's value for its own cell non-existent rather than merely invalid.
This is an implementation-level solution to an architecture-level intent, as the brief's §30
prefers. It affects no later phase.

**D-2 — P7 has no rule-engine fallback risk, where P6 has a catalogue baseline finding.**
P6 emits a baseline finding for every indicated family so that omitting one cannot suppress a G3
gate. P7 deliberately does not do the equivalent: a risk requires a rated judgement with a written
rationale, and manufacturing one deterministically would fabricate exactly what the ratings exist
to record. The consequence — a model-free run records no risk — is visible in the run's counters
and is documented in §20 and §21. The P6 escalations are unaffected. This is a P7 decision, not an
architecture change; `RiskEngine.baseline_risk` exists solely to state it in code.

`docs/02-architecture.md` was **not modified**.

## 23. Roadmap exit decision

| # | P7 exit criterion (`docs/01-analysis.md` §P) | Evidence | Met |
|---|---|---|---|
| 1 | Risk level is computed by the matrix, not the LLM (unit-tested) | 9/9 cells unit-tested against I.3; no severity field in the contract, the accepted object or the engine's signature; the database pins every stored severity to its matrix row, tested by raw SQL on PostgreSQL | ✓ |
| 2 | Every risk links to a requirement and evidence | See the note below on the two risk scopes. `requirement_version_id` (exact version) or project scope, `evidence_count >= 1`, `risk_evidence` with composite FKs, a deferred trigger at commit; 9/9 citations resolved in the adversarial run | ✓ |
| 3 | A high-severity risk **blocks** baseline approval | `ANALYZED → VALIDATED` refuses while an unreviewed HIGH risk exists; the transition itself raises; the block survives a cancelled G8 task; it clears only after the G8 decision | ✓ |
| 4 | `FR-RSK-011` scope guard test passes | 23/23 borrower-credit cases refused, 0/20 false alarms; refusals audited with rule ids; the category enum and the absent customer entity close the other two routes | ✓ |

**Criterion 2 and the two risk scopes.** The authoritative wording in `docs/01-analysis.md` §P is
*"every risk links to a requirement and evidence"*, and it is preserved above unchanged. It is read
together with `FR-RSK-001`, which explicitly requires "risks associated with each requirement, **and
project-level risks arising from the requirement set as a whole**", and `FR-RSK-006`, which requires
the link to evidence for every risk. P7 therefore satisfies criterion 2 as:

> **Every requirement-level risk links to its exact requirement version and evidence; every
> project-level risk links to the project and evidence.**

No risk of either scope can exist without evidence, and no risk can exist without the scope's own
subject: the database check requires a `requirement_version_id` for a requirement-level risk and
forbids one for a project-level risk (§6). In the model run this was 35 requirement-level and 4
project-level risks, all 39 with evidence.

Supporting: offline 1,961 passed / 0 failed; PostgreSQL 2,195 passed / 0 failed / 0 skipped; the
P7 exit test's **16 tests** (14 test functions, one parametrised over three roles); all quality
gates clean; P0–P6 unchanged — 14 lifecycle states, no `CONFLICTED` state, G1 still Analyst +
Compliance Officer, eight gates, and the E1, P5 and P6 frozen benchmarks all verifying against
their unchanged manifests.

**P7 COMPLETE — ROADMAP EXIT PASSED — PROJECT-AUTHOR REVIEW COMPLETED.** The evaluation figures are
first measurements on a synthetic benchmark written after the implementation and reviewed by the
project author only after the runs (§25). They demonstrate the machinery and its controls; they are
not evidence of real-world risk-analysis accuracy.

## 24. Deferred work

- **P8**: the approval fan-out beyond G8 (G4, G5), the traceability matrix, SRS, user stories, use
  cases, the compliance matrix, DOCX export. The register's structured views are shaped to be
  rendered by P8 without redesign.
- **P9**: SDLC factor scoring, MCDA, ranked recommendations, explanations. P7 exposes the I.6
  factor *inputs* and stops there.
- **P10**: the generated SDLC workflow.
- **P11**: masking (`FR-ING-003` — until it exists, non-synthetic project content cannot reach an
  external model, as §21.7 showed), the full adversarial suite, production hardening, retention and
  deletion, the audit replay UI.
- **P12**: the final multi-case-study evaluation, packaging and report.
- **P7-RISK-SYNTHETIC-v2**, carrying the one correction the project author's post-evaluation review
  identified (S-21 → `in_scope`, §25). v1 stays frozen; v2 is created when there is a reason to
  re-measure, and results from the two versions are not comparable case-for-case.
- **A non-lexical scope guard** and an independent review of its cases (§21.2).

## 25. Benchmark review status (2026-09-23, post-evaluation)

Recorded after the runs of §20. **No benchmark file, label, manifest, evaluation result,
implementation file, test or migration was changed by this review.** The measured figures in §20
stand exactly as first measured against v1.

### 25.1 Project-author review

> **Project-author review completed. The benchmark remains synthetic and is not an independently
> validated expert gold standard. S-21 was identified as a label correction during post-evaluation
> author review; v1 remains frozen and the correction is reserved for v2.**

It was the **project author's own review** — not an independent review, not an expert or specialist
review, and not any form of regulatory or legal validation. The review covered the nine matrix
cases, the 43 scope-guard cases and the 20 adversarial cases, with their labels and expected
outcomes.

| Group | Decision |
|---|---|
| Matrix cases (9) | Reviewed; **no corrections**. |
| Adversarial cases (20) | Reviewed; **no corrections**. |
| Scope cases (43) | Reviewed; **one correction**, S-21, below. S-22 and S-23 were examined and **kept** as labelled. |
| Anything else | No other substantive corrections identified. |

### 25.2 The three examined scope cases

| Case | Text (as frozen in v1) | v1 label | Author decision |
|---|---|---|---|
| **S-21** | "Underwriting decisions may be inconsistent between reviewers." | `out_of_scope` | **Change to `in_scope`** — it describes inconsistency between the project's own underwriting reviewers, which is a project/operational risk rather than a borrower-level judgement. |
| **S-22** | "The credit eligibility model needs retraining." | `out_of_scope` | **Keep `out_of_scope`** — the intended boundary treats the credit-eligibility model's lending-decision behaviour as outside ReqPilot's risk domain. |
| **S-23** | "Risk-based pricing could disadvantage some segments." | `out_of_scope` | **Keep `out_of_scope`** — for this benchmark the statement concerns borrower/customer pricing consequences rather than a project/engineering risk. |

### 25.3 Why v1 is not edited, and what the correction would mean

v1 was frozen **before** it was evaluated, precisely so that no label could be changed after seeing
a result. Editing it now would break its manifest hash, invalidate every committed report, and do
the exact thing the freeze exists to prevent. So S-21 is recorded here as a **post-evaluation
author-review correction reserved for `_v2`** (§24), and the §20 figures are left alone.

Stated plainly, so the correction is not quietly lost: under the author's label, the current
`FR-RSK-011` guard **refuses S-21**, which the author considers an in-scope project risk. In a v2
with 22 out-of-scope and 21 in-scope cases, and with today's unchanged patterns, that would read as
recall 22/22 and a false-alarm rate of 1/21 (≈ 0.048) rather than 0/20 — one legitimate project
risk wrongly refused. That is the honest direction of the correction, and it is a **projection, not
a measurement**: no run was made against a v2, because no v2 exists.

**No P7 code was changed to accommodate these labels.** The guard's patterns are not being tuned to
a label decided after the evaluation; the divergence is recorded and left to the deferred work on a
non-lexical guard (§21.2, §24), where it belongs with the underlying design question rather than
with a pattern edit aimed at one case.

### 25.4 What this review does not change

- The benchmark is still **synthetic**, still written by the AI coding assistant that implemented
  P7, still written **after** the implementation, and the author's review of it is still not an
  independent or expert validation.
- The **scope-guard figures remain optimistic** for the reason given in §20 — the cases were written
  by the author of the patterns they test — and the review does not make them an estimate of
  performance on unseen text.
- **No numeric target** exists or is created. §20's figures remain first measurements in the O.1
  sense.
- **Risk quality is still not measured** anywhere (§21.1).
- The **reviewer-load finding** stands as measured: 87 blocking gate tasks for 20 requirements.
- The frozen `manifest.json`, `BENCHMARK.md` and `REVIEW_SHEET.md` still say the review is pending,
  because they record the state at freeze time; editing them would change the manifest hash. This
  section is the later record, following the same provenance approach as `docs/09` §28.
