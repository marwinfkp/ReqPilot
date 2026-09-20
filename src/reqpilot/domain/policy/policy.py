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
"""

from __future__ import annotations

from dataclasses import dataclass, field

from reqpilot.domain.enums import Action, ActorKind, ResourceType, Role
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
    """

    resource_type: ResourceType
    project_id: ProjectId | None = None
    resource_id: str | None = None


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
#: the gate's own required roles (``GATE_REQUIRED_ROLES``) by the approval
#: service in a later roadmap phase, not by a blanket action grant.
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
}

#: Actions an actor may perform without belonging to a project.
_UNSCOPED_ACTIONS: frozenset[Action] = frozenset({Action.PROJECT_CREATE})

#: The Auditor is read-only by construction (approved Phase 0 F.1). Listing the
#: permitted actions positively means a new mutating action is refused for
#: Auditors by default rather than having to be remembered.
_AUDITOR_READ_ONLY_ACTIONS: frozenset[Action] = frozenset(
    {Action.PROJECT_READ, Action.AUDIT_READ, Action.AUDIT_VERIFY, Action.RUN_READ}
)


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

    if not isinstance(action, Action):  # pragma: no cover - defensive
        return Decision(False, "unknown action")

    if actor.is_superuser:
        return Decision(True, "superuser")

    # Gate decisions are authorised per-gate by the approval service against
    # GATE_REQUIRED_ROLES, not by a blanket action grant.
    if action is Action.APPROVAL_DECIDE:
        return Decision(
            False,
            "approval.decide is authorised per-gate by the approval service, not by action grant",
        )

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
