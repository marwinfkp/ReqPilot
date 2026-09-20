# ReqPilot — Phase 0 Analysis (Revision 2.1)

**Status:** Analysis only. No architecture decided, no framework selected, no code written.
**Authoritative source:** `Problem Statement.docx` (reference copy: `docs/problem-statement.md`).
**Supersedes:** Revision 1 (archived at `docs/archive/01-analysis-r1.md`).
**Purpose:** A Phase 0 baseline that is faithful to the problem statement, internally consistent,
realistic for a university team, and technically defensible — the foundation for Phase 1
(architecture design).

---

## Changelog — Revision 1 → Revision 2

| # | Change | Reason |
|---|---|---|
| C1 | **Risk Analysis restored to the core MVP** as the 9th agent role, with a new `FR-RSK` requirement group, a lightweight **Risk Register** artefact, risk entities in the data model, risk in the traceability chain, a dedicated roadmap phase, and a new objective (O8) | The problem statement names a Risk-Analysis agent (§4), a "Threat and risk register" artefact (§12), and risk-related SDLC factors (§13). Excluding it was a faithfulness defect in R1 |
| C2 | **All 13 agent roles retained, each with an explicit implementation kind** (LLM-powered agent / hybrid / deterministic service / rule engine / state machine) | R1 implied that non-LLM roles were somehow lesser. An agent *role* is an architectural responsibility; the implementation technique is a separate, independently justified decision |
| C3 | **Every requirement now carries a provenance tag**: `[PS §n]` = derived from the problem statement, `[PROJ]` = proposed by us. All numeric targets moved into a separate, clearly-labelled "proposed engineering targets" table | R1 presented team-invented numbers (latency, recall, SUS, injection-blocking rate) as though they came from the problem statement |
| C4 | **New section C.1: normative-source taxonomy and knowledge-base framing.** Distinguishes law, regulatory direction, regulatory guidance, organisational policy, industry standard, contractual scheme requirement, control framework, and best practice. The KB is defined as an *educational, evidence-based reference corpus*, with explicit prohibitions on legal interpretation and mandated output language | R1 blurred "regulation", "standard", and "policy" into one undifferentiated category, which is neither legally nor academically precise |
| C5 | **Jurisdiction scope specified source-by-source with justification**, including a note on ISO copyright constraints and a standing verify-at-curation-time caveat | R1 named jurisdictions without justifying each source or addressing redistribution limits |
| C6 | **New scope-guard subsection D.1 and requirement `FR-RSK-011`**: ReqPilot is a requirements-engineering and SDLC system *for* a loan-origination project, not a lending system. "Risk" in ReqPilot always means project/requirement risk, never borrower credit risk | Adding a Risk-Analysis capability to a lending case study creates a genuine scope-drift hazard that must be closed explicitly |
| C7 | **Evaluation split into Core (mandatory, 9 metrics) and Secondary (stretch, 8 metrics)** | R1's 14 mandatory metrics were unrealistic for a semester |
| C8 | **Three-tier scoping applied consistently**: Core MVP / Secondary-Stretch / Explicitly Out of Scope. Problem-statement items that cannot fit the MVP are now *deferred with justification*, never silently dropped | Instruction: do not shrink scope by quiet omission |
| C9 | **Confidence scores redefined** as heuristic system-confidence / review-prioritisation signals, explicitly not calibrated probabilities. Calibration is a stretch goal | R1 implied statistical meaning that would not be validated |
| C10 | **New section J.1: governance outside the LLM** — a named architectural principle listing the eight mechanisms enforced deterministically | R1 made the point in passing; it deserves to be a first-class principle |
| C11 | **Traceability chain extended** to include classification, risk, conflict, compliance, and approval as first-class links | R1's chain omitted analysis outputs |
| C12 | **Consistency pass (new section R)** with a verification table | See C13–C17 for the defects it found |
| C13 | Module count corrected: R1 said "eleven modules" and listed twelve | Internal contradiction |
| C14 | Approval gates corrected from six to **eight enforced ReqPilot gates**, of which **G1–G7 derive from §16's first seven categories and G8 is an additional `[PROJ]` risk gate**; §16's eighth category (production-readiness) is implemented as a gate *inside generated workflows*, not as a ReqPilot gate | R1 under-counted the gates; R2's first pass then over-claimed by presenting G1–G8 as "§16's eight" (corrected in R2.1) |
| C15 | `FR-DOC-011` added: **Data requirements and Interface requirements** as SRS sections | Both are named in §12 and were missing from R1 |
| C16 | §17 platform controls now have an explicit coverage map (new G.18), including MFA and encryption — deferred *with justification* rather than dropped | R1 marked MFA "permanently excluded" without acknowledging it is a §17 requirement |
| C17 | `FR-QAL-009` (infeasibility) and `FR-WFL-004` (testing requirements per phase) added or re-tagged as problem-statement-derived | Both appear in §10 and §15 respectively |
| C18 | Section N retitled **"Project and AI risks (risks of building ReqPilot)"** and explicitly distinguished from the Risk-Analysis capability | Two different meanings of "risk" now coexist in the document |

### Revision 2 → Revision 2.1 (consistency corrections only — no scope change)

| # | Change | Reason |
|---|---|---|
| C19 | **Gate accounting corrected.** G.14 no longer presents G1–G8 as "§16's eight approval points". It now states that **§16 categories 1–7 map to ReqPilot gates G1–G7**, that **G8 is an additional `[PROJ]` risk gate with no §16 counterpart**, and that **§16 category 8 (production-readiness) is `[PS §16]` but is implemented inside the generated project's SDLC workflow** (`FR-WFL-003`) because ReqPilot does not manage the target project's deployment. The gate table gains a §16-category column; prose in C.2, D.3, G.14, and R updated to match | The matching eights were a coincidence, not a correspondence. R2 over-claimed §16 provenance for G8 |
| C20 | **Stale "95 FRs" reference removed** from the terminology row of section R; replaced with the verified 132 | Left over from R2's first-draft count, before `FR-SDL` and `FR-WFL` were restored |
| C21 | **`FR-RSK-012` provenance changed from `—` to `[PROJ]`** (remains OOS) | The document's own rule is that every FR carries an explicit provenance tag; this was the only untagged row. Untagged count is now **0** |
| C22 | **Numeric-target claim in section R made precise.** It now says numeric *engineering targets* are isolated in H.2, and explicitly acknowledges that other numbers legitimately appear elsewhere as implementation parameters, evaluation-design choices, roadmap parameters, or structural counts | The previous wording ("all numbers isolated in H.2") was literally false |
| — | Counts recomputed after C19–C21: **132 FRs unchanged**; provenance split now exactly 99 `[PS]` / 33 `[PROJ]` / **0 untagged**; mixed-provenance rows 3 → 4 (`FR-HIL-001` joins, since it enforces both `[PS]` gates G1–G7 and the `[PROJ]` gate G8) | No requirement was added, removed, or retiered |

---

## A. The problem we are solving

Requirements engineering in the financial sector is done by hand, and the financial sector is the
worst possible place for it to be done by hand.

Three forces combine:

1. **Input is messy and multi-sourced.** Requirements arrive from customers, business teams,
   compliance officers, security teams, regulators, legacy system documentation, and policy
   documents — almost all as unstructured natural language, spread across transcripts, emails,
   meeting notes, and PDFs.
2. **The domain is heavily regulated.** Every requirement potentially carries obligations from
   banking regulation, data-protection law, industry standards, and internal policy. A missed
   control is not a bug; it is an audit finding.
3. **The analysis is cognitively expensive.** Detecting that two stakeholders have stated
   contradictory transaction limits, or that "the system should be fast" is untestable, or that a
   data-retention requirement is missing entirely, requires reading everything at once and holding
   it in mind. Humans do this inconsistently and slowly.

The visible failures are ambiguous and incomplete specifications, contradictions discovered late,
regulatory omissions, incorrect scope, costly rework, and project delay.

There is a **second, coupled problem** the statement raises explicitly: the choice of SDLC model is
usually made by organisational habit rather than from the project's actual characteristics
(requirement stability, regulatory criticality, security risk, legacy dependence, consequences of
failure). Even once a model is chosen, teams rarely tailor it — the security activities, compliance
checkpoints, and approval gates a financial project needs are not inserted into the process in a
deliberate, justified way.

**ReqPilot therefore produces two coupled outputs:** a validated, classified, risk-assessed,
compliance-mapped, fully traceable **requirement baseline**; and, derived from that baseline, a
justified, ranked **SDLC recommendation** plus a **project-specific workflow** containing the phases,
activities, roles, deliverables, testing requirements, security activities, compliance checkpoints,
and approval gates the project actually needs.

**The framing that constrains everything** (§20, closing paragraph): the system automates
information collection and analysis, but **responsibility for regulatory interpretation, requirement
approval, and SDLC adoption remains with authorised human stakeholders.** This is a design
constraint on the data model, the approval workflow, and the user interface — not a disclaimer.

### Why an agentic system rather than one large prompt

This is the project's core academic claim, so it should be stated precisely. Requirements analysis
decomposes into tasks with **different inputs, different success criteria, different grounding
needs, and different escalation rules**:

- Extraction reads the raw transcript and must not invent content.
- Compliance analysis reads retrieved normative text and must cite it.
- Conflict detection needs the *whole* requirement set at once, not one requirement.
- Risk analysis needs the requirement plus its security and compliance analysis results.
- SDLC selection needs project-level aggregates, not individual requirements.

A single monolithic prompt performs all of these mediocrely at once, cannot be evaluated per task,
and cannot be audited. Specialised roles with typed inputs and outputs give per-role evaluation,
per-role guardrails, per-role least-privilege data access, and a replayable audit trail. That
separation of responsibilities — **not** the use of any particular framework — is what the project
demonstrates.

---

## B. Objectives

| # | Objective | Success looks like | Source |
|---|---|---|---|
| O1 | Automate elicitation through adaptive, role-aware interviews | The system asks follow-up questions when an answer is vague, incomplete, or inconsistent — not a fixed questionnaire | §7 |
| O2 | Extract and structure requirements from conversations and documents | Every requirement carries the full structured schema and a link to its source span | §8 |
| O3 | Classify requirements across multiple dimensions | Multi-label classification over the §9 categories with a confidence signal | §9 |
| O4 | Detect requirement quality defects | Ambiguity, incompleteness, untestability, duplication, undefined terms, missing source — each with the offending span | §10 |
| O5 | Detect conflicts and inconsistencies | Contradictions and stakeholder disagreements surfaced as pairs with rationale | §10 |
| O6 | Map requirements to normative sources and controls | Every mapping carries evidence; unmet expected controls reported as gaps | §11 |
| O7 | Derive security and privacy requirements | Controls stakeholders did not think to ask for are proposed, grounded in the knowledge base | §4, §9 |
| **O8** | **Identify, rate, and register requirement-level risks** | **Each risk is categorised, rated by likelihood and impact on an explainable scale, linked to its requirement and evidence, carries mitigation considerations, and escalates when high** | **§4, §12, §13** |
| O9 | Ground generation in approved sources (RAG) | No compliance, security, or risk claim without a citation; low-confidence output escalates instead of asserting | §6 |
| O10 | Maintain end-to-end traceability | Stakeholder input → requirement → analysis results (classification, risk, conflict, compliance, security) → acceptance criterion → artefact → evidence → control → approval | §12 |
| O11 | Enforce human-in-the-loop approval | Nothing reaches a baseline or a generated artefact without a recorded, role-appropriate approval | §16 |
| O12 | Generate requirement documentation | SRS (including data and interface requirements), user stories, use cases, acceptance criteria, compliance matrix, risk register, RTM, assumptions register, open issues | §12 |
| O13 | Recommend an SDLC model with justification | Ranked candidates with suitability scores, factor evidence, an LLM-authored explanation, and counter-arguments | §13, §14 |
| O14 | Generate a project-specific SDLC workflow | Phases, activities, roles, deliverables, testing requirements, security activities, compliance checkpoints, approval gates, entry/exit criteria, traceability requirements | §15 |
| O15 | Provide a complete audit trail | Prompts, retrieved evidence, agent decisions, human edits, and approvals recorded and replayable | §20 |
| O16 | Be measurably better than a conventional manual process | Core metrics (section O) computed against a human-produced gold standard and a manual baseline | §19 |

