"""Human risk management: add, accept, mitigate, reject, close (``FR-RSK-010``).

Architecture I.4: "Only a human moves a risk out of ``UNDER_REVIEW``." This
module is that path, and it is the **only** one besides a G8 decision. Every
method here:

* requires ``RISK_MANAGE``, which is human-only in the policy (rule 10) - no
  agent actor can reach it whatever roles it carries;
* requires a recorded rationale for any decision that changes a risk's status,
  because ``FR-RSK-010`` asks for the rationale, not merely the decision;
* moves the status only along the approved path, which the ORM guard enforces
  independently;
* audits what happened as references and codes.

What this module deliberately **cannot** do:

* write or change a severity. A human-added risk is rated by the same matrix
  from the same two ordinal ratings; there is no parameter for a severity here
  either. A person who disagrees with a rating records a different *rating*, and
  the matrix re-derives the severity - which is the point of having a published
  matrix at all.
* close a G8 task. A blocking G8 task is cleared by a decision at the gate,
  through the approval service. Accepting a risk here moves the register entry;
  it does not decide the gate, and the baseline guard still counts the open
  blocking task.
* bypass the ``FR-RSK-011`` scope guard. A human-entered risk goes through the
  same refusal as a model's.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    Action,
    AuditEventType,
    FindingDetector,
    MitigationStatus,
    ResourceType,
    RiskCategory,
    RiskImpact,
    RiskLikelihood,
    RiskScope,
    RiskStatus,
)
from reqpilot.domain.errors import ReqPilotError, ScopeGuardError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.risk import Risk, RiskMitigation
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.domain.risk.hashing import risk_hash
from reqpilot.domain.risk.matrix import compute_severity
from reqpilot.domain.risk.scope import SCOPE_REFUSAL_NOTICE, SCOPE_RULES_VERSION, check_fields
from reqpilot.repositories.requirements import RequirementVersionRepository
from reqpilot.repositories.risk import RiskMitigationRepository, RiskRepository
from reqpilot.rules.risk import RiskRules, packaged_risk_rules
from reqpilot.services.audit import AuditService

#: The status moves a human may make, and what each one means. ``CLOSED`` is the
#: end of the line for an entry that needs no further attention.
_HUMAN_MOVES: frozenset[RiskStatus] = frozenset(
    {RiskStatus.ACCEPTED, RiskStatus.MITIGATED, RiskStatus.REJECTED, RiskStatus.CLOSED}
)


class RiskService:
    """The human half of the register (``FR-RSK-010``, ``FR-RSK-005``)."""

    def __init__(self, session: Session, actor: Actor, rules: RiskRules | None = None) -> None:
        self._session = session
        self._actor = actor
        self._rules = rules or packaged_risk_rules()
        self._risks = RiskRepository(session, actor)
        self._mitigations = RiskMitigationRepository(session, actor)
        self._versions = RequirementVersionRepository(session, actor)
        self._audit = AuditService(session)

    def _authorise(self, project_id: ProjectId) -> None:
        require(
            self._actor,
            Action.RISK_MANAGE,
            ResourceRef(resource_type=ResourceType.RISK, project_id=project_id),
        )

    # ------------------------------------------------------------------
    # adding a risk by hand
    # ------------------------------------------------------------------
    def add_risk(
        self,
        *,
        project_id: ProjectId,
        category: RiskCategory,
        title: str,
        description: str,
        likelihood: RiskLikelihood,
        impact: RiskImpact,
        likelihood_rationale: str,
        impact_rationale: str,
        evidence_ids: list[uuid.UUID],
        requirement_version_id: uuid.UUID | None = None,
        mitigation: str | None = None,
    ) -> Risk:
        """A human adding a risk the analysis did not find (``FR-RSK-010``).

        Rated by the same matrix as any other risk, refused by the same scope
        guard, and grounded in the same project evidence. A human-added HIGH
        risk goes into ``UNDER_REVIEW`` and raises G8 exactly like a
        pipeline-identified one; the person who entered it is not thereby the
        person who reviewed it.
        """
        self._authorise(project_id)
        if not evidence_ids:
            raise ReqPilotError("FR-RSK-006: a risk must link to at least one piece of evidence")
        hits = check_fields(
            {
                "title": title,
                "description": description,
                "likelihood_rationale": likelihood_rationale,
                "impact_rationale": impact_rationale,
                "mitigation": mitigation,
            }
        )
        if hits:
            self._audit.append(
                event_type=AuditEventType.RISK_DROPPED,
                actor_kind=self._actor.kind,
                actor_ref=str(self._actor.actor_id),
                project_id=project_id,
                subject_type=ResourceType.RISK.value,
                subject_id=str(project_id),
                payload={
                    "reason": "out_of_scope_borrower_risk",
                    "detail": "a human-entered risk was refused by the FR-RSK-011 scope guard",
                    "rule_ids": sorted({h.rule_id for h in hits}),
                    "fields": sorted({h.field for h in hits}),
                    "scope_rules_version": SCOPE_RULES_VERSION,
                    "scope_guard_refusal": True,
                },
            )
            raise ScopeGuardError(SCOPE_REFUSAL_NOTICE)

        scope = RiskScope.REQUIREMENT if requirement_version_id else RiskScope.PROJECT
        version = (
            self._versions.get(project_id, requirement_version_id)
            if requirement_version_id
            else None
        )
        if requirement_version_id and version is None:
            raise ReqPilotError("requirement version not found in this project")

        computation = compute_severity(self._rules.matrix, likelihood, impact)
        title_clean = " ".join(title.split())
        key = title_clean.casefold()[:300]
        content_hash = risk_hash(
            project_id=str(project_id),
            scope=str(scope),
            requirement_version_id=str(requirement_version_id) if requirement_version_id else None,
            version_content_hash=version.content_hash if version else None,
            category=str(category),
            title=title_clean,
            description=" ".join(description.split()),
            likelihood=str(likelihood),
            impact=str(impact),
            severity=str(computation.severity),
            matrix_version=computation.matrix_version,
            likelihood_rationale=" ".join(likelihood_rationale.split()),
            impact_rationale=" ".join(impact_rationale.split()),
            evidence_ids=[str(e) for e in evidence_ids],
        )
        risk = self._risks.add(
            Risk(
                project_id=project_id,
                scope=scope,
                requirement_version_id=requirement_version_id,
                category=category,
                title=title_clean,
                title_key=key,
                description=" ".join(description.split()),
                likelihood=computation.likelihood,
                impact=computation.impact,
                severity=computation.severity,
                matrix_version=computation.matrix_version,
                likelihood_rationale=" ".join(likelihood_rationale.split()),
                impact_rationale=" ".join(impact_rationale.split()),
                citations=[],
                evidence_count=len(set(evidence_ids)),
                detected_by=FindingDetector.HUMAN,
                scope_rules_version=SCOPE_RULES_VERSION,
                rules_version=self._rules.ruleset_ref,
                content_hash=content_hash,
                recorded_by=self._actor.actor_id,
                owner_role=self._rules.owner_for(category),
                status=(
                    RiskStatus.UNDER_REVIEW if computation.requires_gate else RiskStatus.PROPOSED
                ),
            ),
            evidence_ids,
            (
                [
                    RiskMitigation(
                        suggestion=" ".join(mitigation.split()),
                        is_ai_generated=False,
                        status=MitigationStatus.ACCEPTED,
                        accepted_by=self._actor.actor_id,
                    )
                ]
                if mitigation
                else []
            ),
        )
        self._audit.append(
            event_type=AuditEventType.RISK_RECORDED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type=ResourceType.RISK.value,
            subject_id=str(risk.id),
            payload={
                "scope": str(risk.scope),
                "category": str(risk.category),
                "detected_by": str(risk.detected_by),
                "severity": str(risk.severity),
                "matrix_version": risk.matrix_version,
                "explanation": computation.explanation,
            },
        )
        return risk

    # ------------------------------------------------------------------
    # deciding a risk
    # ------------------------------------------------------------------
    def decide(
        self,
        *,
        project_id: ProjectId,
        risk_id: uuid.UUID,
        status: RiskStatus,
        rationale: str,
    ) -> Risk:
        """Accept, mitigate, reject or close a risk, with a recorded rationale.

        A blocking G8 task is **not** closed by this: a HIGH risk is reviewed at
        the gate, by the gate's role, through the approval service. Moving the
        register entry here does not decide that gate, and the requirement stays
        blocked until it is decided.
        """
        self._authorise(project_id)
        if status not in _HUMAN_MOVES:
            raise ReqPilotError(
                f"a human may move a risk to {sorted(str(s) for s in _HUMAN_MOVES)}, not {status}"
            )
        if not rationale or not rationale.strip():
            raise ReqPilotError("FR-RSK-010 requires a recorded rationale for a risk decision")
        risk = self._risks.get(project_id, risk_id)
        if risk is None:
            raise ReqPilotError("risk not found in this project")
        previous = risk.status
        self._risks.record_status(
            risk, status, rationale=" ".join(rationale.split()), decided_by=self._actor.actor_id
        )
        self._audit.append(
            event_type=AuditEventType.RISK_DECISION_RECORDED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type=ResourceType.RISK.value,
            subject_id=str(risk.id),
            payload={
                "from": str(previous),
                "to": str(status),
                "severity": str(risk.severity),
                "rationale_recorded": True,
                "via_gate": None,
                "gate_task_id": str(risk.approval_task_id) if risk.approval_task_id else None,
            },
        )
        return risk

    # ------------------------------------------------------------------
    # mitigations (FR-RSK-005)
    # ------------------------------------------------------------------
    def decide_mitigation(
        self,
        *,
        project_id: ProjectId,
        mitigation_id: uuid.UUID,
        status: MitigationStatus,
        rationale: str | None = None,
    ) -> RiskMitigation:
        """Accept or reject an AI-suggested mitigation. Until then it stays a suggestion."""
        self._authorise(project_id)
        if status is MitigationStatus.SUGGESTED:
            raise ReqPilotError("a mitigation cannot be moved back to suggested")
        mitigation = self._mitigations.get(project_id, mitigation_id)
        if mitigation is None:
            raise ReqPilotError("mitigation not found in this project")
        self._mitigations.decide(
            mitigation, status, actor_id=self._actor.actor_id, rationale=rationale
        )
        self._audit.append(
            event_type=AuditEventType.RISK_MITIGATION_DECIDED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type=ResourceType.RISK_MITIGATION.value,
            subject_id=str(mitigation.id),
            payload={
                "risk_id": str(mitigation.risk_id),
                "status": str(status),
                "was_ai_generated": mitigation.is_ai_generated,
                "rationale_recorded": bool(rationale),
            },
        )
        return mitigation

    def add_mitigation(
        self, *, project_id: ProjectId, risk_id: uuid.UUID, suggestion: str
    ) -> RiskMitigation:
        """A human writing their own mitigation. Recorded as human, accepted on entry."""
        self._authorise(project_id)
        risk = self._risks.get(project_id, risk_id)
        if risk is None:
            raise ReqPilotError("risk not found in this project")
        hits = check_fields({"mitigation": suggestion})
        if hits:
            raise ScopeGuardError(SCOPE_REFUSAL_NOTICE)
        mitigation = self._mitigations.add_human(
            RiskMitigation(
                project_id=project_id,
                risk_id=risk.id,
                suggestion=" ".join(suggestion.split()),
                accepted_by=self._actor.actor_id,
                status=MitigationStatus.ACCEPTED,
            )
        )
        self._audit.append(
            event_type=AuditEventType.RISK_MITIGATION_DECIDED,
            actor_kind=self._actor.kind,
            actor_ref=str(self._actor.actor_id),
            project_id=project_id,
            subject_type=ResourceType.RISK_MITIGATION.value,
            subject_id=str(mitigation.id),
            payload={
                "risk_id": str(risk.id),
                "status": str(mitigation.status),
                "authored": "human",
            },
        )
        return mitigation
