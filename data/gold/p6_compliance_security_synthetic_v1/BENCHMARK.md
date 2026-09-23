# P6-CS-SYNTHETIC-v1 — P6 compliance and security reference benchmark

**Synthetic. Fictional. Not an independently validated gold standard.**

| Property | Value |
|---|---|
| Benchmark id | `P6-CS-SYNTHETIC-v1` |
| Case study | Retail loan origination at "Fabrikam Finance (fictional)", jurisdiction `IN`, domain `loan_origination` |
| Metrics | **E5** (approved Phase 0 O.1) plus supplementary P6 figures (below) |
| Authored by | The AI coding assistant (Claude, Anthropic) that implemented P6, on 2026-09-22 |
| Author review | **Not reviewed by the project author before evaluation** (autonomous P6 instruction). Review sheet: `REVIEW_SHEET.md` |
| Frozen | 2026-09-22, **before any evaluation run over it**; every file's canonical sha256 is in `manifest.json` |
| Real data | None. No real person, institution, customer, account, secret or API key |
| Regulatory claim | None. Every source is a fictional organisational policy. Nothing here says what any law requires |

## Threats to validity (read first)

1. **Written after the implementation, by the same author.** Unlike P5-QC-SYNTHETIC-v1,
   this benchmark was written *after* the P6 pipeline, the checklist
   (`compliance_checklists@1.0.0`), the security catalogue and the language detector
   existed, by the assistant that wrote them. It was frozen before any run over it,
   and nothing in the implementation was changed after seeing its results - but the
   author knew the checklist keys, the evidence-tag vocabulary and the detector's
   patterns. The figures are therefore expected to be **optimistic**.
2. **E5 is structurally favourable.** Gap detection is `expected - covered` over the
   checklist, so every checklist control is either mapped or flagged as a gap. E5
   therefore measures the checklist's *coverage of the reference*, and does not depend
   on the model. It is reported as computed, with that caveat.
3. **The reference is the author's.** "Expert-identified" in the approved E5 definition
   means an expert panel; here it is one AI author following a stated rule. It is not
   an expert list and must not be called one.
4. **Small and single-domain.** 20 requirements, 16 reference controls, one fictional
   organisation, one jurisdiction code.

## Contents

| File | What it holds |
|---|---|
| `kb_manifest.yaml` | The benchmark's own knowledge base: 4 fictional sources, 16 policy clauses plus 1 injected-instruction note (`FAB-NOTE-1`) |
| `requirements.jsonl` | 20 fictional retail-loan requirements with one classification category each; `PR-19` carries an injected instruction |
| `reference_controls.jsonl` | The E5 denominator: 16 applicable controls, each with the clause it comes from, its checklist equivalent (or `null`) and whether interpreting it is high-impact |
| `expected_mappings.jsonl` | The 10 (requirement, checklist control) pairs a correct analysis supports, with the clause that supports each |
| `expected_gaps.jsonl` | The 4 checklist controls no requirement covers |
| `security_privacy.jsonl` | 14 (requirement, family) pairs a correct derivation produces, and whether each should reach G3 |
| `adversarial.jsonl` | 18 attacks replayed as model output: fabricated, foreign, uncited and irrelevant citations, prohibited language, authority claims, a provenance mismatch, an unknown control, the injection obeyed, and risk-level downgrades |
| `language_cases.jsonl` | 30 sentences, 18 prohibited assertions or authority claims and 12 acceptable hedged sentences |

## The reference rule (fixed before evaluation)

**E5 reference.** One reference control per distinct obligation stated by a clause of
the benchmark's allowlisted corpus that applies to a retail loan origination system.
`FAB-SEC-1` states two (limitation, quarterly review) and `FAB-REC-1` two (history,
retention); every other clause states one; the injected note states none. That gives
16. `checklist_key` names the `compliance_checklists@1.0.0` control that represents
the same obligation, or `null` when the checklist has none (fee disclosure and
complaint handling: conduct controls, outside checklist v1's stated scope).

**High impact.** Interpreting a requirement against a control involving a retention
period, consent, data-subject rights or a reporting obligation is labelled high-impact.

**Expected mappings.** A requirement supports a control when its statement, as
written, addresses (fully or partly) the obligation of the supporting clause.

**Expected security/privacy families.** The families a careful reviewer would derive
for each requirement from the `security_risk_rules@1.0.0` catalogue; `expect_g3` is
true exactly for the architecture I.7 high-impact families.

## Protocol (implemented by `services/evaluation/compliance_eval.py`, without discretion)

The benchmark's manifest is verified before anything runs. The harness seeds the
knowledge base from `kb_manifest.yaml` into a fresh project (domain
`loan_origination`, jurisdiction scope `IN`, every source allowlisted), creates one
requirement version per statement citing one synthetic source document, runs the
unchanged P5 quality analysis (deterministic), then the unchanged P6
`AnalysisRunner.analyse_compliance`, and reads the persisted rows.

**E5** = |{ r in reference : r.checklist_key is in (mapped keys ∪ gap keys) }| / |reference|,
where *mapped keys* are the control keys of the run's recorded, non-rejected mappings
and *gap keys* the control keys of the run's gaps. A reference control with no
checklist key is never covered.

**Supplementary figures** (no targets; never called E5):

| Figure | Definition |
|---|---|
| Citation resolution rate | resolved / total citations of the recorded mappings, each resolved against the run's own evidence set |
| Mapping precision / recall | against `expected_mappings.jsonl`, over (requirement, control) pairs with a covering relationship |
| Gap precision / recall | against `expected_gaps.jsonl`, over the run's rule-engine gap keys |
| Unsupported-citation rejection | dropped / injected, over the fabricated, foreign and uncited attacks (adversarial mode) |
| Prohibited-language rejection | dropped / injected, over the prohibited-language, authority and obeyed-injection attacks (adversarial mode) |
| Language detector | precision / recall / accuracy on `language_cases.jsonl` |
| G2 routing correctness | share of recorded mappings whose G2 task exists exactly when the mapping is high-impact; and recall of the benchmark's high-impact expected mappings among G2-routed ones |
| G3 routing correctness | share of recorded findings whose G3 task exists exactly when the authoritative level is high, and whose authoritative level is at least its floor; recall of `expect_g3` pairs among G3-routed findings |
| Family precision / recall | against `security_privacy.jsonl` |

**Modes.** `deterministic` (no model call: rules, catalogue, gaps), `adversarial`
(the attacks of `adversarial.jsonl` replayed as scripted model output over the
expected answers - it evaluates the deterministic validation layer, and says nothing
about model accuracy), and `model` (the configured provider; BILLABLE).

**Corrections** after evaluation become `p6_compliance_security_synthetic_v2`. v1 is
never edited.
