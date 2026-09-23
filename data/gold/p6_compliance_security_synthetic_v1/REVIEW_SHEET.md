# P6-CS-SYNTHETIC-v1 — review sheet for the project author

**Status: not yet reviewed.** The benchmark was written and frozen by the AI coding
assistant during the autonomous P6 implementation. Nothing here has been confirmed by
a human. Tick each item when reviewed; any correction becomes
`p6_compliance_security_synthetic_v2` (v1 is never edited).

## 1. Reference controls (E5 denominator)

- [ ] The 16 reference controls are the applicable obligations of the 16 clauses (two
      each from FAB-SEC-1 and FAB-REC-1; none from FAB-NOTE-1).
- [ ] No applicable obligation of the corpus is missing, and none is invented.
- [ ] Each `checklist_key` crosswalk names the checklist control for the same
      obligation; R-15 (key facts) and R-16 (complaints) correctly have none.
- [ ] The high-impact labels (R-06, R-08, R-09, R-11, R-12) follow the stated rule.

## 2. Expected mappings and gaps

- [ ] Each of the 10 expected mappings is supported by the named clause as written.
- [ ] No requirement supports a checklist control that is missing from the list (for
      example: does PR-02 also address the quarterly review? It is labelled no).
- [ ] The 4 expected gaps are exactly the checklist controls no requirement covers.

## 3. Security and privacy

- [ ] The 14 (requirement, family) pairs are those a careful reviewer would derive.
- [ ] `expect_g3` is true exactly for the architecture I.7 high-impact families.

## 4. Attacks and language cases

- [ ] Each attack's expected outcome is correct under the P6 validation rules.
- [ ] Each language case's label is correct under Phase 0 C.1's mandated language.

## 5. Threats to validity acknowledged

- [ ] The benchmark was written after the implementation by the same author, and its
      figures are expected to be optimistic (BENCHMARK.md).

Reviewer: ______________________  Date: ____________
