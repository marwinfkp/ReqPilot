"""``elicitation_graph`` - architecture C.4, the interactive loop of roadmap P4::

    START -> load_session -+-> select_next_topic -+-> generate_question -> await_answer
                           |                      +-> end_interview -> END   (coverage complete)
                           +-> generate_question / await_answer / assess_answer / end_interview
                                                      (resuming from the durable session)
    await_answer (interrupt) -> record_utterance -> assess_answer
        -> router: follow-up (bounded)  -> generate_question
                   advance topic         -> select_next_topic
    any failure -> stall -> END                            (an analyst retries)

Every edge is deterministic: the conditional edges call routers in
:mod:`reqpilot.graph.routers`, which read flags and counters the nodes set from
typed, validated data - never model text. The model cannot choose a topic, end
the interview, or extend the follow-up bound.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from reqpilot.graph.nodes.elicitation import ElicitationNodes
from reqpilot.graph.routers import (
    route_after_assessment,
    route_after_await,
    route_after_load,
    route_after_question,
    route_after_record,
    route_after_topic,
)
from reqpilot.graph.state import ElicitationState

GRAPH_NAME = "elicitation_graph"

NODE_NAMES = (
    "load_session",
    "select_next_topic",
    "generate_question",
    "await_answer",
    "record_utterance",
    "assess_answer",
    "end_interview",
    "stall",
)


def build_elicitation_graph(nodes: ElicitationNodes, checkpointer: Any | None = None) -> Any:
    graph = StateGraph(ElicitationState)
    for name in NODE_NAMES:
        graph.add_node(name, getattr(nodes, name))

    graph.add_edge(START, "load_session")
    graph.add_conditional_edges(
        "load_session",
        route_after_load,
        {
            "select_next_topic": "select_next_topic",
            "generate_question": "generate_question",
            "await_answer": "await_answer",
            "assess_answer": "assess_answer",
            "end_interview": "end_interview",
            "stall": "stall",
        },
    )
    graph.add_conditional_edges(
        "select_next_topic",
        route_after_topic,
        {
            "generate_question": "generate_question",
            "end_interview": "end_interview",
            "stall": "stall",
        },
    )
    graph.add_conditional_edges(
        "generate_question",
        route_after_question,
        {"await_answer": "await_answer", "stall": "stall"},
    )
    graph.add_conditional_edges(
        "await_answer",
        route_after_await,
        {"record_utterance": "record_utterance", "stall": "stall"},
    )
    graph.add_conditional_edges(
        "record_utterance",
        route_after_record,
        {"assess_answer": "assess_answer", "stall": "stall"},
    )
    graph.add_conditional_edges(
        "assess_answer",
        route_after_assessment,
        {
            "generate_question": "generate_question",
            "select_next_topic": "select_next_topic",
            "stall": "stall",
        },
    )
    graph.add_edge("end_interview", END)
    graph.add_edge("stall", END)
    return graph.compile(checkpointer=checkpointer)
