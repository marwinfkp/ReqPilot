# `data/gold/` — frozen evaluation datasets

Human-annotated reference data used to compute the evaluation metrics. This is
the most consequential directory in `data/`, because everything the project
claims about its own effectiveness rests on it.

**Committed:** yes — and **frozen**.

## The freezing rule

A gold dataset is annotated, then frozen with a manifest before the phase that
is evaluated against it runs:

```
data/gold/<case_study>_v<n>/
├── manifest.json      # sha256 of every file, frozen_at, frozen_by
├── transcripts/
├── requirements.jsonl
├── conflicts.jsonl
├── ambiguity.jsonl
└── controls.jsonl
```

The evaluation harness **verifies the manifest hash before computing anything**
and refuses to run against a modified dataset.

This exists for one reason: it makes it impossible to quietly tune the gold set
after seeing results. That is a recognised threat to validity, and a hash is a
cheaper defence than discipline.

## Hashing is platform-independent

A manifest records, for each file, the sha256 of its **canonical content**: the text decoded as UTF-8, with CRLF
replaced by LF (`src/reqpilot/domain/integrity.py`, `file_canonical_sha256`). A Windows checkout (CRLF under
`core.autocrlf=true`) and a Linux checkout (LF) of the same frozen file therefore verify alike. Any other change - a
character, a lone CR, a BOM, whitespace - still fails the check. `.gitattributes` also checks this directory out with
LF everywhere. Freeze a new set with `file_canonical_sha256`, never with a hash of raw working-tree bytes.

Until 2026-09-22 the harness hashed raw bytes, so E1 and P5 manifest values recorded on Windows did not verify on
Linux CI. The fix re-recorded two E1 entries in canonical form, content unchanged (docs/08 §25).

## Versioning

A gold set is never edited in place. Corrections create `_v2`, and evaluation
reports record which version produced each number, so results stay comparable.

## What must not go here

- Real transcripts, real customer data, real institutional documents.
- Anything the development code reads. Development fixtures live in
  `../dev/`; a CI check enforces the separation.

## Gold transcript #1 - the P3 exit slot

**Filled on 2026-09-21 by `e1_synthetic_v1/` (E1-SYNTHETIC-v1).** That is a
**synthetic reference benchmark**: an AI assistant generated it and the sole
project author reviewed it, because ReqPilot is a solo project with no
annotation team. It is **not** an independently annotated gold standard. Its
`BENCHMARK.md` has the methodology and limitations; `docs/06` §20 has the result
and the approved deviation. The text below is the original expectation, kept for
the record.

The P3 roadmap exit is *"E1 computed against gold transcript #1"*
(`docs/01-analysis.md` §P). As first planned, it was not something
the system - or its AI assistant - may write for itself. E1 is defined as
extracted requirements "matched against a gold set (semantic match,
adjudicated)" on the primary case study, about 60 gold requirements (§O.1). The
gold set is human work: a team-authored, synthetic transcript and the
requirements a team member identifies in it, independently of the system.

`services/evaluation/extraction_eval.py` reads exactly this layout:

```
data/gold/<case_study>_v<n>/
├── manifest.json
├── transcripts/<name>.txt        (or .md) - "Speaker: words" lines
└── requirements.jsonl            - one gold requirement per line
```

`manifest.json`:

```json
{"name": "loan_origination", "version": "1", "role": "gold_transcript_1",
 "frozen_at": "<date>", "frozen_by": "<who>",
 "files": {"transcripts/<name>.txt": "<sha256>", "requirements.jsonl": "<sha256>"}}
```

`"role": "gold_transcript_1"` is what marks the set as the P3 exit's gold
transcript; without it the harness still computes, but reports **NOT E1**.

Each line of `requirements.jsonl`:

```json
{"gold_id": "G01", "transcript": "<name>.txt", "char_start": 120, "char_end": 188,
 "statement": "The system shall ...", "kind": "FR"}
```

