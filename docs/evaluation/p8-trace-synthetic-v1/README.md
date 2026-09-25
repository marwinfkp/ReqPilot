# P8 evaluation — E6 on P8-TRACE-SYNTHETIC-v1

**Status: PROJECT-AUTHOR REVIEW COMPLETED** (2026-09-25, *after* the run below).

> Project-author review completed; no substantive label corrections were identified. The benchmark
> remains synthetic and is not an independently validated expert gold standard. v1 remains frozen;
> any future correction would be v2.

The review changed no benchmark file, no label, no manifest and no result: everything below is
exactly as first measured against frozen v1. `summary.json` in this directory still carries
`"project-author review pending"` in its `validation_status`, because it records the state at the
moment of the run, which was before the review. This note and `docs/11` §25 are the later record.

**Synthetic. Not independently reviewed. Not expert validated.** (At run time: project-author
review pending.)

**Benchmark:** `data/gold/p8_traceability_synthetic_v1`, manifest canonical sha256 `044558a75a10b8b3f785cd9f6c992a9e579b6b0ae2ee72ae0d06280f6b886fc9`, verified (with the scenario fixture's hash) before the run.

**Harness:** `services/evaluation/traceability_eval.py` and `scripts/run_p8_eval.py`; the product's own `version_coverage` / `CoverageReport.e6` (definition `N.3-v1`). Scenario on in-memory SQLite, scripted model, no network.

**Run:** 2026-09-25T10:02:26+00:00. One run. No code, label or fixture was changed after it.

## There is no target here

No approved numeric target exists for E6 (Phase 0 O.1 defines the metric and says targets for E2-E9 are set from measured behaviour, not guessed). The scenario figures are first measurements for the project author to set a target from - not thresholds that were met.

## Part 1 — does P8 compute E6 as defined?

* Definition cases: **27 / 27** agree (the implementation computes N.3 as labelled).
* Aggregate cases: **5 / 5** agree (including the empty scope, E6 undefined, and an all-missing scope, E6 = 0.0).

## Part 2 — first measurement on the synthetic scenario

| Step | Scope | E6 | Fully traced / total | Source | Class. | Risk outcome | Approved→APPROVED_BY | Baselined→RENDERED_IN |
|---|---|---|---|---|---|---|---|---|
| S0 | project (current versions) | 0.875 | 7 / 8 | 8 | 8 | 7 | 0 / 0 | 0 / 0 |
| S1 | baseline B1 | 0.000 | 0 / 5 | 5 | 5 | 4 | 5 / 5 | 0 / 5 |
| S2 | baseline B1 | 0.800 | 4 / 5 | 5 | 5 | 4 | 5 / 5 | 5 / 5 |
| S3 | baseline B2 | 0.800 | 4 / 5 | 5 | 4 | 4 | 5 / 5 | 4 / 5 |
| S4 | baseline B2 | 0.800 | 4 / 5 | 5 | 4 | 4 | 5 / 5 | 5 / 5 |
| S4-project | project (current versions) | 0.857 | 6 / 7 | 7 | 6 | 6 | 5 / 5 | 5 / 5 |

Versions not fully traced, with the N.3 element(s) they lack:

* S0: FR-LOAN-001 v1 — HAS_RISK or recorded risk-analysis outcome
* S1: FR-LOAN-001 v1 — HAS_RISK or recorded risk-analysis outcome; RENDERED_IN (baseline not rendered in any artefact)
* S1: FR-LOAN-003 v1 — RENDERED_IN (baseline not rendered in any artefact)
* S1: NFR-LOAN-001 v1 — RENDERED_IN (baseline not rendered in any artefact)
* S1: NFR-LOAN-003 v1 — RENDERED_IN (baseline not rendered in any artefact)
* S1: NFR-LOAN-004 v1 — RENDERED_IN (baseline not rendered in any artefact)
* S2: FR-LOAN-001 v1 — HAS_RISK or recorded risk-analysis outcome
* S3: FR-LOAN-001 v2 — CLASSIFIED_AS; HAS_RISK or recorded risk-analysis outcome; RENDERED_IN (baseline not rendered in any artefact)
* S4: FR-LOAN-001 v2 — CLASSIFIED_AS; HAS_RISK or recorded risk-analysis outcome
* S4-project: FR-LOAN-001 v2 — CLASSIFIED_AS; HAS_RISK or recorded risk-analysis outcome

## Reading these numbers

* Part 1 checks internal consistency between the implementation and the assistant's reading of N.3 — both written by the same assistant. It does not show the reading is right; the review sheet asks the project author to decide that.
* Part 2 describes how completely **this** synthetic run is traced. It is not an estimate for real projects, and E6 measures the presence of typed links, not whether a source genuinely supports a requirement (that is E4's manual audit).
* A missing element is reported as missing: nothing was back-filled to raise E6.
