"""E5 (compliance / control mapping coverage) and the P6 supplementary figures.

The approved definition (Phase 0 O.1): **E5** is the fraction of expert-identified
potentially applicable controls that the system maps or flags as a gap. The P6
brief's bar is E5 >= 0.75. The counting protocol is fixed in the benchmark's
``BENCHMARK.md`` *before* evaluation, and implemented here without discretion:

1. **The benchmark is frozen.** It is read only through its manifest, and every
   file's canonical sha256 must match before anything is computed (R.3, D16).
2. **E5** = |{reference controls whose checklist key is mapped or a gap}| /
   |reference controls|. *Mapped* keys are those of the run's recorded,
   non-rejected mappings; *gap* keys those of the run's gaps. A reference control
   with no checklist equivalent is never covered.
3. **Supplementary figures** have no targets and are never called E5: citation
   resolution, mapping and gap precision/recall, the validation layer's rejection
   rates, the language detector, G2/G3 routing correctness, family precision/recall.

Everything is read from persisted rows (and the audit trail) through the
project-scoped repositories. Nothing here reads ``data/gold/`` on its own: the
caller names the benchmark. Nothing here calls a model.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from reqpilot.domain.compliance.language import find_prohibited
from reqpilot.domain.compliance.risk import rank
from reqpilot.domain.enums import (
    COVERING_RELATIONSHIPS,
    AuditEventType,
    ComplianceMappingStatus,
    SecurityRiskLevel,
)
from reqpilot.domain.errors import CitationError, GoldSetIntegrityError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.integrity import file_canonical_sha256
from reqpilot.domain.models.approval import ApprovalTask
from reqpilot.domain.models.audit import AuditEvent
from reqpilot.domain.models.compliance import (
    ComplianceGap,
    ComplianceMapping,
    SecurityPrivacyFinding,
)
from reqpilot.domain.policy import Actor
from reqpilot.services.knowledge.evidence import EvidenceService

MANIFEST = "manifest.json"
#: The P6 brief's bar for E5 (Phase 0 set no E5 target; O.1 sets it from measurement).
E5_BAR = 0.75

REQUIRED_FILES = (
    "BENCHMARK.md",
    "kb_manifest.yaml",
    "requirements.jsonl",
    "reference_controls.jsonl",
    "expected_mappings.jsonl",
    "expected_gaps.jsonl",
    "security_privacy.jsonl",
    "adversarial.jsonl",
    "language_cases.jsonl",
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
class BenchRequirement:
    requirement_id: str
    statement: str
    category: str


@dataclass(frozen=True)
class ReferenceControl:
    reference_id: str
    title: str
    clause: str
    checklist_key: str | None
    high_impact: bool


@dataclass(frozen=True)
class ComplianceBenchmark:
    name: str
    version: str
    benchmark_id: str
    manifest_sha256: str
    directory: Path
    requirements: tuple[BenchRequirement, ...]
    references: tuple[ReferenceControl, ...]
    #: (requirement id, checklist control key) -> supporting clause (item key)
    expected_mappings: Mapping[tuple[str, str], str]
    expected_gaps: frozenset[str]
    #: (requirement id, family) -> expect G3
    security_pairs: Mapping[tuple[str, str], bool]
    adversarial: tuple[dict, ...]
    language_cases: tuple[tuple[str, str, bool], ...]

    @property
    def kb_manifest(self) -> Path:
        return self.directory / "kb_manifest.yaml"

    @property
    def high_impact_keys(self) -> frozenset[str]:
        return frozenset(
            r.checklist_key for r in self.references if r.checklist_key and r.high_impact
        )


def load_compliance_benchmark(directory: Path) -> ComplianceBenchmark:
    """Load a frozen P6 benchmark, verifying every file against its manifest first."""
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
    if present != set(files):
        raise GoldSetIntegrityError(
            f"the benchmark's files differ from its manifest: unlisted "
            f"{sorted(present - set(files))}, missing {sorted(set(files) - present)}"
        )
    for relative, expected in sorted(files.items()):
        if file_canonical_sha256(directory / relative) != expected:
            raise GoldSetIntegrityError(
                f"{relative} does not match its frozen hash; a benchmark is never edited in "
                "place - corrections are a new version"
            )
    for required in REQUIRED_FILES:
        if required not in files:
            raise GoldSetIntegrityError(f"the benchmark has no {required}")

    requirements = tuple(
        BenchRequirement(str(r["id"]), str(r["statement"]), str(r["category"]))
        for r in _jsonl(directory / "requirements.jsonl")
    )
    references = tuple(
        ReferenceControl(
            str(r["id"]),
            str(r["title"]),
            str(r["clause"]),
            r.get("checklist_key"),
            bool(r["high_impact"]),
        )
        for r in _jsonl(directory / "reference_controls.jsonl")
    )
    return ComplianceBenchmark(
        name=str(manifest["name"]),
        version=str(manifest["version"]),
        benchmark_id=str(manifest["benchmark_id"]),
        manifest_sha256=file_canonical_sha256(manifest_path),
        directory=directory,
        requirements=requirements,
        references=references,
        expected_mappings={
            (str(r["requirement"]), str(r["control_key"])): str(r["clause"])
            for r in _jsonl(directory / "expected_mappings.jsonl")
        },
        expected_gaps=frozenset(
            str(r["control_key"]) for r in _jsonl(directory / "expected_gaps.jsonl")
        ),
        security_pairs={
            (str(r["requirement"]), str(r["family"])): bool(r["expect_g3"])
            for r in _jsonl(directory / "security_privacy.jsonl")
        },
        adversarial=tuple(_jsonl(directory / "adversarial.jsonl")),
        language_cases=tuple(
            (str(r["id"]), str(r["text"]), bool(r["prohibited"]))
            for r in _jsonl(directory / "language_cases.jsonl")
        ),
    )


def prf(tp: int, fp: int, fn: int) -> dict[str, Any]:
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": _round(precision),
        "recall": _round(recall),
        "f1": _round(f1),
    }


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def _ratio(numerator: int, denominator: int) -> float | None:
    return _round(numerator / denominator) if denominator else None


# ---------------------------------------------------------------------------
# What a run recorded
# ---------------------------------------------------------------------------


@dataclass
class RunObservation:
    """The persisted outcome of one P6 run over the benchmark project."""

    #: (requirement id, control key) of recorded, non-rejected, covering mappings.
    covering: set[tuple[str, str]] = field(default_factory=set)
    #: every recorded mapping: (requirement id, key, high impact, has G2 task)
    mappings: list[tuple[str, str, bool, bool]] = field(default_factory=list)
    gap_keys: set[str] = field(default_factory=set)
    citations_total: int = 0
    citations_resolved: int = 0
    #: (requirement id, family, authoritative level, floor, proposed, detected_by, has G3 task)
    findings: list[tuple[str, str, str, str, str | None, str, bool]] = field(default_factory=list)
    #: (requirement id, reason, control key or family) of every dropped claim
    drops: list[tuple[str, str, str | None]] = field(default_factory=list)
    gate_tasks: int = 0


def observe(
    session: Session,
    actor: Actor,
    project_id: ProjectId,
    run_id: uuid.UUID,
    requirement_of: Mapping[uuid.UUID, str],
) -> RunObservation:
    """Read what the run persisted, re-resolving every citation against the run's evidence."""
    evidence = EvidenceService(session, actor)
    allowed = evidence.evidence_ids_for_run(project_id, run_id)
    obs = RunObservation()
    tasks = {
        t.id: t
        for t in session.scalars(select(ApprovalTask).where(ApprovalTask.project_id == project_id))
    }
    for mapping in session.scalars(
        select(ComplianceMapping).where(
            ComplianceMapping.project_id == project_id, ComplianceMapping.graph_run_id == run_id
        )
    ):
        req = requirement_of[mapping.requirement_version_id]
        has_task = mapping.approval_task_id is not None and mapping.approval_task_id in tasks
        obs.mappings.append((req, mapping.control_key, mapping.is_high_impact, has_task))
        if (
            mapping.status is not ComplianceMappingStatus.REJECTED
            and mapping.relationship in COVERING_RELATIONSHIPS
        ):
            obs.covering.add((req, mapping.control_key))
        for citation in mapping.citations:
            obs.citations_total += 1
            try:
                evidence.resolve_citation(
                    project_id,
                    uuid.UUID(str(citation["evidence_id"])),
                    allowed_evidence_ids=allowed,
                )
                obs.citations_resolved += 1
            except (CitationError, KeyError, ValueError):
                pass
    obs.gap_keys = {
        g.control_key
        for g in session.scalars(
            select(ComplianceGap).where(
                ComplianceGap.project_id == project_id, ComplianceGap.graph_run_id == run_id
            )
        )
    }
    for finding in session.scalars(
        select(SecurityPrivacyFinding).where(
            SecurityPrivacyFinding.project_id == project_id,
            SecurityPrivacyFinding.graph_run_id == run_id,
        )
    ):
        obs.findings.append(
            (
                requirement_of[finding.requirement_version_id],
                finding.family.value,
                finding.risk_level.value,
                finding.catalogue_floor.value,
                finding.proposed_risk_level,
                finding.detected_by.value,
                finding.approval_task_id is not None and finding.approval_task_id in tasks,
            )
        )
    for event in session.scalars(
        select(AuditEvent).where(
            AuditEvent.project_id == project_id,
            AuditEvent.graph_run_id == run_id,
            AuditEvent.event_type.in_(
                [AuditEventType.COMPLIANCE_CLAIM_DROPPED, AuditEventType.SECURITY_FINDING_DROPPED]
            ),
        )
    ):
        version_id = uuid.UUID(str(event.subject_id))
        obs.drops.append(
            (
                requirement_of.get(version_id, "?"),
                str(event.payload.get("reason")),
                event.payload.get("control_key") or event.payload.get("family"),
            )
        )
    obs.gate_tasks = sum(
        1
        for t in tasks.values()
        if t.gate.value in ("G2", "G3") and t.subject_type != "requirement_version"
    )
    return obs


