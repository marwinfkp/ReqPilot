"""The P8 E6 evaluation (P8-TRACE-SYNTHETIC-v1).

**E6 has no approved target, and this module invents none.** Phase 0 O.1 defines
E6 - "fraction of requirements with a complete chain per ``FR-TRC-001``,
including risk links", computed by the system (``FR-TRC-003``) - and says targets
for E2-E9 are set from measured behaviour, not guessed. Architecture N.3 makes
"complete" exact; :mod:`reqpilot.services.traceability.coverage` implements it.

What this module checks, from the frozen benchmark only:

1. **Definition agreement** - each labelled case (a version's state and the edges
   around it) is turned into typed trace links over an in-memory graph and
   evaluated by :func:`version_coverage`, the exact function the product uses.
   The missing N.3 elements must equal the label. The only acceptable agreement
   is 100%; a disagreement is reported case by case, never averaged away.
2. **Aggregate agreement** - scopes of definition cases, checked against the
   expected numerator and denominator (and ``None`` for an empty scope) through
   :class:`CoverageReport`'s own ``e6``.

The scenario measurement (a live synthetic project) is run by
``scripts/run_p8_eval.py``, which passes its coverage reports to
:func:`evaluation_report`. The benchmark is read only through its manifest, and
every file's canonical sha256 must match before anything is computed (R.3, D16).
Nothing here calls a model, persists a row, or reads ``data/gold/`` on its own.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reqpilot.domain.errors import GoldSetIntegrityError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.integrity import file_canonical_sha256
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.traceability import TraceabilityLink
from reqpilot.domain.traceability import TraceLinkType, TraceNodeType, is_allowed
from reqpilot.services.traceability.coverage import (
    E6_DEFINITION_VERSION,
    CoverageReport,
    VersionCoverage,
    version_coverage,
)
from reqpilot.services.traceability.graph import TraceGraph

N = TraceNodeType
L = TraceLinkType

MANIFEST = "manifest.json"
REQUIRED_FILES = (
    "BENCHMARK.md",
    "definition_cases.jsonl",
    "aggregate_cases.jsonl",
    "scenario.json",
)

NO_TARGET = (
    "No approved numeric target exists for E6 (Phase 0 O.1 defines the metric and says "
    "targets for E2-E9 are set from measured behaviour, not guessed). The scenario figures "
    "are first measurements for the project author to set a target from - not thresholds "
    "that were met."
)

#: The five N.3 elements, keyed by the prefix of ``VersionCoverage.missing``.
ELEMENTS: dict[str, str] = {
    "SOURCES": "SOURCES",
    "CLASSIFIED_AS": "CLASSIFIED_AS",
    "HAS_RISK": "RISK_OUTCOME",
    "APPROVED_BY": "APPROVED_BY",
    "RENDERED_IN": "RENDERED_IN",
}

_NS = uuid.UUID("5b1c1a0e-8b0f-4c43-9c49-50385f7e6a01")  # the benchmark's id namespace
_PROJECT = ProjectId(uuid.uuid5(_NS, "project"))
_B1 = uuid.uuid5(_NS, "baseline:B1")
_B2 = uuid.uuid5(_NS, "baseline:B2")


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except ValueError as exc:
                raise GoldSetIntegrityError(f"{path.name} line {number}: {exc}") from exc
    return rows


@dataclass(frozen=True)
class DefinitionCase:
    case_id: str
    state: RequirementState
    edges: tuple[str, ...]
    expected_missing: frozenset[str]
    expected_fully_traced: bool


@dataclass(frozen=True)
class AggregateCase:
    case_id: str
    members: tuple[str, ...]
    expected_fully_traced: int
    expected_total: int


@dataclass(frozen=True)
class TraceBenchmark:
    name: str
    version: str
    benchmark_id: str
    manifest_sha256: str
    directory: Path
    definitions: tuple[DefinitionCase, ...]
    aggregates: tuple[AggregateCase, ...]
    scenario: dict[str, Any]


def _verify(directory: Path, repo_root: Path | None) -> dict[str, Any]:
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
    missing = [name for name in REQUIRED_FILES if name not in files]
    if missing:
        raise GoldSetIntegrityError(f"the manifest does not name {missing}")
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
    if repo_root is not None:
        for name, expected in sorted(dict(manifest.get("external_fixtures", {})).items()):
            path = repo_root / name
            if not path.is_file() or file_canonical_sha256(path) != expected:
                raise GoldSetIntegrityError(
                    f"the scenario fixture {name} does not match its frozen hash; the "
                    "scenario would not be the one the benchmark froze"
                )
    return dict(manifest)


def load_trace_benchmark(directory: Path, *, repo_root: Path | None = None) -> TraceBenchmark:
    """Load the frozen P8 benchmark, verifying every file against its manifest first.

    With ``repo_root`` the external scenario fixture is verified too (the script
    does this before running the scenario).
    """
    manifest = _verify(directory, repo_root)
    definitions = []
    for row in _jsonl(directory / "definition_cases.jsonl"):
        try:
            case = DefinitionCase(
                case_id=str(row["case_id"]),
                state=RequirementState(row["state"]),
                edges=tuple(row["edges"]),
                expected_missing=frozenset(row["expected_missing"]),
                expected_fully_traced=bool(row["expected_fully_traced"]),
            )
        except (KeyError, ValueError) as exc:
            raise GoldSetIntegrityError(f"definition case {row!r} is malformed: {exc}") from exc
        unknown = case.expected_missing - set(ELEMENTS.values())
        if unknown or case.expected_fully_traced is bool(case.expected_missing):
            raise GoldSetIntegrityError(f"definition case {case.case_id} is inconsistent")
        definitions.append(case)
    ids = {c.case_id for c in definitions}
    aggregates = []
    for row in _jsonl(directory / "aggregate_cases.jsonl"):
        aggregate = AggregateCase(
            case_id=str(row["case_id"]),
            members=tuple(row["members"]),
            expected_fully_traced=int(row["expected_fully_traced"]),
            expected_total=int(row["expected_total"]),
        )
        if set(aggregate.members) - ids:
            raise GoldSetIntegrityError(f"aggregate {aggregate.case_id} names unknown cases")
        aggregates.append(aggregate)
    return TraceBenchmark(
        name=str(manifest.get("name")),
        version=str(manifest.get("version")),
        benchmark_id=str(manifest.get("benchmark_id")),
        manifest_sha256=file_canonical_sha256(directory / MANIFEST),
        directory=directory,
        definitions=tuple(definitions),
        aggregates=tuple(aggregates),
        scenario=json.loads((directory / "scenario.json").read_text(encoding="utf-8")),
    )


# ---------------------------------------------------------------------------
# turning a case into typed links
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CaseVersion:
    """The three attributes :func:`version_coverage` reads from a version."""

    id: uuid.UUID
    state: RequirementState
    version_no: int = 1


def _edge(
    token: str, vid: uuid.UUID, case_id: str
) -> list[tuple[TraceNodeType, str, TraceLinkType, TraceNodeType, str]]:
    def node(kind: str) -> str:
        return str(uuid.uuid5(_NS, f"{case_id}:{token}:{kind}"))

    v = str(vid)
    table: dict[str, list[tuple[TraceNodeType, str, TraceLinkType, TraceNodeType, str]]] = {
        "utterance_sources": [(N.UTTERANCE, node("u"), L.SOURCES, N.REQUIREMENT_VERSION, v)],
        "chunk_sources": [(N.SOURCE_CHUNK, node("c"), L.SOURCES, N.REQUIREMENT_VERSION, v)],
        "document_sources": [(N.SOURCE_DOCUMENT, node("d"), L.SOURCES, N.REQUIREMENT_VERSION, v)],
        "stakeholder_stated": [(N.STAKEHOLDER, node("s"), L.STATED, N.UTTERANCE, node("u"))],
        "classified": [(N.REQUIREMENT_VERSION, v, L.CLASSIFIED_AS, N.CLASSIFICATION, node("k"))],
        "has_risk": [(N.REQUIREMENT_VERSION, v, L.HAS_RISK, N.RISK, node("r"))],
        "risk_assessed": [(N.REQUIREMENT_VERSION, v, L.RISK_ASSESSED_BY, N.AGENT_RUN, node("a"))],
        "has_mapping": [(N.REQUIREMENT_VERSION, v, L.HAS_MAPPING, N.COMPLIANCE_MAPPING, node("m"))],
        "has_security_finding": [
            (
                N.REQUIREMENT_VERSION,
                v,
                L.HAS_SECURITY_FINDING,
                N.SECURITY_PRIVACY_FINDING,
                node("f"),
            )
        ],
        "has_finding": [(N.REQUIREMENT_VERSION, v, L.HAS_FINDING, N.QUALITY_FINDING, node("q"))],
        "has_conflict": [(N.REQUIREMENT_VERSION, v, L.HAS_CONFLICT, N.CONFLICT, node("x"))],
        "acceptance": [
            (N.REQUIREMENT_VERSION, v, L.SATISFIED_BY, N.ACCEPTANCE_CRITERION, node("ac"))
        ],
        "approved_by": [
            (N.REQUIREMENT_VERSION, v, L.APPROVED_BY, N.APPROVAL_DECISION, node("dec"))
        ],
        "member_of_B1": [(N.REQUIREMENT_VERSION, v, L.MEMBER_OF, N.BASELINE, str(_B1))],
        "rendered_B1": [(N.BASELINE, str(_B1), L.RENDERED_IN, N.ARTIFACT_VERSION, node("av"))],
        "rendered_B2": [(N.BASELINE, str(_B2), L.RENDERED_IN, N.ARTIFACT_VERSION, node("av"))],
        "cited_by_section": [(N.ARTIFACT_SECTION, node("sec"), L.CITES, N.REQUIREMENT_VERSION, v)],
    }
    if token not in table:
        raise GoldSetIntegrityError(f"case {case_id}: unknown edge token {token!r}")
    return table[token]


def case_graph(case: DefinitionCase) -> tuple[TraceGraph, _CaseVersion]:
    """The in-memory graph a definition case describes. Nothing is persisted."""
    vid = uuid.uuid5(_NS, f"version:{case.case_id}")
    other = uuid.uuid5(_NS, f"other-version:{case.case_id}")
    links = []
    for raw in case.edges:
        token, target = raw, vid
        if raw.startswith("other_version:"):
            token, target = raw.split(":", 1)[1], other
        for from_type, from_id, link_type, to_type, to_id in _edge(token, target, case.case_id):
            if not is_allowed(from_type, link_type, to_type):  # the benchmark obeys N.1 too
                raise GoldSetIntegrityError(f"case {case.case_id}: {raw} is not allowlisted")
            links.append(
                TraceabilityLink(
                    project_id=_PROJECT,
                    from_type=str(from_type),
                    from_id=from_id,
                    link_type=str(link_type),
                    to_type=str(to_type),
                    to_id=to_id,
                    anchor_version_id=target,
                    origin="benchmark",
                )
            )
    return TraceGraph(project_id=_PROJECT, links=links), _CaseVersion(vid, case.state)


def observed_elements(coverage: VersionCoverage) -> frozenset[str]:
    out = set()
    for text in coverage.missing:
        head = text.split(" ", 1)[0]
        if head not in ELEMENTS:  # pragma: no cover - a new element needs a new benchmark
            raise GoldSetIntegrityError(f"unrecognised missing element {text!r}")
        out.add(ELEMENTS[head])
    return frozenset(out)


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------


def evaluate_definitions(
    benchmark: TraceBenchmark,
) -> tuple[dict[str, Any], dict[str, VersionCoverage]]:
    coverages: dict[str, VersionCoverage] = {}
    disagreements = []
    for case in benchmark.definitions:
        graph, version = case_graph(case)
        coverage = version_coverage(graph, case.case_id, version, 0)
        coverages[case.case_id] = coverage
        observed = observed_elements(coverage)
        if observed != case.expected_missing or coverage.fully_traced != (
            case.expected_fully_traced
        ):
            disagreements.append(
                {
                    "case_id": case.case_id,
                    "expected_missing": sorted(case.expected_missing),
                    "observed_missing": sorted(observed),
                }
            )
    total = len(benchmark.definitions)
    agreed = total - len(disagreements)
    return (
        {
            "cases": total,
            "agreed": agreed,
            "agreement": agreed / total if total else None,
            "disagreements": disagreements,
            "verdict": "the implementation computes N.3 as labelled"
            if not disagreements
            else "DEFECT OR LABEL ERROR - see disagreements (no averaging)",
        },
        coverages,
    )


def evaluate_aggregates(
    benchmark: TraceBenchmark, coverages: dict[str, VersionCoverage]
) -> dict[str, Any]:
    rows = []
    for case in benchmark.aggregates:
        report = CoverageReport(
            project_id=_PROJECT,
            scope_kind="benchmark",
            scope_label=case.case_id,
            definition_version=E6_DEFINITION_VERSION,
            versions=tuple(coverages[m] for m in case.members),
            orphan_requirements=(),
            unsourced_statements=(),
            unlinked_risks=(),
            project_level_risks=0,
            findings_without_parent=(),
        )
        expected_e6 = (
            None if case.expected_total == 0 else case.expected_fully_traced / case.expected_total
        )
        rows.append(
            {
                "case_id": case.case_id,
                "expected": [case.expected_fully_traced, case.expected_total, expected_e6],
                "observed": [report.fully_traced, report.total, report.e6],
                "agrees": (report.fully_traced, report.total, report.e6)
                == (case.expected_fully_traced, case.expected_total, expected_e6),
            }
        )
    agreed = sum(1 for r in rows if r["agrees"])
    return {"cases": len(rows), "agreed": agreed, "rows": rows}


def measurement(step: str, report: CoverageReport) -> dict[str, Any]:
    """One scenario measurement: E6, its counts, and every missing element."""
    return {
        "step": step,
        "scope": report.scope_label,
        "e6": report.e6,
        "fully_traced": report.fully_traced,
        "total": report.total,
        "counts": dict(report.counts),
        "not_fully_traced": [
            {"requirement": f"{v.human_id} v{v.version_no}", "missing": list(v.missing)}
            for v in report.versions
            if not v.fully_traced
        ],
        "orphan_requirements": list(report.orphan_requirements),
        "unlinked_risks": len(report.unlinked_risks),
    }


def evaluation_report(
    benchmark: TraceBenchmark,
    definitions: dict[str, Any],
    aggregates: dict[str, Any],
    scenario: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    return {
        "benchmark_id": benchmark.benchmark_id,
        "manifest_sha256": benchmark.manifest_sha256,
        "e6_definition": E6_DEFINITION_VERSION,
        "evaluated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "target": None,
        "target_note": NO_TARGET,
        "definition_agreement": definitions,
        "aggregate_agreement": aggregates,
        "scenario": scenario,
        "validation_status": "synthetic; project-author review pending; not independently "
        "reviewed; not expert validated",
    }


__all__ = [
    "NO_TARGET",
    "AggregateCase",
    "DefinitionCase",
    "TraceBenchmark",
    "case_graph",
    "evaluate_aggregates",
    "evaluate_definitions",
    "evaluation_report",
    "load_trace_benchmark",
    "measurement",
    "observed_elements",
]
