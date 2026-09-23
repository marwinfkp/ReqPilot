"""Compliance and security views and the generated compliance artefact (FR-CMP-003, -005..-007).

Everything here is deterministic assembly of persisted, validated rows:

* every view and the artefact carry the **standing advisory notice**
  (``FR-CMP-007``) from a template constant - never model text;
* every mapping shows its jurisdiction, C.1 source type and how binding that
  type is (``FR-CMP-005``), and every citation its full provenance (J.5);
* implied approval and audit checkpoints and retention and reporting obligations
  are listed from the checklist's obligation kinds (``FR-CMP-003``);
* security/privacy findings show the model's proposed level beside the
  authoritative one and the reason (M.3 G3);
* the rendered artefact is checked by the prohibited-assertion detector before
  it is returned (``FR-CMP-006``) - a failure raises, it is never rewritten.

The artefact is a P6 analysis summary for review. It is not P8's document set,
and it approves nothing.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from reqpilot.domain.compliance.language import (
    COMPLIANCE_ADVISORY_NOTICE,
    assert_artefact_language,
)
from reqpilot.domain.enums import (
    SOURCE_TYPE_BINDING,
    Action,
    NormativeSourceType,
    ObligationKind,
    ResourceType,
)
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.compliance import (
    ComplianceGap,
    ComplianceMapping,
    SecurityPrivacyFinding,
)
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.repositories.compliance import (
    ComplianceGapRepository,
    ComplianceMappingRepository,
    SecurityFindingRepository,
)
from reqpilot.repositories.requirements import (
    RequirementRepository,
    RequirementVersionRepository,
)

#: Obligation kinds listed as implied checkpoints and obligations (FR-CMP-003).
IMPLIED_KINDS: frozenset[ObligationKind] = frozenset(
    {
        ObligationKind.APPROVAL_CHECKPOINT,
        ObligationKind.AUDIT_CHECKPOINT,
        ObligationKind.RETENTION_OBLIGATION,
        ObligationKind.REPORTING_OBLIGATION,
    }
)


@dataclass(frozen=True)
class ComplianceOverview:
    advisory_notice: str
    mappings: list[ComplianceMapping]
    gaps: list[ComplianceGap]
    gap_run_id: uuid.UUID | None
    findings: list[SecurityPrivacyFinding]


def binding_of(source_type: str | NormativeSourceType) -> str:
    try:
        return SOURCE_TYPE_BINDING[NormativeSourceType(str(source_type))]
    except ValueError:
        return "unknown"


class ComplianceReadService:
    def __init__(self, session: Session, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._mappings = ComplianceMappingRepository(session, actor)
        self._gaps = ComplianceGapRepository(session, actor)
        self._findings = SecurityFindingRepository(session, actor)
        self._versions = RequirementVersionRepository(session, actor)
        self._requirements = RequirementRepository(session, actor)

    @staticmethod
    def advisory_notice() -> str:
        return COMPLIANCE_ADVISORY_NOTICE

    def mappings(
        self, project_id: ProjectId, *, version_id: uuid.UUID | None = None
    ) -> list[ComplianceMapping]:
        return self._mappings.list_for_project(project_id, version_id=version_id)

    def mapping(self, project_id: ProjectId, mapping_id: uuid.UUID) -> ComplianceMapping | None:
        return self._mappings.get(project_id, mapping_id)

    def mapping_evidence_ids(self, project_id: ProjectId, mapping_id: uuid.UUID) -> list[uuid.UUID]:
        return self._mappings.evidence_ids(project_id, mapping_id)

    def current_gaps(self, project_id: ProjectId) -> tuple[uuid.UUID | None, list[ComplianceGap]]:
        """The gaps of the latest run that analysed the project (rule-engine and G2)."""
        run_id = self._gaps.latest_run_id(project_id)
        if run_id is None:
            require(
                self._actor,
                Action.COMPLIANCE_READ,
                ResourceRef(resource_type=ResourceType.COMPLIANCE_GAP, project_id=project_id),
            )
            return None, []
        return run_id, self._gaps.list_for_run(project_id, run_id)

    def findings(
        self, project_id: ProjectId, *, version_id: uuid.UUID | None = None
    ) -> list[SecurityPrivacyFinding]:
        return self._findings.list_for_project(project_id, version_id=version_id)

    def finding(
        self, project_id: ProjectId, finding_id: uuid.UUID
    ) -> SecurityPrivacyFinding | None:
        return self._findings.get(project_id, finding_id)

    def overview(self, project_id: ProjectId) -> ComplianceOverview:
        run_id, gaps = self.current_gaps(project_id)
        return ComplianceOverview(
            advisory_notice=COMPLIANCE_ADVISORY_NOTICE,
            mappings=self.mappings(project_id),
            gaps=gaps,
            gap_run_id=run_id,
            findings=self.findings(project_id),
        )

    def label(self, project_id: ProjectId, version_id: uuid.UUID) -> str:
        version = self._versions.get(project_id, version_id)
        if version is None:
            return str(version_id)
        requirement = self._requirements.get(project_id, version.requirement_id)
        human = requirement.human_id if requirement else str(version.requirement_id)
        return f"{human} v{version.version_no}"

    # ------------------------------------------------------------------
    # the generated compliance artefact (FR-CMP-007: carries the notice)
    # ------------------------------------------------------------------
    def render_markdown(self, project_id: ProjectId) -> str:
        """A deterministic compliance analysis summary for review, notice first."""
        overview = self.overview(project_id)
        lines: list[str] = [
            "# Compliance and security analysis (candidate, for review)",
            "",
            f"> **Advisory notice.** {overview.advisory_notice}",
            "",
            "## Candidate mappings to potentially applicable sources",
            "",
        ]
        if not overview.mappings:
            lines.append("No validated candidate mapping has been recorded.")
        for mapping in overview.mappings:
            lines.extend(self._mapping_lines(project_id, mapping))
        lines.extend(["", "## Implied checkpoints and obligations (FR-CMP-003)", ""])
        implied = [m for m in overview.mappings if m.obligation_kind in IMPLIED_KINDS]
        implied_gaps = [g for g in overview.gaps if g.obligation_kind in IMPLIED_KINDS]
        if not implied and not implied_gaps:
            lines.append("None identified from the checklist.")
        for mapping in implied:
            lines.append(
                f"- {mapping.obligation_kind.value.replace('_', ' ')}: {mapping.control_title} "
                f"({mapping.control_key}) - suggested by the candidate mapping of "
                f"{self.label(project_id, mapping.requirement_version_id)}"
                + (f"; noted: {mapping.implied_obligation}" if mapping.implied_obligation else "")
            )
        for gap in implied_gaps:
            lines.append(
                f"- {gap.obligation_kind.value.replace('_', ' ')}: {gap.control_title} "
                f"({gap.control_key}) - expected by the checklist; no requirement covers it yet"
            )
        lines.extend(["", "## Gaps against the expected-control checklist", ""])
        if overview.gap_run_id is None:
            lines.append("No gap analysis has been run.")
        elif not overview.gaps:
            lines.append(
                "Every expected control of the applicable checklist has a candidate mapping "
                "awaiting or past review."
            )
        for gap in overview.gaps:
            lines.append(
                f"- {gap.control_key} {gap.control_title} [{gap.checklist_domain} x "
                f"{gap.checklist_jurisdiction}, {gap.checklist_ref}, origin: {gap.origin.value}]"
                + (" - high-impact" if gap.is_high_impact else "")
            )
        lines.extend(["", "## Derived security and privacy requirements (proposals)", ""])
        if not overview.findings:
            lines.append("None recorded.")
        for finding in overview.findings:
            lines.extend(self._finding_lines(project_id, finding))
        lines.extend(["", f"> **Advisory notice.** {overview.advisory_notice}", ""])
        text = "\n".join(lines)
        assert_artefact_language(text)
        return text

    def _mapping_lines(self, project_id: ProjectId, mapping: ComplianceMapping) -> list[str]:
        lines = [
            f"### {self.label(project_id, mapping.requirement_version_id)} -> "
            f"{mapping.control_key} {mapping.control_title}",
            "",
            f"- Relationship (candidate): {mapping.relationship.value.replace('_', ' ')}",
            f"- Jurisdiction: {mapping.jurisdiction}; source type: {mapping.source_type.value} "
            f"({binding_of(mapping.source_type)})",
            f"- Status: {mapping.status.value}"
            + (" - high-impact interpretation, G2 required" if mapping.is_high_impact else ""),
            f"- Rationale: {mapping.rationale}",
        ]
        if mapping.candidate_text:
            lines.append(f"- Candidate text: {mapping.candidate_text}")
        lines.append("- Evidence:")
        lines.extend(self._citation_lines(mapping.citations))
        lines.append("")
        return lines

    @staticmethod
    def _citation_lines(citations: Iterable[dict]) -> list[str]:
        out = []
        for c in citations:
            out.append(
                f"  - {c.get('source_title')} ({c.get('source_type')}: {c.get('binding')}), "
                f"{c.get('issuing_body')}, {c.get('jurisdiction')}, version "
                f"{c.get('source_version')}, effective {c.get('effective_date') or 'n/a'}, "
                f"curated {c.get('curated_on')}; item {c.get('item_key')} "
                f"clause {c.get('clause_ref') or '-'}, chars {c.get('char_start')}-"
                f"{c.get('char_end')}, KB v{c.get('kb_version')}; evidence {c.get('evidence_id')}"
            )
        return out

    def _finding_lines(self, project_id: ProjectId, finding: SecurityPrivacyFinding) -> list[str]:
        lines = [
            f"### {self.label(project_id, finding.requirement_version_id)} - "
            f"{finding.category.value}: {finding.family.value.replace('_', ' ')}",
            "",
            f"- Derived requirement (proposal): {finding.derived_requirement}",
            f"- Proposed risk level (model, advisory only): {finding.proposed_risk_level or '-'}",
            f"- Authoritative risk level: {finding.risk_level.value} (floor "
            f"{finding.catalogue_floor.value}, {finding.risk_rules_version})",
            f"- Why: {finding.escalation_reason}",
            f"- Status: {finding.status.value}"
            + (" - G3 required" if finding.risk_level.value == "high" else ""),
            f"- Evidence: {finding.evidence_status.value}",
        ]
        lines.extend(self._citation_lines(finding.citations))
        lines.append("")
        return lines
