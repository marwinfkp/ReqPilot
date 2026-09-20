# `data/private/` — local only, never committed

Scratch space for material that must not leave the machine it was created on.

**Committed: never.** This directory is gitignored except for this README, and a
security test asserts that nothing else appears here.

## What this is for

- A stakeholder transcript that has not yet been anonymised.
- An exported document being checked before it is shared.
- Local experiment output.

## What still does not belong here

Gitignoring is a convenience, not a safeguard. The project's data rules apply
regardless of directory:

- **No real customer or production financial data**, anywhere in this
  repository, including here. This is a university project; there is no
  legitimate reason to hold real financial records on a development machine.
- **No secrets.** Credentials belong in `.env`, which is also untracked.

## Anonymising before promotion

Material moves out of `data/private/` only after the identifiers are replaced —
names, account and card numbers, national identifiers, contact details — at
which point it belongs in `../dev/` as a clearly synthetic example.
