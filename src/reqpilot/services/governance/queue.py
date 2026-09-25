"""The single review queue (``FR-HIL-006``; architecture M.5).

One queue, not several: open approval tasks at every gate, AI proposals awaiting
a human look (the P3 review items), open quality findings, open conflicts,
risks that no human has decided yet, and gates whose trigger holds but whose
tasks were never raised. Each becomes a :class:`QueueEntry`, and the entries are
ordered by :func:`reqpilot.domain.governance.queue_sort_key` - blocking first,
then **risk severity**, then **confidence** (lowest first), then age, then id.

**Severity** is the most authoritative severity that applies to the item, never a
model's opinion: a risk's matrix-computed severity (P7); a derived security or
privacy requirement's deterministically evaluated level (P6, I.7); otherwise the
highest P7 severity recorded on the requirement version(s) the item concerns;
otherwise a quality finding's or conflict's own P5 severity. **Confidence** is the
item's persisted review signal - a heuristic prioritisation signal, not a
probability (approved Phase 0 H.1) - and an item with none sorts as the least
confident.

**The queue has no authority.** It is presentation and work management. An
approval task listed here is decided only through ``ApprovalService.decide``;
a finding, conflict or risk only through the service that owns it. Which items a
viewer sees follows the policy (a Stakeholder does not see the AI-proposal
review items it may not read), and ``actionable`` marks what the viewer could
act on - it does not grant anything.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    BLOCKING_CONFLICT_STATUSES,
    UNREVIEWED_RISK_STATUSES,
    Action,
    ConflictStatus,
    Gate,
    QualityFindingStatus,
    ResourceType,
    ReviewStatus,
    RiskSeverity,
    Role,
)
from reqpilot.domain.governance import SEVERITY_ORDER, QueueEntry, queue_sort_key
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.lifecycle.states import PRE_APPROVAL_STATES
from reqpilot.domain.models.approval import ApprovalTask
from reqpilot.domain.models.compliance import ComplianceMapping, SecurityPrivacyFinding
from reqpilot.domain.models.quality import Conflict
from reqpilot.domain.models.risk import Risk
from reqpilot.domain.policy import Actor, ResourceRef, can, require
from reqpilot.repositories.approval import ApprovalTaskRepository
from reqpilot.repositories.compliance import (
    ComplianceMappingRepository,
    SecurityFindingRepository,
)
from reqpilot.repositories.elicitation import QualityFindingRepository
from reqpilot.repositories.extraction import ReviewItemRepository
from reqpilot.repositories.quality import ConflictRepository
from reqpilot.repositories.requirements import (
    RequirementRepository,
    RequirementVersionRepository,
)
from reqpilot.repositories.risk import RiskRepository
from reqpilot.services.governance.readiness import GovernanceReadinessService

#: Maps a requirement version id to its display label ("FR-LOAN-001 v2").
Labeller = Callable[[uuid.UUID | None], str | None]

#: The documented ordering, shown on the queue page and in the API.
ORDERING_RULE = (
    "blocking items first; then higher risk severity; then lower confidence (a missing "
    "review signal counts as the lowest); then older; then kind and id"
)


@dataclass(frozen=True)
class ReviewQueueView:
    project_id: ProjectId
    entries: tuple[QueueEntry, ...]
    ordering_rule: str = ORDERING_RULE

    @property
    def actionable(self) -> tuple[QueueEntry, ...]:
        return tuple(e for e in self.entries if e.actionable)


def _max_severity(values: Iterable[str | None]) -> str | None:
    best: str | None = None
    for value in values:
        if SEVERITY_ORDER.get(value, 0) > SEVERITY_ORDER.get(best, 0):
            best = value
    return best


class UnifiedReviewQueue:
    """Builds the one review queue for a project. Reads only."""

    def __init__(self, session: Session, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._tasks = ApprovalTaskRepository(session, actor)
        self._versions = RequirementVersionRepository(session, actor)
        self._requirements = RequirementRepository(session, actor)

    def _can(self, action: Action, project_id: ProjectId) -> bool:
        return can(
            self._actor,
            action,
            ResourceRef(resource_type=ResourceType.PROJECT, project_id=project_id),
        ).allowed

    def build(self, project_id: ProjectId, *, mine_only: bool = False) -> ReviewQueueView:
        require(
            self._actor,
            Action.GOVERNANCE_READ,
            ResourceRef(resource_type=ResourceType.PROJECT, project_id=project_id),
        )
        roles = self._actor.roles_in(project_id)
        risks = (
            RiskRepository(self._session, self._actor).list_for_project(project_id)
            if self._can(Action.RISK_READ, project_id)
            else []
        )
        version_severity: dict[uuid.UUID, str] = {}
        for risk in risks:
            if risk.requirement_version_id is not None:
                current = version_severity.get(risk.requirement_version_id)
                version_severity[risk.requirement_version_id] = _max_severity(
                    [current, str(risk.severity)]
                ) or str(risk.severity)
        labels: dict[uuid.UUID, str] = {}

        def label(version_id: uuid.UUID | None) -> str | None:
            if version_id is None:
                return None
            if version_id not in labels:
                version = self._versions.get(project_id, version_id)
                if version is None:
                    return None
                requirement = self._requirements.get(project_id, version.requirement_id)
                human = requirement.human_id if requirement else "?"
                labels[version_id] = f"{human} v{version.version_no}"
            return labels[version_id]

        entries: list[QueueEntry] = []
        entries.extend(self._task_entries(project_id, roles, risks, version_severity, label))
        entries.extend(self._finding_entries(project_id, version_severity, label))
        entries.extend(self._conflict_entries(project_id, version_severity, label, roles))
        entries.extend(self._risk_entries(project_id, risks, label, roles))
        entries.extend(self._review_item_entries(project_id, version_severity, label, roles))
        entries.extend(self._unraised_gate_entries(project_id, version_severity, label, roles))
        if mine_only:
            entries = [e for e in entries if e.actionable]
        entries.sort(key=queue_sort_key)
        return ReviewQueueView(project_id=project_id, entries=tuple(entries))

    # ------------------------------------------------------------------
    def _task_entries(
        self,
        project_id: ProjectId,
        roles: frozenset[Role],
        risks: list[Risk],
        version_severity: dict[uuid.UUID, str],
        label: Labeller,
    ) -> list[QueueEntry]:
        if not self._can(Action.APPROVAL_TASK_READ, project_id):
            return []
        risk_by_id = {r.id: r for r in risks}
        mappings = (
            {
                m.id: m
                for m in ComplianceMappingRepository(self._session, self._actor).list_for_project(
                    project_id
                )
            }
            if self._can(Action.COMPLIANCE_READ, project_id)
            else {}
        )
        findings = (
            {
                f.id: f
                for f in SecurityFindingRepository(self._session, self._actor).list_for_project(
                    project_id
                )
            }
            if self._can(Action.SECURITY_READ, project_id)
            else {}
        )
        conflicts = (
            {
                c.id: c
                for c in ConflictRepository(self._session, self._actor).list_for_project(project_id)
            }
            if self._can(Action.CONFLICT_READ, project_id)
            else {}
        )
        out: list[QueueEntry] = []
        for task in self._tasks.list_open(project_id):
            severity, signal, subject = self._task_context(
                task, risk_by_id, mappings, findings, conflicts, version_severity, label
            )
            actionable = task.required_role in roles and (
                task.assignee_user_id is None or task.assignee_user_id == self._actor.actor_id
            )
            out.append(
                QueueEntry(
                    kind="approval_task",
                    item_id=str(task.id),
                    title=f"{task.gate.value} - needs {task.required_role.value}",
                    project_id=str(project_id),
                    blocking=bool(task.blocking),
                    severity=severity,
                    review_signal=signal,
                    created_at=task.created_at,
                    gate=task.gate,
                    required_role=task.required_role.value,
                    subject_type=task.subject_type,
                    subject_id=str(task.subject_id),
                    subject_label=subject,
                    actionable=actionable,
                    link=f"/ui/projects/{project_id}/tasks",
                    detail={"assigned": "yes" if task.assignee_user_id else "no"},
                )
            )
        return out

    @staticmethod
    def _task_context(
        task: ApprovalTask,
        risks: dict[uuid.UUID, Risk],
        mappings: dict[uuid.UUID, ComplianceMapping],
        findings: dict[uuid.UUID, SecurityPrivacyFinding],
        conflicts: dict[uuid.UUID, Conflict],
        version_severity: dict[uuid.UUID, str],
        label: Labeller,
    ) -> tuple[str | None, float | None, str | None]:
        if task.gate is Gate.G8_HIGH_SEVERITY_RISK and task.subject_id in risks:
            risk = risks[task.subject_id]
            return str(risk.severity), risk.review_signal, risk.title
        if task.gate is Gate.G3_HIGH_RISK_SECURITY and task.subject_id in findings:
            finding = findings[task.subject_id]
            return (
                str(finding.risk_level),
                finding.review_signal,
                label(finding.requirement_version_id),
            )
        if task.gate is Gate.G2_REGULATORY_INTERPRETATION and task.subject_id in mappings:
            mapping = mappings[task.subject_id]
            return (
                version_severity.get(mapping.requirement_version_id),
                mapping.review_signal,
                label(mapping.requirement_version_id),
            )
        if task.gate is Gate.G4_STAKEHOLDER_CONFLICT and task.subject_id in conflicts:
            conflict = conflicts[task.subject_id]
            severity = _max_severity(
                [
                    version_severity.get(conflict.version_a_id),
                    version_severity.get(conflict.version_b_id),
                ]
            ) or str(conflict.severity)
            return (
                severity,
                conflict.review_signal,
                f"{label(conflict.version_a_id)} vs {label(conflict.version_b_id)}",
            )
        return version_severity.get(task.subject_id), None, label(task.subject_id)

    def _finding_entries(
        self, project_id: ProjectId, version_severity: dict[uuid.UUID, str], label: Labeller
    ) -> list[QueueEntry]:
        if not self._can(Action.QUALITY_FINDING_READ, project_id):
            return []
        repo = QualityFindingRepository(self._session, self._actor)
        analyst = Role.ANALYST in self._actor.roles_in(project_id)
        return [
            QueueEntry(
                kind="quality_finding",
                item_id=str(f.id),
                title=f"{f.finding_type.value} finding",
                project_id=str(project_id),
                blocking=True,
                severity=version_severity.get(f.requirement_version_id) or str(f.severity),
                review_signal=f.review_signal,
                created_at=f.created_at,
                subject_type="requirement_version",
                subject_id=str(f.requirement_version_id),
                subject_label=label(f.requirement_version_id),
                actionable=analyst,
                link=f"/ui/projects/{project_id}/quality",
            )
            for f in repo.list_for_project(project_id, status=QualityFindingStatus.OPEN)
        ]

    def _conflict_entries(
        self,
        project_id: ProjectId,
        version_severity: dict[uuid.UUID, str],
        label: Labeller,
        roles: frozenset[Role],
    ) -> list[QueueEntry]:
        if not self._can(Action.CONFLICT_READ, project_id):
            return []
        repo = ConflictRepository(self._session, self._actor)
        out = []
        for c in repo.list_for_project(project_id):
            if c.status not in BLOCKING_CONFLICT_STATUSES:
                continue
            out.append(
                QueueEntry(
                    kind="conflict",
                    item_id=str(c.id),
                    title=f"{c.conflict_class.value} {c.kind.value} conflict"
                    + (
                        " (stakeholder disagreement: G4)"
                        if c.involves_stakeholder_disagreement
                        else ""
                    ),
                    project_id=str(project_id),
                    blocking=True,
                    severity=_max_severity(
                        [version_severity.get(c.version_a_id), version_severity.get(c.version_b_id)]
                    )
                    or str(c.severity),
                    review_signal=c.review_signal,
                    created_at=c.created_at,
                    gate=Gate.G4_STAKEHOLDER_CONFLICT
                    if c.involves_stakeholder_disagreement
                    else None,
                    subject_type="conflict",
                    subject_id=str(c.id),
                    subject_label=f"{label(c.version_a_id)} vs {label(c.version_b_id)}",
                    actionable=Role.ANALYST in roles,
                    link=f"/ui/projects/{project_id}/quality",
                    detail={"status": str(c.status)},
                )
            )
        return out

    def _risk_entries(
        self, project_id: ProjectId, risks: list[Risk], label: Labeller, roles: frozenset[Role]
    ) -> list[QueueEntry]:
        out = []
        for risk in risks:
            # A HIGH risk under review is already in the queue as its G8 task.
            if risk.status not in UNREVIEWED_RISK_STATUSES or risk.severity is RiskSeverity.HIGH:
                continue
            out.append(
                QueueEntry(
                    kind="risk_review",
                    item_id=str(risk.id),
                    title=risk.title,
                    project_id=str(project_id),
                    blocking=False,
                    severity=str(risk.severity),
                    review_signal=risk.review_signal,
                    created_at=risk.created_at,
                    subject_type="risk",
                    subject_id=str(risk.id),
                    subject_label=label(risk.requirement_version_id) or "project-level",
                    actionable=bool(
                        roles
                        & {
                            Role.ANALYST,
                            Role.SECURITY_REVIEWER,
                            Role.PROJECT_MANAGER,
                            Role.COMPLIANCE_OFFICER,
                        }
                    ),
                    link=f"/ui/projects/{project_id}/risks",
                    detail={"status": str(risk.status)},
                )
            )
        return out

    def _review_item_entries(
        self,
        project_id: ProjectId,
        version_severity: dict[uuid.UUID, str],
        label: Labeller,
        roles: frozenset[Role],
    ) -> list[QueueEntry]:
        if not self._can(Action.REVIEW_READ, project_id):
            return []
        repo = ReviewItemRepository(self._session, self._actor)
        return [
            QueueEntry(
                kind="review_item",
                item_id=str(item.id),
                title=f"AI proposal: {item.reason.value}",
                project_id=str(project_id),
                blocking=False,
                severity=version_severity.get(item.requirement_version_id)
                if item.requirement_version_id
                else None,
                review_signal=item.review_signal,
                created_at=item.created_at,
                subject_type=item.subject_type,
                subject_id=str(item.subject_id),
                subject_label=label(item.requirement_version_id),
                actionable=Role.ANALYST in roles,
                link=f"/ui/projects/{project_id}/review",
            )
            for item in repo.list_for_project(project_id, status=ReviewStatus.OPEN)
        ]

    def _unraised_gate_entries(
        self,
        project_id: ProjectId,
        version_severity: dict[uuid.UUID, str],
        label: Labeller,
        roles: frozenset[Role],
    ) -> list[QueueEntry]:
        """Gates whose trigger holds but whose tasks do not exist yet (fail closed)."""
        needed = (Action.RISK_READ, Action.COMPLIANCE_READ, Action.CONFLICT_READ)
        if not all(self._can(a, project_id) for a in needed):
            return []
        readiness = GovernanceReadinessService(self._session, self._actor)
        out: list[QueueEntry] = []
        for requirement in self._requirements.list_for_project(project_id):
            if requirement.current_version_id is None:
                continue
            version = self._versions.get(project_id, requirement.current_version_id)
            if version is None or version.state not in PRE_APPROVAL_STATES:
                continue
            for gate, state in (
                (Gate.G5_ARCHITECTURE_CRITICAL, readiness.architecture_state(project_id, version)),
                (Gate.G7_APPROVED_REQUIREMENT_CHANGE, readiness.change_state(project_id, version)),
            ):
                if state.required and not state.raised:
                    out.append(
                        self._unraised(
                            project_id,
                            gate,
                            version.id,
                            version.created_at,
                            version_severity.get(version.id),
                            label(version.id),
                            roles,
                        )
                    )
        for conflict in ConflictRepository(self._session, self._actor).list_for_project(
            project_id, status=ConflictStatus.RESOLVED
        ):
            g4 = readiness.conflict_state(project_id, conflict)
            if g4 is not None and not g4.raised:
                out.append(
                    self._unraised(
                        project_id,
                        Gate.G4_STAKEHOLDER_CONFLICT,
                        conflict.id,
                        conflict.created_at,
                        _max_severity(
                            [
                                version_severity.get(conflict.version_a_id),
                                version_severity.get(conflict.version_b_id),
                            ]
                        )
                        or str(conflict.severity),
                        f"{label(conflict.version_a_id)} vs {label(conflict.version_b_id)}",
                        roles,
                    )
                )
        return out

    @staticmethod
    def _unraised(
        project_id: ProjectId,
        gate: Gate,
        subject_id: uuid.UUID,
        created_at: dt.datetime,
        severity: str | None,
        subject_label: str | None,
        roles: frozenset[Role],
    ) -> QueueEntry:
        return QueueEntry(
            kind="gate_not_raised",
            item_id=f"{gate.value}:{subject_id}",
            title=f"{gate.value} required but not raised",
            project_id=str(project_id),
            blocking=True,
            severity=severity,
            review_signal=None,
            created_at=created_at,
            gate=gate,
            subject_id=str(subject_id),
            subject_label=subject_label,
            actionable=Role.ANALYST in roles,
            link=f"/ui/projects/{project_id}/governance",
        )