# ---------------------------------------------------------------------------
# Scores
# ---------------------------------------------------------------------------


def compute_e5(benchmark: ComplianceBenchmark, obs: RunObservation) -> dict[str, Any]:
    mapped = {key for _req, key in obs.covering}
    flagged = mapped | obs.gap_keys
    covered = [r for r in benchmark.references if r.checklist_key and r.checklist_key in flagged]
    missed = [r.reference_id for r in benchmark.references if r not in covered]
    value = len(covered) / len(benchmark.references)
    return {
        "value": round(value, 4),
        "covered": len(covered),
        "reference_controls": len(benchmark.references),
        "covered_by_mapping": sum(1 for r in covered if r.checklist_key in mapped),
        "covered_by_gap_only": sum(1 for r in covered if r.checklist_key not in mapped),
        "missed_reference_ids": missed,
        "bar": E5_BAR,
        "meets_bar": value >= E5_BAR,
    }


def compute_supplementary(benchmark: ComplianceBenchmark, obs: RunObservation) -> dict[str, Any]:
    expected = set(benchmark.expected_mappings)
    mapping_prf = prf(
        len(obs.covering & expected), len(obs.covering - expected), len(expected - obs.covering)
    )
    gap_prf = prf(
        len(obs.gap_keys & benchmark.expected_gaps),
        len(obs.gap_keys - benchmark.expected_gaps),
        len(benchmark.expected_gaps - obs.gap_keys),
    )
    # G2: a task exists exactly for high-impact mappings.
    g2_correct = sum(1 for _r, _k, high, task in obs.mappings if high == task)
    high_expected = {pair for pair in expected if pair[1] in benchmark.high_impact_keys}
    recorded_high = {(r, k) for r, k, high, task in obs.mappings if high and task}
    # G3: a task exists exactly for HIGH findings, and no level is below its floor.
    g3_correct = sum(
        1
        for _r, _f, level, floor, _p, _d, task in obs.findings
        if (level == "high") == task
        and rank(SecurityRiskLevel(level)) >= rank(SecurityRiskLevel(floor))
    )
    found_pairs = {(r, f) for r, f, *_rest in obs.findings}
    g3_pairs = {
        (r, f) for r, f, level, _fl, _p, _d, task in obs.findings if level == "high" and task
    }
    expected_pairs = set(benchmark.security_pairs)
    expected_g3 = {pair for pair, g3 in benchmark.security_pairs.items() if g3}
    return {
        "citation_resolution": {
            "resolved": obs.citations_resolved,
            "total": obs.citations_total,
            "rate": _ratio(obs.citations_resolved, obs.citations_total),
        },
        "mappings": {
            **mapping_prf,
            "recorded": len(obs.mappings),
            "false_positives": sorted(f"{r} {k}" for r, k in obs.covering - expected),
            "false_negatives": sorted(f"{r} {k}" for r, k in expected - obs.covering),
        },
        "gaps": {
            **gap_prf,
            "recorded": len(obs.gap_keys),
            "false_positives": sorted(obs.gap_keys - benchmark.expected_gaps),
            "false_negatives": sorted(benchmark.expected_gaps - obs.gap_keys),
        },
        "g2_routing": {
            "correct": g2_correct,
            "mappings": len(obs.mappings),
            "rate": _ratio(g2_correct, len(obs.mappings)),
            "expected_high_impact_mappings": len(high_expected),
            "expected_high_impact_routed": len(high_expected & recorded_high),
        },
        "g3_routing": {
            "correct": g3_correct,
            "findings": len(obs.findings),
            "rate": _ratio(g3_correct, len(obs.findings)),
            "expected_g3_pairs": len(expected_g3),
            "expected_g3_routed": len(expected_g3 & g3_pairs),
            "recall": _ratio(len(expected_g3 & g3_pairs), len(expected_g3)),
        },
        "families": {
            **prf(
                len(found_pairs & expected_pairs),
                len(found_pairs - expected_pairs),
                len(expected_pairs - found_pairs),
            ),
            "recorded": len(found_pairs),
            "false_positives": sorted(f"{r} {f}" for r, f in found_pairs - expected_pairs),
            "false_negatives": sorted(f"{r} {f}" for r, f in expected_pairs - found_pairs),
        },
        "claims_dropped": len(obs.drops),
        "drop_reasons": _count(reason for _r, reason, _k in obs.drops),
        "gate_tasks": obs.gate_tasks,
    }


