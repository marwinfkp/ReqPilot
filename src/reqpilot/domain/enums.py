"""Shared domain enumerations (architecture sections E, G, M, O).

Only the foundational vocabulary P0 needs. Enumerations belonging to entities
that later roadmap phases introduce - requirement lifecycle states, risk
categories, classification labels - are deliberately absent, because a
speculative enum is worse than no enum.

Two values here are load-bearing and traceable to the approved baseline:

* :class:`Gate` - the eight ReqPilot platform gates, G1-G8. Production-readiness
  is **not** among them: it is a gate inside the *generated project's* workflow,
  not a ReqPilot platform gate (approved Phase 0; architecture M.1, M.4).
* :class:`AgentRole` - the thirteen conceptual agent roles, unchanged.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """The seven human roles (approved Phase 0 F.1)."""

    ANALYST = "analyst"
    STAKEHOLDER = "stakeholder"
    COMPLIANCE_OFFICER = "compliance_officer"
    SECURITY_REVIEWER = "security_reviewer"
    PROJECT_MANAGER = "project_manager"
    AUDITOR = "auditor"
    KB_ADMIN = "kb_admin"


class AgentRole(StrEnum):
    """The thirteen conceptual agent roles (architecture section E).

    Naming them here does not implement them. Roles are implemented by the
    roadmap phases that need them; this enum exists so that run records and
    audit events can reference a role from P0 onwards.
    """

    COORDINATOR = "coordinator"
    STAKEHOLDER_INTERACTION = "stakeholder_interaction"
    REQUIREMENT_EXTRACTION = "requirement_extraction"
    CLARIFICATION = "clarification"
    CLASSIFICATION = "classification"
    CONFLICT_DETECTION = "conflict_detection"
    COMPLIANCE = "compliance"
    SECURITY_PRIVACY = "security_privacy"
    RISK_ANALYSIS = "risk_analysis"
    SDLC_SELECTION = "sdlc_selection"
    DOCUMENTATION = "documentation"
    VALIDATION = "validation"
    HUMAN_APPROVAL = "human_approval"


class ImplementationKind(StrEnum):
    """How an agent role is implemented (architecture E.0).

    Ten of the thirteen roles invoke or use LLM capabilities: four primarily
    LLM-driven, five hybrid, one LLM-assisted assembler. Three are deterministic.
    """

    LLM_DRIVEN = "llm_driven"
    HYBRID = "hybrid"
    LLM_ASSISTED_ASSEMBLER = "llm_assisted_assembler"
    DETERMINISTIC = "deterministic"


#: Role -> implementation kind (architecture E.0). Asserted in tests so the
#: 4 + 5 + 1 + 3 categorisation cannot drift silently.
ROLE_IMPLEMENTATION_KIND: dict[AgentRole, ImplementationKind] = {
    AgentRole.COORDINATOR: ImplementationKind.DETERMINISTIC,
    AgentRole.STAKEHOLDER_INTERACTION: ImplementationKind.LLM_DRIVEN,
    AgentRole.REQUIREMENT_EXTRACTION: ImplementationKind.LLM_DRIVEN,
    AgentRole.CLARIFICATION: ImplementationKind.LLM_DRIVEN,
    AgentRole.CLASSIFICATION: ImplementationKind.LLM_DRIVEN,
    AgentRole.CONFLICT_DETECTION: ImplementationKind.HYBRID,
    AgentRole.COMPLIANCE: ImplementationKind.HYBRID,
    AgentRole.SECURITY_PRIVACY: ImplementationKind.HYBRID,
    AgentRole.RISK_ANALYSIS: ImplementationKind.HYBRID,
    AgentRole.SDLC_SELECTION: ImplementationKind.HYBRID,
    AgentRole.DOCUMENTATION: ImplementationKind.LLM_ASSISTED_ASSEMBLER,
    AgentRole.VALIDATION: ImplementationKind.DETERMINISTIC,
    AgentRole.HUMAN_APPROVAL: ImplementationKind.DETERMINISTIC,
}


class Gate(StrEnum):
    """The eight ReqPilot platform approval gates (architecture M.1, M.3).

    G1-G7 correspond to the first seven problem-statement approval categories.
    G8 is the project-defined high-severity risk gate and has no counterpart in
    the problem statement. The eighth problem-statement category,
    production-readiness, is emitted into the *generated project's* SDLC
    workflow and is deliberately **not** a member of this enum.
    """

    G1_REQUIREMENT_BASELINE = "G1"
    G2_REGULATORY_INTERPRETATION = "G2"
    G3_HIGH_RISK_SECURITY = "G3"
    G4_STAKEHOLDER_CONFLICT = "G4"
    G5_ARCHITECTURE_CRITICAL = "G5"
    G6_SDLC_SELECTION = "G6"
    G7_APPROVED_REQUIREMENT_CHANGE = "G7"
    G8_HIGH_SEVERITY_RISK = "G8"


#: Gate -> the role required to decide it (architecture M.3). G6 additionally
#: requires four roles; that grouping is implemented by the approval service in
#: the roadmap phase that introduces SDLC selection.
GATE_REQUIRED_ROLES: dict[Gate, frozenset[Role]] = {
    Gate.G1_REQUIREMENT_BASELINE: frozenset({Role.ANALYST, Role.COMPLIANCE_OFFICER}),
    Gate.G2_REGULATORY_INTERPRETATION: frozenset({Role.COMPLIANCE_OFFICER}),
    Gate.G3_HIGH_RISK_SECURITY: frozenset({Role.SECURITY_REVIEWER}),
    Gate.G4_STAKEHOLDER_CONFLICT: frozenset({Role.ANALYST}),
    Gate.G5_ARCHITECTURE_CRITICAL: frozenset({Role.PROJECT_MANAGER}),
    Gate.G6_SDLC_SELECTION: frozenset(
        {Role.PROJECT_MANAGER, Role.SECURITY_REVIEWER, Role.COMPLIANCE_OFFICER}
    ),
    Gate.G7_APPROVED_REQUIREMENT_CHANGE: frozenset({Role.ANALYST, Role.COMPLIANCE_OFFICER}),
    Gate.G8_HIGH_SEVERITY_RISK: frozenset({Role.SECURITY_REVIEWER}),
}


#: Whether a gate needs a decision from **every** role in
#: :data:`GATE_REQUIRED_ROLES` (co-approval) or from any one of them.
#:
#: **Settled, not a tuning knob.** G1 is co-approval: the approved Phase 0
#: analysis F.1 records "Gates G2, and G1 co-approval", and architecture M.3
#: now annotates the G1 row to match. G6 and G7 are co-approval for the same
#: reason - every role a gate names must sign.
#:
#: A ``True`` value makes the gate fan out into one task per required role,
#: sharing a ``task_group_id`` (architecture M.3). A ``False`` value is only
#: meaningful for a gate that names exactly one role.
GATE_REQUIRES_ALL_ROLES: dict[Gate, bool] = {
    Gate.G1_REQUIREMENT_BASELINE: True,
    Gate.G2_REGULATORY_INTERPRETATION: False,
    Gate.G3_HIGH_RISK_SECURITY: False,
    Gate.G4_STAKEHOLDER_CONFLICT: False,
    Gate.G5_ARCHITECTURE_CRITICAL: False,
    Gate.G6_SDLC_SELECTION: True,
    Gate.G7_APPROVED_REQUIREMENT_CHANGE: True,
    Gate.G8_HIGH_SEVERITY_RISK: False,
}


class ApprovalTaskStatus(StrEnum):
    """Lifecycle of one approval task (architecture G.7).

    A task is terminal once it is APPROVED or REJECTED; further decisions
    against it are refused, so a decided task cannot be reused.
    """

    OPEN = "OPEN"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class ApprovalDecisionType(StrEnum):
    """What a human decided (architecture M.3, ``FR-HIL-002``)."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"
    MODIFY = "MODIFY"


