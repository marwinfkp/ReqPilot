# ReqPilot — Phase 0 Analysis

**Status:** Analysis only. No architecture decided, no code written.
**Authoritative source:** `Problem Statement.docx` (reference copy: `docs/problem-statement.md`).
**Purpose:** Establish shared understanding of the problem, scope, and roadmap before architecture design.

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
   banking regulation, data-protection law, payment-card standards, and internal policy. A missed
   control is not a bug; it is an audit finding or a fine.
3. **The analysis is cognitively expensive.** Detecting that two stakeholders have stated
   contradictory transaction limits, or that "the system should be fast" is untestable, or that a
   data-retention requirement is missing entirely, requires reading everything at once and holding
   it in memory. Humans do this inconsistently and slowly.

The visible failures are: ambiguous and incomplete specifications, contradictions discovered late,
regulatory omissions, incorrect scope, costly rework, and project delay.

There is a **second, coupled problem** the statement raises explicitly: the choice of SDLC model is
usually made by organisational habit rather than by analysis of the project's actual characteristics
(requirement stability, regulatory criticality, security risk, legacy dependence, failure
consequences). And even once a model is chosen, teams rarely tailor it — the security activities,
compliance checkpoints, and approval gates that a financial project needs are not inserted into the
process in a deliberate, justified way.

**ReqPilot is therefore a requirements-engineering assistant with two coupled outputs:** a validated,
classified, compliance-mapped, fully traceable requirement baseline; and — derived from that
baseline — a justified, ranked SDLC recommendation plus a project-specific workflow containing the
phases, roles, deliverables, security activities, compliance checkpoints, and approval gates the
project actually needs.

**The non-negotiable framing** (from the closing paragraph of the problem statement): the system
**automates information collection and analysis**, but **responsibility for regulatory
interpretation, requirement approval, and SDLC adoption remains with authorised humans.** ReqPilot
is advisory. This is not a disclaimer bolted on at the end — it is a design constraint that shapes
the approval model, the data model, and the UI.

### Why an *agentic* system rather than one large prompt

This is worth stating, because it is the core academic claim of the project. Requirements analysis
decomposes into tasks with **different inputs, different success criteria, different grounding
needs, and different escalation rules**:

- Extraction needs the raw transcript and must not invent content.
- Compliance analysis needs retrieved regulation text and must cite it.
- Conflict detection needs the *whole* requirement set at once, not one requirement.
- SDLC selection needs project-level aggregates, not individual requirements.

A single monolithic prompt performs all of these badly at once, cannot be evaluated per task, and
cannot be audited. Specialised agents with typed inputs and outputs give per-agent evaluation,
per-agent guardrails, per-agent least-privilege data access, and a replayable audit trail. That
separation is the thing this project demonstrates.

---

## B. Objectives

| # | Objective | Success looks like |
|---|---|---|
| O1 | Automate elicitation through adaptive, role-aware interviews | The agent asks follow-up questions when an answer is vague, incomplete, or inconsistent — not a fixed questionnaire |
| O2 | Extract and structure requirements from conversations and documents | Every candidate requirement carries the full structured schema and a link to its source span |
| O3 | Classify requirements across multiple dimensions | Multi-label classification with per-label confidence |
| O4 | Detect requirement quality defects | Ambiguity, incompleteness, untestability, duplication, undefined terms — each with the offending span |
| O5 | Detect conflicts and inconsistencies | Contradictions and stakeholder disagreements surfaced as pairs with rationale |
| O6 | Map requirements to regulations, policies, and controls | Every mapping carries evidence; gaps reported as missing controls |
| O7 | Derive security and privacy requirements | Controls the stakeholders did not think to ask for are proposed, grounded in the knowledge base |
| O8 | Ground all generation in approved sources (RAG) | No compliance or security claim without a citation; low-confidence output escalates instead of asserting |
| O9 | Maintain end-to-end traceability | Stakeholder utterance → requirement → artefact section → control → acceptance criterion |
| O10 | Enforce human-in-the-loop approval | Nothing reaches a baseline or a generated document without a recorded, role-appropriate approval |
| O11 | Generate requirement documentation | SRS, user stories, use cases, acceptance criteria, compliance matrix, RTM, open issues |
| O12 | Recommend an SDLC model with justification | Ranked candidates with suitability scores, factor evidence, and why the runner-up lost |
| O13 | Generate a project-specific SDLC workflow | Phases, activities, roles, deliverables, entry/exit criteria, security activities, compliance checkpoints, approval gates |
| O14 | Provide a complete audit trail | Prompts, retrieved evidence, agent decisions, human edits, and approvals recorded and replayable |
| O15 | Be measurably better than the manual baseline | Metrics in section O computed on case studies against a human-produced gold standard |

---

## C. Proposed scope

### In scope (full project, across all phases)

- **Domains:** one primary financial use case implemented deeply; two secondary ones used for
  evaluation and generalisation.
- **Inputs:** live interview sessions plus uploaded transcripts, policy documents, regulation
  extracts, and existing requirement documents.
- **The agent team:** the thirteen roles named in the problem statement, under a central
  orchestrator.
- **Knowledge base:** a curated, versioned corpus of regulation extracts, control catalogues,
  organisational policy exemplars, requirement-engineering guidelines, SDLC selection rules, and
  document templates — each item carrying source, jurisdiction, effective date, version, and
  applicability.
- **Retrieval-grounded generation** with citations, confidence scores, and escalation.
- **Requirements repository** with versioning, trace links, and approval state.
- **Human approval workflow** with role-based gates.
- **Document generation** to open formats.
- **SDLC selection engine** (deterministic rules + multi-criteria scoring + LLM-authored
  justification) and project-specific workflow generation.
- **Audit log** covering both agent and human actions.
- **Guardrails:** sensitive-data masking, prompt-injection defence, retrieval allowlisting,
  agent-level least privilege, role-based access control.
- **Evaluation harness** with a gold-standard dataset and reproducible metric computation.

### Out of scope permanently (not merely deferred)

- Real customer data or real production financial data. **Synthetic and anonymised only.**
- Legal advice or final regulatory determination. The system proposes; humans decide.
- Integration with real banking systems, core banking platforms, payment rails, or credit bureaus.
- Production identity infrastructure (enterprise SSO, hardware MFA, HSM key management).
- Multi-tenant SaaS operation, horizontal scaling, high availability.
- Fine-tuning or training a foundation model.
- Continuous ingestion of live regulatory feeds.
- Acting as the system of record for a real organisation's requirements.

---

## D. Recommended MVP scope

**Design principle: one narrow vertical slice, end to end, with all thirteen required concepts
present but thin.** A system that does extraction brilliantly and nothing else demonstrates far less
than a system that carries a single transcript all the way to an approved SRS and a justified SDLC
workflow.

### Recommended MVP case study: retail **loan origination**

Step 18 of the problem statement suggests starting with loan processing or digital customer
onboarding. Loan origination is the better first choice because it naturally exercises every
capability the project must demonstrate:

