"""The authorization policy (architecture ADR-009).

One module, pure functions, no I/O. That combination is what makes
``(role x action x resource)`` assertable as a matrix in a single test file,
which is the property the architecture asks for.

Three design rules are enforced here rather than trusted to callers:

1. **Deny by default.** An unknown action, an unknown role, or an actor with no
   roles is denied. There is no permissive fallthrough.
2. **Project isolation precedes permission.** An actor is checked against the
   resource's project *before* any role grant is considered, so a correct role
   in the wrong project never yields an allow (``FR-PRJ-004``).
3. **Agent roles can never decide a gate.** Only a human actor holding the
   gate's role may perform ``APPROVAL_DECIDE``. This is the code-level form of
   "an LLM cannot approve its own output" (architecture J.1, M.2).
4. **A gate decision is authorised per gate, never by action grant.**
   ``APPROVAL_DECIDE`` is allowed only for a named gate and a declared role that
   the gate requires (``GATE_REQUIRED_ROLES``) and that the actor holds in the
   resource's project. The bare action is refused, and ``is_superuser`` does not
   substitute for holding the gate's role.
5. **Knowledge curation and grounding scope are human decisions.** No non-human
   actor may administer the knowledge base or change a project's allowlist,
   jurisdictions or KB pin, whatever roles it holds (architecture E.1: agent
   roles write proposals only).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from reqpilot.domain.enums import GATE_REQUIRED_ROLES, Action, ActorKind, Gate, ResourceType, Role
from reqpilot.domain.errors import AuthorizationError, ProjectIsolationError
from reqpilot.domain.ids import ActorId, ProjectId


@dataclass(frozen=True)
class Actor:
    """Whoever is attempting an action.

    ``roles_by_project`` reflects that a role is held *within a project*, never
    globally - a compliance officer on one project has no standing on another.
    In a small team one person may hold several roles in one project, which is
    why the value is a set.
    """

    actor_id: ActorId
    kind: ActorKind = ActorKind.HUMAN
    roles_by_project: dict[ProjectId, frozenset[Role]] = field(default_factory=dict)
    is_superuser: bool = False

    def roles_in(self, project_id: ProjectId | None) -> frozenset[Role]:
        if project_id is None:
            return frozenset()
        return self.roles_by_project.get(project_id, frozenset())


@dataclass(frozen=True)
class ResourceRef:
    """The thing being acted upon.

    ``project_id`` is ``None`` only for resources that genuinely have no project
    scope - creating a project being the obvious case.

    ``gate`` and ``role_exercised`` are the context an ``APPROVAL_DECIDE`` check
    needs: which gate is being decided, and in which role the actor claims to
    decide it. Every other action ignores them, and ``APPROVAL_DECIDE`` without
    them is refused.
    """

    resource_type: ResourceType
    project_id: ProjectId | None = None
    resource_id: str | None = None
    gate: Gate | None = None
    role_exercised: Role | None = None


@dataclass(frozen=True)
class Decision:
    """The outcome of an authorization check.

    Carries a reason because a denial that cannot be explained cannot be audited
    usefully, and because the reason is what a test asserts on.
    """

    allowed: bool
    reason: str

    def __bool__(self) -> bool:
        return self.allowed


# ---------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------

#: Which roles may perform each action. Deny-by-default: an action absent from
#: this mapping is refused (see :func:`can`).
#:
#: ``APPROVAL_DECIDE`` is deliberately absent. Gate decisions are authorised by
#: the gate's own required roles (``GATE_REQUIRED_ROLES``) in
#: :func:`_can_decide_gate`, not by a blanket action grant.
_ACTION_GRANTS: dict[Action, frozenset[Role]] = {
    Action.PROJECT_CREATE: frozenset({Role.ANALYST}),
    Action.PROJECT_READ: frozenset(
        {
            Role.ANALYST,
            Role.STAKEHOLDER,
            Role.COMPLIANCE_OFFICER,
            Role.SECURITY_REVIEWER,
            Role.PROJECT_MANAGER,
            Role.AUDITOR,
            Role.KB_ADMIN,
        }
    ),
    Action.PROJECT_DELETE: frozenset({Role.PROJECT_MANAGER}),
    Action.MEMBER_ADD: frozenset({Role.PROJECT_MANAGER}),
    Action.AUDIT_READ: frozenset(
        {
            Role.ANALYST,
            Role.COMPLIANCE_OFFICER,
            Role.SECURITY_REVIEWER,
            Role.PROJECT_MANAGER,
            Role.AUDITOR,
        }
    ),
    Action.AUDIT_VERIFY: frozenset({Role.AUDITOR}),
    Action.RUN_START: frozenset({Role.ANALYST}),
    Action.RUN_READ: frozenset(
        {
            Role.ANALYST,
            Role.COMPLIANCE_OFFICER,
            Role.SECURITY_REVIEWER,
            Role.PROJECT_MANAGER,
            Role.AUDITOR,
        }
    ),
    # --- requirements repository -------------------------------------
    # Authoring is the analyst's job. Reviewing roles can read everything in
    # their project but cannot author, which is what keeps the later approval
    # meaningful rather than a rubber stamp.
    Action.REQUIREMENT_CREATE: frozenset({Role.ANALYST}),
    Action.REQUIREMENT_UPDATE: frozenset({Role.ANALYST}),
    Action.REQUIREMENT_TRANSITION: frozenset({Role.ANALYST}),
    Action.REQUIREMENT_SUBMIT: frozenset({Role.ANALYST}),
    Action.REQUIREMENT_WITHDRAW: frozenset({Role.ANALYST}),
    Action.REQUIREMENT_READ: frozenset(
        {
            Role.ANALYST,
            Role.STAKEHOLDER,
            Role.COMPLIANCE_OFFICER,
            Role.SECURITY_REVIEWER,
            Role.PROJECT_MANAGER,
            Role.AUDITOR,
        }
    ),
    Action.APPROVAL_TASK_READ: frozenset(
        {
            Role.ANALYST,
            Role.COMPLIANCE_OFFICER,
            Role.SECURITY_REVIEWER,
            Role.PROJECT_MANAGER,
            Role.AUDITOR,
        }
    ),
    # Committing a baseline is authorised by passing G1, and the actor who
    # completes G1 may be either of its required roles - so both can write the
    # baseline that the gate has just authorised, and nobody else can.
    Action.BASELINE_CREATE: frozenset({Role.ANALYST, Role.COMPLIANCE_OFFICER}),
    Action.BASELINE_READ: frozenset(
        {
            Role.ANALYST,
            Role.STAKEHOLDER,
            Role.COMPLIANCE_OFFICER,
            Role.SECURITY_REVIEWER,
            Role.PROJECT_MANAGER,
            Role.AUDITOR,
        }
    ),
    # --- knowledge base and retrieval ----------------------------------
    # The curated corpus is shared, so reading and curating it is not scoped to
    # a project: it belongs to the Knowledge-Base Administrator (Phase 0 F.1:
    # "add, version, retire knowledge items"). Everyone else reaches knowledge
    # only through a project, and only through that project's allowlist.
    Action.KB_READ: frozenset({Role.KB_ADMIN}),
    Action.KB_ADMINISTER: frozenset({Role.KB_ADMIN}),
    # A project's grounding scope - allowlist, jurisdictions, KB pin - is set by
    # the KB administrator *of that project*. The analyst who runs an analysis
    # cannot widen the sources that ground it.
    Action.KB_SCOPE_MANAGE: frozenset({Role.KB_ADMIN}),
    Action.KB_SCOPE_READ: frozenset(
        {
            Role.ANALYST,
            Role.COMPLIANCE_OFFICER,
            Role.SECURITY_REVIEWER,
            Role.PROJECT_MANAGER,
            Role.AUDITOR,
            Role.KB_ADMIN,
        }
    ),
    # The roles whose work is grounded in normative evidence (architecture E.1:
    # compliance and security retrieve; the analyst drives the analysis).
    Action.KB_RETRIEVE: frozenset({Role.ANALYST, Role.COMPLIANCE_OFFICER, Role.SECURITY_REVIEWER}),
    Action.EVIDENCE_CREATE: frozenset(
        {Role.ANALYST, Role.COMPLIANCE_OFFICER, Role.SECURITY_REVIEWER}
    ),
    Action.EVIDENCE_READ: frozenset(
        {
            Role.ANALYST,
            Role.COMPLIANCE_OFFICER,
            Role.SECURITY_REVIEWER,
            Role.PROJECT_MANAGER,
            Role.AUDITOR,
        }
    ),
}

#: Actions an actor may perform without belonging to a project. For these the
#: actor's roles across all projects are considered together.
_UNSCOPED_ACTIONS: frozenset[Action] = frozenset(
    {Action.PROJECT_CREATE, Action.KB_READ, Action.KB_ADMINISTER}
)

#: The Auditor is read-only by construction (approved Phase 0 F.1). Listing the
#: permitted actions positively means a new mutating action is refused for
#: Auditors by default rather than having to be remembered.
_AUDITOR_READ_ONLY_ACTIONS: frozenset[Action] = frozenset(
    {
        Action.PROJECT_READ,
        Action.AUDIT_READ,
        Action.AUDIT_VERIFY,
        Action.RUN_READ,
        Action.REQUIREMENT_READ,
        Action.APPROVAL_TASK_READ,
        Action.BASELINE_READ,
        Action.KB_SCOPE_READ,
        Action.EVIDENCE_READ,
    }
)


#: Actions no non-human actor may perform, whatever roles it holds: curating the
#: shared corpus and changing a project's grounding scope (architecture E.1).
_HUMAN_ONLY_ACTIONS: frozenset[Action] = frozenset({Action.KB_ADMINISTER, Action.KB_SCOPE_MANAGE})


def can(actor: Actor, action: Action, resource: ResourceRef) -> Decision:
    """Return whether ``actor`` may perform ``action`` on ``resource``.

    The single authorization entry point. Pure, deterministic, no I/O, so it is
    exhaustively testable and callable from both the API layer and the
    repository layer (the architecture requires both - a missed endpoint
    decorator must not be able to leak data).
    """
    # Agent roles can never decide a gate. Checked first so that no later branch
    # can grant it (architecture J.1, M.2).
    if action is Action.APPROVAL_DECIDE and actor.kind is not ActorKind.HUMAN:
        return Decision(False, f"{actor.kind} actors can never decide an approval gate")

    # Curating the knowledge base and setting what a project may be grounded in
    # are human decisions: no agent role writes anything but proposals
    # (architecture E.1). Checked before any grant, like the gate rule above.
    if action in _HUMAN_ONLY_ACTIONS and actor.kind is not ActorKind.HUMAN:
        return Decision(
            False, f"{actor.kind} actors cannot perform {action}; it is a human decision"
        )

    if not isinstance(action, Action):  # pragma: no cover - defensive
        return Decision(False, "unknown action")

    # Gate decisions are authorised per gate against GATE_REQUIRED_ROLES, not by
    # a blanket action grant. Evaluated before the superuser shortcut so that no
    # flag can stand in for holding the gate's role.
    if action is Action.APPROVAL_DECIDE:
        return _can_decide_gate(actor, resource)

    if actor.is_superuser:
        return Decision(True, "superuser")

    grants = _ACTION_GRANTS.get(action)
    if grants is None:
        return Decision(False, f"action {action} has no grant defined; denied by default")

    # Project isolation is evaluated before permission.
    if action not in _UNSCOPED_ACTIONS:
        if resource.project_id is None:
            return Decision(False, f"action {action} requires a project-scoped resource")
        actor_roles = actor.roles_in(resource.project_id)
        if not actor_roles:
            return Decision(
                False, "actor holds no role in the resource's project (project isolation)"
            )
    else:
        actor_roles = (
            frozenset().union(*actor.roles_by_project.values())
            if (actor.roles_by_project)
            else frozenset()
        )
        if not actor_roles:
            return Decision(False, "actor holds no roles")

    if Role.AUDITOR in actor_roles and action not in _AUDITOR_READ_ONLY_ACTIONS:
        non_auditor = actor_roles - {Role.AUDITOR}
        if not non_auditor:
            return Decision(False, "auditor role is read-only")

    permitted = actor_roles & grants
    if not permitted:
        return Decision(
            False,
            f"none of the actor's roles {sorted(actor_roles)} may perform {action}",
        )

    return Decision(True, f"granted via role(s) {sorted(permitted)}")


def _can_decide_gate(actor: Actor, resource: ResourceRef) -> Decision:
    """Authorise a human decision at one gate, in one declared role.

    Reached only from :func:`can`, after the non-human refusal. Project isolation
    is evaluated before the gate's roles, as for every other scoped action.
    """
    gate, role = resource.gate, resource.role_exercised
    if gate is None or role is None:
        return Decision(
            False,
            "approval.decide is authorised per-gate, not by action grant; "
            "a gate and the role exercised are required",
        )
    if resource.project_id is None:
        return Decision(
            False, f"action {Action.APPROVAL_DECIDE} requires a project-scoped resource"
        )

    held = actor.roles_in(resource.project_id)
    if not held:
        return Decision(False, "actor holds no role in the resource's project (project isolation)")

    gate_roles = GATE_REQUIRED_ROLES.get(gate, frozenset())
    if role not in gate_roles:
        return Decision(False, f"{role} may not decide {gate}; it requires {sorted(gate_roles)}")
    if role not in held:
        return Decision(False, f"actor does not hold {role} in this project (holds {sorted(held)})")

    return Decision(True, f"{role} may decide {gate}")


def require(actor: Actor, action: Action, resource: ResourceRef) -> None:
    """Raise unless ``actor`` may perform ``action`` on ``resource``.

    The enforcing form of :func:`can`, for call sites that should abort.
    Distinguishes isolation failures from ordinary denials so that callers - and
    the audit log - can tell a cross-project attempt apart from a missing role.
    """
    decision = can(actor, action, resource)
    if decision.allowed:
        return
    if "project isolation" in decision.reason:
        raise ProjectIsolationError(decision.reason)
    raise AuthorizationError(decision.reason)
