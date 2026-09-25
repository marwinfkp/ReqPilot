# P8-TRACE-SYNTHETIC-v1 — E6 traceability-coverage reference benchmark

**Synthetic. Not an independently validated gold standard. Not expert validated. No target is set here.**

| Property | Value |
|---|---|
| Benchmark id | `P8-TRACE-SYNTHETIC-v1` |
| Measures | E6, traceability coverage (Phase 0 O.1), under the definition of architecture N.3 |
| Case study | Synthetic loan origination (`tests/p8_helpers.py`, workshop statements L01–L08, fictional speakers) |
| Authored by | The AI coding assistant (Claude, Anthropic) that implemented P8, on 2026-09-25 |
| Author review | **Not reviewed by the project author before evaluation** (autonomous P8 instruction). Review sheet: `REVIEW_SHEET.md` |
| Independent review | None |
| Frozen | 2026-09-25, **before the evaluation harness was first run**; every file's canonical sha256 is in `manifest.json`, and so is the scenario fixture's |
| Real data | None. No real person, institution, customer, account, secret or API key |

## What E6 is, exactly

Phase 0 O.1: *"E6 — Traceability coverage — Fraction of requirements with a complete chain per
`FR-TRC-001`, including risk links — Computed by the system (`FR-TRC-003`)."* `[PS §19]`

Architecture N.3 makes "complete" exact. A requirement version is **fully traced** when it has

1. at least one inbound `SOURCES` edge;
2. at least one `CLASSIFIED_AS` edge;
3. a risk-analysis outcome: a `HAS_RISK` edge, or the recorded "no risk identified" result
   (P8 records this as `RISK_ASSESSED_BY` to the risk-analysis run that examined the exact version);
4. an `APPROVED_BY` edge **if** approved (state APPROVED, BASELINED or SUPERSEDED);
5. a `RENDERED_IN` path **if** baselined (`MEMBER_OF` a baseline that is `RENDERED_IN` an
   artefact version; a version is baselined if it has a `MEMBER_OF` edge or is in state BASELINED).

* **Numerator:** versions in scope that are fully traced.
* **Denominator:** versions in scope (a baseline's versions in force, or the current version of
  every requirement for the project scope).
* **Empty scope:** E6 is undefined (`null`), never a fabricated 1.0.
* **Target: none.** O.1: "Targets for E2–E9 will be set from measured behaviour after P3–P7, not
  guessed in Phase 0." No approved document sets an E6 number, so none is checked here.

## Two parts

### Part 1 — definition cases (labelled)

`definition_cases.jsonl` (27 rows) describes one requirement version each: its lifecycle state and
the edges recorded around it, in a small vocabulary the harness turns into typed trace links over an
in-memory graph (nothing is persisted). Each row carries the N.3 elements it **should** be missing
(`SOURCES`, `CLASSIFIED_AS`, `RISK_OUTCOME`, `APPROVED_BY`, `RENDERED_IN`) and whether it is fully
traced. The labels were written by hand from the N.3 text above, not by running the code.

`aggregate_cases.jsonl` (5 rows) groups definition cases into scopes and states the expected
numerator and denominator — including the empty scope (`null`) and an all-missing scope (`0.0`).

These parts check that P8 **computes E6 as defined**. The only acceptable agreement is 100%; any
disagreement is either a defect in the implementation or a label error, and is reported as such,
never averaged away.

Edge vocabulary: `utterance_sources`, `chunk_sources`, `document_sources` (an inbound `SOURCES`
edge from that node type); `stakeholder_stated` (a stakeholder `STATED` an utterance — no edge to
the version); `classified`; `has_risk`; `risk_assessed`; `has_mapping`, `has_security_finding`,
`has_finding`, `has_conflict`, `acceptance` (analysis outputs that are **not** N.3 elements);
`approved_by`; `member_of_B1`; `rendered_B1`, `rendered_B2` (that baseline is `RENDERED_IN` an
artefact version); `cited_by_section` (an artefact section `CITES` the version); and the prefix
`other_version:` for an edge attached to a different version.

### Part 2 — the scenario (a first measurement, unlabelled)

`scenario.json` fixes a protocol over the synthetic P8 world: build it through the real P3–P7
pipelines (scripted model, no network), baseline five requirements through every gate, generate the
SRS, RTM and risk register, then change an approved requirement through G7 and baseline it again.
E6 is measured at each step. **No expected E6 value is frozen for the scenario** — it is a first
measurement of the running system, which is exactly what O.1 asks for before a target exists.

Disclosure: while implementing P8, the assistant ran development smoke checks on this same synthetic
world and saw that one requirement recorded no risk-analysis outcome (the scripted retrieval found
no evidence for it, so P7 made no call and recorded no run). The scenario carries no label that
this knowledge could bias, and nothing in the implementation was changed to alter it.

## Protocol

```
python scripts/run_p8_eval.py --benchmark data/gold/p8_traceability_synthetic_v1 \
    --out docs/evaluation/p8-trace-synthetic-v1
```

1. Verify `manifest.json`: every file in this directory must be listed and must match its
   canonical sha256, and the scenario fixture (`tests/p8_helpers.py`) must match its recorded hash.
   Any mismatch refuses the run.
2. Evaluate every definition case with `services.traceability.coverage.version_coverage` — the
   exact function the product uses — and every aggregate case with `CoverageReport.e6`.
3. Run the scenario and record E6, the per-element counts and every missing element.
4. Write `summary.json` and `README.md` to the output directory.

## Limits (read before quoting any number)

* **Synthetic and self-authored.** The same assistant wrote the implementation, the N.3 reading and
  the labels. Agreement shows internal consistency with that reading, not that the reading is right.
* The scenario is one small synthetic project with a scripted model. Its E6 says how completely
  **this** run is traced; it is not an estimate for real projects.
* E6 measures **presence** of typed links. It does not judge whether a linked source genuinely
  supports the requirement — that is E4's manual audit, outside P8.
