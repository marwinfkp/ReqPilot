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
from reqpilot.api.routes import extraction, governance, health, knowledge, requirements

DESCRIPTION = (
    "Agentic requirements engineering and SDLC recommendation assistant. "
    "Roadmap phases P1 (requirements repository: deterministic system of record, "
    "immutable versions, lifecycle, G1 approval, baselines) and P2 (knowledge base "
    "and retrieval: typed curated corpus, allowlisted hybrid retrieval, evidence, "
    "citations) and P3 (batch extraction and classification: every model call through "
    "one gateway, typed proposals, deterministic validation, a review queue that is "
    "not approval). Model calls go to the configured provider: the offline stub by default, "
    "or OpenAI when LLM_PROVIDER=openai."
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
    app.include_router(knowledge.router)
    app.include_router(extraction.router)
    return app


app = create_app()
