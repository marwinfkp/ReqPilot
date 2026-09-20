"""Graph definitions (architecture C.1).

Four graphs are planned - elicitation, analysis, documentation, SDLC - each
scoped so that its state stays small and its runs stay independently resumable.
None of them is built in P0; they belong to the roadmap phases that need them.

``smoke`` is a P0-only graph that proves the orchestration machinery
(compile, deterministic routing, interrupt, resume) actually works.
"""

from reqpilot.graph.graphs.smoke import SmokeState, build_smoke_graph

__all__ = ["SmokeState", "build_smoke_graph"]
