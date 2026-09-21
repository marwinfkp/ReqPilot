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

## How material is loaded

P2 built the machinery; this directory holds what a person has curated for it.
A curated set is a `manifest.yaml` plus any source files it references
(`.txt`, `.md`, `.pdf`, `.docx`), loaded with:

```bash
python scripts/seed_kb.py --manifest data/kb_seed/<set>/manifest.yaml --actor <user id of a Knowledge-Base Administrator>
```

The manifest format is exactly the one used by the synthetic development corpus,
[`../dev/kb_synthetic/manifest.yaml`](../dev/kb_synthetic/manifest.yaml): each
source declares its C.1 `source_type`, `issuing_body`, `title`, `jurisdiction`
(ISO alpha-2, or `INTL`), `version`, `effective_date`, `retrieved_at`,
`source_url`, `licence_class` (`extract_permitted`, `paraphrase_only` or
`synthetic`) and `licence_note`. Each item declares a stable `item_key`, its
`text_origin` (`verbatim_extract` or `team_paraphrase` for real sources), and its
text. The loader applies the same rules as the API: a paraphrase-only source
refuses verbatim text, and no synthetic source may be a statute, regulation or
standard.

## Roadmap note

**Still empty after P2, deliberately.** The knowledge base is manual curation
by a person who verifies each instrument against the issuing body's published
text (approved Phase 0 D.2), and the jurisdiction decision (Q2) is still open.
Nothing here was fetched or written automatically. The ET-06 retrieval probe
(recall@5 on 20 questions) is written against this curated set and frozen in
`../gold/`.
