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
    DataSensitivity,
    MaskingStatus,
    ReviewReason,
)
from reqpilot.domain.errors import AuthorizationError, EgressRefusedError, ReqPilotError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.base import utc_now
from reqpilot.domain.models.extraction import SourceChunk
from reqpilot.domain.models.requirements import RequirementVersion
from reqpilot.domain.policy import Actor
from reqpilot.domain.proposals import ExtractionDecision, ProposalRecord
from reqpilot.domain.requirement_ids import RequirementKind
from reqpilot.graph.state import AnalysisState, NodeError
from reqpilot.llm.gateway import LLMGateway
from reqpilot.llm.types import StructuredResult
from reqpilot.repositories.extraction import CandidateRepository, SourceDocumentRepository
from reqpilot.repositories.requirements import RequirementVersionRepository
from reqpilot.rules.extraction import ExtractionRules
from reqpilot.services.audit import AuditService
from reqpilot.services.classification import ClassificationService
from reqpilot.services.extraction import ExtractionService, RunLog
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

    @property
    def project_id(self) -> ProjectId:
        return self.log.project_id


def _error(node: str, message: str) -> list[NodeError]:
    return [NodeError(node=node, message=message, attempt=1)]


class AnalysisNodes:
    """The node functions, bound to one run's transient context."""

    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------------
    # 1. load_scope
    # ------------------------------------------------------------------
    def load_scope(self, state: AnalysisState) -> dict[str, Any]:
        node, ctx = "load_scope", self.ctx
        ctx.log.node_started(node)
        started = utc_now()
        try:
            if state.get("scope_source_ids"):
                sources = SourceDocumentRepository(ctx.session, ctx.actor)
                for raw in state["scope_source_ids"]:
                    if sources.get(ctx.project_id, uuid.UUID(raw)) is None:
                        raise ReqPilotError("a source in the scope is not in this project")
                count = {"sources": len(state["scope_source_ids"])}
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
        segments = self._segments(state["scope_source_ids"])
        role = RequirementExtractionRole(ctx.gateway, ctx.rules)
        extraction = ExtractionService(ctx.session, ctx.actor, ctx.rules)
        size = ctx.rules.max_segments_per_call
        agent_run_ids: list[str] = []
        ordinal = 0
        for window, start in enumerate(range(0, len(segments), size), start=1):
            views = self._views(segments[start : start + size])
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
        """Whether every source of a version was masked, and whether all are synthetic."""
        document_ids = sorted(
            {
                uuid.UUID(str(ref["document"]))
                for ref in version.source_refs or []
                if isinstance(ref, dict) and ref.get("document")
            }
        )
        documents = SourceDocumentRepository(self.ctx.session, self.ctx.actor).documents_by_id(
            self.ctx.project_id, document_ids
        )
        if not documents or len(documents) != len(document_ids):
            return False, False
        return (
            all(d.masking_status is MaskingStatus.MASKED for d in documents.values()),
            all(d.sensitivity is DataSensitivity.SYNTHETIC for d in documents.values()),
        )

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
