# ReqPilot — P9 SDLC Recommendation

**Roadmap phase:** P9 (`docs/01-analysis.md` §P) · **Modules:** M3 (`sdlc_graph`; role #10 SDLC
Recommendation), M7 (`sdlc_rules.yaml`, the versioned ruleset and weights), M6 / M9 (gate G6 on the
one approval service), M5 (the P9 trace edges), M1 (the SDLC pages), M12 (the E9 harness)

**Status: P9 COMPLETE — ROADMAP EXIT PASSED — PROJECT-AUTHOR REVIEW COMPLETED**

*2026-09-27.* Everything the P9 roadmap row lists as a key deliverable is implemented and tested:
the factor profile with evidence (including the P7 risk aggregates), human overrides, deterministic
rules plus weighted MCDA, a ranked output, the LLM explanation with counter-arguments, and G6
four-role co-approval. All three approved exit criteria are met (§16). Criterion 2 —
*"E9 computed against the blind expert panel"* — is met through **deviation P9-3, approved by the
project author** (§17): E9 was computed against a **synthetic, AI-generated panel**, not the "≥ 3
experts" Phase 0 O.1 describes. The panel is not a real or independently validated expert panel,
and E9 = 0.167 measures agreement with that synthetic panel only.

> **The LLM proposes; deterministic code disposes.** In P9 a model may propose a bounded adjustment
> to some factor scores and may explain a ranking that already exists. It never chooses the SDLC.
>
> - the **factor profile** is derived by code from approved, persisted records; the four
>   risk-derived factors cannot be proposed at all, and any other proposal may move a derived score
>   by at most one point;
> - the **ranking** is weighted MCDA plus a fixed-order rule pass over versioned data, persisted
>   **before** any explanation exists;
> - the **explanation** is checked field by field against the persisted ranking; a discrepancy is
>   stored and shown, never silently repaired, and never changes the ranking;
> - the **selection** is recorded only when all four G6 roles approve, and it is always the
>   computed first candidate — the database refuses any other.

---

## 1. P9 status

| Item | Result |
|---|---|
| **Implementation** | Complete: FR-SDL-001 … FR-SDL-008 (§4). FR-SDL-009 (secondary tier) deferred |
| **Offline suite** | **2,186 passed**, 254 skipped (PostgreSQL-only), 6 deselected (`llm`), **0 failed** |
| **PostgreSQL suite** | **2,440 passed**, **0 failed**, **0 skipped**, 6 deselected — existing test database, migrated to head |
| **P9 exit test** | `tests/workflow/test_p9_exit_test.py` — one 17-step story test (§13) |
| **Migration** | `0011_p9_sdlc_recommendation`, additive, `down_revision = 0010_p8_trace_documents` (§10) |
| **E9** | **2 / 12 = 0.167** top-choice agreement with a **synthetic AI-generated panel** (§14). No target exists; none is claimed |
| **Benchmark review** | P9-SDLC-SYNTHETIC-v1 — **project-author review completed**, no substantive corrections (§15) |
| **Roadmap exit** | **Passed.** Criteria 1 and 3 met directly; criterion 2 met through author-approved deviation P9-3 (§16, §17) |

## 2. Scope

