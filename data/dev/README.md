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

## Contents

- [`kb_synthetic/`](kb_synthetic/) (P2) - a small, **fictional** knowledge base:
  policies of a bank that does not exist, and a team-written practice note. Used
  by the tests and the demonstration. It contains no law, regulation or standard,
  real or invented, and it is never evidence of anything real.
- [`transcripts/`](transcripts/) (P3) - a short, **fictional** requirements
  workshop for a retail-loan portal, as `Speaker: words` lines. It drives the
  extraction tests and the demonstration. It is deliberately awkward: a stated
  priority, a duplicated need, a "should probably", and an injected instruction.
  It is **not** gold transcript #1 and is never used to compute E1 - development
  data is for building, not measuring.
- [`personas/`](personas/) (P4) - a scripted, **fictional** stakeholder
  (*Dana Reyes*, product owner at the fictional *Harbourside Lending*) with
  clear, vague and incomplete answers per interview topic, an injected
  instruction, and a clarification. It stands in for the human answering in
  the P4 tests and demonstration; it is not a model and not a benchmark.
- [`quality/`](quality/) (P5) - 13 **fictional** requirements for a fictional
  bank's mobile app. The set seeds a definite conflict, a near-miss, a duplicate,
  ambiguity, a placeholder, a security signal, an undefined acronym and an
  injected instruction. It drives the P5 tests and the demonstration. It is not
  the P5 benchmark (`../gold/p5_quality_conflict_synthetic_v1`), and its wording
  deliberately differs from it.

- [`compliance/`](compliance/) (P6) - a **fictional** knowledge base (Acme Bank
  information-security, retention and privacy-notice policies, with one
  deliberately injected instruction) and 7 fictional retail-loan requirements:
  a supported high-impact retention mapping, an authentication requirement whose
  model proposal is "low", consent, a plain functional requirement, a P5 privacy
  signal, an injected instruction and a dual-control disbursement. It drives the
  P6 tests and the demonstration. It is not the P6 benchmark
  (`../gold/p6_compliance_security_synthetic_v1`), and its wording deliberately
  differs from it.

## Roadmap note

Fixtures arrive with the roadmap phases that need something to process
(`docs/01-analysis.md` §P).

Recorded LLM response fixtures are a different thing and live under
`tests/fixtures/llm/` — see ADR-012.
