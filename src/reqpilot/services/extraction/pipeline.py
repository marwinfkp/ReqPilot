"""From validated proposals to requirement versions (``FR-EXT-001`` to ``FR-EXT-007``).

This is the "code disposes" half of "the LLM proposes; deterministic code
disposes". It receives what validation decided (:class:`ExtractionDecision`) and
turns each accepted proposal into a requirement **through the P1 repository**:

* the identifier is allocated here, deterministically, in batch order - never
  taken from the model (``FR-EXT-004``); allocation is serialised per project on
  PostgreSQL so two runs cannot race for a number;
* the version is created by :class:`RequirementService` in ``CANDIDATE`` and moves
  to ``EXTRACTED`` only through the guarded lifecycle transition, whose guard
  requires a source reference (``FR-EXT-007``, architecture H.3);
* acceptance criteria are stored as proposals bound to that version;
* every decision - accepted, merged, rejected - is written onto the candidate,
  and every case that needs a human raises a review item.

Nothing here can approve, submit or baseline: the pipeline actor has no such
permission (policy rule 6), and no code path asks for one.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    AuditEventType,
    CandidateStatus,
    ProposalSource,
    ReviewReason,
)
from reqpilot.domain.errors import ExtractionError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.base import utc_now
from reqpilot.domain.models.extraction import AcceptanceCriterion, ExtractionCandidate
from reqpilot.domain.models.requirements import Requirement, RequirementVersion
from reqpilot.domain.policy import Actor
from reqpilot.domain.proposals import (
    ExtractionDecision,
    Finding,
    FindingCode,
    ProposalRecord,
    RecordedCandidate,
    ValidatedCandidate,
)
from reqpilot.domain.requirement_ids import next_requirement_id, normalise_domain
from reqpilot.domain.similarity import is_exact_duplicate, token_jaccard
from reqpilot.repositories.extraction import AcceptanceCriterionRepository, CandidateRepository
from reqpilot.repositories.requirements import RequirementRepository, RequirementVersionRepository
from reqpilot.rules.extraction import ExtractionRules
from reqpilot.services.audit import AuditService
from reqpilot.services.requirements import RequirementContent, RequirementService
from reqpilot.services.review.queue import ReviewQueue

#: Versions in these states no longer stand for a live requirement, so they are
#: not compared against for duplicates.
_INACTIVE = frozenset(
    {
        RequirementState.WITHDRAWN,
        RequirementState.INVALID,
        RequirementState.SUPERSEDED,
        RequirementState.REJECTED,
    }
)

#: Namespace of the per-project advisory lock serialising id allocation.
_ID_LOCK_NAMESPACE = 5_210_403


@dataclass
class PersistOutcome:
    """What one extraction run persisted."""

    version_ids: list[uuid.UUID] = field(default_factory=list)
    human_ids: list[str] = field(default_factory=list)
    accepted: int = 0
    merged: int = 0
    rejected: int = 0
    review_item_ids: list[uuid.UUID] = field(default_factory=list)


class ExtractionService:
    """Records proposals and applies validation's decision, for one run."""

    def __init__(self, session: Session, actor: Actor, rules: ExtractionRules) -> None:
        self._session = session
        self._actor = actor
        self._rules = rules
        self._candidates = CandidateRepository(session, actor)
        self._criteria = AcceptanceCriterionRepository(session, actor)
        self._requirements = RequirementRepository(session, actor)
        self._versions = RequirementVersionRepository(session, actor)
        self._audit = AuditService(session)
        self._queue = ReviewQueue(session, actor)

    # -- step 1: record what the model proposed ------------------------------
    def record_proposals(
        self,
        *,
        project_id: ProjectId,
        graph_run_id: uuid.UUID,
        agent_run_id: uuid.UUID,
        window: int,
        first_ordinal: int,
        proposals: Sequence[ProposalRecord],
    ) -> list[RecordedCandidate]:
        """Store every schema-valid proposal, before any is judged.

        Keys are namespaced by window (``w1:c1``), so keys the model reuses in a
        later window cannot collide; a key reused *within* one output is kept
        distinct and flagged, and validation rejects the repeat.
        """
        recorded: list[RecordedCandidate] = []
        rows: list[ExtractionCandidate] = []
        seen: dict[str, int] = {}
        for offset, proposal in enumerate(proposals):
            count = seen.get(proposal.model_key, 0)
            seen[proposal.model_key] = count + 1
            run_key = f"w{window}:{proposal.model_key}" + (f"#{count + 1}" if count else "")
            row = ExtractionCandidate(
                id=uuid.uuid4(),
                project_id=project_id,
                graph_run_id=graph_run_id,
                agent_run_id=agent_run_id,
                candidate_key=run_key,
                ordinal=first_ordinal + offset,
                statement=proposal.statement,
                requirement_kind=proposal.kind,
                proposal=proposal.proposal,
                review_signal=proposal.review_signal,
                status=CandidateStatus.PROPOSED,
            )
            rows.append(row)
            recorded.append(
                RecordedCandidate(
                    candidate_id=row.id,
                    run_key=run_key,
                    model_key=proposal.model_key,
                    ordinal=row.ordinal,
                    key_collision=count > 0,
                )
            )
        self._candidates.add_all(project_id, rows)
        self._audit.append(
            event_type=AuditEventType.EXTRACTION_PROPOSED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="graph_run",
            subject_id=str(graph_run_id),
            graph_run_id=graph_run_id,
            agent_run_id=agent_run_id,
            payload={"window": window, "proposal_count": len(rows)},
        )
        return recorded

    # -- step 2: apply validation's decision ---------------------------------
    def apply(
        self,
        *,
        project_id: ProjectId,
        graph_run_id: uuid.UUID,
        domain: str,
        decision: ExtractionDecision,
    ) -> PersistOutcome:
        domain = normalise_domain(domain)
        outcome = PersistOutcome()
        candidates = {c.id: c for c in self._candidates.list_for_run(project_id, graph_run_id)}
        existing = self._live_versions(project_id)
        self._lock_id_allocation(project_id)

        # Identifiers first, in batch order, so dependencies can name each other.
        accepted = sorted(decision.accepted, key=lambda c: c.ordinal)
        used_ids = self._requirements.existing_human_ids(project_id)
        human_ids: dict[str, str] = {}
        for candidate in accepted:
            human_id = next_requirement_id(candidate.kind, domain, used_ids)
            used_ids.append(human_id)
            human_ids[candidate.candidate_key] = human_id

        service = RequirementService(self._session, self._actor)
        version_by_key: dict[str, RequirementVersion] = {}
        for candidate in accepted:
            findings = list(candidate.findings)
            dependencies = []
            for key in candidate.depends_on_keys:
                if key in human_ids:
                    dependencies.append(human_ids[key])
                else:
                    findings.append(
                        Finding(
                            FindingCode.UNKNOWN_DEPENDENCY,
                            f"dependency {key} was not accepted, so it was dropped",
                        )
                    )
            _requirement, version = service.create_requirement(
                project_id=project_id,
                domain=domain,
                kind=candidate.kind,
                human_id=human_ids[candidate.candidate_key],
                content=RequirementContent(
                    statement=candidate.statement,
                    original_text=candidate.original_text,
                    priority=candidate.priority,
                    justification=candidate.justification,
                    dependencies=tuple(dependencies),
                    assumptions=candidate.assumptions,
                    source_refs=candidate.source_refs(),
                    review_signal=candidate.review_signal,
                ),
            )
            row = candidates[candidate.candidate_id]
            self._store_criteria(project_id, version, candidate, row.agent_run_id)
            # CANDIDATE -> EXTRACTED only through the guarded transition (H.3).
            service.transition(
                project_id=project_id, version_id=version.id, target=RequirementState.EXTRACTED
            )
            self._decide(
                row,
                CandidateStatus.ACCEPTED,
                findings,
                spans=[s.as_source_ref() for s in candidate.spans],
                original_text=candidate.original_text,
                requirement_version_id=version.id,
            )
            for merged_id in candidate.merged_candidate_ids:
                merged = candidates[merged_id]
                self._decide(
                    merged,
                    CandidateStatus.MERGED,
                    [
                        Finding(
                            FindingCode.EXACT_DUPLICATE_MERGED,
                            f"merged into {candidate.candidate_key}; its sources were kept there",
                        )
                    ],
                    merged_into_id=candidate.candidate_id,
                )
                outcome.merged += 1
            version_by_key[candidate.candidate_key] = version
            outcome.version_ids.append(version.id)
            outcome.human_ids.append(human_ids[candidate.candidate_key])
            outcome.accepted += 1
            self._raise_for_accepted(project_id, graph_run_id, candidate, version, row, outcome)

        for rejected in decision.rejected:
            row = candidates[rejected.candidate_id]
            self._decide(
                row,
                CandidateStatus.REJECTED,
                list(rejected.findings),
                spans=[s.as_source_ref() for s in rejected.spans],
            )
            outcome.rejected += 1
            item = self._queue.raise_item(
                project_id=project_id,
                reason=rejected.reason,
                subject_type="extraction_candidate",
                subject_id=row.id,
                review_signal=row.review_signal,
                graph_run_id=graph_run_id,
                agent_run_id=row.agent_run_id,
                detail={"codes": sorted({str(f.code) for f in rejected.findings})},
            )
            outcome.review_item_ids.append(item.id)

        self._raise_duplicates(
            project_id, graph_run_id, decision, version_by_key, existing, outcome
        )

        self._audit.append(
            event_type=AuditEventType.EXTRACTION_VALIDATED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type="graph_run",
            subject_id=str(graph_run_id),
            graph_run_id=graph_run_id,
            payload={
                "accepted": outcome.accepted,
                "merged": outcome.merged,
                "rejected": outcome.rejected,
                "human_ids": outcome.human_ids,
                "review_items": len(outcome.review_item_ids),
                "ruleset_version": decision.ruleset_version,
            },
        )
        return outcome

    # -- internals ---------------------------------------------------------
    def _decide(
        self,
        row: ExtractionCandidate,
        status: CandidateStatus,
        findings: Sequence[Finding],
        *,
        spans: list | None = None,
        original_text: str | None = None,
        requirement_version_id: uuid.UUID | None = None,
        merged_into_id: uuid.UUID | None = None,
    ) -> None:
        if row.status is not CandidateStatus.PROPOSED:
            raise ExtractionError("an extraction candidate is decided once")
        row.status = status
        row.findings = [f.as_dict() for f in findings]
        row.spans = spans or []
        row.original_text = original_text
        row.requirement_version_id = requirement_version_id
        row.merged_into_id = merged_into_id
        row.decided_at = utc_now()
        self._candidates.record_decision(ProjectId(row.project_id), row)

    def _store_criteria(
        self,
        project_id: ProjectId,
        version: RequirementVersion,
        candidate: ValidatedCandidate,
        agent_run_id: uuid.UUID,
    ) -> None:
        if not candidate.criteria:
            return
        self._criteria.add_all(
            project_id,
            [
                AcceptanceCriterion(
                    project_id=project_id,
                    requirement_version_id=version.id,
                    ordinal=index,
                    given_text=criterion.given,
                    when_text=criterion.when,
                    then_text=criterion.then,
                    source=ProposalSource.AGENT,
                    agent_run_id=agent_run_id,
                )
                for index, criterion in enumerate(candidate.criteria, start=1)
            ],
        )

    def _raise_for_accepted(
        self,
        project_id: ProjectId,
        graph_run_id: uuid.UUID,
        candidate: ValidatedCandidate,
        version: RequirementVersion,
        row: ExtractionCandidate,
        outcome: PersistOutcome,
    ) -> None:
        reasons: list[tuple[ReviewReason, list[str]]] = []
        if candidate.low_signal:
            reasons.append((ReviewReason.LOW_EXTRACTION_SIGNAL, ["low_review_signal"]))
        if candidate.criteria_rejected:
            codes = sorted(
                {
                    str(f.code)
                    for f in candidate.findings
                    if f.code in {FindingCode.CRITERION_INVALID, FindingCode.TOO_MANY_CRITERIA}
                }
            )
            reasons.append((ReviewReason.ACCEPTANCE_CRITERIA_INVALID, codes))
        if candidate.dropped_evidence:
            codes = sorted(
                {
                    str(f.code)
                    for f in candidate.findings
                    if f.code
                    in {
                        FindingCode.UNKNOWN_SEGMENT,
                        FindingCode.QUOTE_NOT_FOUND,
                        FindingCode.QUOTE_TOO_SHORT,
                    }
                }
            )
            reasons.append((ReviewReason.UNRESOLVED_SOURCE, codes))
        for reason, codes in reasons:
            item = self._queue.raise_item(
                project_id=project_id,
                reason=reason,
                subject_type="requirement_version",
                subject_id=version.id,
                requirement_version_id=version.id,
                review_signal=candidate.review_signal,
                graph_run_id=graph_run_id,
                agent_run_id=row.agent_run_id,
                detail={"codes": codes, "candidate": candidate.candidate_key},
            )
            outcome.review_item_ids.append(item.id)

    def _raise_duplicates(
        self,
        project_id: ProjectId,
        graph_run_id: uuid.UUID,
        decision: ExtractionDecision,
        version_by_key: dict[str, RequirementVersion],
        existing: list[tuple[Requirement, RequirementVersion]],
        outcome: PersistOutcome,
    ) -> None:
        """Ask a human about look-alikes. Code never merges these (FR-EXT-005)."""
        for pair in decision.near_duplicates:
            a, b = version_by_key.get(pair.key_a), version_by_key.get(pair.key_b)
            if a is None or b is None:
                continue
            item = self._queue.raise_item(
                project_id=project_id,
                reason=ReviewReason.POSSIBLE_DUPLICATE,
                subject_type="requirement_version",
                subject_id=b.id,
                requirement_version_id=b.id,
                related_subject_id=a.id,
                graph_run_id=graph_run_id,
                detail={"similarity": pair.similarity, "basis": pair.basis},
            )
            outcome.review_item_ids.append(item.id)

        # Against requirements that existed before this run.
        for version in version_by_key.values():
            for _requirement, other in existing:
                exact = is_exact_duplicate(version.statement, other.statement)
                similarity = token_jaccard(version.statement, other.statement)
                if not exact and similarity < self._rules.duplicate_review_similarity:
                    continue
                item = self._queue.raise_item(
                    project_id=project_id,
                    reason=ReviewReason.POSSIBLE_DUPLICATE,
                    subject_type="requirement_version",
                    subject_id=version.id,
                    requirement_version_id=version.id,
                    related_subject_id=other.id,
                    graph_run_id=graph_run_id,
                    detail={
                        "similarity": 1.0 if exact else round(similarity, 4),
                        "basis": "existing",
                    },
                )
                outcome.review_item_ids.append(item.id)

    def _live_versions(self, project_id: ProjectId) -> list[tuple[Requirement, RequirementVersion]]:
        live: list[tuple[Requirement, RequirementVersion]] = []
        for requirement in self._requirements.list_for_project(project_id):
            if requirement.current_version_id is None:
                continue
            version = self._versions.get(project_id, requirement.current_version_id)
            if version is not None and version.state not in _INACTIVE:
                live.append((requirement, version))
        return live

    def _lock_id_allocation(self, project_id: ProjectId) -> None:
        """Serialise identifier allocation per project, for this transaction.

        On PostgreSQL a transaction-scoped advisory lock; SQLite serialises
        writers anyway. The unique constraint on ``(project_id, human_id)`` stays
        the last line of defence either way.
        """
        if self._session.get_bind().dialect.name != "postgresql":
            return
        key = int.from_bytes(project_id.bytes[:4], "big", signed=True)
        self._session.execute(
            text("SELECT pg_advisory_xact_lock(:namespace, :key)"),
            {"namespace": _ID_LOCK_NAMESPACE, "key": key},
        )
