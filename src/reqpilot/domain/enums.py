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
