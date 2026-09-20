# `data/dev/` — synthetic development fixtures

Small, obviously-fake examples used while developing and demonstrating the
system: sample interview transcripts, example policy documents, seed projects.

**Committed:** yes.

## Rules

- **Synthetic only, and obviously so.** Use names like *Acme Bank*, *Jane
  Example*, and account numbers that cannot be real. If a reader could mistake a
  fixture for real data, it is the wrong fixture.
- **No secrets**, not even fake-looking ones that match a real credential
  format. A repository scanner cannot tell the difference, and neither can a
  reviewer at a glance.
- **Keep them small.** Fixtures are read in tests and reviewed in diffs.

## Separation from `../gold/`

Development data is for building; gold data is for measuring. Development code must
never read from `data/gold/`, so that evaluation datasets cannot influence the
implementation they are meant to judge. A CI check enforces the direction.

## Roadmap note

Empty in P0. Fixtures arrive with the roadmap phases that need something to
process (`docs/01-analysis.md` §P).

Recorded LLM response fixtures are a different thing and live under
`tests/fixtures/llm/` — see ADR-012.
