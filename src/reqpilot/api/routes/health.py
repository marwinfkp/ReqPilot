"""Health endpoints (P0).

Two endpoints with deliberately different contracts:

* ``/health`` - liveness. Never touches the database, so it stays meaningful
  when the database is down.
* ``/health/db`` - readiness. Probes the database and reports pgvector
  availability. Returns 503 when unhealthy rather than raising, because a
  health check that raises cannot report unhealthiness.

Neither endpoint leaks configuration: the database URL contains a password and
is redacted everywhere.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status

from reqpilot import __version__
from reqpilot.config import get_settings
from reqpilot.repositories.database import check_database_health

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, Any]:
    """Liveness check. No I/O."""
    settings = get_settings()
    return {
        "status": "ok",
        "version": __version__,
        "environment": str(settings.app_env),
        "roadmap_phase": "P0 Foundations",
        # Reported so an operator can confirm no provider is configured without
        # inspecting the environment. Never includes the key itself.
        "llm_provider": str(settings.llm_provider),
    }


@router.get("/health/db")
def health_db(response: Response) -> dict[str, Any]:
    """Readiness check. Probes the database; reports rather than raises."""
    report = check_database_health()
    if not report["connected"]:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ok" if report["connected"] else "unavailable",
        "connected": report["connected"],
        "pgvector": report["pgvector"],
        "error": report["error"],
    }