- KYC/AML obligations → compliance mapping has real content
- Credit decisioning → fairness, bias, and explainability requirements appear naturally
- Document upload and retention → privacy and data-management requirements
- Credit-bureau integration → legacy and integration constraints for SDLC factor extraction
- Disbursement limits and approval authority → a realistic natural source of **stakeholder
  conflict** (sales wants a higher auto-approval limit than risk does)
- Regulatory reporting → audit and reporting requirements

A **payments / step-up authentication** slice is recommended as the *second* case study, for
evaluation only. The problem statement's own worked example, `FR-PAY-001`, comes from that domain.

### Recommended MVP jurisdiction scope

**One primary jurisdiction plus cross-cutting standards.** Recommendation: India (RBI guidance +
DPDP Act 2023) plus PCI DSS, ISO/IEC 27001 Annex A, and NIST CSF as jurisdiction-neutral control
catalogues. **This is a decision for you to confirm** — see section Q. Restricting to one
jurisdiction keeps the knowledge base at roughly **40–80 curated control items**: small enough to
curate honestly, large enough that retrieval is non-trivial.

### MVP capability set

| Capability | MVP form | Deliberately thin because |
|---|---|---|
| Adaptive elicitation | Role templates + LLM follow-up generation + topic coverage tracker | Full conversational autonomy is hard to evaluate |
| Extraction & structuring | Full schema, span-linked to source | Core; not thinned |
| Clarification | Defect → targeted question → re-analysis on answer | Core loop; not thinned |
| Classification | Multi-label over the 13 categories, with confidence | Core; not thinned |
| Conflict detection | Embedding shortlist → LLM pairwise adjudication | Exhaustive n² LLM comparison is too costly |
| Compliance analysis | RAG mapping to curated controls + gap report | One jurisdiction, curated corpus, no live feeds |
| Security & privacy analysis | Control-catalogue-driven derivation of missing controls | Full STRIDE threat modelling deferred |
| RAG | Hybrid retrieval over allowlisted KB, mandatory citations, confidence, escalation | Core; not thinned |
| Traceability | Trace links + RTM export + orphan detection | Change-impact analysis deferred |
| Human-in-the-loop | Six enforced gates; accept / reject / modify / regenerate | Core; not thinned |
| Documentation | SRS, user stories, use cases, AC, compliance matrix, open issues → Markdown + DOCX | Risk register and diagrams deferred |
| SDLC recommendation | Factor extraction → rules + weighted scoring → ranked list + LLM justification | Core; not thinned |
| Workflow generation | Phases / activities / roles / deliverables / gates / entry-exit criteria → document | Core; not thinned |
| Audit | Append-only log + viewer + replay | Core; not thinned |
| Evaluation | Gold dataset for one case study + reproducible metric script | Broadened later |

### MVP definition of done

A user can, in one sitting: create a loan-origination project → interview three stakeholder roles →
upload one policy document → obtain structured, classified requirements with citations → see
detected ambiguities and at least one genuine conflict → answer clarification questions → review the
compliance mapping and its gaps → approve a baseline through role-gated approvals → export an SRS
with a traceability matrix → receive a ranked SDLC recommendation with justification → export a
tailored SDLC workflow → and inspect the audit trail explaining how every one of those outputs was
produced.

---

## E. Explicitly excluded from the MVP

Each of these is a legitimate part of the full vision. They are simply not in the first working
system.

| Excluded from MVP | Reason | Reconsider at |
|---|---|---|
| Risk-analysis agent and risk register | Not in the required-concepts list; overlaps compliance/security in a thin implementation | Post-MVP |
| Multi-jurisdiction regulation coverage | Curation cost grows linearly; retrieval precision drops without applicability filtering | After single-jurisdiction retrieval is measured |
| Live regulatory change feeds | Requires scraping, diffing, and re-validation infrastructure | Out of scope for the course |
| Enterprise SSO, MFA, HSM key management | Infrastructure, not a demonstration of the research idea | Permanently excluded |
| Real or production financial data | Ethical and legal exposure with no academic benefit | Permanently excluded |
| Voice capture / speech-to-text interviews | Adds a failure mode orthogonal to the research question | Optional stretch goal |
| Email, Jira, Confluence, ticketing connectors | Integration plumbing, not requirements intelligence | Optional stretch goal |
| Free-form agent-to-agent negotiation or debate | Very hard to evaluate, bound, or reproduce | Post-MVP experiment only |
| Model fine-tuning or training | Cost and reproducibility; RAG addresses the same need | Permanently excluded |
| Process/workflow diagram auto-generation | Nice-to-have on top of the workflow document | Late phase, if time allows |
| Change-impact analysis across the trace graph | Needs a stable trace graph first | Post-MVP |
| Sensitivity analysis on SDLC weights | Depends on a working scoring engine | Post-MVP |
| Multi-project portfolio views and analytics | Not part of the stated problem | Permanently excluded |
| Real-time multi-user collaborative editing | Significant engineering, zero research value | Permanently excluded |
| Mobile application | Web is sufficient | Permanently excluded |
| Monitoring/observability platform | Replaced by the audit log plus simple metrics | Permanently excluded |

---

## F. Actors and stakeholders

### Primary human actors (users of ReqPilot)

| Actor | Goal | Key permissions | In MVP |
|---|---|---|---|
| **Business Analyst / Requirements Engineer** | Drive the process; own requirement quality | Create projects, run interviews, edit requirements, request regeneration | Yes — the main user |
| **Stakeholder / Interviewee** (business, product owner, operations, end-user proxy) | Answer questions; state needs | Participate in an assigned interview session; see own answers | Yes |
| **Compliance / Legal Officer** | Ensure regulatory correctness | Approve regulatory interpretations and compliance mappings; reject with reason | Yes |
| **Security Reviewer (InfoSec)** | Ensure security and privacy controls are present | Approve high-risk security requirements; add controls | Yes |
| **Project Manager / Software Architect** | Decide how the project will be run | Approve the SDLC recommendation and generated workflow; edit the workflow | Yes |
| **Auditor** | Verify accountability after the fact | Read-only access to requirements, evidence, approvals, audit log | Yes (read-only) |
| **Knowledge-Base Administrator** | Keep the KB correct and versioned | Add, version, retire knowledge items | Yes (may be the same person as the Analyst in the MVP) |

### Secondary / external stakeholders (represented, not users)

Regulators, external auditors, end customers, and risk-management teams. Their concerns enter the
system as knowledge-base content and requirement categories rather than as logins.

### Non-human actors

- The **agents** (section J) — each with its own least-privilege data and tool access.
- The **LLM provider** — an external dependency and a trust boundary.
- The **knowledge base** — the only authorised source of regulatory claims.
- The **evaluation harness** — a non-interactive consumer of the system's APIs.

### Course-project stakeholders

Worth naming, because they shape the deliverables: the **course instructor / evaluator** (needs to
see the demonstrated concepts and a defensible evaluation) and the **student team** (needs phases
that split across members and finish on a semester schedule).

---

## G. Functional requirements

