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


class ResourceType(StrEnum):
    """Resource types the policy can authorise against."""

    PROJECT = "project"
    PROJECT_MEMBER = "project_member"
    AUDIT_EVENT = "audit_event"
    GRAPH_RUN = "graph_run"
    APPROVAL_TASK = "approval_task"
