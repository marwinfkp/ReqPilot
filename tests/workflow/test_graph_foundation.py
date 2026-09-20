"""LangGraph foundation tests (ADR-001, architecture C).

Proves the four mechanisms later phases depend on: compilation with a
checkpointer, deterministic routing, interrupt-based suspension, and resume
from checkpoint with state intact.

The interrupt test is the important one. Human-in-the-loop gates are built on
interrupt/resume, so proving the mechanism here means later phases inherit a
known-good foundation instead of debugging it under pressure.

Runs entirely offline: in-memory checkpointer, no database, no model.
"""

from __future__ import annotations

import pytest
from langgraph.types import Command

from reqpilot.config import Settings
from reqpilot.domain.ids import new_graph_run_id, thread_id_for
from reqpilot.graph.builder import build_checkpointer, run_config
from reqpilot.graph.graphs.smoke import build_smoke_graph, route_after_start
from reqpilot.graph.routers import (
    assert_is_deterministic_router,
    route_on_attempts,
    route_on_error,
)

pytestmark = pytest.mark.workflow


@pytest.fixture
def memory_settings() -> Settings:
    return Settings(_env_file=None, LANGGRAPH_CHECKPOINT_BACKEND="memory")


# --- construction --------------------------------------------------------


def test_graph_compiles_without_a_checkpointer() -> None:
    assert build_smoke_graph() is not None


def test_graph_compiles_with_a_checkpointer(memory_settings: Settings) -> None:
    graph = build_smoke_graph(build_checkpointer(memory_settings))
    assert graph is not None


def test_unknown_checkpoint_backend_is_rejected() -> None:
    settings = Settings.model_construct(checkpoint_backend="redis")
    with pytest.raises(ValueError, match="unknown checkpoint backend"):
        build_checkpointer(settings)


# --- thread id convention ------------------------------------------------


def test_run_config_carries_the_thread_id() -> None:
    run_id = new_graph_run_id()
    config = run_config(run_id)
    assert config["configurable"]["thread_id"] == thread_id_for(run_id)


def test_distinct_runs_get_distinct_threads() -> None:
    a, b = new_graph_run_id(), new_graph_run_id()
    assert run_config(a)["configurable"]["thread_id"] != run_config(b)["configurable"]["thread_id"]


# --- deterministic routing -----------------------------------------------


def test_router_returns_a_closed_literal() -> None:
    assert route_after_start({"needs_approval": True}) == "gated"
    assert route_after_start({"needs_approval": False}) == "finish"
    assert route_after_start({}) == "finish"


def test_router_is_pure() -> None:
    """Same input, same output, and the input is not mutated."""
    state = {"needs_approval": True}
    first = route_after_start(state)
    second = route_after_start(state)
    assert first == second == "gated"
    assert state == {"needs_approval": True}


def test_error_router_fails_closed() -> None:
    assert route_on_error({"errors": []}) == "continue"
    assert route_on_error({}) == "continue"
    assert (
        route_on_error({"errors": [{"node": "n", "message": "boom", "attempt": 1}]})
        == "error_handler"
    )


def test_attempt_router_stops_rather_than_looping() -> None:
    assert route_on_attempts({"attempt": 0}, max_attempts=3) == "continue"
    assert route_on_attempts({"attempt": 3}, max_attempts=3) == "error_handler"


def test_routers_have_no_access_to_model_output() -> None:
    """Structural guard: a router must not close over a gateway or provider."""
    for router in (route_after_start, route_on_error, route_on_attempts):
        assert_is_deterministic_router(router)


def test_the_guard_catches_a_router_that_closes_over_a_gateway() -> None:
    from reqpilot.llm import StubLLMGateway

    gateway = StubLLMGateway(Settings(_env_file=None))

    def bad_router(state: dict) -> str:
        return "finish" if gateway else "gated"

    with pytest.raises(ValueError, match="routers must not have access"):
        assert_is_deterministic_router(bad_router)


# --- execution -----------------------------------------------------------


def test_ungated_run_completes(memory_settings: Settings) -> None:
    graph = build_smoke_graph(build_checkpointer(memory_settings))
    run_id = new_graph_run_id()
    result = graph.invoke(
        {"run_id": str(run_id), "project_id": "p", "needs_approval": False},
        config=run_config(run_id),
    )
    assert result["steps"] == ["start", "finish"]


def test_interrupt_suspends_the_run(memory_settings: Settings) -> None:
    """The gate mechanism: execution stops and waits for a human."""
    graph = build_smoke_graph(build_checkpointer(memory_settings))
    run_id = new_graph_run_id()
    config = run_config(run_id)

    result = graph.invoke(
        {"run_id": str(run_id), "project_id": "p", "needs_approval": True},
        config=config,
    )

    assert "__interrupt__" in result, "a gated run must suspend, not run to completion"
    assert "finish" not in result.get("steps", []), "no node past the gate may execute"


def test_resume_continues_from_the_checkpoint(memory_settings: Settings) -> None:
    graph = build_smoke_graph(build_checkpointer(memory_settings))
    run_id = new_graph_run_id()
    config = run_config(run_id)

    graph.invoke(
        {"run_id": str(run_id), "project_id": "p", "needs_approval": True},
        config=config,
    )
    resumed = graph.invoke(Command(resume=True), config=config)

    assert resumed["steps"] == ["start", "gated", "finish"]
    assert resumed["approved"] is True
    # State written before the interrupt survived the suspension.
    assert resumed["run_id"] == str(run_id)


def test_a_suspended_run_stays_suspended_until_resumed(memory_settings: Settings) -> None:
    """Re-invoking without a resume command must not skip the gate."""
    graph = build_smoke_graph(build_checkpointer(memory_settings))
    run_id = new_graph_run_id()
    config = run_config(run_id)

    graph.invoke({"run_id": str(run_id), "project_id": "p", "needs_approval": True}, config=config)
    state = graph.get_state(config)

    assert state.next, "the run should still have pending work"
    assert "finish" not in (state.values.get("steps") or [])


def test_runs_on_different_threads_are_isolated(memory_settings: Settings) -> None:
    """Two runs must not see each other's state."""
    graph = build_smoke_graph(build_checkpointer(memory_settings))
    a, b = new_graph_run_id(), new_graph_run_id()

    graph.invoke(
        {"run_id": str(a), "project_id": "p1", "needs_approval": False}, config=run_config(a)
    )
    graph.invoke(
        {"run_id": str(b), "project_id": "p2", "needs_approval": False}, config=run_config(b)
    )

    assert graph.get_state(run_config(a)).values["project_id"] == "p1"
    assert graph.get_state(run_config(b)).values["project_id"] == "p2"