class RequirementCategory(StrEnum):
    """The thirteen classification categories (problem statement section 9).

    Named here because the requirement schema carries a category field from the
    moment requirements exist. **P1 does not classify anything**: a category is
    supplied manually or left unset, and automated multi-label classification
    belongs to a later roadmap phase.
    """

    BUSINESS = "business"
    STAKEHOLDER = "stakeholder"
    FUNCTIONAL = "functional"
    SECURITY = "security"
    PRIVACY = "privacy"
    REGULATORY = "regulatory"
    PERFORMANCE = "performance"
    AVAILABILITY = "availability"
    USABILITY = "usability"
    DATA_MANAGEMENT = "data_management"
    INTEGRATION = "integration"
    AUDIT_REPORTING = "audit_reporting"
    OPERATIONAL = "operational"


class RequirementPriority(StrEnum):
    """Requirement priority (problem statement section 8)."""

    MUST = "must"
    SHOULD = "should"
    COULD = "could"
    WONT = "wont"


class ProjectLifecycleState(StrEnum):
    """Project lifecycle (approved Phase 0; ``FR-PRJ-002``).

    Advanced only by the deterministic project service, never by an API payload.
    """

    ELICITATION = "elicitation"
    ANALYSIS = "analysis"
    REVIEW = "review"
    BASELINED = "baselined"


class NormativeSourceType(StrEnum):
    """The normative-source taxonomy (approved Phase 0 C.1; architecture G.5).

    Exactly eight types, and every knowledge item carries exactly one of them
    (``FR-RAG-001``). They are deliberately **not** collapsed into a generic
    "regulation": a card-scheme requirement binds by contract, not by law, and a
    control framework is a voluntary reference taxonomy. Presenting either as law
    is the imprecision C.1 exists to prevent. The spellings are the ones G.5 uses.
    """

    STATUTE = "statute"
    REGULATORY_DIRECTION = "regulatory_direction"
    REGULATORY_GUIDANCE = "regulatory_guidance"
    ORG_POLICY = "org_policy"
    CONTRACTUAL_SCHEME = "contractual_scheme"
    INDUSTRY_STANDARD = "industry_standard"
    CONTROL_FRAMEWORK = "control_framework"
    BEST_PRACTICE = "best_practice"


#: How binding each source type is, verbatim from the approved C.1 table. Shown
#: beside every citation so that a standard is never presented as a law.
SOURCE_TYPE_BINDING: dict[NormativeSourceType, str] = {
    NormativeSourceType.STATUTE: "Legally binding",
    NormativeSourceType.REGULATORY_DIRECTION: "Binding on regulated entities",
    NormativeSourceType.REGULATORY_GUIDANCE: "Persuasive, not strictly binding",
    NormativeSourceType.ORG_POLICY: "Binding inside one organisation",
    NormativeSourceType.CONTRACTUAL_SCHEME: "Binding by contract, not law",
    NormativeSourceType.INDUSTRY_STANDARD: "Voluntary unless mandated by law or contract",
    NormativeSourceType.CONTROL_FRAMEWORK: "Voluntary reference taxonomy",
    NormativeSourceType.BEST_PRACTICE: "Non-binding professional convention",
}


