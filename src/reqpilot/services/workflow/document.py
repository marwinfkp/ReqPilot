"""The workflow as a P8 structured document (``FR-WFL-008``; ADR-007).

**No second document subsystem.** The workflow is rendered through P8's
:class:`~reqpilot.artifacts.model.Document` and its Markdown and DOCX renderers -
the same sections, blocks and citations in both formats, the same escaping, the
same reproducibility (``docx`` built from the document, stamped with the workflow's
own last-change time). What is new here is only the assembly: a deterministic
projection of the persisted workflow - its current revision, with every human edit
- into sections. No model writes any of it.

Two statements are printed from this module, never generated: that the workflow's
gates (production readiness included) are gates of the generated project's own
process and not ReqPilot's G1-G8 (architecture M.1, M.4), and that an AI-suggested
mitigation is a suggestion requiring human validation (``FR-RSK-005``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from reqpilot.artifacts.model import (
    Block,
    Citation,
    Document,
    Fields,
    Items,
    Notice,
    Paragraph,
    Section,
    Table,
)
from reqpilot.domain.enums import WorkflowActivityKind, WorkflowGateKind
from reqpilot.domain.workflow.plan import (
    SOURCE_COMPLIANCE_MAPPING,
    SOURCE_RISK,
    SOURCE_RISK_MITIGATION,
    SOURCE_SECURITY_FINDING,
    ActivityPlan,
    GatePlan,
    PhasePlan,
    SourceLink,
    WorkflowPlan,
)

WORKFLOW_ARTIFACT_TYPE = "workflow"
WORKFLOW_TEMPLATE_ID = "reqpilot.workflow"
WORKFLOW_DOCUMENT_VERSION = "1.0.0"

GATE_NOTICE = (
    "The gates in this workflow - the production-readiness approval included - are gates of "
    "this project's own delivery process. They are not ReqPilot gates G1-G8, and ReqPilot does "
    "not operate or enforce them: it records them so that the project team can."
)
GENERATION_NOTICE = (
    "Generated deterministically from the project's approved records: the SDLC selection that "
    "passed G6, the approved requirement baseline, the project's candidate compliance mappings, "
    "its derived security and privacy requirements and its risk register. No model wrote any "
    "part of it. A candidate compliance mapping records a potentially applicable control; it is "
    "not a legal determination."
)
MITIGATION_NOTICE = (
    "A mitigation marked 'AI-suggested - requires human validation' is a suggestion recorded in "
    "the risk register that no human has accepted yet (FR-RSK-005)."
)


@dataclass(frozen=True)
class WorkflowDocumentMeta:
    project_name: str
    workflow_id: str
    revision: int
    status: str
    sdlc_run_id: str
    baseline_label: str
    g6_decisions: int
    content_hash: str
    generated_at: str
    last_changed_at: str


@dataclass(frozen=True)
class ChangeView:
    revision: int
    at: str
    role: str
    operation: str
    element: str
    fields: tuple[str, ...]
    reason: str


def _join(values: Sequence[str]) -> str:
    return "; ".join(values) if values else "-"


def _kind_label(kind: str) -> str:
    return kind.replace("_", " ")


def workflow_document(
    plan: WorkflowPlan,
    meta: WorkflowDocumentMeta,
    *,
    role_label: Callable[[str], str],
    source_label: Mapping[tuple[str, str], str],
    changes: Sequence[ChangeView] = (),
) -> Document:
    """Build the document; the caller validates and renders it."""
    workflow_cite = Citation("workflow", meta.workflow_id, f"workflow r{meta.revision}")

    def roles(values: Sequence[str]) -> str:
        return _join([role_label(r) for r in values])

    def derived(links: Sequence[SourceLink]) -> str:
        labels = [
            source_label.get((s.source_type, s.source_id), f"{s.source_type} {s.source_id[:8]}")
            for s in links
            if s.source_type != "sdlc_candidate"
        ]
        return _join(list(dict.fromkeys(labels))) if labels else "template"

    def cites(links: Sequence[SourceLink]) -> list[Citation]:
        out: list[Citation] = []
        for s in links:
            if s.source_type in (SOURCE_COMPLIANCE_MAPPING, SOURCE_RISK):
                label = source_label.get((s.source_type, s.source_id), s.source_id)
                out.append(Citation(s.source_type, s.source_id, label))
        return out

    phase_names = {p.key: p.name for p in plan.phases}
    sections: list[Section] = [
        Section(
            key="overview",
            number="1",
            title="Overview",
            kind="front_matter",
            scope="project",
            blocks=(
                Fields(
                    (
                        ("Project", meta.project_name),
                        ("Selected SDLC (G6)", plan.candidate_label),
                        ("Composition", ", ".join(m.replace("_", "-") for m in plan.composition)),
                        ("Approved baseline", meta.baseline_label),
                        ("SDLC run", meta.sdlc_run_id),
                        ("G6 co-approval", f"{meta.g6_decisions} of 4 approval decisions recorded"),
                        ("Workflow", meta.workflow_id),
                        ("Revision", str(meta.revision)),
                        ("Status", meta.status.replace("_", " ")),
                        ("Template", plan.template_ref),
                        ("Content hash", meta.content_hash),
                    )
                ),
                Paragraph(plan.approach),
                Notice(GENERATION_NOTICE),
                Notice(GATE_NOTICE),
                Notice(MITIGATION_NOTICE),
            ),
        )
    ]

    number = 2
    if plan.open_items:
        sections.append(
            Section(
                key="open-items",
                number=str(number),
                title="Open items",
                scope="project",
                blocks=(
                    Notice(
                        "The project's records leave these items for a human to resolve. They are "
                        "listed, never filled in."
                    ),
                    Table(
                        ("Code", "Item"),
                        tuple((f.code, f.message) for f in plan.open_items),
                    ),
                ),
                citations=(workflow_cite,),
            )
        )
    else:
        sections.append(
            Section(
                key="open-items",
                number=str(number),
                title="Open items",
                kind="empty",
                scope="project",
                blocks=(Paragraph("The project's records leave no open item."),),
            )
        )

    for phase in plan.phases:
        number += 1
        sections.append(
            _phase_section(phase, str(number), phase_names, roles, derived, cites, workflow_cite)
        )

    # summaries
    checkpoints = [
        (p, g) for p, g in plan.gates() if g.kind == str(WorkflowGateKind.COMPLIANCE_CHECKPOINT)
    ]
    number += 1
    sections.append(
        _summary(
            "compliance-checkpoints",
            str(number),
            "Compliance checkpoints",
            ("Checkpoint", "Phase", "Approvers", "Derived from mapping(s)"),
            tuple(
                (g.name, p.name, roles(g.approver_roles), derived(g.sources))
                for p, g in checkpoints
            ),
            [c for _p, g in checkpoints for c in cites(g.sources)],
            "This project's records hold no eligible compliance mapping.",
        )
    )
    risk_kinds = {
        str(WorkflowActivityKind.RISK_TREATMENT),
        str(WorkflowActivityKind.RISK_VERIFICATION),
    }
    risk_rows = [(p, a) for p, a in plan.activities() if a.kind in risk_kinds]
    number += 1
    sections.append(
        _summary(
            "risk-treatment",
            str(number),
            "HIGH-risk treatment",
            ("Activity", "Phase", "HIGH risk", "Mitigation"),
            tuple(
                (
                    a.name,
                    p.name,
                    derived([s for s in a.sources if s.source_type == SOURCE_RISK]),
                    derived([s for s in a.sources if s.source_type == SOURCE_RISK_MITIGATION])
                    if any(s.source_type == SOURCE_RISK_MITIGATION for s in a.sources)
                    else "none recorded",
                )
                for p, a in risk_rows
            ),
            [c for _p, a in risk_rows for c in cites(a.sources)],
            "The risk register holds no eligible HIGH risk.",
        )
    )
    security = [
        (p, a) for p, a in plan.activities() if a.kind == str(WorkflowActivityKind.SECURITY)
    ]
    number += 1
    sections.append(
        _summary(
            "security-activities",
            str(number),
            "Security activities",
            ("Activity", "Phase", "Derived security and privacy requirements"),
            tuple(
                (
                    a.name,
                    p.name,
                    derived([s for s in a.sources if s.source_type == SOURCE_SECURITY_FINDING]),
                )
                for p, a in security
            ),
            [workflow_cite] if security else [],
            "The approved set carries no derived security or privacy requirement.",
        )
    )
    number += 1
    if changes:
        sections.append(
            Section(
                key="change-log",
                number=str(number),
                title="Change log",
                scope="project",
                blocks=(
                    Table(
                        ("Revision", "When", "Role", "Change", "Element", "Fields", "Reason"),
                        tuple(
                            (
                                str(c.revision),
                                c.at,
                                c.role,
                                c.operation,
                                c.element,
                                ", ".join(c.fields),
                                c.reason,
                            )
                            for c in changes
                        ),
                    ),
                ),
                citations=(workflow_cite,),
            )
        )
    else:
        sections.append(
            Section(
                key="change-log",
                number=str(number),
                title="Change log",
                kind="empty",
                scope="project",
                blocks=(Paragraph("The workflow is unedited since generation (revision 1)."),),
            )
        )

    return Document(
        artifact_type=WORKFLOW_ARTIFACT_TYPE,
        title=f"Project SDLC workflow - {plan.candidate_label}",
        template_id=WORKFLOW_TEMPLATE_ID,
        template_version=WORKFLOW_DOCUMENT_VERSION,
        metadata=(
            ("Project", meta.project_name),
            ("Workflow", meta.workflow_id),
            ("Revision", str(meta.revision)),
            ("Selected SDLC", plan.candidate_label),
        ),
        sections=tuple(sections),
        stamp=(("Generated", meta.generated_at), ("Last changed", meta.last_changed_at)),
    )


def _phase_section(
    phase: PhasePlan,
    number: str,
    phase_names: Mapping[str, str],
    roles: Callable[[Sequence[str]], str],
    derived: Callable[[Sequence[SourceLink]], str],
    cites: Callable[[Sequence[SourceLink]], list[Citation]],
    workflow_cite: Citation,
) -> Section:
    blocks: list[Block] = [
        Paragraph(phase.description),
        Fields(
            tuple(
                pair
                for pair in (
                    ("Stages", ", ".join(phase.stages)),
                    ("Cycle", phase.cycle or ""),
                    (
                        "Verifies",
                        phase_names.get(phase.verifies, phase.verifies or "")
                        if phase.verifies
                        else "",
                    ),
                    ("Responsible roles", roles(phase.responsible_roles)),
                    ("Origin", phase.origin),
                )
                if pair[1]
            )
        ),
    ]
    for label, values in (
        ("Deliverables", phase.deliverables),
        ("Entry criteria", phase.entry_criteria),
        ("Exit criteria", phase.exit_criteria),
        ("Testing requirements", phase.testing_requirements),
        ("Traceability requirements", phase.traceability_requirements),
    ):
        blocks.append(Paragraph(f"{label}:"))
        blocks.append(Items(tuple(values)))
    blocks.append(
        Table(
            ("#", "Activity", "Kind", "Responsible", "Deliverables", "Derived from", "Origin"),
            tuple(
                (
                    str(i),
                    _activity_text(a),
                    _kind_label(a.kind),
                    roles(a.responsible_roles),
                    _join(a.deliverables),
                    derived(a.sources),
                    a.origin,
                )
                for i, a in enumerate(phase.activities, start=1)
            ),
        )
    )
    if phase.gates:
        blocks.append(
            Table(
                ("Gate", "Kind", "Approvers", "Required evidence", "Entry", "Exit", "Derived from"),
                tuple(
                    (
                        _gate_text(g),
                        _kind_label(g.kind),
                        roles(g.approver_roles),
                        _join(g.required_evidence),
                        _join(g.entry_criteria),
                        _join(g.exit_criteria),
                        derived(g.sources),
                    )
                    for g in phase.gates
                ),
            )
        )
    citations = [workflow_cite]
    for activity in phase.activities:
        citations.extend(cites(activity.sources))
    for gate in phase.gates:
        citations.extend(cites(gate.sources))
    return Section(
        key=f"phase-{phase.key}",
        number=number,
        title=f"Phase: {phase.name}",
        scope="project",
        blocks=tuple(blocks),
        citations=tuple(dict.fromkeys(citations)),
    )


def _activity_text(activity: ActivityPlan) -> str:
    return f"{activity.name} - {activity.description}" if activity.description else activity.name


def _gate_text(gate: GatePlan) -> str:
    return f"{gate.name} - {gate.purpose}"


def _summary(
    key: str,
    number: str,
    title: str,
    columns: tuple[str, ...],
    rows: tuple[tuple[str, ...], ...],
    citations: list[Citation],
    empty_text: str,
) -> Section:
    if not rows:
        return Section(
            key=key,
            number=number,
            title=title,
            kind="empty",
            scope="project",
            blocks=(Paragraph(empty_text),),
        )
    return Section(
        key=key,
        number=number,
        title=title,
        scope="project",
        blocks=(Table(columns, rows),),
        citations=tuple(dict.fromkeys(citations)),
    )
