"""The Risk Register (``FR-RSK-008``) and the aggregate measures (``FR-RSK-009``).

**The register is a query, not a table** (architecture G.6: ``RiskRegister`` is
*(derived)*, ``[DESIGN] D7`` - "the register is a query over ``risk`` plus an
``Artifact`` rendering, not a table. Storing it would duplicate mutable state").
So this module builds views over the persisted rows and renders them; it stores
nothing, and it cannot disagree with the register's own data because it has no
copy of it.

It provides the structured interface P8 will render into documents - the
``Artifact`` mechanism, the SRS and DOCX export are P8's, and nothing here
anticipates them beyond returning the fields the register needs:

* per risk: id, scope, requirement/project association, category, title,
  description, likelihood, impact, **authoritative severity**, the matrix
  version that produced it, both rationales, mitigations with their AI/human
  provenance and status, evidence citations, owner role, status, the G8 task if
  one exists, and created/updated metadata (``FR-RSK-008``);
* per project: counts by severity and category, the severity distribution of
  security and compliance risks, and the I.6 SDLC factor inputs
  (``FR-RSK-009``), each carrying the ids of the risks that produced it so a
  factor can always be traced back to rows.

The markdown rendering is deterministic - there is no model anywhere in this
module - and it labels every AI-suggested mitigation as a suggestion requiring
human validation (``FR-RSK-005``).
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    MitigationStatus,
    RiskCategory,
    RiskScope,
    RiskSeverity,
    RiskStatus,
    Role,
)
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.risk import Risk, RiskMitigation
from reqpilot.domain.policy import Actor
from reqpilot.domain.risk.matrix import SEVERITY_RANK
from reqpilot.repositories.requirements import (
    RequirementRepository,
    RequirementVersionRepository,
)
from reqpilot.repositories.risk import RiskMitigationRepository, RiskRepository
from reqpilot.rules.risk import RiskRules, packaged_risk_rules

#: Printed on the register, from template code, never from a model. Risk ratings
#: are explainable ordinal judgements and the mitigations are suggestions; both
#: statements belong on the artefact that a reader might otherwise over-read.
REGISTER_NOTICE = (
    "Ratings are explainable ordinal judgements proposed by automated analysis and "
    "reviewed by a human, not calibrated probabilities. Severity is computed by the "
    "published likelihood x impact matrix, not by a model. Mitigations marked "
    "AI-suggested require human validation before they are relied on. Risks here are "
    "project, engineering, security, privacy, compliance and operational risks; "
    "ReqPilot does not assess borrower credit risk (FR-RSK-011)."
)


@dataclass(frozen=True)
class MitigationView:
    id: uuid.UUID
    suggestion: str
    is_ai_generated: bool
    status: MitigationStatus

    @property
    def label(self) -> str:
        """How the register presents it (``FR-RSK-005``)."""
        if not self.is_ai_generated:
            return "human-authored"
        if self.status is MitigationStatus.ACCEPTED:
            return "AI-suggested, accepted by a human"
        if self.status is MitigationStatus.REJECTED:
            return "AI-suggested, rejected"
        return "AI-suggested - requires human validation"


@dataclass(frozen=True)
class RiskView:
    """One register entry: everything ``FR-RSK-008`` asks the register to carry."""

    id: uuid.UUID
    scope: RiskScope
    requirement_human_id: str | None
    requirement_version_id: uuid.UUID | None
    requirement_version_no: int | None
    category: RiskCategory
    title: str
    description: str
    likelihood: str
    impact: str
    severity: RiskSeverity
    matrix_version: str
    likelihood_rationale: str
    impact_rationale: str
    mitigations: tuple[MitigationView, ...]
    citations: tuple[dict[str, Any], ...]
    evidence_count: int
    owner_role: Role
    status: RiskStatus
    detected_by: str
    approval_task_id: uuid.UUID | None
    decision_rationale: str | None
    created_at: str
    updated_at: str

    @property
    def subject(self) -> str:
        if self.scope is RiskScope.PROJECT:
            return "project-level"
        return f"{self.requirement_human_id or '?'} v{self.requirement_version_no or '?'}"

    @property
    def blocking(self) -> bool:
        """Whether this entry still blocks its requirement's baseline (I.5)."""
        return self.severity is RiskSeverity.HIGH and self.status in (
            RiskStatus.PROPOSED,
            RiskStatus.UNDER_REVIEW,
        )