class LicenceClass(StrEnum):
    """What a source's licence permits the knowledge base to store (G.5, D.2).

    The machine-checkable form of ``licence_note``. Architecture G.5 requires the
    ingestion path to refuse full text where the licence forbids it, which a
    free-text note cannot enforce on its own.
    """

    #: Official statutes, regulator texts and public-domain frameworks may be
    #: extracted with citation (approved Phase 0 D.2).
    EXTRACT_PERMITTED = "extract_permitted"
    #: Copyrighted standards (ISO/IEC): clause identifiers and team-written
    #: paraphrases only, never copied normative text (approved Phase 0 D.2).
    PARAPHRASE_ONLY = "paraphrase_only"
    #: Fictional material written for the project - the synthetic organisational
    #: policies D.2 approves. Never a stand-in for a real law or regulation.
    SYNTHETIC = "synthetic"


class TextOrigin(StrEnum):
    """Where a knowledge item's text came from, as declared by its curator."""

    VERBATIM_EXTRACT = "verbatim_extract"
    TEAM_PARAPHRASE = "team_paraphrase"
    SYNTHETIC = "synthetic"


#: Which text origins each licence admits. Checked deterministically at
#: ingestion; anything outside this table is refused.
LICENCE_PERMITTED_ORIGINS: dict[LicenceClass, frozenset[TextOrigin]] = {
    LicenceClass.EXTRACT_PERMITTED: frozenset(
        {TextOrigin.VERBATIM_EXTRACT, TextOrigin.TEAM_PARAPHRASE}
    ),
    LicenceClass.PARAPHRASE_ONLY: frozenset({TextOrigin.TEAM_PARAPHRASE}),
    LicenceClass.SYNTHETIC: frozenset({TextOrigin.SYNTHETIC}),
}

#: Source types that may be synthetic. D.2 approves synthetic *organisational
#: policies*; team-written practice notes are the other honest case. A synthetic
#: statute or regulatory direction would be a fake law, so it is refused.
SYNTHETIC_PERMITTED_TYPES: frozenset[NormativeSourceType] = frozenset(
    {NormativeSourceType.ORG_POLICY, NormativeSourceType.BEST_PRACTICE}
)


class KnowledgeItemStatus(StrEnum):
    """A knowledge item's status (architecture G.5, J.6): ``active → superseded``."""

    ACTIVE = "active"
    SUPERSEDED = "superseded"


class SupersessionKind(StrEnum):
    """Why an item left ``active``. The status stays two-valued, as G.5 specifies.

    ``FR-RAG-006`` names *add, version, retire*; architecture S names *supersede*.
    All three removals are the same G.5 transition, distinguished here.
    """

    #: Replaced by the next version of the same item (``FR-RAG-006`` *version*).
    VERSIONED = "versioned"
    #: Replaced by a different item, e.g. one from an amended instrument (S).
    REPLACED = "replaced"
    #: Withdrawn with no successor (``FR-RAG-006`` *retire*).
    RETIRED = "retired"


class ChunkStrategy(StrEnum):
    """How a chunk was cut (architecture J.3)."""

    #: Knowledge item: one chunk per clause or control.
    CLAUSE = "clause"
    #: Token window with overlap, where no clause structure exists or a clause
    #: is too long to embed whole.
    WINDOW = "window"
    #: Project document: headings and paragraphs first.
    SECTION = "section"
    #: Transcript: one chunk per utterance, never splitting a speaker turn.
    UTTERANCE = "utterance"


class ActorKind(StrEnum):
    """Who or what performed an audited action (architecture O.1)."""

    HUMAN = "human"
    AGENT_ROLE = "agent_role"
    SYSTEM = "system"


class GraphRunStatus(StrEnum):
    """Lifecycle of a graph run (architecture C.8, T)."""

    PENDING = "pending"
    RUNNING = "running"
    SUSPENDED = "suspended"
    STALLED = "stalled"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentRunStatus(StrEnum):
    """Outcome of a single agent-role invocation (architecture F.1)."""

    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"


