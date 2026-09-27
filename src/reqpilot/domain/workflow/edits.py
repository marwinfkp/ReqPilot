"""The Project Manager's edits to a generated workflow (``FR-WFL-007``), as pure functions.

An edit is applied to a :class:`~reqpilot.domain.workflow.plan.WorkflowPlan`, the
result is validated (:mod:`~reqpilot.domain.workflow.validation`) and only then
stored, with a change-log row recording each changed field's previous and new
value. What an edit can and cannot do is fixed here, not left to the caller:

* **Wording and assignments can change**: a phase's name, description, roles,
  deliverables, criteria and testing and traceability requirements; an activity's
  name, description, roles and deliverables; a gate's name, purpose, approvers,
  required evidence and criteria.
* **Provenance cannot**: no edit names an element's key, kind, ``mandatory`` flag,
  sources or phase, so a checkpoint stays linked to its mapping and a treatment
  activity to its risk, whatever its wording becomes.
* **Mandatory elements cannot be removed**: an activity derived from a record, or
  marked mandatory, is refused removal; only template and manual activities can go.
* **The result must still validate**: emptying a phase's exit criteria or a gate's
  approvers is refused by validation, not stored.

A changed generated element becomes ``edited``; an added one is ``manual``. The
generated workflow itself is kept unchanged on the ``workflow`` row.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from reqpilot.domain.enums import WorkflowActivityKind, WorkflowElementOrigin
from reqpilot.domain.errors import WorkflowError
from reqpilot.domain.workflow.plan import ActivityPlan, GatePlan, PhasePlan, WorkflowPlan

MAX_TEXT = 4000
MAX_NAME = 300
MAX_ITEMS = 50
MAX_ITEM = 500

PHASE_TEXT_FIELDS = frozenset({"name", "description"})
PHASE_LIST_FIELDS = frozenset(
    {
        "responsible_roles",
        "deliverables",
        "entry_criteria",
        "exit_criteria",
        "testing_requirements",
        "traceability_requirements",
    }
)
ACTIVITY_TEXT_FIELDS = frozenset({"name", "description"})
ACTIVITY_LIST_FIELDS = frozenset({"responsible_roles", "deliverables"})
GATE_TEXT_FIELDS = frozenset({"name", "purpose"})
GATE_LIST_FIELDS = frozenset(
    {"approver_roles", "required_evidence", "entry_criteria", "exit_criteria"}
)


@dataclass(frozen=True)
class UpdatePhase:
    phase_key: str
    changes: Mapping[str, Any]


@dataclass(frozen=True)
class UpdateActivity:
    activity_key: str
    changes: Mapping[str, Any]


@dataclass(frozen=True)
class UpdateGate:
    gate_key: str
    changes: Mapping[str, Any]


@dataclass(frozen=True)
class AddActivity:
    phase_key: str
    name: str
    description: str
    responsible_roles: tuple[str, ...]
    deliverables: tuple[str, ...]


@dataclass(frozen=True)
class RemoveActivity:
    activity_key: str


WorkflowEdit = UpdatePhase | UpdateActivity | UpdateGate | AddActivity | RemoveActivity


@dataclass(frozen=True)
class EditResult:
    """What the change log records for one accepted edit."""

    operation: str  # update | add | remove
    element_type: str  # phase | activity | gate
    element_key: str
    #: ``{field: {"before": ..., "after": ...}}``
    changes: dict[str, dict[str, Any]]


def _refuse(code: str, message: str) -> WorkflowError:
    return WorkflowError(message, ({"code": code, "severity": "error", "message": message},))


def _text(field: str, value: Any, *, limit: int) -> str:
    if not isinstance(value, str):
        raise _refuse("EDIT_INVALID_VALUE", f"{field} must be text")
    text = " ".join(value.split()) if limit == MAX_NAME else value.strip()
    if len(text) > limit:
        raise _refuse("EDIT_INVALID_VALUE", f"{field} exceeds {limit} characters")
    return text


def _items(field: str, value: Any) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise _refuse("EDIT_INVALID_VALUE", f"{field} must be a list of text items")
    if len(value) > MAX_ITEMS:
        raise _refuse("EDIT_INVALID_VALUE", f"{field} has more than {MAX_ITEMS} items")
    out: list[str] = []
    for item in value:
        text = _text(field, item, limit=MAX_ITEM)
        if text and text not in out:
            out.append(text)
    return tuple(out)


def _normalise(
    changes: Mapping[str, Any], text_fields: frozenset[str], list_fields: frozenset[str], what: str
) -> dict[str, Any]:
    unknown = sorted(set(changes) - text_fields - list_fields)
    if unknown:
        raise _refuse(
            "EDIT_FIELD_NOT_EDITABLE",
            f"{what} field(s) {unknown} cannot be edited; keys, kinds, mandatory flags, phases "
            "and provenance are fixed at generation",
        )
    if not changes:
        raise _refuse("EDIT_EMPTY", "the edit changes nothing")
    out: dict[str, Any] = {}
    for field, value in changes.items():
        if field in text_fields:
            limit = MAX_NAME if field == "name" else MAX_TEXT
            out[field] = _text(field, value, limit=limit)
        else:
            out[field] = _items(field, value)
    return out


def _diff(before: Any, after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    diff: dict[str, dict[str, Any]] = {}
    for field, value in after.items():
        old = getattr(before, field)
        if old != value:
            diff[field] = {
                "before": list(old) if isinstance(old, tuple) else old,
                "after": list(value) if isinstance(value, tuple) else value,
            }
    if not diff:
        raise _refuse("EDIT_EMPTY", "the edit changes nothing")
    return diff


def _edited(origin: str) -> str:
    return (
        origin if origin == str(WorkflowElementOrigin.MANUAL) else str(WorkflowElementOrigin.EDITED)
    )


def apply_edit(
    plan: WorkflowPlan, edit: WorkflowEdit, *, revision: int
) -> tuple[WorkflowPlan, EditResult]:
    """Apply one edit; return the new plan and the change record. Validation is the caller's."""
    if isinstance(edit, UpdatePhase):
        phase = plan.phase(edit.phase_key)
        if phase is None:
            raise _refuse("EDIT_UNKNOWN_ELEMENT", f"no phase {edit.phase_key!r} in this workflow")
        values = _normalise(edit.changes, PHASE_TEXT_FIELDS, PHASE_LIST_FIELDS, "phase")
        diff = _diff(phase, values)
        updated = replace(phase, **values, origin=_edited(phase.origin))
        return plan.with_phase(updated), EditResult("update", "phase", phase.key, diff)

    if isinstance(edit, UpdateActivity):
        phase, activity = _find_activity(plan, edit.activity_key)
        values = _normalise(edit.changes, ACTIVITY_TEXT_FIELDS, ACTIVITY_LIST_FIELDS, "activity")
        diff = _diff(activity, values)
        new_activity = replace(activity, **values, origin=_edited(activity.origin))
        new_phase = replace(
            phase,
            activities=tuple(
                new_activity if a.key == activity.key else a for a in phase.activities
            ),
        )
        return plan.with_phase(new_phase), EditResult("update", "activity", activity.key, diff)

    if isinstance(edit, UpdateGate):
        phase, gate = _find_gate(plan, edit.gate_key)
        values = _normalise(edit.changes, GATE_TEXT_FIELDS, GATE_LIST_FIELDS, "gate")
        diff = _diff(gate, values)
        new_gate = replace(gate, **values, origin=_edited(gate.origin))
        new_phase = replace(
            phase, gates=tuple(new_gate if g.key == gate.key else g for g in phase.gates)
        )
        return plan.with_phase(new_phase), EditResult("update", "gate", gate.key, diff)

    if isinstance(edit, AddActivity):
        phase_or_none = plan.phase(edit.phase_key)
        if phase_or_none is None:
            raise _refuse("EDIT_UNKNOWN_ELEMENT", f"no phase {edit.phase_key!r} in this workflow")
        phase = phase_or_none
        name = _text("name", edit.name, limit=MAX_NAME)
        if not name:
            raise _refuse("EDIT_INVALID_VALUE", "a new activity needs a name")
        activity = ActivityPlan(
            key=f"{phase.key}:manual:r{revision}",
            kind=str(WorkflowActivityKind.MANUAL),
            name=name,
            description=_text("description", edit.description, limit=MAX_TEXT),
            responsible_roles=_items("responsible_roles", list(edit.responsible_roles)),
            deliverables=_items("deliverables", list(edit.deliverables)),
            mandatory=False,
            origin=str(WorkflowElementOrigin.MANUAL),
        )
        new_phase = replace(phase, activities=(*phase.activities, activity))
        record = {
            field: {"before": None, "after": _plain(getattr(activity, field))}
            for field in ("name", "description", "responsible_roles", "deliverables")
        }
        record["phase"] = {"before": None, "after": phase.key}
        return plan.with_phase(new_phase), EditResult("add", "activity", activity.key, record)

    if isinstance(edit, RemoveActivity):
        phase, activity = _find_activity(plan, edit.activity_key)
        if activity.mandatory or activity.sources:
            raise _refuse(
                "EDIT_REMOVES_MANDATORY",
                f"activity {activity.key!r} is required by the project's records and cannot be "
                "removed; it can be reworded",
            )
        new_phase = replace(
            phase, activities=tuple(a for a in phase.activities if a.key != activity.key)
        )
        record = {
            field: {"before": _plain(getattr(activity, field)), "after": None}
            for field in ("name", "description", "responsible_roles", "deliverables", "kind")
        }
        record["phase"] = {"before": phase.key, "after": None}
        return plan.with_phase(new_phase), EditResult("remove", "activity", activity.key, record)

    raise _refuse("EDIT_UNKNOWN", f"unknown edit {type(edit).__name__}")  # pragma: no cover


def _plain(value: Any) -> Any:
    return list(value) if isinstance(value, tuple) else value


def _find_activity(plan: WorkflowPlan, key: str) -> tuple[PhasePlan, ActivityPlan]:
    for phase, activity in plan.activities():
        if activity.key == key:
            return phase, activity
    raise _refuse("EDIT_UNKNOWN_ELEMENT", f"no activity {key!r} in this workflow")


def _find_gate(plan: WorkflowPlan, key: str) -> tuple[PhasePlan, GatePlan]:
    for phase, gate in plan.gates():
        if gate.key == key:
            return phase, gate
    raise _refuse("EDIT_UNKNOWN_ELEMENT", f"no gate {key!r} in this workflow")
