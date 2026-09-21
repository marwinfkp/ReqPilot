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
