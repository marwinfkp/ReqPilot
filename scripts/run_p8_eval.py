"""Run the P8 E6 evaluation on the frozen P8-TRACE-SYNTHETIC-v1 benchmark.

    python -m scripts.run_p8_eval --benchmark data/gold/p8_traceability_synthetic_v1 \\
        --out docs/evaluation/p8-trace-synthetic-v1

**No numeric target is set or checked.** Phase 0 O.1 defines E6 but no target
(targets for E2-E9 are set from measured behaviour, not guessed). What this
script produces is a first measurement.

1. The benchmark's manifest - and the scenario fixture's recorded hash - are
   verified before anything runs.
2. The labelled definition and aggregate cases are evaluated with the product's
   own ``version_coverage`` and ``CoverageReport.e6`` (no database).
3. The scenario of ``scenario.json`` runs on an in-memory SQLite database with
   the offline synthetic P8 world (``tests/p8_helpers.py``: the scripted model,
   no network, fictional speakers). Nothing is kept: the database is discarded.

The reports record counts and identifiers, not requirement text.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # the scenario fixture lives in tests/
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from reqpilot.domain.enums import (  # noqa: E402
    ApprovalTaskStatus,
    ArtifactType,
    Gate,
    RequirementCategory,
)
from reqpilot.domain.lifecycle import RequirementState  # noqa: E402
from reqpilot.domain.models import Base  # noqa: E402
from reqpilot.services.approval.service import ApprovalService  # noqa: E402
from reqpilot.services.documents import ArtifactService  # noqa: E402
from reqpilot.services.evaluation.traceability_eval import (  # noqa: E402
    TraceBenchmark,
    evaluate_aggregates,
    evaluate_definitions,
    evaluation_report,
    load_trace_benchmark,
    measurement,
)
from reqpilot.services.requirements import (  # noqa: E402
    RequirementContent,
    RequirementService,
)
from reqpilot.services.traceability import TraceGraphSync  # noqa: E402

B1_KEYS = ["L01", "L03", "L05", "L06", "L08"]
EXIT_TYPES = (ArtifactType.SRS, ArtifactType.RTM, ArtifactType.RISK_REGISTER)


def _session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    return Session(bind=engine, expire_on_commit=False)


def run_scenario() -> list[dict[str, Any]]:
    """The five steps of ``scenario.json``, measured after each."""
    from tests.p8_helpers import make_p8_world

    session = _session()
    try:
        world = make_p8_world(session)
        pid = world.project_id
        artefacts = ArtifactService(session, world.analyst)
        sync = TraceGraphSync(session, world.analyst)
        out = []

        sync.sync(pid)
        out.append(measurement("S0", artefacts.coverage(pid, None)))

        b1 = world.govern_and_baseline(B1_KEYS, "B1")
        sync.sync(pid)
        out.append(measurement("S1", artefacts.coverage(pid, b1)))

        for outcome in artefacts.generate_set(pid, b1, EXIT_TYPES):
            if outcome.refused:
                raise RuntimeError(f"S2: {outcome.artifact_type} refused: {outcome.blockers}")
        out.append(measurement("S2", artefacts.coverage(pid, b1)))

        v1 = world.version("L03")
        service = RequirementService(session, world.analyst)
        v2 = service.create_version(
            project_id=pid,
            requirement_id=v1.requirement_id,
            content=RequirementContent(
                statement="The system shall display the current loan status and the next "
                "step to the applicant.",
                category=RequirementCategory.FUNCTIONAL,
                source_refs=tuple(v1.source_refs),
            ),
            change_reason="the applicant also sees the next step (synthetic)",
        )
        for task in world.tasks(
            gate=Gate.G7_APPROVED_REQUIREMENT_CHANGE, status=ApprovalTaskStatus.OPEN
        ):
            world.decide(task)
        for target in (
            RequirementState.EXTRACTED,
            RequirementState.CLASSIFIED,
            RequirementState.ANALYZED,
            RequirementState.VALIDATED,
        ):
            service.transition(project_id=pid, version_id=v2.id, target=target)
        b2 = world.approve_g1(
            ApprovalService(session, world.analyst).submit_versions_for_baseline(
                project_id=pid, version_ids=[v2.id]
            ),
            "B2",
        )
        assert b2 is not None
        sync.sync(pid)
        out.append(measurement("S3", artefacts.coverage(pid, b2)))

        for outcome in artefacts.generate_set(pid, b2, EXIT_TYPES):
            if outcome.refused:
                raise RuntimeError(f"S4: {outcome.artifact_type} refused: {outcome.blockers}")
        out.append(measurement("S4", artefacts.coverage(pid, b2)))
        out.append(measurement("S4-project", artefacts.coverage(pid, None)))
        return out
    finally:
        session.close()


def _pct(value: float | None) -> str:
    return "undefined (empty scope)" if value is None else f"{value:.3f}"


def readme(benchmark: TraceBenchmark, report: dict[str, Any]) -> str:
    definitions = report["definition_agreement"]
    aggregates = report["aggregate_agreement"]
    lines = [
        "# P8 evaluation — E6 on P8-TRACE-SYNTHETIC-v1",
        "",
        "**Synthetic. Not independently reviewed. Not expert validated. Project-author review "
        "pending.**",
        "",
        f"**Benchmark:** `data/gold/p8_traceability_synthetic_v1`, manifest canonical sha256 "
        f"`{benchmark.manifest_sha256}`, verified (with the scenario fixture's hash) "
        "before the run.",
        "",
        "**Harness:** `services/evaluation/traceability_eval.py` and `scripts/run_p8_eval.py`; "
        "the product's own `version_coverage` / `CoverageReport.e6` (definition "
        f"`{report['e6_definition']}`). Scenario on in-memory SQLite, scripted model, no network.",
        "",
        f"**Run:** {report['evaluated_at']}. One run. No code, label or fixture was changed "
        "after it.",
        "",
        "## There is no target here",
        "",
        report["target_note"],
        "",
        "## Part 1 — does P8 compute E6 as defined?",
        "",
        f"* Definition cases: **{definitions['agreed']} / {definitions['cases']}** agree "
        f"({definitions['verdict']}).",
        f"* Aggregate cases: **{aggregates['agreed']} / {aggregates['cases']}** agree "
        "(including the empty scope, E6 undefined, and an all-missing scope, E6 = 0.0).",
        "",
    ]
    if definitions["disagreements"]:
        lines += ["Disagreements:", ""]
        lines += [
            f"* `{d['case_id']}`: expected {d['expected_missing']}, observed "
            f"{d['observed_missing']}"
            for d in definitions["disagreements"]
        ]
        lines.append("")
    lines += [
        "## Part 2 — first measurement on the synthetic scenario",
        "",
        "| Step | Scope | E6 | Fully traced / total | Source | Class. | Risk outcome | "
        "Approved→APPROVED_BY | Baselined→RENDERED_IN |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for m in report["scenario"] or []:
        c = m["counts"]
        lines.append(
            f"| {m['step']} | {m['scope']} | {_pct(m['e6'])} | {m['fully_traced']} / {m['total']} "
            f"| {c.get('with_source')} | {c.get('with_classification')} "
            f"| {c.get('with_risk_outcome')} "
            f"| {c.get('approved_with_approved_by')} / {c.get('approved')} "
            f"| {c.get('baselined_with_rendered_path')} / {c.get('baselined')} |"
        )
    lines += ["", "Versions not fully traced, with the N.3 element(s) they lack:", ""]
    for m in report["scenario"] or []:
        for v in m["not_fully_traced"]:
            lines.append(f"* {m['step']}: {v['requirement']} — {'; '.join(v['missing'])}")
    lines += [
        "",
        "## Reading these numbers",
        "",
        "* Part 1 checks internal consistency between the implementation and the assistant's "
        "reading of N.3 — both written by the same assistant. It does not show the reading is "
        "right; the review sheet asks the project author to decide that.",
        "* Part 2 describes how completely **this** synthetic run is traced. It is not an "
        "estimate for real projects, and E6 measures the presence of typed links, not whether a "
        "source genuinely supports a requirement (that is E4's manual audit).",
        "* A missing element is reported as missing: nothing was back-filled to raise E6.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--benchmark", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--skip-scenario", action="store_true")
    args = parser.parse_args(argv)

    benchmark = load_trace_benchmark(args.benchmark, repo_root=REPO_ROOT)
    definitions, coverages = evaluate_definitions(benchmark)
    aggregates = evaluate_aggregates(benchmark, coverages)
    scenario = None if args.skip_scenario else run_scenario()
    report = evaluation_report(benchmark, definitions, aggregates, scenario)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (args.out / "README.md").write_text(readme(benchmark, report), encoding="utf-8")
    print(
        json.dumps(
            {
                "definition_agreement": f"{definitions['agreed']}/{definitions['cases']}",
                "aggregate_agreement": f"{aggregates['agreed']}/{aggregates['cases']}",
                "scenario_e6": {m["step"]: m["e6"] for m in scenario or []},
            },
            indent=2,
        )
    )
    return (
        0 if not definitions["disagreements"] and aggregates["agreed"] == aggregates["cases"] else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