---

## C. Proposed scope

### C.1 Terminology: normative sources, and what the knowledge base is

The problem statement uses "regulations", "policies", "standards", and "controls" in close
succession. Collapsing them — as Revision 1 did — is imprecise and academically indefensible.
ReqPilot uses this taxonomy consistently, and every knowledge item is typed with exactly one of
these:

| Type | Binding? | Issued by | Example category |
|---|---|---|---|
| **Statute / law** | Legally binding | Legislature | Data-protection legislation |
| **Regulatory direction** | Binding on regulated entities | A financial regulator under statutory authority | Master directions, circulars |
| **Regulatory guidance** | Persuasive, not strictly binding | A regulator | Advisories, FAQs, notes |
| **Organisational policy** | Binding inside one organisation | The institution itself | Internal access-control policy |
| **Contractual / scheme requirement** | Binding by contract, **not law** | An industry scheme or counterparty | Card-scheme security requirements |
| **Industry standard** | Voluntary unless mandated by law or contract | A standards body | Information-security management standards |
| **Security / control framework** | Voluntary reference taxonomy | A public body or consortium | Cybersecurity control catalogues |
| **Engineering best practice** | Non-binding professional convention | Academic and practitioner literature | Requirements-engineering guidelines |

**What the ReqPilot knowledge base is.** An **educational, evidence-based reference corpus**,
assembled by the student team for the purpose of demonstrating retrieval-grounded requirements
analysis. Each item records source, type (from the table above), jurisdiction, effective date,
version, applicability, and retrieval date.

**What it is not.** It is not complete, not continuously updated, not a substitute for professional
advice, and **not an authoritative legal decision engine**. It is deliberately small (see D.2).

**Therefore ReqPilot must not, at any maturity level:**

- provide final legal interpretations or legal advice;
- assert that a requirement *is* compliant with any law, regulation, or standard;
- present LLM-generated interpretation as a professional determination;
- replace a qualified legal or compliance professional.

**Mandated output language** (enforced by `FR-CMP-007`, deterministic post-processing, not prompt
instruction alone). ReqPilot says *"potentially applicable"*, *"candidate mapping"*, *"suggested
control"*, *"requires review by a qualified compliance professional"*. It never says *"is
compliant"*, *"satisfies the regulation"*, or *"meets the legal requirement"*. Human approval remains
mandatory for every high-impact regulatory interpretation (`FR-HIL-001` gate G2), per §11 and §16.

### C.2 In scope (full project, across all phases)

- **Domains:** one primary financial case study implemented deeply; one secondary for evaluation and
  generalisation.
- **Inputs:** live interview sessions plus uploaded transcripts, policy documents, normative-source
  extracts, and existing requirement documents.
- **The thirteen agent roles** of §4 under a central coordinator, each implemented by the technique
  appropriate to its responsibility (section J).
- **Knowledge base:** curated, versioned, typed per C.1, with full provenance metadata.
- **Retrieval-grounded generation** with citations, confidence signals, and escalation.
- **Requirements repository** with versioning, trace links, and approval state.
- **Requirement-level risk analysis** with a lightweight risk register.
- **Human approval workflow** with eight role-gated ReqPilot checkpoints (seven §16-derived plus one
  project-defined risk gate), and §16's production-readiness approval emitted into generated
  workflows.
- **Document generation** to open formats.
- **SDLC selection engine** (deterministic rules + multi-criteria scoring + LLM explanation) and
  project-specific workflow generation.
- **Audit log** covering agent runs and human actions.
- **Guardrails:** sensitive-data masking, prompt-injection defence, retrieval allowlisting,
  agent-level least privilege, role-based access control.
- **Evaluation harness** with a gold-standard dataset and reproducible metric computation.

### C.3 Out of scope permanently

- Real customer data or production financial data. **Synthetic and anonymised only.**
- Legal advice or final regulatory determination (C.1).
- Any lending, credit, or transactional decision function (D.1).
- Integration with real banking systems, core banking platforms, payment rails, or credit bureaus.
- Production identity infrastructure and key management (HSMs, enterprise SSO).
- Multi-tenant SaaS operation, horizontal scaling, high availability.
- Fine-tuning or training a foundation model.
- Continuous ingestion of live regulatory feeds.
- Acting as the system of record for a real organisation's requirements.
- Enterprise risk-management platform capabilities: quantitative risk modelling, Monte Carlo
  simulation, risk-appetite frameworks, KRI dashboards, loss-event databases.

---

## D. Recommended MVP scope

**Design principle: one narrow vertical slice, end to end, with every required concept present but
appropriately thin.** A system that extracts brilliantly and does nothing else demonstrates far less
than one that carries a transcript all the way to an approved, risk-assessed SRS and a justified
SDLC workflow.

### D.1 Primary case study: retail loan origination — and a hard scope guard

§18 recommends beginning with one use case, naming loan processing or digital customer onboarding.
**Retail loan origination** remains the recommendation, because it naturally exercises every required
capability: KYC/CDD obligations give compliance mapping real content; document upload and retention
give privacy and data-management requirements; credit-bureau integration gives legacy and
integration constraints for SDLC factor extraction; disbursement limits and approval authority give
a realistic source of **stakeholder conflict** (sales wants a higher auto-approval limit than risk
does); regulatory reporting gives audit requirements; and the domain has abundant, genuine
**project-level risk** for the risk-analysis capability.

> ### Scope guard — what ReqPilot is and is not
>
> ReqPilot is a **requirements-engineering and SDLC-recommendation system for a loan-origination
> software project**. It is **not** a loan-origination system.
>
> ReqPilot does **not** and will **not**: perform credit scoring; make or recommend loan approval or
> rejection decisions; compute borrower or customer risk scores; execute banking transactions;
> integrate with real banking, bureau, or payment systems; or make any production financial
> decision.
>
> **The loan domain exists solely to supply realistic requirements-engineering and compliance
> scenarios.**
>
> **Critical terminology consequence.** Now that Risk Analysis is in the MVP, "risk" is ambiguous and
> must be disambiguated everywhere. In ReqPilot, a `RiskItem` is always a **project, engineering,
> security, privacy, compliance, or operational risk arising from a requirement** — for example
> *"the requirement for a 2-second bureau response creates a technical risk of timeout cascades
> under load"*. It is **never** a borrower's creditworthiness, a customer risk rating, or a fraud
> score. This is enforced as `FR-RSK-011` and will be enforced again in the Phase 1 data model.

A **payments / step-up authentication** slice is the secondary case study, used for evaluation
generalisation only. The problem statement's own worked example, `FR-PAY-001`, comes from that
domain.

### D.2 Jurisdiction and normative scope for the MVP

**One primary jurisdiction (India) plus a small number of cross-cutting catalogues.** Each source is
listed with its type from C.1 and the reason it is included. Keeping this list short keeps the
knowledge base at roughly **40–80 curated items** — small enough to curate honestly, large enough
that retrieval is non-trivial.

| Source | Type (C.1) | Why included for loan origination | MVP use |
|---|---|---|---|
| India's data-protection statute (DPDP Act, 2023) | Statute | Loan origination collects extensive personal data; governs consent, purpose limitation, retention, and data-principal rights | Privacy requirement derivation; consent and retention mappings |
| RBI Master Direction on KYC | Regulatory direction | Customer due diligence is intrinsic to loan origination onboarding | Identity, verification, and record-keeping mappings |
| RBI directions/guidelines on digital lending | Regulatory direction | Governs digital lending journeys: disclosure, data minimisation, service-provider arrangements, grievance redressal | Disclosure, data-minimisation, and grievance requirements |
| RBI fair-practices expectations for lenders | Regulatory direction / guidance | Loan-term disclosure and borrower communication | Communication and disclosure requirements |
| CERT-In cyber incident reporting directions (2022) | Regulatory direction | Incident-reporting obligations shape logging, audit, and reporting requirements | Audit and incident-reporting requirements |
| ISO/IEC 27001 Annex A control taxonomy | Industry standard | A stable, widely-recognised control vocabulary to map security requirements onto | **Clause identifiers plus team-written paraphrases only** — see copyright note |
| NIST Cybersecurity Framework | Security/control framework | Public-domain, freely redistributable structure for organising security functions | Security requirement organisation and gap checklists |
| Card-scheme security requirements (PCI DSS) | Contractual scheme requirement — **not law** | Applies only where cardholder data is processed; marginal for loan origination | **Secondary case study (payments) only** |
| Synthetic organisational policies | Organisational policy (fictional) | Demonstrates policy mapping without using any real institution's internal documents | Policy mapping demonstrations; clearly labelled fictional |
| Requirements-engineering and SDLC-selection literature | Engineering best practice | Quality criteria, SRS/use-case templates, SDLC selection rules | Quality checks, templates, rule base |

**Two standing constraints on curation:**

1. **Verify at curation time.** Regulatory instruments are amended and superseded. Every knowledge
   item records the exact version and effective date consulted, and the team verifies each against
   the issuing body's current published text when curating. No citation in this document should be
   treated as current without that check.
2. **Respect copyright.** Official statutes, regulator circulars, and public-domain frameworks may be
   extracted with citation. **ISO/IEC standards are copyrighted and not freely redistributable** —
   the KB stores only clause identifiers, titles where permissible, and team-written paraphrases,
   never copied normative text. The same discipline applies to any other licensed material.

### D.3 MVP capability set

| Capability | Core MVP form | Deliberately constrained because |
|---|---|---|
| Adaptive elicitation | Role templates + LLM follow-up generation + topic coverage tracking | Unbounded conversational autonomy is hard to evaluate |
| Extraction & structuring | Full §8 schema, span-linked to source | Core; not constrained |
| Clarification | Defect → targeted question → re-analysis on answer | Core loop; not constrained |
| Classification | Multi-label over the §9 categories with a confidence signal | Core; not constrained |
| Quality analysis | Ambiguity, incompleteness, untestability, duplication, undefined terms, missing source | Infeasibility check deferred (needs architectural context we will not have) |
| Conflict detection | Deterministic embedding shortlist → LLM pairwise adjudication | Exhaustive n² LLM comparison is cost-prohibitive |
| Compliance analysis | RAG mapping to curated normative items + gap report + mandated output language | One jurisdiction, curated corpus, no live feeds |
| Security & privacy analysis | Control-catalogue-driven derivation of missing controls | Full STRIDE threat modelling is secondary |
| **Risk analysis** | **Requirement-level risk identification, six-way categorisation, ordinal likelihood × impact, deterministic risk level, mitigation considerations, evidence links, high-risk escalation, risk register** | **No quantitative modelling, no risk-appetite framework, no KRI dashboards** |
| RAG | Hybrid retrieval over allowlisted KB, mandatory citations, confidence signal, escalation | Core; not constrained |
| Traceability | Full chain incl. risk; RTM export; orphan detection | Change-impact analysis is secondary |
| Human-in-the-loop | Eight enforced ReqPilot gates (G1–G7 from §16, G8 ours for risk); accept / reject / modify / regenerate | Core; not constrained |
| Documentation | SRS (incl. data and interface requirements), user stories, use cases, AC, compliance matrix, **risk register**, RTM, assumptions register, open issues → Markdown + DOCX | Diagrams and PDF are secondary |
| SDLC recommendation | Factor extraction → rules + MCDA → ranked list → LLM explanation → multi-role approval | Core; not constrained |
| Workflow generation | Phases, activities, roles, deliverables, testing requirements, security activities, compliance checkpoints, approval gates, entry/exit criteria | Core; not constrained |
| Audit | Append-only log + viewer + replay | Core; not constrained |
| Evaluation | Nine core metrics on the primary case study | Secondary metrics if time allows |

