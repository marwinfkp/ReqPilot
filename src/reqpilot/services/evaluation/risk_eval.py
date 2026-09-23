"""The P7 risk-analysis evaluation (P7-RISK-SYNTHETIC-v1).

**There is no approved numeric target for risk analysis, and this module invents
none.** Approved Phase 0 O.1 lists nine core metrics and none of them measures
risk identification; E4 (citation / evidence correctness) is defined as a manual
audit of a 50-claim sample and is not computable here. The roadmap's P7 exit
criteria are behavioural. So what this module produces is a **first measurement**,
which is what the O.1 convention asks for: "targets for E2-E9 will be set from
measured behaviour after P3-P7, not guessed in Phase 0."

What it computes, all from the frozen benchmark and from persisted rows:

1. **Matrix agreement** - a correctness check over all nine cells of architecture
   I.3. The only acceptable value is 1.00; anything else is a defect, and
   :func:`matrix_scores` says so rather than reporting a score.
2. **Scope-guard figures** (``FR-RSK-011``) - precision, recall and, the honest
   half, the false-alarm rate on legitimate project risks that mention credit
   systems.
3. **Adversarial outcomes** - each attack replayed through the real schema, the
   real validator and the real matrix.
4. **Pipeline figures** from a live run - citation resolution, G8 routing
   correctness, and the reviewer load (how many blocking tasks a run raises).

The benchmark is read only through its manifest, and every file's canonical
sha256 must match before anything is computed (R.3, D16). Nothing here calls a
model, and nothing here reads ``data/gold/`` on its own: the caller names the
benchmark.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    AuditEventType,
    Gate,
    RiskImpact,
    RiskLikelihood,
    RiskScope,
    RiskSeverity,
    RiskStatus,
)
from reqpilot.domain.errors import CitationError, GoldSetIntegrityError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.integrity import file_canonical_sha256
from reqpilot.domain.models.approval import ApprovalTask
from reqpilot.domain.models.audit import AuditEvent
from reqpilot.domain.models.risk import Risk
from reqpilot.domain.policy import Actor
from reqpilot.domain.risk.matrix import compute_severity
from reqpilot.domain.risk.scope import find_out_of_scope
from reqpilot.rules.risk import RiskRules
from reqpilot.services.knowledge.evidence import EvidenceService

MANIFEST = "manifest.json"

REQUIRED_FILES = (
    "BENCHMARK.md",
    "matrix_cases.jsonl",
    "scope_cases.jsonl",
    "adversarial.jsonl",
)

#: Stated so that no reader has to infer it from an absent key.
NO_TARGET = (
    "No approved numeric target exists for risk analysis (Phase 0 O.1 defines none, and "
    "the P7 roadmap exit criteria are behavioural). These are first measurements, to be "
    "used by the project author to set a target - not thresholds that were met."
)


def _jsonl(path: Path) -> list[dict]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except ValueError as exc:
                raise GoldSetIntegrityError(f"{path.name} line {number}: {exc}") from exc
    return rows


@dataclass(frozen=True)
class MatrixCase:
    case_id: str
    likelihood: RiskLikelihood
    impact: RiskImpact
    expected_severity: RiskSeverity
    expected_gate: str | None


@dataclass(frozen=True)
class ScopeCase:
    case_id: str
    text: str
    #: ``True`` when the text is borrower-level scoring and must be refused.
    out_of_scope: bool
    expected_rule_id: str | None


@dataclass(frozen=True)
class RiskBenchmark:
    name: str
    version: str
    benchmark_id: str
    manifest_sha256: str
    directory: Path
    matrix_cases: tuple[MatrixCase, ...]
    scope_cases: tuple[ScopeCase, ...]
    adversarial: tuple[dict, ...]

    @property
    def out_of_scope_cases(self) -> tuple[ScopeCase, ...]:
        return tuple(c for c in self.scope_cases if c.out_of_scope)

    @property
    def in_scope_cases(self) -> tuple[ScopeCase, ...]:
        return tuple(c for c in self.scope_cases if not c.out_of_scope)


def load_risk_benchmark(directory: Path) -> RiskBenchmark:
    """Load the frozen P7 benchmark, verifying every file against its manifest first."""
    manifest_path = directory / MANIFEST
    if not manifest_path.is_file():
        raise GoldSetIntegrityError(f"{directory} has no {MANIFEST}; a benchmark must be frozen")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files: dict[str, str] = dict(manifest["files"])
    except (ValueError, KeyError, TypeError) as exc:
        raise GoldSetIntegrityError(f"{manifest_path} is malformed: {exc}") from exc

    present = {
        p.relative_to(directory).as_posix()
        for p in directory.rglob("*")
        if p.is_file() and p.name != MANIFEST
    }
    unlisted = sorted(present - set(files))
    if unlisted:
        raise GoldSetIntegrityError(
            f"{directory} contains unlisted file(s): {unlisted}; a frozen benchmark is "
            "exactly what its manifest names"
        )
    for name, expected in sorted(files.items()):
        path = directory / name
        if not path.is_file():
            raise GoldSetIntegrityError(f"{name} is named by the manifest but missing")
        actual = file_canonical_sha256(path)
        if actual != expected:
            raise GoldSetIntegrityError(
                f"{name} does not match its frozen hash ({actual} != {expected}); "
                "a benchmark is never edited - corrections become a new version"
            )
    for name in REQUIRED_FILES:
        if name not in files:
            raise GoldSetIntegrityError(f"the manifest does not name {name}")

    matrix_cases = tuple(
        MatrixCase(
            case_id=str(row["id"]),
            likelihood=RiskLikelihood(str(row["likelihood"])),
            impact=RiskImpact(str(row["impact"])),
            expected_severity=RiskSeverity(str(row["expected_severity"])),
            expected_gate=row.get("expected_gate"),
        )
        for row in _jsonl(directory / "matrix_cases.jsonl")
    )
    scope_cases = tuple(
        ScopeCase(
            case_id=str(row["id"]),
            text=str(row["text"]),
            out_of_scope=str(row["label"]) == "out_of_scope",
            expected_rule_id=row.get("expected_rule_id"),
        )
        for row in _jsonl(directory / "scope_cases.jsonl")
    )
    return RiskBenchmark(
        name=str(manifest["name"]),
        version=str(manifest["version"]),
        benchmark_id=str(manifest["benchmark_id"]),
        manifest_sha256=file_canonical_sha256(manifest_path),
        directory=directory,
        matrix_cases=matrix_cases,
        scope_cases=scope_cases,
        adversarial=tuple(_jsonl(directory / "adversarial.jsonl")),
    )


# ---------------------------------------------------------------------------
# 1. the matrix: a correctness check, not a score
# ---------------------------------------------------------------------------


def matrix_scores(benchmark: RiskBenchmark, rules: RiskRules) -> dict[str, Any]:
    """Agreement between the implementation's matrix and architecture I.3.

    Reported with ``correct`` rather than as a metric: 1.00 is the only
    acceptable value, and anything else is a defect to fix, not a number to
    report as performance.
    """
    severity_hits = 0
    gate_hits = 0
    disagreements: list[dict[str, str]] = []
    for case in benchmark.matrix_cases:
        computation = compute_severity(rules.matrix, case.likelihood, case.impact)
        if computation.severity is case.expected_severity:
            severity_hits += 1
        else:
            disagreements.append(
                {
                    "case": case.case_id,
                    "cell": f"{case.likelihood}x{case.impact}",
                    "expected": str(case.expected_severity),
                    "computed": str(computation.severity),
                }
            )
        expected_gate = case.expected_gate == "G8"
        if computation.requires_gate is expected_gate:
            gate_hits += 1
    total = len(benchmark.matrix_cases)
    return {
        "cells": total,
        "severity_agreement": round(severity_hits / total, 4) if total else 0.0,
        "gate_agreement": round(gate_hits / total, 4) if total else 0.0,
        "matrix_version": rules.matrix.version,
        "disagreements": disagreements,
        "correct": severity_hits == total and gate_hits == total,
        "note": "a correctness check against architecture I.3; 1.00 is the only acceptable value",
    }


# ---------------------------------------------------------------------------
# 2. the scope guard (FR-RSK-011)
# ---------------------------------------------------------------------------


def scope_guard_scores(benchmark: RiskBenchmark) -> dict[str, Any]:
    """Precision, recall, false-alarm rate and rule agreement for the scope guard.

    The false-alarm rate is the figure that matters: a guard that refused
    everything would score 1.00 on recall and be useless in a loan-origination
    project, where legitimate risks mention credit bureaus, fraud controls and
    the word "default".
    """
    true_positive = false_negative = 0
    false_positive = true_negative = 0
    rule_hits = 0
    missed: list[str] = []
    false_alarms: list[dict[str, str]] = []
    for case in benchmark.scope_cases:
        hits = find_out_of_scope(case.text)
        caught = bool(hits)
        if case.out_of_scope:
            if caught:
                true_positive += 1
                if case.expected_rule_id in {h.rule_id for h in hits}:
                    rule_hits += 1
            else:
                false_negative += 1
                missed.append(case.case_id)
        else:
            if caught:
                false_positive += 1
                false_alarms.append(
                    {"case": case.case_id, "rules": ",".join(sorted({h.rule_id for h in hits}))}
                )
            else:
                true_negative += 1

    out_of_scope = len(benchmark.out_of_scope_cases)
    in_scope = len(benchmark.in_scope_cases)
    caught_total = true_positive + false_positive
    return {
        "out_of_scope_cases": out_of_scope,
        "in_scope_cases": in_scope,
        "tp": true_positive,
        "fn": false_negative,
        "fp": false_positive,
        "tn": true_negative,
        "precision": round(true_positive / caught_total, 4) if caught_total else None,
        "recall": round(true_positive / out_of_scope, 4) if out_of_scope else None,
        "false_alarm_rate": round(false_positive / in_scope, 4) if in_scope else None,
        "accuracy": round((true_positive + true_negative) / len(benchmark.scope_cases), 4)
        if benchmark.scope_cases
        else None,
        "rule_agreement": round(rule_hits / out_of_scope, 4) if out_of_scope else None,
        "missed_case_ids": missed,
        "false_alarm_cases": false_alarms,
        "scope_rules_version": _scope_version(),
    }


def _scope_version() -> str:
    from reqpilot.domain.risk.scope import SCOPE_RULES_VERSION

    return SCOPE_RULES_VERSION


# ---------------------------------------------------------------------------
# 3. what a live run produced
# ---------------------------------------------------------------------------


@dataclass
class RunObservation:
    """What one risk run recorded, read from persisted rows."""

    risks: list[dict[str, Any]] = field(default_factory=list)
    citations_total: int = 0
    citations_resolved: int = 0
    gate_tasks: list[dict[str, Any]] = field(default_factory=list)
    requirements: int = 0
    dropped: int = 0
    out_of_scope: int = 0
    audit_events: list[str] = field(default_factory=list)


def observe(
    session: Session, actor: Actor, project_id: ProjectId, *, requirements: int
) -> RunObservation:
    """Read what the run recorded. Persisted rows only; no model, no re-derivation."""
    observation = RunObservation(requirements=requirements)
    evidence = EvidenceService(session, actor)
    risks = list(
        session.scalars(select(Risk).where(Risk.project_id == project_id).order_by(Risk.created_at))
    )
    for risk in risks:
        observation.risks.append(
            {
                "id": str(risk.id),
                "scope": str(risk.scope),
                "category": str(risk.category),
                "likelihood": str(risk.likelihood),
                "impact": str(risk.impact),
                "severity": str(risk.severity),
                "matrix_version": risk.matrix_version,
                "status": str(risk.status),
                "evidence_count": risk.evidence_count,
                "has_gate_task": risk.approval_task_id is not None,
                "requirement_version_id": str(risk.requirement_version_id)
                if risk.requirement_version_id
                else None,
            }
        )
        for citation in risk.citations or []:
            observation.citations_total += 1
            try:
                evidence.describe(project_id, uuid.UUID(str(citation["evidence_id"])))
            except (CitationError, KeyError, ValueError):
                continue
            observation.citations_resolved += 1

    for task in session.scalars(select(ApprovalTask).where(ApprovalTask.project_id == project_id)):
        observation.gate_tasks.append(
            {
                "gate": str(task.gate),
                "subject_type": task.subject_type,
                "subject_id": str(task.subject_id),
                "blocking": bool(task.blocking),
                "required_role": str(task.required_role),
            }
        )

    for event in session.scalars(select(AuditEvent).where(AuditEvent.project_id == project_id)):
        observation.audit_events.append(str(event.event_type))
        if event.event_type is AuditEventType.RISK_DROPPED:
            observation.dropped += 1
            if event.payload.get("scope_guard_refusal"):
                observation.out_of_scope += 1
    return observation


def pipeline_scores(observation: RunObservation, rules: RiskRules) -> dict[str, Any]:
    """The supplementary figures of one run. No targets; see :data:`NO_TARGET`."""
    risks = observation.risks
    matrix = rules.matrix
    severity_correct = sum(
        1
        for r in risks
        if matrix.severity(RiskLikelihood(r["likelihood"]), RiskImpact(r["impact"]))
        is RiskSeverity(r["severity"])
    )
    high = [r for r in risks if r["severity"] == RiskSeverity.HIGH.value]
    high_gated = [r for r in high if r["has_gate_task"]]
    not_high_gated = [
        r for r in risks if r["severity"] != RiskSeverity.HIGH.value and r["has_gate_task"]
    ]
    g8 = [t for t in observation.gate_tasks if t["gate"] == str(Gate.G8_HIGH_SEVERITY_RISK)]
    blocking = [t for t in observation.gate_tasks if t["blocking"]]
    by_severity: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for risk in risks:
        by_severity[risk["severity"]] = by_severity.get(risk["severity"], 0) + 1
        by_category[risk["category"]] = by_category.get(risk["category"], 0) + 1
    return {
        "risks_recorded": len(risks),
        "requirement_level": sum(1 for r in risks if r["scope"] == RiskScope.REQUIREMENT.value),
        "project_level": sum(1 for r in risks if r["scope"] == RiskScope.PROJECT.value),
        "by_severity": by_severity,
        "by_category": by_category,
        "severity_from_matrix": {
            "correct": severity_correct,
            "total": len(risks),
            "rate": round(severity_correct / len(risks), 4) if risks else None,
        },
        "citation_resolution": {
            "resolved": observation.citations_resolved,
            "total": observation.citations_total,
            "rate": round(observation.citations_resolved / observation.citations_total, 4)
            if observation.citations_total
            else None,
        },
        "every_risk_cites_evidence": all(r["evidence_count"] >= 1 for r in risks),
        "g8_routing": {
            "high_risks": len(high),
            "high_with_gate": len(high_gated),
            "non_high_with_gate": len(not_high_gated),
            "g8_tasks": len(g8),
            "correct": len(high_gated) == len(high) and not not_high_gated,
        },
        "reviewer_load": {
            "blocking_tasks": len(blocking),
            "requirements": observation.requirements,
            "blocking_per_requirement": round(len(blocking) / observation.requirements, 2)
            if observation.requirements
            else None,
        },
        "risks_dropped": observation.dropped,
        "scope_guard_refusals": observation.out_of_scope,
        "high_risks_under_review": sum(
            1 for r in high if r["status"] == RiskStatus.UNDER_REVIEW.value
        ),
    }


# ---------------------------------------------------------------------------
# 4. the report
# ---------------------------------------------------------------------------


def evaluation_report(
    benchmark: RiskBenchmark,
    rules: RiskRules,
    *,
    mode: str,
    pipeline: dict[str, Any] | None = None,
    adversarial: dict[str, Any] | None = None,
    model_calls: int = 0,
    all_calls_real_model: bool = False,
    notes: Iterable[str] = (),
) -> dict[str, Any]:
    """Assemble one run's report. Every figure is a first measurement."""
    return {
        "benchmark": benchmark.benchmark_id,
        "manifest_sha256": benchmark.manifest_sha256,
        "mode": mode,
        "targets": {},
        "targets_note": NO_TARGET,
        "matrix": matrix_scores(benchmark, rules),
        "scope_guard": scope_guard_scores(benchmark),
        "adversarial": adversarial or {},
        "pipeline": pipeline or {},
        "model_calls": model_calls,
        "all_calls_real_model": all_calls_real_model,
        "notes": list(notes),
        "caveat": (
            "Synthetic benchmark written after the implementation by the same AI author, not "
            "reviewed by the project author, not independently validated. No numeric target is "
            "set or claimed. The scope-guard figures are optimistic: the cases were written by "
            "the author of the patterns they test. Nothing here measures whether the right "
            "risks were identified."
        ),
    }
