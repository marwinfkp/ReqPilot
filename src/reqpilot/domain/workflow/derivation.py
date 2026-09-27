"""``generate_workflow``: the selected SDLC's template plus the project's own records.

Deterministic and model-free (architecture traceability matrix: ``FR-WFL`` ->
``generate_workflow``, M8; no role contract proposes a workflow). The derivation:

1. **Structure** from the template of the G6-selected candidate - never the
   runner-up and never a different model. A hybrid's template must be composed of
   exactly the SDLC models its P9 candidate names (``composition``), or generation
   refuses.
2. **Compliance checkpoints** (``FR-WFL-003``; N.2 #25): one gate per checklist
   control the project's eligible mappings name, placed by the obligation kind
   (a checklist ``approval_checkpoint`` goes before deployment), linked to every
   mapping of that control.
3. **Security activities** (``FR-WFL-002``): from the derived security/privacy
   requirements - threat modelling in design and static security testing in
   implementation whenever any exists, plus one activity per control family
   present, each linked to the requirements of its family, with the family's
   testing requirement added to the phase it belongs to (``FR-WFL-004``).
4. **HIGH-risk treatment** (``FR-WFL-002``; N.2 #26): every recorded mitigation of
   every HIGH risk becomes an implementation activity and a verification activity,
   both linked to the risk and the mitigation. A HIGH risk with no mitigation gets
   a treatment-definition activity and an open item; nothing is invented.
5. **Production readiness** (``[PS §16]`` category 8; architecture M.4): one gate in
   the release phase, whose required evidence names this project's checkpoints and
   risk activities. It is a gate of the target project's process, not a ReqPilot gate.
6. **Phase criteria that name the project's elements**: each phase hosting
   checkpoints or risk activities gets exit criteria and traceability requirements
   that name them (``FR-WFL-005``, ``-006``).

What the records leave unresolved is listed as **open items** on the workflow
(:class:`~reqpilot.domain.workflow.plan.Finding` with severity ``open_item``).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from reqpilot.domain.enums import WorkflowActivityKind, WorkflowGateKind
from reqpilot.domain.errors import WorkflowError
from reqpilot.domain.workflow.inputs import (
    MappingInput,
    MitigationInput,
    RiskInput,
    SecurityFindingInput,
    WorkflowInputs,
)
from reqpilot.domain.workflow.plan import (
    REL_CHECKPOINT_FOR,
    REL_DERIVED_FROM,
    REL_IMPLEMENTS,
    REL_REALISES,
    REL_TREATS,
    REL_VERIFIES,
    SOURCE_COMPLIANCE_MAPPING,
    SOURCE_RISK,
    SOURCE_RISK_MITIGATION,
    SOURCE_SDLC_CANDIDATE,
    SOURCE_SECURITY_FINDING,
    ActivityPlan,
    Finding,
    GatePlan,
    PhasePlan,
    SourceLink,
    WorkflowPlan,
)
from reqpilot.domain.workflow.templates import PhaseSpec, SdlcTemplate, WorkflowTemplates


@dataclass
class _PhaseBuild:
    spec: PhaseSpec
    activities: list[ActivityPlan] = field(default_factory=list)
    gates: list[GatePlan] = field(default_factory=list)
    exit_criteria: list[str] = field(default_factory=list)
    testing: list[str] = field(default_factory=list)
    traceability: list[str] = field(default_factory=list)

    def add_unique(self, target: list[str], text: str) -> None:
        if text not in target and text not in self._template_values(target):
            target.append(text)

    def _template_values(self, target: list[str]) -> tuple[str, ...]:
        if target is self.exit_criteria:
            return self.spec.exit_criteria
        if target is self.testing:
            return self.spec.testing
        return self.spec.traceability

    def build(self) -> PhasePlan:
        spec = self.spec
        return PhasePlan(
            key=spec.key,
            name=spec.name,
            description=spec.description,
            stages=spec.stages,
            cycle=spec.cycle,
            verifies=spec.verifies,
            responsible_roles=spec.roles,
            deliverables=spec.deliverables,
            entry_criteria=spec.entry_criteria,
            exit_criteria=(*spec.exit_criteria, *self.exit_criteria),
            testing_requirements=(*spec.testing, *self.testing),
            traceability_requirements=(*spec.traceability, *self.traceability),
            activities=tuple(self.activities),
            gates=tuple(self.gates),
        )


def _refuse(code: str, message: str) -> WorkflowError:
    return WorkflowError(message, (Finding(code, "error", message).as_dict(),))


def select_template(inputs: WorkflowInputs, templates: WorkflowTemplates) -> SdlcTemplate:
    """The selected candidate's template, or a refusal - never a substitute."""
    template = templates.template(inputs.candidate_key)
    if template is None:
        raise _refuse(
            "TEMPLATE_MISSING",
            f"no workflow template exists for the selected SDLC {inputs.candidate_key!r}; "
            "a different model is never substituted",
        )
    if set(template.composition) != set(inputs.candidate_models):
        raise _refuse(
            "COMPOSITION_MISMATCH",
            f"the {inputs.candidate_key} template is composed of {sorted(template.composition)} "
            f"but the selected candidate names {sorted(inputs.candidate_models)}",
        )
    return template