**In:** the thirteen `[PS §13]` factors derived from the approved baseline and the governed risk
register, each with evidence and provenance; bounded LLM factor proposals; human overrides with a
recorded reason; seven candidates (`[PS §14]`'s five models and the two hybrids it names); weighted
MCDA and four declarative post-MCDA rules; a persisted, reproducible ranking with reversal
analysis; the LLM explanation and its consistency check; counter-arguments; G6; the P9 trace edges;
audit; the API and UI; the E9 harness and benchmark.

**Out:** P10 workflow generation (no workflow is generated anywhere in P9), sensitivity analysis on
the weights (`FR-SDL-009`, secondary tier, §19), P11 hardening and masking, P12 final evaluation.

## 3. Source hierarchy

The problem statement, then `docs/01-analysis.md` (approved Phase 0 analysis and roadmap), then
`docs/02-architecture.md` (not modified in P9), then the earlier phase reports. Provenance tags as
Phase 0 defines them: `[PS §n]`, `[P0 §x]`, `[DESIGN]`, `[PROJ]`, and **P9** for a decision taken
in this phase. `sdlc_rules.yaml` states the provenance of every number it holds: no approved source
fixes a weight, coefficient, band, keyword or uplift, so each is `[PROJ]` and reviewable as data.

## 4. FR-SDL requirement coverage

| Requirement | Tier | Status | Where |
|---|---|---|---|
| **FR-SDL-001** Derive the thirteen-factor profile, each with evidence | MVP | **Implemented** | `domain/sdlc/derivation.py`, `services/sdlc/evidence.py`; `sdlc_factor` keeps score, provenance and evidence per factor |
| **FR-SDL-002** Consume the risk register's aggregates as factor inputs | MVP | **Implemented** | P7's I.6 formula function (`services/risk/register.py`) feeds security risk, consequences of failure, regulatory criticality and project complexity; these four cannot be model-proposed |
| **FR-SDL-003** Human override of any factor, with reason recorded | MVP | **Implemented** | `POST /sdlc-runs/{id}/factors/{factor}/override`; the table check requires reason, actor, role, time and previous score; an overrider may not sign G6 |
| **FR-SDL-004** Score candidates by deterministic rules plus weighted MCDA | MVP | **Implemented** | `domain/sdlc/scoring.py`; rules R1–R4 in `sdlc_rules.yaml` |
| **FR-SDL-005** Ranked output with suitability scores | MVP | **Implemented** | `sdlc_candidate` holds every candidate's score and rank |
| **FR-SDL-006** Explanation grounded strictly in derived factors and evidence; the LLM does not select | MVP | **Implemented** | `agents/roles/sdlc.py`, `domain/sdlc/consistency.py` (§8) |
| **FR-SDL-007** Counter-arguments: why not the runner-up, what would reverse it | MVP | **Implemented** | reversal analysis re-runs the pipeline per factor; the check requires the runner-up and a reversal condition |
| **FR-SDL-008** G6 multi-role approval: PM, Architect, Security, Compliance | MVP | **Implemented** | `services/sdlc/gates.py` on the one approval service (§9) |
| **FR-SDL-009** Sensitivity analysis on the weights | SEC | **Not implemented** | Not a P9 roadmap deliverable; deferred (§19) |

## 5. Architecture

`sdlc_graph` (architecture C.5), five nodes:

```
START -> collect_factor_evidence -+-> propose_factor_scores -> apply_rules_and_mcda
                                  |        -> generate_explanation -+-> raise_g6 -> END
                                  |                                 +-> END (no explanation yet)
                                  +-> generate_explanation            (mode "explain": a retry)
                                  +-> END                             (inputs not approved)
```

Every edge is deterministic: routers read flags set from persisted, validated values, never model
text. `apply_rules_and_mcda` persists the ranking before `generate_explanation` runs, so an
explanation can only describe a ranking that already exists (architecture L.5). G6 is raised only
once an explanation is stored, and no node decides it. The graph runs through `graph/sdlc_runner.py`.

## 6. The factor profile

Thirteen factors (`[PS §13]`), each scored on 1–5 (`[DESIGN] D11`) with written anchors in the
ruleset. **Derivation** (`domain/sdlc/derivation.py`) maps approved, persisted facts — the
baseline's requirement counts and categories, revision and conflict history, compliance mappings
and gaps, and P7's risk aggregates — onto each factor through versioned bands; the highest band
reached wins. Each factor records its provenance and the evidence references that produced it.

**Risk-derived factors** — security risk, consequences of failure, regulatory criticality and
project complexity — come from the governed risk register and are **not proposable**: a model
proposal for one is rejected (`RISK_DERIVED`). For the other nine, role #10 may propose a score,
which validation accepts only within `max_proposal_deviation = 1` of the derived score, with a
rationale and with evidence it was actually supplied (rejections are named: `OUT_OF_BOUNDS`,
`INVALID_SCORE`, `UNSUPPLIED_EVIDENCE`, `NO_RATIONALE`, `NO_EVIDENCE`, `DUPLICATE`).

**Overrides** (`FR-SDL-003`) are human-only (policy rule 12), may set any factor, and must record
reason, actor, role, time and the previous score.

## 7. Scoring: MCDA and the rule pass

Weighted MCDA (architecture L.3), per candidate `c`:

```
raw(c)  = Σ_f  w[f] · S[c][f] · norm(score[f])      norm: 1..5 → −1..+1
max(c)  = Σ_f  w[f] · |S[c][f]|
mcda(c) = 50 · (1 + raw(c) / max(c))                 0..100; 50 when max(c) = 0
```

Coefficients `S[c][f]` lie in −2..+2 and encode the direction of the `[PS §14]` table. Normalising
by the candidate's own attainable range is the P9 reading of L.3's "normalise raw to 0–100" (§17).

The rule pass (architecture L.4, `[DESIGN] D18`), in fixed order and each triggered rule recorded
whether or not it changed anything: **boost** (bounded uplift, capped at 100) → sort, ties by the
versioned candidate order → **veto** (a vetoed candidate cannot rank first) → **require_top_n**
(never above a veto). A veto or top-n rule never alters a score, so the MCDA result and the rule
effect both stay visible.

| Rule | Effect | Condition |
|---|---|---|
| R1 | veto pure Waterfall | regulatory criticality ≥ 4 and requirement stability ≤ 2 |
| R2 | a V-Model candidate in the top 2 | formal verification = 5 and consequences of failure = 5 |
| R3 | boost DevSecOps candidates by 5 | security risk = 5 |
| R4 | boost hybrids by 5 | regulatory criticality ≥ 4 and expected frequency of change ≥ 4 |

R1–R3 are architecture L.4's example rows; R4 is the `[PS §14]` row for high regulation with
evolving requirements. Both uplifts are `[PROJ]`. **Reversal analysis** (`FR-SDL-007`) finds, per
factor, the smallest single change that would put the runner-up first, by re-running this pipeline.

The ruleset is `sdlc_rules@1.0.0`, canonical sha256
`41730a927957ecabd369f2ee6dc3aca3da242b1e5201d83a7fb6685643340904`. Every run records its
ruleset version, weights version and content hash, so a change never silently re-ranks a
recommendation already computed or approved.

## 8. The explanation and its consistency check

Role #10 explains the persisted ranking. Its contract has **nowhere to put** a ranking, a selection,
weights or a G6 decision (`extra="forbid"`). Its draft carries non-authoritative assertions — the
candidate it believes is first and the scores it believes were computed — so that
`domain/sdlc/consistency.py` can compare them with the persisted result. The check names every
disagreement: `TOP_MISMATCH`, `SCORE_MISMATCH`, `TOP_SCORE_MISSING`, `UNKNOWN_CANDIDATE`,
`RUNNER_UP_NOT_ADDRESSED`, `REVERSAL_MISSING`, `COUNTER_ARGUMENT_ON_TOP`, `NO_FACTOR_CITED`,
**`UNKNOWN_FACTOR_CITED`** (a cited factor that is not one of the thirteen derived factors),
**`UNSUPPLIED_EVIDENCE_CITED`**, `MODEL_SELECTION_CLAIM` ("I recommend", "the AI selected") and
`UNSUPPORTED_NUMBER`. Citations are read from the structured fields and from inline markers in the
prose, so a claim cannot slip past by living only in the narrative.

A discrepancy triggers one regeneration (architecture L.5); if it persists, the explanation is
stored **with** its discrepancies and shown. It never changes the ranking. This check **detects and
surfaces** an explanation that cites something other than derived factors; it does not suppress
the explanation.

## 9. G6

Subject: an `sdlc_run`. Co-approval by **Project Manager, Architect, Security Reviewer and
Compliance Officer** — one task per role in one group (`GATE_REQUIRED_ROLES`,
`GATE_REQUIRES_ALL_ROLES`). The approval service's five checks apply unchanged. Specific to G6:

- **Binding:** the run's recommendation hash (ranking and explanation as stored), recomputed at
  decision time; a superseded run, or one no longer awaiting G6, is refused as stale.
- **Self-approval:** whoever started the run and whoever overrode one of its factors may not sign.
- **Settlement:** only when all four approve is the selection recorded, always the computed first
  candidate. One REJECT rejects the run; one MODIFY requests revision; either cancels the remaining
  tasks. A new selection supersedes an earlier one. G6 approves a selection and never moves a
  requirement's lifecycle state.

`ARCHITECT` is a new role (§17, deviation P9-1).

## 10. Data model and migration `0011_p9_sdlc_recommendation`

Additive; no earlier migration touched, no existing column changed. `sdlc_run`, `sdlc_factor`,
`sdlc_candidate`, `sdlc_rule_application` (architecture G.8). A run names the exact approved
baseline it was computed from through a composite foreign key (so only a baseline of its own
project), pins ruleset and weights versions and the ruleset hash, and keeps the whole profile,
every rule effect and every candidate's score and rank. Factors, candidates and rule applications
are **append-only**; a run's inputs and ranking never change after insertion, its explanation and
selection are written once, and only its status moves — enforced by PostgreSQL triggers and an ORM
guard. `sdlc_factor` checks the 1–5 scale and the override record. On PostgreSQL the migration adds
the P9 audit event types and the `ARCHITECT` role (neither removed on downgrade: PostgreSQL cannot
drop an enum value, and audit history is kept) and widens `traceability_link`'s allowlist; the
downgrade restores the P8 allowlist, deleting any P9 edges first.

## 11. Traceability

New node types `sdlc_run`, `sdlc_factor`, `sdlc_candidate`. Edges: risk → factor (N.2 #20),
requirement version → factor (#21), factor → candidate (#22), run → approval decision (#23), plus
compliance mapping → factor and conflict → factor (P9), so every factor's evidence is navigable.
N.2 #24–#26 belong to P10 and are absent.

## 12. API, UI and policy

| Method | Path |
|---|---|
| POST | `/api/v1/projects/{project_id}/sdlc-runs` — start a run from an approved baseline |
| GET | `/api/v1/projects/{project_id}/sdlc-runs` |
| GET | `/api/v1/sdlc-runs/{run_id}` |
| POST | `/api/v1/sdlc-runs/{run_id}/factors/{factor_id}/override` |
| POST | `/api/v1/sdlc-runs/{run_id}/explanation` — ask again for an explanation |

G6 is decided only at the existing `POST /approval-tasks/{id}/decide`. UI: `sdlc.html` (runs),
`sdlc_run.html` (profile, ranking, rule effects, explanation with any discrepancies, G6 tasks).
Policy rule 12: `SDLC_RUN_START`, `SDLC_RECORD` (Analyst); `SDLC_FACTOR_OVERRIDE` (Analyst,
Project Manager); `SDLC_EXPLAIN` (Analyst); `SDLC_READ`. Starting, overriding and explaining are
human-only; nobody decides G6 except through the approval service.

## 13. Tests

73 P9 tests in eight files:

| File | Tests |
|---|---|
| `tests/unit/test_p9_domain.py` | 22 |
| `tests/unit/test_p9_scoring.py` — hand-computed scoring | 13 |
| `tests/integration/test_p9_sdlc_flow.py` | 17 |
| `tests/integration/test_p9_postgres.py` (PostgreSQL only) | 7 |
| `tests/integration/test_p9_evaluation.py` | 5 |
| `tests/integration/test_p9_api_and_ui.py` | 3 |
| `tests/security/test_p9_security.py` | 5 |
| `tests/workflow/test_p9_exit_test.py` — one 17-step story | 1 |

**Results** (2026-09-26, after the §20.1 fixes):

| Gate | Result |
|---|---|
| Offline suite | **2,186 passed**, 254 skipped (PostgreSQL-only), 6 deselected (`llm`), **0 failed** |
| PostgreSQL suite | **2,440 passed**, **0 failed**, **0 skipped**, 6 deselected — PostgreSQL 16.2 + pgvector 0.6.2, an existing local test database migrated to head |
| P9 tests | 73; offline 66 passed + 7 PostgreSQL-only skipped; on PostgreSQL all 73 pass |
| Migration 0011 | up / down to 0010 / up on PostgreSQL (CLI); SQLite via `tests/integration/test_migrations.py` (14 passed) |
| `ruff format --check` / `ruff check` | 441 files clean / clean |
| `mypy` | 271 source files, clean |
| `lint-imports` | 6 contracts kept, 0 broken |
| Provider SDK guard | `openai` (all approved) |
| Frozen benchmarks | P7, P8 and P9 verify through their harnesses; P9 manifest `3486a56f…` unchanged |

Before the §20.1 fixes, the first offline run had **2 failures** (`tests/integration/test_p8_evaluation.py`, the frozen P8 fixture) and a P9 security test that failed intermittently.

The P9 LLM roles were exercised only with the scripted P9 model. **No real-model run of role #10 is
recorded for P9**, and E9 exercises the deterministic path only (§14).

## 14. E9 — methodology and result

**Definition** (Phase 0 O.1): *SDLC recommendation agreement — top-ranked model vs a blind expert
panel — ≥ 3 experts judging **before** seeing system output* (`[PS §19]`).

**Benchmark:** P9-SDLC-SYNTHETIC-v1, `data/gold/p9_sdlc_synthetic_v1`, manifest sha256
`3486a56fcb6cb44aaca340f27680e5b172d417b7e874a071db3a900922a753d5`. Twelve fictional
financial-sector cases (two each: digital banking, loan processing, payment systems, fraud
detection, insurance, regulatory reporting); **five AI-generated expert personas**, each a separate
Claude ("sonnet" tier) sub-agent with a fresh context and no tools or file access; 60 full rankings
of the seven candidates. The panel was **blind**: no persona saw ReqPilot's rules, weights, scores,
ranking or explanation, any expected answer or another persona's answer. The ruleset and its
hand-computed tests were fixed **before** the cases and the panel were generated, and the benchmark
was frozen **before** ReqPilot was run on any case.

**Protocol:** verify the manifest; re-derive the panel aggregate (Borda count over full rankings;
ties by first-place votes, then candidate order) and require it to equal the frozen one; run
ReqPilot's deterministic pipeline on each case (P7 matrix, I.6 aggregates, P9 derivation, MCDA,
rules — no model call); report top-1 agreement and the supplementary figures.

**Result** — exactly as committed in `docs/evaluation/p9-sdlc-synthetic-v1/`, not re-run for this
report:

| Measure | Value |
|---|---|
| **E9 top-choice agreement** (ReqPilot first = panel Borda first) | **2 / 12 = 0.167** |
| ReqPilot first within the panel's top 2 | 5 / 12 = 0.417 |
| Panel first within ReqPilot's top 2 | 3 / 12 = 0.250 |
| ReqPilot first = a panel plurality choice | 2 / 12 = 0.167 |
| ReqPilot first preferred by at least one panellist | 4 / 12 |
| Mean Kendall's τ / Spearman's ρ / pairwise order agreement | 0.4365 / 0.5327 / 0.7182 |
| Panel consensus strength (mean) / panel Kendall's W (mean) | 0.80 / 0.9031 |

Agreement on C03 (`agile`) and C05 (`agile_v_model_hybrid`); the other ten cases disagree. First
choices — ReqPilot: `agile_v_model_hybrid` 10, `devsecops` 1, `agile` 1; panel: `v_model` 6,
`agile_devsecops_hybrid` 2, `spiral` 2, `agile` 1, `agile_v_model_hybrid` 1.

The review sheet asks for E9 with and without the low-consensus cases. From the committed per-case
table, without recomputation: the eight cases with consensus ≥ 0.8 agree **1 / 8** (C03); the four
at 0.6 (C05, C09, C10, C12) agree **1 / 4** (C05). The low agreement is not an artefact of weak
panel consensus.

**Reading it.** The disagreement is systematic, not noise: ReqPilot ranks the Agile-V-Model hybrid
first in 10 of 12 cases, while the panel prefers plain V-Model in 6. R4 (a +5 uplift to hybrids
when regulation and change are both high) fired in 5 of the 10 disagreement cases (C02, C04, C08,
C10, C12), and R2 requires only a V-Model-*containing* candidate in the top two, which the hybrid
satisfies. But no rule fired at all in three disagreements (C06, C07, C09), and the hybrid was
still first, so the preference comes from the MCDA coefficients as well as from R4. A plausible
reading is that the `[PROJ]` coefficients and uplifts favour hybrids more than this panel does.
**That is an observation, not a finding acted on**: nothing was tuned after the comparison, and a
change to the ruleset would be a new ruleset version evaluated on a new benchmark version.

**What E9 here is not.** It measures agreement with a synthetic simulation of expert judgement, not
with experts. The personas and ReqPilot's coefficients may share the same textbook view of SDLC
models, so agreement could overstate what a human panel would show; here agreement is low, and that
disagreement is the more informative signal. **No target exists** (O.1: targets for E2–E9 come from
measured behaviour), so 0.167 is a first measurement, not a threshold missed or met.

## 15. Benchmark review status

> **Project-author review completed. The project author reviewed the P9-SDLC-SYNTHETIC-v1 benchmark
> materials and identified no substantive corrections. The benchmark remains synthetic: its cases
> and its panel are AI-generated, and it is not independently validated or expert-validated. No
> real expert reviewed it.**

It was the **project author's own review** — not an independent review, and not a review by
software architects, project managers or any other experts. Because no correction was identified,
no `_v2` was created and v1 stays frozen. The frozen `manifest.json`, `BENCHMARK.md` and
`REVIEW_SHEET.md` still say "not reviewed by the project author at freeze time", because they record
the state at freeze; editing them would change the manifest hash. This section is the later record,
following the provenance approach of `docs/09` §28 and `docs/10` §25.

What the review does **not** change: the E9 figures (§14), or the fact that the panel is
AI-generated — reviewing a synthetic panel does not make it an expert panel. The roadmap exit rests
on a separate, explicit decision: deviation P9-3 (§16, §17).

## 16. Roadmap exit assessment

| # | P9 exit criterion (`docs/01-analysis.md` §P) | Evidence | Met |
|---|---|---|---|
| 1 | Scoring unit tests pass on hand-computed cases | `tests/unit/test_p9_scoring.py`: MCDA, normalisation, ties, veto, boost cap, require-top-n, veto precedence, reversal, bands and rounding, and the packaged ruleset computed by hand for one candidate; exit step 6 recomputes the MCDA by hand | **Yes** |
| 2 | E9 computed against the blind expert panel | E9 **computed** (2/12) against a **blind** panel of five AI-generated personas, which fills the expert-panel slot under **deviation P9-3, approved by the project author** (§17) | **Yes — by approved deviation** |
| 3 | The justification cites only derived factors | `UNKNOWN_FACTOR_CITED` and `UNSUPPLIED_EVIDENCE_CITED` unit-tested; exit steps 8–9 assert the explanation follows the ranking with **zero** discrepancies; the contract cannot carry a selection | **Yes** |

**Decision: P9 COMPLETE — ROADMAP EXIT PASSED.** Criteria 1 and 3 are met directly. Criterion 2's
approved wording names an expert panel; the project author has explicitly approved using the
existing synthetic, AI-generated panel in that slot (**deviation P9-3**, §17), following the P3
precedent (`docs/06` §20.6, deviation 1), where a synthetic benchmark filled an exit slot only by
an explicit author-approved deviation.

The deviation closes the **roadmap criterion**; it does not change what E9 measures. E9 = 2/12 =
0.167 is agreement with an AI-generated simulation of expert judgement. It was not re-run for this
decision, and it is not evidence of real-world SDLC recommendation accuracy (§14, §18). Nothing is
outstanding for the P9 exit.

*History:* the first version of this report (2026-09-26) recorded the exit as **not passed**,
pending this decision. The approval was given on 2026-09-27; nothing else changed between the two
versions.

## 17. Deviations

- **P9-1 — an eighth human role, `ARCHITECT`.** Phase 0 F.1 merges "Project Manager / Software
  Architect" into one actor, but G.14, M.3, `FR-SDL-008` and `[PS §14]` name the architect as a
  separate G6 approver. Four distinct `role_exercised` values need four roles, so P9 adds
  `ARCHITECT`: a G6 approver, with the Project Manager's read access (so it can see what it signs)
  and nothing that authors, manages or records. The role-count invariant test moved from seven to
  eight.
- **P9-2 — MCDA normalisation.** Architecture L.3 says "normalise raw to 0–100" without fixing how.
  P9 normalises by the candidate's own attainable range, so a score is comparable across projects.
- **P9-3 — E9 against a synthetic panel. Approved by the project author on 2026-09-27.** Phase 0
  O.1 defines E9 against a blind panel of at least three experts judging before seeing the system's
  output, and the P9 exit criterion reads "E9 computed against the blind expert panel". ReqPilot is
  a solo project with no access to such a panel, and the project author explicitly approved using
  the existing P9-SDLC-SYNTHETIC-v1 panel instead: five AI-generated expert personas, each a
  separate AI sub-agent, blind to ReqPilot's rules, weights, scores and output. What the deviation
  keeps is the E9 *protocol* — several independent judges deciding before and without seeing the
  system's output. What it gives up is the E9 *meaning*: **the panel is synthetic and AI-generated,
  not a real expert panel and not independently validated**, so E9 = 0.167 measures agreement with
  that synthetic panel and nothing more. The benchmark, its manifest and the E9 result are
  unchanged by the approval.
- **Not a deviation, but stated:** every weight, coefficient, band and uplift is `[PROJ]`; no
  approved source fixes them. `docs/02-architecture.md` was not modified.

## 18. Limitations and caveats

1. **E9 is agreement with a synthetic panel** (deviation P9-3). It says nothing about real-world
   SDLC recommendation accuracy; only a human panel on a new benchmark version could.
2. **E9 is low (0.167), and the pattern is systematic** (§14). ReqPilot's `[PROJ]` numbers may
   over-favour hybrids relative to this panel. This is untested against any human judgement and was
   deliberately not tuned.
3. **Twelve cases.** The figures describe this benchmark only.
4. **E9 exercises the deterministic path only.** Model proposals (±1) and human overrides can move a
   real recommendation and are not part of E9.
5. **No real-model run of role #10 is recorded for P9.** Proposal validation and the consistency
   check are tested with the scripted model; behaviour with a live model is unmeasured.
6. **The consistency check surfaces, it does not block.** An explanation that cites a non-derived
   factor after one regeneration is stored with its discrepancy and shown; G6 approvers see it.
7. **Same-family generation.** The panel, the cases and the implementation all come from AI
   assistance, so shared assumptions are possible — which would tend to inflate agreement, not
   lower it.

## 19. Deferred work

- **Optional, not required for the P9 exit:** a real ≥ 3-expert blind panel on a new benchmark
  version, to measure what deviation P9-3 cannot.
- **FR-SDL-009**, sensitivity analysis on the weights (secondary tier).
- **A real-model check** of role #10's proposals and explanations on synthetic data.
- **P10** generated SDLC workflow (N.2 #24–#26); **P11** masking and hardening; **P12** final
  evaluation.

## 20. Files in the P9 completion pass

The P9 implementation arrived as `ReqPilot-P9-changes.zip` (78 files and `P9_CHANGES_MANIFEST.tsv`;
51 created, 27 modified), applied byte-for-byte after verifying every manifest hash. The completion
pass then changed only:

- **Created:** `docs/12-p9-sdlc-recommendation.md` (this report).
- **Modified, documentation:** `src/reqpilot/services/approval/service.py` — the module docstring
  gains the G6 paragraph alongside the P6/P7/P8 ones (no code change); `README.md`;
  `data/gold/README.md`; `docs/evaluation/p9-sdlc-synthetic-v1/README.md` — a review-status and
  deviation note at the top, results unchanged. The closure (deviation P9-3) touched only these
  documentation files and this report.
- **Modified, three defects found in the ZIP** (§20.1): `tests/p8_helpers.py` (restored to its
  committed content), `tests/p9_helpers.py`, `tests/security/test_p9_security.py`,
  `alembic/versions/0011_p9_sdlc_recommendation.py` (formatting only).
- **Unchanged:** every other P9 file from the ZIP (73 of 79 byte-identical); the frozen
  P9-SDLC-SYNTHETIC-v1 benchmark and its manifest; the committed E9 results
  (`summary.json` byte-identical).

### 20.1 Defects found in the ZIP, and how each was fixed

1. **The ZIP broke the frozen P8 benchmark.** It modified `tests/p8_helpers.py` (a new `model=`
   parameter on `make_p8_world`), but that file is P8-TRACE-SYNTHETIC-v1's scenario fixture and
   its hash is frozen in that benchmark's manifest (`05778d1a…`). The P8 harness therefore refused
   to run, and two tests in `tests/integration/test_p8_evaluation.py` failed. Updating the P8
   manifest would have edited a frozen benchmark, so instead `tests/p8_helpers.py` was restored to
   its committed content (canonical hash verified equal to the frozen one), and `make_p9_world` in
   `tests/p9_helpers.py` now builds the P8 world with `ScriptedP9Model` by substituting the class
   for that one call. `ScriptedP9Model` answers every P3–P8 prompt as `ScriptedP8Model` does and
   adds only the two SDLC prompts, so the P9 world is the one the ZIP intended.
2. **A flaky security test.** `test_audit_payloads_carry_no_override_reason_or_explanation_text`
   asserted that the marker `"7731"` was absent from the audit payloads — but the payloads
   legitimately carry random UUIDs and hashes, and a UUID containing `7731` (observed:
   `86c77319-…`) failed it about once in twelve runs. **Nothing leaked**; the override reason was
   never in the payloads. The marker is now `qzxw-7731` (letters outside hex) and the test also
   asserts the full reason is absent: 25 / 25 runs pass, against 23 / 25 before.
3. **Lint failures in migration 0011.** One line over the 100-character limit (`E501`) and two
   statements `ruff format` would reflow — both would fail CI. Fixed as formatting only; the long
   SQL `CHECK` text is split into adjacent string literals, which Python joins into the identical
   string, so the constraint is unchanged.

### 20.2 Final inventory, reconciled against the handoff manifest

Against the last commit (`60c4ab3`), the working tree has **81 changed paths**, and no others:

| Group | Paths |
|---|---|
| ZIP files, byte-identical to the ZIP | 72 |
| ZIP files, changed after application (§20.1, the G6 docstring, the evaluation README note) | 5 |
| ZIP file restored to its committed content, so no longer a change | `tests/p8_helpers.py` |
| The handoff manifest, at the repository root | `P9_CHANGES_MANIFEST.tsv` |
| Created or modified in the completion pass, outside the ZIP | `docs/12-p9-sdlc-recommendation.md`, `README.md`, `data/gold/README.md` |

That is 77 + 1 + 3 = 81 paths: 28 modified, 53 new. **No unintended file is included.**
`P9_CHANGES_MANIFEST.tsv` is kept at the root, as `P8-CHANGE-MANIFEST.txt` and
`P8-CLEANUP-MANIFEST.txt` were for P8. It is **the record of the ZIP as delivered and is not
edited**, so six of its hashes no longer describe the working tree:

| Path | Manifest (as delivered) | Now | Why |
|---|---|---|---|
| `alembic/versions/0011_p9_sdlc_recommendation.py` | `42242c5283e5…` | `c0a4a91f384b…` | §20.1 (3), formatting only |
| `docs/evaluation/p9-sdlc-synthetic-v1/README.md` | `167e9b270b86…` | `98d1211da8dc…` | review and deviation note; results unchanged |
| `src/reqpilot/services/approval/service.py` | `323a490de2bc…` | `d52ba3ffaaf5…` | G6 docstring paragraph; no code change |
| `tests/p8_helpers.py` | `c093b1e17f74…` | `05778d1ad24a…` | §20.1 (1), restored; equals the P8 frozen hash |
| `tests/p9_helpers.py` | `9f2ad714d6d3…` | `387f8e797540…` | §20.1 (1) |
| `tests/security/test_p9_security.py` | `a47e8eae3e12…` | `7215ea680eba…` | §20.1 (2) |

Every other manifest entry still matches, including all seven frozen benchmark files, whose P9
manifest `3486a56f…` is unchanged.
