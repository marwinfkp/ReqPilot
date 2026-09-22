"""Shared fixtures and a scripted model for the P5 tests.

Nothing here is a model. :class:`ScriptedQualityModel` answers the two P5 prompts
- the quality review and the conflict adjudication - from a small table keyed by
the words of the synthetic development requirements
(``data/dev/quality/p5_quality_synthetic.yaml``). Every scripted answer still
passes through the real gateway, schema validation and the deterministic
validators, exactly like a model's.
"""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from reqpilot.domain.enums import RequirementCategory, Role
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.requirements import RequirementVersion
from reqpilot.domain.policy import Actor
from reqpilot.graph.runner import AnalysisRunner, RunSummary
from reqpilot.llm import LLMGateway, LLMRequest, ScriptedProvider
from reqpilot.rules.quality import QualityRules, load_quality_rules
from reqpilot.services.requirements import RequirementService
from reqpilot.services.requirements.service import RequirementContent
from tests.p3_helpers import RULES_DIR, TEST_SETTINGS, extraction_rules, ingest, member

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "data" / "dev" / "quality" / "p5_quality_synthetic.yaml"

_IDS = re.compile(
    r'requirement_version_id_a to exactly "([0-9a-f-]{36})".*?"([0-9a-f-]{36})"', re.S
)
_KEY = re.compile(r"^\[(R\d+)\] (.*)$", re.M)


def quality_rules() -> QualityRules:
    return load_quality_rules(RULES_DIR)


def fixture() -> dict[str, Any]:
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


def pair_ids(request: LLMRequest) -> tuple[str, str]:
    match = _IDS.search(request.instructions)
    assert match, "the adjudication prompt names both version ids"
    return match.group(1), match.group(2)


def adjudication(
    request: LLMRequest,
    verdict: str,
    *,
    kind: str = "other",
    evidence_a: str = "",
    evidence_b: str = "",
    **extra: Any,
) -> str:
    a, b = pair_ids(request)
    return json.dumps(
        {
            "requirement_version_id_a": a,
            "requirement_version_id_b": b,
            "verdict": verdict,
            "conflict_kind": kind,
            "explanation": f"scripted: {verdict}",
            "evidence_a": evidence_a,
            "evidence_b": evidence_b,
            "proposed_severity": "medium",
            "review_signal": 0.8,
            **extra,
        }
    )


@dataclass
class ScriptedQualityModel:
    """A deterministic stand-in for the model on the P5 prompts."""

    calls: Counter = field(default_factory=Counter)
    overrides: dict[str, Callable[[LLMRequest], Any]] = field(default_factory=dict)

    def __call__(self, request: LLMRequest) -> Any:
        kind = request.prompt_template_id.split("@", 1)[0]
        self.calls[kind] += 1
        if kind in self.overrides:
            return self.overrides[kind](request)
        if kind == "requirement_quality_review":
            return self.review(request)
        if kind == "conflict_adjudication":
            return self.adjudicate(request)
        raise AssertionError(f"unexpected prompt {kind}")

    def review(self, request: LLMRequest) -> str:
        findings = []
        for key, statement in _KEY.findall(request.untrusted_content["requirements"]):
            if "close their account" in statement:
                findings.append(
                    {
                        "requirement_key": key,
                        "finding_type": "incompleteness",
                        "evidence": "",
                        "explanation": "the statement does not say what checks precede closure",
                        "missing": "the checks required before an account is closed",
                        "proposed_severity": "high",
                        "review_signal": 0.7,
                    }
                )
        return json.dumps({"findings": findings})

    def adjudicate(self, request: LLMRequest) -> str:
        a = request.untrusted_content["requirement_a"]
        b = request.untrusted_content["requirement_b"]
        text = a + b
        if "close their account" in text and "branch staff" in text:
            first = "close their account from the mobile app"
            second = "Only branch staff shall close a customer account"
            ev_a, ev_b = (first, second) if first in a else (second, first)
            return adjudication(
                request,
                "definite_conflict",
                kind="actor_scope",
                evidence_a=ev_a,
                evidence_b=ev_b,
            )
        return adjudication(request, "no_conflict")


def scripted_gateway(
    model: ScriptedQualityModel | None = None,
) -> tuple[LLMGateway, ScriptedQualityModel, ScriptedProvider]:
    model = model or ScriptedQualityModel()
    provider = ScriptedProvider(model)
    return LLMGateway(provider, settings=TEST_SETTINGS, sleep=lambda _s: None), model, provider


@dataclass
class P5World:
    session: Session
    project_id: ProjectId
    analyst: Actor
    stakeholder_user: Actor
    auditor: Actor
    versions: dict[str, RequirementVersion]
    gateway: LLMGateway
    model: ScriptedQualityModel
    provider: ScriptedProvider
    rules: QualityRules

    def runner(self, gateway: LLMGateway | None = None, **kwargs: Any) -> AnalysisRunner:
        return AnalysisRunner(
            self.session,
            gateway or self.gateway,
            extraction_rules(),
            settings=TEST_SETTINGS,
            quality_rules=self.rules,
            **kwargs,
        )

    def analyse(self, **kwargs: Any) -> RunSummary:
        return self.runner().analyse_quality(
            actor=kwargs.pop("actor", self.analyst), project_id=self.project_id, **kwargs
        )

    def key_of(self, version_id: uuid.UUID) -> str:
        return next(k for k, v in self.versions.items() if v.id == version_id)


def seed_requirements(
    session: Session,
    analyst: Actor,
    project_id: ProjectId,
    requirements: list[dict[str, Any]],
    *,
    domain: str = "BANK",
) -> dict[str, RequirementVersion]:
    """Each statement becomes a requirement's first version, citing a synthetic source."""
    document = ingest(
        session,
        analyst,
        project_id,
        "\n".join(f"{r['stakeholder']}: {r['statement']}" for r in requirements),
        title="P5 statements (synthetic)",
    )
    service = RequirementService(session, analyst)
    versions: dict[str, RequirementVersion] = {}
    for item in requirements:
        _requirement, version = service.create_requirement(
            project_id=project_id,
            domain=domain,
            content=RequirementContent(
                statement=item["statement"],
                category=RequirementCategory.FUNCTIONAL,
                source_refs=(
                    {
                        "kind": "stakeholder_statement",
                        "document": str(document.id),
                        "stakeholder": item["stakeholder"],
                    },
                ),
            ),
        )
        versions[item["key"]] = version
    return versions


def make_world(session: Session, name: str = "P5 mobile banking (synthetic)") -> P5World:
    from tests.workflow.test_p1_exit_test import make_project

    project = make_project(session, name)
    suffix = uuid.uuid4().hex[:6]
    analyst = member(session, project, Role.ANALYST, f"analyst-{suffix}@example.test")
    stakeholder_user = member(session, project, Role.STAKEHOLDER, f"sh-{suffix}@example.test")
    auditor = member(session, project, Role.AUDITOR, f"auditor-{suffix}@example.test")
    project_id = ProjectId(project.id)
    versions = seed_requirements(session, analyst, project_id, fixture()["requirements"])
    gateway, model, provider = scripted_gateway()
    return P5World(
        session=session,
        project_id=project_id,
        analyst=analyst,
        stakeholder_user=stakeholder_user,
        auditor=auditor,
        versions=versions,
        gateway=gateway,
        model=model,
        provider=provider,
        rules=quality_rules(),
    )
