"""The Coordinator's run entry point (architecture E #1, C.1: ``GraphRunner``).

Starts one ``analysis_graph`` run in **batch mode**: the analyst supplies sources
that already exist, the run extracts and classifies, and it ends. There is no
interview loop and no resume from a human answer - that is the elicitation
graph, roadmap phase P4.

The run executes synchronously within the caller's transaction, one node after
another. Each node records what it did as it goes; the final status is decided
by whether any node recorded an error. A bug - an exception no node expected -
is not caught: the request fails and its transaction rolls back, which is the
architecture's "fail loudly" (T).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from reqpilot.config import Settings, get_settings
from reqpilot.domain.enums import Action, GraphRunStatus
from reqpilot.domain.errors import ReqPilotError
from reqpilot.domain.ids import GraphRunId, ProjectId
from reqpilot.domain.policy import Actor
from reqpilot.domain.requirement_ids import normalise_domain
from reqpilot.graph.builder import build_checkpointer, checkpointer_scope, run_config
from reqpilot.graph.graphs.analysis import build_analysis_graph
from reqpilot.graph.nodes.analysis import AnalysisNodes, RunContext
from reqpilot.graph.state import AnalysisState
from reqpilot.llm.accounting import UsageLedger
from reqpilot.llm.gateway import LLMGateway
from reqpilot.retrieval.embeddings import EmbeddingProvider
from reqpilot.rules.compliance import (
    ComplianceRules,
    SecurityRules,
    packaged_compliance_rules,
    packaged_security_rules,
)
from reqpilot.rules.extraction import ExtractionRules
from reqpilot.rules.quality import QualityRules, packaged_quality_rules
from reqpilot.rules.risk import RiskRules, packaged_risk_rules
from reqpilot.services.compliance import Retriever
from reqpilot.services.extraction import RunLog, RunRecorder

#: Architecture D3: a run processes a bounded batch.
MAX_SCOPE_ITEMS = 50


@dataclass(frozen=True)
class RunSummary:
    run_id: uuid.UUID
    status: GraphRunStatus
    requirement_version_ids: tuple[uuid.UUID, ...]
    classified_version_ids: tuple[uuid.UUID, ...]
    review_item_ids: tuple[uuid.UUID, ...]
    accepted: int
    merged: int
    rejected: int
    errors: tuple[str, ...]
    provider_calls: int
    tokens_in: int
    tokens_out: int
    cost_estimate: float | None
    #: P4 clarification re-analysis: a ``ReanalysisStatus`` value, else ``None``.
    revision_status: str | None = None
    #: P5: what quality analysis and conflict detection recorded in this run.
    quality_finding_ids: tuple[uuid.UUID, ...] = ()
    conflict_ids: tuple[uuid.UUID, ...] = ()
    #: Semantic (LLM) calls that failed or were refused; the rules still ran.
    semantic_failures: int = 0
    #: P6: what compliance and security analysis recorded in this run.
    evidence_ids: tuple[uuid.UUID, ...] = ()
    evidence_unavailable_ids: tuple[uuid.UUID, ...] = ()
    compliance_mapping_ids: tuple[uuid.UUID, ...] = ()
    compliance_gap_ids: tuple[uuid.UUID, ...] = ()
    security_finding_ids: tuple[uuid.UUID, ...] = ()
    claims_dropped: int = 0
    gate_task_ids: tuple[uuid.UUID, ...] = ()
    #: P7: the risks this run identified, and how many proposals the FR-RSK-011
    #: scope guard refused.
    risk_ids: tuple[uuid.UUID, ...] = ()
    risks_out_of_scope: int = 0


class AnalysisRunner:
    def __init__(
        self,
        session: Session,
        gateway: LLMGateway,
        rules: ExtractionRules,
        *,
        settings: Settings | None = None,
        quality_rules: QualityRules | None = None,
        embedder: EmbeddingProvider | None = None,
        compliance_rules: ComplianceRules | None = None,
        security_rules: SecurityRules | None = None,
        retriever: Retriever | None = None,
        risk_rules: RiskRules | None = None,
    ) -> None:
        self._session = session
        self._gateway = gateway
        self._rules = rules
        self._settings = settings or get_settings()
        self._quality_rules = quality_rules or packaged_quality_rules()
        #: The conflict shortlist's embedding provider (ADR-005, local). ``None``
        #: shortlists on lexical and structural signals only.
        self._embedder = embedder
        #: P6: the versioned checklist and security catalogue, and the P2
        #: allowlisted retrieval boundary (``RetrievalService.retrieve``).
        self._compliance_rules = compliance_rules or packaged_compliance_rules()
        self._security_rules = security_rules or packaged_security_rules()
        self._retriever = retriever
        #: P7: the versioned severity matrix and register rules (architecture I.3).
        self._risk_rules = risk_rules or packaged_risk_rules()

    def semantic_default(self) -> bool:
        """The LLM semantic layer runs unless the gateway is the offline stub."""
        return self._gateway.provider_name != "stub"

    def extract(
        self,
        *,
        actor: Actor,
        project_id: ProjectId,
        source_ids: Sequence[uuid.UUID] = (),
        domain: str,
        session_ids: Sequence[uuid.UUID] = (),
    ) -> RunSummary:
        """Extract requirements from sources and/or interview sessions, and classify them.

        From P4 an interview session is a scope item like a source: each of its
        stakeholder answers is one extraction segment (architecture C.1: the
        analysis graph reads the utterances the elicitation graph wrote).
        """
        if not source_ids and not session_ids:
            raise ReqPilotError("an extraction run needs at least one source or session")
        if len(source_ids) + len(session_ids) > MAX_SCOPE_ITEMS:
            raise ReqPilotError(f"a run takes at most {MAX_SCOPE_ITEMS} sources and sessions")
        domain = normalise_domain(domain)
        return self._run(
            actor,
            project_id,
            {
                "domain": domain,
                "scope_source_ids": [str(s) for s in dict.fromkeys(source_ids)],
                "scope_session_ids": [str(s) for s in dict.fromkeys(session_ids)],
                "scope_version_ids": [],
            },
            scope={
                "mode": "extract",
                "sources": len(set(source_ids)),
                "sessions": len(set(session_ids)),
                "domain": domain,
            },
        )

    def reanalyse_clarification(
        self,
        *,
        actor: Actor,
        project_id: ProjectId,
        clarification_id: uuid.UUID,
        domain: str,
        trigger: Action | None = None,
    ) -> RunSummary:
        """Re-analyse the requirement an answered clarification is about (``FR-CLR-003``).

        Architecture C.3 ``route_after_clarification``: answered ->
        ``extract_requirements`` over the requirement's own sources plus the
        answer, then a new immutable version if the statement changed, then
        classification. Started by the person whose answer triggered it; the
        run acts as its pipeline actor.
        """
        return self._run(
            actor,
            project_id,
            {
                "domain": normalise_domain(domain),
                "scope_source_ids": [],
                "scope_session_ids": [],
                "scope_version_ids": [],
                "clarification_id": str(clarification_id),
                # P5: the quality half of FR-CLR-003 - a new version is analysed,
                # and checked for conflicts against the current set.
                "analyse_quality": True,
                "quality_focus": True,
                "semantic": self.semantic_default(),
            },
            scope={"mode": "clarification_reanalysis", "clarification_id": str(clarification_id)},
            trigger=trigger,
        )

    def analyse_quality(
        self,
        *,
        actor: Actor,
        project_id: ProjectId,
        version_ids: Sequence[uuid.UUID] = (),
        semantic: bool | None = None,
        detect_conflicts: bool = True,
    ) -> RunSummary:
        """Quality analysis and conflict detection over the project's requirements (P5).

        Architecture C.3 nodes 6-8. With no ``version_ids`` every current version
        is analysed and every pair is a conflict candidate (``FR-CNF-001``: the
        full requirement set); with ``version_ids`` findings are recorded for
        those versions and conflicts only for pairs touching them. ``semantic``
        defaults to on unless the gateway is the offline stub.
        """
        if len(version_ids) > self._quality_rules.max_versions_per_run:
            raise ReqPilotError(
                f"a quality run takes at most {self._quality_rules.max_versions_per_run} versions"
            )
        use_semantic = self.semantic_default() if semantic is None else semantic
        return self._run(
            actor,
            project_id,
            {
                "domain": "",
                "scope_source_ids": [],
                "scope_version_ids": [],
                "quality_mode": True,
                "quality_version_ids": [str(v) for v in dict.fromkeys(version_ids)],
                "quality_focus": bool(version_ids),
                "semantic": use_semantic,
                "detect_conflicts": detect_conflicts,
            },
            scope={
                "mode": "quality",
                "versions": len(set(version_ids)) or "all_current",
                "semantic": use_semantic,
                "detect_conflicts": detect_conflicts,
                "ruleset": self._quality_rules.ruleset_ref,
            },
        )

    def analyse_compliance(
        self,
        *,
        actor: Actor,
        project_id: ProjectId,
        version_ids: Sequence[uuid.UUID] = (),
        semantic: bool | None = None,
    ) -> RunSummary:
        """Compliance and security/privacy analysis of the project's requirements (P6).

        Architecture C.3 nodes 12-17 and the G2/G3 part of 20. With no
        ``version_ids`` every current version is analysed. Evidence comes only
        from the P2 allowlisted retrieval; gaps come only from the versioned
        checklist; the authoritative security/privacy risk level comes only from
        the deterministic evaluator; G2/G3 tasks are raised from persisted
        values. ``semantic`` defaults to on unless the gateway is the offline stub.
        """
        if len(version_ids) > self._compliance_rules.max_versions_per_run:
            raise ReqPilotError(
                "a compliance run takes at most "
                f"{self._compliance_rules.max_versions_per_run} versions"
            )
        if self._retriever is None:
            raise ReqPilotError(
                "compliance analysis needs the allowlisted retrieval service (P2); none is "
                "configured for this runner"
            )
        use_semantic = self.semantic_default() if semantic is None else semantic
        return self._run(
            actor,
            project_id,
            {
                "domain": "",
                "scope_source_ids": [],
                "scope_version_ids": [],
                "compliance_mode": True,
                "compliance_version_ids": [str(v) for v in dict.fromkeys(version_ids)],
                "semantic": use_semantic,
            },
            scope={
                "mode": "compliance",
                "versions": len(set(version_ids)) or "all_current",
                "semantic": use_semantic,
                "checklist": self._compliance_rules.ruleset_ref,
                "risk_rules": self._security_rules.ruleset_ref,
                "risk_matrix": self._risk_rules.matrix.version,
            },
        )

    def analyse_risk(
        self,
        *,
        actor: Actor,
        project_id: ProjectId,
        version_ids: Sequence[uuid.UUID] = (),
        semantic: bool | None = None,
    ) -> RunSummary:
        """Risk analysis of requirements already analysed by P5/P6 (P7).

        Architecture C.3 nodes 18-19 and the G8 part of 20. With no
        ``version_ids`` every current version is analysed. Risks are grounded in
        the evidence this project's allowlisted retrieval recorded, the severity
        of each comes only from the versioned 3x3 matrix, and G8 tasks are
        raised from the persisted severity. ``semantic`` defaults to on unless
        the gateway is the offline stub.

        A risk-only run reuses the evidence a compliance run already recorded
        for the project; it does not retrieve again. Running risk analysis on a
        project that has never had a compliance run therefore records no risks
        (nothing to cite), which the run's counters show.
        """
        if len(version_ids) > self._risk_rules.max_versions_per_run:
            raise ReqPilotError(
                f"a risk run takes at most {self._risk_rules.max_versions_per_run} versions"
            )
        use_semantic = self.semantic_default() if semantic is None else semantic
        return self._run(
            actor,
            project_id,
            {
                "domain": "",
                "scope_source_ids": [],
                "scope_version_ids": [],
                "risk_mode": True,
                "risk_version_ids": [str(v) for v in dict.fromkeys(version_ids)],
                "semantic": use_semantic,
            },
            scope={
                "mode": "risk",
                "versions": len(set(version_ids)) or "all_current",
                "semantic": use_semantic,
                "risk_rules": self._risk_rules.ruleset_ref,
                "risk_matrix": self._risk_rules.matrix.version,
            },
        )

    def classify(
        self, *, actor: Actor, project_id: ProjectId, version_ids: Sequence[uuid.UUID]
    ) -> RunSummary:
        """Classify ``EXTRACTED`` versions - after a failure, or after a merge."""
        if not version_ids:
            raise ReqPilotError("a classification run needs at least one version")
        if len(version_ids) > MAX_SCOPE_ITEMS:
            raise ReqPilotError(f"a run takes at most {MAX_SCOPE_ITEMS} versions")
        return self._run(
            actor,
            project_id,
            {
                "domain": "",
                "scope_source_ids": [],
                "scope_version_ids": [str(v) for v in dict.fromkeys(version_ids)],
            },
            scope={"mode": "classify", "versions": len(set(version_ids))},
        )

    def _run(
        self,
        actor: Actor,
        project_id: ProjectId,
        initial: dict[str, Any],
        *,
        scope: dict[str, Any],
        trigger: Action | None = None,
    ) -> RunSummary:
        run, pipeline = RunRecorder(self._session, actor).start(
            project_id, scope=scope, trigger=trigger
        )
        log = RunLog(self._session, pipeline, run)
        ledger = UsageLedger()
        context = RunContext(
            session=self._session,
            actor=pipeline,
            log=log,
            gateway=self._gateway.with_usage(ledger),
            rules=self._rules,
            quality_rules=self._quality_rules,
            embedder=self._embedder,
            compliance_rules=self._compliance_rules,
            security_rules=self._security_rules,
            retriever=self._retriever,
            risk_rules=self._risk_rules,
        )
        state: AnalysisState = {
            "run_id": str(run.id),
            "project_id": str(project_id),
            "actor_id": str(actor.actor_id),
            "errors": [],
            **initial,  # type: ignore[typeddict-item]
        }
        # A batch run lives within one request: a fresh in-memory saver offline,
        # the PostgreSQL checkpointer when configured (architecture C.7).
        saver_scope = (
            nullcontext(build_checkpointer(self._settings))
            if self._settings.checkpoint_backend == "memory"
            else checkpointer_scope(self._settings)
        )
        with saver_scope as checkpointer:
            graph = build_analysis_graph(AnalysisNodes(context), checkpointer=checkpointer)
            final = graph.invoke(state, config=run_config(GraphRunId(run.id)))

        errors = tuple(e["message"] for e in final.get("errors", []))
        status = GraphRunStatus.FAILED if errors else GraphRunStatus.COMPLETED
        log.finish(
            status,
            accepted=final.get("accepted", 0),
            merged=final.get("merged", 0),
            rejected=final.get("rejected", 0),
            classified=len(final.get("classified_version_ids", [])),
            review_items=len(final.get("review_item_ids", [])),
            quality_findings=len(final.get("quality_finding_ids", [])),
            conflicts=len(final.get("conflict_ids", [])),
            semantic_failures=final.get("semantic_failures", 0),
            compliance_mappings=len(final.get("compliance_mapping_ids", [])),
            compliance_gaps=len(final.get("compliance_gap_ids", [])),
            security_findings=len(final.get("security_finding_ids", [])),
            claims_dropped=final.get("claims_dropped", 0),
            risks=len(final.get("risk_ids", [])),
            risks_out_of_scope=final.get("risks_out_of_scope", 0),
            gate_tasks=len(final.get("pending_gate_tasks", [])),
            provider_calls=ledger.calls,
            tokens_in=ledger.tokens_in,
            tokens_out=ledger.tokens_out,
            cost_estimate=ledger.cost_estimate,
        )
        return RunSummary(
            run_id=run.id,
            status=status,
            requirement_version_ids=tuple(
                uuid.UUID(v) for v in final.get("requirement_version_ids", [])
            ),
            classified_version_ids=tuple(
                uuid.UUID(v) for v in final.get("classified_version_ids", [])
            ),
            review_item_ids=tuple(uuid.UUID(v) for v in final.get("review_item_ids", [])),
            accepted=final.get("accepted", 0),
            merged=final.get("merged", 0),
            rejected=final.get("rejected", 0),
            errors=errors,
            provider_calls=ledger.calls,
            tokens_in=ledger.tokens_in,
            tokens_out=ledger.tokens_out,
            cost_estimate=ledger.cost_estimate,
            revision_status=final.get("revision_status"),
            quality_finding_ids=tuple(uuid.UUID(v) for v in final.get("quality_finding_ids", [])),
            conflict_ids=tuple(uuid.UUID(v) for v in final.get("conflict_ids", [])),
            semantic_failures=final.get("semantic_failures", 0),
            evidence_ids=tuple(uuid.UUID(v) for v in final.get("evidence_ids", [])),
            evidence_unavailable_ids=tuple(
                uuid.UUID(v) for v in final.get("evidence_unavailable_ids", [])
            ),
            compliance_mapping_ids=tuple(
                uuid.UUID(v) for v in final.get("compliance_mapping_ids", [])
            ),
            compliance_gap_ids=tuple(uuid.UUID(v) for v in final.get("compliance_gap_ids", [])),
            security_finding_ids=tuple(uuid.UUID(v) for v in final.get("security_finding_ids", [])),
            claims_dropped=final.get("claims_dropped", 0),
            risk_ids=tuple(uuid.UUID(v) for v in final.get("risk_ids", [])),
            risks_out_of_scope=final.get("risks_out_of_scope", 0),
            gate_task_ids=tuple(
                uuid.UUID(ref["task_id"]) for ref in final.get("pending_gate_tasks", [])
            ),
        )