@dataclass(frozen=True)
class FactorInput:
    """One I.6 SDLC factor input (``FR-RSK-009``), with the rows that produced it."""

    key: str
    value: int
    description: str
    evidence_risk_ids: tuple[uuid.UUID, ...]
    counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class RegisterView:
    """The whole register for one project."""

    project_id: ProjectId
    risks: tuple[RiskView, ...]
    matrix_version: str
    rules_version: str

    @property
    def by_severity(self) -> dict[str, int]:
        return dict(Counter(str(r.severity) for r in self.risks))

    @property
    def by_category(self) -> dict[str, int]:
        return dict(Counter(str(r.category) for r in self.risks))

    @property
    def by_status(self) -> dict[str, int]:
        return dict(Counter(str(r.status) for r in self.risks))

    @property
    def blocking(self) -> tuple[RiskView, ...]:
        return tuple(r for r in self.risks if r.blocking)

    def distribution(self, category: RiskCategory) -> dict[str, int]:
        """The severity distribution of one category - the ``FR-RSK-009`` example."""
        counts = Counter(str(r.severity) for r in self.risks if r.category is category)
        return {level.value: counts.get(level.value, 0) for level in RiskSeverity}


class RiskRegisterService:
    """Builds the register views and the aggregate measures. Reads only."""

    def __init__(self, session: Session, actor: Actor, rules: RiskRules | None = None) -> None:
        self._session = session
        self._actor = actor
        self._rules = rules or packaged_risk_rules()
        self._risks = RiskRepository(session, actor)
        self._mitigations = RiskMitigationRepository(session, actor)
        self._versions = RequirementVersionRepository(session, actor)
        self._requirements = RequirementRepository(session, actor)

    def register(self, project_id: ProjectId) -> RegisterView:
        """The whole register, most severe first, then newest (``FR-RSK-008``)."""
        risks = self._risks.list_for_project(project_id)
        by_risk: dict[uuid.UUID, list[RiskMitigation]] = {}
        for mitigation in self._mitigations.list_for_project(project_id):
            by_risk.setdefault(mitigation.risk_id, []).append(mitigation)
        views = [self._view(project_id, risk, by_risk.get(risk.id, [])) for risk in risks]
        views.sort(key=lambda v: (-SEVERITY_RANK[v.severity], v.created_at, v.title))
        return RegisterView(
            project_id=project_id,
            risks=tuple(views),
            matrix_version=self._rules.matrix.version,
            rules_version=self._rules.ruleset_ref,
        )

    def _view(
        self, project_id: ProjectId, risk: Risk, mitigations: list[RiskMitigation]
    ) -> RiskView:
        human_id: str | None = None
        version_no: int | None = None
        if risk.requirement_version_id is not None:
            version = self._versions.get(project_id, risk.requirement_version_id)
            if version is not None:
                version_no = version.version_no
                requirement = self._requirements.get(project_id, version.requirement_id)
                human_id = requirement.human_id if requirement else None
        return RiskView(
            id=risk.id,
            scope=risk.scope,
            requirement_human_id=human_id,
            requirement_version_id=risk.requirement_version_id,
            requirement_version_no=version_no,
            category=risk.category,
            title=risk.title,
            description=risk.description,
            likelihood=str(risk.likelihood),
            impact=str(risk.impact),
            severity=risk.severity,
            matrix_version=risk.matrix_version,
            likelihood_rationale=risk.likelihood_rationale,
            impact_rationale=risk.impact_rationale,
            mitigations=tuple(
                MitigationView(
                    id=m.id,
                    suggestion=m.suggestion,
                    is_ai_generated=bool(m.is_ai_generated),
                    status=m.status,
                )
                for m in mitigations
            ),
            citations=tuple(dict(c) for c in (risk.citations or [])),
            evidence_count=risk.evidence_count,
            owner_role=risk.owner_role,
            status=risk.status,
            detected_by=str(risk.detected_by),
            approval_task_id=risk.approval_task_id,
            decision_rationale=risk.decision_rationale,
            created_at=risk.created_at.isoformat(),
            updated_at=risk.updated_at.isoformat(),
        )

    # ------------------------------------------------------------------
    # FR-RSK-009: aggregate measures as SDLC factor inputs (architecture I.6)
    # ------------------------------------------------------------------
    def factor_inputs(self, project_id: ProjectId) -> tuple[FactorInput, ...]:
        """The I.6 aggregates, computed deterministically from the register.

        These are *inputs* to the SDLC factor profile, which P9 builds. P7
        exposes them and stops there: no scoring, no weighting, no ranking.
        """
        view = self.register(project_id)
        out: list[FactorInput] = []
        for formula in self._rules.factors:
            relevant = [r for r in view.risks if r.category in formula.categories]
            counts = Counter(str(r.severity) for r in relevant)
            if formula.impact_scores:
                worst = max(relevant, key=lambda r: _impact_rank(r.impact), default=None)
                value = formula.impact_scores.get(worst.impact, 1) if worst else 1
            else:
                value = _threshold_value(formula.thresholds, counts)
            out.append(
                FactorInput(
                    key=formula.key,
                    value=value,
                    description=formula.description,
                    evidence_risk_ids=tuple(r.id for r in relevant),
                    counts={level.value: counts.get(level.value, 0) for level in RiskSeverity},
                )
            )
        return tuple(out)

    # ------------------------------------------------------------------
    # rendering (deterministic; the P8 document generator will reuse the views)
    # ------------------------------------------------------------------
    def render_markdown(self, project_id: ProjectId) -> str:
        """The Risk Register artefact as markdown. No model is involved."""
        view = self.register(project_id)
        lines: list[str] = [
            "# Risk register",
            "",
            f"> {REGISTER_NOTICE}",
            "",
            f"Severity matrix: `{view.matrix_version}` - ruleset `{view.rules_version}`.",
            "",
            f"**{len(view.risks)} risk(s)**: "
            + (", ".join(f"{k} {v}" for k, v in sorted(view.by_severity.items())) or "none")
            + f". Blocking a baseline now: {len(view.blocking)}.",
            "",
            "| Risk | Subject | Category | L | I | Severity | Status | Owner | Evidence |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for risk in view.risks:
            lines.append(
                f"| {risk.title} | {risk.subject} | {risk.category} | {risk.likelihood} | "
                f"{risk.impact} | **{risk.severity}** | {risk.status} | {risk.owner_role} | "
                f"{risk.evidence_count} |"
            )
        lines.append("")
        for risk in view.risks:
            lines.extend(
                [
                    f"## {risk.title}",
                    "",
                    f"*{risk.category} risk on {risk.subject}; recorded by {risk.detected_by}.*",
                    "",
                    risk.description,
                    "",
                    f"- **Likelihood {risk.likelihood}** - {risk.likelihood_rationale}",
                    f"- **Impact {risk.impact}** - {risk.impact_rationale}",
                    f"- **Severity {risk.severity}**, computed by matrix "
                    f"`{risk.matrix_version}` from those two ratings.",
                    f"- Status: {risk.status}; owner: {risk.owner_role}."
                    + (f" G8 task: `{risk.approval_task_id}`." if risk.approval_task_id else ""),
                ]
            )
            if risk.decision_rationale:
                lines.append(f"- Decision rationale: {risk.decision_rationale}")
            if risk.mitigations:
                lines.append("- Mitigation considerations:")
                for mitigation in risk.mitigations:
                    lines.append(f"  - {mitigation.suggestion} *({mitigation.label})*")
            if risk.citations:
                lines.append("- Evidence:")
                for citation in risk.citations:
                    lines.append(
                        f"  - {citation.get('source_title', 'source')} "
                        f"{citation.get('clause_ref') or ''} "
                        f"(`{citation.get('evidence_id')}`)".rstrip()
                    )
            lines.append("")
        lines.extend(["---", "", f"> {REGISTER_NOTICE}", ""])
        return "\n".join(lines)


def _impact_rank(impact: str) -> int:
    return {"I1": 1, "I2": 2, "I3": 3}.get(impact, 0)


def _threshold_value(thresholds: tuple[dict[str, int], ...], counts: Counter[str]) -> int:
    """First matching threshold wins; a threshold with no minimum is the floor."""
    for threshold in thresholds:
        high = threshold.get("min_high")
        medium = threshold.get("min_medium")
        low = threshold.get("min_low")
        if high is not None and counts.get(RiskSeverity.HIGH.value, 0) < high:
            continue
        if (
            medium is not None
            and (counts.get(RiskSeverity.MEDIUM.value, 0) + counts.get(RiskSeverity.HIGH.value, 0))
            < medium
        ):
            continue
        if low is not None and sum(counts.values()) < low:
            continue
        return int(threshold["value"])
    return 1