class AuditEventType(StrEnum):
    """Audit event taxonomy (architecture O.2).

    P0 defines the subset the foundation itself emits. The content, grounded
    analysis, governance and SDLC events are added by the roadmap phases that
    raise them - the architecture lists the full taxonomy.
    """

    # Run lifecycle
    RUN_STARTED = "RUN_STARTED"
    RUN_SUSPENDED = "RUN_SUSPENDED"
    RUN_RESUMED = "RUN_RESUMED"
    RUN_COMPLETED = "RUN_COMPLETED"
    RUN_FAILED = "RUN_FAILED"
    NODE_STARTED = "NODE_STARTED"
    NODE_COMPLETED = "NODE_COMPLETED"
    NODE_FAILED = "NODE_FAILED"

    # Administration and security
    PROJECT_CREATED = "PROJECT_CREATED"
    PROJECT_DELETED = "PROJECT_DELETED"
    MEMBER_ADDED = "MEMBER_ADDED"
    PERMISSION_DENIED = "PERMISSION_DENIED"

    # Requirements repository (added by the requirements-repository phase)
    REQUIREMENT_CREATED = "REQUIREMENT_CREATED"
    REQUIREMENT_VERSION_CREATED = "REQUIREMENT_VERSION_CREATED"
    REQUIREMENT_WITHDRAWN = "REQUIREMENT_WITHDRAWN"
    REQUIREMENT_SUPERSEDED = "REQUIREMENT_SUPERSEDED"
    STATE_TRANSITION = "STATE_TRANSITION"

    # Governance
    APPROVAL_TASK_CREATED = "APPROVAL_TASK_CREATED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    APPROVAL_MODIFIED = "APPROVAL_MODIFIED"
    GATE_PASSED = "GATE_PASSED"
    BASELINE_COMMITTED = "BASELINE_COMMITTED"
    BASELINE_MEMBER_ADDED = "BASELINE_MEMBER_ADDED"

    # Knowledge base (added by the knowledge-base phase). The first three are
    # named in architecture O.2; the last three follow the P1 precedent of a
    # phase adding the events it raises.
    KB_ITEM_ADDED = "KB_ITEM_ADDED"
    KB_ITEM_SUPERSEDED = "KB_ITEM_SUPERSEDED"
    SOURCE_INGESTED = "SOURCE_INGESTED"
    KB_SOURCE_ADDED = "KB_SOURCE_ADDED"
    KB_CONTROL_ADDED = "KB_CONTROL_ADDED"
    KB_SCOPE_CHANGED = "KB_SCOPE_CHANGED"

    # Extraction and classification (added by the extraction phase). The first
    # four are named in architecture O.2; the review and merge events follow the
    # P1/P2 precedent of a phase adding the events it raises.
    EXTRACTION_PROPOSED = "EXTRACTION_PROPOSED"
    EXTRACTION_VALIDATED = "EXTRACTION_VALIDATED"
    CLASSIFICATION_PROPOSED = "CLASSIFICATION_PROPOSED"
    HUMAN_OVERRIDE = "HUMAN_OVERRIDE"
    REVIEW_ITEM_RAISED = "REVIEW_ITEM_RAISED"
    REVIEW_ITEM_RESOLVED = "REVIEW_ITEM_RESOLVED"
    REQUIREMENTS_MERGED = "REQUIREMENTS_MERGED"

    # Elicitation and clarification (added by the elicitation phase, P4). The
    # content events are named in architecture O.2 and E #2 / E #4; the session
    # events follow the P1-P3 precedent of a phase adding the events it raises.
    STAKEHOLDER_CREATED = "STAKEHOLDER_CREATED"
    INTERVIEW_SESSION_CREATED = "INTERVIEW_SESSION_CREATED"
    INTERVIEW_SESSION_PAUSED = "INTERVIEW_SESSION_PAUSED"
    INTERVIEW_SESSION_RESUMED = "INTERVIEW_SESSION_RESUMED"
    INTERVIEW_SESSION_COMPLETED = "INTERVIEW_SESSION_COMPLETED"
    INTERVIEW_SESSION_STALLED = "INTERVIEW_SESSION_STALLED"
    QUESTION_GENERATED = "QUESTION_GENERATED"
    UTTERANCE_RECORDED = "UTTERANCE_RECORDED"
    ANSWER_ASSESSED = "ANSWER_ASSESSED"
    QUALITY_FINDING_RAISED = "QUALITY_FINDING_RAISED"
    CLARIFICATION_RAISED = "CLARIFICATION_RAISED"
    CLARIFICATION_ANSWERED = "CLARIFICATION_ANSWERED"
    CLARIFICATION_DISMISSED = "CLARIFICATION_DISMISSED"
    CLARIFICATION_REANALYSED = "CLARIFICATION_REANALYSED"

    # Quality and conflict detection (added by the quality phase, P5). The
    # conflict events are named in architecture E #6 and O.2; the review events
    # follow the P1-P4 precedent of a phase adding the events it raises.
    QUALITY_FINDING_RESOLVED = "QUALITY_FINDING_RESOLVED"
    QUALITY_FINDING_DISMISSED = "QUALITY_FINDING_DISMISSED"
    CONFLICT_SHORTLISTED = "CONFLICT_SHORTLISTED"
    CONFLICT_PROPOSED = "CONFLICT_PROPOSED"
    CONFLICT_REVIEWED = "CONFLICT_REVIEWED"
    CONFLICT_RESOLVED = "CONFLICT_RESOLVED"
    CONFLICT_DISMISSED = "CONFLICT_DISMISSED"
    GLOSSARY_TERM_ADDED = "GLOSSARY_TERM_ADDED"

    # Compliance and security analysis (added by the compliance phase, P6). The
    # grounded-analysis events are named in architecture O.2 and E #7 / E #8; the
    # rest follow the P1-P5 precedent of a phase adding the events it raises.
    COMPLIANCE_RETRIEVED = "COMPLIANCE_RETRIEVED"
    COMPLIANCE_PROPOSED = "COMPLIANCE_PROPOSED"
    COMPLIANCE_MAPPING_ACCEPTED = "COMPLIANCE_MAPPING_ACCEPTED"
    COMPLIANCE_CLAIM_DROPPED = "COMPLIANCE_CLAIM_DROPPED"
    COMPLIANCE_GAP_FOUND = "COMPLIANCE_GAP_FOUND"
    COMPLIANCE_MAPPING_REVIEWED = "COMPLIANCE_MAPPING_REVIEWED"
    SECURITY_REQUIREMENT_DERIVED = "SECURITY_REQUIREMENT_DERIVED"
    PRIVACY_REQUIREMENT_DERIVED = "PRIVACY_REQUIREMENT_DERIVED"
    SECURITY_FINDING_DROPPED = "SECURITY_FINDING_DROPPED"
    SECURITY_RISK_EVALUATED = "SECURITY_RISK_EVALUATED"
    SECURITY_FINDING_REVIEWED = "SECURITY_FINDING_REVIEWED"