### D.4 MVP definition of done

A user can, in one sitting: create a loan-origination project → interview three stakeholder roles →
upload one policy document → obtain structured, classified requirements with source links → see
detected ambiguities and at least one genuine conflict → answer clarification questions → review the
compliance mapping and its gaps → **review the risk register with rated, categorised, evidence-linked
risks** → approve a baseline through role-gated approvals → export an SRS with a traceability matrix
and risk register → receive a ranked SDLC recommendation with justification and counter-arguments →
export a tailored SDLC workflow → and inspect the audit trail explaining how every one of those
outputs was produced.

---

## E. Scope tiering: Core MVP / Secondary / Out of scope

Problem-statement items that do not fit the MVP are **deferred with justification**, never silently
dropped. `[PS §n]` marks an item the problem statement asks for.

### E.1 Core MVP

All thirteen agent roles (J); all `[MVP]`-tagged requirements in G; the nine core metrics in O.1;
the artefacts in K including the risk register.

### E.2 Secondary / stretch — deferred with justification

| Item | Source | Why deferred | Earliest phase |
|---|---|---|---|
| Infeasibility detection | `[PS §10]` | Requires architectural and legacy context the MVP does not model | P12 |
| STRIDE threat enumeration per data flow | `[PS §4]` extension | Needs data-flow modelling; the control-catalogue approach already yields security requirements | P12 |
| Multi-factor authentication | `[PS §17]` | Infrastructure work with no research contribution; RBAC is implemented, MFA is not | P12 or documented as a deployment assumption |
| Application-level encryption at rest | `[PS §17]` | MVP relies on transport encryption plus OS/disk encryption as a stated deployment assumption | P12 |
| Emails, meeting notes, incident reports, audit findings, API/DB specs as inputs | `[PS §3]` | Each adds a parser and a trust class; transcripts and policy documents exercise the same pipeline | P12 |
| Process workflow diagrams | `[PS §12]` | Presentation layer on top of an already-generated workflow | P12 |
| Change-impact analysis | `[PROJ]` | Needs a stable trace graph first | P12 |
| SDLC weight sensitivity analysis | `[PROJ]` | Depends on a working scoring engine | P12 |
| Confidence calibration study | `[PROJ]` | Requires volume of labelled outcomes the MVP will not produce | P12 |
| Multi-jurisdiction comparison | `[PS §11]` | Curation cost is linear; retrieval precision drops without applicability filtering | Post-project |
| Supersession flagging of stale knowledge items | `[PS §17]` | Needs KB version history depth the MVP will not accumulate | P12 |
| PDF export, secondary metrics (O.2) | `[PROJ]` | Convenience and depth, not capability | P12 |

### E.3 Explicitly out of scope

Everything in C.3, plus: live regulatory feeds, voice/speech-to-text interviews, external tool
connectors (Jira/Confluence/email), free-form agent-to-agent negotiation, model fine-tuning,
portfolio dashboards, real-time collaborative editing, mobile applications, and a monitoring
platform (replaced by the audit log).

---

## F. Actors and stakeholders

### F.1 Primary human actors

| Actor | Goal | Key permissions | Approval authority |
|---|---|---|---|
| **Business Analyst / Requirements Engineer** | Drive the process; own requirement quality | Create projects, run interviews, edit requirements, request regeneration, override factor scores | None over compliance, security, risk, or SDLC gates |
| **Stakeholder / Interviewee** (business, product owner, operations, end-user proxy) | Answer questions; state needs | Participate in an assigned session; see own answers | None |
| **Compliance / Legal Officer** | Ensure regulatory correctness | Approve or reject compliance mappings and regulatory interpretations | Gates G2, and G1 co-approval |
| **Security Reviewer (InfoSec)** | Ensure security and privacy controls are present | Approve high-risk security requirements; add controls | Gates G3, G8 |
| **Risk Owner** *(may be the same person as the Security Reviewer or the PM in a student team)* | Own the risk register | Accept, modify, or close risk items; approve mitigations | Gate G8 |
| **Project Manager / Software Architect** | Decide how the project will be run | Approve the SDLC recommendation and generated workflow; edit the workflow | Gates G5, G6 |
| **Auditor** | Verify accountability after the fact | Read-only across requirements, evidence, risks, approvals, audit log | None (read-only) |
| **Knowledge-Base Administrator** | Keep the KB correct, typed, and versioned | Add, version, retire knowledge items | None |

In a student team one person will hold several of these roles. **The roles must nevertheless remain
distinct in the system**, because role-appropriate approval (`FR-HIL-003`) is one of the concepts
being demonstrated.

### F.2 Secondary / external stakeholders (represented, not users)

Regulators, external auditors, end customers, and enterprise risk-management functions. Their
concerns enter the system as knowledge-base content and requirement categories, not as logins.

### F.3 Non-human actors

The thirteen agent roles (J); the LLM provider (an external dependency and a trust boundary); the
knowledge base (the only authorised source of normative claims); the evaluation harness (a
non-interactive API consumer).

### F.4 Course-project stakeholders

The **course instructor / evaluator** (needs to see the demonstrated concepts and a defensible
evaluation) and the **student team** (needs phases that split across members and finish on schedule).

---

## G. Functional requirements

**Provenance tags.** `[PS §n]` = derived from the problem statement, with the step number.
`[PROJ]` = proposed by us to make the system implementable or testable; not a problem-statement
requirement. **Tier tags:** `[MVP]` core, `[SEC]` secondary/stretch, `[OOS]` out of scope for this
project but recorded for faithfulness.

### G.1 Project and session management

| ID | Requirement | Provenance | Tier |
|---|---|---|---|
| FR-PRJ-001 | Create a project with name, financial domain, description, and jurisdiction scope | `[PS §1]` | MVP |
| FR-PRJ-002 | Maintain project lifecycle state (Elicitation → Analysis → Review → Baselined) as a deterministic state machine | `[PROJ]` (enables §16) | MVP |
| FR-PRJ-003 | Register stakeholders with role, authority level, and assigned interview template | `[PS §2]` | MVP |
| FR-PRJ-004 | Isolate project data; no cross-project retrieval, context, or prompt content | `[PS §17]` | MVP |
| FR-PRJ-005 | Clone a project as a starting baseline | `[PROJ]` | SEC |

### G.2 Ingestion

| ID | Requirement | Provenance | Tier |
|---|---|---|---|
| FR-ING-001 | Upload source documents (`.txt`, `.md`, `.pdf`, `.docx`) typed as transcript, policy, normative source, legacy specification, or audit finding | `[PS §3]` | MVP |
| FR-ING-002 | Parse each document to text, chunk with character offsets, store immutably | `[PROJ]` (enables §6, §12 traceability) | MVP |
| FR-ING-003 | Classify, mask, and protect sensitive information before any text is submitted to an LLM; retain the unmasking map locally | `[PS §3, §17]` | MVP |
| FR-ING-004 | Record provenance for every source: filename, uploader, timestamp, content hash, sensitivity classification | `[PS §5, §20]` | MVP |
| FR-ING-005 | Accept emails, meeting notes, incident reports, audit findings, and API/database specifications as input types | `[PS §3]` | SEC |

### G.3 Elicitation (Stakeholder Interaction)

| ID | Requirement | Provenance | Tier |
|---|---|---|---|
| FR-ELI-001 | Conduct role-specific adaptive interviews using role templates rather than a fixed questionnaire | `[PS §2, §7]` | MVP |
| FR-ELI-002 | Cover the §7 topic checklist and track coverage per topic | `[PS §7]` | MVP |
| FR-ELI-003 | Ask follow-up questions when an answer is incomplete, vague, or inconsistent, bounded by a maximum depth per topic | `[PS §7]` | MVP |
| FR-ELI-004 | Persist every utterance with speaker, role, timestamp, and session | `[PS §20]` | MVP |
| FR-ELI-005 | Allow the analyst to pause and resume a session, and to record answers on a stakeholder's behalf | `[PROJ]` | MVP |
| FR-ELI-006 | Display live topic coverage and remaining uncovered topics | `[PROJ]` | MVP |
| FR-ELI-007 | Suggest which stakeholder role to interview next based on coverage gaps | `[PROJ]` | SEC |

### G.4 Extraction and structuring

| ID | Requirement | Provenance | Tier |
|---|---|---|---|
| FR-EXT-001 | Extract candidate requirements from utterances and documents, each linked to its exact source span | `[PS §8, §12]` | MVP |
| FR-EXT-002 | Normalise every requirement to the §8 schema: ID, statement, category, source stakeholder, business justification, priority, dependencies, assumptions, acceptance criteria, applicable regulations, risk level, confidence score, approval status | `[PS §8]` | MVP |
| FR-EXT-003 | Rewrite statements into declarative "The system shall …" form while preserving meaning and retaining the original wording | `[PROJ]` (supports §10 testability) | MVP |
| FR-EXT-004 | Assign stable, human-readable IDs (`FR-<DOMAIN>-nnn`, `NFR-<DOMAIN>-nnn`) | `[PS §8]` | MVP |
| FR-EXT-005 | Detect and merge near-duplicate extractions, retaining all source links | `[PS §10]` | MVP |
| FR-EXT-006 | Generate acceptance criteria for each requirement (Given/When/Then format) | `[PS §12]`; format `[PROJ]` | MVP |
| FR-EXT-007 | Never emit a requirement without at least one source link | `[PS §10]` (missing source) | MVP |

### G.5 Classification

| ID | Requirement | Provenance | Tier |
|---|---|---|---|
| FR-CLS-001 | Multi-label classify each requirement into the §9 categories (business, stakeholder, functional, security, privacy, regulatory, performance, availability/reliability, usability, data-management, integration, audit/reporting, operational/maintenance) | `[PS §9]` | MVP |
| FR-CLS-002 | Attach a per-label confidence signal; below-threshold labels enter the human review queue | `[PS §6]` | MVP |
| FR-CLS-003 | Allow human override of any label, recorded as a versioned change | `[PS §16]` | MVP |
| FR-CLS-004 | Use accumulated human overrides as few-shot examples | `[PROJ]` | SEC |

### G.6 Quality analysis

| ID | Requirement | Provenance | Tier |
|---|---|---|---|
| FR-QAL-001 | Detect ambiguity and report the offending span | `[PS §10]` | MVP |
| FR-QAL-002 | Detect incompleteness (missing actor, trigger, condition, or measurable outcome) | `[PS §10]` | MVP |
| FR-QAL-003 | Detect lack of testability | `[PS §10]` | MVP |
| FR-QAL-004 | Detect duplication and overlap | `[PS §10]` | MVP |
| FR-QAL-005 | Detect undefined terminology against the project glossary | `[PS §10]` | MVP |
| FR-QAL-006 | Detect missing source attribution | `[PS §10]` | MVP |
| FR-QAL-007 | Route every detected defect to the Clarification role with a generated question | `[PS §10]` | MVP |
| FR-QAL-008 | Detect missing security or compliance controls (delegated to `FR-CMP-002` / `FR-SEC-001`) | `[PS §10]` | MVP |
| FR-QAL-009 | Assess infeasibility against stated legacy and integration constraints | `[PS §10]` | SEC |

### G.7 Conflict detection

| ID | Requirement | Provenance | Tier |
|---|---|---|---|
| FR-CNF-001 | Detect inconsistencies and contradictions across the full requirement set | `[PS §10]` | MVP |
| FR-CNF-002 | Detect conflicting stakeholder expectations, naming both stakeholders | `[PS §10]` | MVP |
| FR-CNF-003 | Report each conflict with both requirement IDs, conflict type, and rationale | `[PROJ]` | MVP |
| FR-CNF-004 | Shortlist candidate pairs deterministically (embedding similarity) before LLM adjudication, to bound cost | `[PROJ]` | MVP |
| FR-CNF-005 | Route conflict resolution to a human decision (gate G4); the system proposes, it does not resolve | `[PS §16]` | MVP |

