# `data/` — what goes where

Four directories with deliberately different rules. The distinction that matters
most is between material that may be committed and material that must never
leave the machine it was created on.

| Directory | Committed? | Contents |
|---|---|---|
| [`kb_seed/`](kb_seed/README.md) | Yes | Curated knowledge-base source material |
| [`gold/`](gold/README.md) | Yes, frozen | Evaluation datasets with a hash manifest |
| [`dev/`](dev/README.md) | Yes | Small synthetic examples for development and tests |
| [`private/`](private/README.md) | **Never** | Anything local-only; gitignored |

## Rules that apply everywhere

**Synthetic and anonymised data only.** No real customer data, no real account
or transaction records, no production financial data. This is a university
project; there is no scenario in which real data belongs here.

**No secrets.** No API keys, passwords, tokens, or connection strings in any
data file. Configuration lives in the environment (`.env`, untracked).

**Respect source licences.** Official statutes and public-domain frameworks may
be extracted with citation. Copyrighted standards may **not** be redistributed —
store clause identifiers and team-written paraphrases only, never copied
normative text. Every knowledge item records what its licence permits.

**Record provenance.** Any file derived from an external source records where it
came from, which version, and when it was retrieved. A citation that cannot be
checked is not evidence.

## Roadmap note

P0 creates the structure and the rules. The directories are otherwise empty:
knowledge-base content and gold datasets are produced by the roadmap phases that
need them, defined in `docs/01-analysis.md` §P.
