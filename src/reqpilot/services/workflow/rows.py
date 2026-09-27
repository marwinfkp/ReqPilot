"""Converting between a :class:`WorkflowPlan` and its persisted rows - in both directions.

One definition of a workflow's content: generation writes the plan as rows, and
every read, edit, validation and export rebuilds the plan from the rows, so the
content hash stored on ``workflow`` always means the same thing. A round trip is
exact (tested).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass

from reqpilot.domain.models.workflow import (
    Workflow,
    WorkflowActivity,
    WorkflowGate,
    WorkflowPhase,
    WorkflowSource,
)
from reqpilot.domain.workflow.plan import (
    ActivityPlan,
    Finding,
    GatePlan,
    PhasePlan,
    SourceLink,
    WorkflowPlan,
)


@dataclass(frozen=True)
class WorkflowRows:
    phases: tuple[WorkflowPhase, ...]
    activities: tuple[WorkflowActivity, ...]
    gates: tuple[WorkflowGate, ...]
    sources: tuple[WorkflowSource, ...]


def rows_from_plan(
    project_id: uuid.UUID, workflow_id: uuid.UUID, plan: WorkflowPlan
) -> WorkflowRows:
    phases: list[WorkflowPhase] = []
    activities: list[WorkflowActivity] = []
    gates: list[WorkflowGate] = []
    sources: list[WorkflowSource] = []

    def link_rows(element_type: str, element_id: uuid.UUID, links: tuple[SourceLink, ...]) -> None:
        for link in links:
            sources.append(
                WorkflowSource(
                    id=uuid.uuid4(),
                    project_id=project_id,
                    workflow_id=workflow_id,
                    element_type=element_type,
                    element_id=element_id,
                    source_type=link.source_type,
                    source_id=uuid.UUID(link.source_id),
                    relation=link.relation,
                    source_hash=link.source_hash,
                )
            )

    link_rows("workflow", workflow_id, plan.sources)
    for position, phase in enumerate(plan.phases, start=1):
        phase_id = uuid.uuid4()
        phases.append(
            WorkflowPhase(
                id=phase_id,
                project_id=project_id,
                workflow_id=workflow_id,
                position=position,
                key=phase.key,
                name=phase.name,
                description=phase.description,
                stages=list(phase.stages),
                cycle=phase.cycle,
                verifies_phase_key=phase.verifies,
                responsible_roles=list(phase.responsible_roles),
                deliverables=list(phase.deliverables),
                entry_criteria=list(phase.entry_criteria),
                exit_criteria=list(phase.exit_criteria),
                testing_requirements=list(phase.testing_requirements),
                traceability_requirements=list(phase.traceability_requirements),
                origin=phase.origin,
            )
        )
        for a_pos, activity in enumerate(phase.activities, start=1):
            activity_id = uuid.uuid4()
            activities.append(
                WorkflowActivity(
                    id=activity_id,
                    project_id=project_id,
                    workflow_id=workflow_id,
                    phase_id=phase_id,
                    position=a_pos,
                    key=activity.key,
                    kind=activity.kind,
                    name=activity.name,
                    description=activity.description,
                    responsible_roles=list(activity.responsible_roles),
                    deliverables=list(activity.deliverables),
                    mandatory=activity.mandatory,
                    origin=activity.origin,
                )
            )
            link_rows("activity", activity_id, activity.sources)
        for g_pos, gate in enumerate(phase.gates, start=1):
            gate_id = uuid.uuid4()
            gates.append(
                WorkflowGate(
                    id=gate_id,
                    project_id=project_id,
                    workflow_id=workflow_id,
                    phase_id=phase_id,
                    position=g_pos,
                    key=gate.key,
                    kind=gate.kind,
                    name=gate.name,
                    purpose=gate.purpose,
                    approver_roles=list(gate.approver_roles),
                    required_evidence=list(gate.required_evidence),
                    entry_criteria=list(gate.entry_criteria),
                    exit_criteria=list(gate.exit_criteria),
                    mandatory=gate.mandatory,
                    origin=gate.origin,
                )
            )
            link_rows("gate", gate_id, gate.sources)
    return WorkflowRows(tuple(phases), tuple(activities), tuple(gates), tuple(sources))


def plan_from_rows(
    workflow: Workflow,
    phases: list[WorkflowPhase],
    activities: list[WorkflowActivity],
    gates: list[WorkflowGate],
    sources: list[WorkflowSource],
) -> WorkflowPlan:
    """The workflow's *current* content, rebuilt from its rows."""
    generated = workflow.generated_structure
    links: dict[tuple[str, uuid.UUID], list[SourceLink]] = defaultdict(list)
    for row in sources:
        links[(row.element_type, row.element_id)].append(
            SourceLink(row.source_type, str(row.source_id), row.relation, row.source_hash)
        )
    # The generated order of each element's links is the canonical order.
    order = _generated_link_order(generated)

    def sorted_links(element_type: str, element_id: uuid.UUID, key: str) -> tuple[SourceLink, ...]:
        found = links.get((element_type, element_id), [])
        rank = order.get((element_type, key), {})
        return tuple(
            sorted(found, key=lambda s: rank.get((s.source_type, s.source_id, s.relation), 10**6))
        )

    by_phase_a: dict[uuid.UUID, list[WorkflowActivity]] = defaultdict(list)
    for activity in activities:
        by_phase_a[activity.phase_id].append(activity)
    by_phase_g: dict[uuid.UUID, list[WorkflowGate]] = defaultdict(list)
    for gate in gates:
        by_phase_g[gate.phase_id].append(gate)

    plans: list[PhasePlan] = []
    for phase in sorted(phases, key=lambda p: p.position):
        plans.append(
            PhasePlan(
                key=phase.key,
                name=phase.name,
                description=phase.description,
                stages=tuple(phase.stages),
                cycle=phase.cycle,
                verifies=phase.verifies_phase_key,
                responsible_roles=tuple(phase.responsible_roles),
                deliverables=tuple(phase.deliverables),
                entry_criteria=tuple(phase.entry_criteria),
                exit_criteria=tuple(phase.exit_criteria),
                testing_requirements=tuple(phase.testing_requirements),
                traceability_requirements=tuple(phase.traceability_requirements),
                origin=phase.origin,
                activities=tuple(
                    ActivityPlan(
                        key=a.key,
                        kind=a.kind,
                        name=a.name,
                        description=a.description,
                        responsible_roles=tuple(a.responsible_roles),
                        deliverables=tuple(a.deliverables),
                        mandatory=bool(a.mandatory),
                        origin=a.origin,
                        sources=sorted_links("activity", a.id, a.key),
                    )
                    for a in sorted(by_phase_a[phase.id], key=lambda a: a.position)
                ),
                gates=tuple(
                    GatePlan(
                        key=g.key,
                        kind=g.kind,
                        name=g.name,
                        purpose=g.purpose,
                        approver_roles=tuple(g.approver_roles),
                        required_evidence=tuple(g.required_evidence),
                        entry_criteria=tuple(g.entry_criteria),
                        exit_criteria=tuple(g.exit_criteria),
                        mandatory=bool(g.mandatory),
                        origin=g.origin,
                        sources=sorted_links("gate", g.id, g.key),
                    )
                    for g in sorted(by_phase_g[phase.id], key=lambda g: g.position)
                ),
            )
        )
    return WorkflowPlan(
        candidate_key=str(generated["candidate_key"]),
        candidate_label=str(generated["candidate_label"]),
        composition=tuple(str(x) for x in generated["composition"]),
        approach=str(generated["approach"]),
        template_ref=str(generated["template_ref"]),
        phases=tuple(plans),
        sources=sorted_links("workflow", workflow.id, ""),
        open_items=tuple(
            Finding(str(f["code"]), f["severity"], str(f["message"]), str(f.get("subject", "")))
            for f in workflow.open_items or []
        ),
    )


def _generated_link_order(
    generated: dict,  # type: ignore[type-arg]
) -> dict[tuple[str, str], dict[tuple[str, str, str], int]]:
    """Each element's source links in their generated order, keyed by element key."""
    order: dict[tuple[str, str], dict[tuple[str, str, str], int]] = {}

    def record(element_type: str, key: str, raw: list) -> None:  # type: ignore[type-arg]
        order[(element_type, key)] = {
            (str(s["source_type"]), str(s["source_id"]), str(s["relation"])): i
            for i, s in enumerate(raw)
        }

    record("workflow", "", generated.get("sources", []))
    for phase in generated.get("phases", []):
        for activity in phase.get("activities", []):
            record("activity", str(activity["key"]), activity.get("sources", []))
        for gate in phase.get("gates", []):
            record("gate", str(gate["key"]), gate.get("sources", []))
    return order