### G.8 Clarification

| ID | Requirement | Provenance | Tier |
|---|---|---|---|
| FR-CLR-001 | Generate a targeted clarification question bound to a specific requirement and a specific defect | `[PS §4]` | MVP |
| FR-CLR-002 | Maintain an open-issues list with status, assignee, and age | `[PS §12]` | MVP |
| FR-CLR-003 | Re-run extraction and quality analysis when a clarification is answered, recording the resulting version change | `[PROJ]` | MVP |
| FR-CLR-004 | Allow an analyst to dismiss a clarification with a recorded reason | `[PROJ]` | MVP |

### G.9 Retrieval-grounded generation and knowledge base

| ID | Requirement | Provenance | Tier |
|---|---|---|---|
| FR-RAG-001 | Maintain a curated, versioned knowledge base in which every item records source, **type (per C.1)**, jurisdiction, effective date, version, applicability, and retrieval date | `[PS §5]`; type field `[PROJ]` | MVP |
| FR-RAG-002 | Classify the query, then retrieve relevant items restricted to an allowlist of approved sources | `[PS §6, §17]` | MVP |
| FR-RAG-003 | Provide retrieved evidence to the LLM and attach citations resolvable to the exact chunk for every compliance, security, or risk claim | `[PS §6]` | MVP |
| FR-RAG-004 | Attach a confidence signal to generated items; below threshold routes to human review (see H.1 for the definition of "confidence") | `[PS §6]` | MVP |
| FR-RAG-005 | Escalate for human review when retrieval returns nothing relevant, rather than answering from the model's parametric memory | `[PS §6]` | MVP |
| FR-RAG-006 | Provide knowledge-base administration: add, version, retire, with an audit record | `[PS §5, §17]` | MVP |
| FR-RAG-007 | Flag requirements whose supporting knowledge item has been superseded | `[PS §17]` | SEC |

### G.10 Compliance analysis

| ID | Requirement | Provenance | Tier |
|---|---|---|---|
| FR-CMP-001 | Map each requirement to potentially applicable normative sources and controls, with supporting evidence | `[PS §11]` | MVP |
| FR-CMP-002 | Identify compliance gaps: expected controls for the domain with no covering requirement | `[PS §11]` | MVP |
| FR-CMP-003 | Emit implied approval and audit checkpoints, and data-retention and reporting obligations | `[PS §11]` | MVP |
| FR-CMP-004 | Never make a final legal determination; route every high-impact interpretation to the Compliance Officer (gate G2) | `[PS §11]` | MVP |
| FR-CMP-005 | Report the applicable jurisdiction and source type for every mapping | `[PS §11]` | MVP |
| FR-CMP-006 | **Enforce mandated output language** (C.1) by deterministic post-processing of generated compliance text, not by prompt instruction alone | `[PROJ]` (enforces §11) | MVP |
| FR-CMP-007 | Display a standing advisory notice on every compliance view and generated compliance artefact | `[PROJ]` (enforces §11) | MVP |
| FR-CMP-008 | Compare mappings across jurisdictions and report divergence | `[PS §11]` | OOS |

### G.11 Security and privacy analysis

| ID | Requirement | Provenance | Tier |
|---|---|---|---|
| FR-SEC-001 | Derive missing security requirements for the system under analysis (authentication, authorisation, encryption, logging, session management, transaction integrity, fraud controls) | `[PS §4, §10]` | MVP |
| FR-SEC-002 | Derive privacy requirements (data minimisation, consent, retention, subject rights) | `[PS §4, §9]` | MVP |
| FR-SEC-003 | Assign a risk level to each derived security and privacy requirement and route high-risk items to the Security Reviewer (gate G3) | `[PS §16]` | MVP |
| FR-SEC-004 | Produce a STRIDE threat enumeration per data flow | `[PROJ]` | SEC |

### G.12 Risk analysis *(restored to core MVP — change C1)*

| ID | Requirement | Provenance | Tier |
|---|---|---|---|
| FR-RSK-001 | Identify risks associated with each requirement, and project-level risks arising from the requirement set as a whole | `[PS §4]` | MVP |
| FR-RSK-002 | Categorise each risk as **business, technical, security, privacy, compliance, or operational** | `[PS §4]` | MVP |
| FR-RSK-003 | Estimate **likelihood** and **impact** on a simple explainable ordinal scale (proposed: 1–3 Low/Medium/High, with written rationale for each rating) | `[PROJ]` — scheme is ours | MVP |
| FR-RSK-004 | Derive the overall risk level/priority **deterministically** from a published likelihood × impact matrix; the LLM proposes the ratings, the matrix computes the level | `[PROJ]` | MVP |
| FR-RSK-005 | Suggest mitigation considerations for each risk, explicitly labelled as suggestions requiring human validation | `[PS §12]` | MVP |
| FR-RSK-006 | Link every risk to its originating requirement **and** to the supporting evidence (source span and/or knowledge-item citation) | `[PS §12]` | MVP |
| FR-RSK-007 | Flag high-severity risks for human review; a high-severity risk blocks baseline approval until reviewed (gate G8) | `[PS §16]` | MVP |
| FR-RSK-008 | Generate a **Risk Register** artefact (the "threat and risk register" of §12) containing all risks with category, ratings, level, mitigations, owner, status, and links | `[PS §12]` | MVP |
| FR-RSK-009 | Expose aggregate risk measures (e.g. count and severity distribution of security and compliance risks) as inputs to the SDLC factor profile | `[PS §13]` | MVP |
| FR-RSK-010 | Allow a human to add, edit, accept, or close a risk with a recorded rationale | `[PS §16]` | MVP |
| FR-RSK-011 | **Scope guard:** risks are project/engineering risks only. ReqPilot shall not compute borrower credit risk, customer risk ratings, fraud scores, or any lending decision input | `[PROJ]` (enforces D.1) | MVP |
| FR-RSK-012 | Quantitative risk modelling, risk-appetite frameworks, and KRI dashboards | `[PROJ]` — our scope decision, recorded so the boundary is explicit | OOS |

### G.13 Traceability and evidence

| ID | Requirement | Provenance | Tier |
|---|---|---|---|
| FR-TRC-001 | Maintain typed trace links across the full chain: stakeholder input → requirement → classification → risk / conflict / compliance / security analysis → acceptance criterion → generated artefact → evidence/source → control/normative item → approval | `[PS §12]` | MVP |
| FR-TRC-002 | Generate and export a Requirements Traceability Matrix | `[PS §1, §12]` | MVP |
| FR-TRC-003 | Report traceability coverage; flag orphan requirements, unsourced statements, and unlinked risks | `[PROJ]` | MVP |
| FR-TRC-004 | Preserve trace links across requirement versions | `[PROJ]` | MVP |
| FR-TRC-005 | Perform change-impact analysis across the trace graph | `[PROJ]` | SEC |

### G.14 Human-in-the-loop approval — eight enforced ReqPilot gates

**Gate accounting — read this carefully, because the eights are a coincidence, not a correspondence.**

§16 lists **eight mandatory human approval categories**. ReqPilot also enforces **eight gates**. They
are *not* the same eight:

- **§16 categories 1–7** (final requirement baseline, regulatory interpretations, high-risk security
  requirements, conflicting stakeholder decisions, architecture-critical requirements, SDLC
  selection, changes to approved requirements) map one-to-one onto **ReqPilot-native gates G1–G7**.
- **§16 category 8 — production-readiness decisions — is `[PS §16]` and is NOT dropped**, but it is
  *not* a ReqPilot platform gate. ReqPilot does not manage the target project's delivery or
  deployment, so a production-readiness approval inside ReqPilot would have nothing to gate. It is
  instead emitted as an approval gate **inside the generated project's SDLC workflow**
  (`FR-WFL-003`), which is where the target project actually reaches production readiness.
- **G8 is an additional `[PROJ]` gate**, introduced by us because Risk Analysis is part of the MVP
  (change C1). It has no §16 counterpart and is not claimed to have one.

So: **7 of ReqPilot's 8 gates derive from §16; 1 is ours; and §16's eighth category is satisfied
outside the gate set, in generated workflows.** All eight §16 categories remain represented in the
system.

| Gate | Trigger | Approver role | Provenance | §16 category |
|---|---|---|---|---|
| G1 | Final requirement baseline | Analyst + Compliance Officer | `[PS §16]` | 1 |
| G2 | High-impact regulatory interpretation | Compliance Officer | `[PS §16]` | 2 |
| G3 | High-risk security requirement | Security Reviewer | `[PS §16]` | 3 |
| G4 | Conflicting stakeholder decision | Analyst + affected stakeholders | `[PS §16]` | 4 |
| G5 | Architecture-critical requirement | Architect / PM | `[PS §16]` | 5 |
| G6 | SDLC selection | PM, Architect, Security, Compliance | `[PS §14, §16]` | 6 |
| G7 | Change to an already-approved requirement | Original approver's role | `[PS §16]` | 7 |
| G8 | High-severity risk item | Risk Owner / Security Reviewer | `[PROJ]` — **no §16 counterpart** | — |
| *(not a ReqPilot gate)* | Production-readiness decision | Target project's approvers | `[PS §16]` | **8 — implemented in the generated workflow via `FR-WFL-003`** |

| ID | Requirement | Provenance | Tier |
|---|---|---|---|
| FR-HIL-001 | Enforce gates G1–G8 in the application layer, not by prompt instruction | `[PS §16]` for G1–G7; `[PROJ]` for G8 | MVP |
| FR-HIL-002 | Offer accept, reject, modify, and request-regeneration on every AI-produced output | `[PS §16]` | MVP |
| FR-HIL-003 | Enforce role-appropriate approval per the gate table | `[PS §14, §16]` | MVP |
| FR-HIL-004 | Prevent any unapproved requirement from entering a baseline or a generated artefact | `[PS §12, §16]` | MVP |
| FR-HIL-005 | Record approver identity, role, timestamp, decision, comment, and the exact version approved | `[PS §20]` | MVP |
| FR-HIL-006 | Present a single review queue ordered by risk severity and confidence | `[PROJ]` | MVP |

### G.15 Documentation generation

| ID | Requirement | Provenance | Tier |
|---|---|---|---|
| FR-DOC-001 | Generate a Software Requirements Specification from approved requirements using a versioned template | `[PS §12]` | MVP |
| FR-DOC-002 | Generate user stories with acceptance criteria | `[PS §12]` | MVP |
| FR-DOC-003 | Generate use-case descriptions | `[PS §12]` | MVP |
| FR-DOC-004 | Generate a compliance-control matrix | `[PS §12]` | MVP |
| FR-DOC-005 | Generate the assumptions and dependency register and the open-issues list | `[PS §12]` | MVP |
| FR-DOC-006 | **Generate the threat and risk register** | `[PS §12]` | MVP |
| FR-DOC-007 | **Generate data-requirements and interface-requirements sections** | `[PS §12]` | MVP |
| FR-DOC-008 | Link every generated artefact section back to its contributing requirement IDs | `[PS §12]` | MVP |
| FR-DOC-009 | Stamp every artefact with generation timestamp, model identifier, prompt version, and knowledge-base version | `[PS §17]` | MVP |
| FR-DOC-010 | Export to Markdown and DOCX | `[PROJ]` | MVP |
| FR-DOC-011 | Generate process workflow diagrams | `[PS §12]` | SEC |
| FR-DOC-012 | Export to PDF | `[PROJ]` | SEC |

### G.16 SDLC decision factors and recommendation