def derive_workflow(inputs: WorkflowInputs, templates: WorkflowTemplates) -> WorkflowPlan:
    template = select_template(inputs, templates)
    phases = {spec.key: _PhaseBuild(spec) for spec in template.phases}
    open_items: list[Finding] = []

    # 1. the template's own activities and phase approvals
    for build in phases.values():
        phase_spec = build.spec
        for activity in phase_spec.activities:
            build.activities.append(
                ActivityPlan(
                    key=f"{phase_spec.key}:{activity.key}",
                    kind=str(WorkflowActivityKind.TEMPLATE),
                    name=activity.name,
                    description=activity.description,
                    responsible_roles=activity.roles,
                    deliverables=activity.deliverables,
                )
            )
        for gate in phase_spec.gates:
            build.gates.append(
                GatePlan(
                    key=f"{phase_spec.key}:{gate.key}",
                    kind=str(WorkflowGateKind.PHASE_APPROVAL),
                    name=gate.name,
                    purpose=gate.purpose,
                    approver_roles=gate.approver_roles,
                    required_evidence=gate.required_evidence,
                    entry_criteria=gate.entry_criteria,
                    exit_criteria=gate.exit_criteria,
                )
            )

    def host(stage: str) -> _PhaseBuild:
        return phases[template.phase_for(stage)]

    # 2. security activities from the derived security/privacy requirements
    findings = sorted(inputs.findings, key=lambda f: (f.family, f.id))
    if findings:
        every = tuple(
            SourceLink(SOURCE_SECURITY_FINDING, f.id, REL_DERIVED_FROM, f.content_hash, f.family)
            for f in findings
        )
        for baseline_spec in templates.security_baseline:
            build = host(baseline_spec.stage)
            build.activities.append(
                ActivityPlan(
                    key=f"security:{baseline_spec.key}",
                    kind=str(WorkflowActivityKind.SECURITY),
                    name=baseline_spec.name,
                    description=baseline_spec.description,
                    responsible_roles=baseline_spec.roles,
                    deliverables=baseline_spec.deliverables,
                    mandatory=True,
                    sources=every,
                )
            )
        by_family: dict[str, list[SecurityFindingInput]] = defaultdict(list)
        for finding in findings:
            by_family[finding.family].append(finding)
        for family in sorted(by_family):
            spec_or_none = templates.security_families.get(family)
            if spec_or_none is None:  # pragma: no cover - the loader covers every family
                raise _refuse("SECURITY_FAMILY_UNPLACED", f"no security activity for {family}")
            family_spec = spec_or_none
            members = by_family[family]
            labels = ", ".join(sorted({f.requirement_label for f in members}))
            build = host(family_spec.stage)
            build.activities.append(
                ActivityPlan(
                    key=f"security:{family}",
                    kind=str(WorkflowActivityKind.SECURITY),
                    name=family_spec.name,
                    description=(
                        f"{family_spec.description} Derived from {len(members)} derived "
                        f"{family.replace('_', ' ')} requirement(s) of {labels}."
                    ),
                    responsible_roles=family_spec.roles,
                    deliverables=family_spec.deliverables,
                    mandatory=True,
                    sources=tuple(
                        SourceLink(
                            SOURCE_SECURITY_FINDING,
                            f.id,
                            REL_DERIVED_FROM,
                            f.content_hash,
                            f"{family} ({f.requirement_label})",
                        )
                        for f in members
                    ),
                )
            )
            if family_spec.testing_stage and family_spec.testing_requirement:
                testing_host = host(family_spec.testing_stage)
                testing_host.add_unique(testing_host.testing, family_spec.testing_requirement)
        host("design").add_unique(
            host("design").traceability,
            "Each security activity cites the derived security or privacy requirements it "
            "comes from",
        )

    # 3. compliance checkpoints from the project's own mappings
    by_control: dict[str, list[MappingInput]] = defaultdict(list)
    for mapping in inputs.mappings:
        by_control[mapping.control_key].append(mapping)
    checkpoint_phases: dict[str, list[str]] = defaultdict(list)
    for control_key in sorted(by_control):
        mappings = sorted(by_control[control_key], key=lambda m: m.id)
        kind = mappings[0].obligation_kind
        checkpoint_spec = templates.checkpoints.get(kind)
        if checkpoint_spec is None:
            raise _refuse(
                "CHECKPOINT_KIND_UNPLACED",
                f"no checkpoint placement for obligation kind {kind!r} ({control_key})",
            )
        title = mappings[0].control_title
        requirements = ", ".join(sorted({m.requirement_label for m in mappings}))
        relationships = ", ".join(sorted({m.relationship.replace("_", " ") for m in mappings}))
        build = host(checkpoint_spec.stage)
        build.gates.append(
            GatePlan(
                key=f"checkpoint:{control_key}",
                kind=str(WorkflowGateKind.COMPLIANCE_CHECKPOINT),
                name=checkpoint_spec.name.format(control_title=title, control_key=control_key),
                purpose=(
                    f"{checkpoint_spec.purpose} Derived from this project's candidate "
                    f"mapping(s) of checklist control {control_key} ({relationships}) for "
                    f"{requirements}. "
                    "A candidate mapping records a potentially applicable control; it is not "
                    "a legal determination."
                ),
                approver_roles=checkpoint_spec.approver_roles,
                required_evidence=(
                    *checkpoint_spec.required_evidence,
                    f"The evidence cited by the mapping(s) of {control_key}",
                ),
                entry_criteria=checkpoint_spec.entry_criteria,
                exit_criteria=checkpoint_spec.exit_criteria,
                mandatory=True,
                sources=tuple(
                    SourceLink(
                        SOURCE_COMPLIANCE_MAPPING,
                        m.id,
                        REL_CHECKPOINT_FOR,
                        m.content_hash,
                        f"{control_key} ({m.requirement_label})",
                    )
                    for m in mappings
                ),
            )
        )
        checkpoint_phases[build.spec.key].append(control_key)
        for m in mappings:
            if m.evidence_count < 1:
                open_items.append(
                    Finding(
                        "MAPPING_WITHOUT_EVIDENCE",
                        "open_item",
                        f"The mapping of {control_key} for {m.requirement_label} cites no "
                        "evidence; its checkpoint cannot be decided on the record alone.",
                        m.id,
                    )
                )
    for phase_key, controls in checkpoint_phases.items():
        build = phases[phase_key]
        build.add_unique(
            build.exit_criteria,
            "A recorded decision on every compliance checkpoint of this phase: "
            + ", ".join(controls),
        )
        build.add_unique(
            build.traceability,
            "Each compliance checkpoint cites the compliance mapping(s) and evidence it was "
            "derived from",
        )

    # 4. HIGH-risk treatment from the risk register
    treatment_phases: dict[str, int] = defaultdict(int)
    for risk in sorted(inputs.high_risks(), key=lambda r: (r.title, r.id)):
        _treat(risk, templates, host, treatment_phases, open_items)
    for phase_key, count in treatment_phases.items():
        build = phases[phase_key]
        build.add_unique(
            build.exit_criteria,
            f"Every HIGH-risk treatment or verification activity of this phase complete "
            f"({count} in this workflow's plan)",
        )
        build.add_unique(
            build.traceability,
            "Each risk activity cites the HIGH risk and the recorded mitigation it implements "
            "or verifies",
        )
    if treatment_phases:
        verify_host = host(templates.treatment_verify.stage)
        verify_host.add_unique(
            verify_host.testing,
            "Mitigation verification tests for every HIGH risk treated in this workflow",
        )

    # 5. compliance gaps of the latest compliance run: open items, never checkpoints
    for gap in sorted(inputs.gaps, key=lambda g: (g.control_key, g.id)):
        open_items.append(
            Finding(
                "COMPLIANCE_GAP_OPEN",
                "open_item",
                f"Checklist control {gap.control_key} ({gap.control_title}) has no covering "
                "requirement in the approved set; the workflow cannot contain a checkpoint for "
                "a mapping that does not exist.",
                gap.id,
            )
        )

    # 6. production readiness - the generated-workflow gate of [PS §16] category 8
    ready = templates.production_readiness
    checkpoints = sum(len(v) for v in checkpoint_phases.values())
    risk_activities = sum(treatment_phases.values())
    evidence = list(ready.required_evidence)
    if checkpoints:
        evidence.append(
            f"A recorded decision on each of the {checkpoints} compliance checkpoint(s)"
        )
    if risk_activities:
        evidence.append(
            f"Completion records for each of the {risk_activities} HIGH-risk treatment and "
            "verification activities"
        )
    evidence.append("Resolution, or recorded acceptance, of every open item of this workflow")
    release = host(ready.stage)
    release.gates.append(
        GatePlan(
            key=ready.key,
            kind=str(WorkflowGateKind.PRODUCTION_READINESS),
            name=ready.name,
            purpose=ready.purpose,
            approver_roles=ready.approver_roles,
            required_evidence=tuple(evidence),
            entry_criteria=ready.entry_criteria,
            exit_criteria=ready.exit_criteria,
            mandatory=True,
        )
    )
    release.add_unique(
        release.exit_criteria, "The production-readiness decision recorded by every approver"
    )

    return WorkflowPlan(
        candidate_key=inputs.candidate_key,
        candidate_label=template.label,
        composition=template.composition,
        approach=template.approach,
        template_ref=templates.ref,
        phases=tuple(phases[spec.key].build() for spec in template.phases),
        sources=(
            SourceLink(
                SOURCE_SDLC_CANDIDATE,
                inputs.candidate_id,
                REL_REALISES,
                None,
                inputs.candidate_label,
            ),
        ),
        open_items=tuple(open_items),
    )


