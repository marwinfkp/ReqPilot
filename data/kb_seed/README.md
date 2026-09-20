# `data/kb_seed/` — knowledge-base source material

Curated source material for the knowledge base: normative extracts, control
catalogue entries, synthetic organisational policies, and requirements-engineering
templates.

**Committed:** yes.

## What belongs here

Each item is a small file plus metadata recording, at minimum:

| Field | Why |
|---|---|
| `source_type` | statute · regulatory_direction · regulatory_guidance · org_policy · contractual_scheme · industry_standard · control_framework · best_practice |
| `issuing_body` | Who published it |
| `jurisdiction` | Where it applies |
| `title`, `version`, `effective_date` | Which text this is |
| `retrieved_at` | When a human consulted the source |
| `source_url` | Where to check it |
| `licence_note` | What may be stored and redistributed |

## What must not go here

- **Copyrighted standard text.** Store clause identifiers and team-written
  paraphrases only. This is a hard rule, not a preference.
- **Any real institution's internal documents.** Organisational policy examples
  are synthetic and must be labelled as fictional.
- **Live feeds.** The corpus is curated by hand. Regulatory instruments are
  amended and superseded, so every item records the version consulted, and a
  human verifies it at curation time.

## Roadmap note

Empty in P0. Content is curated by the roadmap phase that builds the knowledge
base and retrieval subsystem (`docs/01-analysis.md` §P).
