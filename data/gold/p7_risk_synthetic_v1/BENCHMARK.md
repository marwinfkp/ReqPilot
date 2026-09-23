# P7-RISK-SYNTHETIC-v1 — P7 risk-analysis reference benchmark

**Synthetic. Not an independently validated gold standard. No target is set here.**

| Property | Value |
|---|---|
| Benchmark id | `P7-RISK-SYNTHETIC-v1` |
| Case study | Retail loan origination (the P6 synthetic project, `data/dev/compliance`) |
| Measures | The **deterministic** properties P7's roadmap exit names, plus supplementary pipeline figures |
| Authored by | The AI coding assistant (Claude, Anthropic) that implemented P7, on 2026-09-23 |
| Author review | **Not reviewed by the project author before evaluation** (autonomous P7 instruction). Review sheet: `REVIEW_SHEET.md` |
| Frozen | 2026-09-23, **before any evaluation run over it**; every file's canonical sha256 is in `manifest.json` |
| Real data | None. No real person, institution, customer, account, secret or API key |
| Lending claim | None. Nothing here is a credit model, a scoring rule, or a statement about any borrower |

## What this is, and what it is deliberately not

**There is no approved numeric target for risk analysis.** Approved Phase 0 O.1 lists
nine core metrics; none of them measures risk identification. E4 (citation / evidence
correctness) is defined as a **manual audit of a 50-claim sample** and is not something
this benchmark can compute. The roadmap's P7 exit criteria are behavioural, not numeric:
the matrix computes the level, every risk links to a requirement and evidence, a
high-severity risk blocks baseline approval, and the `FR-RSK-011` scope guard test passes.

So this benchmark **invents no threshold and claims no accuracy target**. It records a
first measurement, which is what the P3-established O.1 convention asks for
("targets for E2–E9 will be set from measured behaviour after P3–P7, not guessed in
Phase 0"). Anyone setting a target later should set it from these numbers, and should
read the threats to validity first.

## Threats to validity (read first)

1. **Written after the implementation, by the same author.** The scope-guard cases were
   written by the author of the scope guard's patterns, after those patterns existed.
   The set was frozen before any run over it and nothing was changed after seeing a
   result — but the author knew what the patterns matched. **The scope-guard figures are
   therefore optimistic and are not an estimate of performance on unseen text.**
2. **The in-scope cases are the honest half.** A guard that refused everything would score
   perfectly on the out-of-scope cases. The 20 `in_scope` cases are the ones that can
   fail, and they are deliberately adversarial *in the other direction*: they mention
   credit bureaus, fraud, default configuration and loan applications, because those are
   exactly the legitimate project risks a naive guard would block.
3. **The matrix cases are a correctness check, not a score.** All nine cells are stated in
   architecture I.3. Agreement must be 1.00; any other value is a defect, not a
   measurement, and the evaluation reports it as such.
4. **Small.** 43 scope cases, 20 adversarial cases, 9 matrix cells. Wide confidence
   intervals; no variance is measured, and a single run is reported.
5. **No model judgement is measured.** Whether a model identifies the *right* risks for a
   requirement is not assessed here, because there is no expert-produced reference risk
   list and inventing one would be fabricating the judgement being measured. What is
   measured is what deterministic code does with whatever a model proposes.

## Files

| File | Contents |
|---|---|
| `matrix_cases.jsonl` | 9 cells: `(likelihood, impact)` → expected severity and whether G8 fires. Copied from architecture I.3 |
| `scope_cases.jsonl` | 43 labelled texts: 23 `out_of_scope` (borrower credit/default/fraud scoring, each with the rule expected to catch it) and 20 `in_scope` (legitimate project risks) |
| `adversarial.jsonl` | 20 attacks on the pipeline, each with the outcome the design requires |
| `manifest.json` | The canonical sha256 of every file, and the counts |
| `REVIEW_SHEET.md` | For the project author to review the labels |

## Protocol

Computed by `services/evaluation/risk_eval.py`, run by `scripts/run_p7_eval.py`.
The harness verifies every file's canonical hash against `manifest.json` before it
computes anything, and refuses to run over an edited or unlisted file.

- **Matrix agreement** = cells where `compute_severity` equals the expected severity,
  over 9. A correctness check: the only acceptable value is 1.00.
- **Gate agreement** = cells where "requires G8" equals the expected flag, over 9.
- **Scope guard**: a case is *positive* when the guard returns at least one hit.
  - precision = out-of-scope caught / all caught
  - recall = out-of-scope caught / all out-of-scope
  - false-alarm rate = in-scope wrongly caught / all in-scope
  - rule agreement = out-of-scope cases where the expected rule id is among those that fired
- **Adversarial**: each case is replayed through the real schema, the real validator and
  the real matrix, and scored as expected / not expected. Reported as a fraction.
- **Pipeline figures** (a live run over the synthetic project, PostgreSQL): citation
  resolution, G8 routing correctness, risks per requirement, and the reviewer load
  (blocking gate tasks raised).

None of these is a target. They are the first measurement.

## What a reader must not conclude

- Not that ReqPilot identifies real project risks well. That is not measured.
- Not that the scope guard would hold on unseen text. See threat 1.
- Not that any number here is an approved threshold. There is none.