Tagged `[MVP]` or `[LATER]`. IDs use the same convention ReqPilot itself produces, so the project
can trace its own requirements.

### Project and session management

- `FR-PRJ-001` [MVP] Create a project with name, financial domain, description, and target jurisdiction(s).
- `FR-PRJ-002` [MVP] Maintain project lifecycle state: *Elicitation → Analysis → Review → Baselined*.
- `FR-PRJ-003` [MVP] Register stakeholders with role, authority level, and assigned interview templates.
- `FR-PRJ-004` [MVP] Isolate data per project; no cross-project retrieval or leakage.
- `FR-PRJ-005` [LATER] Clone a project as a baseline for a similar engagement.

### Ingestion

- `FR-ING-001` [MVP] Upload source documents (`.txt`, `.md`, `.pdf`, `.docx`) tagged by type: transcript, policy, regulation, legacy specification, audit finding.
- `FR-ING-002` [MVP] Parse each document to text, chunk it with character offsets, and store it as an immutable source with chunks.
- `FR-ING-003` [MVP] Detect and mask sensitive data (account numbers, card numbers, national IDs, names, emails, phone numbers) before any text is sent to the LLM; keep the unmasking map local.
- `FR-ING-004` [MVP] Record provenance for every source: filename, uploader, timestamp, content hash, sensitivity classification.
- `FR-ING-005` [LATER] Import structured specifications (OpenAPI, database schema) as integration constraints.

### Elicitation (stakeholder interaction)

- `FR-ELI-001` [MVP] Conduct a role-specific adaptive interview per stakeholder, combining a role template with LLM-generated questions.
- `FR-ELI-002` [MVP] Cover the interview topic checklist from step 7 of the problem statement and track coverage per topic.
- `FR-ELI-003` [MVP] Ask follow-up questions when an answer is vague, incomplete, or inconsistent, bounded by a maximum depth per topic.
- `FR-ELI-004` [MVP] Persist every utterance with speaker, role, timestamp, and session.
- `FR-ELI-005` [MVP] Allow the analyst to pause and resume a session, and to record answers on a stakeholder's behalf.
- `FR-ELI-006` [MVP] Display live topic-coverage state and the remaining uncovered topics.
- `FR-ELI-007` [LATER] Suggest which stakeholder role to interview next based on coverage gaps.

### Extraction and structuring

- `FR-EXT-001` [MVP] Extract candidate requirements from utterances and documents, each linked to its exact source span.
- `FR-EXT-002` [MVP] Normalise every requirement to the structured schema from step 8: ID, statement, category, source stakeholder, business justification, priority, dependencies, assumptions, acceptance criteria, applicable regulations, risk level, confidence score, approval status.
- `FR-EXT-003` [MVP] Rewrite statements into declarative "The system shall …" form while preserving meaning and retaining the original wording.
- `FR-EXT-004` [MVP] Assign stable, human-readable IDs (`FR-<DOMAIN>-nnn`, `NFR-<DOMAIN>-nnn`).
- `FR-EXT-005` [MVP] Detect and merge near-duplicate extractions from different sources, retaining all source links.
- `FR-EXT-006` [MVP] Generate acceptance criteria in Given/When/Then form for each requirement.
- `FR-EXT-007` [MVP] Never emit a requirement without at least one source link.

### Classification

- `FR-CLS-001` [MVP] Multi-label classify each requirement into the thirteen categories from step 9.
- `FR-CLS-002` [MVP] Return per-label confidence; below-threshold labels enter the human review queue.
- `FR-CLS-003` [MVP] Allow human override of any label, recorded as a versioned change.
- `FR-CLS-004` [LATER] Use accumulated human overrides as few-shot examples to improve subsequent classification.

### Quality analysis and conflict detection

- `FR-QAL-001` [MVP] Detect ambiguity (vague quantifiers, undefined comparatives, unclear referents) and report the offending span.
- `FR-QAL-002` [MVP] Detect incompleteness (missing actor, trigger, condition, or measurable outcome).
- `FR-QAL-003` [MVP] Detect untestability (no measurable acceptance criterion is derivable).
- `FR-QAL-004` [MVP] Detect duplication and overlap using semantic similarity.
- `FR-QAL-005` [MVP] Detect conflicts — direct contradiction, mutually exclusive constraints, and stakeholder disagreement — reporting both requirement IDs and a rationale.
- `FR-QAL-006` [MVP] Detect undefined terminology against the project glossary.
- `FR-QAL-007` [MVP] Detect missing source attribution.
- `FR-QAL-008` [MVP] Route every detected defect to the clarification agent with a generated question.
- `FR-QAL-009` [LATER] Assess technical feasibility against the stated legacy and integration constraints.

### Clarification

- `FR-CLR-001` [MVP] Generate a targeted clarification question bound to a specific requirement and a specific defect.
- `FR-CLR-002` [MVP] Maintain an open-issues list with status, assignee, and age.
- `FR-CLR-003` [MVP] Re-run extraction and quality analysis when a clarification is answered, recording the resulting requirement version change.
- `FR-CLR-004` [MVP] Allow the analyst to dismiss a clarification with a recorded reason.

### Retrieval-augmented generation and knowledge base

- `FR-RAG-001` [MVP] Maintain a curated, versioned knowledge base in which every item carries source, jurisdiction, effective date, version, and applicability.
- `FR-RAG-002` [MVP] Perform hybrid (semantic + keyword) retrieval restricted to an allowlist of approved sources.
- `FR-RAG-003` [MVP] Attach at least one citation to every generated compliance, regulatory, or security claim, resolvable to the exact retrieved chunk.
- `FR-RAG-004` [MVP] Attach a confidence score to every generated item; below threshold routes to human review instead of being asserted.
- `FR-RAG-005` [MVP] When retrieval returns nothing relevant, escalate rather than answer from the model's parametric memory.
- `FR-RAG-006` [MVP] Provide a knowledge-base administration view for adding, versioning, and retiring items.
- `FR-RAG-007` [LATER] Flag requirements whose supporting knowledge item has since been superseded.

### Compliance analysis

- `FR-CMP-001` [MVP] Map each requirement to applicable regulations, policies, and controls, with supporting evidence.
- `FR-CMP-002` [MVP] Identify compliance gaps: expected controls for the domain with no covering requirement.
- `FR-CMP-003` [MVP] Emit mandatory approval and audit checkpoints, and data-retention and reporting obligations, implied by the mapping.
- `FR-CMP-004` [MVP] Never issue a final legal determination; flag every high-impact interpretation for compliance-officer approval.
- `FR-CMP-005` [MVP] Report the applicable jurisdiction for every mapping.
- `FR-CMP-006` [LATER] Compare mappings across jurisdictions and report divergence.

### Security and privacy analysis

- `FR-SEC-001` [MVP] Derive missing security requirements (authentication, authorisation, encryption, logging, session management, transaction integrity, fraud controls) for the system under analysis.
- `FR-SEC-002` [MVP] Derive privacy requirements (data minimisation, consent, retention, subject rights).
- `FR-SEC-003` [MVP] Assign a risk level to each security and privacy requirement and route high-risk ones to the security reviewer.
- `FR-SEC-004` [LATER] Produce a lightweight STRIDE threat enumeration per data flow.

