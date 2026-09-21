"""The API application (architecture ADR-002, section S).

This factory builds the **API only**. The demonstration UI is assembled on top
of it by :mod:`reqpilot.main`, which is the composition root.

Keeping the two apart is not tidiness for its own sake: the layered dependency
rule runs ``web → api → services → repositories → domain``, one way. If this
module imported the web package the direction would reverse and the contract
would - correctly - fail.
"""

from __future__ import annotations

from fastapi import FastAPI

from reqpilot import __version__
from reqpilot.api.errors import install_error_handlers
from reqpilot.api.routes import governance, health, requirements

DESCRIPTION = (
    "Agentic requirements engineering and SDLC recommendation assistant. "
    "Roadmap phase P1 (Requirements repository): deterministic system of record, "
    "immutable versions, lifecycle, G1 approval and baselines. No AI."
)


def create_app() -> FastAPI:
    """Build the API application.

    A factory rather than a module-level singleton so tests can construct an app
    with overridden dependencies without mutating global state.
    """
    app = FastAPI(title="ReqPilot", version=__version__, description=DESCRIPTION)
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(requirements.router)
    app.include_router(governance.router)
    return app


app = create_app()
