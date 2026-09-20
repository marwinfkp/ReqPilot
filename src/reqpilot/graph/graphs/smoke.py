"""A minimal graph proving the orchestration foundation works (P0 only).

This is **not** one of the four real graphs. It exists to demonstrate, in CI and
offline, that four mechanisms the later phases depend on are actually wired:

1. a graph can be compiled with a checkpointer;
2. a deterministic router selects the next node;
3. an interrupt suspends a run;
4. the run resumes from its checkpoint with state intact.

Point 3 is the one that matters most: human-in-the-loop gates are built on
interrupt/resume, so proving the mechanism now means later phases inherit a
known-good foundation rather than debugging it under pressure.

Delete or replace this module once the real graphs exist.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt


class SmokeState(TypedDict, total=False):
    """State for the smoke graph.

    Ids and flags only, following the three-tier rule (architecture D.1).
    """

    run_id: str
    project_id: str
    steps: Annotated[list[str], operator.add]
    needs_approval: bool
    approved: bool


def start_node(state: SmokeState) -> dict[str, Any]:
    return {"steps": ["start"]}


def gated_node(state: SmokeState) -> dict[str, Any]:
    """Suspend for a human decision.

    Note what this node does *not* do: it does not decide, and it does not read
    an approval from its own input. In the real gates the resumed value is
    cross-checked against a persisted ``ApprovalDecision`` so that a crafted
    resume payload cannot fabricate an approval (architecture M.2).
    """
    decision = interrupt({"question": "approve?", "run_id": state.get("run_id")})
    return {"steps": ["gated"], "approved": bool(decision)}


def finish_node(state: SmokeState) -> dict[str, Any]:
    return {"steps": ["finish"]}


def route_after_start(state: SmokeState) -> Literal["gated", "finish"]:
    """Deterministic router: state in, closed literal out, nothing else."""
    return "gated" if state.get("needs_approval") else "finish"


def build_smoke_graph(checkpointer: Any | None = None) -> Any:
    """Compile the smoke graph."""
    graph = StateGraph(SmokeState)
    graph.add_node("start", start_node)
    graph.add_node("gated", gated_node)
    graph.add_node("finish", finish_node)

    graph.add_edge(START, "start")
    graph.add_conditional_edges(
        "start",
        route_after_start,
        {"gated": "gated", "finish": "finish"},
    )
    graph.add_edge("gated", "finish")
    graph.add_edge("finish", END)

    return graph.compile(checkpointer=checkpointer)