def _treat(
    risk: RiskInput,
    templates: WorkflowTemplates,
    host: Callable[[str], _PhaseBuild],
    treatment_phases: dict[str, int],
    open_items: list[Finding],
) -> None:
    live = sorted(risk.live_mitigations(), key=lambda m: m.id)
    risk_link = SourceLink(SOURCE_RISK, risk.id, REL_TREATS, risk.content_hash, risk.title)
    if not live:
        spec = templates.treatment_undefined
        build = host(spec.stage)
        build.activities.append(
            ActivityPlan(
                key=f"risk:{risk.id}:treatment",
                kind=str(WorkflowActivityKind.RISK_TREATMENT),
                name=spec.name.format(risk_title=_short(risk.title)),
                description=f"{spec.description} Risk: {risk.title} ({risk.subject_label}).",
                responsible_roles=spec.roles,
                deliverables=spec.deliverables,
                mandatory=True,
                sources=(risk_link,),
            )
        )
        treatment_phases[build.spec.key] += 1
        open_items.append(
            Finding(
                "HIGH_RISK_WITHOUT_MITIGATION",
                "open_item",
                f"HIGH risk {risk.title!r} ({risk.subject_label}) has no recorded mitigation.",
                risk.id,
            )
        )
        return
    for mitigation in live:
        provenance = _mitigation_label(mitigation)
        for spec, kind, relation in (
            (templates.treatment_implement, WorkflowActivityKind.RISK_TREATMENT, REL_IMPLEMENTS),
            (templates.treatment_verify, WorkflowActivityKind.RISK_VERIFICATION, REL_VERIFIES),
        ):
            build = host(spec.stage)
            suffix = "implement" if relation == REL_IMPLEMENTS else "verify"
            build.activities.append(
                ActivityPlan(
                    key=f"risk:{risk.id}:mitigation:{mitigation.id}:{suffix}",
                    kind=str(kind),
                    name=spec.name.format(risk_title=_short(risk.title)),
                    description=(
                        f"Recorded mitigation ({provenance}): {mitigation.suggestion} "
                        f"Risk: {risk.title} ({risk.subject_label}; {risk.category})."
                    ),
                    responsible_roles=spec.roles,
                    deliverables=spec.deliverables,
                    mandatory=True,
                    sources=(
                        risk_link,
                        SourceLink(
                            SOURCE_RISK_MITIGATION,
                            mitigation.id,
                            relation,
                            None,
                            _short(mitigation.suggestion),
                        ),
                    ),
                )
            )
            treatment_phases[build.spec.key] += 1
        if mitigation.awaits_validation:
            open_items.append(
                Finding(
                    "MITIGATION_AWAITS_VALIDATION",
                    "open_item",
                    f"The mitigation of HIGH risk {risk.title!r} is an AI suggestion that no "
                    "human has accepted yet (FR-RSK-005).",
                    mitigation.id,
                )
            )


def _mitigation_label(mitigation: MitigationInput) -> str:
    if not mitigation.is_ai_generated:
        return "human-authored"
    if mitigation.status == "accepted":
        return "AI-suggested, accepted by a human"
    return "AI-suggested - requires human validation"


def _short(text: str, limit: int = 120) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


__all__ = ["derive_workflow", "select_template"]
