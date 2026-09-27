"""Deterministic validation of a workflow - fails closed (roadmap phase P10).

Run on every generated workflow before anything is stored, and again on the result
of every edit before the edit is stored. Any ``error`` refuses: nothing is persisted
and the caller receives the findings. Nothing here repairs, drops or reorders an
element to make a workflow pass.

What is checked:

* **Structure** (``FR-WFL-001``, ``-004``..``-006``): phases present and uniquely
  keyed; every phase has a name, stages, responsible roles, deliverables, entry and
  exit criteria, testing and traceability requirements and at least one activity;
  every activity has a phase, a name, roles and a deliverable; every gate has a
  purpose, approver roles, required evidence, entry and exit criteria; element keys
  are unique; roles are in the target-project vocabulary; a V-Model ``verifies``
  pairing names an earlier phase.
* **Production readiness** (``FR-WFL-003``; architecture M.4): exactly one, in a
  phase that releases.
* **Coverage - the P10 exit criterion**: every mandatory source is represented -
  each eligible compliance mapping by a compliance checkpoint, each HIGH risk by a
  risk activity, each of its recorded mitigations by an implementation *and* a
  verification activity, each derived security requirement by a security activity.
  ``required`` is computed from the records independently of the derivation
  (:func:`mandatory_sources`), so a derivation defect cannot hide a missing element.
* **Provenance**: every source link resolves to a record that was eligible for this
  workflow (``known``), the workflow realises the selected candidate, and an element
  derived from a record is mandatory.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from reqpilot.domain.enums import WorkflowActivityKind, WorkflowGateKind
from reqpilot.domain.workflow.inputs import WorkflowInputs
from reqpilot.domain.workflow.plan import (
    REL_CHECKPOINT_FOR,
    REL_IMPLEMENTS,
    REL_REALISES,
    REL_VERIFIES,
    SOURCE_COMPLIANCE_MAPPING,
    SOURCE_RISK,
    SOURCE_RISK_MITIGATION,
    SOURCE_SDLC_CANDIDATE,
    SOURCE_SECURITY_FINDING,
    SOURCE_TYPES,
    Finding,
    SourceLink,
    WorkflowPlan,
)

SourceSets = Mapping[str, frozenset[str]]

ACTIVITY_KINDS = frozenset(str(k) for k in WorkflowActivityKind)
GATE_KINDS = frozenset(str(k) for k in WorkflowGateKind)
_RISK_KINDS = {
    str(WorkflowActivityKind.RISK_TREATMENT),
    str(WorkflowActivityKind.RISK_VERIFICATION),
}


def mandatory_sources(inputs: WorkflowInputs) -> dict[str, frozenset[str]]:
    """What the project's own records make mandatory, computed from the records alone."""
    high = inputs.high_risks()
    return {
        SOURCE_COMPLIANCE_MAPPING: frozenset(m.id for m in inputs.mappings),
        SOURCE_RISK: frozenset(r.id for r in high),
        SOURCE_RISK_MITIGATION: frozenset(m.id for r in high for m in r.live_mitigations()),
        SOURCE_SECURITY_FINDING: frozenset(f.id for f in inputs.findings),
    }


def known_sources(inputs: WorkflowInputs) -> dict[str, frozenset[str]]:
    """Every record a source link may legitimately name for this workflow."""
    return {
        SOURCE_SDLC_CANDIDATE: frozenset({inputs.candidate_id}),
        SOURCE_COMPLIANCE_MAPPING: frozenset(m.id for m in inputs.mappings),
        SOURCE_RISK: frozenset(r.id for r in inputs.risks),
        SOURCE_RISK_MITIGATION: frozenset(m.id for r in inputs.risks for m in r.mitigations),
        SOURCE_SECURITY_FINDING: frozenset(f.id for f in inputs.findings),
    }


def sources_of(plan: WorkflowPlan) -> dict[str, frozenset[str]]:
    """The sources a stored plan names (the generated snapshot, for edit validation)."""
    found: dict[str, set[str]] = {t: set() for t in SOURCE_TYPES}
    for link in _all_links(plan):
        found.setdefault(link.source_type, set()).add(link.source_id)
    return {k: frozenset(v) for k, v in found.items()}


def _all_links(plan: WorkflowPlan) -> Iterable[SourceLink]:
    yield from plan.sources
    for _phase, activity in plan.activities():
        yield from activity.sources
    for _phase, gate in plan.gates():
        yield from gate.sources


def _err(code: str, message: str, subject: str = "") -> Finding:
    return Finding(code, "error", message, subject)


