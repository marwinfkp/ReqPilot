# P6 evaluation — E5 and supplementary figures on P6-CS-SYNTHETIC-v1

**Benchmark:** `data/gold/p6_compliance_security_synthetic_v1`, manifest canonical sha256
`2120466972db56da32ee3990033bdc0eefc080519f1e888feb5168bc9a7c3277`, verified by the harness before each run.

**Harness:** `services/evaluation/compliance_eval.py` (the protocol in the benchmark's `BENCHMARK.md`) and
`scripts/run_p6_eval.py`. PostgreSQL 16.2 + pgvector 0.6.2 (local pgserver), real P2 hybrid retrieval,
`BAAI/bge-small-en-v1.5` embeddings; every run inside one transaction, rolled back.

**Date:** 2026-09-22. One run per mode. No detector, rule, checklist, catalogue or prompt was changed after any run.

## What this is, and is not

- A **synthetic reference benchmark**, written by the AI coding assistant that implemented P6, **after** the
  implementation, and frozen before any run. The **project author reviewed it on 2026-09-23**, after these runs, and
  identified no substantive label corrections; no `_v2` was created and v1 stays frozen. It is a synthetic,
  project-author-reviewed benchmark — **not** independently validated, **not** an expert list, and carrying no
  regulatory or legal validation. Figures are expected to be optimistic.
- The benchmark's frozen files (`manifest.json`, `BENCHMARK.md`, `REVIEW_SHEET.md`) and the `caveat` strings inside
  the three report JSONs below still read "author review pending" / "not reviewed by the project author". That is
  deliberate: they record the state **at freeze and run time**, and editing them would change the frozen manifest
  hash and falsify the provenance of the runs. This file and `docs/09` §28 carry the current status.
- **E5 is structurally favourable**: gaps are `expected − covered`, so every checklist control is mapped or a gap.
  E5 measures the checklist's coverage of the reference, and is the same in every mode.
- No real-world regulatory, legal or security accuracy is claimed. E5 = 0.875 is **controlled synthetic checklist
  coverage**, nothing more. One run per mode; no variance measured.

## Integrity (verified read-only, 2026-09-23)

All ten manifest-listed files re-hash with `domain/integrity.file_canonical_sha256` to exactly their manifest
values; no unlisted file is present; `requirements.jsonl` is
`b3955f7fba7309fc58ef35bfc034aa3fdfb778a0af276db2908ef9c694e6f451` as recorded. The manifest's own canonical hash,
`2120466972db56da32ee3990033bdc0eefc080519f1e888feb5168bc9a7c3277`, is the value in all three reports below, so the
runs used exactly this benchmark.

A review copy reported as hashing to `699fcc04…` was the **P5** benchmark's `requirements.jsonl`
(`data/gold/p5_quality_conflict_synthetic_v1/`, canonical `699fcc04…dcc6c1b07f5a13d257d`, matching its own
manifest); the reported digest differed from it only by a two-character transposition. Nothing about the P6
benchmark changed. Full detail: `docs/09-p6-compliance-security.md` §28.

## Runs

| Mode | What ran | Model calls | Tokens (in / out) |
|---|---|---|---|
| `deterministic` | rules, catalogue, gaps; no model | 0 | 0 / 0 |
| `adversarial` | 18 attacks replayed as scripted output (validation layer only) | 61 scripted (not a model) | n/a |
| `model` | `gpt-5.6-luna` via the gateway (20 compliance, 40 security/privacy) | 60 | 123,287 / 34,718 |

## E5

| Mode | Covered / reference | **E5** | By mapping | By gap only | Missed |
|---|---|---|---|---|---|
| deterministic | 14 / 16 | **0.875** | 0 | 14 | R-15 key facts, R-16 complaints |
| adversarial | 14 / 16 | **0.875** | 1 | 13 | same |
| model | 14 / 16 | **0.875** | 10 | 4 | same |

Bar (the P6 brief's): E5 ≥ 0.75 — **met**. The two misses are conduct controls outside checklist v1's stated scope.

## Supplementary figures (no targets)

| Figure | deterministic | adversarial | model |
|---|---|---|---|
| Citation resolution | n/a (no mappings) | 1 / 1 | **21 / 21 = 1.00** |
| Mapping P / R (covering relationships) | – / 0.00 | 1.00 / 0.10 (attacks break most) | **0.71 / 1.00** |
| Gap P / R | 0.29 / 1.00 | 0.31 / 1.00 | **1.00 / 1.00** |
| G2 routing correct | n/a | 1 / 1 | 21 / 21; both expected high-impact mappings routed |
| G3 routing correct | 14 / 14; expected G3 9 / 10 | 16 / 16; 10 / 10 | 60 / 60; 10 / 10 |
| Family P / R | 0.93 / 0.93 | 0.88 / 1.00 | **0.23 / 1.00** |
| Supporting clause retrieved | 10 / 10 | 10 / 10 | 10 / 10 |
| Claims dropped | 0 | 11 | 1 (unsupported citation) |
| G2 + G3 tasks raised | 9 | 11 | 74 |

**Adversarial replay:**
- All 18 attacks had the expected outcome.
- Unsupported-citation rejection was **3 / 3** (fabricated, foreign-project and uncited).
- Prohibited-language rejection was **5 / 5** (prohibited language, authority claims and the obeyed injection).
- Every risk downgrade was raised to its floor, with G3 for the high-impact families.
- A forged `risk_level` field was schema-refused, and the catalogue baseline stood.

**Language detector** on the 30 labelled cases: 18 / 18 prohibited caught, 0 / 12 false alarms. The cases were written
by the detector's author after it existed.

## Reading the figures

- **Citations and gaps.** Every recorded mapping's citations resolve to evidence of its own run. With the model,
  the rule engine reports exactly the four planted gaps.
- **Mapping false positives (model).** Four covering mappings are not in the reference:
  - PR-05 → retention, and PR-16 → minimisation and encryption. These are defensible readings of shared clauses.
  - PR-11 → the sanction checkpoint, which is a stretch.
  - 7 more mappings were `relevant_context`, which never covers a control.
- **Families (model).** The model derives security and privacy requirements generously: 60 findings against 14
  expected. With the I.7 floors, most land on high-impact families, so there are **74 blocking gate tasks for 20
  requirements**. Nothing is suppressed, but reviewer load is real. This is the main finding for P7/P8 triage design.
- **Deterministic triggers.** They missed PR-05 audit logging ("keep a history…" has no catalogue keyword) and
  over-triggered PR-15 data minimisation ("identity documents").
