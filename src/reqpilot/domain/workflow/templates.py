"""The workflow templates as validated domain configuration (M7; DQ-03).

``rules/workflow.py`` parses ``workflow_templates.yaml`` into these types. The
types check their own invariants, so a malformed template is a startup error, never
a silently thinner workflow:

* every phase names at least one stage, role, deliverable, entry and exit
  criterion, testing and traceability requirement, and activity (``FR-WFL-001``,
  ``-004``, ``-005``, ``-006``);
* every role is in the target-project role vocabulary;
* every template hosts each placement stage in a phase whose stages include it,
  so a derived checkpoint or activity always has somewhere to go;
* a V-Model ``verifies`` pairing names an earlier phase.

Tying a template to P9's candidate definitions (the keys and the hybrid
composition) is checked by the loader, which can see both rulesets.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from reqpilot.domain.errors import RuleConfigurationError


@dataclass(frozen=True)
class ActivitySpec:
    key: str
    name: str
    description: str
    roles: tuple[str, ...]
    deliverables: tuple[str, ...]


@dataclass(frozen=True)
class GateSpec:
    key: str
    name: str
    purpose: str
    approver_roles: tuple[str, ...]
    required_evidence: tuple[str, ...]
    entry_criteria: tuple[str, ...]
    exit_criteria: tuple[str, ...]


@dataclass(frozen=True)
class PhaseSpec:
    key: str
    name: str
    description: str
    stages: tuple[str, ...]
    roles: tuple[str, ...]
    deliverables: tuple[str, ...]
    entry_criteria: tuple[str, ...]
    exit_criteria: tuple[str, ...]
    testing: tuple[str, ...]
    traceability: tuple[str, ...]
    activities: tuple[ActivitySpec, ...]
    gates: tuple[GateSpec, ...] = ()
    cycle: str | None = None
    verifies: str | None = None


@dataclass(frozen=True)
class SdlcTemplate:
    key: str
    label: str
    composition: tuple[str, ...]
    approach: str
    placement: Mapping[str, str]
    phases: tuple[PhaseSpec, ...]

    def phase_for(self, stage: str) -> str:
        return self.placement[stage]


@dataclass(frozen=True)
class DerivedActivitySpec:
    """A project-derived activity: a security family's, or a baseline security one."""

    key: str
    stage: str
    name: str
    description: str
    roles: tuple[str, ...]
    deliverables: tuple[str, ...]
    testing_stage: str | None = None
    testing_requirement: str | None = None


@dataclass(frozen=True)
class CheckpointSpec:
    stage: str
    name: str
    purpose: str
    approver_roles: tuple[str, ...]
    required_evidence: tuple[str, ...]
    entry_criteria: tuple[str, ...]
    exit_criteria: tuple[str, ...]


@dataclass(frozen=True)
class TreatmentSpec:
    stage: str
    name: str
    roles: tuple[str, ...]
    deliverables: tuple[str, ...]
    description: str = ""


