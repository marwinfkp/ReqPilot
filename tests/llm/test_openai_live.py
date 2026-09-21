"""Live OpenAI checks. **BILLABLE**: each test sends real requests on the developer's key.

Opt-in only, never in the default suite or CI:

* the ``llm`` mark is excluded by ``addopts`` (pyproject.toml), so these run only
  when selected explicitly;
* each test also skips unless the developer's own configuration (``.env`` or
  the shell) selects ``LLM_PROVIDER=openai`` with ``LLM_API_KEY`` and
  ``LLM_MODEL_DEFAULT``.

Run with::

    pytest -m llm tests/llm/test_openai_live.py -s

Only synthetic, fictional text is sent: a one-sentence requirement, and the
team's synthetic development transcript, declared ``SYNTHETIC``
(``data/dev/transcripts/``). Every request passes the gateway's egress guards
exactly as in the application. The key is never printed: the summaries below
show the model, token counts and outcomes only.

These are checks of the provider integration, **not E1**. E1 is measured only
against the frozen gold transcript #1, with human adjudication
(``data/gold/README.md``, ``scripts/evaluate_extraction.py``).
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.hermetic import SHELL_LLM_ENVIRONMENT
from tests.p3_helpers import extraction_rules, ingest, member
from tests.workflow.test_p1_exit_test import make_project

from reqpilot.agents.contracts.classification import ClassificationOutput
from reqpilot.agents.validation.classification import validate_classification
from reqpilot.config import LLMProvider, Settings
from reqpilot.domain.enums import AgentRole, GraphRunStatus, Role
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.approval import ApprovalTask
from reqpilot.domain.models.extraction import ModelVersion, SourceChunk, SourceDocument
from reqpilot.domain.models.requirements import RequirementVersion
from reqpilot.domain.models.runs import AgentRun
from reqpilot.graph.runner import AnalysisRunner
from reqpilot.llm import ContentBlock, TrustClass, build_gateway
from reqpilot.services.audit import AuditService

pytestmark = pytest.mark.llm

SYNTHETIC_REQUIREMENT = "The system shall export a monthly report of approved loan applications."


@pytest.fixture
def live_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """The developer's real configuration, restored for this test only."""
    monkeypatch.setenv("REQPILOT_DOTENV", "on")
    for name, value in SHELL_LLM_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    # A live check must reach the provider, never answer from a recording.
    monkeypatch.setenv("LLM_FIXTURE_MODE", "live")
    try:
        settings = Settings()
    except ValidationError as exc:  # the values are not shown: they may include the key
        pytest.skip(f"the live LLM configuration is invalid ({exc.error_count()} error(s))")
    if settings.llm_provider is not LLMProvider.OPENAI or not settings.llm_api_key:
        pytest.skip("live OpenAI checks need LLM_PROVIDER=openai and LLM_API_KEY (.env or shell)")
    return settings


def test_a_live_structured_call_returns_a_typed_valid_proposal(live_settings: Settings) -> None:
    """One tiny classification call: schema-valid, labelled, stamped with provenance."""
    result = build_gateway(live_settings).generate(
        role=AgentRole.CLASSIFICATION,
        prompt_name="requirement_classification",
        params={},
        content=[
            ContentBlock(
                label="requirement",
                text=SYNTHETIC_REQUIREMENT,
                trust_class=TrustClass.PROJECT_CONTENT,
                synthetic=True,
            )
        ],
        schema=ClassificationOutput,
    )
    meta = result.meta
    print(
        f"\n[live] provider={meta.provider} model={meta.model_id} ok={result.ok} "
        f"error={result.error_code} tokens_in={meta.tokens_in} tokens_out={meta.tokens_out} "
        f"latency_ms={meta.latency_ms} attempts={meta.attempts} repaired={meta.repaired} "
        f"cost={meta.cost_estimate}"
    )
    assert result.ok and result.value is not None, result.error_message
    assert meta.provider == "openai" and meta.is_model
    assert meta.model_id.startswith(str(live_settings.llm_model_default))
    assert meta.tokens_in > 0 and meta.tokens_out > 0 and meta.response_ids
    decision = validate_classification(result.value, extraction_rules())
    print(
        f"[live] labels={[(str(label.category), label.review_signal) for label in decision.labels]}"
    )
    assert not decision.failed, "deterministic validation accepted no label"
    assert live_settings.llm_api_key not in repr(result)


def test_the_p3_pipeline_runs_on_the_synthetic_workshop(
    live_settings: Settings, db_session: Session
) -> None:
    """The whole batch pipeline on the real model: every P3 invariant still holds."""
    session = db_session
    project = make_project(session, "Live OpenAI check (synthetic)")
    analyst = member(session, project, Role.ANALYST, "live-analyst@example.test")
    document = ingest(session, analyst, project.id)  # declared SYNTHETIC
    gateway = build_gateway(live_settings)
    summary = AnalysisRunner(session, gateway, extraction_rules(), settings=live_settings).extract(
        actor=analyst, project_id=ProjectId(project.id), source_ids=[document.id], domain="LOAN"
    )
    versions = list(session.scalars(select(RequirementVersion)))
    print(
        f"\n[live] status={summary.status} accepted={summary.accepted} merged={summary.merged} "
        f"rejected={summary.rejected} review_items={len(summary.review_item_ids)} "
        f"classified={len(summary.classified_version_ids)} calls={summary.provider_calls} "
        f"tokens_in={summary.tokens_in} tokens_out={summary.tokens_out} "
        f"cost={summary.cost_estimate} errors={list(summary.errors)}"
    )
    for version in versions:
        print(f"[live]   {version.state}: {version.statement}")

    assert summary.status is GraphRunStatus.COMPLETED, summary.errors
    assert summary.accepted >= 1, "no real proposal survived deterministic validation"

    # FR-EXT-007: every requirement is traceable to the exact source words.
    text = session.get(SourceDocument, document.id).text
    for version in versions:
        assert version.source_refs, "a requirement without a source"
        for ref in version.source_refs:
            chunk = session.get(SourceChunk, uuid.UUID(ref["ref"]))
            assert chunk is not None and chunk.source_document_id == document.id
            start, end = ref["span"]
            assert text[start:end] == ref["quote"]
        assert version.statement.startswith("The system shall ")

    # The model decides nothing: nothing past CLASSIFIED, no approval requested.
    assert {v.state for v in versions} <= {RequirementState.EXTRACTED, RequirementState.CLASSIFIED}
    assert session.scalars(select(ApprovalTask)).first() is None

    # Provenance: every LLM invocation is stamped with the real provider and model.
    llm_runs = [r for r in session.scalars(select(AgentRun)) if r.model_version_id]
    assert llm_runs
    for run in llm_runs:
        model = session.get(ModelVersion, uuid.UUID(run.model_version_id))
        assert model is not None and model.provider == "openai" and model.is_model
        assert run.output_refs.get("provider_response_ids")
    assert AuditService(session).verify_project_chain(project.id) == (True, None)
