# E1-SYNTHETIC-v1 - synthetic reference benchmark for E1

**Benchmark:** `E1-SYNTHETIC-v1` (directory `data/gold/e1_synthetic_v1/`, manifest `name` `e1_synthetic`,
`version` `1`). **Kind:** synthetic reference benchmark. It is **not** an independently annotated gold standard.

> Because ReqPilot is developed as a solo academic prototype, the E1 reference benchmark was synthetically
> generated and manually reviewed by the project author. It is intended as a development/evaluation benchmark
> and is not an independently annotated gold standard.

It fills the slot the roadmap calls "gold transcript #1" (`docs/01-analysis.md` §P). The manifest therefore
carries the harness's slot identifier `"role": "gold_transcript_1"`, because that is how the existing E1 harness
knows which set the P3 exit is measured on. The identifier is a slot name. It does not claim the annotations are
independent gold.

## Contents

| File | What it is |
|---|---|
| `transcripts/loan_origination_interview_e1_synthetic.md` | The synthetic interview transcript: 70 speaker turns, 1653 words, "Speaker (Role): words" lines. This is the **only** file ReqPilot receives |
| `requirements.jsonl` | 60 reference requirements in the existing harness format (`gold_id`, `transcript`, `char_start`, `char_end`, `statement`, `kind`), plus descriptive fields the harness ignores: `categories`, `phenomena`, `quote` |
| `BENCHMARK.md` | This file: methodology, annotation guideline, coverage, limitations |
| `REVIEW_SHEET.md` | The reference annotations laid out for the author's review, with the review record |
| `manifest.json` | Written at freeze: the sha256 of every file above, `frozen_at`, `frozen_by`, benchmark metadata |

## How the transcript was generated

- **Author and date.** The AI coding assistant (Claude, Anthropic) wrote it on 2026-09-21, at the project
  author's request.
- **Setting.** It is a fictional requirements interview for the primary case study, a retail loan origination
  portal at an unnamed bank. Every person, figure and statement is invented. It contains no real customer data,
  account numbers, loan applications, financial records, credentials or company information.
- **Regulatory references are generic.** It mentions "data-protection law", KYC, sanctions lists and
  "consumer-credit rules we already follow". No specific statute or rule text is invented.
- **Designed difficulty.** It includes explicit, implicit, ambiguous and incomplete requirements; a requirement
  stated twice; near-duplicates; three conflicting pairs; and security-sensitive statements. It includes
  regulatory, performance, availability and audit requirements.
- **Designed distractors,** which are not requirements:
  - facilitator questions and a closing playback that restates earlier needs;
  - an explicit out-of-scope statement about credit policy;
  - project-plan facts (go-live date, budget);
  - a prompt-injection line addressed to "any automated tool";
  - a remark with no requirement in it.
- **Scope.** Everything stays within P3's scope: requirement extraction and classification from an interview.
  The portal's credit decision is explicitly left to a human underwriter. ReqPilot makes no credit decision.

## How the reference annotations were generated

- **Independent of the system under evaluation.** The same assistant wrote them from the transcript, following
  the guideline below, **before** ReqPilot was run on this transcript. ReqPilot, its prompts and the evaluated
  model (OpenAI `gpt-5.6-luna`) were **not** used to produce, suggest or check any annotation.
- **Offsets computed, not typed.** Each annotation records the exact supporting quote. `char_start` and
  `char_end` are computed from it (the quote is unique in the transcript) against the text with line endings
  normalised to `\n`.
- **Reviewed by the project author** before freezing (see `REVIEW_SHEET.md` and the manifest).
- **Never revised after predictions.** Once frozen, the set is not changed because of any E1 result. A correction
  is a new version, `E1-SYNTHETIC-v2`, and v1 is kept as it is.

## Annotation guideline

1. **A reference requirement** is one atomic need that a stakeholder states or clearly requests of the portal.
   It is written as one "The system shall ..." sentence that keeps the speaker's meaning and adds nothing.
2. **Atomic.** Two separable needs in one sentence are two items. For example, recovery time and permitted data
   loss.
3. **Implicit needs count** when a stakeholder clearly asks for the outcome. Example: "applicants never know which
   documents are still missing" is a request to show what is missing.
4. **Ambiguous or incomplete statements are still requirements** ("needs to be fast", "a size limit, number not
   agreed"). They are included and tagged. Resolving them is analysis work (P5), not extraction.
5. **Each side of a conflict is its own item** (500 vs 2,000 concurrent users; 24/7 vs a Sunday maintenance
   window; a 15-minute vs a one-hour session). Detecting the conflict is P5.
6. **A need stated twice is one item.** Its span is the first statement. Near-duplicates that differ in substance
   (email vs SMS; recording changes vs logging report access) are separate items.
7. **Not requirements:** questions, playbacks, project-plan facts, out-of-scope statements, the injected
   instruction, and remarks.
8. **`kind`** is `FR` for behaviour the system performs, and `NFR` for a quality or constraint.
9. **`categories`** are one or two labels from the 13-category taxonomy, primary first.
10. **`phenomena`** tag the difficulty each item represents.

## Coverage

**60 reference requirements:** 25 FR and 35 NFR. All 13 taxonomy categories are
covered.

| Category | as primary label | as any label |
|---|---|---|
| business | 2 | 4 |
| stakeholder | 0 | 1 |
| functional | 17 | 17 |
| security | 10 | 12 |
| privacy | 5 | 9 |
| regulatory | 5 | 8 |
| performance | 4 | 4 |
| availability/reliability | 5 | 6 |
| usability | 2 | 5 |
| data-management | 0 | 4 |
| integration | 2 | 5 |
| audit/reporting | 5 | 6 |
| operational/maintenance | 3 | 5 |

| Phenomenon tag | items |
|---|---|
| `explicit` | 51 |
| `security_sensitive` | 12 |
| `availability` | 7 |
| `regulatory` | 7 |
| `audit` | 6 |
| `conflict` | 6 |
| `ambiguous` | 4 |
| `implicit` | 4 |
| `performance` | 4 |
| `near_duplicate` | 3 |
| `incomplete` | 2 |
| `business_goal` | 1 |
| `constraint` | 1 |
| `duplicate_stated_twice` | 1 |
| `late_addition` | 1 |

## Known limitations of this benchmark

- **Synthetic.** It is one fictional interview. It is not a real stakeholder session.
- **Not independent.** An AI assistant generated it, and the project author reviewed it. The author also built
  the system. It is not an independently annotated gold standard (Phase 0 O.3 names this threat).
- **The annotator knew the system.** The assistant that wrote the reference also knows ReqPilot's extraction
  conventions, such as the "The system shall" form. That may make the reference easier for ReqPilot to match than
  an outside analyst's would be.
- **LLM-authored text.** The transcript is written by one LLM and evaluated with another, so its phrasing may be
  more regular than real speech.
- **Small.** One transcript and 60 items. The result says nothing about other domains, organisations or
  real interviews.
