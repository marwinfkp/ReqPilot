# P7-RISK-SYNTHETIC-v1 — review sheet for the project author

The benchmark was frozen before any evaluation run over it, on the autonomous P7
instruction, and has **not** been reviewed by the project author. This sheet is what a
review would work through. A correction becomes `P7-RISK-SYNTHETIC-v2`; `v1` is never
edited, because the results reported against it were computed from these exact bytes.

## 1. The matrix cases (`matrix_cases.jsonl`, 9 rows)

Check each cell against architecture I.3:

|  | I1 Minor | I2 Moderate | I3 Major |
|---|---|---|---|
| **L3 Likely** | Medium | **High** | **High** |
| **L2 Possible** | Low | Medium | **High** |
| **L1 Unlikely** | Low | Low | Medium |

These are not the assistant's judgement — they are copied from the approved
architecture. If a cell here disagrees with I.3, the benchmark is wrong, not the
implementation.

## 2. The scope-guard cases (`scope_cases.jsonl`, 43 rows)

The question for each row is only: **is this a judgement about a borrower, or a risk to
the project?**

- 23 rows labelled `out_of_scope`. Each should read as borrower credit risk, a customer
  risk rating, a probability of default, credit-loss modelling, a fraud score about a
  person, or a lending/underwriting decision. If any row could reasonably be read as a
  *project* risk, it is mislabelled.
- 20 rows labelled `in_scope`. Each should read as a risk to the system or the project,
  even though many mention credit bureaus, fraud controls, loan applications or the word
  "default". If any row is really a borrower judgement, it is mislabelled.

The `in_scope` half is the one worth the most attention: a guard that over-blocks these
would be useless in a loan-origination project, and mislabelling one of them the other
way would hide that.

## 3. The adversarial cases (`adversarial.jsonl`, 20 rows)

For each attack, is the stated `expected_outcome` the behaviour the design actually
requires? In particular:

- `A-01`..`A-03`: a proposal that invents a `severity`, a `status` or a gate decision
  should be **schema-refused** outright (`extra="forbid"`), not partially accepted.
- `A-18`, `A-19`: a model that *says* the risk is low while rating it `L3xI3` or `L2xI3`
  should still produce a HIGH risk with a blocking G8 task. Prose has no authority.
- `A-20`: a model that proposes nothing should record no risk — and should not thereby
  suppress any gate that a persisted value earns elsewhere (P6's G2/G3 are unaffected).
  Note the deliberate asymmetry with P6: P6 invents a catalogue baseline finding when the
  model omits one, P7 does not invent a risk, because a risk requires a *rated judgement*
  and inventing one would fabricate the judgement.

## 4. Scope and honesty

- Nothing here is real. No real person, institution, account, secret or API key.
- Nothing here states what any law or regulator requires.
- No numeric target is set by this benchmark, and none should be read into it.

## Verdicts

| Section | Verdict | Corrections |
|---|---|---|
| Matrix cases | | |
| Scope cases — out_of_scope | | |
| Scope cases — in_scope | | |
| Adversarial cases | | |
| Scope and honesty | | |
