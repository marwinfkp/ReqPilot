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

from reqpilot.graph.state import AnalysisState, BaseGraphState, ElicitationState

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
AfterValidation = Literal["persist_candidates", "persist_revision", "error_handler"]


def route_after_scope(state: AnalysisState) -> AfterScope:
    """Sources, interview sessions (P4) or an answered clarification (P4) in scope ->
    extract; versions only -> classify; a bad scope -> fail."""
    if state.get("errors"):
        return "error_handler"
    if (
        state.get("scope_source_ids")
        or state.get("scope_session_ids")
        or state.get("clarification_id")
    ):
        return "extract_requirements"
    return "classify"


def route_extraction(state: AnalysisState) -> AfterExtraction:
    """C.3 ``route_extraction``. The gateway already made the one schema repair;
    a failure that survived it goes to the error handler, never onward."""
    return "error_handler" if state.get("errors") else "validate_extraction"


def route_validation(state: AnalysisState) -> AfterValidation:
    """A clarification re-analysis revises its one requirement (P4); a batch creates."""
    if state.get("errors"):
        return "error_handler"
    return "persist_revision" if state.get("clarification_id") else "persist_candidates"


# ---------------------------------------------------------------------------
# elicitation_graph (architecture C.4, P4)
# ---------------------------------------------------------------------------

AfterLoad = Literal[
    "select_next_topic",
    "generate_question",
    "await_answer",
    "assess_answer",
    "end_interview",
    "stall",
]
AfterTopic = Literal["generate_question", "end_interview", "stall"]
AfterQuestion = Literal["await_answer", "stall"]
AfterAwait = Literal["record_utterance", "stall"]
AfterRecord = Literal["assess_answer", "stall"]
AfterAssessment = Literal["generate_question", "select_next_topic", "stall"]

_RESUME_POINTS: frozenset[str] = frozenset(
    {"select_next_topic", "generate_question", "await_answer", "assess_answer", "end_interview"}
)


def route_after_load(state: ElicitationState) -> AfterLoad:
    """Continue from where the durable session is, never from where a client says."""
    if state.get("failure"):
        return "stall"
    point = state.get("resume_point", "")
    if point not in _RESUME_POINTS:
        return "stall"
    return point  # type: ignore[return-value]


def route_after_topic(state: ElicitationState) -> AfterTopic:
    """C.4: coverage complete -> end; otherwise ask about the selected topic."""
    if state.get("failure"):
        return "stall"
    return "end_interview" if state.get("complete") else "generate_question"


def route_after_question(state: ElicitationState) -> AfterQuestion:
    return "stall" if state.get("failure") else "await_answer"


def route_after_await(state: ElicitationState) -> AfterAwait:
    return "stall" if state.get("failure") else "record_utterance"


def route_after_record(state: ElicitationState) -> AfterRecord:
    return "stall" if state.get("failure") else "assess_answer"


def route_after_assessment(state: ElicitationState) -> AfterAssessment:
    """C.4's router. Reads the flag the deterministic tracker set - never model text.

    ``awaiting_followup`` is true only when the tracker found the answer vague,
    incomplete or inconsistent *and* ``followups_this_topic < max_followups``.
    """
    if state.get("failure"):
        return "stall"
    return "generate_question" if state.get("awaiting_followup") else "select_next_topic"
