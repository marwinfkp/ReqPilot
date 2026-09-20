"""FastAPI application factory (architecture ADR-002, section S).

P0 exposes health endpoints only. Domain endpoints arrive with the roadmap
phases that own them - adding them now, ahead of the domain they operate on,
would be speculative.

The layering rule this module sits at the top of:
``api -> services -> repositories -> domain``, one way only, enforced in CI.
"""

from __future__ import annotations

from fastapi import FastAPI

from reqpilot import __version__
from reqpilot.api.routes import health


def create_app() -> FastAPI:
    """Build the application.

    A factory rather than a module-level singleton so tests can construct an
    app with overridden settings without mutating global state.
    """
    app = FastAPI(
        title="ReqPilot",
        version=__version__,
        description=(
            "Agentic requirements engineering and SDLC recommendation assistant. "
            "Roadmap phase P0 (Foundations): health endpoints only."
        ),
    )
    app.include_router(health.router)
    return app


app = create_app()