Offsets index the transcript's text with line endings normalised to `
`.

**The E1 procedure** (`scripts/evaluate_extraction.py`):

1. Upload the transcript as a project source, declared `synthetic`, and run
   extraction with a real model (`LLM_PROVIDER=openai`, the provider selected at
   P3 closure). The harness refuses to score a stub or scripted run.
2. `--propose-pairs` lists candidate (prediction, gold) pairs for adjudication.
3. Adjudicators record a verdict for every pair - `match` or `no_match` - in a
   JSONL file kept **outside** this directory (the gold set stays frozen).
4. With `--adjudications`, the harness computes precision, recall and F1 over a
   one-to-one matching, and reports them as E1 only if all three conditions
   hold: the set is gold transcript #1, adjudication is complete, and a real
   model made every extraction call.

That result re-baselines ET-07 (provisional F1 >= 0.75) - it is not tested
against it.

## P5-QC-SYNTHETIC-v1 - the P5 exit slot (E2, E3)

**Frozen on 2026-09-22 as `p5_quality_conflict_synthetic_v1/`.** It holds 40
requirements with 12 planted conflicts and 14 labelled near-miss distractors
(E3), and 40 statements, half ambiguous (E2). Like E1-SYNTHETIC-v1 it is a
**synthetic reference benchmark**. It was written by the AI assistant *before* the
P5 detectors existed, but by the same assistant that then wrote them, and the
project author has not reviewed it. Its `BENCHMARK.md` fixes the counting
protocol. `services/evaluation/quality_eval.py` and `scripts/run_p5_eval.py`
verify the manifest and compute E2/E3. The results are in
`docs/evaluation/p5-qc-synthetic-v1/` and `docs/08` §17. A correction is `_v2`.

## P6-CS-SYNTHETIC-v1 - the P6 exit slot (E5)

**Frozen on 2026-09-22 as `p6_compliance_security_synthetic_v1/`.** It holds a
fictional knowledge base (four "Fabrikam Finance (fictional)" policies, 16
clauses and one injected-instruction note), 20 retail-loan requirements, the 16
reference controls that are E5's denominator, the expected mappings, gaps and
security/privacy families, 18 replayed attacks and 30 labelled language cases.
Like the others it is a **synthetic reference benchmark**, and it is weaker than
P5's in one respect stated up front: it was written **after** the P6 pipeline,
checklist and language detector existed, by the same AI assistant. The project
author reviewed it on 2026-09-23, after the evaluation runs, and identified no
substantive label corrections, so no `_v2` was created and v1 stays frozen. It
is therefore a synthetic, **project-author-reviewed** benchmark - not
independently validated, not an expert list, and carrying no regulatory or legal
validation. Its frozen `manifest.json`, `BENCHMARK.md` and `REVIEW_SHEET.md`
still say "review pending" because they record the state at freeze time; editing
them would change the manifest hash. Its `BENCHMARK.md` fixes the protocol and
lists the threats to validity. `services/evaluation/compliance_eval.py` and
`scripts/run_p6_eval.py` verify the manifest and compute E5 and the
supplementary figures (PostgreSQL required: P2 hybrid retrieval). The results
are in `docs/evaluation/p6-cs-synthetic-v1/` and `docs/09` (§28 records the
review status and a read-only integrity verification). A correction is `_v2`.

## P7-RISK-SYNTHETIC-v1 - the P7 first measurement

**Frozen on 2026-09-23 as `p7_risk_synthetic_v1/`.** It holds the nine cells of
the approved severity matrix (architecture I.3), 43 labelled scope-guard cases
(23 borrower-credit texts that `FR-RSK-011` must refuse and 20 legitimate project
risks it must not), and 20 adversarial cases with the outcome the design
requires for each.

**It sets no target, and none should be read into it.** Approved Phase 0 O.1
defines no risk-analysis metric, and the P7 roadmap exit criteria are
behavioural, so the benchmark records a *first measurement* in the O.1 sense
rather than a threshold. Like the others it is a **synthetic reference
benchmark**, written after the P7 implementation by the same AI assistant; the
scope-guard figures in particular are optimistic, because the cases were written
by the author of the patterns they test. The project author reviewed it on
2026-09-23, **after** the evaluation runs: the matrix and adversarial cases
needed no correction, and one scope case (S-21) was identified as a label
correction. **v1 remains frozen and unedited and the correction is reserved for
`_v2`**, so it is a synthetic, **project-author-reviewed** benchmark — not an
independently validated expert gold standard. Its frozen `manifest.json`,
`BENCHMARK.md` and `REVIEW_SHEET.md` still say "not reviewed" because they record
the state at freeze time; editing them would change the manifest hash. `docs/10`
§25 is the later record. Its `BENCHMARK.md` fixes the protocol and states the
threats to validity.
`services/evaluation/risk_eval.py` and `scripts/run_p7_eval.py` verify the
manifest and compute the figures (PostgreSQL required for the pipeline half).
The results are in `docs/evaluation/p7-risk-synthetic-v1/` and `docs/10` (§25
records the post-evaluation author review). A correction is `_v2`.

## P8-TRACE-SYNTHETIC-v1 - the P8 E6 first measurement

**Frozen on 2026-09-25 as `p8_traceability_synthetic_v1/`, before the harness was
first run.** It holds 27 labelled definition cases (a version's lifecycle state
and the trace edges around it, with the architecture N.3 elements it should be
missing), 5 aggregate cases (expected numerator and denominator, including the
empty scope, whose E6 is undefined), and an unlabelled scenario protocol over
the synthetic P8 loan-origination world (`tests/p8_helpers.py`, whose hash the
manifest also freezes).

**It sets no target.** O.1 defines E6 but sets no target, so the scenario is a
first measurement. It is a **synthetic reference benchmark**, written after the
P8 implementation by the same AI assistant. The project author reviewed it on
2026-09-25, **after** the evaluation run: **no substantive label corrections
were identified**. **v1 remains frozen and unedited**; any future correction
would be `_v2`. It is a synthetic, **project-author-reviewed** benchmark — not
independently reviewed and not an expert-validated gold standard. Its frozen
`manifest.json`, `BENCHMARK.md` and `REVIEW_SHEET.md` still say "not reviewed"
because they record the state at freeze time; `docs/11` §25 is the later record.
`services/evaluation/traceability_eval.py` and `scripts/run_p8_eval.py` verify
the manifest and compute the figures; the results are in
`docs/evaluation/p8-trace-synthetic-v1/` and `docs/11` §25. A correction is `_v2`.

## Roadmap note

Empty in P0. Gold datasets are produced alongside the phases they evaluate, and
each is frozen before use (`docs/01-analysis.md` §O, §P). After P3 this directory
holds `e1_synthetic_v1/`, frozen before ReqPilot was run on it; after P5 also
`p5_quality_conflict_synthetic_v1/`, after P6 `p6_compliance_security_synthetic_v1/`,
after P7 `p7_risk_synthetic_v1/`, and after P8 `p8_traceability_synthetic_v1/`.
None is ever edited; a correction is a new `_v2`.