### Traceability and evidence

- `FR-TRC-001` [MVP] Maintain trace links: source utterance or chunk → requirement → artefact section → control → acceptance criterion.
- `FR-TRC-002` [MVP] Generate and export a Requirements Traceability Matrix.
- `FR-TRC-003` [MVP] Report traceability coverage and flag orphan requirements and unsourced statements.
- `FR-TRC-004` [MVP] Preserve trace links across requirement versions.
- `FR-TRC-005` [LATER] Perform change-impact analysis across the trace graph.

### Human-in-the-loop approval

- `FR-HIL-001` [MVP] Enforce approval gates for: final requirement baseline, regulatory interpretation, high-risk security requirements, conflict resolution, SDLC selection, and changes to already-approved requirements.
- `FR-HIL-002` [MVP] Offer accept, reject, modify, and regenerate on every AI-produced output.
- `FR-HIL-003` [MVP] Enforce role-appropriate approval (compliance items by the compliance officer, security items by the security reviewer, SDLC selection by the PM/architect).
- `FR-HIL-004` [MVP] Prevent any unapproved requirement from entering a baseline or a generated artefact.
- `FR-HIL-005` [MVP] Record approver identity, timestamp, decision, comment, and the exact version approved.
- `FR-HIL-006` [MVP] Show the analyst a single review queue ordered by risk and confidence.

### Documentation generation

- `FR-DOC-001` [MVP] Generate a Software Requirements Specification from approved requirements using a versioned template.
- `FR-DOC-002` [MVP] Generate user stories with acceptance criteria.
- `FR-DOC-003` [MVP] Generate use-case descriptions.
- `FR-DOC-004` [MVP] Generate a compliance-control matrix.
- `FR-DOC-005` [MVP] Generate the assumptions and dependencies register and the open-issues list.
- `FR-DOC-006` [MVP] Export to Markdown and DOCX.
- `FR-DOC-007` [MVP] Link every generated artefact section back to its contributing requirement IDs.
- `FR-DOC-008` [MVP] Stamp every generated artefact with a generation timestamp, model version, and knowledge-base version.
- `FR-DOC-009` [LATER] Generate a threat and risk register.
- `FR-DOC-010` [LATER] Generate process workflow diagrams.
- `FR-DOC-011` [LATER] Export to PDF.

### SDLC decision factors and recommendation

- `FR-SDL-001` [MVP] Derive the project factor profile from step 13 (requirement stability, regulatory criticality, security risk, complexity, size, legacy dependence, expected change frequency, need for continuous delivery, stakeholder availability, testing and documentation needs, budget and schedule constraints, need for formal verification, consequences of failure), scoring each factor with supporting evidence.
- `FR-SDL-002` [MVP] Allow the analyst to override any factor score, with the override recorded.
- `FR-SDL-003` [MVP] Score SDLC candidates (Waterfall, V-Model, Spiral, Agile, DevSecOps, and hybrids) using deterministic rules plus weighted multi-criteria scoring, producing a **ranked** list with suitability percentages.
- `FR-SDL-004` [MVP] Generate a justification narrative grounded strictly in the derived factors and their evidence — the LLM explains the computed result, it does not choose it.
- `FR-SDL-005` [MVP] Present counter-arguments: why the runner-up was not selected and under what change it would win.
- `FR-SDL-006` [MVP] Require multi-role approval of the final selection.
- `FR-SDL-007` [LATER] Perform sensitivity analysis on the scoring weights.

### Workflow generation

- `FR-WFL-001` [MVP] Generate a project-specific workflow with phases, activities, responsible roles, deliverables, and entry/exit criteria.
- `FR-WFL-002` [MVP] Insert security activities and compliance checkpoints derived from the project's actual compliance mapping.
- `FR-WFL-003` [MVP] Insert human approval gates and traceability requirements.
- `FR-WFL-004` [MVP] Allow the project manager to edit the workflow, with a change log.
- `FR-WFL-005` [MVP] Export the workflow to Markdown and DOCX.

### Audit

- `FR-AUD-001` [MVP] Record an append-only entry for every agent run: agent, prompt template and version, inputs, retrieved evidence IDs, output, model and version, latency, token cost, confidence.
- `FR-AUD-002` [MVP] Record every human action: edits, approvals, rejections, overrides, dismissals.
- `FR-AUD-003` [MVP] Provide an audit viewer filterable by requirement, agent, user, and time.
- `FR-AUD-004` [MVP] Support replay: reconstruct how any requirement reached its current state.
- `FR-AUD-005` [MVP] Make audit records immutable through the application (no update or delete endpoints).

### Access control and administration

- `FR-ADM-001` [MVP] Support the roles Analyst, Stakeholder, Compliance Officer, Security Reviewer, PM/Architect, Auditor (read-only), and Administrator.
- `FR-ADM-002` [MVP] Enforce role-based access control at the API layer, not only in the UI.
- `FR-ADM-003` [MVP] Enforce agent-level least privilege: each agent receives only the data and tools its task requires.
- `FR-ADM-004` [EXCLUDED] Multi-factor authentication and enterprise SSO.

### Evaluation

- `FR-EVL-001` [MVP] Load a gold-standard dataset and compute the metrics in section O.
- `FR-EVL-002` [MVP] Produce a reproducible evaluation report from a single command.
- `FR-EVL-003` [MVP] Record manual-baseline timings for the time-saved comparison.
- `FR-EVL-004` [LATER] Compare across multiple model or prompt versions.

---

## H. Non-functional requirements

These are requirements **on ReqPilot itself**, not on the systems it analyses.

### Performance

- `NFR-PRF-001` Interview turn response: ≤ 5 s median, ≤ 12 s at the 95th percentile.
- `NFR-PRF-002` Full analysis of a 3,000-word transcript (extract → classify → quality → compliance): ≤ 3 minutes.
- `NFR-PRF-003` SRS generation for a 60-requirement project: ≤ 90 s.
- `NFR-PRF-004` Runs acceptably on a developer laptop: 8–16 GB RAM, **no GPU required**.
- `NFR-PRF-005` Supports 5 concurrent users — a demo, not a production load.

### Reliability

- `NFR-REL-001` A failed agent step must not corrupt project state; runs are resumable from the last completed step.
- `NFR-REL-002` LLM calls retry with backoff; exhausted retries surface as a visible, actionable error, never as a silently empty result.
- `NFR-REL-003` Long-running analyses report progress per agent.

### Security

- `NFR-SEC-001` No secrets in source control; all credentials via environment variables, with a committed `.env.example`.
- `NFR-SEC-002` Sensitive data is masked before leaving the process boundary toward the LLM.
- `NFR-SEC-003` Uploaded documents and retrieved chunks are treated as untrusted data, never as instructions.
- `NFR-SEC-004` Retrieval is restricted to an allowlist; agents cannot fetch arbitrary external content.
- `NFR-SEC-005` Sessions are isolated; one project's context never enters another's prompts.
- `NFR-SEC-006` Authorisation is enforced server-side on every request.

