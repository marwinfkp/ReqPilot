"""The workflow as a pure, hashable structure (architecture G.8 ``workflow`` and children).

A :class:`WorkflowPlan` is what generation produces, what validation checks, what
an edit transforms and what export renders. The persisted rows are this structure
spread over ``workflow_phase`` / ``workflow_activity`` / ``workflow_gate`` (and the
provenance in ``workflow_source``); the service converts in both directions, so
there is one definition of a workflow's content and one content hash.

**Provenance is part of the element, not a note about it.** Every activity and
gate carries the :class:`SourceLink` rows it was derived from - the compliance
mapping behind a checkpoint, the risk and mitigation behind a treatment activity,
the derived security requirement behind a security activity - and those are what
the trace edges N.2 #25 / #26 are built from. An edit can change an element's
wording; it can never change its sources (:mod:`~reqpilot.domain.workflow.edits`).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any, Literal

Severity = Literal["error", "open_item"]

#: Bumped when the canonical serialisation changes (it invalidates old hashes).
WORKFLOW_MODEL_VERSION = "workflow-model-1.0.0"

# Source types (``workflow_source.source_type``) and relations.
SOURCE_SDLC_CANDIDATE = "sdlc_candidate"
SOURCE_COMPLIANCE_MAPPING = "compliance_mapping"
SOURCE_RISK = "risk"
SOURCE_RISK_MITIGATION = "risk_mitigation"
SOURCE_SECURITY_FINDING = "security_privacy_finding"
SOURCE_TYPES: frozenset[str] = frozenset(
    {
        SOURCE_SDLC_CANDIDATE,
        SOURCE_COMPLIANCE_MAPPING,
        SOURCE_RISK,
        SOURCE_RISK_MITIGATION,
        SOURCE_SECURITY_FINDING,
    }
)

REL_REALISES = "realises"
REL_CHECKPOINT_FOR = "checkpoint_for"
REL_TREATS = "treats"
REL_IMPLEMENTS = "implements"
REL_VERIFIES = "verifies"
REL_DERIVED_FROM = "derived_from"


@dataclass(frozen=True)
class SourceLink:
    """One persisted record an element was derived from."""

    source_type: str
    source_id: str
    relation: str
    #: The source row's content hash at generation time, where it has one - so a
    #: later change to the source is detectable as staleness, never silent.
    source_hash: str | None = None
    #: A short human label (a control key, a risk title); display only.
    label: str = ""

    def canonical(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "relation": self.relation,
            "source_hash": self.source_hash,
        }


@dataclass(frozen=True)
class Finding:
    """A validation result: an ``error`` refuses the workflow; an ``open_item`` is listed on it."""

    code: str
    severity: Severity
    message: str
    subject: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "subject": self.subject,
        }


@dataclass(frozen=True)
class ActivityPlan:
    key: str
    kind: str
    name: str
    description: str
    responsible_roles: tuple[str, ...]
    deliverables: tuple[str, ...]
    #: A mandatory activity is required by the project's own records and can be
    #: reworded but never removed (``FR-WFL-002``; the P10 exit criterion).
    mandatory: bool = False
    origin: str = "generated"
    sources: tuple[SourceLink, ...] = ()

    def canonical(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind,
            "name": self.name,
            "description": self.description,
            "responsible_roles": list(self.responsible_roles),
            "deliverables": list(self.deliverables),
            "mandatory": self.mandatory,
            "origin": self.origin,
            "sources": [s.canonical() for s in self.sources],
        }


@dataclass(frozen=True)
class GatePlan:
    """A gate of the *generated project's* process - never a ReqPilot gate (M.1, M.4)."""

    key: str
    kind: str
    name: str
    purpose: str
    approver_roles: tuple[str, ...]
    required_evidence: tuple[str, ...]
    entry_criteria: tuple[str, ...]
    exit_criteria: tuple[str, ...]
    mandatory: bool = False
    origin: str = "generated"
    sources: tuple[SourceLink, ...] = ()

    def canonical(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind,
            "name": self.name,
            "purpose": self.purpose,
            "approver_roles": list(self.approver_roles),
            "required_evidence": list(self.required_evidence),
            "entry_criteria": list(self.entry_criteria),
            "exit_criteria": list(self.exit_criteria),
            "mandatory": self.mandatory,
            "origin": self.origin,
            "sources": [s.canonical() for s in self.sources],
        }


@dataclass(frozen=True)
class PhasePlan:
    key: str
    name: str
    description: str
    stages: tuple[str, ...]
    responsible_roles: tuple[str, ...]
    deliverables: tuple[str, ...]
    entry_criteria: tuple[str, ...]
    exit_criteria: tuple[str, ...]
    testing_requirements: tuple[str, ...]
    traceability_requirements: tuple[str, ...]
    activities: tuple[ActivityPlan, ...] = ()
    gates: tuple[GatePlan, ...] = ()
    #: How the phase repeats (iterative models), e.g. "Repeated every sprint".
    cycle: str | None = None
    #: V-Model pairing: the key of the specification phase this test phase verifies.
    verifies: str | None = None
    origin: str = "generated"

    def canonical(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "stages": list(self.stages),
            "cycle": self.cycle,
            "verifies": self.verifies,
            "responsible_roles": list(self.responsible_roles),
            "deliverables": list(self.deliverables),
            "entry_criteria": list(self.entry_criteria),
            "exit_criteria": list(self.exit_criteria),
            "testing_requirements": list(self.testing_requirements),
            "traceability_requirements": list(self.traceability_requirements),
            "origin": self.origin,
            "activities": [a.canonical() for a in self.activities],
            "gates": [g.canonical() for g in self.gates],
        }


