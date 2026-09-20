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