def validate_workflow(
    plan: WorkflowPlan,
    *,
    candidate_key: str,
    candidate_id: str,
    roles: Iterable[str],
    required: SourceSets,
    known: SourceSets,
) -> list[Finding]:
    """Return every error; an empty list means the workflow may be stored."""
    errors: list[Finding] = []
    vocabulary = frozenset(roles)

    if plan.candidate_key != candidate_key:
        errors.append(
            _err(
                "CANDIDATE_MISMATCH",
                f"the workflow realises {plan.candidate_key!r} but the G6 selection is "
                f"{candidate_key!r}",
            )
        )
    if not any(
        s.source_type == SOURCE_SDLC_CANDIDATE
        and s.source_id == candidate_id
        and s.relation == REL_REALISES
        for s in plan.sources
    ):
        errors.append(
            _err("CANDIDATE_LINK_MISSING", "the workflow does not name the candidate it realises")
        )
    if not plan.phases:
        return [*errors, _err("NO_PHASES", "the workflow has no phases")]

    def bad_roles(where: str, values: tuple[str, ...], subject: str) -> None:
        unknown = [r for r in values if r not in vocabulary]
        if unknown:
            errors.append(_err("UNKNOWN_ROLE", f"{where} names unknown role(s) {unknown}", subject))

    def required_text(where: str, value: str, code: str, subject: str) -> None:
        if not value.strip():
            errors.append(_err(code, f"{where} is empty", subject))

    phase_keys: list[str] = []
    element_keys: list[str] = []
    for phase in plan.phases:
        where = f"phase {phase.key!r}"
        required_text(f"{where} name", phase.name, "PHASE_NAME_MISSING", phase.key)
        for code, label, values in (
            ("PHASE_STAGES_MISSING", "stages", phase.stages),
            ("PHASE_ROLES_MISSING", "responsible roles", phase.responsible_roles),
            ("PHASE_DELIVERABLES_MISSING", "deliverables", phase.deliverables),
            ("PHASE_ENTRY_CRITERIA_MISSING", "entry criteria", phase.entry_criteria),
            ("PHASE_EXIT_CRITERIA_MISSING", "exit criteria", phase.exit_criteria),
            ("PHASE_TESTING_MISSING", "testing requirements", phase.testing_requirements),
            (
                "PHASE_TRACEABILITY_MISSING",
                "traceability requirements",
                phase.traceability_requirements,
            ),
        ):
            if not any(v.strip() for v in values):
                errors.append(_err(code, f"{where} has no {label}", phase.key))
        if not phase.activities:
            errors.append(_err("PHASE_ACTIVITIES_MISSING", f"{where} has no activities", phase.key))
        bad_roles(where, phase.responsible_roles, phase.key)
        if phase.verifies is not None and phase.verifies not in phase_keys:
            errors.append(
                _err(
                    "VERIFIES_UNKNOWN_PHASE",
                    f"{where} verifies {phase.verifies!r}, which is not an earlier phase",
                    phase.key,
                )
            )
        phase_keys.append(phase.key)
        for activity in phase.activities:
            element_keys.append(activity.key)
            awhere = f"activity {activity.key!r}"
            required_text(f"{awhere} name", activity.name, "ACTIVITY_NAME_MISSING", activity.key)
            if activity.kind not in ACTIVITY_KINDS:
                errors.append(_err("ACTIVITY_KIND_UNKNOWN", f"{awhere} kind", activity.key))
            if not activity.responsible_roles:
                errors.append(_err("ACTIVITY_ROLES_MISSING", f"{awhere} has no role", activity.key))
            bad_roles(awhere, activity.responsible_roles, activity.key)
            if not any(d.strip() for d in activity.deliverables):
                errors.append(
                    _err(
                        "ACTIVITY_DELIVERABLES_MISSING",
                        f"{awhere} has no deliverable",
                        activity.key,
                    )
                )
            if activity.sources and not activity.mandatory:
                errors.append(
                    _err(
                        "SOURCED_ELEMENT_NOT_MANDATORY",
                        f"{awhere} is derived from a record but is not mandatory",
                        activity.key,
                    )
                )
        for gate in phase.gates:
            element_keys.append(gate.key)
            gwhere = f"gate {gate.key!r}"
            required_text(f"{gwhere} name", gate.name, "GATE_NAME_MISSING", gate.key)
            required_text(f"{gwhere} purpose", gate.purpose, "GATE_PURPOSE_MISSING", gate.key)
            if gate.kind not in GATE_KINDS:
                errors.append(_err("GATE_KIND_UNKNOWN", f"{gwhere} kind", gate.key))
            for code, label, values in (
                ("GATE_APPROVERS_MISSING", "approver roles", gate.approver_roles),
                ("GATE_EVIDENCE_MISSING", "required evidence", gate.required_evidence),
                ("GATE_ENTRY_CRITERIA_MISSING", "entry criteria", gate.entry_criteria),
                ("GATE_EXIT_CRITERIA_MISSING", "exit criteria", gate.exit_criteria),
            ):
                if not any(v.strip() for v in values):
                    errors.append(_err(code, f"{gwhere} has no {label}", gate.key))
            bad_roles(gwhere, gate.approver_roles, gate.key)
            if gate.sources and not gate.mandatory:
                errors.append(
                    _err(
                        "SOURCED_ELEMENT_NOT_MANDATORY",
                        f"{gwhere} is derived from a record but is not mandatory",
                        gate.key,
                    )
                )

    if len(phase_keys) != len(set(phase_keys)):
        errors.append(_err("DUPLICATE_PHASE_KEY", "two phases share a key"))
    duplicates = sorted({k for k in element_keys if element_keys.count(k) > 1})
    if duplicates:
        errors.append(_err("DUPLICATE_ELEMENT_KEY", f"element keys repeat: {duplicates}"))

    readiness = [
        (p, g) for p, g in plan.gates() if g.kind == str(WorkflowGateKind.PRODUCTION_READINESS)
    ]
    if len(readiness) != 1:
        errors.append(
            _err(
                "PRODUCTION_READINESS_COUNT",
                f"exactly one production-readiness gate is required; found {len(readiness)}",
            )
        )
    elif "release" not in readiness[0][0].stages:
        errors.append(
            _err(
                "PRODUCTION_READINESS_MISPLACED",
                f"the production-readiness gate sits in {readiness[0][0].key!r}, which does not "
                "release",
                readiness[0][1].key,
            )
        )

    # provenance resolves to eligible records
    for link in _all_links(plan):
        if link.source_id not in known.get(link.source_type, frozenset()):
            errors.append(
                _err(
                    "SOURCE_UNRESOLVED",
                    f"{link.source_type} {link.source_id} is not an eligible record of this "
                    "workflow's project and run",
                    link.source_id,
                )
            )

    errors.extend(_coverage(plan, required))
    return errors


