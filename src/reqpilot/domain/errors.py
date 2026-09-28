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


class KnowledgeBaseError(ReqPilotError):
    """A knowledge-base operation was refused (architecture G.5, J.6).

    Covers malformed metadata, a supersession the item's status does not allow,
    and a duplicate of an active item.
    """


class LicenceViolationError(KnowledgeBaseError):
    """Text was offered that the source's licence does not permit storing.

    The ingestion path refuses full text where the licence forbids it
    (architecture G.5; approved Phase 0 D.2 copyright constraint).
    """


class EmbeddingUnavailableError(ReqPilotError):
    """The configured embedding provider cannot run here.

    Raised instead of silently falling back to another model, because vectors
    from different models are not comparable (architecture ADR-004, ADR-005).
    """


class UngroundedRetrievalError(ReqPilotError):
    """An operation needed retrieved evidence and retrieval found none.

    The deterministic form of ``FR-RAG-005``: an empty retrieval is escalated for
    human review, never answered from a model's parametric memory.
    """


class CitationError(ReqPilotError):
    """A citation does not resolve to evidence this run was given (``FR-RAG-003``).

    Deliberately says nothing about whether the evidence exists elsewhere, so a
    forged or cross-project id is indistinguishable from a missing one.
    """


class EvidenceIntegrityError(CitationError):
    """Stored evidence no longer matches the chunk and span it records.

    Evidence is append-only; a mismatch means tampering or corruption, and the
    citation is refused rather than resolved to text nobody actually saw.
    """


# ---------------------------------------------------------------------------
# Extraction, classification and the LLM gateway (roadmap phase P3)
# ---------------------------------------------------------------------------


class SourceDocumentError(ReqPilotError):
    """A project source document was refused (empty, unsupported, malformed)."""


class ExtractionError(ReqPilotError):
    """An extraction operation was refused by a deterministic rule."""


class ClassificationError(ReqPilotError):
    """A classification operation was refused by a deterministic rule.

    For example: an override with no label, a label outside the approved
    taxonomy, or an override of a version that is already under approval.
    """


class ReviewError(ReqPilotError):
    """A review-queue action was refused (wrong resolution, already resolved)."""


class LLMGatewayError(ReqPilotError):
    """The LLM gateway could not produce a usable result (architecture ADR-006).

    Every model call passes through one gateway; its failures are typed so that
    a node can record *why* no proposal exists, rather than inventing one.
    """


class ProviderUnavailableError(LLMGatewayError):
    """The configured provider cannot be used, or failed after bounded retries."""


class TransientProviderError(ProviderUnavailableError):
    """A provider failure that may succeed on retry (429, 5xx, timeout)."""


class StructuredOutputError(LLMGatewayError):
    """The model's output failed schema validation, including after one repair."""


class PromptRegistryError(LLMGatewayError):
    """A prompt template is missing, malformed, or changed without a version bump."""


class FixtureMissingError(LLMGatewayError):
    """Strict replay found no recorded response for a request (ADR-012)."""


class EgressRefusedError(LLMGatewayError):
    """Content was refused at the trust boundary before reaching a provider.

    A security event, not a data-quality event: either an application secret
    appeared in a prompt, or unmasked project content would have left the
    machine without the data being declared synthetic (``FR-ING-003``).
    """


class EvaluationError(ReqPilotError):
    """An evaluation could not be computed as the approved protocol requires."""


class GoldSetIntegrityError(EvaluationError):
    """A gold dataset does not match its frozen manifest (architecture R.3).

    The evaluation refuses to run rather than score against a modified set.
    """


# --- elicitation and clarification (P4) ------------------------------------------


class ElicitationError(ReqPilotError):
    """An interview operation refused in the session's current state."""


class InterviewStalledError(ElicitationError):
    """A session step failed safely; the session is STALLED until an analyst retries."""


class ClarificationError(ReqPilotError):
    """A clarification operation refused in its current state (FR-CLR-001..004)."""


class QualityError(ReqPilotError):
    """A quality-finding, conflict or glossary operation refused in its state (P5)."""


class RiskError(ReqPilotError):
    """A risk-analysis or risk-register operation was refused (roadmap phase P7)."""


class ScopeGuardError(RiskError):
    """Refused by the ``FR-RSK-011`` scope guard (approved Phase 0 D.1).

    ReqPilot analyses project, engineering, security, privacy, compliance and
    operational risk. It does not compute borrower credit risk, customer risk
    ratings, probability of default or fraud scores, and it makes no lending
    decision. A refusal is audited; it is never downgraded to a warning.
    """


class GovernanceBlockedError(ApprovalError):
    """A governed step is refused because something still blocks it (roadmap phase P8).

    Raised when a version is submitted for G1, approved at G1, committed to a
    baseline or rendered into an authoritative artefact while a gate it needs is
    unresolved - an open or stake-holder-unsigned conflict (G4), a pending G2/G3
    interpretation, an architecture-critical requirement without G5, a change to
    an approved requirement without G7, or an unreviewed high-severity risk (G8).
    ``blockers`` carries the deterministic reasons, so the refusal is explainable.
    """

    def __init__(self, message: str, blockers: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.blockers = blockers


class TraceabilityError(ReqPilotError):
    """A trace link outside the closed allowlist, or with an unresolvable end (N.1)."""


class ArtifactError(ReqPilotError):
    """An artefact could not be generated, validated or exported (roadmap phase P8).

    Generation fails closed: an artefact whose sections do not trace to the
    baseline, or that cites a requirement version outside it, is never stored.
    """


class SdlcError(ReqPilotError):
    """An SDLC recommendation refused in the current state (roadmap phase P9).

    Raised when the inputs are not approved (fails closed: no recommendation is
    computed from an unapproved baseline or an ungoverned risk register), when an
    override is malformed, or when a run's lifecycle does not allow the request.
    """


class WorkflowError(ReqPilotError):
    """A project workflow refused in the current state (roadmap phase P10).

    Raised when the SDLC run has not passed G6, when a source it must derive from
    is not governed (a pending G8 or G2/G3), when the generated or edited
    workflow fails deterministic validation (fails closed: nothing is stored),
    or when an edit would remove a mandatory element or its provenance.
    ``findings`` carries the validation codes, so a caller can show them.
    """

    def __init__(self, message: str, findings: tuple[dict[str, str], ...] = ()) -> None:
        super().__init__(message)
        self.findings = findings


# --- guardrails hardening (P11) ---------------------------------------------------


class CapabilityError(AuthorizationError):
    """An agent-role invocation outside its capability token (architecture P.1, D10).

    Raised for a missing, forged, expired, widened or mismatched token, and for
    a read, write, retrieval or model call the token does not grant. A subclass
    of :class:`AuthorizationError`, so every handler that audits authorization
    failures as ``PERMISSION_DENIED`` audits these too.
    """


class CapabilityEgressError(CapabilityError, EgressRefusedError):
    """A model call refused at the gateway for want of a matching capability.

    Also an :class:`EgressRefusedError`: nothing reached a provider, and the
    nodes that already record a refused egress as a failed agent run and a
    ``PERMISSION_DENIED`` event record this the same way.
    """


class ProjectDeletedError(ReqPilotError):
    """The project was deleted (``FR-ADM-006``; architecture P.2).

    Its content is gone; only its redacted audit trail remains readable. Any
    other action on it is refused.
    """


class DeletionError(ReqPilotError):
    """A project deletion could not be completed and nothing was deleted."""


class AuthSessionError(ReqPilotError):
    """A server-side session is unknown, expired, revoked or mismatched (ADR-009)."""