| ID | Requirement | Provenance | Tier |
|---|---|---|---|
| FR-SDL-001 | Derive the project factor profile from the §13 factors — requirement stability, regulatory criticality, security risk, project complexity, system size, legacy-system dependence, expected frequency of change, need for continuous delivery, stakeholder availability, testing and documentation requirements, budget and schedule constraints, need for formal verification, consequences of system failure — scoring each with supporting evidence | `[PS §13]` | MVP |
| FR-SDL-002 | Consume aggregate risk measures from the risk register as factor inputs (security risk, consequences of failure, regulatory criticality) | `[PS §13]` | MVP |
| FR-SDL-003 | Allow a human to override any factor score, with the override and its reason recorded | `[PROJ]` | MVP |
| FR-SDL-004 | Score SDLC candidates (Waterfall, V-Model, Spiral, Agile, DevSecOps, and hybrids) using deterministic rules combined with weighted multi-criteria decision analysis | `[PS §14]` | MVP |
| FR-SDL-005 | Produce **ranked** recommendations with suitability scores rather than a single label | `[PS §14]` | MVP |
| FR-SDL-006 | Generate an explanation of the recommendation that is grounded strictly in the derived factors and their evidence — the LLM explains and contextualises the computed result, it does not select it | `[PS §14]` | MVP |
| FR-SDL-007 | Present counter-arguments: why the runner-up was not selected, and what change would reverse the ranking | `[PROJ]` | MVP |
| FR-SDL-008 | Require multi-role approval of the final selection by the project manager, architect, security team, and compliance officer (gate G6) | `[PS §14, §16]` | MVP |
| FR-SDL-009 | Perform sensitivity analysis on the scoring weights | `[PROJ]` | SEC |

### G.17 Project-specific SDLC workflow generation

| ID | Requirement | Provenance | Tier |
|---|---|---|---|
| FR-WFL-001 | Generate a workflow customised to the selected SDLC with development phases, activities within each phase, responsible roles, and expected deliverables | `[PS §15]` | MVP |
| FR-WFL-002 | Insert security activities derived from the project's own security requirements and risk register (for example threat modelling during design, static security testing during implementation) | `[PS §15]` | MVP |
| FR-WFL-003 | Insert compliance checkpoints and human approval gates derived from the project's own compliance mapping — including the production-readiness approval of §16, which falls inside the generated workflow rather than inside ReqPilot | `[PS §15, §16]` | MVP |
| FR-WFL-004 | Specify testing requirements per phase (for example transaction-integrity testing during validation) | `[PS §15]` | MVP |
| FR-WFL-005 | Specify entry and exit criteria for each phase | `[PS §15]` | MVP |
| FR-WFL-006 | Specify traceability requirements for each phase | `[PS §15]` | MVP |
| FR-WFL-007 | Allow the project manager to edit the generated workflow, with a change log | `[PROJ]` | MVP |
| FR-WFL-008 | Export the workflow to Markdown and DOCX | `[PROJ]` | MVP |

### G.18 Platform security controls — §17 coverage map

§17 lists twelve controls for securing the agentic platform. None are dropped; each has an explicit
MVP treatment.

| §17 control | MVP treatment | Tier |
|---|---|---|
| Role-based access control | `FR-ADM-002` — enforced server-side | MVP |
| Multi-factor authentication | Deferred; password authentication in MVP, MFA documented as a deployment assumption | SEC |
| Encryption in transit and at rest | HTTPS in deployment; at-rest relies on OS/disk encryption as a stated assumption; application-level field encryption deferred | MVP (partial) / SEC |
| Sensitive-data masking | `FR-ING-003` | MVP |
| Secure prompt and output filtering | `FR-ADM-005` (input) and `FR-CMP-006` (output) | MVP |
| Agent-level permissions | `FR-ADM-003` | MVP |
| Retrieval-source allowlisting | `FR-RAG-002` | MVP |
| Protection against prompt injection | `FR-ADM-005`, plus the governance-outside-the-LLM principle (J.1) | MVP |
| Session isolation | `FR-PRJ-004` | MVP |
| Audit logging | `FR-AUD-001`–`005` | MVP |
| Data-retention policies | `FR-ADM-006` — documented policy plus deletion cascade | MVP |
| Model and knowledge-base versioning | `FR-DOC-009`, `FR-RAG-006` | MVP |

### G.19 Access control and administration

| ID | Requirement | Provenance | Tier |
|---|---|---|---|
| FR-ADM-001 | Support the roles in F.1 | `[PS §2, §17]` | MVP |
| FR-ADM-002 | Enforce role-based access control at the API layer, not only in the UI | `[PS §17]` | MVP |
| FR-ADM-003 | Enforce agent-level least privilege: each role receives only the data and tools its task requires | `[PS §17]` | MVP |
| FR-ADM-004 | Multi-factor authentication | `[PS §17]` | SEC |
| FR-ADM-005 | Filter prompts and treat all retrieved and uploaded content as untrusted data, never as instructions | `[PS §17]` | MVP |
| FR-ADM-006 | Apply a documented data-retention policy; project deletion cascades to all derived data | `[PS §17]` | MVP |

### G.20 Audit

| ID | Requirement | Provenance | Tier |
|---|---|---|---|
| FR-AUD-001 | Record every agent run: role, prompt template and version, inputs, retrieved evidence IDs, output, model identifier, latency, token usage, confidence signal | `[PS §20]` | MVP |
| FR-AUD-002 | Record every human action: edits, approvals, rejections, overrides, dismissals, risk decisions | `[PS §20]` | MVP |
| FR-AUD-003 | Provide an audit viewer filterable by requirement, role, user, and time | `[PROJ]` | MVP |
| FR-AUD-004 | Support replay: reconstruct how any requirement or risk reached its current state | `[PROJ]` | MVP |
| FR-AUD-005 | Make audit records append-only through the application | `[PS §17]` | MVP |

### G.21 Evaluation

| ID | Requirement | Provenance | Tier |
|---|---|---|---|
| FR-EVL-001 | Load a gold-standard dataset and compute the core metrics (O.1) | `[PS §19]` | MVP |
| FR-EVL-002 | Produce a reproducible evaluation report from a single command | `[PROJ]` | MVP |
| FR-EVL-003 | Record manual-baseline timings for the time-saved comparison | `[PS §19]` | MVP |
| FR-EVL-004 | Compute the secondary metrics (O.2) | `[PS §19]` / `[PROJ]` | SEC |

**Verified count** (recomputed from the tables above, not estimated): **132 functional requirements
across 20 FR-bearing groups**.

Section G contains **21 subsections (G.1–G.21)**, of which **20 are FR-bearing groups**. The
twenty-first, **G.18 (Platform security controls — §17 coverage map), is a coverage section and
contains no functional-requirement rows**: it maps each of §17's twelve controls onto requirements
that live in other groups (`FR-ADM`, `FR-ING`, `FR-RAG`, `FR-PRJ`, `FR-CMP`, `FR-AUD`, `FR-DOC`).
Counting G subsections therefore yields 21; counting FR-bearing groups yields 20. No requirement is
missing.

| Split | Count |
|---|---|
| Problem-statement-derived (`[PS §n]`) | 99 — of which 4 are mixed, citing a `[PS]` obligation realised partly by a `[PROJ]` mechanism (`FR-EXT-006`, `FR-RAG-001`, `FR-HIL-001`, `FR-EVL-004`) |
| Project-proposed (`[PROJ]`) only | 33 |
| Untagged | **0** — every functional requirement carries an explicit provenance tag |
| Core MVP | 117 |
| Secondary / stretch | 13 |
| Out of scope (recorded for faithfulness, not built) | 2 |

**75% of the functional requirements trace to the problem statement.** The remaining 25% are
enabling mechanisms we propose — state machines, review queues, export formats, version tracking,
scope guards — and every one of them is tagged as ours.

---

## H. Non-functional requirements

Split as instructed into **H.1 problem-statement-derived quality attributes** (qualitative, from the
source) and **H.2 proposed project-specific engineering targets** (numeric, ours).

### H.1 Problem-statement-derived quality attributes

These are qualitative obligations the source text supports. They carry no numbers.

| ID | Quality attribute | Statement | Source |
|---|---|---|---|
| QA-SEC | Security | The platform enforces the §17 control set per G.18 | §17 |
| QA-PRV | Privacy | Sensitive information is classified, masked, and protected before submission to an LLM; only synthetic or anonymised data is used | §3, §17, §19 |
| QA-GRD | Groundedness | Outputs are grounded in retrieved, approved sources; unsupported or low-confidence output escalates rather than asserting | §6 |
| QA-EXP | Explainability | Every automated decision is presented with its evidence, rationale, and confidence signal — never as a bare verdict | Research-challenges paragraph |
| QA-AUD | Auditability | All prompts, retrieved evidence, agent decisions, user changes, approvals, and generated artefacts are recorded | §20 |
| QA-TRC | Traceability | Every generated item remains linked to its originating stakeholder statement and supporting document | §12 |
| QA-HUM | Human oversight | Authorised humans retain responsibility for regulatory interpretation, requirement approval, and SDLC adoption | §16, §20 |
| QA-ACC | Accountability | Responsibility for each decision is attributable to a named human or a logged agent run | §20 |
| QA-MNT | Knowledge maintainability | Regulatory knowledge and decision rules can be updated without redeveloping the system | §5, §20 |
| QA-INT | Interoperability | Outputs are usable by other tools and processes | Research-challenges paragraph |
| QA-BIA | Bias awareness | Bias is treated as an explicit risk to be addressed | Research-challenges paragraph |

### H.2 Proposed project-specific engineering targets

> **These numbers are not from the problem statement.** They are engineering targets **we** propose,
> so the project has something testable to aim at. They are **provisional** and several are
> explicitly scheduled for re-baselining after Phase P3, when we first have measured behaviour
> instead of guesses. None of them should be cited as a requirement of the original specification.

| ID | Target | Proposed value | Why this value | Status |
|---|---|---|---|---|
| ET-01 | Interview turn response | ≤ 5 s median, ≤ 12 s p95 | Conversational usability judgement | Provisional |
| ET-02 | Full analysis of a 3,000-word transcript | ≤ 3 min | Must fit inside a live demo | Provisional |
| ET-03 | SRS generation, 60 requirements | ≤ 90 s | Demo practicality | Provisional |
| ET-04 | Hardware envelope | Developer laptop, 8–16 GB RAM, no GPU | The team's actual hardware | Fixed |
| ET-05 | Concurrent users | 5 | Classroom demonstration, not production load | Fixed |
| ET-06 | Retrieval recall@5 | ≥ 0.80 on a 20-question probe set | Phase P2 exit gate; a reasonable bar for a small curated corpus | **Re-baseline after P2** |
| ET-07 | Extraction F1 | ≥ 0.75 | Placeholder only | **Re-baseline after P3** |
| ET-08 | Cost per full project run | Within a documented token budget, tracked per run | Limited API credits | Provisional |
| ET-09 | Fresh-machine setup time | ≤ 15 min | Team onboarding | Provisional |
| ET-10 | Test suite runs offline | 100% of tests pass with recorded LLM fixtures, no API calls | Cost and determinism in CI | Fixed |
| ET-11 | Usability (SUS) | ≥ 70 | Conventional "above average" threshold; **secondary metric** | Provisional |
| ET-12 | Prompt-injection resistance | ≥ 90% of a crafted suite blocked, **zero** approval-gate bypasses | The zero-bypass part follows from J.1; the 90% is our own bar | Provisional; **secondary metric** |

### H.3 Development-quality attributes (ours, qualitative)

