# ReqPilot — Problem Statement (reference copy)

> Text extracted verbatim from `Problem Statement.docx`.
> The original `.docx` remains the authoritative specification.

Problem Statement
Financial institutions operate in a highly regulated, security-sensitive, and rapidly evolving environment. Gathering and analysing software requirements in this sector is largely manual, time-consuming, and prone to ambiguity, inconsistency, regulatory omissions, and inadequate stakeholder alignment. Requirements originate from diverse sources—including customers, business teams, compliance officers, security teams, regulators, legacy systems, and policy documents—and are often expressed in unstructured natural language. These challenges can lead to incorrect scope, unsuitable development practices, security and compliance gaps, project delays, and costly rework.
The problem is to design and develop an Agentic AI–based Large Language Model system for automated requirement gathering, analysis, and SDLC identification in the financial sector. The proposed system should employ autonomous, collaborative AI agents to interact with stakeholders, analyse conversations and documents, ask context-aware clarification questions, detect incomplete or conflicting requirements, and transform validated inputs into structured functional and non-functional requirements, user stories, use cases, acceptance criteria, and traceability records.
The system should classify requirements across business, technical, security, privacy, compliance, performance, availability, auditability, and operational dimensions. It should map them to applicable financial regulations, organisational policies, risk controls, and legacy-system constraints, while maintaining evidence and human-approval checkpoints. Based on the analysed requirements, project characteristics, risks, regulatory criticality, and expected frequency of change, the system should recommend an appropriate Software Development Life Cycle model—such as Agile, DevSecOps, Spiral, V-Model, Waterfall, or a hybrid model—and generate a justified, project-specific SDLC workflow with phases, activities, roles, deliverables, validation gates, security controls, and compliance checkpoints.
The research must address challenges associated with LLM hallucinations, explainability, handling sensitive financial data, bias, prompt injection, access control, regulatory change, interoperability, and human oversight. The proposed solution should use retrieval-grounded generation, rule-based compliance validation, multi-agent coordination, confidence scoring, audit trails, and human-in-the-loop verification to ensure that its outputs are accurate, traceable, secure, and accountable.
The system’s effectiveness should be evaluated using representative financial-sector case studies, such as digital banking, loan processing, payment systems, fraud detection, insurance, and regulatory reporting. Evaluation should measure requirement completeness, correctness, consistency, ambiguity detection, regulatory control coverage, suitability of SDLC recommendations, traceability, processing time, hallucination rate, and stakeholder satisfaction, compared with conventional requirements engineering practices.






