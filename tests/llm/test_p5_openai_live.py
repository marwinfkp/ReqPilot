"""Live P5 smoke test. **BILLABLE**: a small number of real requests on the developer's key.

Opt-in only, exactly as ``test_openai_live.py``: the ``llm`` mark is excluded by
``addopts``, and the test skips unless the developer's own configuration selects
``LLM_PROVIDER=openai`` with a key and a model. Run with::

    pytest -m llm tests/llm/test_p5_openai_live.py -s

Only the fictional development fixture of ``data/dev/quality/`` is sent (13
statements attributed to fictional people), declared synthetic. It asserts the
invariants - typed, validated proposals or a recorded failure, bounded calls,
evidence that is words of the right statement, nothing approved - never that the
model judged well. That is what E2/E3 measure, on the frozen benchmark.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.llm.test_openai_live import live_settings  # noqa: F401 - the shared fixture
from tests.p5_helpers import make_world

from reqpilot.config import Settings
from reqpilot.domain.enums import FindingDetector
from reqpilot.domain.models.approval import ApprovalTask
from reqpilot.domain.models.elicitation import QualityFinding
from reqpilot.domain.models.quality import Conflict
from reqpilot.domain.models.requirements import RequirementVersion
from reqpilot.domain.models.runs import AgentRun
from reqpilot.llm import build_gateway

pytestmark = pytest.mark.llm


def test_a_live_quality_and_conflict_run_on_synthetic_requirements(
    db_session: Session,
    live_settings: Settings,  # noqa: F811
) -> None:
    world = make_world(db_session)
    snapshot = {k: v.content_hash for k, v in world.versions.items()}
    gateway = build_gateway(live_settings)
    summary = world.runner(gateway).analyse_quality(
        actor=world.analyst, project_id=world.project_id, semantic=True
    )
    print(f"\n[live] status={summary.status} semantic_failures={summary.semantic_failures}")
    print(f"[live] provider_calls={summary.provider_calls}")
    print(f"[live] tokens in/out={summary.tokens_in}/{summary.tokens_out}")
    assert summary.status.value == "completed"
    pairs = len(world.versions) * (len(world.versions) - 1) // 2
    assert summary.provider_calls < pairs, "the shortlist bounds the pairwise calls"

    by_id = {v.id: v for v in db_session.scalars(select(RequirementVersion))}
    for conflict in db_session.scalars(select(Conflict)):
        a, b = by_id[conflict.version_a_id], by_id[conflict.version_b_id]
        assert conflict.evidence_a.lower() in a.statement.lower()
        assert conflict.evidence_b.lower() in b.statement.lower()
        key = {world.key_of(a.id), world.key_of(b.id)}
        what = f"{conflict.conflict_class} {conflict.kind} by {conflict.detected_by}"
        print(f"[live] conflict {sorted(key)} {what}")
    agent_findings = [
        f
        for f in db_session.scalars(select(QualityFinding))
        if f.detected_by is FindingDetector.AGENT
    ]
    for finding in agent_findings:
        version = by_id[finding.requirement_version_id]
        if finding.span_quote:
            assert finding.span_quote in version.statement
    print(f"[live] agent findings={len(agent_findings)}")
    calls: dict[str, int] = {}
    for run in db_session.scalars(select(AgentRun).where(AgentRun.graph_run_id == summary.run_id)):
        if run.model_version_id:
            calls[str(run.role)] = calls.get(str(run.role), 0) + 1
    print(f"[live] model calls by role: {calls}")

    # Nothing was approved, and no version changed.
    assert db_session.scalars(select(ApprovalTask)).first() is None
    for key, version in world.versions.items():
        db_session.refresh(version)
        assert version.content_hash == snapshot[key]
