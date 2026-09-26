"""``sdlc_graph`` - architecture C.5, the SDLC recommendation of roadmap P9::

    START -> collect_factor_evidence -+-> propose_factor_scores -> apply_rules_and_mcda
                                      |        -> generate_explanation -+-> raise_g6 -> END
                                      |                                 +-> END (no explanation yet)
                                      +-> generate_explanation            (mode "explain": a retry)
                                      +-> END                             (inputs not approved)

Every edge is deterministic: the routers read flags the nodes set from
persisted, validated values - never model text. The ranking is persisted by
``apply_rules_and_mcda`` before ``generate_explanation`` runs, so an explanation
can only describe a ranking that already exists (architecture L.5). G6 is raised
only once an explanation is stored, and it is decided by four humans through the
approval service - no node decides it.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from reqpilot.graph.nodes.sdlc import SdlcNodes
from reqpilot.graph.routers import (
    route_after_collect,
    route_after_explanation,
    route_after_scoring,
)
from reqpilot.graph.state import SDLCState

GRAPH_NAME = "sdlc_graph"

NODE_NAMES = (
    "collect_factor_evidence",
    "propose_factor_scores",
    "apply_rules_and_mcda",
    "generate_explanation",
    "raise_g6",
)


def build_sdlc_graph(nodes: SdlcNodes, checkpointer: Any | None = None) -> Any:
    graph = StateGraph(SDLCState)
    for name in NODE_NAMES:
        graph.add_node(name, getattr(nodes, name))

    graph.add_edge(START, "collect_factor_evidence")
    graph.add_conditional_edges(
        "collect_factor_evidence",
        route_after_collect,
        {"propose": "propose_factor_scores", "explain": "generate_explanation", "end": END},
    )
    graph.add_edge("propose_factor_scores", "apply_rules_and_mcda")
    graph.add_conditional_edges(
        "apply_rules_and_mcda",
        route_after_scoring,
        {"explain": "generate_explanation", "end": END},
    )
    graph.add_conditional_edges(
        "generate_explanation",
        route_after_explanation,
        {"raise_g6": "raise_g6", "end": END},
    )
    graph.add_edge("raise_g6", END)
    return graph.compile(checkpointer=checkpointer)