### Privacy

- `NFR-PRV-001` Only synthetic or anonymised data is used in development, demos, and evaluation.
- `NFR-PRV-002` Project deletion cascades to utterances, sources, chunks, embeddings, and generated artefacts.
- `NFR-PRV-003` A documented retention policy governs interview transcripts and audit logs.

### Auditability and explainability

- `NFR-AUD-001` Every AI-produced output is traceable to its inputs, its retrieved evidence, and the prompt version that produced it.
- `NFR-EXP-001` Every automated decision is presented with evidence, rationale, and confidence — never as a bare verdict.
- `NFR-EXP-002` The SDLC recommendation's numeric score is inspectable factor by factor.

### Usability

- `NFR-USE-001` An analyst can triage a flagged requirement (see defect, see evidence, decide) in at most three interactions.
- `NFR-USE-002` Every screen distinguishes AI-generated, human-edited, and human-approved content visually.
- `NFR-USE-003` Target System Usability Scale score ≥ 70 in end-of-project evaluation.

### Maintainability and extensibility

- `NFR-MNT-001` Adding a regulation, control, or SDLC rule requires **data changes only**, not code changes.
- `NFR-MNT-002` All agents implement a common interface; adding an agent does not modify the orchestrator's core logic.
- `NFR-MNT-003` Prompts are versioned artefacts stored outside application code.
- `NFR-MNT-004` A new financial domain is added via a knowledge pack plus interview templates.

### Testability

- `NFR-TST-001` Every agent is independently testable with fixed inputs.
- `NFR-TST-002` LLM calls are mockable via recorded fixtures so the test suite runs offline, deterministically, and without API cost.
- `NFR-TST-003` Deterministic components (rule engine, scoring, RBAC, traceability) have unit tests with no LLM involvement.
- `NFR-TST-004` Each roadmap phase has a defined, automatable exit test.

### Portability and operability

- `NFR-POR-001` Cross-platform; the primary development environment is Windows.
- `NFR-POR-002` One-command local setup; a new team member is running in ≤ 15 minutes.
- `NFR-POR-003` Exports use open formats (Markdown, DOCX, CSV, JSON).

### Cost

- `NFR-CST-001` A full end-to-end run of the primary case study stays within a documented token budget, tracked per run and visible in the audit log.
- `NFR-CST-002` Retrieval results and deterministic agent outputs are cached to avoid repeat spend during development.

---

## I. Major system modules

Eleven modules. Note that **module ≠ agent**: the agent layer is one module among several, and most
guarantees the problem statement demands (traceability, approval, audit, access control) live in
deterministic modules rather than in the agents.

| # | Module | Responsibility | Depends on |
|---|---|---|---|
| M1 | **Web interface** | Interview console, requirement workbench, review/approval queue, compliance view, artefact viewer, SDLC dashboard, audit viewer, KB admin | M2 |
| M2 | **Application / API layer** | Request handling, authentication, authorisation, project lifecycle, input validation | M3–M10 |
| M3 | **Agent orchestration layer** | Agent registry, execution order, shared run context, retries, gate checks, per-agent permissions | M4, M5, M6, M9 |
| M4 | **LLM gateway** | Single choke point for all model calls: prompt assembly from versioned templates, masking, injection filtering, schema-validated output parsing, token accounting, caching | M10, M11 |
| M5 | **Knowledge & retrieval service** | KB ingestion, chunking, embedding, hybrid retrieval, allowlisting, citation resolution, KB versioning | — |
| M6 | **Requirements repository** | Requirements, versions, classifications, defects, conflicts, compliance mappings, trace links, evidence | — |
| M7 | **Rule engine** | Declarative rules: compliance expectations per domain, quality heuristics, SDLC scoring rules and weights, workflow templates | — |
| M8 | **Artefact generator** | Template-driven assembly of SRS, user stories, use cases, compliance matrix, RTM, workflow document; export to Markdown/DOCX | M6, M7 |
| M9 | **Approval & workflow service** | Gate definitions, approval state machine, role checks, review queue, baseline freezing | M6, M10 |
| M10 | **Audit & trace service** | Append-only event log for agent runs and human actions; replay reconstruction | — |
| M11 | **Guardrails layer** | Sensitive-data detection and masking, prompt-injection detection, output filtering, retrieval allowlist enforcement, agent permission checks | — |
| M12 | **Evaluation harness** | Gold dataset loading, metric computation, report generation, manual-baseline capture | M2, M6 |

---

## J. Proposed agent responsibilities

The thirteen agents from step 4 of the problem statement, plus a structural note.

**Important design observation:** only about eight of these need to be LLM-driven. Treating the
Coordinator, Human-Approval, and (partly) Validation and SDLC-Selection agents as **deterministic
services** rather than LLM agents is what makes the system testable, auditable, and affordable. The
problem statement's guarantees — approval gates, evidence, traceability — must not depend on a model
choosing to honour them.

| Agent | Input | Output | LLM? | Escalates when | MVP |
|---|---|---|---|---|---|
| **Coordinator / Orchestrator** | Project state, pending work | Agent invocations, run records | No — deterministic supervisor | A gate is unsatisfied or an agent fails repeatedly | Yes |
| **Stakeholder Interaction** | Role template, topic coverage, conversation so far | Next question, recorded utterance | Yes | Topic coverage stalls or the stakeholder disengages | Yes |
| **Requirement Extraction** | Utterances, document chunks | Structured candidate requirements with source spans | Yes | A statement is too vague to structure at all | Yes |
| **Clarification** | Requirement + detected defect | Targeted question, open issue | Yes | An issue remains unresolved past a threshold | Yes |
| **Classification** | Requirement text | Multi-label categories with confidence | Yes | Confidence below threshold | Yes |
| **Conflict Detection** | Full requirement set | Conflict pairs with type and rationale | Yes (after embedding shortlist) | Stakeholder disagreement requires a human decision | Yes |
| **Compliance** | Requirement + retrieved regulation chunks | Regulation/control mappings with citations, gap list | Yes, RAG-grounded | Any high-impact interpretation, always | Yes |
| **Security & Privacy** | Requirement set + domain control catalogue | Proposed security/privacy requirements with risk level | Yes, RAG-grounded | Any high-risk control | Yes (thin) |
| **Risk Analysis** | Requirements, factors, conflicts | Risk register entries | Yes | High-severity risk | **No — post-MVP** |
| **SDLC Selection** | Factor profile | Ranked SDLC candidates + justification | Scoring deterministic; **narrative only** from LLM | Always — selection requires approval | Yes |
| **Documentation** | Approved requirements + templates | SRS, stories, use cases, matrices | Yes, low-creativity template filling | An approved requirement is missing a mandatory field | Yes |
| **Validation** | Candidate baseline | Pass/fail checklist with reasons | Mostly deterministic checks | Any check fails | Yes (thin) |
| **Human-Approval** | Pending items | Routed approval tasks, recorded decisions | No — workflow service | By definition, always | Yes |

