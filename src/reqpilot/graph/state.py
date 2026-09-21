"""Graph state conventions (architecture section D).

The three-tier rule the architecture sets out, restated because it governs every
state type added later:

+----------------------+------------------------------------------------------+
| Durable database     | All entities, evidence and decisions. Source of truth.|
| Graph state (here)   | Run identity, phase, working-set **ids**, bounded     |
|                      | typed objects, control flags, counters.               |
| Transient context    | Retrieved chunk text, assembled prompts, embeddings.  |
|                      | Never checkpointed.                                   |
+----------------------+------------------------------------------------------+

Two consequences worth stating plainly, because they are easy to violate:

* Graph state never contains a fact that is not also in the database.
* Chunk text, prompts and requirement bodies do **not** go in state. Nodes
  re-fetch by id. This keeps checkpoints small and stops project-sensitive text
  accumulating in a second store.

P0 defines the shared base only. ``AnalysisState``, ``ElicitationState``,
``SDLCState`` and ``DocumentationState`` are added by the roadmap phases that
introduce their graphs.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from reqpilot.domain.enums import GraphRunStatus


class NodeError(TypedDict):
    """A recorded node failure. Accumulated, never overwritten."""

    node: str
    message: str
    attempt: int


class BaseGraphState(TypedDict, total=False):
    """Fields every ReqPilot graph state carries.

    ``errors`` uses an append reducer so parallel branches merge without loss;
    scalar fields are last-write-wins, which is safe because exactly one node
    sets each.
    """

    # Identity - immutable for the life of the run.
    run_id: str
    project_id: str
    actor_id: str

    # Control.
    status: GraphRunStatus
    current_node: str
    attempt: int
    errors: Annotated[list[NodeError], operator.add]


#: Field names that must never be placed in graph state, checked by a test.
#: These carry content rather than references, and content belongs in the
#: database (architecture D.3).
FORBIDDEN_STATE_FIELDS: frozenset[str] = frozenset(
    {
        "chunk_text",
        "prompt",
        "prompts",
        "requirement_text",
        "statement",
        "api_key",
        "secret",
        "password",
        "permissions",
    }
)


def assert_state_shape(state_type: type) -> None:
    """Raise if a graph state declares a forbidden content-bearing field.

    Called by tests so that the three-tier rule is enforced mechanically as new
    state types are added, rather than remembered.
    """
    annotations = getattr(state_type, "__annotations__", {})
    offending = FORBIDDEN_STATE_FIELDS & set(annotations)
    if offending:
        raise ValueError(
            f"{state_type.__name__} declares forbidden content-bearing "
            f"state field(s): {sorted(offending)}. Graph state carries ids and "
            "flags; content lives in the database (architecture D.1, D.3)."
        )


class AnalysisState(BaseGraphState, total=False):
    """State of the P3 subset of ``analysis_graph`` (architecture C.3, D.2).

    Ids, counts and flags only. Proposals, statements and source text live in
    the database and are re-read by id; the one working object that passes from
    validation to persistence - the validation decision - is held in the run's
    transient context, never here, so no content reaches a checkpoint (D.1, D.3).
    """

    #: The identifier domain token the analyst chose for this run, e.g. ``LOAN``.
    domain: str
    #: Scope: the sources to extract from, or - for a classification-only run -
    #: the requirement versions to classify.
    scope_source_ids: list[str]
    scope_version_ids: list[str]
    #: One agent run per extraction window; their ids, in window order.
    extraction_agent_run_ids: list[str]
    #: Versions this run created (extraction) or was asked to classify.
    requirement_version_ids: list[str]
    classified_version_ids: Annotated[list[str], operator.add]
    #: Review signals are carried only as the ids of items needing review (D.3).
    low_confidence_item_ids: Annotated[list[str], operator.add]
    review_item_ids: Annotated[list[str], operator.add]
    accepted: int
    merged: int
    rejected: int