| ID | Attribute | Statement |
|---|---|---|
| DQ-01 | Testability | Every agent role is independently testable with fixed inputs; LLM calls are mockable via recorded fixtures |
| DQ-02 | Determinism where possible | Deterministic components (rule engine, scoring matrix, RBAC, traceability, gates) contain no LLM call and are unit-tested |
| DQ-03 | Data-driven configuration | Adding a normative source, control, risk-matrix cell, or SDLC rule requires data changes, not code changes |
| DQ-04 | Prompt versioning | Prompts are versioned artefacts stored outside application code |
| DQ-05 | Portability | Cross-platform; primary development environment is Windows |
| DQ-06 | Open formats | Markdown, DOCX, CSV, JSON |

---

## I. Major system modules

**Twelve modules (M1–M12).** Note that **module ≠ agent role**: the orchestration layer is one module
among twelve, and most of the guarantees the problem statement demands live in deterministic modules
(see J.1).

| # | Module | Responsibility | Depends on |
|---|---|---|---|
| M1 | **Web interface** | Interview console, requirement workbench, review/approval queue, compliance view, **risk register view**, artefact viewer, SDLC dashboard, audit viewer, KB administration | M2 |
| M2 | **Application / API layer** | Request handling, authentication, authorisation, project lifecycle state machine, input validation | M3–M12 |
| M3 | **Orchestration layer** | Agent-role registry, execution order, shared run context, retries, gate checks, per-role permission enforcement | M4–M7, M9, M10 |
| M4 | **LLM gateway** | The single choke point for model calls: prompt assembly from versioned templates, masking, injection filtering, schema-validated output parsing, token accounting, caching | M11 |
| M5 | **Knowledge & retrieval service** | KB ingestion, typing per C.1, chunking, embedding, hybrid retrieval, allowlist enforcement, citation resolution, KB versioning | — |
| M6 | **Requirements repository** | Requirements, versions, classifications, defects, conflicts, **risks**, compliance mappings, trace links, evidence | — |
| M7 | **Rule & scoring engine** | Declarative rules: expected-control checklists per domain, quality heuristics, **risk likelihood × impact matrix**, SDLC scoring rules and weights, workflow templates | — |
| M8 | **Artefact generator** | Template-driven assembly of SRS, user stories, use cases, compliance matrix, **risk register**, RTM, workflow document; Markdown/DOCX export | M6, M7 |
| M9 | **Approval & workflow service** | Gate definitions G1–G8, approval state machine, role checks, review queue, baseline freezing | M6, M10 |
| M10 | **Audit & trace service** | Append-only event log for agent runs and human actions; replay reconstruction | — |
| M11 | **Guardrails layer** | Sensitive-data detection and masking, prompt-injection detection, output language enforcement, retrieval allowlist enforcement, role permission checks | — |
| M12 | **Evaluation harness** | Gold dataset loading, core and secondary metric computation, report generation, manual-baseline capture | M2, M6 |

---

## J. The thirteen agent roles

All **thirteen roles from §4 are preserved**. What changes from role to role is the *implementation
technique*, chosen for what the responsibility actually requires.

> **An "agent role" is an architectural responsibility, not a commitment to an autonomous LLM
> process.** A role may be realised as an LLM-powered agent, a hybrid of deterministic computation
> and LLM judgement, a rule engine, a state machine, a workflow node, or a conventional service.
> Forcing every role to be an autonomous LLM agent would make the system more expensive, less
> testable, less auditable, and *less* academically defensible — it would put governance guarantees
> at the mercy of a probabilistic component. Choosing the right technique per role, and justifying
> it, is itself a software-engineering contribution.
>
> **The assignments below are a Phase 0 proposal, to be confirmed or revised in Phase 1.**

| # | Role (§4) | Proposed implementation kind | Input | Output | Escalates when | Tier |
|---|---|---|---|---|---|---|
| 1 | **Coordinator** | Deterministic orchestration service + workflow state machine | Project state, pending work | Role invocations, run records, gate checks | A gate is unsatisfied, or a role fails repeatedly | MVP |
| 2 | **Stakeholder Interaction** | LLM-powered agent over a deterministic template + coverage tracker | Role template, coverage state, conversation so far | Next question, recorded utterance | Coverage stalls; stakeholder disengages | MVP |
| 3 | **Requirement Extraction** | LLM-powered agent with schema-constrained output | Utterances, document chunks | Structured candidate requirements with source spans | A statement is too vague to structure at all | MVP |
| 4 | **Clarification** | LLM question generation + deterministic defect→question routing | Requirement + detected defect | Targeted question, open issue | An issue stays unresolved past a threshold | MVP |
| 5 | **Classification** | LLM-powered agent + deterministic threshold routing | Requirement text | Multi-label categories with confidence signal | Confidence below threshold | MVP |
| 6 | **Conflict Detection** | Hybrid: deterministic embedding shortlist → LLM pairwise adjudication | Full requirement set | Conflict pairs with type and rationale | Always, for stakeholder disagreement (G4) | MVP |
| 7 | **Compliance** | LLM-powered, RAG-grounded + rule engine for expected-control checklists + deterministic output-language enforcement | Requirement + retrieved normative items | Candidate mappings with citations, gap list | Every high-impact interpretation (G2) | MVP |
| 8 | **Security & Privacy** | Hybrid: control-catalogue rule engine + LLM derivation | Requirement set + domain control catalogue | Proposed security/privacy requirements with risk level | High-risk controls (G3) | MVP |
| 9 | **Risk Analysis** | **Hybrid: LLM identifies and rates; deterministic likelihood × impact matrix computes the level** | Requirements + compliance and security analysis results | Categorised, rated, evidence-linked risk items with mitigation considerations | High-severity risks (G8) | **MVP** |
| 10 | **SDLC Selection** | Hybrid: deterministic rules + MCDA scoring; **LLM produces the explanation only** | Factor profile (incl. risk aggregates) | Ranked candidates, scores, justification, counter-arguments | Always — selection requires approval (G6) | MVP |
| 11 | **Documentation** | LLM-assisted template filling + deterministic assembly and linking | Approved requirements, risks, mappings + templates | SRS, stories, use cases, matrices, risk register | An approved item is missing a mandatory field | MVP |
| 12 | **Validation** | Primarily deterministic checklist verification; optional LLM check for semantic completeness | Candidate baseline | Pass/fail checklist with reasons | Any check fails | MVP |
| 13 | **Human Approval** | Deterministic workflow service + state machine — **no LLM involvement at all** | Pending items | Routed approval tasks, recorded decisions | By definition, always | MVP |

**Summary of proposed kinds:** 4 LLM-powered agents (2, 3, 4, 5); 5 hybrids (6, 7, 8, 9, 10); 1
LLM-assisted deterministic assembler (11); 3 deterministic services (1, 12, 13).

### J.1 Architectural principle — governance lives outside the LLM

**Critical governance mechanisms are enforced by deterministic application and orchestration logic,
never by prompt instruction alone.** A model can be persuaded, confused, or injected into; a state
machine cannot. Specifically, the following are enforced in code:

1. **RBAC and permissions** (`FR-ADM-002`, `FR-ADM-003`)
2. **Approval gates G1–G8** (`FR-HIL-001`)
3. **Workflow state transitions** (`FR-PRJ-002`)
4. **Audit logging** (`FR-AUD-001`, `FR-AUD-005`)
5. **Artefact versioning** (`FR-DOC-009`)
6. **Traceability link creation and preservation** (`FR-TRC-001`, `FR-TRC-004`)
7. **Access restrictions and project isolation** (`FR-PRJ-004`)
8. **Human-in-the-loop requirements** (`FR-HIL-004`)

Consequences that follow directly: an agent role cannot approve its own output; a prompt-injected
document cannot bypass a gate, because the gate is not reading the document; and "the LLM was told
to log this" is never an acceptable implementation of an audit requirement. This principle is also
the strongest available mitigation for risk R2 in section N.

### J.2 Orchestration pattern — **not being decided in Phase 0**

Three candidate patterns, to be evaluated with reasoning in Phase 1. **No framework is being
selected here.**

1. **Deterministic supervisor with typed contracts** — the coordinator runs a defined graph; roles
   have typed inputs and outputs. Most testable, most auditable, cheapest; least "autonomous" in
   appearance.
2. **Blackboard** — roles observe shared state and trigger on conditions. More emergent; harder to
   bound, cost, and evaluate.
3. **Conversational multi-agent** — roles message each other freely. Best demo; worst
   reproducibility, cost, and auditability.

Preliminary leaning, stated for transparency and **not a decision**: a deterministic backbone with
selective autonomy at the points where genuine agent judgement adds value — which question to ask
next, whether to re-open a requirement, whether to escalate. Phase 1 will evaluate all three against
the criteria in J.1 and H.

---

## K. Inputs and outputs

### K.1 Inputs

| Input | Form | Source | Tier |
|---|---|---|---|
| Project brief | Structured form (domain, jurisdiction scope, description) | Analyst | MVP |
| Live interview answers | Chat turns | Stakeholders | MVP |
| Interview transcripts | `.txt`, `.md`, `.docx` | Upload | MVP |
| Policy and procedure documents | `.pdf`, `.docx`, `.md` | Upload | MVP |
| Normative-source extracts and control catalogues | Typed, versioned KB items | KB administrator | MVP |
| Existing requirement documents | `.docx`, `.md` | Upload | MVP |
| Legacy-system notes | Free text or upload | Analyst | MVP |
| Human decisions | Approve / reject / modify / regenerate | Approver roles | MVP |
| Risk decisions | Accept / modify / close, with rationale | Risk Owner | MVP |
| Factor overrides | Ordinal scores with reasons | Analyst, PM | MVP |
| Emails, meeting notes, incident reports, audit findings, API/DB specs | Various | Upload | SEC `[PS §3]` |

### K.2 Outputs

| Output | Form | Tier |
|---|---|---|
| Structured requirement set | UI + JSON/CSV | MVP |
| Classification results with confidence signal | UI | MVP |
| Quality-defect report | UI + document | MVP |
| Conflict report | UI + document | MVP |
| Clarification questions and open-issues list | UI + document | MVP |
| Compliance mapping with citations, gaps, and advisory notice | UI + compliance-control matrix | MVP |
| Derived security and privacy requirements | Requirement set | MVP |
| **Risk register** (threat and risk register) | **UI + Markdown + DOCX** | **MVP** |
| Software Requirements Specification, incl. data and interface requirements | Markdown + DOCX | MVP |
| User stories with acceptance criteria | Markdown + DOCX | MVP |
| Use-case descriptions | Markdown + DOCX | MVP |
| Requirements Traceability Matrix | Markdown + CSV | MVP |
| Assumptions and dependency register | Markdown | MVP |
| SDLC factor profile with evidence | UI + document | MVP |
| Ranked SDLC recommendation with justification and counter-arguments | UI + document | MVP |
| Project-specific SDLC workflow | Markdown + DOCX | MVP |
| Audit trail | UI + JSON | MVP |
| Evaluation report | Markdown + CSV | MVP |
| Process workflow diagrams; PDF exports; change-impact reports | Various | SEC |

---

## L. Major data entities

**36 entities in seven clusters.** Risk entities are core MVP (change C1).

**Project context (4):** `Project` · `Stakeholder` · `InterviewSession` · `Utterance` *(traceability
root)*

**Sources and knowledge (6):** `SourceDocument` *(traceability root)* · `Chunk` · `KnowledgeItem`
*(typed per C.1, with source, jurisdiction, effective date, version, applicability, retrieval date)*
· `NormativeSource` · `Control` · `GlossaryTerm`

**Requirements core (11):** `Requirement` · `RequirementVersion` · `Classification` ·
`AcceptanceCriterion` · `Defect` · `Conflict` · `ClarificationQuestion` · `ComplianceMapping` ·
`ComplianceGap` · `Evidence` *(a resolved citation)* · `TraceLink` *(typed edge over the full chain
in `FR-TRC-001`)*

**Risk (3):** `RiskItem` *(category, likelihood, impact, computed level, status, owner, rationale —
always a project/engineering risk per `FR-RSK-011`)* · `MitigationSuggestion` · `RiskRegister`