### Orchestration pattern (to be decided at architecture stage)

Three candidate patterns, to be chosen with reasoning in the architecture phase:

1. **Deterministic supervisor / typed pipeline** — the orchestrator runs a fixed graph; agents have
   typed inputs and outputs. Most testable, most auditable, cheapest; least "autonomous-looking".
2. **Blackboard** — agents observe shared state and trigger on conditions. More emergent; harder to
   bound and evaluate.
3. **Conversational multi-agent** — agents message each other freely. Most impressive in a demo;
   hardest to make reproducible, cheapest to build badly, very expensive in tokens.

My preliminary leaning is **(1) with selective autonomy**: a deterministic backbone, with genuine
agent decision-making where it adds value (which question to ask next, whether to re-open a
requirement, whether to escalate). This preserves the multi-agent claim while keeping the system
evaluable. **I am not committing to this yet** — it is the first architecture-phase decision.

---

## K. Inputs and outputs

### Inputs (MVP)

| Input | Form | Source |
|---|---|---|
| Project brief | Structured form (domain, jurisdiction, description) | Analyst |
| Live interview answers | Chat turns | Stakeholders |
| Interview transcripts | `.txt`, `.md`, `.docx` | Upload |
| Policy and procedure documents | `.pdf`, `.docx`, `.md` | Upload |
| Regulation extracts and control catalogues | Curated KB items with metadata | KB administrator |
| Existing requirement documents | `.docx`, `.md` | Upload |
| Legacy system notes | Free text or upload | Analyst |
| Human decisions | Approve / reject / modify / regenerate | All approver roles |
| Factor overrides | Numeric scores with reasons | Analyst, PM |

### Inputs (later)

Emails and meeting notes, incident reports and audit findings, API and database specifications,
questionnaire exports.

### Outputs (MVP)

| Output | Form |
|---|---|
| Structured requirement set | UI + JSON/CSV export |
| Classification results with confidence | UI |
| Defect report (ambiguity, incompleteness, untestability, duplication, undefined terms) | UI + document |
| Conflict report | UI + document |
| Clarification questions and open-issues list | UI + document |
| Compliance mapping with citations and gap list | UI + compliance-control matrix |
| Derived security and privacy requirements | Requirement set |
| Software Requirements Specification | Markdown + DOCX |
| User stories with acceptance criteria | Markdown + DOCX |
| Use-case descriptions | Markdown + DOCX |
| Requirements Traceability Matrix | Markdown + CSV |
| Assumptions and dependencies register | Markdown |
| SDLC factor profile with evidence | UI + document |
| Ranked SDLC recommendation with justification and counter-arguments | UI + document |
| Project-specific SDLC workflow | Markdown + DOCX |
| Audit trail | UI + JSON export |
| Evaluation report | Markdown + CSV |

### Outputs (later)

Threat and risk register, process workflow diagrams, PDF exports, change-impact reports.

---

## L. Major data entities

Roughly twenty-five entities. Relationships noted where they carry the traceability guarantee.

**Project context**
- `Project` — domain, jurisdictions, lifecycle state, owner
- `Stakeholder` — role, authority level, project
- `InterviewSession` — stakeholder, template, topic coverage, status
- `Utterance` — session, speaker, text, timestamp *(a traceability root)*

**Sources and knowledge**
- `SourceDocument` — type, filename, hash, uploader, sensitivity classification *(a traceability root)*
- `Chunk` — source document, text, character offsets, embedding reference
- `KnowledgeItem` — text, **source, jurisdiction, effective date, version, applicability**, status (active/superseded)
- `Regulation` / `Control` — catalogue entries referenced by knowledge items
- `Glossary Term` — project-scoped definitions used for undefined-terminology detection

**Requirements core**
- `Requirement` — the step-8 schema; current version pointer
- `RequirementVersion` — immutable snapshot with the change reason and author
- `Classification` — requirement, label, confidence, source (agent or human)
- `AcceptanceCriterion` — requirement, Given/When/Then text
- `Defect` — requirement, type, span, severity, detecting agent, status
- `Conflict` — requirement A, requirement B, type, rationale, resolution, resolver
- `ClarificationQuestion` — requirement, defect, question, answer, status
- `ComplianceMapping` — requirement, control, evidence, confidence, approval status
- `ComplianceGap` — project, expected control, why it is unmet
- `Evidence` — a resolved citation: chunk or knowledge item, quoted span, retrieval score
- `TraceLink` — typed edge (source → requirement → artefact section → control → criterion)

**Process and governance**
- `ApprovalRecord` — item reference, gate, approver, role, decision, comment, version approved, timestamp
- `Baseline` — frozen, approved requirement set with a version label
- `AgentRun` — agent, inputs, prompt version, evidence IDs, output, model, latency, tokens, confidence
- `AuditEvent` — append-only record of any agent or human action

**SDLC**
- `SDLCFactorProfile` — thirteen factors, each with score, evidence, and override flag
- `SDLCRecommendation` — ranked candidates with scores, justification, counter-arguments, approval state
- `WorkflowPhase` / `WorkflowActivity` / `WorkflowGate` — the generated project-specific process

**Artefacts and evaluation**
- `Artifact` — type, version, content, generation metadata (model version, KB version, timestamp)
- `EvaluationRun` / `GoldItem` — dataset, metric results, comparison baseline

**Post-MVP:** `RiskItem`, `RiskRegister`.

---

## M. External dependencies

Choices are **deliberately not made here** — they belong to the architecture phase. This section
records what must be decided and the realistic option space.

| Dependency | Why needed | Options to weigh | Notes |
|---|---|---|---|
| **LLM API** | All generative agents | Hosted frontier API vs local open-weights model | Hosted: far better quality, costs money, sends data off-machine. Local: free and private, much weaker at structured extraction, needs hardware. Affects `NFR-PRF-004`, `NFR-CST-001`, and the privacy risk profile |
| **Embedding model** | Semantic retrieval, duplicate and conflict shortlisting | Hosted embeddings vs local sentence-transformers | Local embeddings are genuinely good and remove per-call cost; strong candidate for a hybrid approach |
| **Vector store** | Semantic search over KB and requirements | SQLite-based vector extension, Chroma, FAISS, pgvector | Corpus is small (hundreds to low thousands of chunks); simplicity should win over scale |
| **Relational database** | Requirements, versions, approvals, audit | SQLite vs PostgreSQL | SQLite: zero setup, ideal for a student team. Postgres: concurrency and pgvector in one system |
| **Agent orchestration** | Agent graph, state, retries | Hand-rolled vs a framework | Hand-rolled: full control, no version churn, better for a course project where you must explain every line. Framework: faster start, opaque behaviour, harder to audit |
| **Backend framework** | API layer | Python (FastAPI/Flask) vs TypeScript (Node) | Python aligns with the ML ecosystem and the team's likely familiarity |
| **Frontend** | All UI | React/Next.js, server-rendered templates + HTMX, or Streamlit | Streamlit is fastest but limits the review/approval UX, which is a *demonstrated concept* here |
| **Document parsing** | Ingest PDF/DOCX | `pypdf`/`pdfplumber`, `python-docx` | Needs character offsets preserved for span-level traceability |
| **Document generation** | SRS, RTM, workflow exports | Markdown + `python-docx`, or Markdown + Pandoc | Markdown-first keeps artefacts diffable |
| **Rule engine** | Compliance expectations, SDLC scoring | Plain code over declarative YAML/JSON rules vs a rules library | A small declarative format satisfies `NFR-MNT-001` without a dependency |
| **PII detection** | Masking before LLM calls | Regex/heuristics vs a library such as Presidio | Financial identifiers are highly patterned; regex covers most of it |
| **Testing** | Phase exit tests | `pytest` + recorded LLM fixtures | Fixtures are essential for `NFR-TST-002` |