class Action(StrEnum):
    """Actions the policy can authorise (architecture ADR-009).

    Only the actions P0 can actually perform, plus the gate-decision action,
    which is named here because the policy matrix test asserts that no agent
    role can ever perform it.
    """

    PROJECT_CREATE = "project.create"
    PROJECT_READ = "project.read"
    PROJECT_DELETE = "project.delete"
    MEMBER_ADD = "member.add"
    AUDIT_READ = "audit.read"
    AUDIT_VERIFY = "audit.verify"
    RUN_START = "run.start"
    RUN_READ = "run.read"
    #: Writing a run's own records - agent runs, proposals, review items. The
    #: pipeline does this on the analyst's behalf; it decides nothing.
    RUN_RECORD = "run.record"
    APPROVAL_DECIDE = "approval.decide"

    # Requirements repository
    REQUIREMENT_CREATE = "requirement.create"
    REQUIREMENT_READ = "requirement.read"
    REQUIREMENT_UPDATE = "requirement.update"
    REQUIREMENT_TRANSITION = "requirement.transition"
    REQUIREMENT_SUBMIT = "requirement.submit"
    REQUIREMENT_WITHDRAW = "requirement.withdraw"
    APPROVAL_TASK_READ = "approval_task.read"
    BASELINE_CREATE = "baseline.create"
    BASELINE_READ = "baseline.read"

    # Knowledge base and retrieval
    KB_READ = "kb.read"
    KB_ADMINISTER = "kb.administer"
    KB_SCOPE_READ = "kb.scope.read"
    KB_SCOPE_MANAGE = "kb.scope.manage"
    KB_RETRIEVE = "kb.retrieve"
    EVIDENCE_CREATE = "evidence.create"
    EVIDENCE_READ = "evidence.read"

    # Project sources, extraction and classification
    SOURCE_CREATE = "source.create"
    SOURCE_READ = "source.read"
    #: Recording a model's classification proposal. The pipeline does this on
    #: the analyst's behalf; the proposal is never authoritative.
    CLASSIFICATION_PROPOSE = "classification.propose"
    #: A human replacing a requirement version's labels (``FR-CLS-003``).
    CLASSIFICATION_OVERRIDE = "classification.override"
    REVIEW_READ = "review.read"
    REVIEW_RESOLVE = "review.resolve"
    #: Merging a duplicate into another requirement, keeping both source links.
    REQUIREMENT_MERGE = "requirement.merge"

    # Elicitation and clarification (P4)
    STAKEHOLDER_CREATE = "stakeholder.create"
    STAKEHOLDER_READ = "stakeholder.read"
    #: Starting an interview session (and its elicitation graph run).
    SESSION_CREATE = "session.create"
    #: Reading a session, its coverage and its utterances. A user holding only the
    #: Stakeholder role reads only the sessions they are the stakeholder of.
    SESSION_READ = "session.read"
    #: Answering the pending interview question: the stakeholder themself, or an
    #: analyst recording the answer on their behalf (``FR-ELI-005``).
    SESSION_ANSWER = "session.answer"
    #: Pausing, resuming or retrying a session (``FR-ELI-005``).
    SESSION_MANAGE = "session.manage"
    #: Recording a question the interviewer role generated. The elicitation
    #: pipeline does this on the analyst's behalf; it decides nothing.
    UTTERANCE_RECORD = "utterance.record"
    #: Recording a quality finding against a requirement version. In P4 only an
    #: analyst records findings (the quality-analysis role is P5).
    QUALITY_FINDING_CREATE = "quality_finding.create"
    QUALITY_FINDING_READ = "quality_finding.read"
    #: Raising a clarification for a finding: role #4 proposes the question.
    CLARIFICATION_RAISE = "clarification.raise"
    CLARIFICATION_READ = "clarification.read"
    CLARIFICATION_ANSWER = "clarification.answer"
    #: Dismissing a clarification with a recorded reason (``FR-CLR-004``).
    CLARIFICATION_DISMISS = "clarification.dismiss"

    # Quality and conflict detection (P5)
    #: Recording what the quality engine detected - a rule's finding or a model's
    #: validated proposal. The pipeline does this on the analyst's behalf; a
    #: detected finding decides nothing.
    QUALITY_FINDING_DETECT = "quality_finding.detect"
    #: Closing a finding: resolved (the defect is gone) or dismissed (it never was).
    QUALITY_FINDING_RESOLVE = "quality_finding.resolve"
    QUALITY_FINDING_DISMISS = "quality_finding.dismiss"
    #: Recording a detected conflict (``FR-CNF-001``..``003``). Pipeline, like detect.
    CONFLICT_DETECT = "conflict.detect"
    CONFLICT_READ = "conflict.read"
    #: Taking a conflict under review, resolving it (the G4 decision,
    #: ``FR-CNF-005``) or dismissing it as not a conflict. Human decisions.
    CONFLICT_REVIEW = "conflict.review"
    CONFLICT_RESOLVE = "conflict.resolve"
    CONFLICT_DISMISS = "conflict.dismiss"
    #: The project glossary against which undefined terms are detected (``FR-QAL-005``).
    GLOSSARY_READ = "glossary.read"
    GLOSSARY_MANAGE = "glossary.manage"

    # Compliance and security analysis (P6)
    #: Recording what the compliance pipeline produced - validated candidate
    #: mappings and rule-engine gaps. The pipeline does this on the analyst's
    #: behalf; none of it is a legal determination or an approval.
    COMPLIANCE_ANALYSE = "compliance.analyse"
    COMPLIANCE_READ = "compliance.read"
    #: Recording derived security/privacy requirements with their
    #: deterministically evaluated risk level (architecture I.7).
    SECURITY_ANALYSE = "security.analyse"
    SECURITY_READ = "security.read"
    #: Raising a G2 or G3 approval task from a persisted, deterministically
    #: evaluated value (architecture C.3 ``gate_fanout``). Raising is not deciding:
    #: only a human holding the gate's role decides (``APPROVAL_DECIDE``).
    GATE_TASK_RAISE = "gate_task.raise"


