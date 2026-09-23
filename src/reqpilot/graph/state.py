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
    #: P4: interview sessions whose stakeholder answers are extraction segments.
    scope_session_ids: list[str]
    #: P4: an answered clarification whose requirement is re-analysed
    #: (architecture C.3 ``route_after_clarification`` -> ``extract_requirements``).
    clarification_id: str
    #: P4: the outcome of a clarification re-analysis (a ``ReanalysisStatus`` value).
    revision_status: str
    # ---- P5: quality and conflict detection (C.3 nodes 6-8) ----------------
    #: A quality run: ``load_scope`` goes straight to ``quality_analysis``.
    quality_mode: bool
    #: After ``classify``, analyse the versions this run produced (P5; set for a
    #: clarification's re-analysis, FR-CLR-003's quality half).
    analyse_quality: bool
    #: A quality run's versions (empty = every current one). A follow-on
    #: analysis after ``classify`` uses ``requirement_version_ids`` instead.
    quality_version_ids: list[str]
    #: Conflicts only for pairs touching ``quality_version_ids`` (else all pairs).
    quality_focus: bool
    #: Whether the LLM semantic layer runs (review and adjudication).
    semantic: bool
    #: False: record findings only, no conflict shortlist (default: detect).
    detect_conflicts: bool
    quality_finding_ids: Annotated[list[str], operator.add]
    #: The deterministic shortlist: ids and scores only, cleared by
    #: ``conflict_adjudicate`` (architecture D.2 ``conflict_pairs``, D.4).
    conflict_pairs: list[dict]
    conflict_ids: Annotated[list[str], operator.add]
    #: Architecture D.2 routing flag: any open conflict was recorded.
    has_open_conflicts: bool
    #: Semantic calls that failed or were refused (recorded; rules still ran).
    semantic_failures: Annotated[int, operator.add]
    # ---- P6: compliance and security analysis (C.3 nodes 12-17, 20) ---------
    #: A compliance run: ``load_scope`` goes straight to ``compliance_retrieve``.
    compliance_mode: bool
    #: The versions to analyse (empty = every current, analysed version).
    compliance_version_ids: list[str]
    #: D.2 ``evidence_ids``: every evidence id this run supplied to a model -
    #: resolved citations; text is re-fetched by id, never carried here.
    evidence_ids: Annotated[list[str], operator.add]
    #: Versions whose retrieval was ``RETRIEVAL_EMPTY`` (escalated, not mapped).
    evidence_unavailable_ids: Annotated[list[str], operator.add]
    compliance_mapping_ids: Annotated[list[str], operator.add]
    compliance_gap_ids: Annotated[list[str], operator.add]
    security_finding_ids: Annotated[list[str], operator.add]
    #: Claims dropped by deterministic validation (audited one by one).
    claims_dropped: Annotated[int, operator.add]
    #: D.2 routing flags, set only by deterministic nodes from persisted values.
    has_high_impact_interpretation: bool
    has_high_security_risk: bool
    #: D.2 ``pending_gate_tasks``: (gate, task id, blocking) of raised G2/G3 tasks.
    pending_gate_tasks: Annotated[list[dict], operator.add]


class ElicitationState(BaseGraphState, total=False):
    """State of ``elicitation_graph`` (architecture C.4, F.6), ids and counters only.

    Mirrors the durable ``interview_session`` row: every value here is also in
    the database, and ``load_session`` re-derives them from it. No question,
    answer, issue or prompt text is ever placed here (D.1, D.3).
    """

    session_id: str
    stakeholder_id: str
    #: ``{topic_id: TopicStatus value}`` - written only by the coverage tracker.
    topic_coverage: dict[str, str]
    current_topic: str | None
    #: The C.4 bound's counter: ``followups_this_topic < max_followups``.
    followups_this_topic: int
    #: F.6 ``last_question_id``: the question utterance awaiting an answer.
    pending_question_id: str | None
    #: F.6 ``pending_answer``: the answer utterance to record and assess.
    answer_utterance_id: str | None
    #: The router's flag: the last assessment earned a bounded follow-up.
    awaiting_followup: bool
    #: The last assessment's status (an ``AnswerStatus`` value).
    last_assessment: str | None
    #: Where ``load_session`` found the durable session to be.
    resume_point: str
    complete: bool
    #: A node failed safely; the session stalls. Last-write-wins, reset by
    #: ``load_session`` - unlike ``errors``, which accumulates across a thread.
    failure: str | None