### Non-software dependencies

- **Curated regulatory content.** Must be publicly available and license-safe; store extracts and
  paraphrases with citations rather than bulk-copied regulatory text.
- **A gold-standard dataset.** Human-annotated requirements for the case studies — the single
  largest non-coding effort in the project, and the thing that makes section O possible.
- **An expert baseline for SDLC evaluation.** Independent judgements from instructors or experienced
  practitioners, collected *before* they see the system's recommendation.
- **LLM API credits**, with a tracked budget.

---

## N. Security, privacy, compliance, and AI risks

The problem statement names these explicitly, so they are graded deliverables, not afterthoughts.

| # | Risk | Concretely, in ReqPilot | MVP mitigation | Deferred mitigation |
|---|---|---|---|---|
| R1 | **Hallucination** | The system invents a regulation, a control, or a requirement nobody stated | Mandatory citations for all compliance/security claims (`FR-RAG-003`); source spans for all requirements (`FR-EXT-007`); escalate on empty retrieval (`FR-RAG-005`); hallucination rate measured (section O) | Automated claim-level verification against retrieved text |
| R2 | **Prompt injection** | An uploaded "policy document" contains text instructing the agent to approve everything or ignore a control | Treat all retrieved and uploaded content as data (`NFR-SEC-003`); structural separation of instructions from content; injection-pattern detection; **approval gates that live outside the model** | Adversarial evaluation suite; per-source trust levels |
| R3 | **Sensitive financial data exposure** | Account numbers or personal identifiers in a transcript are sent to a third-party LLM | Masking before the process boundary (`FR-ING-003`); synthetic data only (`NFR-PRV-001`); sensitivity classification on every source | Local-model option for sensitive projects |
| R4 | **Over-reliance / automation bias** | An analyst approves a 60-requirement baseline without reading it | Confidence surfaced everywhere; AI vs human-approved content visually distinct (`NFR-USE-002`); role-appropriate gates; human correction rate tracked as a metric | Deliberate review-quality sampling |
| R5 | **Unauthorised regulatory determination** | The system states a legal conclusion the team then relies on | `FR-CMP-004` — no final legal determinations; high-impact interpretations always escalate; disclaimers on generated compliance content | Formal legal review workflow |
| R6 | **Bias** | Credit-decisioning requirements encode discriminatory criteria; or the agent systematically under-weights one stakeholder role | Fairness prompts in the security/privacy agent for decisioning domains; per-stakeholder contribution reporting | Bias evaluation set |
| R7 | **Access control failure** | A stakeholder sees another project's requirements; an analyst self-approves a compliance item | Server-side RBAC (`NFR-SEC-006`); project isolation (`FR-PRJ-004`); role-appropriate approval (`FR-HIL-003`) | Segregation-of-duties rules |
| R8 | **Excessive agent privilege** | The documentation agent can modify approval state | Agent-level least privilege (`FR-ADM-003`); agents access data through narrow, declared interfaces | Capability tokens per agent run |
| R9 | **Regulatory change / stale knowledge** | A requirement cites a superseded regulation | KB versioning with effective dates and status (`FR-RAG-001`); KB version stamped on every artefact (`FR-DOC-008`) | `FR-RAG-007` supersession flagging |
| R10 | **Non-determinism undermining audit** | The same input yields different outputs, so "replay" means nothing | Temperature-0 where possible; prompt versioning; full input+output logging so the *actual* run is always reconstructible (`FR-AUD-004`) | Response caching keyed by input hash |
| R11 | **Cost overrun** | An unbounded agent loop consumes the API budget | Bounded follow-up depth (`FR-ELI-003`); embedding shortlist before pairwise LLM conflict checks; per-run token accounting and caps | Budget alerting |
| R12 | **Evaluation invalidity** | The team annotates the gold set after seeing system output | Gold set frozen before the relevant phase; independent annotators; inter-annotator agreement reported | Held-out case study |
| R13 | **Scope inflation** | The team builds a production platform and finishes nothing | Section E treated as binding; phase exit tests enforced | — |

---

## O. Evaluation metrics

Derived from step 19, each made measurable at student scale. Targets are **initial hypotheses to be
replaced by measured baselines after Phase 3** — setting them before a baseline exists would be
guesswork.

| # | Metric | Definition | How measured | Initial target |
|---|---|---|---|---|
| E1 | Extraction precision / recall / F1 | Extracted requirements matched against a gold set (semantic match, adjudicated) | 3 case studies; primary ≈ 60 gold requirements, secondary ≈ 25 each | F1 ≥ 0.75 |
| E2 | Requirement completeness | Fraction of gold requirements covered by at least one extracted requirement | Same gold set | ≥ 0.80 |
| E3 | Ambiguity-detection accuracy | Precision/recall against a seeded set of deliberately ambiguous statements | ≈ 40 seeded items, half ambiguous | Recall ≥ 0.80, precision ≥ 0.70 |
| E4 | Conflict-detection accuracy | Precision/recall on a corpus with ≈ 10 deliberately planted conflicts plus distractor near-misses | Seeded corpus | Recall ≥ 0.80; false-positive rate reported |
| E5 | Regulatory-control coverage | Fraction of the expert-identified applicable controls the system maps or flags as a gap | Expert-produced control list per case study | ≥ 0.75 |
| E6 | Hallucination rate | Fraction of generated compliance/security claims not supported by their cited evidence | Manual audit of a 50-claim sample | ≤ 5% |
| E7 | Citation correctness | Fraction of citations that resolve and genuinely support the claim | Same sample | ≥ 0.90 |
| E8 | SDLC recommendation accuracy | Agreement of the top-ranked model with an expert panel; plus rank correlation over the full ranking | ≥ 3 experts, judging blind and *before* seeing system output | Top-1 agreement on ≥ 2 of 3 cases |
| E9 | Human correction rate | Fraction of AI outputs edited or rejected by reviewers | Instrumented in the UI | Reported, not targeted — a proxy for trust |
| E10 | Time saved | System-assisted vs fully manual analysis of the same transcript | Timed comparison with a human analyst on a held-out transcript | ≥ 50% reduction |
| E11 | Traceability coverage | Fraction of requirements with a complete source → requirement → artefact → control chain | Computed by the system (`FR-TRC-003`) | ≥ 0.95 |
| E12 | Stakeholder satisfaction | System Usability Scale plus targeted Likert items on trust and explainability | ≥ 8 participants | SUS ≥ 70 |
| E13 | Latency and cost | Wall-clock time and token cost per full project run | Audit log aggregation | Within `NFR-PRF` / `NFR-CST` budgets |
| E14 | Prompt-injection resistance | Fraction of adversarial documents that fail to alter agent behaviour | ≈ 20 crafted injection documents | ≥ 0.90 blocked; zero approval-gate bypasses |