class ResourceType(StrEnum):
    """Resource types the policy can authorise against."""

    PROJECT = "project"
    PROJECT_MEMBER = "project_member"
    AUDIT_EVENT = "audit_event"
    GRAPH_RUN = "graph_run"
    APPROVAL_TASK = "approval_task"
    REQUIREMENT = "requirement"
    REQUIREMENT_VERSION = "requirement_version"
    BASELINE = "baseline"
    NORMATIVE_SOURCE = "normative_source"
    CONTROL = "control"
    KNOWLEDGE_ITEM = "knowledge_item"
    KNOWLEDGE_CHUNK = "knowledge_chunk"
    SOURCE_ALLOWLIST = "source_allowlist"
    EVIDENCE = "evidence"
    SOURCE_DOCUMENT = "source_document"
    EXTRACTION_CANDIDATE = "extraction_candidate"
    CLASSIFICATION = "requirement_classification"
    REVIEW_ITEM = "review_item"
    STAKEHOLDER = "stakeholder"
    INTERVIEW_SESSION = "interview_session"
    UTTERANCE = "utterance"
    QUALITY_FINDING = "quality_finding"
    CLARIFICATION = "clarification"
    CONFLICT = "conflict"
    GLOSSARY_TERM = "glossary_term"
    COMPLIANCE_MAPPING = "compliance_mapping"
    COMPLIANCE_GAP = "compliance_gap"
    SECURITY_PRIVACY_FINDING = "security_privacy_finding"


# ---------------------------------------------------------------------------
# Extraction and classification (roadmap phase P3)
# ---------------------------------------------------------------------------


class SourceDocumentType(StrEnum):
    """What a project source document is (``FR-ING-001``; architecture G.3).

    Project content only. A normative source is curated into the knowledge base
    (P2), never uploaded as project content: the two corpora carry different
    trust classes and are never mixed (architecture J.1).
    """

    TRANSCRIPT = "transcript"
    MEETING_NOTES = "meeting_notes"
    POLICY = "policy"
    LEGACY_SPECIFICATION = "legacy_specification"
    AUDIT_FINDING = "audit_finding"


class DataSensitivity(StrEnum):
    """The uploader's declared sensitivity of a source (``FR-ING-004``).

    Declared, not detected: automatic sensitivity classification belongs with the
    masking pipeline (``FR-ING-003``), which is not implemented yet. The value
    matters now because it decides whether content may leave the machine at all
    (see the LLM gateway's egress rule).
    """

    #: Fictional or team-authored material with no real person's data in it.
    SYNTHETIC = "synthetic"
    #: Not yet classified. Treated as sensitive.
    UNCLASSIFIED = "unclassified"
    CONFIDENTIAL = "confidential"


class MaskingStatus(StrEnum):
    """Whether a source's stored text passed a protective masking stage (J.2)."""

    NOT_MASKED = "not_masked"
    MASKED = "masked"


class CandidateStatus(StrEnum):
    """What happened to one extraction proposal.

    ``PROPOSED`` is the only non-terminal value; a candidate is decided once.
    """

    PROPOSED = "proposed"
    #: Validated and persisted as a requirement version.
    ACCEPTED = "accepted"
    #: An exact duplicate of another candidate in the same batch. Its source
    #: spans were added to that candidate's requirement.
    MERGED = "merged"
    #: Failed deterministic validation. Never persisted as a requirement.
    REJECTED = "rejected"


class ProposalSource(StrEnum):
    """Who produced a label or an acceptance criterion."""

    AGENT = "agent"
    HUMAN = "human"


class ReviewReason(StrEnum):
    """Why an AI-produced proposal needs a human look (``FR-CLS-002``).

    The review queue is for AI proposals. It is **not** approval: requirements
    are approved only at gate G1.
    """

    #: The model's output failed schema validation, including after the repair.
    MALFORMED_OUTPUT = "malformed_output"
    #: A proposal cited no source, or a source that does not resolve (FR-EXT-007).
    UNRESOLVED_SOURCE = "unresolved_source"
    #: A proposal failed another deterministic extraction check.
    EXTRACTION_INVALID = "extraction_invalid"
    #: The model's own review signal for an extraction is below the threshold.
    LOW_EXTRACTION_SIGNAL = "low_extraction_signal"
    #: A classification label's review signal is below the threshold.
    LOW_CLASSIFICATION_SIGNAL = "low_classification_signal"
    #: The model proposed a label outside the thirteen approved categories.
    UNKNOWN_LABEL = "unknown_label"
    #: No valid label survived validation, or the classification call failed.
    CLASSIFICATION_FAILED = "classification_failed"
    #: Two requirements look alike but are not identical; a human decides.
    POSSIBLE_DUPLICATE = "possible_duplicate"
    #: Proposed acceptance criteria failed validation and were not stored.
    ACCEPTANCE_CRITERIA_INVALID = "acceptance_criteria_invalid"
    #: P6: retrieval found no allowlisted evidence for a requirement, so no
    #: compliance mapping was attempted (``FR-RAG-005``); a human must look.
    EVIDENCE_UNAVAILABLE = "evidence_unavailable"
    #: P6: a compliance or security/privacy claim was dropped by deterministic
    #: validation (unsupported citation, prohibited language, authority claim).
    CLAIM_DROPPED = "claim_dropped"


class ReviewStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