def _count(values: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items()))


def language_detector_scores(benchmark: ComplianceBenchmark) -> dict[str, Any]:
    """The prohibited-assertion detector against the labelled language cases."""
    tp = fp = fn = tn = 0
    misses: list[str] = []
    for case_id, text, prohibited in benchmark.language_cases:
        flagged = bool(find_prohibited(text))
        if flagged and prohibited:
            tp += 1
        elif flagged:
            fp += 1
            misses.append(f"{case_id}: false alarm")
        elif prohibited:
            fn += 1
            misses.append(f"{case_id}: missed")
        else:
            tn += 1
    return {
        **prf(tp, fp, fn),
        "tn": tn,
        "accuracy": _ratio(tp + tn, tp + fp + fn + tn),
        "errors": misses,
    }


#: Attack families counted by each rejection rate (BENCHMARK.md).
UNSUPPORTED_ATTACKS = frozenset({"fabricated_citation", "foreign_citation", "uncited"})
LANGUAGE_ATTACKS = frozenset({"prohibited_language", "authority_claim", "injection_obeyed"})


def adversarial_scores(
    benchmark: ComplianceBenchmark,
    obs: RunObservation,
    *,
    delivered: Mapping[str, bool],
    schema_refused: Mapping[str, bool],
) -> dict[str, Any]:
    """Outcome of each replayed attack against its expected outcome (adversarial mode)."""
    by_req_key = {(r, k): reason for r, reason, k in obs.drops}
    findings = {(r, f): (level, task, det) for r, f, level, _fl, _p, det, task in obs.findings}
    cases = []
    for case in benchmark.adversarial:
        case_id = str(case["id"])
        req = str(case["requirement"])
        expected = str(case["expected"])
        if not delivered.get(case_id, False):
            cases.append({"id": case_id, "attack": case["attack"], "outcome": "not_delivered"})
            continue
        if case["call"] == "compliance":
            key = str(case["control_key"]).strip().upper()
            reason = by_req_key.get((req, key))
            recorded = (req, key) in obs.covering
            if expected == "dropped":
                ok = reason is not None and not recorded
            else:
                ok = reason == expected.split(":", 1)[1] and not recorded
            cases.append(
                {"id": case_id, "attack": case["attack"], "outcome": reason or "recorded", "ok": ok}
            )
            continue
        family = str(case["family"])
        level, task, detector = findings.get((req, family), (None, False, None))
        if expected == "schema_refused":
            ok = bool(schema_refused.get(case_id)) and detector != "agent"
            outcome = f"schema_refused={schema_refused.get(case_id)} detector={detector}"
        else:
            _risk, want_level, want_gate = expected.split(":")
            ok = level == want_level and task == (want_gate == "g3")
            outcome = f"risk={level} g3={task}"
        cases.append({"id": case_id, "attack": case["attack"], "outcome": outcome, "ok": ok})

    def rate(attacks: frozenset[str]) -> dict[str, Any]:
        rows = [c for c in cases if c["attack"] in attacks and "ok" in c]
        good = sum(1 for c in rows if c["ok"])
        return {"rejected": good, "delivered": len(rows), "rate": _ratio(good, len(rows))}

    return {
        "unsupported_citation_rejection": rate(UNSUPPORTED_ATTACKS),
        "prohibited_language_rejection": rate(LANGUAGE_ATTACKS),
        "all_cases_as_expected": _ratio(
            sum(1 for c in cases if c.get("ok")), sum(1 for c in cases if "ok" in c)
        ),
        "cases": cases,
    }


def evaluation_report(
    benchmark: ComplianceBenchmark,
    *,
    mode: str,
    e5: Mapping[str, Any],
    supplementary: Mapping[str, Any],
    language: Mapping[str, Any],
    adversarial: Mapping[str, Any] | None,
    model_calls: Mapping[str, int],
    all_calls_real_model: bool,
    notes: list[str],
) -> dict[str, Any]:
    return {
        "benchmark": benchmark.benchmark_id,
        "manifest_sha256": benchmark.manifest_sha256,
        "mode": mode,
        "e5": dict(e5),
        "supplementary": dict(supplementary),
        "language_detector": dict(language),
        "adversarial": dict(adversarial) if adversarial is not None else None,
        "model_calls": dict(model_calls),
        "all_calls_real_model": all_calls_real_model,
        "notes": notes,
        "caveat": (
            "Synthetic benchmark written after the implementation by the same AI author, not "
            "reviewed by the project author, not independently validated. E5 is structurally "
            "favourable (gaps are expected - covered). No real-world regulatory accuracy is "
            "claimed."
        ),
    }
