# P5-QC-SYNTHETIC-v1 — seeded quality and conflict benchmark (E2, E3)

**Kind:** synthetic reference benchmark. **Not** an independently annotated gold standard.

## Purpose

The P5 roadmap exit (`docs/01-analysis.md` §P) is *"E2 and E3 computed on the seeded corpora with false-positive
rates reported"*. The P5 implementation brief adds a bar: seeded conflict recall ≥ 0.80. Phase 0 O.1 defines the two
metrics:

- **E2 (ambiguity-detection accuracy):** precision and recall against deliberately ambiguous seeded statements. About
  40 seeded items, half of them ambiguous.
- **E3 (conflict-detection accuracy):** precision and recall on a corpus with about 10 planted conflicts plus near-miss
  distractors.

## Provenance and honesty statement

- **Authorship.** Every file here was written by the AI coding assistant (Claude, Anthropic) on 2026-09-22. It was
  written **before** the P5 detectors were implemented and before any ReqPilot run over it.
- **Review.** The labels are the assistant's. The project author has not reviewed them before evaluation. The P5
  instruction was to proceed autonomously, so author review is recorded as pending.
- **The main threat to validity.** The same assistant that wrote this corpus also writes the detectors. A corpus
  written by the builder of the system measures the system against the builder's own intuitions. The results are
  evidence that the pipeline works on controlled synthetic data, not evidence of real-world accuracy.
- **All content is fictional:** the organisation, the people (marked "(fictional)") and the product. There is no real
  customer data, no real regulation, and no credential.
- **Frozen.** `manifest.json` records the sha256 of every file. The harness refuses to evaluate a modified set.
  Corrections after evaluation create `p5_quality_conflict_synthetic_v2`. This version is never edited.

## Files

| File | Content |
|---|---|
| `requirements.jsonl` | E3 corpus: 40 requirement statements `C01`–`C40`, each attributed to a fictional stakeholder |
| `conflicts.jsonl` | 26 labelled pairs: 12 planted conflicts (`K01`–`K12`) and 14 labelled distractors (`D01`–`D14`) |
| `ambiguity.jsonl` | E2 corpus: 40 statements `A01`–`A40`: 20 ambiguous (with the offending words), 20 not ambiguous |

### Planted conflicts (12)

| Kind | Pairs |
|---|---|
| numeric | K01, K04, K08, K09, K11 |
| timing | K02, K03, K10 |
| actor/scope | K05 |
| logical (negation) | K07 |
| security | K06, K12 |

### Labelled distractors (14): pairs that look related but are not conflicts

| Distractor type | Pairs |
|---|---|
| conditional compatible (explicit scoping condition) | D01, D02, D14 |
| near-miss, different actor or population | D03, D12 |
| near-miss, numeric (different attribute or action) | D04, D05, D10, D11 |
| near-miss, logical or security | D06, D07, D13 |
| duplicate (not a conflict) | D08 |
| refinement | D09 |

## Counting protocol (fixed before evaluation)

**E3.**

- **Unit:** an unordered pair of requirements from `requirements.jsonl`. There are 40 × 39 / 2 = 780 pairs.
- **Gold positive:** a pair labelled `conflict` (12).
- **Gold negative:** every other pair (768), including the 14 labelled distractors. The corpus is exhaustively
  labelled: an unlisted pair is a non-conflict.
- **Predicted positive:** the pair has a persisted `conflict` row, class `definite` or `potential`. A pair judged
  `conditional_compatible`, `duplicate`, `no_conflict` or `insufficient_information` is predicted negative.
- **Reported:**
  - TP, FP, FN, TN;
  - precision, recall and F1;
  - the false-positive rate over all negative pairs, FP / 768;
  - the false-positive rate over the labelled distractors, FP among D01–D14 / 14;
  - a `definite`-only variant.
- **Exit bar (from the P5 brief):** recall ≥ 0.80, with the false-positive rates reported. The Phase 0 O.1 rule
  applies to everything else: other targets are set from this first measurement, not guessed.

**E2.**

- **Unit:** a statement from `ambiguity.jsonl`.
- **Predicted positive:** at least one `ambiguity` quality finding (any detector) on the statement's version.
- **Reported:** TP, FP, FN, TN, precision, recall, F1, and the false-positive rate FP / 20.
- **Target:** none. It is set from this first measurement (O.1).

**Modes.** Both are reported. Neither is tuned after seeing results.

- `deterministic`: the rule layer only, with no model calls.
- `model`: the rule layer plus the LLM semantic layer through the gateway, with the configured provider.

The roadmap-exit figure is the configured pipeline, which is `model` mode when a real provider is configured. Every
model call must be a real model (not the stub or scripted provider) for a `model`-mode figure to count.

**Seeding.**

- Each statement is loaded into a fresh project as a requirement version through the P1 service. Its source reference
  names the fictional stakeholder it is attributed to.
- The quality/conflict run then runs over the whole project, exactly as in the application.
- Nothing in the corpus is shown to the model except the statements themselves.