@dataclass(frozen=True)
class WorkflowPlan:
    """One project workflow for one G6-selected SDLC candidate."""

    candidate_key: str
    candidate_label: str
    composition: tuple[str, ...]
    approach: str
    template_ref: str
    phases: tuple[PhasePlan, ...]
    #: Workflow-level provenance: the selected candidate it realises (N.2 #24).
    sources: tuple[SourceLink, ...] = ()
    #: What the source records leave for a human to resolve (never errors).
    open_items: tuple[Finding, ...] = field(default=())

    def canonical(self) -> dict[str, Any]:
        return {
            "model": WORKFLOW_MODEL_VERSION,
            "candidate_key": self.candidate_key,
            "candidate_label": self.candidate_label,
            "composition": list(self.composition),
            "approach": self.approach,
            "template_ref": self.template_ref,
            "sources": [s.canonical() for s in self.sources],
            "phases": [p.canonical() for p in self.phases],
            "open_items": [f.as_dict() for f in self.open_items],
        }

    def content_hash(self) -> str:
        return sha256_json(self.canonical())

    # -- lookups -----------------------------------------------------------
    def activities(self) -> list[tuple[PhasePlan, ActivityPlan]]:
        return [(p, a) for p in self.phases for a in p.activities]

    def gates(self) -> list[tuple[PhasePlan, GatePlan]]:
        return [(p, g) for p in self.phases for g in p.gates]

    def phase(self, key: str) -> PhasePlan | None:
        return next((p for p in self.phases if p.key == key), None)

    def with_phase(self, phase: PhasePlan) -> WorkflowPlan:
        return replace(self, phases=tuple(phase if p.key == phase.key else p for p in self.phases))

    def with_open_items(self, items: tuple[Finding, ...]) -> WorkflowPlan:
        return replace(self, open_items=items)


def sha256_json(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def plan_from_structure(data: dict[str, Any]) -> WorkflowPlan:
    """Rebuild a plan from :meth:`WorkflowPlan.canonical` (the stored generated snapshot)."""

    def sources(raw: list[dict[str, Any]]) -> tuple[SourceLink, ...]:
        return tuple(
            SourceLink(
                str(s["source_type"]),
                str(s["source_id"]),
                str(s["relation"]),
                s.get("source_hash"),
            )
            for s in raw
        )

    def strs(raw: Any) -> tuple[str, ...]:
        return tuple(str(x) for x in raw or ())

    phases = []
    for p in data["phases"]:
        phases.append(
            PhasePlan(
                key=str(p["key"]),
                name=str(p["name"]),
                description=str(p["description"]),
                stages=strs(p["stages"]),
                cycle=p.get("cycle"),
                verifies=p.get("verifies"),
                responsible_roles=strs(p["responsible_roles"]),
                deliverables=strs(p["deliverables"]),
                entry_criteria=strs(p["entry_criteria"]),
                exit_criteria=strs(p["exit_criteria"]),
                testing_requirements=strs(p["testing_requirements"]),
                traceability_requirements=strs(p["traceability_requirements"]),
                origin=str(p.get("origin", "generated")),
                activities=tuple(
                    ActivityPlan(
                        key=str(a["key"]),
                        kind=str(a["kind"]),
                        name=str(a["name"]),
                        description=str(a["description"]),
                        responsible_roles=strs(a["responsible_roles"]),
                        deliverables=strs(a["deliverables"]),
                        mandatory=bool(a["mandatory"]),
                        origin=str(a.get("origin", "generated")),
                        sources=sources(a.get("sources", [])),
                    )
                    for a in p["activities"]
                ),
                gates=tuple(
                    GatePlan(
                        key=str(g["key"]),
                        kind=str(g["kind"]),
                        name=str(g["name"]),
                        purpose=str(g["purpose"]),
                        approver_roles=strs(g["approver_roles"]),
                        required_evidence=strs(g["required_evidence"]),
                        entry_criteria=strs(g["entry_criteria"]),
                        exit_criteria=strs(g["exit_criteria"]),
                        mandatory=bool(g["mandatory"]),
                        origin=str(g.get("origin", "generated")),
                        sources=sources(g.get("sources", [])),
                    )
                    for g in p["gates"]
                ),
            )
        )
    return WorkflowPlan(
        candidate_key=str(data["candidate_key"]),
        candidate_label=str(data["candidate_label"]),
        composition=strs(data["composition"]),
        approach=str(data["approach"]),
        template_ref=str(data["template_ref"]),
        phases=tuple(phases),
        sources=sources(data.get("sources", [])),
        open_items=tuple(
            Finding(str(f["code"]), f["severity"], str(f["message"]), str(f.get("subject", "")))
            for f in data.get("open_items", [])
        ),
    )
