"""Baseline readiness: what still blocks a requirement version, and which gate owns it.

``FR-HIL-004`` - *prevent any unapproved requirement from entering a baseline or
a generated artefact* - needs one deterministic answer to "is anything still
unresolved for this exact version?". This module is that answer. It reads only
persisted rows, never model output, and it fails closed: a gate whose trigger
holds but whose task was never raised is a blocker, not a pass.

It is consulted at four points, each of which refuses on any blocker:

1. **submission for G1** (``VALIDATED -> PENDING_APPROVAL``);
2. **a G1 approval** (before the decision row is written);
3. **baseline commit** (the P1 service, defence in depth);
4. **authoritative artefact generation** (every requirement version rendered).

What counts, per version:

* an open or under-review conflict (``[DESIGN] D12`` - a guard, never a state);
* a resolved stakeholder-disagreement conflict without a passed **G4**;
* a pending high-impact interpretation (**G2**) or high-risk derived security /
  privacy requirement (**G3**), from the persisted P6 status;
* an unreviewed HIGH risk (**G8**), from the persisted P7 severity and status;
* an architecture-critical version (M.3 predicate or analyst flag) without a
  passed **G5**;
* a change to an approved requirement without a passed **G7**;
* an open quality finding (H.3's "no open defects").

Nothing here decides anything. It reports; the calling step refuses.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    BLOCKING_CONFLICT_STATUSES,
    ConflictStatus,
    Gate,
)
from reqpilot.domain.errors import GovernanceBlockedError
from reqpilot.domain.governance import Blocker, LabelSignal, architecture_critical_reasons
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.quality import Conflict
from reqpilot.domain.models.requirements import RequirementVersion
from reqpilot.domain.policy import Actor
from reqpilot.repositories.compliance import (
    ComplianceMappingRepository,
    SecurityFindingRepository,
)
from reqpilot.repositories.elicitation import QualityFindingRepository
from reqpilot.repositories.extraction import ClassificationRepository
from reqpilot.repositories.quality import ConflictRepository
from reqpilot.repositories.requirements import RequirementVersionRepository
from reqpilot.repositories.risk import RiskRepository
from reqpilot.services.governance.gates import (
    GateState,
    GovernanceGateService,
    conflict_gate_hash,
)

Stage = Literal["submission", "approval", "baseline", "artifact"]

#: States of an earlier version that make a successor a *change to an approved
#: requirement* (G7). SUPERSEDED is reached only from APPROVED or BASELINED.
_APPROVED_HISTORY: frozenset[RequirementState] = frozenset(
    {RequirementState.APPROVED, RequirementState.BASELINED, RequirementState.SUPERSEDED}
)


def packaged_g5_threshold() -> float:
    """The G5 threshold: the existing P3 classification review threshold (M.3)."""
    from pathlib import Path

    import reqpilot.rules
    from reqpilot.rules.extraction import load_extraction_rules

    rules_dir = Path(reqpilot.rules.__file__).resolve().parent / "data"
    return load_extraction_rules(rules_dir).classification_review_threshold


@dataclass(frozen=True)
class Readiness:
    version_id: uuid.UUID
    blockers: tuple[Blocker, ...]

    @property
    def ready(self) -> bool:
        return not self.blockers


class GovernanceReadinessService:
    """Deterministic blockers for one requirement version. Reads only."""

    def __init__(self, session: Session, actor: Actor, *, g5_threshold: float | None = None):
        self._session = session
        self._actor = actor
        self._g5_threshold = g5_threshold if g5_threshold is not None else packaged_g5_threshold()
        self._gates = GovernanceGateService(session, actor)
        self._versions = RequirementVersionRepository(session, actor)
        self._conflicts = ConflictRepository(session, actor)
        self._mappings = ComplianceMappingRepository(session, actor)
        self._security = SecurityFindingRepository(session, actor)
        self._risks = RiskRepository(session, actor)
        self._findings = QualityFindingRepository(session, actor)
        self._labels = ClassificationRepository(session, actor)

    @property
    def g5_threshold(self) -> float:
        return self._g5_threshold

    # ------------------------------------------------------------------
    # per-gate state
    # ------------------------------------------------------------------
    def architecture_reasons(
        self, project_id: ProjectId, version: RequirementVersion
    ) -> tuple[str, ...]:
        """Why G5 applies to this version (M.3), from persisted labels and tasks."""
        from reqpilot.domain.enums import RequirementCategory

        labels = [
            LabelSignal(RequirementCategory(str(label.category)), label.review_signal)
            for label in self._labels.current(project_id, version.id)
        ]
        flagged = bool(self._gates.tasks_for(project_id, Gate.G5_ARCHITECTURE_CRITICAL, version.id))
        return architecture_critical_reasons(
            labels, threshold=self._g5_threshold, analyst_flagged=flagged
        )

    def architecture_state(self, project_id: ProjectId, version: RequirementVersion) -> GateState:
        reasons = self.architecture_reasons(project_id, version)
        return self._gates.state(
            project_id,
            Gate.G5_ARCHITECTURE_CRITICAL,
            version.id,
            version.content_hash,
            required=bool(reasons),
            reasons=reasons,
        )

    def change_required(self, project_id: ProjectId, version: RequirementVersion) -> bool:
        """Whether this version changes a requirement some earlier version of which was approved."""
        history = self._versions.list_for_requirement(project_id, version.requirement_id)
        return any(
            v.version_no < version.version_no and v.state in _APPROVED_HISTORY for v in history
        )

    def change_state(self, project_id: ProjectId, version: RequirementVersion) -> GateState:
        return self._gates.state(
            project_id,
            Gate.G7_APPROVED_REQUIREMENT_CHANGE,
            version.id,
            version.content_hash,
            required=self.change_required(project_id, version),
        )

    def conflict_state(self, project_id: ProjectId, conflict: Conflict) -> GateState | None:
        """G4 for one conflict, or ``None`` when G4 does not apply to it."""
        if not conflict.involves_stakeholder_disagreement:
            return None
        if conflict.status is not ConflictStatus.RESOLVED:
            return None
        a = self._versions.get(project_id, conflict.version_a_id)
        b = self._versions.get(project_id, conflict.version_b_id)
        if a is None or b is None:  # pragma: no cover - composite foreign keys
            return None
        return self._gates.state(
            project_id,
            Gate.G4_STAKEHOLDER_CONFLICT,
            conflict.id,
            conflict_gate_hash(conflict, a.content_hash, b.content_hash),
            required=True,
        )

    # ------------------------------------------------------------------
    # the blockers
    # ------------------------------------------------------------------
    def evaluate(
        self, project_id: ProjectId, version: RequirementVersion, *, stage: Stage
    ) -> Readiness:
        blockers: list[Blocker] = []
        vid = str(version.id)

        # D12 and G4 --------------------------------------------------------
        for conflict in self._conflicts.touching(project_id, version.id):
            if conflict.status in BLOCKING_CONFLICT_STATUSES:
                blockers.append(
                    Blocker(
                        "OPEN_CONFLICT",
                        f"conflict {conflict.id} is {conflict.status}; resolve or dismiss it",
                        gate=Gate.G4_STAKEHOLDER_CONFLICT
                        if conflict.involves_stakeholder_disagreement
                        else None,
                        subject_type="conflict",
                        subject_id=str(conflict.id),
                    )
                )
                continue
            g4 = self.conflict_state(project_id, conflict)
            if g4 is None or g4.passed:
                continue
            code = (
                "G4_REJECTED" if g4.rejected else "G4_PENDING" if g4.open_tasks else "G4_REQUIRED"
            )
            blockers.append(
                Blocker(
                    code,
                    (
                        f"the resolution of stakeholder conflict {conflict.id} "
                        + (
                            "was rejected at G4; the affected requirement needs a new version"
                            if g4.rejected
                            else f"awaits {g4.open_tasks} G4 signature(s)"
                            if g4.open_tasks
                            else "has not been put to G4; raise the required gates"
                        )
                    ),
                    gate=Gate.G4_STAKEHOLDER_CONFLICT,
                    subject_type="conflict",
                    subject_id=str(conflict.id),
                )
            )

        # G2 / G3 (P6, persisted status) ---------------------------------------
        pending_g2 = self._mappings.pending_count(project_id, version.id)
        if pending_g2:
            blockers.append(
                Blocker(
                    "G2_PENDING",
                    f"{pending_g2} high-impact regulatory interpretation(s) await the "
                    "Compliance Officer",
                    gate=Gate.G2_REGULATORY_INTERPRETATION,
                    subject_type="requirement_version",
                    subject_id=vid,
                )
            )
        pending_g3 = self._security.pending_count(project_id, version.id)
        if pending_g3:
            blockers.append(
                Blocker(
                    "G3_PENDING",
                    f"{pending_g3} high-risk security/privacy requirement(s) await the "
                    "Security Reviewer",
                    gate=Gate.G3_HIGH_RISK_SECURITY,
                    subject_type="requirement_version",
                    subject_id=vid,
                )
            )

        # G8 (P7, persisted severity and status) -------------------------------
        unreviewed = self._risks.unreviewed_high_count(project_id, version.id)
        if unreviewed:
            blockers.append(
                Blocker(
                    "G8_UNREVIEWED",
                    f"{unreviewed} high-severity risk(s) have not been reviewed at G8",
                    gate=Gate.G8_HIGH_SEVERITY_RISK,
                    subject_type="requirement_version",
                    subject_id=vid,
                )
            )

        # G5 and G7 ------------------------------------------------------------
        for gate, state, what in (
            (
                Gate.G5_ARCHITECTURE_CRITICAL,
                self.architecture_state(project_id, version),
                "architecture-critical requirement",
            ),
            (
                Gate.G7_APPROVED_REQUIREMENT_CHANGE,
                self.change_state(project_id, version),
                "change to an approved requirement",
            ),
        ):
            if not state.required or state.passed:
                continue
            short = gate.value
            if state.rejected:
                code, message = f"{short}_REJECTED", f"this {what} was rejected at {short}"
            elif state.open_tasks:
                code, message = (
                    f"{short}_PENDING",
                    f"this {what} awaits {state.open_tasks} {short} signature(s)",
                )
            else:
                code, message = (
                    f"{short}_REQUIRED",
                    f"this is a {what} and {short} has not been raised for this exact version",
                )
            if state.reasons:
                message += f" ({'; '.join(state.reasons)})"
            blockers.append(
                Blocker(
                    code, message, gate=gate, subject_type="requirement_version", subject_id=vid
                )
            )

        # H.3: no open defects --------------------------------------------------
        open_findings = self._findings.open_count(project_id, version.id)
        if open_findings:
            blockers.append(
                Blocker(
                    "QUALITY_FINDINGS_OPEN",
                    f"{open_findings} open quality finding(s) must be resolved or dismissed",
                    subject_type="requirement_version",
                    subject_id=vid,
                )
            )

        del stage  # every stage applies the same blockers; the caller checks state
        return Readiness(version_id=version.id, blockers=tuple(blockers))

    def require_ready(
        self, project_id: ProjectId, version: RequirementVersion, *, stage: Stage, label: str
    ) -> None:
        """Raise :class:`GovernanceBlockedError` unless nothing blocks this version."""
        readiness = self.evaluate(project_id, version, stage=stage)
        if readiness.ready:
            return
        rendered = tuple(b.render() for b in readiness.blockers)
        raise GovernanceBlockedError(
            f"{label} v{version.version_no} cannot proceed ({stage}): " + "; ".join(rendered),
            blockers=rendered,
        )