class ReviewResolution(StrEnum):
    """How a human closed a review item. None of these approves anything."""

    #: The proposal stands as the model made it.
    ACCEPTED = "accepted"
    #: The proposal is discarded (a requirement is withdrawn through P1).
    REJECTED = "rejected"
    #: A human replaced the labels (``FR-CLS-003``).
    OVERRIDDEN = "overridden"
    #: Duplicates merged, keeping every source link.
    MERGED = "merged"
    #: Look-alikes confirmed as different requirements.
    KEPT_DISTINCT = "kept_distinct"
    #: Seen and recorded; nothing to change (e.g. a rejected proposal).
    ACKNOWLEDGED = "acknowledged"


# ---------------------------------------------------------------------------
# Elicitation and clarification (P4; architecture C.4, E #2, E #4, G.3, G.4)
# ---------------------------------------------------------------------------


class StakeholderAuthority(StrEnum):
    """How much weight a stakeholder's statements carry (architecture G.3)."""

    DECISION_MAKER = "decision_maker"
    CONTRIBUTOR = "contributor"
    INFORMANT = "informant"


class InterviewSessionKind(StrEnum):
    """What a session holds. Every utterance belongs to exactly one session."""

    #: An adaptive interview driven by ``elicitation_graph``.
    INTERVIEW = "interview"
    #: One clarification round trip: the question and the answer to it.
    CLARIFICATION = "clarification"


class InterviewSessionStatus(StrEnum):
    """Deterministic session states. Only code moves between them."""

    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    #: A step failed safely (model output invalid after its bounded retries, or
    #: durable and checkpoint state disagreed). An analyst can retry it.
    STALLED = "stalled"


class SpeakerKind(StrEnum):
    #: The interviewer: a question the Stakeholder Interaction role proposed.
    SYSTEM = "system"
    #: A stakeholder's own words, typed by them or recorded on their behalf.
    STAKEHOLDER = "stakeholder"


