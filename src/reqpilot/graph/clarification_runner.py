"""The clarification loop's entry point (architecture E #4, C.3 nodes 10-11; P4).

* **raise** - ``generate_clarifications`` (C.3 node 10) for one finding: role #4
  proposes one question, deterministic validation checks it is bound to the
  defect and targeted, and the service binds it to the version and finding. A
  refused proposal is retried within the bound, then reported - never stored.
* **answer** - the answer is persisted as an utterance, the clarification is
  closed, and ``analysis_graph`` re-analyses the requirement at once
  (``FR-CLR-003``; C.3 ``route_after_clarification``: answered ->
  ``extract_requirements``). A re-analysis failure never undoes the answer: it
  is recorded (``failed``) and can be retried.
* **dismiss** - deterministic, in the service (``FR-CLR-004``).

The wait between question and answer is the ``clarification`` row's ``open``
status in the database - a question may wait days - rather than a checkpointed
interrupt of a batch run (see ``docs/07`` for this implementation decision).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from reqpilot.agents.contracts.clarification import ClarificationInput
from reqpilot.agents.roles import ClarificationRole
from reqpilot.agents.validation import validate_clarification
from reqpilot.config import Settings, get_settings
from reqpilot.domain.enums import (
    Action,
    AgentRunStatus,
    ClarificationStatus,
    GraphRunStatus,
    ReanalysisStatus,
)
from reqpilot.domain.errors import ClarificationError, EgressRefusedError, ProjectIsolationError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.base import utc_now
from reqpilot.domain.models.elicitation import Clarification, Utterance
from reqpilot.domain.models.runs import GraphRun
from reqpilot.domain.policy import Actor
from reqpilot.domain.requirement_ids import parse_requirement_id
from reqpilot.graph.runner import AnalysisRunner, RunSummary
from reqpilot.llm.accounting import UsageLedger
from reqpilot.llm.gateway import LLMGateway
from reqpilot.repositories.requirements import RequirementRepository, RequirementVersionRepository
from reqpilot.rules.elicitation import ElicitationRules
from reqpilot.rules.extraction import ExtractionRules
from reqpilot.services.clarification import ClarificationService
from reqpilot.services.extraction import RunLog, RunRecorder, run_actor

NODE = "generate_clarifications"


@dataclass(frozen=True)
class RaiseOutcome:
    clarification: Clarification | None
    run_id: uuid.UUID
    error: str | None = None


@dataclass(frozen=True)
class AnswerOutcome:
    clarification: Clarification
    answer: Utterance
    reanalysis: RunSummary | None
    error: str | None = None


class ClarificationRunner:
    def __init__(
        self,
        session: Session,
        gateway: LLMGateway,
        rules: ElicitationRules,
        extraction_rules: ExtractionRules,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._gateway = gateway
        self._rules = rules
        self._extraction_rules = extraction_rules
        self._settings = settings or get_settings()

    def raise_for_finding(
        self,
        *,
        actor: Actor,
        project_id: ProjectId,
        finding_id: uuid.UUID,
        asked_of: uuid.UUID,
    ) -> RaiseOutcome:
        service = ClarificationService(self._session, actor, self._rules)
        context = service.prepare_raise(
            project_id=project_id, finding_id=finding_id, asked_of=asked_of
        )
        run, pipeline = RunRecorder(self._session, actor).start(
            project_id,
            scope={"mode": "clarification_question", "finding_id": str(finding_id)},
        )
        log = RunLog(self._session, pipeline, run)
        log.node_started(NODE)
        ledger = UsageLedger()
        role = ClarificationRole(self._gateway.with_usage(ledger))
        item = ClarificationInput(
            requirement_ref=context.requirement.human_id,
            statement=context.version.statement,
            original_wording=context.version.original_text,
            defect_id=str(context.finding.id),
            defect_type=str(context.finding.finding_type),
            severity=str(context.finding.severity),
            defect_rationale=context.finding.rationale,
            defect_span=context.finding.span_quote,
            prior_questions=context.prior_questions,
            synthetic=context.synthetic,
            masked=context.masked,
        )
        error = "no proposal"
        for attempt in range(1, self._rules.clarification_max_question_attempts + 1):
            started = utc_now()
            try:
                result = role.propose(item)
            except EgressRefusedError:
                error = "egress_refused"
                log.agent_run(
                    node=NODE,
                    role=role.role,
                    status=AgentRunStatus.FAILED,
                    started_at=started,
                    error_code=error,
                )
                break
            decision = (
                validate_clarification(
                    result.value,
                    defect_id=str(context.finding.id),
                    prior_questions=context.prior_questions,
                    rules=self._rules,
                )
                if result.value is not None
                else None
            )
            spec = self._gateway.prompts.get(result.meta.prompt_name)
            agent_run = log.agent_run(
                node=NODE,
                role=role.role,
                status=(
                    AgentRunStatus.OK
                    if decision is not None and decision.accepted
                    else AgentRunStatus.PARTIAL
                    if decision is not None
                    else AgentRunStatus.FAILED
                ),
                started_at=started,
                meta=result.meta,
                prompt_role=spec.role,
                prompt_text=spec.text,
                input_refs={
                    "finding_id": str(context.finding.id),
                    "requirement_version_id": str(context.version.id),
                    "attempt": attempt,
                },
                output_refs={
                    "accepted": bool(decision and decision.accepted),
                    "findings": list(decision.findings) if decision else [],
                    "output_sha256": result.output_sha256,
                },
                error_code=str(result.error_code) if result.error_code else None,
            )
            if decision is None:
                error = str(result.error_code)
                break
            if decision.accepted:
                clarification = service.record_raised(
                    context,
                    question=decision.question,
                    expected_answer_shape=decision.expected_answer_shape,
                    agent_run_id=agent_run.id,
                )
                log.node_completed(NODE, clarification=1)
                log.finish(GraphRunStatus.COMPLETED, provider_calls=ledger.calls)
                return RaiseOutcome(clarification, run.id)
            error = f"question_rejected: {decision.findings[0]}"
        log.node_failed(NODE, error[:100])
        log.finish(GraphRunStatus.FAILED, provider_calls=ledger.calls)
        return RaiseOutcome(None, run.id, error)

    def answer(
        self, *, actor: Actor, project_id: ProjectId, clarification_id: uuid.UUID, text: str
    ) -> AnswerOutcome:
        """Record the answer, then re-analyse (``FR-CLR-003``) - in that order."""
        service = ClarificationService(self._session, actor, self._rules)
        clarification, answer = service.answer(
            project_id=project_id, clarification_id=clarification_id, text=text
        )
        summary, error = self._reanalyse(actor, project_id, clarification)
        return AnswerOutcome(clarification, answer, summary, error)

    def reanalyse(
        self, *, actor: Actor, project_id: ProjectId, clarification_id: uuid.UUID
    ) -> AnswerOutcome:
        """Retry a failed re-analysis (an Analyst; the answer stands)."""
        service = ClarificationService(self._session, actor, self._rules)
        clarification = service.get(project_id, clarification_id)
        if clarification is None:
            raise ProjectIsolationError("not found")
        if clarification.status is not ClarificationStatus.ANSWERED:
            raise ClarificationError("only an answered clarification is re-analysed")
        if clarification.reanalysis_status not in (
            ReanalysisStatus.FAILED,
            ReanalysisStatus.PENDING,
        ):
            raise ClarificationError(
                f"the re-analysis has already completed ({clarification.reanalysis_status})"
            )
        answer = service.answer_utterance(clarification)
        if answer is None:  # pragma: no cover - the check constraint
            raise ClarificationError("the clarification has no answer")
        summary, error = self._reanalyse(actor, project_id, clarification, retry=True)
        return AnswerOutcome(clarification, answer, summary, error)

    def _reanalyse(
        self,
        actor: Actor,
        project_id: ProjectId,
        clarification: Clarification,
        *,
        retry: bool = False,
    ) -> tuple[RunSummary | None, str | None]:
        # The re-extraction keeps the requirement's identifier domain (FR-EXT-004).
        version = RequirementVersionRepository(self._session, actor).get(
            project_id, clarification.requirement_version_id
        )
        requirement = (
            RequirementRepository(self._session, actor).get(project_id, version.requirement_id)
            if version is not None
            else None
        )
        if requirement is None:  # pragma: no cover - foreign keys
            raise ClarificationError("the clarified requirement is missing")
        summary = AnalysisRunner(
            self._session, self._gateway, self._extraction_rules, settings=self._settings
        ).reanalyse_clarification(
            actor=actor,
            project_id=project_id,
            clarification_id=clarification.id,
            domain=parse_requirement_id(requirement.human_id).domain,
            trigger=None if retry else Action.CLARIFICATION_ANSWER,
        )
        # Recorded by the run's own system actor: bookkeeping, not a decision.
        run = self._session.get(GraphRun, summary.run_id)
        if run is None:  # pragma: no cover - the run was just created
            raise ClarificationError("the re-analysis run is missing")
        recorder = ClarificationService(self._session, run_actor(run), self._rules)
        status = (
            ReanalysisStatus(summary.revision_status)
            if summary.status is GraphRunStatus.COMPLETED and summary.revision_status
            else ReanalysisStatus.FAILED
        )
        version_id = (
            summary.requirement_version_ids[0]
            if status is ReanalysisStatus.NEW_VERSION and summary.requirement_version_ids
            else None
        )
        recorder.record_reanalysis(
            clarification, status=status, run_id=summary.run_id, version_id=version_id
        )
        error = (
            None
            if status is not ReanalysisStatus.FAILED
            else "; ".join(summary.errors) or ("the re-analysis produced no valid revision")
        )
        return summary, error
