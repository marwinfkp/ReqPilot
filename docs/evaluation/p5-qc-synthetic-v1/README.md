# P5 evaluation — E2 and E3 on P5-QC-SYNTHETIC-v1

**Benchmark:** `data/gold/p5_quality_conflict_synthetic_v1`. Manifest sha256
`0b0dde3fc2ad251a5c5eebbbcabd00641525c629780898cf94306a9eddae64ef`, verified by the harness before each run.

**Harness:**

- `services/evaluation/quality_eval.py`, which implements the counting protocol in the benchmark's `BENCHMARK.md`;
- `scripts/run_p5_eval.py`.

**Date:** 2026-09-22. One run per mode. No detector, rule or prompt was changed after either run.

## What this is, and is not

- A **synthetic reference benchmark**, written by the AI coding assistant that also wrote the P5 detectors. The corpus
  was frozen before the detectors existed, but the same author's intuitions shaped both. The project author has not
  reviewed the labels.
- The results show that the pipeline works on controlled synthetic data. They are **not** evidence of real-world
  accuracy.
- A single run of each mode. Model outputs vary between runs; no variance was measured.

## Runs

| Mode | What ran | Model calls | Tokens (in / out) | Embeddings | Counts for the exit? |
|---|---|---|---|---|---|
| `deterministic` | Rule layer only | 0 | 0 / 0 | `BAAI/bge-small-en-v1.5` (local) | No (ablation) |
| `model` | Rules + LLM semantic layer (configured pipeline) | 108 (8 quality reviews, 100 adjudications) | 142,931 / 32,943 | `BAAI/bge-small-en-v1.5` (local) | **Yes**: every semantic call was `gpt-5.6-luna` |

The model-mode token totals are the sum of the E3 run (104 calls, 137,815 / 25,988) and the E2 run (4 calls, 5,116 /
6,955).

## E3 — conflict detection (780 pairs: 12 planted conflicts, 768 negatives, 14 labelled distractors)

| Mode | TP | FP | FN | TN | Precision | Recall | F1 | FP rate (all negatives) | FP rate (labelled distractors) |
|---|---|---|---|---|---|---|---|---|---|
| **model** | **12** | **0** | **0** | **768** | **1.00** | **1.00** | **1.00** | **0.000** (0/768) | **0.000** (0/14) |
| deterministic | 7 | 4 | 5 | 764 | 0.636 | 0.583 | 0.609 | 0.005 (4/768) | 0.286 (4/14: D03, D06, D10, D11) |

Definite-only variant:

- **model:** TP 11, FP 0, recall 0.917. One planted conflict was judged `potential`, not `definite`.
- **deterministic:** TP 5, FP 0, recall 0.417.

The deterministic layer missed K03, K06, K08, K10 and K11. These pairs need reading comprehension:

- "no planned downtime" vs a maintenance window;
- MFA vs a one-time link;
- a looser bound under the same condition;
- "within one day" vs "weekly";
- a maximum vs an accepted size.

Recall by kind, model mode: numeric, timing, actor/scope, logical and security are all 1.00.

**The exit bar (the P5 brief: recall ≥ 0.80) is met in model mode:** recall is 1.00.

## E2 — ambiguity detection (40 statements, 20 ambiguous)

| Mode | TP | FP | FN | TN | Precision | Recall | F1 | FP rate |
|---|---|---|---|---|---|---|---|---|
| **model** | **20** | **3** | **0** | **17** | **0.870** | **1.00** | **0.930** | **0.15** (A24, A27, A40) |
| deterministic | 20 | 1 | 0 | 19 | 0.952 | 1.00 | 0.976 | 0.05 (A40) |

The model layer adds false positives: it flags "up to 10 MB each" (A24) and "next to each application" (A27) as
ambiguous. The deterministic rule layer is near-perfect here. That is the clearest sign of the threat to validity
above: the vague-term list and the ambiguous statements came from the same author. **No E2 target exists.** Per Phase
0 O.1 it is set from this first measurement, by the project author.

Raw reports: [`deterministic.json`](deterministic.json), [`model.json`](model.json).
