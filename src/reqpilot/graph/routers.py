"""Conditional-edge routers (architecture C.3).

**The invariant this module exists to protect: LLM output never controls graph
topology.**

Every router is an ordinary deterministic Python function that

* takes typed graph state,
* returns a node name drawn from a closed ``Literal`` type,
* performs no LLM call and no write,

so routing is exhaustively unit-testable without a model, a database, or a
running graph.

A router that branched on free text from a model response would hand topology
control to the model. If you ever need that, the correct move is to have a
deterministic node convert the model's *typed* output into a state flag, and
route on the flag.
"""

from __future__ import annotations

from typing import Literal, Protocol, TypeVar

from reqpilot.graph.state import AnalysisState, BaseGraphState

StateT = TypeVar("StateT", bound=BaseGraphState)


class Router(Protocol):
    """The shape every router must have."""

    def __call__(self, state: dict) -> str:  # pragma: no cover - structural
        ...


ContinueOrFail = Literal["continue", "error_handler"]


def route_on_error(state: BaseGraphState) -> ContinueOrFail:
    """Route to the error handler when the run has accumulated failures.

    The canonical example of the convention: reads state, returns a literal,
    touches nothing else.
    """
    errors = state.get("errors") or []
    return "error_handler" if errors else "continue"


def route_on_attempts(state: BaseGraphState, max_attempts: int) -> ContinueOrFail:
    """Fail closed once a node has been attempted too many times.

    Failing loudly and stopping is always preferred to advancing with partial
    state (architecture T).
    """
    return "error_handler" if state.get("attempt", 0) >= max_attempts else "continue"


def assert_is_deterministic_router(func: object) -> None:
    """Raise if a router closes over anything LLM-related.

    A cheap structural guard used by tests. It cannot prove determinism, but it
    does catch the specific mistake the architecture forbids: a routing function
    that has a gateway or provider client in scope.
    """
    closure = getattr(func, "__closure__", None) or ()
    for cell in closure:
        try:
            value = cell.cell_contents
        except ValueError:  # pragma: no cover - empty cell
            continue
        module = type(value).__module__ or ""
        if "llm" in module or "langchain" in module or "anthropic" in module:
            raise ValueError(
                f"router {getattr(func, '__name__', func)!r} closes over "
                f"{type(value).__name__} from {module}; routers must not have "
                "access to model output or providers (architecture C.3)"
            )


# ---------------------------------------------------------------------------
# analysis_graph - the P3 subset (architecture C.3)
# ---------------------------------------------------------------------------

# Routers are annotated with the full state type: LangGraph reads the
# annotation as the router's input schema, and a narrower type would hide the
# fields the route depends on.
AfterScope = Literal["extract_requirements", "classify", "error_handler"]
AfterExtraction = Literal["validate_extraction", "error_handler"]
AfterValidation = Literal["persist_candidates", "error_handler"]


def route_after_scope(state: AnalysisState) -> AfterScope:
    """Sources in scope -> extract; versions only -> classify; a bad scope -> fail."""
    if state.get("errors"):
        return "error_handler"
    return "extract_requirements" if state.get("scope_source_ids") else "classify"


def route_extraction(state: AnalysisState) -> AfterExtraction:
    """C.3 ``route_extraction``. The gateway already made the one schema repair;
    a failure that survived it goes to the error handler, never onward."""
    return "error_handler" if state.get("errors") else "validate_extraction"


def route_validation(state: AnalysisState) -> AfterValidation:
    return "error_handler" if state.get("errors") else "persist_candidates"
