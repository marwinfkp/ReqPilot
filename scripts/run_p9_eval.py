"""Run the P9 E9 evaluation on the frozen P9-SDLC-SYNTHETIC-v1 benchmark.

    python -m scripts.run_p9_eval --benchmark data/gold/p9_sdlc_synthetic_v1 \\
        --out docs/evaluation/p9-sdlc-synthetic-v1

E9 measures agreement between ReqPilot and a synthetic AI-generated expert-panel
simulation. It is an engineering benchmark, not independent human-expert
validation.

**No numeric target is set or checked.** Phase 0 O.1 defines E9 but no target;
what this script produces is a first measurement.

1. The benchmark's manifest is verified (every file's hash, the case and
   response counts), and the frozen panel aggregate must equal a fresh
   deterministic aggregation of the frozen responses - before anything is
   compared.
2. Each case's approved facts go through the product's own deterministic path:
   the P7 matrix and I.6 aggregates, the P9 derivation, MCDA and the rule pass.
   No model is called; the ruleset is the packaged ``sdlc_rules.yaml`` whose
   hash the benchmark froze.
3. ReqPilot's ranking is compared with the panel's: top-choice agreement, rank
   correlation (Kendall's tau, Spearman's rho), pairwise order agreement, the
   disagreements, and the panel's own consensus strength.

The reports record identifiers, rankings and numbers - the cases are synthetic
and contain no real data.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from reqpilot.rules.risk import packaged_risk_rules
from reqpilot.rules.sdlc import packaged_sdlc_rules
from reqpilot.services.evaluation.sdlc_eval import (
    E9_STATEMENT,
    NO_TARGET,
    VALIDATION_STATUS,
    SdlcBenchmark,
    check_frozen_aggregate,
    compare,
    evaluation_report,
    load_sdlc_benchmark,
)


def _rate(block: dict[str, Any]) -> str:
    return f"{block['count']}/{block['of']} ({block['rate']:.2%})"


def readme(benchmark: SdlcBenchmark, report: dict[str, Any]) -> str:
    s = report["summary"]
    sut = report["system_under_test"]
    lines = [
        "# E9 - SDLC recommendation agreement (P9-SDLC-SYNTHETIC-v1)",
        "",
        f"> **{E9_STATEMENT}**",
        "",
        f"- Benchmark: `{report['benchmark_id']}`, manifest sha256 "
        f"`{report['manifest_sha256']}` (verified before the run).",
        f"- Validation status: {VALIDATION_STATUS}.",
        f"- System under test: `{sut['ruleset_ref']}` (sha256 `{sut['sdlc_rules_sha256']}`); "
        f"matches the ruleset frozen with the benchmark: "
        f"**{sut['matches_ruleset_frozen_with_the_benchmark']}**. {sut['path']}.",
        f"- Evaluated at {report['evaluated_at']} (the run is deterministic; only this "
        "timestamp changes between runs).",
        f"- Target: none. {NO_TARGET}",
        "",
        "## Summary",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| Cases | {s['cases']} |",
        f"| **E9 top-choice agreement** (ReqPilot first = panel Borda first) | "
        f"{_rate(s['e9_top1_agreement'])} |",
        f"| ReqPilot first within the panel's top 2 | {_rate(s['top1_in_panel_top2'])} |",
        f"| Panel first within ReqPilot's top 2 | {_rate(s['panel_top_in_reqpilot_top2'])} |",
        f"| ReqPilot first = a panel plurality choice | "
        f"{_rate(s['agreement_with_panel_plurality'])} |",
        f"| ReqPilot first preferred by at least one panellist | "
        f"{s['top1_supported_by_at_least_one_panellist']['count']}/"
        f"{s['top1_supported_by_at_least_one_panellist']['of']} |",
        f"| Mean Kendall's tau (full ranking) | {s['mean_kendall_tau']} |",
        f"| Mean Spearman's rho (full ranking) | {s['mean_spearman_rho']} |",
        f"| Mean pairwise order agreement | {s['mean_pairwise_agreement']} |",
        f"| Mean panel consensus strength (share of panellists whose first = Borda first) | "
        f"{s['mean_panel_consensus_strength']} |",
        f"| Mean panel Kendall's W (inter-panellist concordance) | {s['mean_panel_kendall_w']} |",
        "",
        f"First choices - ReqPilot: `{s['top1_distribution']['reqpilot']}`; panel: "
        f"`{s['top1_distribution']['panel']}`.",
        "",
        "## Per case",
        "",
        "| Case | ReqPilot first | Panel first (Borda) | Panel plurality | Consensus | "
        "tau | pairwise | Rules triggered |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for case in report["cases"]:
        panel = case["panel"]
        lines.append(
            f"| {case['case_id']} | `{case['reqpilot_top']}` | `{case['panel_top']}` | "
            f"{', '.join(f'`{p}`' for p in panel['plurality'])} | "
            f"{panel['consensus_strength']} | {case['kendall_tau']} | "
            f"{case['pairwise_agreement']} | {', '.join(case['rules_triggered']) or '-'} |"
        )
    lines += [
        "",
        "## Disagreements",
        "",
        "Cases where ReqPilot's first choice differs from the panel's Borda winner: "
        + (", ".join(s["disagreement_cases"]) or "none")
        + ". They are reported as measured; nothing was tuned after this comparison, and any "
        "correction to the scoring or the benchmark belongs in a new version (v2).",
        "",
        "## Limitations",
        "",
        "- The panel is five AI-generated personas answering independently and blind to "
        "ReqPilot's output; it is a simulation of expert judgement, not experts.",
        "- Twelve synthetic cases; the numbers describe this benchmark only.",
        "- The comparison exercises the deterministic path (derivation, MCDA, rules). Model "
        "proposals and human overrides - which can move a real recommendation - are not part "
        "of it.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--benchmark", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    benchmark = load_sdlc_benchmark(args.benchmark)
    check_frozen_aggregate(benchmark)
    sdlc_rules = packaged_sdlc_rules()
    comparisons = compare(benchmark, packaged_risk_rules(), sdlc_rules)
    report = evaluation_report(benchmark, comparisons, sdlc_rules)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (args.out / "README.md").write_text(readme(benchmark, report), encoding="utf-8")
    summary = report["summary"]
    print(
        json.dumps(
            {
                "statement": E9_STATEMENT,
                "e9_top1_agreement": summary["e9_top1_agreement"],
                "mean_kendall_tau": summary["mean_kendall_tau"],
                "mean_pairwise_agreement": summary["mean_pairwise_agreement"],
                "ruleset_matches_frozen": report["system_under_test"][
                    "matches_ruleset_frozen_with_the_benchmark"
                ],
            },
            indent=2,
        )
    )
    return 0 if report["system_under_test"]["matches_ruleset_frozen_with_the_benchmark"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
