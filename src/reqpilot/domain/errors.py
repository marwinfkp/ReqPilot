"""Domain error types.

Deliberately small. The distinction that matters here is between an
authorization failure - a security event, audited as such - and a rule
violation, which is a data-quality event. The architecture treats them very
differently, so they are different exception types.
"""

from __future__ import annotations


class ReqPilotError(Exception):
    """Base class for all ReqPilot domain errors."""


class ConfigurationError(ReqPilotError):
    """Configuration is missing or malformed. Raised at startup, never later."""


class AuthorizationError(ReqPilotError):
    """An actor attempted an action the policy refuses.

    Treated as a security event: audited as ``PERMISSION_DENIED`` and never
    silently downgraded to an empty result.
    """


class ProjectIsolationError(AuthorizationError):
    """An attempt to reach data belonging to another project.

    A subclass of :class:`AuthorizationError` so that any handler catching
    authorization failures also catches isolation failures.
    """


class ImmutableRecordError(ReqPilotError):
    """An attempt to modify an append-only record (audit events, versions)."""


class RuleConfigurationError(ReqPilotError):
    """A versioned rule/config data file is missing, malformed, or unversioned."""


class StateTransitionError(ReqPilotError):
    """An attempt to make a transition the state machine does not permit."""


class RequirementIdError(ReqPilotError):
    """A human requirement identifier does not match the approved convention."""


class ImmutableVersionError(ImmutableRecordError):
    """An attempt to modify a requirement version.

    Versions are immutable once created: content changes create a successor,
    and state changes happen only through validated lifecycle transitions.
    """


class ApprovalError(ReqPilotError):
    """An approval was attempted that the governance rules refuse.

    Covers the wrong role, a decided task, a stale version binding, and
    self-approval. Distinct from :class:`AuthorizationError` because a gate
    refusal is about *this decision*, not about the actor's standing generally.
    """


class StaleApprovalError(ApprovalError):
    """The approved subject changed after the task was raised.

    The exact-version binding did not match, so the decision cannot be applied.
    A new approval task is required for the new version.
    """


class SelfApprovalError(ApprovalError):
    """An actor attempted to approve something they authored."""


class BaselineInvariantError(ReqPilotError):
    """A baseline operation would have violated a baseline invariant.

    The invariant that matters most: no unapproved requirement version may
    enter a baseline (architecture H.4).
    """
