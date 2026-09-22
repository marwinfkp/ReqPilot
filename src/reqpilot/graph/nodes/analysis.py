"""The P3 nodes of ``analysis_graph`` (architecture C.3, nodes 1-5 and 25).

========================  ==============  =====================  ==================
Node                      Kind            Role                   Writes
========================  ==============  =====================  ==================
``load_scope``            deterministic   Coordinator (#1)       run record
``extract_requirements``  LLM             Extraction (#3)        proposals
``validate_extraction``   deterministic   Validation (#12)       nothing
``persist_candidates``    deterministic   Coordinator (#1)       requirements
``classify``              LLM             Classification (#5)    labels
``error_handler``         deterministic   Coordinator (#1)       failure record
========================  ==============  =====================  ==================

Each node is one step of "the LLM proposes; deterministic code disposes":
agents produce typed proposals through the gateway; the validation pipeline
judges them; services persist what survived, through the P1 repository. No node
routes on model text - the routers read counts and flags this module sets.

A node that fails records why - an agent run, a ``NODE_FAILED`` event, a review
item where a human should look - and adds to ``errors``; the router then sends
the run to ``error_handler`` rather than onward (C.8: never advance past a node
whose output a later guarantee needs).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from reqpilot.agents.contracts.extraction import ExtractedRequirement, SegmentView
from reqpilot.agents.roles import ClassificationRole, RequirementExtractionRole
from reqpilot.agents.validation import RecordedProposal, decide, validate_classification
from reqpilot.domain.enums import (
    AgentRole,
    AgentRunStatus,
    AuditEventType,
    ClarificationStatus,
    DataSensitivity,
    InterviewSessionKind,
    MaskingStatus,
    ReviewReason,
    SpeakerKind,
)
from reqpilot.domain.errors import AuthorizationError, EgressRefusedError, ReqPilotError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.base import utc_now
from reqpilot.domain.models.elicitation import Clarification
from reqpilot.domain.models.extraction import SourceChunk
from reqpilot.domain.models.requirements import RequirementVersion
from reqpilot.domain.policy import Actor
from reqpilot.domain.proposals import ExtractionDecision, ProposalRecord
from reqpilot.domain.requirement_ids import RequirementKind
from reqpilot.graph.nodes.quality import QualityNodes
from reqpilot.graph.state import AnalysisState, NodeError
from reqpilot.llm.gateway import LLMGateway
from reqpilot.llm.types import StructuredResult
from reqpilot.repositories.elicitation import (
    ClarificationRepository,
    InterviewSessionRepository,
    StakeholderRepository,
    UtteranceRepository,
)
from reqpilot.repositories.extraction import CandidateRepository, SourceDocumentRepository
from reqpilot.repositories.requirements import RequirementVersionRepository
from reqpilot.retrieval.embeddings import EmbeddingProvider
from reqpilot.rules.extraction import ExtractionRules
from reqpilot.rules.quality import QualityRules
from reqpilot.services.audit import AuditService
from reqpilot.services.classification import ClassificationService
from reqpilot.services.elicitation import source_facts
from reqpilot.services.extraction import ExtractionService, RunLog
from reqpilot.services.quality import VersionView
from reqpilot.services.requirements import RequirementService
from reqpilot.services.review import ReviewQueue

_KIND = {"functional": RequirementKind.FUNCTIONAL, "non_functional": RequirementKind.NON_FUNCTIONAL}


@dataclass
class RunContext:
    """The transient tier of a run (architecture D.1): never checkpointed.

    Holds the session, the pipeline actor, the gateway and rules, and the one
    working object that passes from validation to persistence.
    """

    session: Session
    actor: Actor
    log: RunLog
    gateway: LLMGateway
    rules: ExtractionRules
    decision: ExtractionDecision | None = None
    window_segments: dict[str, dict[str, SegmentView]] = field(default_factory=dict)
    # --- P5: quality and conflict detection --------------------------------
    quality_rules: QualityRules | None = None
    #: The local embedding provider for the conflict shortlist (ADR-005). The
    #: vectors it produces live only in this transient tier - never stored.
    embedder: EmbeddingProvider | None = None
    #: The versions a quality run reads, and those it records findings for.
    quality_pool: dict[str, VersionView] = field(default_factory=dict)
    quality_scope: list[str] = field(default_factory=list)

    @property
    def project_id(self) -> ProjectId:
        return self.log.project_id


def _error(node: str, message: str) -> list[NodeError]:
    return [NodeError(node=node, message=message, attempt=1)]


class AnalysisNodes:
    """The node functions, bound to one run's transient context."""

    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx
        #: C.3 nodes 6-8 (P5), sharing this run's context and recording helpers.
        self.quality = QualityNodes(self)

    # ------------------------------------------------------------------
    # 1. load_scope
    # ------------------------------------------------------------------
    def load_scope(self, state: AnalysisState) -> dict[str, Any]:
        node, ctx = "load_scope", self.ctx
        ctx.log.node_started(node)
        started = utc_now()
        try:
            if state.get("clarification_id"):
                clarification = self._clarification(state)
                if clarification.status is not ClarificationStatus.ANSWERED:
                    raise ReqPilotError("only an answered clarification is re-analysed")
                count = {"clarification": 1}
            elif state.get("scope_source_ids") or state.get("scope_session_ids"):
                sources = SourceDocumentRepository(ctx.session, ctx.actor)
                for raw in state.get("scope_source_ids", []):
                    if sources.get(ctx.project_id, uuid.UUID(raw)) is None:
                        raise ReqPilotError("a source in the scope is not in this project")
                sessions = InterviewSessionRepository(ctx.session, ctx.actor)
                for raw in state.get("scope_session_ids", []):
                    row = sessions.get(ctx.project_id, uuid.UUID(raw))
                    if row is None or row.kind is not InterviewSessionKind.INTERVIEW:
                        raise ReqPilotError(
                            "an interview session in the scope is not in this project"
                        )
                count = {
                    "sources": len(state.get("scope_source_ids", [])),
                    "sessions": len(state.get("scope_session_ids", [])),
                }
            else:
                versions = RequirementVersionRepository(ctx.session, ctx.actor)
                for raw in state.get("scope_version_ids", []):
                    version = versions.get(ctx.project_id, uuid.UUID(raw))
                    if version is None:
                        raise ReqPilotError("a version in the scope is not in this project")
                    if version.state is not RequirementState.EXTRACTED:
                        raise ReqPilotError(
                            "classification runs on EXTRACTED versions; "
                            f"one in the scope is {version.state}"
                        )
                count = {"versions": len(state.get("scope_version_ids", []))}
        except ReqPilotError as exc:
            return self._fail(node, AgentRole.COORDINATOR, started, "invalid_scope", exc)
        self._deterministic_run(node, AgentRole.COORDINATOR, started, count)
        ctx.log.node_completed(node, **count)
        return {"current_node": node}

    # ------------------------------------------------------------------
    # 2. extract_requirements (LLM - role #3)
    # ------------------------------------------------------------------
    def extract_requirements(self, state: AnalysisState) -> dict[str, Any]:
        node, ctx = "extract_requirements", self.ctx
        ctx.log.node_started(node)
        segments = self._scope_segments(state)
        if not segments:
            return self._fail(
                node,
                AgentRole.REQUIREMENT_EXTRACTION,
                utc_now(),
                "empty_scope",
                ReqPilotError("the scope contains no segment to extract from"),
            )
        role = RequirementExtractionRole(ctx.gateway, ctx.rules)
        extraction = ExtractionService(ctx.session, ctx.actor, ctx.rules)
        size = ctx.rules.max_segments_per_call
        agent_run_ids: list[str] = []
        ordinal = 0
        for window, start in enumerate(range(0, len(segments), size), start=1):
            views = {
                f"S{index}": dataclasses.replace(segment, segment_id=f"S{index}")
                for index, segment in enumerate(segments[start : start + size], start=1)
            }
            started = utc_now()
            try:
                result = role.propose(list(views.values()), domain=state["domain"])
            except EgressRefusedError as exc:
                return self._fail(node, role.role, started, "egress_refused", exc)
            input_refs = {
                "window": window,
                "segments": {sid: str(view.chunk_id) for sid, view in views.items()},
            }
            if not result.ok:
                agent_run = self._llm_run(node, role.role, started, result, input_refs)
                item = ReviewQueue(ctx.session, ctx.actor).raise_item(
                    project_id=ctx.project_id,
                    reason=ReviewReason.MALFORMED_OUTPUT,
                    subject_type="agent_run",
                    subject_id=agent_run.id,
                    graph_run_id=ctx.log.run.id,
                    agent_run_id=agent_run.id,
                    detail={"error_code": str(result.error_code), "window": window},
                )
                ctx.log.node_failed(node, str(result.error_code))
                return {
                    "errors": _error(node, result.error_message or "extraction failed"),
                    "review_item_ids": [str(item.id)],
                }
            assert result.value is not None
            agent_run = self._llm_run(
                node,
                role.role,
                started,
                result,
                input_refs,
                output_refs={"proposals": len(result.value.requirements)},
            )
            recorded = extraction.record_proposals(
                project_id=ctx.project_id,
                graph_run_id=ctx.log.run.id,
                agent_run_id=agent_run.id,
                window=window,
                first_ordinal=ordinal,
                proposals=[_record(r) for r in result.value.requirements],
            )
            ordinal += len(recorded)
            ctx.window_segments[str(agent_run.id)] = views
            agent_run_ids.append(str(agent_run.id))
        ctx.log.node_completed(node, windows=len(agent_run_ids), proposals=ordinal)
        return {"current_node": node, "extraction_agent_run_ids": agent_run_ids}

    # ------------------------------------------------------------------
    # 3. validate_extraction (deterministic - role #12)
    # ------------------------------------------------------------------
    def validate_extraction(self, state: AnalysisState) -> dict[str, Any]:
        node, ctx = "validate_extraction", self.ctx
        ctx.log.node_started(node)
        started = utc_now()
        candidates = CandidateRepository(ctx.session, ctx.actor).list_for_run(
            ctx.project_id, ctx.log.run.id
        )
        windows: list[tuple[list[RecordedProposal], dict[str, SegmentView]]] = []
        for agent_run_id in state.get("extraction_agent_run_ids", []):
            proposals = [
                RecordedProposal(
                    candidate_id=c.id,
                    run_key=c.candidate_key,
                    model_key=ExtractedRequirement.model_validate(c.proposal).candidate_key,
                    ordinal=c.ordinal,
                    requirement=ExtractedRequirement.model_validate(c.proposal),
                    key_collision="#" in c.candidate_key,
                )
                for c in candidates
                if str(c.agent_run_id) == agent_run_id
            ]
            windows.append((proposals, ctx.window_segments[agent_run_id]))
        ctx.decision = decide(windows, ctx.rules)
        counts = {
            "accepted": len(ctx.decision.accepted),
            "merged": len(ctx.decision.merged),
            "rejected": len(ctx.decision.rejected),
            "near_duplicates": len(ctx.decision.near_duplicates),
        }
        self._deterministic_run(node, AgentRole.VALIDATION, started, counts)
        ctx.log.node_completed(node, **counts)
        return {"current_node": node}

    # ------------------------------------------------------------------
    # 4. persist_candidates (deterministic)
    # ------------------------------------------------------------------
    def persist_candidates(self, state: AnalysisState) -> dict[str, Any]:
        node, ctx = "persist_candidates", self.ctx
        ctx.log.node_started(node)
        started = utc_now()
        if ctx.decision is None:  # pragma: no cover - the graph's edges prevent it
            raise RuntimeError("persist_candidates reached without a validation decision")
        outcome = ExtractionService(ctx.session, ctx.actor, ctx.rules).apply(
            project_id=ctx.project_id,
            graph_run_id=ctx.log.run.id,
            domain=state["domain"],
            decision=ctx.decision,
        )
        ctx.decision = None  # the working object is consumed here (D.4)
        counts = {
            "accepted": outcome.accepted,
            "merged": outcome.merged,
            "rejected": outcome.rejected,
            "review_items": len(outcome.review_item_ids),
        }
        self._deterministic_run(
            node,
            AgentRole.COORDINATOR,
            started,
            counts,
            output_refs={"requirement_version_ids": [str(v) for v in outcome.version_ids]},
        )
        ctx.log.node_completed(node, **counts)
        return {
            "current_node": node,
            "requirement_version_ids": [str(v) for v in outcome.version_ids],
            "review_item_ids": [str(i) for i in outcome.review_item_ids],
            "accepted": outcome.accepted,
            "merged": outcome.merged,
            "rejected": outcome.rejected,
        }

    # ------------------------------------------------------------------
    # 4b. persist_revision (deterministic, P4: clarification re-analysis)
    # ------------------------------------------------------------------
    def persist_revision(self, state: AnalysisState) -> dict[str, Any]:
        """Revise the clarified requirement - a new immutable version, or none (FR-CLR-003).

        The clarified version moves ``CLARIFICATION_REQUIRED -> CLARIFIED`` (its
        guard: a recorded answer, H.3) once no open clarification is left on it.
        Re-extraction then either confirms the statement (no version is created)
        or yields a new version of the *same* requirement, which starts its own
        lifecycle and inherits no approval (P1 ``create_version``).
        """
        node, ctx = "persist_revision", self.ctx
        ctx.log.node_started(node)
        started = utc_now()
        if ctx.decision is None:  # pragma: no cover - the graph's edges prevent it
            raise RuntimeError("persist_revision reached without a validation decision")
        clarification = self._clarification(state)
        predecessor = RequirementVersionRepository(ctx.session, ctx.actor).get(
            ctx.project_id, clarification.requirement_version_id
        )
        if predecessor is None:  # pragma: no cover - a foreign key
            raise RuntimeError("the clarified version is missing")
        requirements = RequirementService(ctx.session, ctx.actor)
        still_open = [
            c
            for c in ClarificationRepository(ctx.session, ctx.actor).for_version(
                ctx.project_id, predecessor.id
            )
            if c.status is ClarificationStatus.OPEN
        ]
        if predecessor.state is RequirementState.CLARIFICATION_REQUIRED and not still_open:
            requirements.transition(
                project_id=ctx.project_id,
                version_id=predecessor.id,
                target=RequirementState.CLARIFIED,
            )
        answer = UtteranceRepository(ctx.session, ctx.actor).get(
            ctx.project_id,
            clarification.answer_utterance_id,  # type: ignore[arg-type]
        )
        provenance = []
        if answer is not None:
            provenance.append(
                {
                    "kind": "utterance",
                    "ref": str(answer.id),
                    "span": [0, len(answer.text)],
                    "session": str(answer.session_id),
                    "quote": answer.text,
                    "speaker": self._speaker_label(answer),
                    "clarification": str(clarification.id),
                }
            )
        outcome = ExtractionService(ctx.session, ctx.actor, ctx.rules).apply_revision(
            project_id=ctx.project_id,
            graph_run_id=ctx.log.run.id,
            decision=ctx.decision,
            requirement_id=predecessor.requirement_id,
            predecessor=predecessor,
            provenance_refs=provenance,
            change_reason=(
                f"clarification {clarification.id} answered "
                f"(finding {clarification.quality_finding_id})"
            ),
        )
        ctx.decision = None
        counts = {"outcome": outcome.status, "review_items": len(outcome.review_item_ids)}
        self._deterministic_run(
            node,
            AgentRole.COORDINATOR,
            started,
            counts,
            output_refs={"version_id": str(outcome.version_id) if outcome.version_id else None},
        )
        ctx.log.node_completed(node, **counts)
        created = outcome.status == "new_version" and outcome.version_id is not None
        return {
            "current_node": node,
            "revision_status": outcome.status,
            "requirement_version_ids": [str(outcome.version_id)] if created else [],
            "review_item_ids": [str(i) for i in outcome.review_item_ids],
            "accepted": 1 if created else 0,
        }

    # ------------------------------------------------------------------
    # 5. classify (LLM - role #5)
    # ------------------------------------------------------------------
    def classify(self, state: AnalysisState) -> dict[str, Any]:
        node, ctx = "classify", self.ctx
        ctx.log.node_started(node)
        ids = state.get("requirement_version_ids") or state.get("scope_version_ids") or []
        versions = RequirementVersionRepository(ctx.session, ctx.actor)
        role = ClassificationRole(ctx.gateway)
        queue = ReviewQueue(ctx.session, ctx.actor)
        classified: list[str] = []
        low: list[str] = []
        items: list[str] = []
        for raw in ids:
            version = versions.get(ctx.project_id, uuid.UUID(raw))
            if version is None or version.state is not RequirementState.EXTRACTED:
                continue
            masked, synthetic = self._source_facts(version)
            started = utc_now()
            try:
                result = role.propose(version.statement, masked=masked, synthetic=synthetic)
            except EgressRefusedError as exc:
                return self._fail(node, role.role, started, "egress_refused", exc)
            decision = (
                validate_classification(result.value, ctx.rules)
                if result.value is not None
                else None
            )
            agent_run = self._llm_run(
                node,
                role.role,
                started,
                result,
                {"requirement_version_id": raw},
                output_refs=(
                    {
                        "labels": [str(label.category) for label in decision.labels],
                        "unknown": len(decision.unknown),
                    }
                    if decision is not None
                    else {}
                ),
                status_override=(
                    AgentRunStatus.PARTIAL
                    if decision is not None and (decision.unknown or decision.failed)
                    else None
                ),
            )
            if decision is None or decision.failed:
                item = queue.raise_item(
                    project_id=ctx.project_id,
                    reason=ReviewReason.CLASSIFICATION_FAILED,
                    subject_type="requirement_version",
                    subject_id=version.id,
                    requirement_version_id=version.id,
                    graph_run_id=ctx.log.run.id,
                    agent_run_id=agent_run.id,
                    detail={
                        "error_code": str(result.error_code) if result.error_code else None,
                        "unknown_labels": list(decision.unknown) if decision else [],
                    },
                )
                items.append(str(item.id))
                continue
            ClassificationService(ctx.session, ctx.actor).record_proposal(
                project_id=ctx.project_id,
                version_id=version.id,
                labels=decision.labels,
                agent_run_id=agent_run.id,
                graph_run_id=ctx.log.run.id,
                ruleset_version=decision.ruleset_version,
            )
            # EXTRACTED -> CLASSIFIED only through the guarded transition (H.3).
            RequirementService(ctx.session, ctx.actor).transition(
                project_id=ctx.project_id,
                version_id=version.id,
                target=RequirementState.CLASSIFIED,
            )
            classified.append(raw)
            for label in decision.low_signal:
                item = queue.raise_item(
                    project_id=ctx.project_id,
                    reason=ReviewReason.LOW_CLASSIFICATION_SIGNAL,
                    subject_type="requirement_version",
                    subject_id=version.id,
                    requirement_version_id=version.id,
                    category=label.category,
                    review_signal=label.review_signal,
                    graph_run_id=ctx.log.run.id,
                    agent_run_id=agent_run.id,
                    detail={"threshold": ctx.rules.classification_review_threshold},
                )
                items.append(str(item.id))
                low.append(str(item.id))
            if decision.unknown:
                item = queue.raise_item(
                    project_id=ctx.project_id,
                    reason=ReviewReason.UNKNOWN_LABEL,
                    subject_type="requirement_version",
                    subject_id=version.id,
                    requirement_version_id=version.id,
                    graph_run_id=ctx.log.run.id,
                    agent_run_id=agent_run.id,
                    detail={"unknown_labels": list(decision.unknown)},
                )
                items.append(str(item.id))
        ctx.log.node_completed(node, classified=len(classified), review_items=len(items))
        return {
            "current_node": node,
            "classified_version_ids": classified,
            "low_confidence_item_ids": low,
            "review_item_ids": items,
        }

    # ------------------------------------------------------------------
    # 25. error_handler
    # ------------------------------------------------------------------
    def error_handler(self, state: AnalysisState) -> dict[str, Any]:
        node, ctx = "error_handler", self.ctx
        ctx.log.node_started(node)
        self._deterministic_run(
            node, AgentRole.COORDINATOR, utc_now(), {"errors": len(state.get("errors", []))}
        )
        ctx.log.node_completed(node, errors=len(state.get("errors", [])))
        return {"current_node": node}

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _segments(self, source_ids: Sequence[str]) -> list[SourceChunk]:
        sources = SourceDocumentRepository(self.ctx.session, self.ctx.actor)
        segments: list[SourceChunk] = []
        for raw in source_ids:
            segments.extend(sources.chunks(self.ctx.project_id, uuid.UUID(raw)))
        return segments

    def _views(self, chunks: Sequence[SourceChunk]) -> dict[str, SegmentView]:
        sources = SourceDocumentRepository(self.ctx.session, self.ctx.actor)
        documents = sources.documents_by_id(
            self.ctx.project_id, sorted({c.source_document_id for c in chunks})
        )
        views: dict[str, SegmentView] = {}
        for index, chunk in enumerate(chunks, start=1):
            document = documents[chunk.source_document_id]
            views[f"S{index}"] = SegmentView(
                segment_id=f"S{index}",
                chunk_id=chunk.id,
                document_id=chunk.source_document_id,
                char_start=chunk.char_start,
                text=chunk.text,
                speaker=chunk.speaker,
                masked=document.masking_status is MaskingStatus.MASKED,
                synthetic=document.sensitivity is DataSensitivity.SYNTHETIC,
            )
        return views

    def _source_facts(self, version: RequirementVersion) -> tuple[bool, bool]:
        """Whether every source of a version was masked, and whether all are synthetic.

        Sources are document chunks and, from P4, interview utterances.
        """
        return source_facts(
            self.ctx.session, self.ctx.actor, self.ctx.project_id, version.source_refs or []
        )

    # -- scope segments (P3 documents; P4 interviews and clarifications) --------
    def _scope_segments(self, state: AnalysisState) -> list[SegmentView]:
        if state.get("clarification_id"):
            return self._clarification_segments(self._clarification(state))
        segments = self._views_without_ids(self._segments(state.get("scope_source_ids", [])))
        for raw in state.get("scope_session_ids", []):
            segments.extend(self._session_segments(uuid.UUID(raw)))
        return segments

    def _views_without_ids(self, chunks: Sequence[SourceChunk]) -> list[SegmentView]:
        return list(self._views(chunks).values())

    def _session_segments(self, session_id: uuid.UUID) -> list[SegmentView]:
        """One segment per stakeholder answer; the question it replies to is context."""
        ctx = self.ctx
        row = InterviewSessionRepository(ctx.session, ctx.actor).get(ctx.project_id, session_id)
        if row is None:  # pragma: no cover - load_scope checked it
            return []
        utterances = UtteranceRepository(ctx.session, ctx.actor).for_session(ctx.project_id, row.id)
        by_id = {u.id: u for u in utterances}
        synthetic = row.sensitivity is DataSensitivity.SYNTHETIC
        return [
            self._utterance_view(
                u, by_id.get(u.replies_to_id) if u.replies_to_id else None, synthetic
            )
            for u in utterances
            if u.speaker_kind is SpeakerKind.STAKEHOLDER
        ]

    def _clarification_segments(self, clarification: Clarification) -> list[SegmentView]:
        """The clarified requirement's own sources, plus the clarification answer."""
        ctx = self.ctx
        version = RequirementVersionRepository(ctx.session, ctx.actor).get(
            ctx.project_id, clarification.requirement_version_id
        )
        if version is None:  # pragma: no cover - a foreign key
            return []
        segments: list[SegmentView] = []
        seen: set[uuid.UUID] = set()
        chunk_ids = [
            uuid.UUID(str(r["ref"]))
            for r in version.source_refs or []
            if isinstance(r, dict) and r.get("kind") == "source_chunk"
        ]
        for chunk_id in chunk_ids:
            chunk = ctx.session.get(SourceChunk, chunk_id)
            if chunk is not None and chunk.project_id == ctx.project_id and chunk.id not in seen:
                seen.add(chunk.id)
                segments.extend(self._views_without_ids([chunk]))
        utterances = UtteranceRepository(ctx.session, ctx.actor)
        sessions = InterviewSessionRepository(ctx.session, ctx.actor)
        cited = [
            uuid.UUID(str(r["ref"]))
            for r in version.source_refs or []
            if isinstance(r, dict) and r.get("kind") == "utterance"
        ]
        for utterance_id in [*cited, clarification.answer_utterance_id]:
            if utterance_id is None or utterance_id in seen:
                continue
            utterance = utterances.get(ctx.project_id, utterance_id)
            if utterance is None:
                continue
            seen.add(utterance.id)
            row = sessions.get(ctx.project_id, utterance.session_id)
            question = (
                utterances.get(ctx.project_id, utterance.replies_to_id)
                if utterance.replies_to_id
                else None
            )
            segments.append(
                self._utterance_view(
                    utterance,
                    question,
                    row is not None and row.sensitivity is DataSensitivity.SYNTHETIC,
                )
            )
        return segments

    def _utterance_view(self, utterance: Any, question: Any, synthetic: bool) -> SegmentView:
        return SegmentView(
            segment_id="",
            chunk_id=utterance.id,
            document_id=utterance.session_id,
            char_start=0,
            text=utterance.text,
            speaker=self._speaker_label(utterance),
            masked=False,
            synthetic=synthetic,
            source_kind="utterance",
            context=" ".join(question.text.split()) if question is not None else None,
        )

    def _speaker_label(self, utterance: Any) -> str | None:
        if utterance.speaker_ref is None:
            return None
        stakeholder = StakeholderRepository(self.ctx.session, self.ctx.actor).get(
            self.ctx.project_id, utterance.speaker_ref
        )
        if stakeholder is None:  # pragma: no cover - a foreign key
            return None
        return f"{stakeholder.name} ({stakeholder.stakeholder_role})"

    def _clarification(self, state: AnalysisState) -> Clarification:
        clarification = ClarificationRepository(self.ctx.session, self.ctx.actor).get(
            self.ctx.project_id, uuid.UUID(state["clarification_id"])
        )
        if clarification is None:
            raise ReqPilotError("the clarification is not in this project")
        return clarification

    def _llm_run(
        self,
        node: str,
        role: AgentRole,
        started: dt.datetime,
        result: StructuredResult[Any],
        input_refs: dict[str, Any],
        *,
        output_refs: dict[str, Any] | None = None,
        status_override: AgentRunStatus | None = None,
    ) -> Any:
        spec = self.ctx.gateway.prompts.get(result.meta.prompt_name)
        status = (
            status_override
            if status_override is not None
            else (AgentRunStatus.OK if result.ok else AgentRunStatus.FAILED)
        )
        return self.ctx.log.agent_run(
            node=node,
            role=role,
            status=status,
            started_at=started,
            meta=result.meta,
            prompt_role=spec.role,
            prompt_text=spec.text,
            input_refs=input_refs,
            output_refs={**(output_refs or {}), "output_sha256": result.output_sha256},
            error_code=str(result.error_code) if result.error_code else None,
        )

    def _deterministic_run(
        self,
        node: str,
        role: AgentRole,
        started: dt.datetime,
        counts: dict[str, Any],
        *,
        output_refs: dict[str, Any] | None = None,
    ) -> None:
        self.ctx.log.agent_run(
            node=node,
            role=role,
            status=AgentRunStatus.OK,
            started_at=started,
            input_refs={},
            output_refs={**counts, **(output_refs or {})},
        )

    def _fail(
        self,
        node: str,
        role: AgentRole,
        started: dt.datetime,
        code: str,
        exc: ReqPilotError,
    ) -> dict[str, Any]:
        ctx = self.ctx
        ctx.log.agent_run(
            node=node,
            role=role,
            status=AgentRunStatus.FAILED,
            started_at=started,
            error_code=code,
        )
        ctx.log.node_failed(node, code)
        if isinstance(exc, (AuthorizationError, EgressRefusedError)):
            AuditService(ctx.session).append(
                event_type=AuditEventType.PERMISSION_DENIED,
                actor_kind=ctx.actor.kind,
                actor_ref=str(ctx.actor.actor_id),
                project_id=ctx.project_id,
                subject_type="graph_run",
                subject_id=str(ctx.log.run.id),
                graph_run_id=ctx.log.run.id,
                payload={"node": node, "error_code": code},
            )
        return {"errors": _error(node, str(exc))}


def _record(requirement: ExtractedRequirement) -> ProposalRecord:
    return ProposalRecord(
        model_key=requirement.candidate_key,
        statement=requirement.statement,
        kind=_KIND[requirement.requirement_type],
        review_signal=requirement.review_signal,
        proposal=requirement.model_dump(mode="json"),
    )
