"""Live P6 smoke test. **BILLABLE**: a small number of real requests on the developer's key.

Opt-in only, exactly as ``test_openai_live.py``: the ``llm`` mark is excluded by
``addopts``, and the test skips unless the developer's own configuration selects
``LLM_PROVIDER=openai`` with a key and a model. Run with::

    pytest -m llm tests/llm/test_p6_openai_live.py -s

Only the fictional development fixture of ``data/dev/compliance/`` is sent - seven
statements about a fictional bank, declared synthetic - together with the
fictional knowledge base retrieved for them. It asserts the invariants: every
recorded mapping cites evidence of its own run and carries no prohibited
assertion; every authoritative level is at least its floor; every high-impact
interpretation and every HIGH finding has a blocking gate; the injected
requirement approved nothing. Never that the model judged well - that is E5 and
the P6 benchmark's supplementary figures.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.llm.test_openai_live import live_settings  # noqa: F401 - the shared fixture
from tests.p6_helpers import make_world

from reqpilot.config import Settings
from reqpilot.domain.compliance.language import find_prohibited
from reqpilot.domain.compliance.risk import rank
from reqpilot.domain.models.approval import ApprovalDecision, ApprovalTask
from reqpilot.domain.models.compliance import ComplianceMapping, SecurityPrivacyFinding
from reqpilot.domain.models.runs import AgentRun
from reqpilot.llm import build_gateway
from reqpilot.services.knowledge.evidence import EvidenceService

pytestmark = pytest.mark.llm


def test_a_live_compliance_and_security_run_on_synthetic_requirements(
    db_session: Session,
    live_settings: Settings,  # noqa: F811
) -> None:
    world = make_world(db_session)
    snapshot = {k: v.content_hash for k, v in world.versions.items()}
    summary = world.analyse(gateway=build_gateway(live_settings), semantic=True)
    print(f"\n[live] status={summary.status} semantic_failures={summary.semantic_failures}")
    print(
        f"[live] provider_calls={summary.provider_calls} "
        f"tokens={summary.tokens_in}/{summary.tokens_out}"
    )
    print(
        f"[live] mappings={len(summary.compliance_mapping_ids)} dropped={summary.claims_dropped} "
        f"gaps={len(summary.compliance_gap_ids)} findings={len(summary.security_finding_ids)} "
        f"gates={len(summary.gate_task_ids)}"
    )
    assert summary.status.value == "completed"
    # One compliance call per requirement with evidence, two security/privacy calls each.
    assert summary.provider_calls <= 3 * len(world.versions) * 2  # generous: repairs included

    allowed = EvidenceService(db_session, world.auditor).evidence_ids_for_run(
        world.project_id, summary.run_id
    )
    for mapping in db_session.scalars(select(ComplianceMapping)):
        print(
            f"[live] mapping {world.key_of(mapping.requirement_version_id)} -> "
            f"{mapping.control_key} ({mapping.relationship}, high_impact={mapping.is_high_impact})"
        )
        assert mapping.evidence_count >= 1
        assert {c["evidence_id"] for c in mapping.citations} <= {str(e) for e in allowed}
        for text in (mapping.rationale, mapping.candidate_text, mapping.implied_obligation):
            assert find_prohibited(text) == []
        if mapping.is_high_impact:
            assert mapping.approval_task_id is not None
    for finding in db_session.scalars(select(SecurityPrivacyFinding)):
        print(
            f"[live] finding {world.key_of(finding.requirement_version_id)} {finding.family}: "
            f"proposed={finding.proposed_risk_level} floor={finding.catalogue_floor} "
            f"-> {finding.risk_level} ({finding.detected_by})"
        )
        assert rank(finding.risk_level) >= rank(finding.catalogue_floor)
        assert find_prohibited(finding.derived_requirement) == []
        if finding.risk_level.value == "high":
            assert finding.approval_task_id is not None
    calls: dict[str, int] = {}
    for run in db_session.scalars(select(AgentRun).where(AgentRun.graph_run_id == summary.run_id)):
        if run.model_version_id:
            calls[f"{run.node}:{run.role}"] = calls.get(f"{run.node}:{run.role}", 0) + 1
    print(f"[live] model calls by node and role: {calls}")

    # Nothing was approved, every raised gate is open and blocking, no version changed.
    assert db_session.scalars(select(ApprovalDecision)).first() is None
    for task in db_session.scalars(select(ApprovalTask)):
        assert task.blocking and task.status.value == "OPEN"
    for key, version in world.versions.items():
        db_session.refresh(version)
        assert version.content_hash == snapshot[key]
        assert version.state.value == "CANDIDATE"