def _coverage(plan: WorkflowPlan, required: SourceSets) -> list[Finding]:
    """The P10 exit criterion as a check: every mandatory record is represented."""
    covered_mappings = {
        s.source_id
        for _p, g in plan.gates()
        if g.kind == str(WorkflowGateKind.COMPLIANCE_CHECKPOINT)
        for s in g.sources
        if s.source_type == SOURCE_COMPLIANCE_MAPPING and s.relation == REL_CHECKPOINT_FOR
    }
    covered_risks: set[str] = set()
    implemented: set[str] = set()
    verified: set[str] = set()
    covered_findings: set[str] = set()
    for _p, activity in plan.activities():
        for s in activity.sources:
            if activity.kind in _RISK_KINDS and s.source_type == SOURCE_RISK:
                covered_risks.add(s.source_id)
            if (
                activity.kind == str(WorkflowActivityKind.RISK_TREATMENT)
                and s.source_type == SOURCE_RISK_MITIGATION
                and s.relation == REL_IMPLEMENTS
            ):
                implemented.add(s.source_id)
            if (
                activity.kind == str(WorkflowActivityKind.RISK_VERIFICATION)
                and s.source_type == SOURCE_RISK_MITIGATION
                and s.relation == REL_VERIFIES
            ):
                verified.add(s.source_id)
            if (
                activity.kind == str(WorkflowActivityKind.SECURITY)
                and s.source_type == SOURCE_SECURITY_FINDING
            ):
                covered_findings.add(s.source_id)
    errors: list[Finding] = []
    for source_id in sorted(
        required.get(SOURCE_COMPLIANCE_MAPPING, frozenset()) - covered_mappings
    ):
        errors.append(
            _err(
                "CHECKPOINT_MISSING",
                f"compliance mapping {source_id} has no compliance checkpoint",
                source_id,
            )
        )
    for source_id in sorted(required.get(SOURCE_RISK, frozenset()) - covered_risks):
        errors.append(
            _err("RISK_ACTIVITY_MISSING", f"HIGH risk {source_id} has no risk activity", source_id)
        )
    mitigations = required.get(SOURCE_RISK_MITIGATION, frozenset())
    for source_id in sorted(mitigations - implemented):
        errors.append(
            _err(
                "MITIGATION_NOT_IMPLEMENTED",
                f"mitigation {source_id} of a HIGH risk has no implementation activity",
                source_id,
            )
        )
    for source_id in sorted(mitigations - verified):
        errors.append(
            _err(
                "MITIGATION_NOT_VERIFIED",
                f"mitigation {source_id} of a HIGH risk has no verification activity",
                source_id,
            )
        )
    for source_id in sorted(required.get(SOURCE_SECURITY_FINDING, frozenset()) - covered_findings):
        errors.append(
            _err(
                "SECURITY_ACTIVITY_MISSING",
                f"derived security requirement {source_id} has no security activity",
                source_id,
            )
        )
    return errors
