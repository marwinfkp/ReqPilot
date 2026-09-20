"""Module M3 - LangGraph orchestration (architecture ADR-001, section C).

This package may import the domain and services. Nothing in the domain,
repository or service layers may import this package - that direction is
enforced in CI by import-linter, and it is the structural expression of
"governance lives outside the LLM" (architecture J.1).

The invariant this package must never break: **LLM output does not control graph
topology.** Routers are ordinary deterministic Python functions over typed state
(see :mod:`reqpilot.graph.routers`).
"""

from reqpilot.graph.builder import build_checkpointer, run_config
from reqpilot.graph.state import BaseGraphState, NodeError

__all__ = ["BaseGraphState", "NodeError", "build_checkpointer", "run_config"]
