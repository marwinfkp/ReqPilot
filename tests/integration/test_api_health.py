"""API health endpoint tests (architecture section S).

P0 exposes health endpoints only. Two properties are worth pinning: liveness
does not depend on the database, and no endpoint leaks configuration.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from reqpilot.api.app import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_liveness_needs_no_database(client: TestClient) -> None:
    """Liveness must stay meaningful when the database is down."""
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["roadmap_phase"] == "P0 Foundations"


def test_liveness_reports_the_provider_without_the_key(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["llm_provider"] == "stub"
    assert "api_key" not in body
    assert "llm_api_key" not in body


def test_readiness_reports_rather_than_raises(client: TestClient) -> None:
    """With no database running, readiness must return 503, not crash."""
    response = client.get("/health/db")
    assert response.status_code in (200, 503)
    body = response.json()
    assert "connected" in body
    assert "pgvector" in body


def test_readiness_does_not_leak_the_connection_string(client: TestClient) -> None:
    """The database URL carries a password and must never appear in a response."""
    body = client.get("/health/db").json()
    blob = str(body)
    assert "postgresql+psycopg://" not in blob
    assert "password" not in blob.lower()


def collect_paths(app) -> set[str]:
    """Return every HTTP path the application exposes.

    Read from the generated OpenAPI schema rather than by walking ``app.routes``:
    the route objects are internal and their shape varies between FastAPI
    versions, whereas the schema is the stable, public statement of what the
    service exposes - which is exactly what these tests assert about.
    """
    return set(app.openapi().get("paths", {}))


def test_health_endpoints_are_registered() -> None:
    """Guard against the next test passing because it found no routes at all."""
    paths = collect_paths(create_app())
    assert "/health" in paths
    assert "/health/db" in paths


def test_only_current_phase_endpoints_are_exposed() -> None:
    """The API must not run ahead of the roadmap.

    Requirement, approval and baseline endpoints (P1) and knowledge-base,
    retrieval and evidence endpoints (P2) belong to the current phases.
    Everything listed below belongs to a later one and must be absent.
    """
    paths = collect_paths(create_app())

    future_prefixes = (
        "/api/v1/analysis-runs",
        "/api/v1/interviews",
        # "/api/v1/conflicts" arrived with P5 (quality and conflict detection).
        # "/api/v1/compliance-mappings" arrived with P6 (compliance and security).
        # "/api/v1/risks" arrived with P7 (risk analysis and the register).
        "/api/v1/sdlc-runs",
        "/api/v1/artifacts",
        "/api/v1/evaluations",
    )
    premature = [p for p in paths if p.startswith(future_prefixes)]
    assert not premature, f"endpoints from a later roadmap phase appeared: {premature}"


def test_current_phase_endpoints_are_present() -> None:
    """Guard against the previous test passing because nothing is routed."""
    paths = collect_paths(create_app())
    assert any(p.endswith("/requirements") for p in paths)
    assert any("approval-tasks" in p for p in paths)
    assert any(p.endswith("/baselines") for p in paths)
    assert any(p.startswith("/api/v1/kb/") for p in paths)
    assert any(p.endswith("/retrievals") for p in paths)
    assert any(p.endswith("/kb-allowlist") for p in paths)
    # P3: sources, batch analysis runs, the review queue, classification.
    assert any(p.endswith("/sources") for p in paths)
    assert any(p.endswith("/analysis-runs") for p in paths)
    assert any(p.endswith("/review-items") for p in paths)
    # P4: stakeholders, interview sessions, coverage, the clarification loop.
    assert "/api/v1/projects/{project_id}/stakeholders" in paths
    assert "/api/v1/projects/{project_id}/sessions" in paths
    assert "/api/v1/sessions/{session_id}/answer" in paths
    assert "/api/v1/sessions/{session_id}/coverage" in paths
    assert "/api/v1/projects/{project_id}/clarifications" in paths
    assert "/api/v1/clarifications/{clarification_id}/answer" in paths
    assert "/api/v1/clarifications/{clarification_id}/dismiss" in paths
    assert any(p.endswith("/classification") for p in paths)
    # P5: quality runs, findings, conflicts with both sides, the glossary.
    assert "/api/v1/projects/{project_id}/quality-runs" in paths
    assert "/api/v1/projects/{project_id}/quality-findings" in paths
    assert "/api/v1/projects/{project_id}/conflicts" in paths
    assert "/api/v1/conflicts/{conflict_id}/resolve" in paths
    assert "/api/v1/projects/{project_id}/glossary" in paths


def test_p6_and_p7_endpoints_are_present_and_p8_is_not() -> None:
    """P6 exposes compliance mappings, gaps, security/privacy findings and the report,
    and P7 the risk register, the matrix and the human risk actions. Document
    generation and traceability (P8) are still absent, and there is still no
    endpoint that writes a gate decision other than the one approval path."""
    paths = collect_paths(create_app())
    assert "/api/v1/compliance-mappings/{mapping_id}" in paths
    assert "/api/v1/projects/{project_id}/compliance-runs" in paths
    assert "/api/v1/projects/{project_id}/compliance-gaps" in paths
    assert "/api/v1/projects/{project_id}/security-privacy-findings" in paths
    assert "/api/v1/projects/{project_id}/compliance-report" in paths
    # P7: the register, its markdown artefact, the published matrix, and the
    # human actions of FR-RSK-010 / FR-RSK-005.
    assert "/api/v1/projects/{project_id}/risk-runs" in paths
    assert "/api/v1/projects/{project_id}/risks" in paths
    assert "/api/v1/projects/{project_id}/risk-register" in paths
    assert "/api/v1/projects/{project_id}/risk-register.md" in paths
    assert "/api/v1/risk-matrix" in paths
    assert "/api/v1/risks/{risk_id}/decision" in paths
    assert "/api/v1/risk-mitigations/{mitigation_id}/decision" in paths
    # P8 and later stay absent.
    assert not [p for p in paths if p.startswith(("/api/v1/artifacts", "/api/v1/traceability"))]
    # The one decision path is unchanged: G8 is decided there like G1-G3, and
    # no P7 endpoint decides a gate, approves or baselines anything.
    assert [p for p in paths if p.endswith("/decide")] == [
        "/api/v1/approval-tasks/{task_id}/decide"
    ]
