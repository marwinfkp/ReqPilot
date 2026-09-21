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
    AuthorizationError,
    BaselineInvariantError,
    ImmutableRecordError,
    ProjectIsolationError,
    ReqPilotError,
    RequirementIdError,
    StateTransitionError,
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
