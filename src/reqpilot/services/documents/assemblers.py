"""Deterministic assembly of the P8 artefacts (architecture C.6; ``FR-DOC-001``..``-008``).

Every artefact is a projection of persisted, approved data into the structured
document model (:mod:`reqpilot.artifacts.model`). The rules each assembler keeps:

* **Only baseline members are requirements.** A requirement version appears as a
  requirement only if it is in the baseline scope, i.e. it passed G1 and entered
  a baseline (``FR-HIL-004``). Other versions can be *mentioned* only in the
  open-issues list, and only as open issues.
* **Nothing absent is invented.** A field the records do not hold is printed as
  :data:`NOT_RECORDED`; a section with no approved data is declared empty with
  the template's note. No actor, business rule, interface, schema, control,
  evidence, risk level or trace link is produced here.
* **Authoritative values are read, not recomputed.** Risk severity is the P7
  matrix value stored on the risk; the security/privacy level is P6's
  authoritative column; mapping and gate status come from the persisted rows.
* **Advisory language is preserved.** Compliance content says "candidate",
  "potentially applicable", "requires human review" and carries the P6 advisory
  notice; it never says a system is compliant (``FR-CMP-006``).
* **Every content section cites** the requirement versions it is about
  (``FR-DOC-008``); project-level sections cite the rows they are about.

No model is involved anywhere in this module (architecture C.6: tabular artefacts
are rendered entirely deterministically; P8 fills prose sections from the
versioned template and the records too - see the P8 report).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Iterable

from reqpilot.artifacts.model import (
    Citation,
    Document,
    Fields,
    Items,
    Notice,
    Paragraph,
    Section,
    Table,
)
from reqpilot.artifacts.registry import ArtefactTemplate
from reqpilot.domain.compliance.language import COMPLIANCE_ADVISORY_NOTICE
from reqpilot.domain.enums import (
    SOURCE_TYPE_BINDING,
    ApprovalTaskStatus,
    ArtifactType,
    Gate,
    NormativeSourceType,
    ObligationKind,
    RequirementCategory,
    RiskScope,
)
from reqpilot.domain.models.base import as_utc
from reqpilot.domain.models.compliance import ComplianceMapping
from reqpilot.domain.models.quality import Conflict
from reqpilot.domain.models.requirements import RequirementVersion
from reqpilot.domain.models.risk import Risk
from reqpilot.domain.traceability import TraceLinkType, TraceNodeType
from reqpilot.services.documents.context import DocumentContext
from reqpilot.services.risk.register import REGISTER_NOTICE
from reqpilot.services.traceability.coverage import version_coverage
from reqpilot.services.traceability.rtm import RTM_COLUMNS, RtmBuilder
from reqpilot.services.traceability.scope import ScopedVersion

NOT_RECORDED = "not recorded"

#: What a data or interface section may show (FR-DOC-007) and why.
DATA_NOTICE = (
    "ReqPilot does not infer a data model. Data objects, fields, relationships and "
    "retention rules appear here only as the approved requirements and the recorded "
    "compliance and privacy analysis state them."
)
INTERFACE_NOTICE = (
    "Interfaces appear here only as the approved requirements state them. No interface, "
    "API or integration is added because it is common in the domain."
)

_SHALL = re.compile(r"^\s*the system shall\s+(.+)$", re.IGNORECASE | re.DOTALL)

#: The P0 classification dimensions, in the problem statement's order (PS section 9).
DIMENSIONS: tuple[tuple[RequirementCategory, str], ...] = (
    (RequirementCategory.BUSINESS, "Business requirements"),
    (RequirementCategory.STAKEHOLDER, "Stakeholder requirements"),
    (RequirementCategory.FUNCTIONAL, "Functional requirements (classified)"),
    (RequirementCategory.SECURITY, "Security requirements"),
    (RequirementCategory.PRIVACY, "Privacy requirements"),
    (RequirementCategory.REGULATORY, "Regulatory and compliance requirements"),
    (RequirementCategory.PERFORMANCE, "Performance requirements"),
    (RequirementCategory.AVAILABILITY, "Availability and reliability requirements"),
    (RequirementCategory.USABILITY, "Usability requirements"),
    (RequirementCategory.DATA_MANAGEMENT, "Data-management requirements"),
    (RequirementCategory.INTEGRATION, "Integration requirements"),
    (RequirementCategory.AUDIT_REPORTING, "Audit and reporting (auditability) requirements"),
    (RequirementCategory.OPERATIONAL, "Operational and maintenance requirements"),
)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def cite(ctx: DocumentContext, version: RequirementVersion) -> Citation:
    return Citation("requirement_version", str(version.id), ctx.version_label(version.id))


def cite_risk(risk: Risk) -> Citation:
    return Citation("risk", str(risk.id), f"risk {str(risk.id)[:8]}")


def cite_mapping(mapping: ComplianceMapping) -> Citation:
    return Citation("compliance_mapping", str(mapping.id), f"mapping {mapping.control_key}")


def cite_evidence(ctx: DocumentContext, evidence_ids: Iterable[uuid.UUID]) -> list[Citation]:
    out = []
    for eid in sorted(set(evidence_ids), key=str):
        view = ctx.evidence.get(eid)
        out.append(
            Citation("evidence", str(eid), view.label if view else f"evidence {str(eid)[:8]}")
        )
    return out


def _other_side(conflict: Conflict, version_id: uuid.UUID) -> uuid.UUID:
    return conflict.version_b_id if conflict.version_a_id == version_id else conflict.version_a_id


def kind_label(human_id: str) -> str:
    return "Functional" if human_id.startswith("FR-") else "Non-functional"


def or_not_recorded(values: Iterable[str]) -> str:
    cleaned = [v for v in values if v]
    return "; ".join(cleaned) if cleaned else NOT_RECORDED


def empty_section(key: str, number: str, title: str, note: str, level: int = 2) -> Section:
    return Section(key, number, title, level=level, kind="empty", blocks=(Paragraph(note),))


def binding(source_type: str) -> str:
    try:
        return SOURCE_TYPE_BINDING[NormativeSourceType(source_type)]
    except ValueError:
        return "unknown"


def g1_record(ctx: DocumentContext, version: RequirementVersion) -> list[str]:
    out = []
    for task, decision in ctx.approvals_for(version.id):
        if task.gate is not Gate.G1_REQUIREMENT_BASELINE:
            continue
        out.append(
            f"{decision.decision.value} as {decision.role_exercised.value} on "
            f"{decision.decided_at.date().isoformat()} (decision {str(decision.id)[:8]}, "
            f"hash {decision.subject_version_hash[:12]})"
        )
    return out


def other_gate_record(ctx: DocumentContext, version: RequirementVersion) -> list[str]:
    """G2/G3/G4/G5/G7/G8 decisions that concern this version, from persisted rows."""
    subjects: list[tuple[str, uuid.UUID]] = [("version", version.id)]
    subjects += [("mapping", m.id) for m in ctx.mappings.get(version.id, [])]
    subjects += [("finding", f.id) for f in ctx.findings.get(version.id, [])]
    subjects += [("risk", r.id) for r in ctx.risks_for(version.id)]
    subjects += [("conflict", c.id) for c in ctx.conflicts_for(version.id)]
    out = []
    for _kind, subject in subjects:
        for task, decision in ctx.approvals_for(subject):
            if task.gate is Gate.G1_REQUIREMENT_BASELINE:
                continue
            out.append(
                f"{task.gate.value} {decision.decision.value} by "
                f"{decision.role_exercised.value} on {decision.decided_at.date().isoformat()}"
            )
    return sorted(out)


def risk_level(ctx: DocumentContext, version: RequirementVersion) -> str:
    risks = ctx.risks_for(version.id)
    if not risks:
        assessed = ctx.graph.outgoing(
            TraceNodeType.REQUIREMENT_VERSION, version.id, TraceLinkType.RISK_ASSESSED_BY
        )
        return "no risk recorded (risk analysis ran)" if assessed else NOT_RECORDED
    order = {"high": 3, "medium": 2, "low": 1}
    worst = max(risks, key=lambda r: order.get(str(r.severity), 0))
    return (
        f"{worst.severity.value} (highest of {len(risks)} recorded risk(s); severity from "
        f"matrix {worst.matrix_version})"
    )


def criteria_lines(ctx: DocumentContext, version: RequirementVersion) -> list[str]:
    return [
        f"AC{c.ordinal}: Given {c.given_text}; When {c.when_text}; Then {c.then_text}"
        + (" (human)" if str(c.source) == "human" else "")
        for c in ctx.criteria.get(version.id, [])
    ]


def mapping_lines(ctx: DocumentContext, version: RequirementVersion) -> list[str]:
    return [
        f"{m.control_key} {m.control_title} - candidate mapping, "
        f"{m.relationship.value.replace('_', ' ')}, status {m.status.value}"
        + (", high-impact interpretation (G2)" if m.is_high_impact else "")
        for m in ctx.mappings.get(version.id, [])
    ]


def finding_lines(ctx: DocumentContext, version: RequirementVersion) -> list[str]:
    return [
        f"{f.category.value}/{f.family.value.replace('_', ' ')}: {f.derived_requirement} "
        f"[authoritative level {f.risk_level.value}, status {f.status.value}]"
        for f in ctx.findings.get(version.id, [])
    ]


def header_metadata(
    ctx: DocumentContext, template: ArtefactTemplate
) -> tuple[tuple[str, str], ...]:
    baseline = ctx.scope.baseline
    assert baseline is not None
    return (
        ("Project", ctx.project.name),
        ("Domain", ctx.project.domain),
        ("Jurisdiction scope", ", ".join(ctx.project.jurisdiction_scope or []) or NOT_RECORDED),
        ("Baseline", f"{baseline.label} ({baseline.id})"),
        ("Baseline frozen at", baseline.frozen_at.isoformat()),
        (
            "Baselines contributing",
            ", ".join(b.label for b in ctx.scope.contributing) or baseline.label,
        ),
        ("Requirement versions in scope", str(len(ctx.items))),
        ("Template", f"{template.template_id} {template.version}"),
        (
            "Scope rule",
            "requirement versions in force as of the baseline - approved baseline members only",
        ),
    )


def control_section(template: ArtefactTemplate) -> Section:
    return Section(
        "control",
        "0",
        "Purpose and document control",
        kind="front_matter",
        blocks=(Paragraph(template.purpose),),
    )


def _versions(ctx: DocumentContext) -> list[RequirementVersion]:
    return [i.version for i in ctx.items]


# ---------------------------------------------------------------------------
# SRS (FR-DOC-001, FR-DOC-007)
# ---------------------------------------------------------------------------


def requirement_section(ctx: DocumentContext, item: ScopedVersion, number: str) -> Section:
    version = item.version
    speakers = ctx.speakers(version)
    risks = ctx.risks_for(version.id)
    evidence = [
        e for m in ctx.mappings.get(version.id, []) for e in ctx.mapping_evidence.get(m.id, [])
    ]
    evidence += [
        e for f in ctx.findings.get(version.id, []) for e in ctx.finding_evidence.get(f.id, [])
    ]
    evidence += [e for r in risks for e in ctx.risk_evidence.get(r.id, [])]
    conflicts = [
        f"conflict {str(c.id)[:8]} with {ctx.version_label(_other_side(c, version.id))}: "
        f"{c.status.value}" + (f" ({c.resolution.value})" if c.resolution else "")
        for c in ctx.conflicts_for(version.id)
    ]
    pairs = (
        ("Requirement", f"{item.human_id} v{version.version_no}"),
        ("Kind", kind_label(item.human_id)),
        ("Statement", version.statement),
        ("Categories", or_not_recorded(ctx.current_categories(version))),
        ("Priority", version.priority.value if version.priority else NOT_RECORDED),
        ("Business justification", version.justification or NOT_RECORDED),
        ("Source stakeholder(s)", or_not_recorded(speakers)),
        ("Source reference(s)", or_not_recorded(ctx.source_descriptions(version))),
        ("Dependencies", or_not_recorded(version.dependencies or [])),
        ("Assumptions", or_not_recorded(version.assumptions or [])),
        (
            "Applicable regulations (candidate mappings)",
            or_not_recorded(mapping_lines(ctx, version)),
        ),
        ("Security / privacy analysis", or_not_recorded(finding_lines(ctx, version))),
        ("Risk level", risk_level(ctx, version)),
        ("Conflicts", or_not_recorded(conflicts) if conflicts else "none recorded"),
        (
            "Confidence (review signal, not a probability)",
            f"{version.review_signal:.2f}" if version.review_signal is not None else NOT_RECORDED,
        ),
        ("Approval (G1)", or_not_recorded(g1_record(ctx, version))),
        ("Other gates", or_not_recorded(other_gate_record(ctx, version))),
        ("Baseline", item.baseline.label if item.baseline else NOT_RECORDED),
        ("Content hash", version.content_hash),
    )
    criteria = criteria_lines(ctx, version)
    blocks: list = [Fields(pairs)]
    blocks.append(
        Items(tuple(criteria), ordered=True)
        if criteria
        else Paragraph("Acceptance criteria: not recorded for this requirement version.")
    )
    citations = [cite(ctx, version)]
    citations += [cite_mapping(m) for m in ctx.mappings.get(version.id, [])]
    citations += [cite_risk(r) for r in risks]
    citations += cite_evidence(ctx, evidence)
    return Section(
        f"req.{item.human_id}",
        number,
        f"{item.human_id} v{version.version_no}",
        level=3,
        blocks=tuple(blocks),
        citations=tuple(citations),
    )


def _table_section(
    ctx: DocumentContext,
    key: str,
    number: str,
    title: str,
    versions: list[RequirementVersion],
    note: str,
    *,
    level: int = 3,
    extra: tuple = (),  # type: ignore[type-arg]
) -> Section:
    if not versions:
        return empty_section(key, number, title, note, level=level)
    rows = tuple(
        (ctx.version_label(v.id), v.statement, ", ".join(ctx.current_categories(v)) or NOT_RECORDED)
        for v in versions
    )
    return Section(
        key,
        number,
        title,
        level=level,
        blocks=(*extra, Table(("Requirement", "Statement", "Categories"), rows)),
        citations=tuple(cite(ctx, v) for v in versions),
    )


def assemble_srs(ctx: DocumentContext, template: ArtefactTemplate) -> Document:
    empty = template.get("empty_note")
    versions = _versions(ctx)
    items = list(ctx.items)
    sections: list[Section] = [
        control_section(template),
        Section(
            "conventions",
            "1",
            "Introduction and conventions",
            kind="front_matter",
            blocks=(
                Paragraph(template.get("conventions")),
                Notice(COMPLIANCE_ADVISORY_NOTICE),
            ),
        ),
    ]
    # 1.x Scope
    functional_count = sum(1 for i in items if i.human_id.startswith("FR-"))
    counts: dict[str, int] = {}
    for v in versions:
        for cat in ctx.current_categories(v):
            counts[cat] = counts.get(cat, 0) + 1
    sections.append(
        Section(
            "scope",
            "1.1",
            "Scope",
            level=3,
            blocks=(
                Paragraph(
                    f"System context: project '{ctx.project.name}' in the {ctx.project.domain} "
                    f"domain. The specification covers {len(items)} approved requirement "
                    f"version(s): {functional_count} functional "
                    f"and {len(items) - functional_count} non-functional."
                ),
                Table(
                    ("Category", "Requirement versions"),
                    tuple((c, str(n)) for c, n in sorted(counts.items())) or (("none", "0"),),
                ),
            ),
            citations=tuple(cite(ctx, v) for v in versions),
        )
    )
    # 2 Stakeholders and sources
    stakeholder_rows = []
    cited: list[RequirementVersion] = []
    for item in items:
        speakers = ctx.speakers(item.version)
        sources = ctx.source_descriptions(item.version)
        stakeholder_rows.append(
            (
                ctx.version_label(item.version.id),
                or_not_recorded(speakers),
                or_not_recorded(sources),
            )
        )
        cited.append(item.version)
    sections.append(
        Section(
            "stakeholders",
            "2",
            "Stakeholders and sources",
            blocks=(
                Paragraph(
                    "The stakeholders and sources each approved requirement version traces to, "
                    "as recorded. A requirement without a recorded stakeholder is shown as such."
                ),
                Table(
                    ("Requirement", "Source stakeholder(s)", "Source reference(s)"),
                    tuple(stakeholder_rows),
                ),
            ),
            citations=tuple(cite(ctx, v) for v in cited),
        )
        if stakeholder_rows
        else empty_section("stakeholders", "2", "Stakeholders and sources", empty)
    )
    # 3 Specific requirements
    sections.append(
        Section(
            "requirements",
            "3",
            "Specific requirements",
            kind="front_matter",
            blocks=(Paragraph("Each approved requirement version, with its recorded attributes."),),
        )
    )
    for index, item in enumerate(items, start=1):
        sections.append(requirement_section(ctx, item, f"3.{index}"))
    if not items:
        sections.append(empty_section("req.none", "3.1", "Requirements", empty, level=3))
    # 4 Requirements by dimension
    sections.append(
        Section(
            "dimensions",
            "4",
            "Requirements by dimension",
            kind="front_matter",
            blocks=(
                Paragraph(
                    "Functional and non-functional requirements by identifier, then every "
                    "classification dimension of problem statement section 9. A requirement may "
                    "appear under several dimensions (multi-label classification)."
                ),
            ),
        )
    )
    sections.append(
        _table_section(
            ctx,
            "dim.fr",
            "4.1",
            "Functional requirements",
            [i.version for i in items if i.human_id.startswith("FR-")],
            empty,
        )
    )
    sections.append(
        _table_section(
            ctx,
            "dim.nfr",
            "4.2",
            "Non-functional requirements",
            [i.version for i in items if not i.human_id.startswith("FR-")],
            empty,
        )
    )
    for offset, (category, title) in enumerate(DIMENSIONS, start=3):
        matching = [v for v in versions if category.value in ctx.current_categories(v)]
        sections.append(
            _table_section(ctx, f"dim.{category.value}", f"4.{offset}", title, matching, empty)
        )
    # 5 Data requirements (FR-DOC-007)
    sections.extend(_data_sections(ctx, empty))
    # 6 Interface requirements (FR-DOC-007)
    sections.extend(_interface_sections(ctx, empty))
    # 7 Assumptions, dependencies, constraints
    sections.extend(_assumption_sections(ctx, "7", empty, level=3))
    sections.append(
        empty_section(
            "constraints",
            "7.3",
            "Constraints",
            "ReqPilot records no separate constraint field. Regulatory requirements (section 4) "
            "and candidate compliance obligations (section 9) are the recorded constraints.",
            level=3,
        )
    )
    # 8 Open issues
    open_versions = {f.requirement_version_id for f in ctx.open_findings} | {
        c.requirement_version_id for c in ctx.open_clarifications
    }
    in_scope_open = [v for v in versions if v.id in open_versions]
    sections.append(
        _table_section(
            ctx,
            "open_issues",
            "8",
            "Open issues concerning baseline requirements",
            in_scope_open,
            "No open issue concerns a requirement version of this baseline. The project's open "
            "items are listed in the Open-Issues List artefact.",
            level=2,
        )
    )
    # 9 Compliance summary
    sections.append(_compliance_summary(ctx, empty))
    # 10 Risk summary
    sections.extend(_risk_summary(ctx, empty))
    # 11 Traceability references
    sections.append(_trace_summary(ctx, empty))
    # 12 Approval and baseline record
    sections.append(_approval_record(ctx, empty))
    sections.append(
        Section(
            "notices",
            "A",
            "Notices",
            kind="front_matter",
            blocks=(Notice(COMPLIANCE_ADVISORY_NOTICE), Notice(REGISTER_NOTICE)),
        )
    )
    return Document(
        artifact_type=ArtifactType.SRS.value,
        title=template.title,
        template_id=template.template_id,
        template_version=template.version,
        metadata=header_metadata(ctx, template),
        sections=tuple(sections),
    )


def _labelled(ctx: DocumentContext, *categories: RequirementCategory) -> list[RequirementVersion]:
    wanted = {c.value for c in categories}
    return [v for v in _versions(ctx) if wanted & set(ctx.current_categories(v))]


def _data_sections(ctx: DocumentContext, empty: str) -> list[Section]:
    out = [
        Section(
            "data",
            "5",
            "Data requirements",
            kind="front_matter",
            blocks=(Notice(DATA_NOTICE),),
        ),
        _table_section(
            ctx,
            "data.requirements",
            "5.1",
            "Data-related requirements",
            _labelled(ctx, RequirementCategory.DATA_MANAGEMENT, RequirementCategory.PRIVACY),
            empty,
        ),
    ]
    retention_rows, retention_versions, retention_mappings = [], [], []
    for v in _versions(ctx):
        for m in ctx.mappings.get(v.id, []):
            if m.obligation_kind in (
                ObligationKind.RETENTION_OBLIGATION,
                ObligationKind.REPORTING_OBLIGATION,
            ):
                retention_rows.append(
                    (
                        ctx.version_label(v.id),
                        f"{m.control_key} {m.control_title}",
                        m.obligation_kind.value.replace("_", " "),
                        f"candidate, {m.status.value}",
                    )
                )
                retention_versions.append(v)
                retention_mappings.append(m)
        for f in ctx.findings.get(v.id, []):
            if f.family.value in ("retention", "data_minimisation"):
                retention_rows.append(
                    (
                        ctx.version_label(v.id),
                        f.derived_requirement,
                        f.family.value.replace("_", " "),
                        f"level {f.risk_level.value}, {f.status.value}",
                    )
                )
                retention_versions.append(v)
    if retention_rows:
        out.append(
            Section(
                "data.retention",
                "5.2",
                "Retention, minimisation and reporting obligations",
                level=3,
                blocks=(
                    Table(
                        ("Requirement", "Obligation / derived requirement", "Kind", "Status"),
                        tuple(retention_rows),
                    ),
                ),
                citations=tuple(dict.fromkeys(cite(ctx, v) for v in retention_versions))
                + tuple(cite_mapping(m) for m in retention_mappings),
            )
        )
    else:
        out.append(
            empty_section(
                "data.retention",
                "5.2",
                "Retention, minimisation and reporting obligations",
                empty,
                level=3,
            )
        )
    privacy_rows, privacy_versions = [], []
    for v in _versions(ctx):
        for f in ctx.findings.get(v.id, []):
            if f.category.value == "privacy":
                privacy_rows.append(
                    (
                        ctx.version_label(v.id),
                        f.family.value.replace("_", " "),
                        f.derived_requirement,
                        f.risk_level.value,
                        f.status.value,
                    )
                )
                privacy_versions.append(v)
    out.append(
        Section(
            "data.privacy",
            "5.3",
            "Privacy and sensitivity",
            level=3,
            blocks=(
                Table(
                    (
                        "Requirement",
                        "Family",
                        "Derived privacy requirement",
                        "Authoritative level",
                        "Status",
                    ),
                    tuple(privacy_rows),
                ),
            ),
            citations=tuple(dict.fromkeys(cite(ctx, v) for v in privacy_versions)),
        )
        if privacy_rows
        else empty_section("data.privacy", "5.3", "Privacy and sensitivity", empty, level=3)
    )
    audit_versions = _labelled(ctx, RequirementCategory.AUDIT_REPORTING)
    for v in _versions(ctx):
        if (
            any(f.family.value == "audit_logging" for f in ctx.findings.get(v.id, []))
            and v not in audit_versions
        ):
            audit_versions.append(v)
    out.append(
        _table_section(
            ctx, "data.audit", "5.4", "Audit and data-quality requirements", audit_versions, empty
        )
    )
    return out


def _interface_sections(ctx: DocumentContext, empty: str) -> list[Section]:
    out = [
        Section(
            "interfaces",
            "6",
            "Interface requirements",
            kind="front_matter",
            blocks=(Notice(INTERFACE_NOTICE),),
        ),
        _table_section(
            ctx,
            "if.system",
            "6.1",
            "System, external and legacy interfaces",
            _labelled(ctx, RequirementCategory.INTEGRATION),
            empty,
        ),
        _table_section(
            ctx,
            "if.user",
            "6.2",
            "User interfaces",
            _labelled(ctx, RequirementCategory.USABILITY),
            empty,
        ),
    ]
    rows, versions = [], []
    for v in _versions(ctx):
        for f in ctx.findings.get(v.id, []):
            if f.family.value in ("authentication", "authorisation", "session_management"):
                rows.append(
                    (
                        ctx.version_label(v.id),
                        f.family.value.replace("_", " "),
                        f.derived_requirement,
                        f"level {f.risk_level.value}, {f.status.value}",
                    )
                )
                versions.append(v)
    out.append(
        Section(
            "if.auth",
            "6.3",
            "Interface authentication and access constraints",
            level=3,
            blocks=(
                Table(
                    ("Requirement", "Family", "Derived security requirement", "Status"), tuple(rows)
                ),
            ),
            citations=tuple(dict.fromkeys(cite(ctx, v) for v in versions)),
        )
        if rows
        else empty_section(
            "if.auth", "6.3", "Interface authentication and access constraints", empty, level=3
        )
    )
    return out


def _assumption_sections(
    ctx: DocumentContext, number: str, empty: str, *, level: int
) -> list[Section]:
    assumption_rows, a_versions, dependency_rows, d_versions = [], [], [], []
    by_human = {i.human_id: i for i in ctx.items}
    a_no = d_no = 0
    for item in ctx.items:
        v = item.version
        for text in v.assumptions or []:
            a_no += 1
            assumption_rows.append(
                (
                    f"A-{a_no:03d}",
                    str(text),
                    ctx.version_label(v.id),
                    "recorded on the requirement; not verified",
                )
            )
            a_versions.append(v)
        for text in v.dependencies or []:
            d_no += 1
            target = by_human.get(str(text).strip())
            resolved = (
                f"resolves to {target.human_id} v{target.version.version_no} (in this baseline)"
                if target is not None
                else "not resolved to a requirement of this baseline"
            )
            dependency_rows.append((f"D-{d_no:03d}", str(text), ctx.version_label(v.id), resolved))
            d_versions.append(v)
    out = [
        Section(
            f"{number}.assumptions",
            f"{number}.1",
            "Assumptions",
            level=level,
            blocks=(Table(("ID", "Assumption", "Stated by", "Status"), tuple(assumption_rows)),),
            citations=tuple(dict.fromkeys(cite(ctx, v) for v in a_versions)),
        )
        if assumption_rows
        else empty_section(
            f"{number}.assumptions", f"{number}.1", "Assumptions", empty, level=level
        ),
        Section(
            f"{number}.dependencies",
            f"{number}.2",
            "Dependencies",
            level=level,
            blocks=(Table(("ID", "Dependency", "Of", "Resolution"), tuple(dependency_rows)),),
            citations=tuple(dict.fromkeys(cite(ctx, v) for v in d_versions)),
        )
        if dependency_rows
        else empty_section(
            f"{number}.dependencies", f"{number}.2", "Dependencies", empty, level=level
        ),
    ]
    if level == 3:
        out.insert(
            0,
            Section(
                number,
                number,
                "Assumptions, dependencies and constraints",
                kind="front_matter",
                blocks=(
                    Paragraph(
                        "Assumptions are not requirements and are not verified by being listed."
                    ),
                ),
            ),
        )
    return out


def _compliance_rows(ctx: DocumentContext) -> tuple[list[tuple[str, ...]], list[Citation]]:
    gap_keys = {g.control_key for g in ctx.gaps}
    rows: list[tuple[str, ...]] = []
    citations: list[Citation] = []
    for item in ctx.items:
        v = item.version
        for m in ctx.mappings.get(v.id, []):
            evidence = cite_evidence(ctx, ctx.mapping_evidence.get(m.id, []))
            g2 = [
                f"{d.decision.value} by {d.role_exercised.value}"
                for t, d in ctx.approvals_for(m.id)
                if t.gate is Gate.G2_REGULATORY_INTERPRETATION
            ]
            rows.append(
                (
                    ctx.version_label(v.id),
                    f"{m.control_key} {m.control_title}",
                    m.obligation_kind.value.replace("_", " "),
                    f"{m.source_type.value} ({binding(m.source_type.value)})",
                    m.jurisdiction,
                    f"candidate: {m.relationship.value.replace('_', ' ')}",
                    m.status.value
                    + (
                        f"; G2 {', '.join(g2)}"
                        if g2
                        else ("; G2 required" if m.is_high_impact else "")
                    ),
                    "; ".join(c.label for c in evidence) or NOT_RECORDED,
                    "gap recorded for this control"
                    if m.control_key in gap_keys
                    else "no gap recorded",
                    "approved at G1" if g1_record(ctx, v) else NOT_RECORDED,
                )
            )
            citations += [cite(ctx, v), cite_mapping(m), *evidence]
    return rows, citations


COMPLIANCE_COLUMNS = (
    "Requirement",
    "Control / normative item (checklist)",
    "Obligation / checkpoint",
    "Source type (binding)",
    "Jurisdiction",
    "Relationship",
    "Mapping status",
    "Evidence",
    "Gap status",
    "Requirement approval",
)


def _compliance_summary(ctx: DocumentContext, empty: str) -> Section:
    rows, citations = _compliance_rows(ctx)
    if not rows:
        return empty_section("compliance", "9", "Compliance summary (candidate mappings)", empty)
    return Section(
        "compliance",
        "9",
        "Compliance summary (candidate mappings)",
        blocks=(
            Notice(COMPLIANCE_ADVISORY_NOTICE),
            Table(COMPLIANCE_COLUMNS[:7], tuple(r[:7] for r in rows)),
        ),
        citations=tuple(dict.fromkeys(citations)),
    )


RISK_COLUMNS = ("Risk", "Subject", "Category", "L", "I", "Severity (matrix)", "Status", "Owner")


def _risk_row(ctx: DocumentContext, risk: Risk) -> tuple[str, ...]:
    subject = (
        ctx.version_label(risk.requirement_version_id)
        if risk.requirement_version_id
        else "project-level"
    )
    return (
        risk.title,
        subject,
        risk.category.value,
        risk.likelihood.value,
        risk.impact.value,
        risk.severity.value,
        risk.status.value,
        risk.owner_role.value,
    )


def _risk_summary(ctx: DocumentContext, empty: str) -> list[Section]:
    in_scope = [r for v in _versions(ctx) for r in ctx.risks_for(v.id)]
    project = ctx.project_risks()
    out = [
        Section(
            "risk", "10", "Risk summary", kind="front_matter", blocks=(Notice(REGISTER_NOTICE),)
        ),
        Section(
            "risk.requirements",
            "10.1",
            "Risks of baseline requirements",
            level=3,
            blocks=(Table(RISK_COLUMNS, tuple(_risk_row(ctx, r) for r in in_scope)),),
            citations=tuple(
                dict.fromkeys(
                    [
                        cite(ctx, ctx.all_versions[r.requirement_version_id])
                        for r in in_scope
                        if r.requirement_version_id
                    ]
                    + [cite_risk(r) for r in in_scope]
                )
            ),
        )
        if in_scope
        else empty_section(
            "risk.requirements", "10.1", "Risks of baseline requirements", empty, level=3
        ),
        Section(
            "risk.project",
            "10.2",
            "Project-level risks",
            level=3,
            scope="project",
            blocks=(Table(RISK_COLUMNS, tuple(_risk_row(ctx, r) for r in project)),),
            citations=tuple(cite_risk(r) for r in project),
        )
        if project
        else empty_section(
            "risk.project",
            "10.2",
            "Project-level risks",
            "No project-level risk is recorded.",
            level=3,
        ),
    ]
    return out


def _trace_summary(ctx: DocumentContext, empty: str) -> Section:
    if not ctx.items:
        return empty_section("trace", "11", "Traceability references", empty)
    rows = []
    for item in ctx.items:
        cov = version_coverage(ctx.graph, item.human_id, item.version, 0)
        rows.append(
            (
                ctx.version_label(item.version.id),
                "yes" if cov.has_source else "missing",
                "yes" if cov.has_classification else "missing",
                "yes" if cov.has_risk_outcome else "missing",
                "yes" if cov.has_acceptance_criteria else "missing",
                "yes" if cov.has_approved_by else "missing",
            )
        )
    return Section(
        "trace",
        "11",
        "Traceability references",
        blocks=(
            Paragraph(
                "Read from the persisted trace graph when this document was generated. The "
                "Requirements Traceability Matrix artefact carries every link; this document's "
                "own RENDERED_IN and CITES links are recorded after it is generated."
            ),
            Table(
                (
                    "Requirement",
                    "Source",
                    "Classification",
                    "Risk outcome",
                    "Acceptance criteria",
                    "Approval",
                ),
                tuple(rows),
            ),
        ),
        citations=tuple(cite(ctx, i.version) for i in ctx.items),
    )


def _approval_record(ctx: DocumentContext, empty: str) -> Section:
    if not ctx.items:
        return empty_section("approvals", "12", "Approval and baseline record", empty)
    rows = [
        (
            ctx.version_label(i.version.id),
            or_not_recorded(g1_record(ctx, i.version)),
            or_not_recorded(other_gate_record(ctx, i.version)),
            i.baseline.label if i.baseline else NOT_RECORDED,
            (i.member.version_hash[:16] if i.member else NOT_RECORDED),
        )
        for i in ctx.items
    ]
    return Section(
        "approvals",
        "12",
        "Approval and baseline record",
        blocks=(
            Table(
                (
                    "Requirement",
                    "G1 decisions",
                    "Other gate decisions",
                    "Baseline",
                    "Baselined hash",
                ),
                tuple(rows),
            ),
        ),
        citations=tuple(cite(ctx, i.version) for i in ctx.items),
    )


# ---------------------------------------------------------------------------
# user stories (FR-DOC-002) and use cases (FR-DOC-003)
# ---------------------------------------------------------------------------


def _want(statement: str) -> str:
    match = _SHALL.match(statement)
    if match:
        return "the system to " + " ".join(match.group(1).split()).rstrip(".")
    return "the following: " + " ".join(statement.split())


def assemble_user_stories(ctx: DocumentContext, template: ArtefactTemplate) -> Document:
    sections: list[Section] = [control_section(template)]
    functional = [i for i in ctx.items if i.human_id.startswith("FR-")]
    for index, item in enumerate(functional, start=1):
        v = item.version
        roles = ctx.speakers(v)
        role = " / ".join(roles) if roles else "stakeholder (not recorded)"
        value = v.justification or "business value not recorded"
        criteria = criteria_lines(ctx, v)
        story_id = f"US-{item.human_id}-v{v.version_no}"
        sections.append(
            Section(
                f"story.{item.human_id}",
                f"{index}",
                story_id,
                blocks=(
                    Paragraph(f"As a {role}, I want {_want(v.statement)}, so that {value}."),
                    Fields(
                        (
                            ("Story ID", story_id),
                            ("Requirement", ctx.version_label(v.id)),
                            ("Priority", v.priority.value if v.priority else NOT_RECORDED),
                        )
                    ),
                    Items(tuple(criteria), ordered=True)
                    if criteria
                    else Paragraph(
                        "Acceptance criteria: not recorded for this requirement version; "
                        "none are generated."
                    ),
                ),
                citations=(cite(ctx, v),),
            )
        )
    if not functional:
        sections.append(
            empty_section(
                "story.none",
                "1",
                "User stories",
                "No functional requirement version is in this baseline.",
            )
        )
    nfr = [i for i in ctx.items if not i.human_id.startswith("FR-")]
    if nfr:
        sections.append(
            Section(
                "story.nfr",
                str(len(functional) + 1),
                "Non-functional requirements (not expressed as stories)",
                blocks=(Items(tuple(ctx.version_label(i.version.id) for i in nfr)),),
                citations=tuple(cite(ctx, i.version) for i in nfr),
            )
        )
    return Document(
        ArtifactType.USER_STORIES.value,
        template.title,
        template.template_id,
        template.version,
        header_metadata(ctx, template),
        tuple(sections),
    )


def assemble_use_cases(ctx: DocumentContext, template: ArtefactTemplate) -> Document:
    sections: list[Section] = [control_section(template)]
    functional = [i for i in ctx.items if i.human_id.startswith("FR-")]
    for index, item in enumerate(functional, start=1):
        v = item.version
        criteria = ctx.criteria.get(v.id, [])
        actors = ctx.speakers(v)
        uc_id = f"UC-{item.human_id}-v{v.version_no}"
        evidence = [
            e for m in ctx.mappings.get(v.id, []) for e in ctx.mapping_evidence.get(m.id, [])
        ]
        sections.append(
            Section(
                f"uc.{item.human_id}",
                f"{index}",
                uc_id,
                blocks=(
                    Fields(
                        (
                            ("Use-case ID", uc_id),
                            ("Name", " ".join(v.statement.split())[:120]),
                            ("Primary actor", or_not_recorded(actors)),
                            ("Goal", v.statement),
                            ("Preconditions", or_not_recorded(c.given_text for c in criteria)),
                            ("Trigger", or_not_recorded(c.when_text for c in criteria)),
                            ("Postconditions", or_not_recorded(c.then_text for c in criteria)),
                            ("Alternate / exception flows", NOT_RECORDED),
                            (
                                "Related requirements",
                                or_not_recorded([ctx.version_label(v.id), *(v.dependencies or [])]),
                            ),
                            (
                                "Evidence / trace references",
                                or_not_recorded(
                                    [
                                        *ctx.source_descriptions(v),
                                        *(c.label for c in cite_evidence(ctx, evidence)),
                                    ]
                                ),
                            ),
                        )
                    ),
                    Items(
                        tuple(f"When {c.when_text}, then {c.then_text}" for c in criteria),
                        ordered=True,
                    )
                    if criteria
                    else Paragraph(
                        "Main flow: not recorded "
                        "(no acceptance criteria are recorded for this version)."
                    ),
                ),
                citations=(cite(ctx, v), *cite_evidence(ctx, evidence)),
            )
        )
    if not functional:
        sections.append(
            empty_section(
                "uc.none",
                "1",
                "Use cases",
                "No functional requirement version is in this baseline.",
            )
        )
    return Document(
        ArtifactType.USE_CASES.value,
        template.title,
        template.template_id,
        template.version,
        header_metadata(ctx, template),
        tuple(sections),
    )


# ---------------------------------------------------------------------------
# compliance-control matrix (FR-DOC-004)
# ---------------------------------------------------------------------------


def assemble_compliance_matrix(ctx: DocumentContext, template: ArtefactTemplate) -> Document:
    rows, citations = _compliance_rows(ctx)
    sections = [
        control_section(template),
        Section(
            "notice",
            "1",
            "Advisory notice",
            kind="front_matter",
            blocks=(Notice(COMPLIANCE_ADVISORY_NOTICE),),
        ),
        Section(
            "matrix",
            "2",
            "Candidate control mappings",
            blocks=(Table(COMPLIANCE_COLUMNS, tuple(rows)),),
            citations=tuple(dict.fromkeys(citations)),
        )
        if rows
        else empty_section(
            "matrix",
            "2",
            "Candidate control mappings",
            "No candidate mapping is recorded for a requirement version of this baseline.",
        ),
    ]
    implied = [r for r in rows if r[2] != "control"]
    sections.append(
        Section(
            "checkpoints",
            "3",
            "Implied approval and audit checkpoints and obligations",
            blocks=(
                Table(
                    ("Requirement", "Checkpoint / obligation", "Kind"),
                    tuple((r[0], r[1], r[2]) for r in implied),
                ),
            ),
            citations=tuple(
                c
                for c in dict.fromkeys(citations)
                if c.kind in ("requirement_version", "compliance_mapping")
            ),
        )
        if implied
        else empty_section(
            "checkpoints",
            "3",
            "Implied approval and audit checkpoints and obligations",
            "No implied checkpoint or obligation is recorded for this baseline.",
        )
    )
    gap_rows = tuple(
        (
            g.control_key,
            g.control_title,
            g.obligation_kind.value.replace("_", " "),
            f"{g.checklist_domain} x {g.checklist_jurisdiction}",
            g.origin.value,
            "high-impact" if g.is_high_impact else "-",
        )
        for g in ctx.gaps
    )
    sections.append(
        Section(
            "gaps",
            "4",
            "Expected-control gaps (human review required)",
            scope="project",
            blocks=(
                Table(("Control", "Title", "Kind", "Checklist", "Origin", "Impact"), gap_rows),
            ),
            citations=tuple(
                Citation("compliance_gap", str(g.id), f"gap {g.control_key}") for g in ctx.gaps
            ),
        )
        if gap_rows
        else empty_section(
            "gaps",
            "4",
            "Expected-control gaps (human review required)",
            "No expected-control gap is recorded by the latest compliance analysis run.",
        )
    )
    return Document(
        ArtifactType.COMPLIANCE_MATRIX.value,
        template.title,
        template.template_id,
        template.version,
        header_metadata(ctx, template),
        tuple(sections),
    )


# ---------------------------------------------------------------------------
# risk register (FR-DOC-006)
# ---------------------------------------------------------------------------


def _risk_detail(ctx: DocumentContext, risk: Risk, number: str) -> Section:
    g8 = [
        f"{d.decision.value} by {d.role_exercised.value} on {d.decided_at.date().isoformat()}"
        for t, d in ctx.approvals_for(risk.id)
        if t.gate is Gate.G8_HIGH_SEVERITY_RISK
    ]
    open_g8 = [t for t in ctx.tasks_for(risk.id) if t.status is ApprovalTaskStatus.OPEN]
    mitigations = []
    for m in ctx.mitigations.get(risk.id, []):
        if not m.is_ai_generated:
            label = "human-authored"
        elif m.status.value == "accepted":
            label = "AI-suggested, accepted by a human"
        elif m.status.value == "rejected":
            label = "AI-suggested, rejected"
        else:
            label = "AI-suggested - requires human validation"
        mitigations.append(f"{m.suggestion} ({label})")
    evidence = cite_evidence(ctx, ctx.risk_evidence.get(risk.id, []))
    subject = (
        ctx.version_label(risk.requirement_version_id)
        if risk.requirement_version_id
        else "project-level"
    )
    citations: list[Citation] = [cite_risk(risk), *evidence]
    if risk.requirement_version_id is not None:
        citations.insert(0, cite(ctx, ctx.all_versions[risk.requirement_version_id]))
    return Section(
        f"risk.{risk.id}",
        number,
        risk.title,
        level=3,
        scope="baseline" if risk.scope is RiskScope.REQUIREMENT else "project",
        blocks=(
            Fields(
                (
                    ("Risk ID", str(risk.id)),
                    ("Subject", subject),
                    ("Category", risk.category.value),
                    ("Description", risk.description),
                    ("Likelihood", f"{risk.likelihood.value} - {risk.likelihood_rationale}"),
                    ("Impact", f"{risk.impact.value} - {risk.impact_rationale}"),
                    (
                        "Severity",
                        f"{risk.severity.value} (matrix {risk.matrix_version}; not recalculated)",
                    ),
                    ("Owner role", risk.owner_role.value),
                    ("Status", risk.status.value),
                    (
                        "G8",
                        or_not_recorded(g8)
                        if risk.severity.value == "high"
                        else "not applicable (severity below high)",
                    ),
                    ("Open G8 tasks", str(len(open_g8))),
                    ("Decision rationale", risk.decision_rationale or NOT_RECORDED),
                    ("Detected by", risk.detected_by.value),
                )
            ),
            Items(tuple(mitigations))
            if mitigations
            else Paragraph("Mitigation considerations: not recorded."),
            Items(tuple(c.label for c in evidence))
            if evidence
            else Paragraph("Evidence: not recorded."),
        ),
        citations=tuple(citations),
    )


def assemble_risk_register(ctx: DocumentContext, template: ArtefactTemplate) -> Document:
    order = {"high": 0, "medium": 1, "low": 2}
    scoped = sorted(
        (r for v in _versions(ctx) for r in ctx.risks_for(v.id)),
        key=lambda r: (order.get(r.severity.value, 3), as_utc(r.created_at), r.title_key),
    )
    project = sorted(
        ctx.project_risks(),
        key=lambda r: (order.get(r.severity.value, 3), as_utc(r.created_at), r.title_key),
    )
    excluded = sum(
        1
        for r in ctx.risks
        if r.requirement_version_id is not None
        and r.requirement_version_id not in ctx.scope.version_ids
    )
    sections: list[Section] = [
        control_section(template),
        Section(
            "notice",
            "1",
            "Notice",
            kind="front_matter",
            blocks=(
                Notice(REGISTER_NOTICE),
                Paragraph(
                    f"{excluded} risk(s) recorded against requirement versions outside this "
                    "baseline are not included: those versions are not authoritative."
                ),
            ),
        ),
    ]
    all_rows = [*scoped, *project]
    sections.append(
        Section(
            "summary",
            "2",
            "Register summary",
            scope="baseline" if scoped else "project",
            blocks=(Table(RISK_COLUMNS, tuple(_risk_row(ctx, r) for r in all_rows)),),
            citations=tuple(
                dict.fromkeys(
                    [
                        cite(ctx, ctx.all_versions[r.requirement_version_id])
                        for r in scoped
                        if r.requirement_version_id
                    ]
                    + [cite_risk(r) for r in all_rows]
                )
            ),
        )
        if all_rows
        else empty_section(
            "summary",
            "2",
            "Register summary",
            "No risk is recorded for this baseline or the project.",
        )
    )
    sections.append(
        Section(
            "requirement_risks",
            "3",
            "Requirement-level risks",
            kind="front_matter",
            blocks=(Paragraph(f"{len(scoped)} risk(s)."),),
        )
    )
    for index, risk in enumerate(scoped, start=1):
        sections.append(_risk_detail(ctx, risk, f"3.{index}"))
    sections.append(
        Section(
            "project_risks",
            "4",
            "Project-level risks",
            kind="front_matter",
            blocks=(Paragraph(f"{len(project)} risk(s)."),),
        )
    )
    for index, risk in enumerate(project, start=1):
        sections.append(_risk_detail(ctx, risk, f"4.{index}"))
    return Document(
        ArtifactType.RISK_REGISTER.value,
        template.title,
        template.template_id,
        template.version,
        header_metadata(ctx, template),
        tuple(sections),
    )


# ---------------------------------------------------------------------------
# assumptions and dependencies (FR-DOC-005) and open issues (FR-DOC-005)
# ---------------------------------------------------------------------------


def assemble_assumptions(ctx: DocumentContext, template: ArtefactTemplate) -> Document:
    empty = "Nothing of this kind is recorded on a requirement version of this baseline."
    sections = [control_section(template), *_assumption_sections(ctx, "1", empty, level=2)]
    return Document(
        ArtifactType.ASSUMPTIONS_DEPENDENCIES.value,
        template.title,
        template.template_id,
        template.version,
        header_metadata(ctx, template),
        tuple(sections),
    )


def assemble_open_issues(ctx: DocumentContext, template: ArtefactTemplate) -> Document:
    """The project's unresolved items, live. Nothing here is presented as resolved."""
    in_scope = ctx.scope.version_ids

    def about(version_id: uuid.UUID) -> str:
        label = ctx.version_label(version_id)
        return label if version_id in in_scope else f"{label} (not in this baseline; not approved)"

    groups: list[tuple[str, str, tuple[str, ...], list[tuple[str, ...]], list[Citation]]] = []
    groups.append(
        (
            "clarifications",
            "Open clarifications",
            ("Clarification", "About", "Question"),
            [
                (str(c.id)[:8], about(c.requirement_version_id), c.question)
                for c in ctx.open_clarifications
            ],
            [
                Citation("clarification", str(c.id), f"clarification {str(c.id)[:8]}")
                for c in ctx.open_clarifications
            ],
        )
    )
    groups.append(
        (
            "findings",
            "Open quality findings",
            ("Finding", "About", "Type", "Severity"),
            [
                (
                    str(f.id)[:8],
                    about(f.requirement_version_id),
                    f.finding_type.value,
                    f.severity.value,
                )
                for f in ctx.open_findings
            ],
            [
                Citation("quality_finding", str(f.id), f"finding {str(f.id)[:8]}")
                for f in ctx.open_findings
            ],
        )
    )
    open_conflicts = [c for c in ctx.conflicts if c.status.value in ("open", "under_review")]
    groups.append(
        (
            "conflicts",
            "Conflicts not yet resolved",
            ("Conflict", "Between", "Status", "Stakeholder disagreement (G4)"),
            [
                (
                    str(c.id)[:8],
                    f"{about(c.version_a_id)} / {about(c.version_b_id)}",
                    c.status.value,
                    "yes" if c.involves_stakeholder_disagreement else "no",
                )
                for c in open_conflicts
            ],
            [Citation("conflict", str(c.id), f"conflict {str(c.id)[:8]}") for c in open_conflicts],
        )
    )
    groups.append(
        (
            "gaps",
            "Expected-control gaps",
            ("Control", "Title", "Origin"),
            [(g.control_key, g.control_title, g.origin.value) for g in ctx.gaps],
            [Citation("compliance_gap", str(g.id), f"gap {g.control_key}") for g in ctx.gaps],
        )
    )
    unreviewed = [r for r in ctx.risks if r.status.value in ("proposed", "under_review")]
    groups.append(
        (
            "risks",
            "Risks not yet reviewed",
            ("Risk", "Subject", "Severity", "Status"),
            [
                (
                    r.title,
                    about(r.requirement_version_id)
                    if r.requirement_version_id
                    else "project-level",
                    r.severity.value,
                    r.status.value,
                )
                for r in unreviewed
            ],
            [Citation("risk_issue", str(r.id), f"risk {str(r.id)[:8]}") for r in unreviewed],
        )
    )
    open_tasks = [t for t in ctx.tasks if t.status is ApprovalTaskStatus.OPEN]
    groups.append(
        (
            "tasks",
            "Open approval tasks",
            ("Task", "Gate", "Needs", "Subject"),
            [
                (
                    str(t.id)[:8],
                    t.gate.value,
                    t.required_role.value,
                    f"{t.subject_type} {str(t.subject_id)[:8]}",
                )
                for t in open_tasks
            ],
            [Citation("approval_task", str(t.id), f"task {str(t.id)[:8]}") for t in open_tasks],
        )
    )
    sections: list[Section] = [control_section(template)]
    for index, (key, title, columns, rows, citations) in enumerate(groups, start=1):
        if rows:
            sections.append(
                Section(
                    key,
                    str(index),
                    title,
                    scope="project",
                    blocks=(Table(columns, tuple(rows)),),
                    citations=tuple(citations),
                )
            )
        else:
            sections.append(empty_section(key, str(index), title, "None open."))
    return Document(
        ArtifactType.OPEN_ISSUES.value,
        template.title,
        template.template_id,
        template.version,
        header_metadata(ctx, template),
        tuple(sections),
    )