@dataclass(frozen=True)
class ProductionReadinessSpec:
    stage: str
    key: str
    name: str
    purpose: str
    approver_roles: tuple[str, ...]
    required_evidence: tuple[str, ...]
    entry_criteria: tuple[str, ...]
    exit_criteria: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowTemplates:
    version: str
    #: ``name@version#sha12`` - pins the file's content on every workflow.
    ref: str
    sha256: str
    roles: Mapping[str, str]
    stages: tuple[str, ...]
    placement_stages: tuple[str, ...]
    security_baseline: tuple[DerivedActivitySpec, ...]
    security_families: Mapping[str, DerivedActivitySpec]
    checkpoints: Mapping[str, CheckpointSpec]
    treatment_implement: TreatmentSpec
    treatment_verify: TreatmentSpec
    treatment_undefined: TreatmentSpec
    production_readiness: ProductionReadinessSpec
    templates: Mapping[str, SdlcTemplate]

    def __post_init__(self) -> None:
        self._validate()

    # ------------------------------------------------------------------
    def template(self, candidate_key: str) -> SdlcTemplate | None:
        return self.templates.get(candidate_key)

    def role_label(self, role: str) -> str:
        return self.roles.get(role, role)

    def _validate(self) -> None:
        def roles_ok(where: str, roles: tuple[str, ...]) -> None:
            if not roles:
                raise RuleConfigurationError(f"{where} names no role")
            unknown = [r for r in roles if r not in self.roles]
            if unknown:
                raise RuleConfigurationError(f"{where} names unknown role(s) {unknown}")

        def stage_ok(where: str, stage: str) -> None:
            if stage not in self.placement_stages:
                raise RuleConfigurationError(f"{where} names stage {stage!r} with no placement")

        for spec in (*self.security_baseline, *self.security_families.values()):
            roles_ok(f"security activity {spec.key}", spec.roles)
            stage_ok(f"security activity {spec.key}", spec.stage)
            if spec.testing_stage is not None:
                stage_ok(f"security testing {spec.key}", spec.testing_stage)
        for kind, checkpoint in self.checkpoints.items():
            roles_ok(f"checkpoint {kind}", checkpoint.approver_roles)
            stage_ok(f"checkpoint {kind}", checkpoint.stage)
            if not (
                checkpoint.required_evidence
                and checkpoint.entry_criteria
                and checkpoint.exit_criteria
            ):
                raise RuleConfigurationError(f"checkpoint {kind} lacks evidence or criteria")
        for treatment in (
            self.treatment_implement,
            self.treatment_verify,
            self.treatment_undefined,
        ):
            roles_ok(f"risk treatment {treatment.name!r}", treatment.roles)
            stage_ok(f"risk treatment {treatment.name!r}", treatment.stage)
        ready = self.production_readiness
        roles_ok("production readiness", ready.approver_roles)
        stage_ok("production readiness", ready.stage)
        if not (ready.required_evidence and ready.entry_criteria and ready.exit_criteria):
            raise RuleConfigurationError("production readiness lacks evidence or criteria")

        for key, template in self.templates.items():
            if not template.phases:
                raise RuleConfigurationError(f"template {key} has no phases")
            phase_keys = [p.key for p in template.phases]
            if len(phase_keys) != len(set(phase_keys)):
                raise RuleConfigurationError(f"template {key} repeats a phase key")
            by_key = {p.key: p for p in template.phases}
            for stage in self.placement_stages:
                host = template.placement.get(stage)
                if host is None or host not in by_key:
                    raise RuleConfigurationError(f"template {key} does not place stage {stage}")
                if stage not in by_key[host].stages:
                    raise RuleConfigurationError(
                        f"template {key} places {stage} in {host}, whose stages "
                        f"{list(by_key[host].stages)} do not include it"
                    )
            seen: set[str] = set()
            for phase in template.phases:
                where = f"template {key} phase {phase.key}"
                for stage in phase.stages:
                    if stage not in self.stages:
                        raise RuleConfigurationError(f"{where} names unknown stage {stage!r}")
                roles_ok(where, phase.roles)
                for label, values in (
                    ("stage", phase.stages),
                    ("deliverable", phase.deliverables),
                    ("entry criterion", phase.entry_criteria),
                    ("exit criterion", phase.exit_criteria),
                    ("testing requirement", phase.testing),
                    ("traceability requirement", phase.traceability),
                    ("activity", phase.activities),
                ):
                    if not values:
                        raise RuleConfigurationError(f"{where} has no {label}")
                if phase.verifies is not None and phase.verifies not in seen:
                    raise RuleConfigurationError(
                        f"{where} verifies {phase.verifies!r}, which is not an earlier phase"
                    )
                activity_keys = [a.key for a in phase.activities]
                if len(activity_keys) != len(set(activity_keys)):
                    raise RuleConfigurationError(f"{where} repeats an activity key")
                for activity in phase.activities:
                    roles_ok(f"{where} activity {activity.key}", activity.roles)
                    if not activity.deliverables:
                        raise RuleConfigurationError(
                            f"{where} activity {activity.key} has no deliverable"
                        )
                for gate in phase.gates:
                    roles_ok(f"{where} gate {gate.key}", gate.approver_roles)
                    if not (gate.required_evidence and gate.entry_criteria and gate.exit_criteria):
                        raise RuleConfigurationError(
                            f"{where} gate {gate.key} lacks evidence or criteria"
                        )
                seen.add(phase.key)
