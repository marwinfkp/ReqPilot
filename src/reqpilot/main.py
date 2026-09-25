"""Composition root: the API plus the demonstration UI.

This is the uvicorn entry point::

    uvicorn reqpilot.main:app --reload

It sits above both layers so that neither has to import the other. The API
knows nothing about the UI, which is what keeps the dependency direction
``web → api → services → repositories → domain`` one-way and enforceable.
"""

from __future__ import annotations

from fastapi import FastAPI

from reqpilot.api.app import create_app as create_api_app
from reqpilot.web.compliance import router as compliance_web_router
from reqpilot.web.elicitation import router as elicitation_web_router
from reqpilot.web.extraction import router as extraction_web_router
from reqpilot.web.knowledge import router as knowledge_web_router
from reqpilot.web.quality import router as quality_web_router
from reqpilot.web.risk import router as risk_web_router
from reqpilot.web.router import router as web_router
from reqpilot.web.traceability import router as traceability_web_router


def create_app() -> FastAPI:
    """Build the full application: API endpoints plus the demonstration UI."""
    app = create_api_app()
    app.include_router(web_router)
    app.include_router(knowledge_web_router)
    app.include_router(extraction_web_router)
    app.include_router(elicitation_web_router)
    app.include_router(quality_web_router)
    app.include_router(compliance_web_router)
    app.include_router(risk_web_router)
    app.include_router(traceability_web_router)
    return app


app = create_app()
