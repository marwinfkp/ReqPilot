"""Shared fixtures and a scripted model for the P7 tests.

Nothing here is a model. :class:`ScriptedRiskModel` extends P6's scripted model
with answers for the two P7 prompts - requirement-level and project-level risk
identification - from a small table keyed by the words of the synthetic
development requirements. Every scripted answer still passes through the real
gateway, schema validation, citation resolution, the deterministic validator
(including the ``FR-RSK-011`` scope guard) and the severity matrix, exactly like
a model's.

The scripted ratings are chosen so that the synthetic world contains at least
one of every severity the matrix can produce, and at least one HIGH - which is
what the exit test needs in order to exercise G8 and the baseline block for real
rather than by construction.

:class:`P7World` is P6's world plus ``analyse_risk``: the P7 run reuses the
evidence the compliance run recorded, exactly as the production runner does.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from reqpilot.domain.enums import (
    ApprovalDecisionType,
    Gate,
    RiskSeverity,
    Role,
)
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.risk import Risk
from reqpilot.domain.policy import Actor
from reqpilot.graph.runner import AnalysisRunner, RunSummary
from reqpilot.llm import LLMRequest
from reqpilot.repositories.risk import RiskRepository
from reqpilot.rules.risk import RiskRules, load_risk_rules
from reqpilot.services.approval.service import ApprovalService
from reqpilot.services.risk.gates import RISK_SUBJECT
from tests.p3_helpers import TEST_SETTINGS, extraction_rules
from tests.p6_helpers import (
    EvidenceSeen,
    P6World,
    ScriptedComplianceModel,
    cite,
    compliance_rules,
    evidence_seen,
    make_world,
    requirement_text,
    scripted_gateway,
    security_rules,
)

RULES_DIR = TEST_SETTINGS.rules_dir


def risk_rules() -> RiskRules:
    return load_risk_rules(RULES_DIR)


#: (words of the requirement, category, title, likelihood, impact, evidence words)
#:
#: Chosen to cover the matrix: L3xI3 and L2xI3 are HIGH, L3xI1 and L2xI2 are
#: MEDIUM, L1xI2 is LOW. The HIGH ones are what make G8 and the baseline block
#: testable end to end.
RISK_TABLE: tuple[tuple[str, str, str, str, str, str], ...] = (
    (
        "multi-factor",
        "security",
        "Second-factor delivery may fail at peak load",
        "L3",
        "I3",
        "second factor",
    ),
    (
        "second loan officer",
        "operational",
        "Dual authorisation may stall when only one officer is available",
        "L2",
        "I2",
        "second authorised officer",
    ),
    (
        "retained for eight years",
        "compliance",
        "Retention schedule may not survive a storage migration",
        "L2",
        "I3",
        "retained for eight years",
    ),
    (
        "consent",
        "privacy",
        "Consent records may be incomplete for pre-existing applicants",
        "L3",
        "I1",
        "recorded consent",
    ),
    (
        "date of birth",
        "technical",
        "Identity fields may be duplicated across services",
        "L1",
        "I2",
        "",
    ),
)

#: The project-level pass's one risk (``FR-RSK-001``: risks of the set as a whole).
PROJECT_RISK = (
    "business",
    "The requirement set does not cover operational handover",
    "L2",
    "I2",
)


def risk(
    category: str,
    title: str,
    likelihood: str,
    impact: str,
    evidence: list[EvidenceSeen],
    **extra: Any,
) -> dict[str, Any]:
    """One scripted proposal. Note what it cannot carry: a severity."""
    return {
        "category": category,
        "title": title,
        "description": f"Scripted risk: {title.lower()}.",
        "likelihood": likelihood,
        "impact": impact,
        "likelihood_rationale": f"scripted rationale for {likelihood}",
        "impact_rationale": f"scripted rationale for {impact}",
        "evidence_ids": [e.evidence_id for e in evidence],
        "mitigations": [{"suggestion": f"Consider a control for {title.lower()}."}],
        "review_signal": 0.6,
        **extra,
    }


@dataclass
class ScriptedRiskModel(ScriptedComplianceModel):
    """P6's scripted model plus deterministic answers for the two P7 prompts."""

    def __call__(self, request: LLMRequest) -> Any:
        kind = request.prompt_template_id.split("@", 1)[0]
        if kind in self.overrides:
            self.calls[kind] += 1
            return self.overrides[kind](request)
        if kind == "risk_identification":
            self.calls[kind] += 1
            return self.identify(request)
        if kind == "project_risk_identification":
            self.calls[kind] += 1
            return self.identify_project(request)
        return super().__call__(request)

    def identify(self, request: LLMRequest) -> str:
        text = requirement_text(request).lower()
        risks = []
        for words, category, title, likelihood, impact, evidence_words in RISK_TABLE:
            if words not in text:
                continue
            cited = (
                cite(request, evidence_words)[:1] if evidence_words else evidence_seen(request)[:1]
            )
            if not cited:
                continue
            risks.append(risk(category, title, likelihood, impact, cited))
        return json.dumps({"requirement_version_id": self.risk_subject(request), "risks": risks})

    def identify_project(self, request: LLMRequest) -> str:
        cited = evidence_seen(request)[:1]
        category, title, likelihood, impact = PROJECT_RISK
        risks = [risk(category, title, likelihood, impact, cited)] if cited else []
        return json.dumps({"requirement_version_id": "", "risks": risks})


