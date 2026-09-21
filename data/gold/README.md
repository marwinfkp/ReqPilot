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

## Roadmap note

Empty in P0. Gold datasets are produced alongside the phases they evaluate, and
each is frozen before use (`docs/01-analysis.md` §O, §P). After P3 this directory
holds `e1_synthetic_v1/`, frozen before ReqPilot was run on it. It is never edited;
a correction would be `e1_synthetic_v2/`.
