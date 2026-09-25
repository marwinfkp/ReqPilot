"""Traceability coverage (``FR-TRC-003``) and E6, computed from the persisted graph.

**E6 - traceability coverage** is defined by approved Phase 0 O.1 as "the fraction
of requirements with a complete chain per ``FR-TRC-001``, including risk links",
"computed by the system (``FR-TRC-003``)", and architecture N.3 makes "complete"
exact. A requirement version is **fully traced** when it has:

1. at least one inbound ``SOURCES`` edge (it traces to stakeholder input);
2. at least one ``CLASSIFIED_AS`` edge;
3. a risk-analysis outcome - a ``HAS_RISK`` edge, or the recorded "no risk
   identified" result (a ``RISK_ASSESSED_BY`` edge to the risk-analysis run that
   examined this exact version);
4. an ``APPROVED_BY`` edge **if** it was approved;
5. a ``RENDERED_IN`` path **if** it was baselined (``MEMBER_OF`` a baseline that is
   ``RENDERED_IN`` an artefact version).

E6 = fully traced / versions in scope. No target exists for E6 (O.1: targets for
E2-E9 are set from measured behaviour, not guessed), so this module reports the
value and invents no pass mark.

Everything else ``FR-TRC-003`` asks for is reported beside it - orphan
requirements, unsourced statements, unlinked risks, and per-version missing
links - and nothing is rounded up: a missing edge is a missing edge.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from reqpilot.domain.enums import RiskScope
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.elicitation import QualityFinding
from reqpilot.domain.models.risk import Risk
from reqpilot.domain.policy import Actor
from reqpilot.domain.traceability import SOURCE_NODE_TYPES, TraceLinkType, TraceNodeType
from reqpilot.services.traceability.graph import TraceGraph, TraceQueryService
from reqpilot.services.traceability.scope import RequirementScope

N = TraceNodeType
L = TraceLinkType

#: E6's definition version, stamped on every report (architecture N.3 as read by P8).
E6_DEFINITION_VERSION = "N.3-v1"

#: A version counts as approved once it has passed G1, even after supersession.
_APPROVED_STATES = frozenset(
    {RequirementState.APPROVED, RequirementState.BASELINED, RequirementState.SUPERSEDED}
)


@dataclass(frozen=True)
class VersionCoverage:
    version_id: uuid.UUID
    human_id: str
    version_no: int
    state: str
    has_source: bool
    has_classification: bool
    has_risk_outcome: bool
    has_acceptance_criteria: bool
    has_analysis: bool
    approved: bool
    has_approved_by: bool
    baselined: bool
    has_rendered_path: bool
    in_artifact: bool
    unresolved_source_refs: int

    @property
    def missing(self) -> tuple[str, ...]:
        """The N.3 elements that are absent - the ones that stop it being fully traced."""
        out = []
        if not self.has_source:
            out.append("SOURCES (no resolvable stakeholder input or source)")
        if not self.has_classification:
            out.append("CLASSIFIED_AS")
        if not self.has_risk_outcome:
            out.append("HAS_RISK or recorded risk-analysis outcome")
        if self.approved and not self.has_approved_by:
            out.append("APPROVED_BY")
        if self.baselined and not self.has_rendered_path:
            out.append("RENDERED_IN (baseline not rendered in any artefact)")
        return tuple(out)

    @property
    def fully_traced(self) -> bool:
        return not self.missing


@dataclass(frozen=True)
class CoverageReport:
    project_id: ProjectId
    scope_kind: str
    scope_label: str
    definition_version: str
    versions: tuple[VersionCoverage, ...]
    orphan_requirements: tuple[str, ...]
    unsourced_statements: tuple[str, ...]
    unlinked_risks: tuple[str, ...]
    project_level_risks: int
    findings_without_parent: tuple[str, ...]
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.versions)

    @property
    def fully_traced(self) -> int:
        return sum(1 for v in self.versions if v.fully_traced)

    @property
    def e6(self) -> float | None:
        """Fully traced / total, or ``None`` for an empty scope (never a fabricated 1.0)."""
        return None if not self.versions else self.fully_traced / self.total

    def percentage(self, key: str) -> float | None:
        return None if not self.versions else self.counts.get(key, 0) / self.total

    def as_dict(self) -> dict[str, object]:
        return {
            "scope_kind": self.scope_kind,
            "scope_label": self.scope_label,
            "definition_version": self.definition_version,
            "total": self.total,
            "fully_traced": self.fully_traced,
            "e6": self.e6,
            "counts": dict(self.counts),
            "orphan_requirements": list(self.orphan_requirements),
            "unsourced_statements": list(self.unsourced_statements),
            "unlinked_risks": list(self.unlinked_risks),
            "project_level_risks": self.project_level_risks,
            "findings_without_parent": list(self.findings_without_parent),
            "versions": [
                {
                    "version_id": str(v.version_id),
                    "requirement": f"{v.human_id} v{v.version_no}",
                    "state": v.state,
                    "fully_traced": v.fully_traced,
                    "missing": list(v.missing),
                }
                for v in self.versions
            ],
        }


def version_coverage(
    graph: TraceGraph, human_id: str, version: object, unresolved_refs: int
) -> VersionCoverage:
    """Evaluate N.3 for one version against the persisted graph. Pure over the graph."""
    vid = version.id  # type: ignore[attr-defined]
    has_source = any(
        e.from_type in {str(t) for t in SOURCE_NODE_TYPES}
        for e in graph.incoming(N.REQUIREMENT_VERSION, vid, L.SOURCES)
    )
    has_risk = bool(graph.outgoing(N.REQUIREMENT_VERSION, vid, L.HAS_RISK))
    assessed = bool(graph.outgoing(N.REQUIREMENT_VERSION, vid, L.RISK_ASSESSED_BY))
    analysis = any(
        graph.outgoing(N.REQUIREMENT_VERSION, vid, lt)
        for lt in (
            L.HAS_FINDING,
            L.HAS_MAPPING,
            L.HAS_SECURITY_FINDING,
            L.HAS_RISK,
            L.RISK_ASSESSED_BY,
            L.HAS_CONFLICT,
        )
    )
    baselines = graph.targets(N.REQUIREMENT_VERSION, vid, L.MEMBER_OF)
    rendered = any(graph.outgoing(N.BASELINE, b, L.RENDERED_IN) for b in baselines)
    state = version.state  # type: ignore[attr-defined]
    return VersionCoverage(
        version_id=vid,
        human_id=human_id,
        version_no=version.version_no,  # type: ignore[attr-defined]
        state=str(state),
        has_source=has_source,
        has_classification=bool(graph.outgoing(N.REQUIREMENT_VERSION, vid, L.CLASSIFIED_AS)),
        has_risk_outcome=has_risk or assessed,
        has_acceptance_criteria=bool(graph.outgoing(N.REQUIREMENT_VERSION, vid, L.SATISFIED_BY)),
        has_analysis=analysis,
        approved=state in _APPROVED_STATES,
        has_approved_by=bool(graph.outgoing(N.REQUIREMENT_VERSION, vid, L.APPROVED_BY)),
        baselined=bool(baselines) or state is RequirementState.BASELINED,
        has_rendered_path=rendered,
        in_artifact=bool(graph.incoming(N.REQUIREMENT_VERSION, vid, L.CITES)),
        unresolved_source_refs=unresolved_refs,
    )


def unresolved_ref_count(graph: TraceGraph, version: object) -> int:
    """Source references on the version that no ``SOURCES`` edge accounts for."""
    linked = {e.from_id for e in graph.incoming(N.REQUIREMENT_VERSION, version.id, L.SOURCES)}  # type: ignore[attr-defined]
    unresolved = 0
    for ref in getattr(version, "source_refs", None) or []:
        ids = {str(ref.get(k)) for k in ("ref", "document")} if isinstance(ref, dict) else set()
        if not ids & linked:
            unresolved += 1
    return unresolved


class CoverageService:
    """``FR-TRC-003`` over a scope. Reads the persisted graph; writes nothing."""

    def __init__(self, session: Session, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._query = TraceQueryService(session, actor)

    def report(self, scope: RequirementScope, *, graph: TraceGraph | None = None) -> CoverageReport:
        project_id = scope.project_id
        graph = graph or self._query.graph(project_id)
        rows = []
        for item in scope.items:
            unresolved = unresolved_ref_count(graph, item.version)
            rows.append(version_coverage(graph, item.human_id, item.version, unresolved))

        def label(v: VersionCoverage) -> str:
            return f"{v.human_id} v{v.version_no}"

        in_scope = scope.version_ids
        risks = list(self._session.scalars(select(Risk).where(Risk.project_id == project_id)))
        unlinked = tuple(
            sorted(
                f"{r.title} ({r.id})"
                for r in risks
                if r.scope is RiskScope.REQUIREMENT
                and (
                    r.requirement_version_id is None or not graph.incoming(N.RISK, r.id, L.HAS_RISK)
                )
            )
        )
        findings = list(
            self._session.scalars(
                select(QualityFinding).where(QualityFinding.project_id == project_id)
            )
        )
        orphan_findings = tuple(
            sorted(
                str(f.id)
                for f in findings
                if f.requirement_version_id in in_scope
                and not graph.incoming(N.QUALITY_FINDING, f.id, L.HAS_FINDING)
            )
        )
        counts = {
            "with_source": sum(1 for r in rows if r.has_source),
            "with_classification": sum(1 for r in rows if r.has_classification),
            "with_risk_outcome": sum(1 for r in rows if r.has_risk_outcome),
            "with_acceptance_criteria": sum(1 for r in rows if r.has_acceptance_criteria),
            "with_analysis_outputs": sum(1 for r in rows if r.has_analysis),
            "approved": sum(1 for r in rows if r.approved),
            "approved_with_approved_by": sum(1 for r in rows if r.approved and r.has_approved_by),
            "baselined": sum(1 for r in rows if r.baselined),
            "baselined_with_rendered_path": sum(
                1 for r in rows if r.baselined and r.has_rendered_path
            ),
            "represented_in_artifacts": sum(1 for r in rows if r.in_artifact),
            "fully_traced": sum(1 for r in rows if r.fully_traced),
        }
        scope_label = (
            f"baseline {scope.baseline.label}"
            if scope.baseline is not None
            else "project (current versions)"
        )
        return CoverageReport(
            project_id=project_id,
            scope_kind=scope.kind,
            scope_label=scope_label,
            definition_version=E6_DEFINITION_VERSION,
            versions=tuple(rows),
            orphan_requirements=tuple(sorted(label(r) for r in rows if not r.has_source)),
            unsourced_statements=tuple(
                sorted(
                    f"{label(r)} ({r.unresolved_source_refs} unresolved reference(s))"
                    for r in rows
                    if r.unresolved_source_refs
                )
            ),
            unlinked_risks=unlinked,
            project_level_risks=sum(1 for r in risks if r.scope is RiskScope.PROJECT),
            findings_without_parent=orphan_findings,
            counts=counts,
        )
