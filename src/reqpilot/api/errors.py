"""Mapping domain errors onto HTTP status codes.

One deliberate choice worth stating: :class:`ProjectIsolationError` becomes
**404, not 403**. Answering "forbidden" would confirm that the resource exists,
which is precisely the cross-project existence disclosure the architecture
forbids. A caller outside the project gets the same answer they would get for an
id that does not exist at all.

An ordinary authorization failure - a member of the project holding the wrong
role - is a genuine 403: the resource's existence is not a secret from them.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from reqpilot.domain.errors import (
    ApprovalError,
    ArtifactError,
    AuthorizationError,
    BaselineInvariantError,
    CitationError,
    ClarificationError,
    ClassificationError,
    EgressRefusedError,
    ElicitationError,
    EmbeddingUnavailableError,
    EvidenceIntegrityError,
    ImmutableRecordError,
    LicenceViolationError,
    ProjectIsolationError,
    PromptRegistryError,
    QualityError,
    ReqPilotError,
    RequirementIdError,
    ReviewError,
    RuleConfigurationError,
    StateTransitionError,
    TraceabilityError,
    UngroundedRetrievalError,
)

#: Checked in order, so the most specific type wins.
ERROR_STATUS: tuple[tuple[type[Exception], int], ...] = (
    # Isolation first: it is a subclass of AuthorizationError and must not be
    # answered with 403.
    (ProjectIsolationError, status.HTTP_404_NOT_FOUND),
    (AuthorizationError, status.HTTP_403_FORBIDDEN),
    # Governance refusals: the request was understood and is not permitted in
    # the current state. 409 rather than 400 - nothing about the payload is wrong.
    (ApprovalError, status.HTTP_409_CONFLICT),
    (StateTransitionError, status.HTTP_409_CONFLICT),
    (BaselineInvariantError, status.HTTP_409_CONFLICT),
    (ImmutableRecordError, status.HTTP_409_CONFLICT),
    (LicenceViolationError, status.HTTP_409_CONFLICT),
    (UngroundedRetrievalError, status.HTTP_409_CONFLICT),
    # Tampered or corrupted evidence conflicts with what was recorded. Checked
    # before the general citation case, of which it is a subclass.
    (EvidenceIntegrityError, status.HTTP_409_CONFLICT),
    (CitationError, status.HTTP_404_NOT_FOUND),
    # AI-proposal decisions refused in the current state (P3).
    (ClassificationError, status.HTTP_409_CONFLICT),
    (ReviewError, status.HTTP_409_CONFLICT),
    # Elicitation and clarification refusals in the current state (P4).
    (ElicitationError, status.HTTP_409_CONFLICT),
    (ClarificationError, status.HTTP_409_CONFLICT),
    (QualityError, status.HTTP_409_CONFLICT),
    # P8: an artefact refused in the current governance state, or a trace link
    # outside the closed allowlist. Neither is a malformed request.
    (ArtifactError, status.HTTP_409_CONFLICT),
    (TraceabilityError, status.HTTP_409_CONFLICT),
    # A trust-boundary refusal is not the caller's input error.
    (EgressRefusedError, status.HTTP_409_CONFLICT),
    (PromptRegistryError, status.HTTP_500_INTERNAL_SERVER_ERROR),
    # The server cannot do the work here and now; the request is not at fault.
    (EmbeddingUnavailableError, status.HTTP_503_SERVICE_UNAVAILABLE),
    (RuleConfigurationError, status.HTTP_500_INTERNAL_SERVER_ERROR),
    # Input problems.
    (RequirementIdError, status.HTTP_400_BAD_REQUEST),
    (ReqPilotError, status.HTTP_400_BAD_REQUEST),
)


def status_for(exc: Exception) -> int:
    for error_type, code in ERROR_STATUS:
        if isinstance(exc, error_type):
            return code
    return status.HTTP_500_INTERNAL_SERVER_ERROR  # pragma: no cover - defensive


def install_error_handlers(app: FastAPI) -> None:
    """Register one handler covering every domain error."""

    @app.exception_handler(ReqPilotError)
    async def _handle(request: Request, exc: ReqPilotError) -> JSONResponse:
        code = status_for(exc)
        detail = str(exc)
        if code == status.HTTP_404_NOT_FOUND:
            # Do not echo the isolation reason back: it would disclose that the
            # refusal was about membership rather than existence.
            detail = "not found"
        return JSONResponse(status_code=code, content={"detail": detail})
