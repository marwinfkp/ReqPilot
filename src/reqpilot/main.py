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
from reqpilot.web.router import router as web_router


def create_app() -> FastAPI:
    """Build the full application: API endpoints plus the demonstration UI."""
    app = create_api_app()
    app.include_router(web_router)
    return app


app = create_app()