class TopicStatus(StrEnum):
    """Coverage of one template topic. Written only by the coverage tracker."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COVERED = "covered"
    #: Addressed, but still vague, incomplete or inconsistent when the follow-up
    #: bound was reached. Visible to the analyst; never silently "covered".
    UNRESOLVED = "unresolved"


class AnswerStatus(StrEnum):
    """What the answer-assessment step may propose (architecture C.4)."""

    COMPLETE = "complete"
    VAGUE = "vague"
    INCOMPLETE = "incomplete"
    INCONSISTENT = "inconsistent"


class QualityFindingType(StrEnum):
    """Defect types a quality finding may carry (architecture G.4; problem statement §10).

    P4 defined the first seven; P5 adds the rest of the §10 checklist it detects.
    A *conflict between two requirements* is not a finding type: it is its own
    entity (``conflict``, G.4) and a transition guard (D12), never a state.
    """

    AMBIGUITY = "ambiguity"
    INCOMPLETENESS = "incompleteness"
    UNTESTABILITY = "untestability"
    #: An exact duplicate of another requirement (``FR-QAL-004``).
    DUPLICATION = "duplication"
    UNDEFINED_TERM = "undefined_term"
    MISSING_SOURCE = "missing_source"
    INCONSISTENCY = "inconsistency"
    #: Overlapping, not identical, wording with another requirement (``FR-QAL-004``).
    NEAR_DUPLICATE = "near_duplicate"
    #: An absolute or impossible target (``FR-QAL-009``, secondary).
    INFEASIBILITY = "infeasibility"
    #: A *signal* only: the full analysis is P6 (``FR-QAL-008`` -> ``FR-SEC-001``).
    MISSING_SECURITY_CONSIDERATION = "missing_security_consideration"
    #: A *signal* only: the full analysis is P6 (``FR-QAL-008`` -> ``FR-CMP-002``).
    MISSING_PRIVACY_CONSIDERATION = "missing_privacy_consideration"


class FindingSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class QualityFindingStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class FindingDetector(StrEnum):
    """Who recorded a finding or a conflict.

    ``rule`` - a deterministic heuristic (P5); ``agent`` - a model proposal that
    passed deterministic validation (P5); ``human`` - an analyst (P4).
    """

    HUMAN = "human"
    AGENT = "agent"
    RULE = "rule"


class ClarificationStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    DISMISSED = "dismissed"


class ReanalysisStatus(StrEnum):
    """The outcome of re-analysing a requirement after its clarification (FR-CLR-003)."""

    PENDING = "pending"
    #: A new requirement version was created.
    NEW_VERSION = "new_version"
    #: Re-extraction produced the same statement; no version was created.
    NO_CHANGE = "no_change"
    #: Re-analysis failed; the answered clarification stands and can be retried.
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Quality and conflict detection (roadmap phase P5)
# ---------------------------------------------------------------------------


class ConflictClass(StrEnum):
    """How sure the detector is that two requirements contradict (P5).

    Only these two are ever persisted. A pair judged compatible under its
    conditions, a duplicate, unrelated, or without enough information is not a
    conflict and leaves no ``conflict`` row.
    """

    #: The two cannot both be satisfied as written.
    DEFINITE = "definite"
    #: They may contradict; conditions or scope are unclear. A human decides.
    POTENTIAL = "potential"


class ConflictKind(StrEnum):
    """What the two requirements disagree about (architecture E #6: ``type``)."""

    NUMERIC = "numeric"
    TIMING = "timing"
    ACTOR_SCOPE = "actor_scope"
    LOGICAL = "logical"
    SECURITY = "security"
    BEHAVIOURAL = "behavioural"
    OTHER = "other"


class ConflictVerdict(StrEnum):
    """Every outcome the conflict pipeline distinguishes for a shortlisted pair."""

    DEFINITE_CONFLICT = "definite_conflict"
    POTENTIAL_CONFLICT = "potential_conflict"
    CONDITIONAL_COMPATIBLE = "conditional_compatible"
    DUPLICATE = "duplicate"
    NO_CONFLICT = "no_conflict"
    INSUFFICIENT_INFORMATION = "insufficient_information"


class ConflictStatus(StrEnum):
    """A conflict's review status. ``OPEN`` and ``UNDER_REVIEW`` block (D12)."""

    OPEN = "open"
    UNDER_REVIEW = "under_review"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


#: Conflict statuses that still block ``VALIDATED`` and ``PENDING_APPROVAL`` (H.2).
BLOCKING_CONFLICT_STATUSES: frozenset[ConflictStatus] = frozenset(
    {ConflictStatus.OPEN, ConflictStatus.UNDER_REVIEW}
)


class ConflictResolution(StrEnum):
    """The G4 decision (architecture M.3): choose A, choose B, or synthesise new.

    "Defer" is not a resolution: a deferred conflict simply stays open.
    """

    CHOOSE_A = "choose_a"
    CHOOSE_B = "choose_b"
    SYNTHESISE_NEW = "synthesise_new"
    #: Both stand, reconciled by a stated condition the analyst records.
    RECONCILED = "reconciled"


class ReviewPriority(StrEnum):
    """A heuristic review-prioritisation label - never a probability (Phase 0 H.1)."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ---------------------------------------------------------------------------
# Compliance and security analysis (roadmap phase P6)
# ---------------------------------------------------------------------------


class ComplianceRelationship(StrEnum):
    """How a requirement relates to a potentially applicable control (E #7).

    Candidate language only: none of these says a requirement *complies*.
    """

    #: The requirement, as written, appears to address the control.
    ADDRESSES = "addresses"
    #: The requirement addresses part of the control; the rest is not covered.
    PARTIALLY_ADDRESSES = "partially_addresses"
    #: The control is relevant context for the requirement, which does not address it.
    RELEVANT_CONTEXT = "relevant_context"


#: Relationships that count a checklist control as *covered* for gap detection
#: (K.2). ``relevant_context`` never covers a control.
COVERING_RELATIONSHIPS: frozenset[ComplianceRelationship] = frozenset(
    {ComplianceRelationship.ADDRESSES, ComplianceRelationship.PARTIALLY_ADDRESSES}
)


class ObligationKind(StrEnum):
    """What kind of expectation a checklist entry is (``FR-CMP-003``)."""

    CONTROL = "control"
    APPROVAL_CHECKPOINT = "approval_checkpoint"
    AUDIT_CHECKPOINT = "audit_checkpoint"
    RETENTION_OBLIGATION = "retention_obligation"
    REPORTING_OBLIGATION = "reporting_obligation"


class ComplianceMappingStatus(StrEnum):
    """A validated candidate mapping's review status.

    ``CANDIDATE`` - accepted by deterministic validation and not a high-impact
    interpretation. ``PENDING_REVIEW`` - a high-impact interpretation awaiting
    G2; it blocks ``ANALYZED -> VALIDATED``. ``APPROVED`` / ``REJECTED`` - the
    Compliance Officer's G2 decision. A high-impact mapping can never be
    ``CANDIDATE`` (a database check).
    """

    CANDIDATE = "candidate"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class ComplianceGapOrigin(StrEnum):
    """Why a gap row exists. Neither origin is a model's judgement."""

    #: The rule engine: an expected control with no covering mapping (K.2).
    RULE_ENGINE = "rule_engine"
    #: The Compliance Officer rejected, at G2, the mapping that covered it (M.3).
    G2_REJECTION = "g2_rejection"


class SecurityPrivacyCategory(StrEnum):
    SECURITY = "security"
    PRIVACY = "privacy"


class SecurityControlFamily(StrEnum):
    """Security and privacy control families (``FR-SEC-001``, ``FR-SEC-002``; I.7)."""

    AUTHENTICATION = "authentication"
    AUTHORISATION = "authorisation"
    CRYPTOGRAPHY = "cryptography"
    AUDIT_LOGGING = "audit_logging"
    SESSION_MANAGEMENT = "session_management"
    TRANSACTION_INTEGRITY = "transaction_integrity"
    FRAUD_CONTROLS = "fraud_controls"
    DATA_MINIMISATION = "data_minimisation"
    CONSENT = "consent"
    RETENTION = "retention"
    SUBJECT_RIGHTS = "subject_rights"


class SecurityRiskLevel(StrEnum):
    """The security/privacy risk level of a derived requirement (I.7).

    Project/requirement risk only: never a borrower's credit risk, and not the
    P7 risk register's severity.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SecurityFindingStatus(StrEnum):
    """A derived security/privacy requirement's review status.

    ``PROPOSED`` - persisted with an authoritative level below ``high``.
    ``PENDING_REVIEW`` - authoritative level ``high``: G3 is required, and it
    blocks ``ANALYZED -> VALIDATED``. A ``high`` finding can never be
    ``PROPOSED`` (a database check). ``APPROVED`` / ``REJECTED`` - the Security
    Reviewer's G3 decision.
    """

    PROPOSED = "proposed"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class EvidenceStatus(StrEnum):
    """Whether a derived security/privacy requirement cites curated evidence."""

    SUPPORTED = "supported"
    #: No supplied evidence supports it. Stated as such, never filled from memory.
    UNAVAILABLE = "unavailable"