**Comparison baseline:** manual analysis of the same inputs by a team member acting as a business
analyst, working without the system, on a held-out case study. Without this, none of E1, E2, or E10
mean anything.

**Threats to validity to state honestly in the report:** the gold set is produced by the same team
that built the system; case studies are synthetic; the expert panel is small; and time-saved
measurements are subject to familiarity effects.

---

## P. Proposed development roadmap

Twelve phases. Assumes roughly a 14-week semester and a team of about four, with suggested tracks:
**Orchestration/Backend**, **RAG/Knowledge**, **Frontend**, **Evaluation/Documentation**. Adjust the
week mapping once you confirm team size and timeline.

Two principles: **every phase has an automatable exit test**, and **there is something demonstrable
from Phase 4 onward** — never a system that only works at the end.

| Phase | Weeks | Goal | Deliverable | Exit test |
|---|---|---|---|---|
| **0 — Foundations** | 1 | Freeze scope; decide architecture and tech stack with recorded reasoning; define the data model and agent contracts | Architecture document, ADRs, data model, repo skeleton, `.env.example`, seed-corpus and gold-data plan | Schema creates and fixtures load; test suite runs green in CI; every major technology choice has a written ADR with alternatives |
| **1 — Requirements repository** | 2 | The system of record, with **no AI at all** | Requirement CRUD, versioning, trace links, audit log, minimal UI, RBAC | Create → edit → approve a requirement by hand; version history correct; audit entries written; RBAC matrix tests pass |
| **2 — Knowledge base & RAG** | 3 | Grounding infrastructure | KB ingestion with full metadata, chunking, embeddings, hybrid retrieval, citation resolution, allowlisting, KB admin view | 20-question retrieval set: recall@5 ≥ 0.80; every citation resolves to a real chunk; out-of-allowlist source is rejected |
| **3 — Extraction & classification** | 4–5 | First AI capability, batch mode over a transcript | LLM gateway, extraction agent, classification agent, confidence scoring, review queue | Precision/recall/F1 computed against gold transcript #1; **this run sets the real targets for section O**; offline fixture tests pass |
| **4 — Elicitation & clarification** | 5–6 | The interactive loop; **first end-to-end demo** | Interview console, role templates, coverage tracking, adaptive follow-ups, clarification loop, open-issues list | Scripted persona interview produces ≥ 15 requirements; follow-ups trigger on ≥ 80% of seeded vague answers; answering a clarification creates a new requirement version |
| **5 — Quality & conflict detection** | 7 | Requirement analysis | Ambiguity, incompleteness, untestability, duplication, undefined terms, conflict detection; validation agent | Seeded conflict corpus recall ≥ 0.80 with false-positive rate reported; ambiguity detection meets E3 |
| **6 — Compliance & security analysis** | 8 | The regulated-domain core | Compliance mapping with evidence, gap detection, security/privacy requirement derivation, high-impact escalation | 100% of mappings carry resolvable citations; control coverage measured against the expert list; every high-impact interpretation is routed for approval |
| **7 — Approval, traceability & documents** | 9–10 | Governance and output | Six approval gates, baselining, RTM, SRS/stories/use-cases/compliance-matrix generation, DOCX export | **Unapproved requirement cannot enter a baseline or a document** (automated test); end-to-end SRS + RTM produced for case study 1; traceability coverage ≥ 0.95 |
| **8 — SDLC recommendation** | 11 | The second half of the problem statement | Factor extraction with evidence, overrides, rule + weighted scoring, ranked output, justification, counter-arguments, multi-role approval | Scoring engine unit tests pass on hand-computed cases; recommendation compared against the expert panel; justification cites only derived factors |
| **9 — Workflow generation** | 11–12 | The tailored process | Phases, activities, roles, deliverables, entry/exit criteria, injected security activities, compliance checkpoints, approval gates; editable; exportable | Generated workflow for a high-regulation project contains every mandatory compliance checkpoint from the project's own mapping; PM edits are change-logged |
| **10 — Guardrails hardening** | 12 | Make the security claims real | Masking, injection defence, agent least privilege, session isolation, audit viewer with replay | Injection suite ≥ 90% blocked and **zero** gate bypasses; masking test on synthetic financial identifiers; replay reconstructs a requirement's history |
| **11 — Evaluation** | 13 | Prove it works | Full metric computation across 3 case studies, manual baseline comparison, expert SDLC panel, usability study | `make evaluate` reproduces the full report; all section-O metrics reported with threats to validity |
| **12 — Packaging & stretch goals** | 14 | Ship and present | Setup docs, demo script, final report; then, if time allows: risk agent, PDF export, diagrams, change-impact analysis | Fresh-machine setup in ≤ 15 minutes; demo runs end to end without intervention |

### Sequencing rationale

- **AI comes third, not first.** Phases 1 and 2 build the repository and the grounding layer, so
  that when agents arrive they have somewhere trustworthy to write and something real to cite.
  Building agents first produces impressive demos with no traceability, which is precisely what the
  problem statement warns against.
- **Phase 3 sets the targets.** Metric targets guessed before a baseline exists are fiction.
- **Governance (Phase 7) precedes SDLC work (Phases 8–9)** because the SDLC recommendation must be
  derived from an *approved* baseline, not from raw extractions.
- **Hardening is its own phase.** Bolting guardrails on continuously tends to mean they are never
  actually tested; a dedicated phase with an adversarial suite makes the security claims defensible.

---

## Q. Open decisions before architecture design

These need your input. They are decisions about the project, not about implementation detail.

1. **Primary case study** — loan origination (recommended), digital onboarding/KYC, or payments?
2. **Jurisdiction and regulatory scope** — India (RBI + DPDP Act) plus PCI DSS / ISO 27001 / NIST, or
   an EU framing (GDPR + PSD2), or US?
3. **Team size, roles, and timeline** — the roadmap assumes about four people over about 14 weeks.
4. **LLM access** — hosted API (and what budget) or a local model? This materially changes both
   achievable quality and the privacy risk profile.
5. **Assessment constraints** — are there mandated deliverables, technologies, or milestone dates
   from the course that must be honoured?
6. **Depth vs breadth preference** — one case study done excellently, or three done adequately? This
   changes Phase 11 significantly.
7. **Language and stack preferences** — any existing team familiarity that should weigh in the
   Phase 0 ADRs?

---

**Next step (awaiting approval):** architecture design — the orchestration pattern, the technology
ADRs, the data model in detail, the agent interface contract, and the Phase 0/1 work breakdown.
