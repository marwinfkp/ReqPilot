"""The P10 workflow templates as a typed, validated object (M7; DQ-03).

``workflow_templates.yaml`` -> :class:`~reqpilot.domain.workflow.templates.WorkflowTemplates`.
The domain type checks each template's own invariants; this loader parses the file,
pins its content hash in the ``ref`` every workflow records, and checks what only a
view of *both* rulesets can check:

* every P9 SDLC candidate has a template, keyed by the candidate's key - a selected
  candidate can never fall back to another model's template;
* each template's ``composition`` is exactly the SDLC models the P9 candidate's
  attributes name (``agile_v_model_hybrid`` -> agile + V-Model), so a hybrid keeps
  its actual composition;
* every P6 security control family and every checklist obligation kind has a
  placement, so no derived requirement or mapping is left without an element.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from reqpilot.domain.enums import ObligationKind, SecurityControlFamily
from reqpilot.domain.errors import RuleConfigurationError
from reqpilot.domain.integrity import file_canonical_sha256
from reqpilot.domain.workflow.templates import (
    ActivitySpec,
    CheckpointSpec,
    DerivedActivitySpec,
    GateSpec,
    PhaseSpec,
    ProductionReadinessSpec,
    SdlcTemplate,
    TreatmentSpec,
    WorkflowTemplates,
)
from reqpilot.rules.loader import RuleSet, load_ruleset
from reqpilot.rules.sdlc import SdlcRules, packaged_sdlc_rules

WORKFLOW_FILE = "workflow_templates.yaml"
PACKAGED_RULES_DIR = Path(__file__).resolve().parent / "data"

#: The SDLC models a P9 candidate's attributes can name (the rest - ``hybrid``,
#: ``iterative``, ``plan_driven`` ... - describe it, they are not models).
SDLC_MODELS: frozenset[str] = frozenset({"waterfall", "v_model", "spiral", "agile", "devsecops"})


def candidate_models(attributes: frozenset[str] | set[str]) -> tuple[str, ...]:
    return tuple(sorted(set(attributes) & SDLC_MODELS))


def _strs(value: Any, where: str, *, required: bool = True) -> tuple[str, ...]:
    if value is None:
        if required:
            raise RuleConfigurationError(f"{where} is missing")
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise RuleConfigurationError(f"{where} must be a list")
    return tuple(" ".join(str(v).split()) for v in value)


def _text(value: Any, where: str) -> str:
    if value is None or not str(value).strip():
        raise RuleConfigurationError(f"{where} is missing")
    return " ".join(str(value).split())


def _derived(key: str, raw: Mapping[str, Any]) -> DerivedActivitySpec:
    testing = raw.get("testing")
    return DerivedActivitySpec(
        key=key,
        stage=_text(raw.get("stage"), f"{key}.stage"),
        name=_text(raw.get("name"), f"{key}.name"),
        description=_text(raw.get("description"), f"{key}.description"),
        roles=_strs(raw.get("roles"), f"{key}.roles"),
        deliverables=_strs(raw.get("deliverables"), f"{key}.deliverables"),
        testing_stage=_text(testing["stage"], f"{key}.testing.stage") if testing else None,
        testing_requirement=(
            _text(testing["requirement"], f"{key}.testing.requirement") if testing else None
        ),
    )


def _treatment(raw: Mapping[str, Any], where: str) -> TreatmentSpec:
    return TreatmentSpec(
        stage=_text(raw.get("stage"), f"{where}.stage"),
        name=_text(raw.get("name"), f"{where}.name"),
        roles=_strs(raw.get("roles"), f"{where}.roles"),
        deliverables=_strs(raw.get("deliverables"), f"{where}.deliverables"),
        description=" ".join(str(raw.get("description", "")).split()),
    )


def _template(key: str, raw: Mapping[str, Any]) -> SdlcTemplate:
    phases = []
    for p in raw.get("phases") or ():
        where = f"templates.{key}.{p.get('key')}"
        phases.append(
            PhaseSpec(
                key=_text(p.get("key"), f"{where}.key"),
                name=_text(p.get("name"), f"{where}.name"),
                description=_text(p.get("description"), f"{where}.description"),
                stages=_strs(p.get("stages"), f"{where}.stages"),
                roles=_strs(p.get("roles"), f"{where}.roles"),
                deliverables=_strs(p.get("deliverables"), f"{where}.deliverables"),
                entry_criteria=_strs(p.get("entry_criteria"), f"{where}.entry_criteria"),
                exit_criteria=_strs(p.get("exit_criteria"), f"{where}.exit_criteria"),
                testing=_strs(p.get("testing"), f"{where}.testing"),
                traceability=_strs(p.get("traceability"), f"{where}.traceability"),
                cycle=" ".join(str(p["cycle"]).split()) if p.get("cycle") else None,
                verifies=str(p["verifies"]) if p.get("verifies") else None,
                activities=tuple(
                    ActivitySpec(
                        key=_text(a.get("key"), f"{where}.activities.key"),
                        name=_text(a.get("name"), f"{where}.activities.name"),
                        description=" ".join(str(a.get("description", a.get("name"))).split()),
                        roles=_strs(a.get("roles"), f"{where}.activities.roles"),
                        deliverables=_strs(
                            a.get("deliverables"), f"{where}.activities.deliverables"
                        ),
                    )
                    for a in p.get("activities") or ()
                ),
                gates=tuple(
                    GateSpec(
                        key=_text(g.get("key"), f"{where}.gates.key"),
                        name=_text(g.get("name"), f"{where}.gates.name"),
                        purpose=_text(g.get("purpose"), f"{where}.gates.purpose"),
                        approver_roles=_strs(g.get("approver_roles"), f"{where}.gates.approvers"),
                        required_evidence=_strs(g.get("required_evidence"), f"{where}.evidence"),
                        entry_criteria=_strs(g.get("entry_criteria"), f"{where}.gates.entry"),
                        exit_criteria=_strs(g.get("exit_criteria"), f"{where}.gates.exit"),
                    )
                    for g in p.get("gates") or ()
                ),
            )
        )
    return SdlcTemplate(
        key=key,
        label=_text(raw.get("label"), f"templates.{key}.label"),
        composition=_strs(raw.get("composition"), f"templates.{key}.composition"),
        approach=_text(raw.get("approach"), f"templates.{key}.approach"),
        placement={str(k): str(v) for k, v in dict(raw.get("placement") or {}).items()},
        phases=tuple(phases),
    )


def templates_from_ruleset(ruleset: RuleSet, content_sha256: str) -> WorkflowTemplates:
    data = ruleset.data
    try:
        treatment = data["risk_treatment"]
        ready = data["production_readiness"]
        return WorkflowTemplates(
            version=ruleset.version,
            ref=f"{ruleset.name}@{ruleset.version}#{content_sha256[:12]}",
            sha256=content_sha256,
            roles={str(k): str(v) for k, v in data["roles"].items()},
            stages=_strs(data["stages"], "stages"),
            placement_stages=_strs(data["placement_stages"], "placement_stages"),
            security_baseline=tuple(
                _derived(str(item["key"]), item) for item in data["security_baseline"]
            ),
            security_families={
                str(k): _derived(str(k), v) for k, v in data["security_families"].items()
            },
            checkpoints={
                str(k): CheckpointSpec(
                    stage=_text(v.get("stage"), f"checkpoints.{k}.stage"),
                    name=_text(v.get("name"), f"checkpoints.{k}.name"),
                    purpose=_text(v.get("purpose"), f"checkpoints.{k}.purpose"),
                    approver_roles=_strs(v.get("approver_roles"), f"checkpoints.{k}.approvers"),
                    required_evidence=_strs(
                        v.get("required_evidence"), f"checkpoints.{k}.evidence"
                    ),
                    entry_criteria=_strs(v.get("entry_criteria"), f"checkpoints.{k}.entry"),
                    exit_criteria=_strs(v.get("exit_criteria"), f"checkpoints.{k}.exit"),
                )
                for k, v in data["checkpoints"].items()
            },
            treatment_implement=_treatment(treatment["implement"], "risk_treatment.implement"),
            treatment_verify=_treatment(treatment["verify"], "risk_treatment.verify"),
            treatment_undefined=_treatment(treatment["undefined"], "risk_treatment.undefined"),
            production_readiness=ProductionReadinessSpec(
                stage=_text(ready.get("stage"), "production_readiness.stage"),
                key=_text(ready.get("key"), "production_readiness.key"),
                name=_text(ready.get("name"), "production_readiness.name"),
                purpose=_text(ready.get("purpose"), "production_readiness.purpose"),
                approver_roles=_strs(ready.get("approver_roles"), "production_readiness.approvers"),
                required_evidence=_strs(ready.get("required_evidence"), "production_readiness.ev"),
                entry_criteria=_strs(ready.get("entry_criteria"), "production_readiness.entry"),
                exit_criteria=_strs(ready.get("exit_criteria"), "production_readiness.exit"),
            ),
            templates={str(k): _template(str(k), v) for k, v in data["templates"].items()},
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise RuleConfigurationError(f"workflow templates are malformed: {exc!r}") from exc


def check_against_sdlc(templates: WorkflowTemplates, sdlc: SdlcRules) -> None:
    """The ties to P9 and P6 that the templates alone cannot see."""
    candidates = {c.key: c for c in sdlc.config.candidates}
    missing = sorted(set(candidates) - set(templates.templates))
    if missing:
        raise RuleConfigurationError(f"no workflow template for SDLC candidate(s) {missing}")
    extra = sorted(set(templates.templates) - set(candidates))
    if extra:
        raise RuleConfigurationError(f"workflow template(s) {extra} name no SDLC candidate")
    for key, candidate in candidates.items():
        expected = candidate_models(candidate.attributes)
        actual = tuple(sorted(templates.templates[key].composition))
        if actual != expected:
            raise RuleConfigurationError(
                f"the {key} template is composed of {list(actual)} but the P9 candidate names "
                f"{list(expected)}"
            )
    families = sorted({str(f) for f in SecurityControlFamily} - set(templates.security_families))
    if families:
        raise RuleConfigurationError(f"no security activity for control family(ies) {families}")
    kinds = sorted({str(k) for k in ObligationKind} - set(templates.checkpoints))
    if kinds:
        raise RuleConfigurationError(f"no checkpoint placement for obligation kind(s) {kinds}")


def load_workflow_templates(
    path: Path = PACKAGED_RULES_DIR / WORKFLOW_FILE, *, sdlc: SdlcRules | None = None
) -> WorkflowTemplates:
    templates = templates_from_ruleset(load_ruleset(path), file_canonical_sha256(path))
    check_against_sdlc(templates, sdlc or packaged_sdlc_rules())
    return templates


@lru_cache(maxsize=1)
def packaged_workflow_templates() -> WorkflowTemplates:
    return load_workflow_templates()
