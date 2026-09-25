# P8-TRACE-SYNTHETIC-v1 — review sheet for the project author

The benchmark was frozen before the harness was first run, on the autonomous P8 instruction, and
has **not** been reviewed by the project author or anyone independent. This sheet is what a review
would work through. A correction becomes `P8-TRACE-SYNTHETIC-v2`; `v1` is never edited, because
results reported against it were computed from these exact bytes.

## 1. The reading of N.3 (decide first — every label depends on it)

| Question | v1's reading | Agree? |
|---|---|---|
| Does "approved" include BASELINED and SUPERSEDED versions? | Yes — they passed G1 | ☐ |
| Does PENDING_APPROVAL count as approved? | No — G1 has not completed | ☐ |
| Is a version "baselined" if it has a `MEMBER_OF` edge whatever its state label? | Yes | ☐ |
| Is a BASELINED-state version with no `MEMBER_OF` edge baselined? | Yes (and so it can never have a rendered path) | ☐ |
| Does a section `CITES` edge satisfy "a `RENDERED_IN` path"? | No — the path is `MEMBER_OF` → baseline → `RENDERED_IN` | ☐ |
| Is `RISK_ASSESSED_BY` (a finished risk run that examined the exact version) the "recorded 'no risk identified' result"? | Yes (a P8 edge; see the P8 report) | ☐ |
| Do other analysis outputs (mappings, findings, conflicts, acceptance criteria) substitute for a risk outcome? | No | ☐ |
| Is a source document with no chunk a valid `SOURCES` origin? | Yes (P8 addition to N.2) | ☐ |
| Empty scope: E6 = ? | undefined (`null`) | ☐ |

## 2. The definition cases (`definition_cases.jsonl`, 27 rows)

For each row, check `expected_missing` against the reading above. Nine rows are fully traced:
D01, D03, D04, D09, D11, D12, D14, D18, D26. Rows that encode a judgement rather than a plain
reading: D12 (pending approval), D17 and D27 (baselined without a `MEMBER_OF` edge), D19 (superseded
without `APPROVED_BY`), D25 (a `MEMBER_OF` edge on an APPROVED-state version), D26 (an
`APPROVED_BY` edge on a VALIDATED version).

## 3. The aggregate cases (`aggregate_cases.jsonl`, 5 rows)

Check each `expected_fully_traced` / `expected_total` against the rows it names.

## 4. The scenario (`scenario.json`)

Carries no expected value. Review only whether its steps are a fair picture of the P8 flow.

## 5. Record of the review

| Reviewer | Date | Outcome | Issues (each becomes a v2 change, never a v1 edit) |
|---|---|---|---|
| — | — | not yet reviewed | — |