**Governance (4):** `ApprovalRecord` · `Baseline` · `AgentRun` · `AuditEvent`

**SDLC (5):** `SDLCFactorProfile` · `SDLCRecommendation` · `WorkflowPhase` · `WorkflowActivity` ·
`WorkflowGate`

**Artefacts and evaluation (3):** `Artifact` · `EvaluationRun` · `GoldItem`

### L.1 The traceability chain

```
Stakeholder input (Utterance / SourceDocument+Chunk)
   └→ Requirement (+ RequirementVersion)
        ├→ Classification
        ├→ Defect  →  ClarificationQuestion
        ├→ Conflict (paired with another Requirement)
        ├→ ComplianceMapping  →  Control / NormativeSource
        ├→ Security / Privacy derived Requirement
        ├→ RiskItem  →  MitigationSuggestion
        ├→ AcceptanceCriterion
        └→ Artifact section (SRS, stories, use cases, matrices, risk register)

   Every link above carries Evidence, and every state change carries an
   ApprovalRecord and an AuditEvent.
```

---

## M. External dependencies

**No technology is being selected here.** This records what Phase 1 must decide and the realistic
option space, per the instruction that major decisions come with alternatives and reasoning.

| Dependency | Why needed | Options to weigh in Phase 1 | Trade-off that matters |
|---|---|---|---|
| **LLM API** | Roles 2–11 | Hosted frontier API vs local open-weights model | Hosted: better structured extraction, costs money, data leaves the machine. Local: free and private, weaker, needs hardware. Changes both achievable quality and the privacy risk profile |
| **Embedding model** | Retrieval, duplicate and conflict shortlisting | Hosted vs local sentence-transformers | Local embeddings are strong and remove per-call cost — a likely hybrid |
| **Vector store** | Semantic search | SQLite vector extension, Chroma, FAISS, pgvector | Corpus is hundreds to low thousands of chunks; simplicity should beat scale |
| **Relational database** | Requirements, versions, risks, approvals, audit | SQLite vs PostgreSQL | SQLite: zero setup. Postgres: concurrency plus pgvector in one system |
| **Orchestration** | Role graph, state, retries | Hand-rolled vs a framework | Hand-rolled: full control, explainable line by line, no version churn. Framework: faster start, opaque behaviour, harder to audit against J.1 |
| **Backend framework** | API layer | Python vs TypeScript | Python aligns with the ML ecosystem and likely team familiarity |
| **Frontend** | All UI | Component framework, server-rendered templates, or a rapid-prototyping UI toolkit | A rapid toolkit is fastest but constrains the review/approval UX — which is itself a demonstrated concept |
| **Document parsing** | PDF/DOCX ingest | Standard Python libraries | Must preserve character offsets for span-level traceability |
| **Document generation** | SRS, RTM, risk register, workflow | Markdown + DOCX library, or Markdown + a converter | Markdown-first keeps artefacts diffable |
| **Rule & scoring engine** | Expected controls, risk matrix, SDLC scoring | Declarative YAML/JSON interpreted by plain code vs a rules library | A small declarative format satisfies DQ-03 without a dependency |
| **Sensitive-data detection** | Masking before LLM calls | Regex/heuristics vs a dedicated library | Financial identifiers are highly patterned |
| **Testing** | Phase exit tests | Standard test runner + recorded LLM fixtures | Fixtures are required by ET-10 |

### M.1 Non-software dependencies

- **Curated normative content** — publicly available and licence-safe; extracts and paraphrases with
  citation, subject to the copyright constraint in D.2.
- **A gold-standard dataset** — human-annotated requirements, risks, and control mappings for the
  case studies. The single largest non-coding effort, and the precondition for section O.
- **An expert baseline for SDLC evaluation** — judgements collected from instructors or
  practitioners **before** they see the system's recommendation.
- **LLM API credits**, with a tracked budget (ET-08).

---

## N. Project and AI risks (risks of building and operating ReqPilot)

> **Terminology note.** This section is about risks to *this project*. It is distinct from the
> **Risk Analysis capability** (G.12), which is a ReqPilot feature that analyses risks in the
> *requirements it processes*. Both are needed; they are not the same thing.

| # | Risk | Concretely, in ReqPilot | MVP mitigation | Deferred |
|---|---|---|---|---|
| R1 | **Hallucination** | The system invents a regulation, a control, a risk, or a requirement nobody stated | Mandatory citations (`FR-RAG-003`); source links on every requirement (`FR-EXT-007`) and every risk (`FR-RSK-006`); escalate on empty retrieval (`FR-RAG-005`); measured via core metric E4 | Automated claim-level verification |
| R2 | **Prompt injection** | An uploaded "policy document" instructs an agent to approve everything or ignore a control | All retrieved and uploaded content treated as data (`FR-ADM-005`); injection detection; **and decisively, gates enforced outside the LLM (J.1)** — an injected document cannot pass a gate it never touches | Adversarial suite as a secondary metric; per-source trust levels |
| R3 | **Sensitive data exposure** | Identifiers in a transcript reach a third-party LLM | Masking before the process boundary (`FR-ING-003`); synthetic data only; sensitivity classification per source | Local-model option |
| R4 | **Legal over-reach** | The system states a legal conclusion a reader relies on | C.1 prohibitions; `FR-CMP-004`, `FR-CMP-006` deterministic language enforcement, `FR-CMP-007` advisory notice; gate G2 | Formal review workflow |
| R5 | **Scope drift into lending decisions** | Because the case study is loans, someone adds credit scoring or borrower risk scoring | D.1 scope guard; `FR-RSK-011`; the Phase 1 data model will make borrower-level risk unrepresentable | — |
| R6 | **Over-reliance / automation bias** | An analyst approves a 60-requirement baseline without reading it | Confidence signals surfaced; AI vs human-approved content visually distinct; role-appropriate gates; human correction rate is core metric E7 | Review-quality sampling |
| R7 | **Bias** | Requirements for a decisioning system encode discriminatory criteria; or one stakeholder role is systematically under-weighted | Fairness prompts in the Security & Privacy role; per-stakeholder contribution reporting; bias named in the risk categories (`FR-RSK-002`) | Dedicated bias evaluation set |
| R8 | **Access-control failure** | A stakeholder sees another project; an analyst self-approves a compliance item | Server-side RBAC; project isolation; role-appropriate approval; J.1 | Segregation-of-duties rules |
| R9 | **Excessive agent privilege** | The Documentation role can alter approval state | Agent-level least privilege (`FR-ADM-003`); narrow declared interfaces | Per-run capability tokens |
| R10 | **Stale knowledge** | A mapping cites a superseded instrument | KB versioning with effective dates (`FR-RAG-001`); KB version stamped on artefacts (`FR-DOC-009`); verify-at-curation discipline (D.2) | `FR-RAG-007` supersession flagging |
| R11 | **Non-determinism undermining audit** | The same input yields different output, so "replay" means nothing | Low-temperature settings; prompt versioning; **full input and output logging so the actual run is always reconstructible** (`FR-AUD-004`) | Response caching by input hash |
| R12 | **Cost overrun** | An unbounded loop consumes API credits | Bounded follow-up depth; deterministic shortlisting before pairwise LLM calls; per-run token accounting (ET-08) | Budget alerting |
| R13 | **Confidence misinterpretation** | A reader treats a confidence signal as a probability | H.1 definition; UI labels it a review-prioritisation signal; no probabilistic language in artefacts | Calibration study (secondary) |
| R14 | **Evaluation invalidity** | The team annotates the gold set after seeing system output | Gold set frozen before the relevant phase; independent annotators; agreement reported | Held-out case study |
| R15 | **Scope inflation** | The team builds a platform and finishes nothing | Section E tiering treated as binding; phase exit tests enforced | — |

---

## O. Evaluation

Split as instructed into a small **mandatory core** and a **secondary/stretch** set. All metrics are
computed on the primary case study; the secondary case study generalises where time allows.

### O.1 Core evaluation (mandatory) — 9 metrics plus 1 derived at no extra cost

| # | Metric | Definition | How measured | Source |
|---|---|---|---|---|
| E1 | Extraction precision / recall / F1 | Extracted requirements matched against a gold set (semantic match, adjudicated) | Primary case study ≈ 60 gold requirements | `[PS §19]` |
| E2 | Ambiguity-detection accuracy | Precision and recall against deliberately ambiguous seeded statements | ≈ 40 seeded items, half ambiguous | `[PS §19]` |
| E3 | Conflict-detection accuracy | Precision and recall on a corpus with ≈ 10 planted conflicts plus near-miss distractors | Seeded corpus | `[PS §19]` |
| E4 | Citation / evidence correctness | Fraction of citations that resolve **and** genuinely support the claim | Manual audit of a 50-claim sample | `[PS §19]` |
| E4b | *(derived at no extra cost)* Unsupported-claim rate | The complement of E4 — the operational form of §19's "hallucination rate" | Same audit sample | `[PS §19]` |
| E5 | Compliance / control mapping coverage | Fraction of expert-identified potentially applicable controls that the system maps or flags as a gap | Expert-produced control list | `[PS §19]` |
| E6 | Traceability coverage | Fraction of requirements with a complete chain per `FR-TRC-001`, including risk links | Computed by the system (`FR-TRC-003`) | `[PS §19]` |
| E7 | Human correction rate | Fraction of AI outputs edited or rejected by reviewers | Instrumented in the UI | `[PS §19]` |
| E8 | Time saved vs a conventional manual process | System-assisted vs fully manual analysis of the same held-out transcript | Timed comparison with a team member acting as analyst without the system | `[PS §19]` |
| E9 | SDLC recommendation agreement | Top-ranked model vs a blind expert panel | ≥ 3 experts judging **before** seeing system output | `[PS §19]` |

> **One deliberate deviation, flagged for your decision.** Your list of core metrics did not include
> SDLC recommendation accuracy. I have kept **E9 in the core** because SDLC recommendation is half of
> the problem statement, and without E9 that half goes unevaluated. Its *ranking correlation*
> companion is correctly secondary. Overrule me if you would rather E9 were secondary.

**Targets.** Only ET-07 (extraction F1 ≥ 0.75) carries a provisional number, and it is explicitly
scheduled for re-baselining after Phase P3. **Targets for E2–E9 will be set from measured behaviour
after P3–P7, not guessed in Phase 0.**

**Comparison baseline.** A team member performing the same analysis manually on a held-out case
study. Without it, E1 and E8 are meaningless.

### O.2 Secondary / stretch evaluation

| # | Metric | Why secondary |
|---|---|---|
| S1 | Requirement completeness (full assessment) | E1's recall is the MVP proxy; full completeness assessment needs a larger gold set |
| S2 | SDLC ranking correlation (full ranking, not top-1) | E9 covers the decision that matters |
| S3 | Risk-identification agreement with an expert | No problem-statement metric exists for risk; worth reporting if time allows |
| S4 | Prompt-injection resistance (ET-12) | Security evaluation, valuable but not a requirements-engineering metric |
| S5 | Latency (ET-01–03) | Engineering quality, not capability |
| S6 | Token cost per run (ET-08) | Engineering quality |
| S7 | Stakeholder satisfaction / SUS (ET-11) | Needs ≥ 8 participants; schedule-dependent |
| S8 | Confidence calibration | Requires outcome volume the MVP will not produce |

### O.3 Threats to validity (to state honestly in the final report)

The gold set is produced by the team that built the system; case studies are synthetic; the expert
panel is small; time-saved measurements are subject to familiarity effects; and the knowledge base
is a teaching corpus, not a complete statement of applicable obligations.

---

## P. Proposed development roadmap

**Thirteen phases (P0–P12).** Assumes roughly a 14-week semester and a team of about four, with
tracks: **Orchestration/Backend**, **Knowledge/RAG**, **Frontend**, **Evaluation/Documentation**.
Adjust once team size and timeline are confirmed (Q3).