The proposed system can be designed as a multi-agent platform in which specialised AI agents collaborate across requirement engineering, compliance analysis, risk assessment, and SDLC selection.
1. Define the system scope
Specify the financial services and projects the system will support, such as:
- Digital banking and mobile banking
- Loan origination and credit assessment
- Payment processing
- Fraud detection
- Insurance and investment platforms
- Regulatory reporting
- Customer onboarding and KYC
- Financial data analytics
Define expected outputs: Software Requirements Specification (SRS), user stories, use cases, acceptance criteria, compliance mappings, risk register, traceability matrix, and recommended SDLC model.
2. Identify stakeholders
Identify the parties from whom requirements will be collected:
- Customers and end users
- Business analysts
- Product owners
- Software architects and developers
- Information-security teams
- Compliance and legal officers
- Risk-management teams
- Operations personnel
- Auditors and regulators
Create role-specific interview templates because each stakeholder provides different categories of requirements.
3. Define input sources
The system should accept both structured and unstructured inputs:
- Stakeholder conversations
- Interview transcripts
- Questionnaires
- Emails and meeting notes
- Existing requirement documents
- Banking policies and procedures
- Regulatory and compliance documents
- API and database specifications
- Legacy-system documentation
- Incident reports and audit findings
Sensitive information should be classified, masked, and protected before being submitted to an LLM.
4. Design the multi-agent architecture
A possible agent architecture is:
[TABLE]
| Agent | Responsibility |
| Coordinator agent | Controls the workflow and assigns tasks |
| Stakeholder interaction agent | Conducts interviews and asks questions |
| Requirement extraction agent | Extracts requirements from conversations and documents |
| Clarification agent | Detects missing or ambiguous information |
| Classification agent | Classifies functional and non-functional requirements |
| Conflict-detection agent | Identifies contradictions and duplication |
| Compliance agent | Maps requirements to relevant regulations and policies |
| Security and privacy agent | Identifies cybersecurity and privacy requirements |
| Risk-analysis agent | Assesses business, technical and compliance risks |
| SDLC selection agent | Recommends an appropriate SDLC model |
| Documentation agent | Generates SRS, user stories and traceability records |
| Validation agent | Verifies completeness, consistency and evidence |
| Human-approval agent | Routes critical decisions to authorised personnel |
[/TABLE]
A central orchestrator should manage agent communication, execution order, shared context, and approval gates.
5. Build the financial knowledge base
Develop an authorised and version-controlled knowledge base containing:
- Financial terminology and business processes
- Organisational policies and controls
- Regulatory requirements
- Security standards
- Requirement-engineering guidelines
- SDLC selection rules
- Templates for SRS, user stories, and use cases
- Previously approved project documents
Every knowledge item should include its source, jurisdiction, effective date, version, and applicability.
6. Implement retrieval-grounded generation
Use Retrieval-Augmented Generation to ensure that responses are grounded in approved sources.
The workflow should be:
- Classify the stakeholder query or requirement.
- Retrieve relevant policies, regulations and domain documents.
- Provide retrieved evidence to the LLM.
- Generate the requirement or analysis.
- attach citations and confidence scores.
- Escalate unsupported or low-confidence outputs for human review.
This reduces hallucinations and improves regulatory traceability.
7. Design requirement-gathering conversations
The interaction agent should conduct adaptive interviews rather than use a fixed questionnaire.
It should ask questions about:
- Business objectives
- Users and roles
- Existing workflow
- Inputs, outputs and business rules
- Exceptional conditions
- Data collection and retention
- Authentication and authorisation
- Financial transaction limits
- Audit and reporting requirements
- Performance and availability
- Integration with existing systems
- Regulatory and security constraints
- Expected project schedule and budget
The agent should ask follow-up questions when an answer is incomplete, vague or inconsistent.
8. Extract and structure requirements
Convert stakeholder input into a standard requirement structure:
- Requirement ID
- Requirement statement
- Requirement category
- Source stakeholder
- Business justification
- Priority
- Dependencies
- Assumptions
- Acceptance criteria
- Applicable regulations
- Risk level
- Confidence score
- Approval status
For example:
FR-PAY-001: The system shall require step-up authentication for transactions exceeding the organisation-approved risk threshold.
9. Classify the requirements
Classify extracted requirements into:
- Business requirements
- Stakeholder requirements
- Functional requirements
- Security requirements
- Privacy requirements
- Regulatory requirements
- Performance requirements
- Availability and reliability requirements
- Usability requirements
- Data-management requirements
- Integration requirements
- Audit and reporting requirements
- Operational and maintenance requirements
Multi-label classification should be allowed because one requirement may belong to several categories.
10. Analyse requirement quality
The analysis agents should check each requirement for:
- Ambiguity
- Incompleteness
- Inconsistency
- Duplication
- Infeasibility
- Lack of testability
- Missing source
- Undefined terminology
- Conflicting stakeholder expectations
- Missing security or compliance controls
Requirements that fail these checks should be returned to the clarification agent.
11. Perform compliance and security analysis
Map each requirement to applicable regulations, standards, and organisational policies.
The compliance agent should determine:
- Applicable jurisdiction and regulation
- Required security or privacy control
- Evidence supporting the mapping
- Compliance gaps
- Mandatory approval or audit checkpoints
- Data-retention and reporting obligations
The compliance agent should not make final legal determinations. High-impact interpretations must be approved by compliance or legal officers.
12. Generate requirement artefacts
After validation, generate:
- Software Requirements Specification
- User stories and acceptance criteria
- Use-case descriptions
- Process workflows
- Data requirements
- Interface requirements
- Threat and risk register
- Compliance-control matrix
- Requirements Traceability Matrix
- Assumptions and dependency register
- Open-issues list
Each generated item should remain linked to its original stakeholder statement and supporting document.
13. Extract SDLC decision factors
The SDLC selection agent should derive project characteristics such as:
- Requirement stability
- Regulatory criticality
- Security risk
- Project complexity
- System size
- Legacy-system dependence
- Frequency of expected changes
- Need for continuous delivery
- Availability of stakeholders
- Testing and documentation requirements
- Budget and schedule constraints
- Need for formal verification
- Consequences of system failure
These factors become inputs to the SDLC recommendation mechanism.
14. Design the SDLC selection engine
Use a hybrid approach combining deterministic rules, multi-criteria decision analysis, and LLM-generated explanations.
[TABLE]
| Project condition | Suitable SDLC approach |
| Stable requirements and extensive approvals | Waterfall |
| Strict verification and validation | V-Model |
| High uncertainty or technical risk | Spiral |
| Frequently changing requirements | Agile |
| Continuous secure deployment | DevSecOps |
| High regulation with evolving requirements | Agile–V-Model or DevSecOps hybrid |
[/TABLE]
The engine should produce ranked recommendations rather than only one label. For example:
- Agile–DevSecOps hybrid: 88%
- V-Model: 76%
- Spiral: 65%
The final choice should be approved by the project manager, architect, security team, and compliance officer.
15. Generate a project-specific SDLC workflow
The system should customise the selected SDLC with:
- Development phases
- Activities within each phase
- Responsible roles
- Expected deliverables
- Security activities
- Testing requirements
- Compliance checkpoints
- Human-approval gates
- Entry and exit criteria
- Traceability requirements
For a financial application, the generated workflow might include threat modelling during design, static security testing during implementation, transaction-integrity testing during validation, and compliance approval before deployment.
16. Implement human-in-the-loop controls
Human approval should be mandatory for:
- Final requirement baselines
- Regulatory interpretations
- High-risk security requirements
- Conflicting stakeholder decisions
- Architecture-critical requirements
- SDLC selection
- Changes to approved requirements
- Production-readiness decisions
Users must be able to accept, reject, modify, or request regeneration of AI outputs.
17. Secure the Agentic AI platform
Implement controls including:
- Role-based access control
- Multi-factor authentication
- Encryption in transit and at rest
- Sensitive-data masking
- Secure prompt and output filtering
- Agent-level permissions
- Retrieval-source allowlisting
- Protection against prompt injection
- Session isolation
- Audit logging
- Data-retention policies
- Model and knowledge-base versioning
Agents should receive only the minimum data and tool permissions needed for their assigned tasks.
18. Develop the prototype
A practical technology stack may include:
- LLM for language understanding and generation
- Agent orchestration framework
- Vector database for semantic retrieval
- Relational database for structured requirements
- Rule engine for compliance and SDLC logic
- Workflow engine for approvals
- Web interface for stakeholder interaction
- Document-generation component
- Identity and access-management system
- Monitoring and audit platform
Begin with one financial use case, such as loan processing or digital customer onboarding, before extending to other services.
19. Test and evaluate the system
Evaluate it using real or carefully anonymised financial case studies.
Important metrics include:
- Requirement extraction precision, recall and F1-score
- Requirement completeness
- Ambiguity-detection accuracy
- Conflict-detection accuracy
- Regulatory-control coverage
- Hallucination rate
- Citation correctness
- SDLC recommendation accuracy
- Human correction rate
- Time saved compared with manual analysis
- Stakeholder satisfaction
- Traceability coverage
The SDLC recommendation can be evaluated by comparing it with decisions made by experienced software architects and project managers.
20. Deploy, monitor and improve
Deploy the system gradually:
- Run it in advisory mode.
- Compare its outputs with those of business analysts.
- Introduce human-approved document generation.
- Monitor incorrect recommendations and compliance gaps.
- Update regulatory knowledge and decision rules.
- Re-evaluate the model after every significant update.
- Expand to additional financial use cases.
All prompts, retrieved evidence, agent decisions, user changes, approvals, and generated artefacts should be recorded to support accountability and audits.
The final system should function as an intelligent requirement-engineering assistant. It can automate information collection and analysis, but responsibility for regulatory interpretation, requirement approval, and SDLC adoption should remain with authorised human stakeholders.
