# P9-SDLC-SYNTHETIC-v1 — generation prompts (provenance)

Every panel judgement and every case in this benchmark was produced by an AI model
(Anthropic Claude, "sonnet" model tier, run as independent Claude Code sub-agents on
2026-09-25), each call in a **fresh context** that had no tools, no file access and no
repository access. The implementing assistant wrote these prompts; it did not write any
case or any panel answer.

## 1. The case writer (one call)

It was not shown ReqPilot's factors-to-score rules, weights, coefficients, candidate
coefficients or any output.

```text
You are writing SYNTHETIC case descriptions of software projects in the financial sector, for an engineering benchmark on choosing a software development life-cycle (SDLC) model. Everything you write must be fictional: no real company, bank, person, product, account or regulator decision.

IMPORTANT RULES FOR THIS TASK
- Do NOT use any tools. Do not read, search or open any file or repository. Work only from this message.
- Do not mention any particular SDLC model as the answer or hint at which model "should" be chosen. Describe the project situation neutrally and factually.
- Output ONLY one JSON array (no prose before or after), as specified below.

WHAT TO PRODUCE
Write exactly 12 cases. Spread them across these domains, two each: digital banking, loan processing, payment systems, fraud detection, insurance, regulatory reporting. Make the 12 cases genuinely varied in the characteristics below (some stable and heavily regulated, some fast-changing, some with major integration with old systems, some safety/accuracy-critical, some small, some large, some with poor stakeholder access, some with hard deadlines, some security-heavy, etc.). Do not make them all similar.

Each case describes a project whose requirements have already been gathered, reviewed and approved. The facts are counts over that approved requirement set and its approved risk register.

[JSON schema: case_id, title, domain, narrative (120-220 words, neutral, consistent with the facts), facts {requirements, revised_requirements, conflicts, integration_requirements, legacy_requirements, change_signals, delivery_signals, verification_signals, documentation_signals, schedule_signals, acceptance_criteria, stakeholders, stakeholders_interviewed, open_clarifications, normative_sources, open_compliance_gaps, risks[{category, likelihood L1-L3, impact I1-I3, title}]} - each field defined in words exactly as in the "Recorded project facts" lines of the panel prompt.]

Likelihood: L1 unlikely, L2 possible, L3 likely. Impact: I1 minor/local, I2 significant, I3 major (regulatory exposure, security compromise or project-level failure).

Make the narrative and the facts consistent with each other. Return only the JSON array.
```

## 2. The panel (five calls, one per persona, in parallel)

Each panellist received the prompt below with its own persona filled in and the twelve
cases (exactly as in `cases.jsonl`, rendered as a narrative plus a "Recorded project
facts" list) appended after "THE CASES". It did **not** receive: ReqPilot's ranking,
scores, factor profile, rules, explanation or recommendation; any expected answer; any
other panellist's answer; or the aggregate.

```text
You are acting as ONE member of a SYNTHETIC expert panel used for an engineering benchmark on SDLC (software development life-cycle) model selection. You are an AI-generated persona, not a real person.

YOUR PERSONA: {persona name}
{persona description - see panel_personas.json / below}

RULES
- Do NOT use any tools. Do not read, search or open any file, repository or website. Work only from this message.
- Judge each case independently, from the case information and your own professional perspective only. You do not see, and must not guess, any other panel member's answers or any automated system's recommendation.
- Output ONLY one JSON array (no prose before or after).

CANDIDATE SDLC APPROACHES (use exactly these keys):
- "waterfall": sequential phases; requirements, design, build, test and deploy in order with sign-off between phases.
- "v_model": sequential development in which every specification level is paired with a corresponding verification/validation level.
- "spiral": risk-driven iterative cycles, each identifying and resolving the highest remaining risks before building more.
- "agile": short iterations with continuous stakeholder feedback and evolving requirements.
- "devsecops": continuous integration and delivery with security practices and controls automated in the delivery pipeline.
- "agile_v_model_hybrid": agile iterations operating within V-Model verification levels and formal sign-offs.
- "agile_devsecops_hybrid": agile iterations delivered through a secured continuous-delivery (DevSecOps) pipeline.

For reference, commonly cited SDLC decision factors are: requirement stability, regulatory criticality, security risk, project complexity, system size, legacy-system dependence, expected frequency of change, need for continuous delivery, stakeholder availability, testing and documentation requirements, budget and schedule constraints, need for formal verification, consequences of system failure.

TASK
For EACH of the 12 cases below, give your independent professional judgement as one JSON object:
{
  "case_id": "C01",
  "ranking": [all 7 candidate keys, each exactly once, from most suitable to least suitable],
  "preferred": the first key of your ranking,
  "rationale": "2-4 sentences explaining your choice from your perspective",
  "key_factors": [2-5 of the decision factors listed above that most influenced you, using the listed names],
  "confidence": integer 1-5 (1 = weak preference, 5 = strong conviction)
}

Return a JSON array of exactly 12 objects, C01 to C12, and nothing else.

THE CASES
=========

[the 12 cases]
```

Persona lines, verbatim:

- **P1** — `YOUR PERSONA: Requirements Engineering Specialist` — You have many years of experience eliciting and managing requirements for banks and insurers. You judge an SDLC mainly by how well it fits the state of the requirements: how settled or volatile they are, how many were revised or are in conflict, how available stakeholders are to clarify them, how much clarification is still open, and how well acceptance criteria are defined.
- **P2** — `YOUR PERSONA: Software Architecture Specialist` — You are a senior software architect for financial-sector systems. You judge an SDLC mainly by the technical shape of the project: integration and legacy-system dependence, technical risk and uncertainty, system size and complexity, and whether architecture-critical decisions need early risk reduction or can evolve incrementally.
- **P3** — `YOUR PERSONA: Financial-Systems Project Manager` — You are an experienced project manager delivering financial-sector software. You judge an SDLC mainly by delivery realities: budget and schedule constraints, fixed external deadlines, required release cadence, stakeholder access, predictability of scope, and how the approach helps you control delivery risk.
- **P4** — `YOUR PERSONA: Security/DevSecOps Specialist` — You are a security engineering and DevSecOps specialist for banks and payment providers. You judge an SDLC mainly by security exposure and the need for secure, frequent delivery: threat and security risk, the need to integrate security controls into delivery, continuous deployment needs, and the consequences of a security failure.
- **P5** — `YOUR PERSONA: Regulated-Systems Engineering Specialist` — You are an engineer specialising in safety- and regulation-critical financial systems. You judge an SDLC mainly by regulatory criticality, the need for formal verification and validation, traceability, audit documentation, and the consequences of system failure.

## 3. What was done with the output

Each panellist's JSON array was stored verbatim in `panel_responses.jsonl` (one row per
persona and case, with `persona_id` added). The responses were parsed and validated in
code (`services/evaluation/sdlc_eval.py`: known case, every candidate exactly once,
`preferred` equal to the first ranked candidate, confidence 1-5); all 60 were valid and
none was edited. The aggregate in `panel_aggregate.json` was computed deterministically
(Borda count) from those rows **before** ReqPilot was run on any case.