Two principles: **every phase has an automatable exit test**, and **something is demonstrable from
P4 onward** — never a system that only works at the end.

| Phase | Wk | Goal | Deliverable | Exit test |
|---|---|---|---|---|
| **P0 Foundations** | 1 | Freeze Phase 0; design architecture; decide the stack with recorded alternatives | Architecture document, ADRs, data model, role contracts, repo skeleton, `.env.example`, seed-corpus and gold-data plan | Schema creates; fixtures load; test suite green; **every major technology choice has an ADR naming alternatives and reasoning** |
| **P1 Requirements repository** | 2 | The system of record, **no AI at all** | Requirement CRUD, versioning, trace links, audit log, RBAC, minimal UI | Create → edit → approve by hand; version history correct; audit entries written; RBAC matrix tests pass |
| **P2 Knowledge base & RAG** | 3 | Grounding infrastructure | Typed KB per C.1 with full metadata, chunking, embeddings, hybrid retrieval, citation resolution, allowlisting, KB admin | recall@5 ≥ ET-06 on a 20-question probe; every citation resolves; a non-allowlisted source is rejected |
| **P3 Extraction & classification** | 4–5 | First AI capability, batch mode | LLM gateway, extraction, classification, confidence signals, review queue | E1 computed against gold transcript #1; **this run re-baselines ET-07 and sets targets for E2–E9**; offline fixture tests pass |
| **P4 Elicitation & clarification** | 5–6 | The interactive loop — **first end-to-end demo** | Interview console, role templates, coverage tracking, adaptive follow-ups, clarification loop, open issues | A scripted persona interview yields a usable requirement set; follow-ups trigger on seeded vague answers; answering a clarification creates a new version |
| **P5 Quality & conflict detection** | 7 | Requirement analysis | Ambiguity, incompleteness, untestability, duplication, undefined terms, missing source; conflict detection; Validation role | E2 and E3 computed on the seeded corpora with false-positive rates reported |
| **P6 Compliance & security analysis** | 8 | The regulated-domain core | Compliance mapping with evidence, gap detection, mandated output language, advisory notices, security/privacy derivation, G2/G3 escalation | 100% of mappings carry resolvable citations; E5 computed; **no generated text contains a prohibited assertion** (automated language test) |
| **P7 Risk analysis & register** | 9 | **The restored capability (C1)** | Risk identification, six-way categorisation, ordinal likelihood/impact with rationale, deterministic matrix, mitigation suggestions, evidence links, G8 escalation, risk register artefact | Risk level is computed by the matrix, not the LLM (unit-tested); every risk links to a requirement and evidence; a high-severity risk **blocks** baseline approval; `FR-RSK-011` scope guard test passes |
| **P8 Approval, traceability & documents** | 10–11 | Governance and output | Gates G1–G8, baselining, RTM, SRS (incl. data and interface requirements), stories, use cases, compliance matrix, risk register, DOCX export | **An unapproved requirement cannot enter a baseline or a document** (automated); end-to-end SRS + RTM + risk register produced; E6 computed |
| **P9 SDLC recommendation** | 11–12 | The second half of the statement | Factor profile with evidence (incl. risk aggregates), overrides, rules + MCDA, ranked output, LLM explanation, counter-arguments, G6 multi-role approval | Scoring unit tests pass on hand-computed cases; E9 computed against the blind expert panel; the justification cites only derived factors |
| **P10 Workflow generation** | 12 | The tailored process | Phases, activities, roles, deliverables, testing requirements, security activities, compliance checkpoints, approval gates (incl. production-readiness), entry/exit criteria, traceability requirements; editable; exportable | A generated workflow for a high-regulation project contains every mandatory compliance checkpoint derived from that project's own mapping and every high-risk mitigation from its register |
| **P11 Guardrails hardening** | 13 | Make the security claims real | Masking, injection defence, agent least privilege, session isolation, retention/deletion, audit viewer with replay | Zero approval-gate bypasses under the adversarial suite; masking test on synthetic financial identifiers; replay reconstructs a requirement's and a risk's history |
| **P12 Evaluation, packaging & stretch** | 13–14 | Prove it and ship it | Core metrics across case studies, manual baseline, expert panel, setup docs, demo script, final report; then secondary metrics and E.2 items if time allows | `evaluate` reproduces the full core report; fresh-machine setup ≤ ET-09; demo runs end to end without intervention |

### P.1 Sequencing rationale

- **AI comes third, not first.** P1 and P2 build the repository and the grounding layer, so that when
  agent roles arrive they have somewhere trustworthy to write and something real to cite. Building
  agents first produces impressive demos with no traceability — exactly what the problem statement
  warns against.
- **P3 sets the targets.** Metric targets guessed before a baseline exists are fiction (H.2).
- **Risk analysis (P7) follows compliance and security (P6)**, because risk identification consumes
  their outputs (`FR-RSK-001` takes compliance and security results as input).
- **Governance (P8) precedes SDLC work (P9–P10)**, because the recommendation must derive from an
  *approved* baseline and an *approved* risk register, not raw extractions.
- **Hardening is its own phase (P11).** Guardrails bolted on continuously tend never to be tested;
  a dedicated phase with an adversarial suite makes the J.1 claims defensible.

---

## Q. Open decisions before architecture design

1. **Primary case study** — retail loan origination (recommended, with the D.1 scope guard),
   onboarding/KYC, or payments?
2. **Jurisdiction and normative scope** — the India-centred set in D.2, or an EU or US framing?
3. **Team size, roles, and timeline** — the roadmap assumes ~4 people over ~14 weeks.
4. **LLM access** — hosted API (and what budget) or a local model? Materially changes achievable
   quality *and* the privacy risk profile.
5. **Course constraints** — mandated deliverables, technologies, or milestone dates?
6. **Depth vs breadth** — one case study excellently, or two adequately? Changes P12 significantly.
7. **Stack familiarity** — anything that should weigh in the P0 ADRs?
8. **E9 placement** — keep SDLC recommendation agreement as a core metric (my recommendation, O.1),
   or move it to secondary?
9. **Risk rating scale** — 3×3 (simpler to explain and annotate) or 5×5 (finer, more conventional)?
   Proposed: **3×3** for the MVP.

---

## R. Internal consistency check

Performed across the whole document per instruction 11.

| Check | Result |
|---|---|
| Agent roles consistently 13 | ✅ 13 in §4, 13 in J, 13 referenced in C.2. **R1 defect fixed** (R1 marked role 9 as post-MVP) |
| Risk Analysis present everywhere it should be | ✅ O8 (objectives), C.2 (scope), D.3 (MVP capabilities), D.4 (definition of done), E.1 (core tier), F.1 (Risk Owner), G.12 (`FR-RSK-001`–`012`), G.14 (gate G8), I (M6, M7, M8, M1), J (role 9), K (inputs and outputs), L (3 entities), L.1 (trace chain), N (R5 scope-drift risk), O.2 (S3), P (phase P7) |
| Risk Register among required artefacts | ✅ `FR-RSK-008`, `FR-DOC-006`, K.2, D.4, P7, P8 |
| Module count matches modules listed | ✅ 12 stated, M1–M12 listed. **R1 defect fixed** (said eleven, listed twelve) |
| MVP capabilities match stated scope | ✅ D.3 capability table ↔ G `[MVP]` tags ↔ E.1 |
| Exclusions do not contradict required capabilities | ✅ E.2 items are all `[SEC]`-tagged in G, never `[MVP]`; E.3 items appear nowhere as MVP |
| Every problem-statement item is accounted for | ✅ §1–§20 traced into G, K, and G.18. Items that cannot fit are in E.2 **with justification**, not dropped |
| Evaluation metrics match the MVP | ✅ O.1's nine core metrics (plus derived E4b) measure only MVP capabilities; O.2's eight secondary metrics map to `[SEC]` items and H.2 targets |
| Asserted counts verified, not estimated | ✅ Recomputed from the document: 132 FRs across 20 FR-bearing groups — section G has 21 subsections, of which G.18 is a §17 platform-security coverage map with no FR rows (99 `[PS]`-citing, 33 `[PROJ]`-only, 0 untagged; 117 MVP / 13 secondary / 2 OOS), 13 agent roles, 12 modules, 8 ReqPilot gates, 36 entities, 13 phases, 9+1 core metrics, 8 secondary metrics. **R2 defect fixed:** a stale "95 FRs" reference survived in this table |
| Roadmap phases cover all MVP capabilities | ✅ Each D.3 core row maps to P1–P11; P7 covers risk; P10 covers workflow |
| HITL consistent throughout | ✅ Eight ReqPilot gates in G.14, referenced identically in C.2, D.3, F.1, I (M9), J.1, N, P8. **R1 defect fixed** (said six); **R2 defect fixed** (claimed G1–G8 *were* §16's eight categories) |
| All eight §16 approval categories represented | ✅ Categories 1–7 → gates G1–G7 (`FR-HIL-001`); category 8 (production-readiness) → `FR-WFL-003`, a gate inside the generated project's SDLC workflow. G8 is an additional `[PROJ]` risk gate with no §16 counterpart and is not claimed to have one |
| SDLC requirements consistent | ✅ Chain in B/O13–O14, G.16 (`FR-SDL`) and G.17 (`FR-WFL`), J role 10, L, P9–P10, and the pipeline in S below |
| No requirement group is referenced but absent | ✅ All 20 `FR-` group prefixes referenced in prose have a table in G. **Defect found and fixed during this pass:** the first draft of R2 referenced `FR-SDL` and `FR-WFL` in prose but omitted both tables entirely |
| G-subsection count vs FR-group count reconciled | ✅ Section G has **21 subsections (G.1–G.21)** but **20 FR-bearing groups**. G.18 is the §17 platform-security coverage map and carries no FR rows; it references requirements held in other groups. Both numbers are now stated explicitly so the difference reads as intentional rather than as a missing group |
| Terminology consistent | ✅ "agent role" vs "module" (I, J); "project risk" vs "credit risk" (D.1, N); "normative source" typed per C.1; "confidence signal" not "probability" (H.1, R13); "§16 approval category" vs "ReqPilot gate" (G.14); `[PS]` vs `[PROJ]` provenance used on all **132** FRs, with no requirement left untagged |
| Numeric **engineering targets** separated from source requirements | ✅ **Numeric engineering targets** — the values that would otherwise read as NFRs of the system — are isolated in H.2 (ET-01–ET-12), each labelled a proposed project-specific target with a provisional/fixed status. None appear in H.1, which is deliberately number-free. **This is not a claim that no numbers appear elsewhere in the document**: other numbers do appear where they represent implementation parameters (the proposed 3×3 risk scale, the 40–80-item knowledge-base estimate), evaluation-design choices (gold-set and sample sizes, panel and participant counts in O), roadmap parameters (team size, week allocations in P), or structural counts (13 agent roles, 12 modules, 8 gates). Those are not engineering targets and are not governed by H.2 |

---

## S. The SDLC recommendation pipeline (confirmed unchanged from R1)

```
Requirements + project characteristics + risk aggregates
   → SDLC factor profile (§13, with evidence per factor, human-overridable)
   → deterministic rule-based scoring (§14)
   → multi-criteria decision analysis (weighted)
   → ranked SDLC candidates with suitability scores (§14)
   → LLM-generated explanation and counter-arguments  ← explains, does not decide
   → Human-in-the-loop approval (gate G6: PM, Architect, Security, Compliance)
   → project-specific SDLC workflow generation (§15)
```

---

**Next step — awaiting explicit approval.** Phase 1 (architecture and design): orchestration pattern
selection with reasoning, technology ADRs, the detailed data model, the agent-role interface
contract, the risk matrix definition, and the P0/P1 work breakdown. **Nothing beyond Phase 0 has
been produced.**
