"""P10 workflow generation, the pure core: templates, derivation, validation, edits, rendering.

Every case is hand-constructed, so its expected result can be checked by reading it:
a mapping that must become a checkpoint, a HIGH risk whose recorded mitigation must
be implemented *and* verified, a rejected mitigation that must not appear, a gap
that must stay an open item rather than become a checkpoint.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import replace

import pytest

from reqpilot.artifacts.docx import docx_text, render_docx
from reqpilot.artifacts.markdown import render_markdown
from reqpilot.artifacts.model import validate_document
from reqpilot.domain.compliance.language import assert_artefact_language
from reqpilot.domain.enums import WorkflowActivityKind, WorkflowGateKind
from reqpilot.domain.errors import RuleConfigurationError, WorkflowError
from reqpilot.domain.models.workflow import Workflow
from reqpilot.domain.workflow import (
    AddActivity,
    GapInput,
    MappingInput,
    MitigationInput,
    RemoveActivity,
    RiskInput,
    SecurityFindingInput,
    UpdateActivity,
    UpdateGate,
    UpdatePhase,
    WorkflowInputs,
    apply_edit,
    derive_workflow,
    mandatory_sources,
    validate_workflow,
)
from reqpilot.domain.workflow.plan import SourceLink, plan_from_structure
from reqpilot.domain.workflow.validation import known_sources, sources_of
from reqpilot.rules.loader import RuleSet
from reqpilot.rules.sdlc import packaged_sdlc_rules
from reqpilot.rules.workflow import (
    candidate_models,
    check_against_sdlc,
    packaged_workflow_templates,
    templates_from_ruleset,
)
from reqpilot.services.workflow.document import (
    GATE_NOTICE,
    WorkflowDocumentMeta,
    workflow_document,
)
from reqpilot.services.workflow.rows import plan_from_rows, rows_from_plan

pytestmark = pytest.mark.unit

T = packaged_workflow_templates()
H = "a" * 64


def uid() -> str:
    return str(uuid.uuid4())


def inputs(candidate: str = "v_model", **overrides: object) -> WorkflowInputs:
    definition = next(c for c in packaged_sdlc_rules().config.candidates if c.key == candidate)
    values: dict[str, object] = {
        "project_id": uid(),
        "sdlc_run_id": uid(),
        "candidate_id": uid(),
        "candidate_key": candidate,
        "candidate_label": definition.label,
        "candidate_models": candidate_models(definition.attributes),
        "baseline_id": uid(),
        "baseline_label": "B1",
    }
    values.update(overrides)
    return WorkflowInputs(**values)  # type: ignore[arg-type]


def mapping(control: str, kind: str = "control", **kw: object) -> MappingInput:
    values: dict[str, object] = {
        "id": uid(),
        "control_key": control,
        "control_title": f"Title of {control}",
        "obligation_kind": kind,
        "relationship": "addresses",
        "status": "candidate",
        "requirement_label": "FR-LOAN-001 v1",
        "evidence_count": 1,
        "content_hash": H,
    }
    values.update(kw)
    return MappingInput(**values)  # type: ignore[arg-type]


def mitigation(status: str = "accepted", ai: bool = True) -> MitigationInput:
    return MitigationInput(uid(), "Rehearse the migration with retention checks.", ai, status)


def risk(severity: str = "high", mitigations: tuple[MitigationInput, ...] = ()) -> RiskInput:
    return RiskInput(uid(), "Retention may not survive migration", "compliance", severity,
                     "accepted", "FR-LOAN-003 v1", H, mitigations)  # fmt: skip


def finding(family: str) -> SecurityFindingInput:
    return SecurityFindingInput(uid(), family, "security", "high", "approved", "FR-LOAN-002 v1", H)


def validate(plan, data: WorkflowInputs):  # type: ignore[no-untyped-def]
    return validate_workflow(
        plan,
        candidate_key=data.candidate_key,
        candidate_id=data.candidate_id,
        roles=T.roles,
        required=mandatory_sources(data),
        known=known_sources(data),
    )


def codes(findings) -> set[str]:  # type: ignore[no-untyped-def]
    return {f.code for f in findings}


# --- the templates -----------------------------------------------------------------------


def test_every_p9_candidate_has_a_template_of_its_own_composition() -> None:
    candidates = packaged_sdlc_rules().config.candidates
    assert {c.key for c in candidates} == set(T.templates)
    for c in candidates:
        assert tuple(sorted(T.templates[c.key].composition)) == candidate_models(c.attributes)
    assert set(T.templates["agile_v_model_hybrid"].composition) == {"agile", "v_model"}
    assert set(T.templates["agile_devsecops_hybrid"].composition) == {"agile", "devsecops"}


def test_the_templates_are_different_structures_not_renamed_sequences() -> None:
    shapes = {k: tuple(p.key for p in t.phases) for k, t in T.templates.items()}
    assert len(set(shapes.values())) == len(shapes), "two SDLCs share one phase sequence"
    v_model = T.templates["v_model"].phases
    pairs = {p.key: p.verifies for p in v_model if p.verifies}
    assert pairs == {
        "unit_testing": "module_design",
        "integration_testing": "architecture_design",
        "system_testing": "system_design",
        "acceptance_testing": "requirements",
    }, "the V-Model pairs each test level with its specification level"
    assert any(p.cycle for p in T.templates["agile"].phases), "agile iterates"
    assert sum(1 for p in T.templates["spiral"].phases if p.cycle) >= 3, "spiral cycles"
    assert "risk_analysis" in {p.key for p in T.templates["spiral"].phases}
    assert "operate" in {p.key for p in T.templates["devsecops"].phases}


def test_the_loader_refuses_a_template_that_would_thin_the_workflow() -> None:
    base = T  # the packaged file loaded; now break copies of its raw data

    from reqpilot.rules.loader import load_ruleset
    from reqpilot.rules.workflow import PACKAGED_RULES_DIR, WORKFLOW_FILE

    raw = load_ruleset(PACKAGED_RULES_DIR / WORKFLOW_FILE)

    def thaw(value):  # type: ignore[no-untyped-def]
        if hasattr(value, "items"):
            return {k: thaw(v) for k, v in value.items()}
        if isinstance(value, tuple):
            return [thaw(v) for v in value]
        return value

    def load(mutate) -> None:  # type: ignore[no-untyped-def]
        data = thaw(raw.data)
        mutate(data)
        templates = templates_from_ruleset(
            RuleSet(raw.name, raw.version, raw.description, data),  # type: ignore[arg-type]
            "b" * 64,
        )
        check_against_sdlc(templates, packaged_sdlc_rules())

    load(lambda d: None)  # the untouched data loads
    for mutate in (
        lambda d: d["templates"].pop("spiral"),
        lambda d: d["templates"]["agile_v_model_hybrid"].__setitem__("composition", ["agile"]),
        lambda d: d["templates"]["agile"]["phases"][0].__setitem__("exit_criteria", []),
        lambda d: d["templates"]["agile"]["phases"][0].__setitem__("roles", ["wizard"]),
        lambda d: d["templates"]["waterfall"]["placement"].pop("verification"),
        lambda d: d["security_families"].pop("transaction_integrity"),
        lambda d: d["checkpoints"].pop("approval_checkpoint"),
    ):
        with pytest.raises(RuleConfigurationError):
            load(mutate)
    assert base.ref.startswith("workflow_templates@1.0.0#")


# --- derivation ----------------------------------------------------------------------------


@pytest.mark.parametrize("candidate", sorted(T.templates))
def test_each_candidate_gets_its_own_phases_in_order_with_one_production_readiness_gate(
    candidate: str,
) -> None:
    data = inputs(candidate)
    plan = derive_workflow(data, T)
    assert [p.key for p in plan.phases] == [p.key for p in T.templates[candidate].phases]
    assert (
        plan.candidate_key == candidate and plan.composition == T.templates[candidate].composition
    )
    ready = [(p, g) for p, g in plan.gates() if g.kind == WorkflowGateKind.PRODUCTION_READINESS]
    assert len(ready) == 1 and "release" in ready[0][0].stages
    for phase in plan.phases:
        assert phase.responsible_roles and phase.deliverables and phase.activities
        assert phase.entry_criteria and phase.exit_criteria
        assert phase.testing_requirements and phase.traceability_requirements
    assert not validate(plan, data)


def test_mappings_become_checkpoints_placed_by_obligation_kind() -> None:
    a, b = mapping("CTL-A"), mapping("CTL-A", requirement_label="FR-LOAN-004 v2")
    approval = mapping("APR-1", "approval_checkpoint", relationship="partially_addresses")
    data = inputs("v_model", mappings=(a, b, approval))
    plan = derive_workflow(data, T)
    gates = {g.key: (p, g) for p, g in plan.gates()}
    phase, control = gates["checkpoint:CTL-A"]
    assert phase.key == "system_testing" and control.mandatory
    assert {s.source_id for s in control.sources} == {a.id, b.id}, "one checkpoint per control"
    assert "potentially applicable" in control.purpose
    phase, before_deploy = gates["checkpoint:APR-1"]
    assert phase.key == "release", "a checklist approval checkpoint comes before deployment"
    assert "project_manager" in before_deploy.approver_roles
    exit_text = " ".join(plan.phase("system_testing").exit_criteria)  # type: ignore[union-attr]
    assert "CTL-A" in exit_text
    assert not validate(plan, data)


def test_every_recorded_high_risk_mitigation_is_implemented_and_verified() -> None:
    live, rejected = mitigation("accepted"), mitigation("rejected")
    high = risk("high", (live, rejected))
    medium = risk("medium", (mitigation(),))
    data = inputs("v_model", risks=(high, medium))
    plan = derive_workflow(data, T)
    risk_activities = [(p, a) for p, a in plan.activities() if a.kind.startswith("risk_")]
    assert {(p.key, a.kind) for p, a in risk_activities} == {
        ("coding", WorkflowActivityKind.RISK_TREATMENT),
        ("system_testing", WorkflowActivityKind.RISK_VERIFICATION),
    }
    for _p, activity in risk_activities:
        ids = {s.source_id for s in activity.sources}
        assert high.id in ids and live.id in ids and rejected.id not in ids
        assert "accepted by a human" in activity.description
    assert medium.id not in {s.source_id for _p, a in plan.activities() for s in a.sources}
    assert not plan.open_items


def test_a_high_risk_without_a_mitigation_is_an_open_item_never_an_invented_mitigation() -> None:
    bare = risk("high", ())
    data = inputs("spiral", risks=(bare,))
    plan = derive_workflow(data, T)
    [(phase, activity)] = [(p, a) for p, a in plan.activities() if a.kind.startswith("risk_")]
    assert phase.key == "risk_analysis" and activity.kind == WorkflowActivityKind.RISK_TREATMENT
    assert [s.source_type for s in activity.sources] == ["risk"]
    assert codes(plan.open_items) == {"HIGH_RISK_WITHOUT_MITIGATION"}
    assert not validate(plan, data)


def test_an_unaccepted_ai_suggestion_is_listed_as_an_open_item() -> None:
    data = inputs("agile", risks=(risk("high", (mitigation("suggested", ai=True),)),))
    plan = derive_workflow(data, T)
    assert codes(plan.open_items) == {"MITIGATION_AWAITS_VALIDATION"}
    human = inputs("agile", risks=(risk("high", (mitigation("suggested", ai=False),)),))
    assert not derive_workflow(human, T).open_items


def test_security_activities_come_from_the_derived_requirements() -> None:
    data = inputs(
        "waterfall", findings=(finding("transaction_integrity"), finding("authentication"))
    )
    plan = derive_workflow(data, T)
    security = {a.key: (p.key, a) for p, a in plan.activities() if a.kind == "security"}
    assert security["security:threat_modelling"][0] == "design"
    assert security["security:static_security_testing"][0] == "implementation"
    assert security["security:transaction_integrity"][0] == "design"
    validation_phase = plan.phase("acceptance")
    assert validation_phase is not None
    assert any(
        t.startswith("Transaction-integrity testing") for t in validation_phase.testing_requirements
    ), "the problem statement's example: transaction-integrity testing during validation"
    assert not validate(plan, data)
    none = derive_workflow(inputs("waterfall"), T)
    assert not [a for _p, a in none.activities() if a.kind == "security"]


def test_gaps_stay_open_items_and_never_become_checkpoints() -> None:
    data = inputs("devsecops", gaps=(GapInput(uid(), "LO-PRV-CONSENT", "Consent", "control"),))
    plan = derive_workflow(data, T)
    assert codes(plan.open_items) == {"COMPLIANCE_GAP_OPEN"}
    assert not [g for _p, g in plan.gates() if g.kind == "compliance_checkpoint"]


def test_a_mapping_without_evidence_is_an_open_item() -> None:
    data = inputs("v_model", mappings=(mapping("CTL-B", evidence_count=0),))
    assert codes(derive_workflow(data, T).open_items) == {"MAPPING_WITHOUT_EVIDENCE"}


def test_generation_refuses_rather_than_substitute_a_model() -> None:
    with pytest.raises(WorkflowError, match="no workflow template"):
        derive_workflow(inputs("v_model", candidate_key="kanban"), T)
    with pytest.raises(WorkflowError, match="composed of"):
        derive_workflow(inputs("agile_v_model_hybrid", candidate_models=("agile",)), T)


def test_derivation_is_deterministic() -> None:
    data = inputs(
        "agile_devsecops_hybrid",
        mappings=(mapping("CTL-A"), mapping("APR-1", "approval_checkpoint")),
        risks=(risk("high", (mitigation(),)),),
        findings=(finding("session_management"),),
    )
    assert derive_workflow(data, T).content_hash() == derive_workflow(data, T).content_hash()


# --- validation: the exit criterion as a check ------------------------------------------


def _full() -> tuple[WorkflowInputs, object]:
    data = inputs(
        "v_model",
        mappings=(mapping("CTL-A"), mapping("APR-1", "approval_checkpoint")),
        risks=(risk("high", (mitigation(),)),),
        findings=(finding("retention"),),
    )
    return data, derive_workflow(data, T)


def _drop(plan, predicate):  # type: ignore[no-untyped-def]
    phases = tuple(
        replace(
            p,
            activities=tuple(a for a in p.activities if not predicate(a)),
            gates=tuple(g for g in p.gates if not predicate(g)),
        )
        for p in plan.phases
    )
    return replace(plan, phases=phases)


def test_a_missing_checkpoint_or_mitigation_activity_fails_validation() -> None:
    data, plan = _full()
    assert not validate(plan, data)
    assert "CHECKPOINT_MISSING" in codes(
        validate(_drop(plan, lambda e: e.key == "checkpoint:CTL-A"), data)
    )
    assert "MITIGATION_NOT_VERIFIED" in codes(
        validate(_drop(plan, lambda e: e.kind == "risk_verification"), data)
    )
    assert "MITIGATION_NOT_IMPLEMENTED" in codes(
        validate(_drop(plan, lambda e: e.kind == "risk_treatment"), data)
    )
    no_risk = _drop(plan, lambda e: e.kind.startswith("risk_"))
    assert {"RISK_ACTIVITY_MISSING", "MITIGATION_NOT_IMPLEMENTED"} <= codes(validate(no_risk, data))
    assert "SECURITY_ACTIVITY_MISSING" in codes(
        validate(_drop(plan, lambda e: e.kind == "security"), data)
    )
    assert "PRODUCTION_READINESS_COUNT" in codes(
        validate(_drop(plan, lambda e: e.kind == "production_readiness"), data)
    )


def test_validation_refuses_forged_or_foreign_provenance_and_a_wrong_candidate() -> None:
    data, plan = _full()
    phase = plan.phases[0]
    forged = replace(
        phase.activities[0],
        mandatory=True,
        sources=(SourceLink("risk", uid(), "treats"),),  # a risk of no eligible record
    )
    tampered = plan.with_phase(replace(phase, activities=(forged, *phase.activities[1:])))
    assert "SOURCE_UNRESOLVED" in codes(validate(tampered, data))
    assert "CANDIDATE_MISMATCH" in codes(
        validate_workflow(
            plan,
            candidate_key="agile",
            candidate_id=data.candidate_id,
            roles=T.roles,
            required=mandatory_sources(data),
            known=known_sources(data),
        )
    )


def test_validation_refuses_empty_structure_and_unknown_roles() -> None:
    data, plan = _full()
    phase = plan.phases[0]
    empty = plan.with_phase(replace(phase, exit_criteria=()))
    assert "PHASE_EXIT_CRITERIA_MISSING" in codes(validate(empty, data))
    wizard = plan.with_phase(replace(phase, responsible_roles=("wizard",)))
    assert "UNKNOWN_ROLE" in codes(validate(wizard, data))
    odd = plan.with_phase(replace(phase, verifies="nowhere"))
    assert "VERIFIES_UNKNOWN_PHASE" in codes(validate(odd, data))


# --- edits ---------------------------------------------------------------------------------


def test_an_edit_rewords_and_records_before_and_after() -> None:
    _data, plan = _full()
    phase = plan.phases[0]
    new_plan, result = apply_edit(
        plan, UpdatePhase(phase.key, {"exit_criteria": ["Plan reviewed by QA"]}), revision=2
    )
    assert result.operation == "update" and result.element_type == "phase"
    assert result.changes["exit_criteria"]["before"] == list(phase.exit_criteria)
    assert result.changes["exit_criteria"]["after"] == ["Plan reviewed by QA"]
    edited = new_plan.phase(phase.key)
    assert edited is not None and edited.origin == "edited"
    assert new_plan.content_hash() != plan.content_hash()


def test_an_edit_can_never_touch_provenance_kind_key_or_the_mandatory_flag() -> None:
    _data, plan = _full()
    _p, checkpoint = next((p, g) for p, g in plan.gates() if g.kind == "compliance_checkpoint")
    for field in ("sources", "kind", "key", "mandatory", "phase"):
        with pytest.raises(WorkflowError, match="cannot be edited"):
            apply_edit(plan, UpdateGate(checkpoint.key, {field: "x"}), revision=2)
    reworded, _ = apply_edit(plan, UpdateGate(checkpoint.key, {"name": "Renamed"}), revision=2)
    _p, after = next((p, g) for p, g in reworded.gates() if g.key == checkpoint.key)
    assert after.sources == checkpoint.sources and after.mandatory


def test_mandatory_activities_cannot_be_removed_but_template_and_manual_ones_can() -> None:
    data, plan = _full()
    _p, treatment = next((p, a) for p, a in plan.activities() if a.kind == "risk_treatment")
    with pytest.raises(WorkflowError, match="cannot be removed"):
        apply_edit(plan, RemoveActivity(treatment.key), revision=2)
    phase = plan.phases[0]
    added, result = apply_edit(
        plan,
        AddActivity(phase.key, "Regulator walkthrough", "", ("compliance_officer",), ("Minutes",)),
        revision=2,
    )
    assert result.operation == "add" and result.element_key == f"{phase.key}:manual:r2"
    assert not validate(added, data)
    removed, _ = apply_edit(added, RemoveActivity(result.element_key), revision=3)
    template = next(a for a in phase.activities if a.kind == "template")
    removed, _ = apply_edit(removed, RemoveActivity(template.key), revision=4)
    assert not codes(validate(removed, data)) - {"PHASE_ACTIVITIES_MISSING"}


def test_an_empty_or_unchanged_edit_is_refused_and_an_invalid_result_fails_validation() -> None:
    data, plan = _full()
    phase = plan.phases[0]
    with pytest.raises(WorkflowError, match="changes nothing"):
        apply_edit(plan, UpdatePhase(phase.key, {}), revision=2)
    with pytest.raises(WorkflowError, match="changes nothing"):
        apply_edit(plan, UpdatePhase(phase.key, {"name": phase.name}), revision=2)
    emptied, _ = apply_edit(plan, UpdatePhase(phase.key, {"exit_criteria": []}), revision=2)
    assert "PHASE_EXIT_CRITERIA_MISSING" in codes(validate(emptied, data))
    _p, activity = plan.activities()[0]
    noroles, _ = apply_edit(
        plan, UpdateActivity(activity.key, {"responsible_roles": []}), revision=2
    )
    assert "ACTIVITY_ROLES_MISSING" in codes(validate(noroles, data))


def test_edit_validation_uses_the_provenance_the_workflow_was_generated_with() -> None:
    _data, plan = _full()
    recorded = sources_of(plan_from_structure(plan.canonical()))
    assert recorded["compliance_mapping"] and recorded["risk_mitigation"]


# --- rows and rendering -----------------------------------------------------------------------


def test_rows_round_trip_exactly() -> None:
    data, plan = _full()
    project_id, workflow_id = uuid.uuid4(), uuid.uuid4()
    rows = rows_from_plan(project_id, workflow_id, plan)
    workflow = Workflow(
        id=workflow_id, project_id=project_id, generated_structure=plan.canonical(),
        open_items=[f.as_dict() for f in plan.open_items],
    )  # fmt: skip
    rebuilt = plan_from_rows(
        workflow, list(rows.phases), list(rows.activities), list(rows.gates), list(rows.sources)
    )
    assert rebuilt.content_hash() == plan.content_hash()
    assert plan_from_structure(plan.canonical()).content_hash() == plan.content_hash()
    assert not validate(rebuilt, data)


def test_the_workflow_renders_through_the_p8_renderers_with_every_element() -> None:
    _data, plan = _full()
    meta = WorkflowDocumentMeta(
        project_name="Synthetic", workflow_id=uid(), revision=1, status="complete",
        sdlc_run_id=uid(), baseline_label="B1", g6_decisions=4, content_hash=H,
        generated_at="2026-09-26T10:00:00", last_changed_at="2026-09-26T10:00:00",
    )  # fmt: skip
    document = workflow_document(plan, meta, role_label=T.role_label, source_label={})
    validate_document(document, frozenset())
    markdown = render_markdown(document)
    assert_artefact_language(markdown)
    for phase in plan.phases:
        assert phase.name in markdown
    for _p, gate in plan.gates():
        assert gate.name in markdown
    assert "Production-readiness approval" in markdown
    assert GATE_NOTICE[:60] in markdown
    when = dt.datetime(2026, 9, 26, 10, tzinfo=dt.UTC)
    first, second = (
        render_docx(document, generated_at=when),
        render_docx(document, generated_at=when),
    )
    assert first == second, "DOCX export is byte-reproducible"
    text = docx_text(first)
    assert "Production-readiness approval" in text and plan.phases[-1].name in text
