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

## Roadmap note

Empty in P0. Gold datasets are produced alongside the phases they evaluate, and
each is frozen before use (`docs/01-analysis.md` §O, §P).
