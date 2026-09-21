"""Compute E1 for one extraction run against a frozen gold set (Phase 0 O.1; R.4).

Two steps, because "semantic match, adjudicated" needs a human in the middle::

    # 1. list the candidate pairs for adjudicators to decide
    python scripts/evaluate_extraction.py --gold data/gold/<set> --run <run id> \\
        --actor <user id> --propose-pairs pairs.jsonl

    # 2. with their verdicts ({"prediction_ref", "gold_id", "verdict", "adjudicator"})
    python scripts/evaluate_extraction.py --gold data/gold/<set> --run <run id> \\
        --actor <user id> --adjudications verdicts.jsonl --report e1.md

The gold set is verified against its manifest first; a modified set is refused.
The report says **NOT E1** - and gives no figure - unless the set is declared gold
transcript #1, every proposed pair is adjudicated, and a real model made every
extraction call. The actor must be able to read the run (any project member
role with ``run.read``).
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import asdict
from pathlib import Path

from reqpilot.api.dependencies import load_actor
from reqpilot.api.lookup import scan_actor_projects
from reqpilot.domain.enums import Action, ResourceType
from reqpilot.repositories.database import get_session_factory
from reqpilot.repositories.extraction import RunRepository
from reqpilot.services.evaluation.extraction_eval import (
    compute_e1,
    load_adjudications,
    load_gold_set,
    propose_pairs,
)
from reqpilot.services.evaluation.extraction_predictions import predictions_for_run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gold", required=True, type=Path, help="frozen gold-set directory")
    parser.add_argument("--run", required=True, type=uuid.UUID, help="extraction graph run id")
    parser.add_argument("--actor", required=True, type=uuid.UUID, help="user id reading the run")
    parser.add_argument("--propose-pairs", type=Path, help="write candidate pairs (JSONL)")
    parser.add_argument("--adjudications", type=Path, help="human verdicts (JSONL)")
    parser.add_argument("--report", type=Path, help="write the Markdown report here")
    args = parser.parse_args(argv)

    gold = load_gold_set(args.gold)
    session = get_session_factory()()
    try:
        actor = load_actor(session, args.actor)
        runs = RunRepository(session, actor)
        found = scan_actor_projects(
            actor, Action.RUN_READ, ResourceType.GRAPH_RUN, lambda p: runs.get_run(p, args.run)
        )
        if found is None:
            print("run not found in any project this actor may read", file=sys.stderr)
            return 2
        project_id = found[0]
        predictions, facts = predictions_for_run(session, actor, project_id, args.run, gold)
    finally:
        session.close()

    if args.propose_pairs:
        pairs = propose_pairs(gold, predictions)
        args.propose_pairs.write_text(
            "".join(json.dumps(asdict(p)) + "\n" for p in pairs), encoding="utf-8"
        )
        print(f"{len(pairs)} candidate pair(s) written to {args.propose_pairs}")

    adjudications = load_adjudications(args.adjudications) if args.adjudications else []
    report = compute_e1(gold, predictions, adjudications, facts)
    markdown = report.to_markdown()
    if args.report:
        args.report.write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
