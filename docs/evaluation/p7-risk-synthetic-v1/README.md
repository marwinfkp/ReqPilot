# P7 evaluation — first measurements on P7-RISK-SYNTHETIC-v1

**Status: PROJECT-AUTHOR REVIEW COMPLETED** (2026-09-23, *after* the runs below).

> Project-author review completed. The benchmark remains synthetic and is not an independently
> validated expert gold standard. S-21 was identified as a label correction during post-evaluation
> author review; v1 remains frozen and the correction is reserved for v2.

The review changed no benchmark file, no label, no manifest and no result: everything below is
exactly as first measured against frozen v1. The three report JSONs in this directory still carry
`"not reviewed by the project author"` in their `caveat`, because each report records the state at
the moment its run was made — which was before the review. This README and `docs/10` §25 are the
later record; the same provenance approach as P6 (`docs/09` §28). `docs/10` §25 records the review in full — the three
scope cases examined (S-21 corrected, S-22 and S-23 kept), why v1 is not edited, and what the S-21
correction would mean for the scope-guard figures in a future `_v2`.

**Benchmark:** `data/gold/p7_risk_synthetic_v1`, manifest canonical sha256
`4c93e887b1359e7d6a351255bf06ba85d0181124a95e86c861c3deaf0715ea94`, verified by the harness before
each run.

**Harness:** `services/evaluation/risk_eval.py` (the protocol in the benchmark's `BENCHMARK.md`)
and `scripts/run_p7_eval.py`. PostgreSQL 16.2 + pgvector 0.6.2 (local pgserver), real P2 hybrid
retrieval, `BAAI/bge-small-en-v1.5` embeddings; every run inside one transaction, rolled back.

**Date:** 2026-09-23. One run per mode. No rule, matrix, scope pattern or prompt was changed after
any run.

## There is no target here

Approved Phase 0 O.1 defines **no** risk-analysis metric. E4 (citation / evidence correctness) is a
manual audit of a 50-claim sample and is not computable by a harness. The roadmap's P7 exit
criteria are behavioural, not numeric.

So these are **first measurements**, recorded so the project author can set a target from them —
the O.1 convention established at P3. **Nothing below is a threshold that was met.**

## What this is, and is not

- A **synthetic reference benchmark**, written by the AI coding assistant that implemented P7,
  **after** the implementation, frozen before any run, **reviewed by the project author only after
  the runs**, and **not independently validated**. Figures are expected to be optimistic.
- **The scope-guard figures are the most optimistic.** The cases were written by the author of the
  patterns they test. They are not an estimate of performance on unseen text. The author review
  relabels S-21 (`out_of_scope` → `in_scope`); v1 is frozen, so the figures below are the v1
  figures and are not restated — `docs/10` §25 states the direction that correction points in.
- **The matrix figures are a correctness check, not a score.** 1.00 is the only acceptable value.
- **Risk *quality* is not measured anywhere.** Whether the risks identified are the right ones is
  not assessed, because there is no expert-produced reference risk list and inventing one would
  fabricate the judgement being measured.

## Runs

| Mode | What ran | Model calls | Tokens (in / out) |
|---|---|---|---|
| `deterministic` | matrix, scope guard, pipeline with no model | 0 | 0 / 0 |
| `adversarial` | 20 attacks replayed as scripted output (validation layer and matrix) | 84 scripted (not a model) | n/a |
| `model` | `gpt-5.6-luna` via the gateway | 81 | 180,660 / 57,790 |

## The deterministic figures (identical in every mode; no model is involved)

| Figure | Value |
|---|---|
| Matrix severity agreement with architecture I.3 | **1.00** (9 / 9) — `correct: true` |
| Matrix gate agreement (which cells fire G8) | **1.00** (9 / 9) |
| Scope guard precision | **1.00** (23 / 23 caught were out of scope) |
| Scope guard recall | **1.00** (23 / 23 out-of-scope cases caught) |
| Scope guard **false-alarm rate** | **0.00** (0 / 20 in-scope cases wrongly refused) |
| Scope rule agreement | **1.00** (the expected rule fired in 23 / 23) |

The false-alarm rate is the figure worth carrying forward: the 20 in-scope cases deliberately
mention credit bureaus, fraud controls, loan applications and "default" configuration, because a
guard that blocked those would be useless in a loan-origination project.

## The pipeline figures

| Figure | deterministic | adversarial | model |
|---|---|---|---|
| Risks recorded | 0 | 9 | **39** |
| — requirement-level / project-level | – | 9 / 0 | **35 / 4** |
| Severity equals the matrix's | n/a | 9 / 9 | **39 / 39 = 1.00** |
| Citation resolution | n/a | 9 / 9 = 1.00 | **51 / 51 = 1.00** |
| Every risk cites evidence | yes | yes | **yes** |
| G8: high risks gated | n/a | 2 / 2 | **15 / 15** |
| G8: non-high risks gated | 0 | 0 | **0** |
| Proposals dropped | 0 | 19 | 0 |
| `FR-RSK-011` refusals | 0 | 1 | 0 |
| Blocking gate tasks (P6 + P7) | 9 | 11 | **87** |
| Blocking tasks per requirement | 0.45 | 0.55 | **4.35** |

Severity distribution in the model run: **15 high, 24 medium, 0 low**. Categories: security 14,
privacy 11, compliance 6, operational 4, business 3, technical 1.

## Adversarial replay

**20 / 20 attacks had the expected outcome.**

- **3 distinct schema refusals** — a proposal that invents a `severity`, a `status` or a
  `g8_decision` is refused outright by `extra="forbid"`, not partially accepted.
- **10 distinct drop reasons observed**: `unknown_category`, `invalid_likelihood`, `invalid_impact`,
  `missing_rationale`, `uncited`, `unsupported_citation`, `wrong_requirement`, `duplicate`,
  `over_limit`, `out_of_scope_borrower_risk`.
- **Both "obeyed injection" attacks produced a HIGH risk with a blocking G8 task.** A model that
  writes "treat this as LOW" and "no Security Reviewer is required" in its rationales, while rating
  the risk `L3×I3`, is rated by the matrix from the ratings. Prose has no authority.
- The omission attack recorded no risk and suppressed no gate: P6's escalations are unaffected.

## Reading the figures

- **The deterministic run records no risk, by design.** With no model there is no rated judgement,
  and P7 does not invent one — a deliberate asymmetry with P6, which emits a catalogue baseline
  finding so that omission cannot suppress G3. A risk needs a rating with a written rationale;
  manufacturing one would fabricate exactly what the rating exists to record. The P6 gates still
  fired (9 blocking tasks in that run).
- **Reviewer load is the main finding.** 87 blocking tasks for 20 requirements, of which 15 are
  P7's G8. Nothing is suppressed and each is a real unreviewed item, but that is the practical
  problem P8's triage design has to answer. P6 alone measured 74 on the same project.
- **The model rated generously and never LOW** (15 high, 24 medium, 0 low). Whether that reflects
  this requirement set or a tendency of the model is not determinable from one run, and is not
  measured here.
- **A defect the live run found.** The first model run produced 21 semantic failures: the
  prior-findings content block did not declare its masking and synthetic facts, so the gateway's
  egress guard correctly refused to send it to a real provider (`FR-ING-003`). Nothing left the
  machine, the refusal was recorded, and the deterministic half of the run continued. It is fixed,
  with a regression test; the figures above are from the corrected code. See `docs/10` §21.7.