# ---------------------------------------------------------------------------
# RTM (FR-TRC-002)
# ---------------------------------------------------------------------------


def assemble_rtm(ctx: DocumentContext, template: ArtefactTemplate, rtm: RtmBuilder) -> Document:
    rows = rtm.rows(ctx.scope, ctx.graph)
    columns = tuple(
        title for key, title in RTM_COLUMNS if key not in ("version_id", "content_hash")
    )
    keys = [key for key, _t in RTM_COLUMNS if key not in ("version_id", "content_hash")]
    table_rows = tuple(tuple(r.get(k) for k in keys) for r in rows)
    sections = [
        control_section(template),
        Section(
            "matrix",
            "1",
            "Traceability matrix",
            blocks=(Table(columns, table_rows),),
            citations=tuple(cite(ctx, i.version) for i in ctx.items),
        )
        if rows
        else empty_section(
            "matrix", "1", "Traceability matrix", "No requirement version is in this baseline."
        ),
    ]
    if ctx.items:
        cov = [version_coverage(ctx.graph, i.human_id, i.version, 0) for i in ctx.items]
        sections.append(
            Section(
                "coverage",
                "2",
                "Chain completeness at generation",
                blocks=(
                    Paragraph(
                        "Per architecture N.3, evaluated on the persisted graph before this "
                        "document's own links were recorded, so the RENDERED_IN element "
                        "reflects earlier artefacts only."
                    ),
                    Table(
                        ("Requirement", "Fully traced", "Missing"),
                        tuple(
                            (
                                f"{c.human_id} v{c.version_no}",
                                "yes" if c.fully_traced else "no",
                                "; ".join(c.missing) or "-",
                            )
                            for c in cov
                        ),
                    ),
                ),
                citations=tuple(cite(ctx, i.version) for i in ctx.items),
            )
        )
    return Document(
        ArtifactType.RTM.value,
        template.title,
        template.template_id,
        template.version,
        header_metadata(ctx, template),
        tuple(sections),
    )


Assembler = Callable[[DocumentContext, ArtefactTemplate], Document]