@dataclass
class P7World(P6World):
    """P6's world, with the risk run and the G8 helpers the P7 tests need."""

    def runner(self, gateway: Any | None = None, **kwargs: Any) -> AnalysisRunner:
        return AnalysisRunner(
            self.session,
            gateway or self.gateway,
            extraction_rules(),
            settings=TEST_SETTINGS,
            compliance_rules=compliance_rules(),
            security_rules=security_rules(),
            risk_rules=risk_rules(),
            retriever=kwargs.pop("retriever", self.retriever),
            **kwargs,
        )

    def analyse_risk(self, **kwargs: Any) -> RunSummary:
        """A risk-only run (C.3 nodes 18-19 and the G8 part of 20)."""
        gateway = kwargs.pop("gateway", None)
        return self.runner(gateway).analyse_risk(
            actor=kwargs.pop("actor", self.analyst), project_id=self.project_id, **kwargs
        )

    def full_analysis(self, **kwargs: Any) -> RunSummary:
        """One analysis run: compliance, security/privacy, then risk.

        Risk analysis is part of the compliance run's own graph path from P7
        (C.3 ``security_privacy_evaluate -> risk_identify``), because it takes
        the compliance and security results as input (``FR-RSK-001``). A
        separate ``analyse_risk`` run exists for re-analysing risk on its own
        and normally records nothing new straight afterwards, because the same
        risks are already recorded.
        """
        return self.analyse(**kwargs)

    # -- reads ---------------------------------------------------------
    def risks(self, **filters: Any) -> list[Risk]:
        return RiskRepository(self.session, self.analyst).list_for_project(
            self.project_id, **filters
        )

    def risk_by_title(self, fragment: str) -> Risk:
        found = [r for r in self.risks() if fragment.lower() in r.title.lower()]
        assert found, f"no risk whose title contains {fragment!r}"
        return found[0]

    def high_risk(self) -> Risk:
        found = [r for r in self.risks() if r.severity is RiskSeverity.HIGH]
        assert found, "the scripted world is expected to contain a HIGH risk"
        return found[0]

    def g8_tasks(self) -> list[Any]:
        return [
            t
            for t in ApprovalService(self.session, self.analyst).list_tasks(self.project_id)
            if t.gate is Gate.G8_HIGH_SEVERITY_RISK and t.subject_type == RISK_SUBJECT
        ]

    def g8_task_for(self, risk_id: uuid.UUID) -> Any:
        found = [t for t in self.g8_tasks() if t.subject_id == risk_id]
        assert found, f"no G8 task for risk {risk_id}"
        return found[0]

    def decide_g8(
        self,
        risk_id: uuid.UUID,
        *,
        decision: ApprovalDecisionType = ApprovalDecisionType.APPROVE,
        actor: Actor | None = None,
        role: Role = Role.SECURITY_REVIEWER,
        justification: str = "Reviewed; the mitigation is planned for the next iteration.",
    ) -> Any:
        """Decide a G8 task through the one approval path. No shortcut exists."""
        task = self.g8_task_for(risk_id)
        service = ApprovalService(self.session, actor or self.security_reviewer)
        return service.decide(
            project_id=self.project_id,
            task_id=task.id,
            decision=decision,
            role_exercised=role,
            justification=justification,
        )


def make_p7_world(
    session: Session,
    name: str = "P7 retail loan origination (synthetic)",
    model: ScriptedComplianceModel | None = None,
) -> P7World:
    base = make_world(session, name, model or ScriptedRiskModel())
    return P7World(**{f: getattr(base, f) for f in base.__dataclass_fields__})


def scripted_risk_gateway(
    model: ScriptedComplianceModel | None = None,
) -> tuple[Any, ScriptedComplianceModel, Any]:
    return scripted_gateway(model or ScriptedRiskModel())


def project_id_of(world: P7World) -> ProjectId:
    return world.project_id


#: Callable overriding one prompt's scripted answer, for the adversarial tests.
Override = Callable[[LLMRequest], Any]
