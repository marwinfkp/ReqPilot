"""The P9 synthetic world: the P8 world, baselined, plus an architect and a scripted role #10.

Nothing here is a model. :class:`ScriptedP9Model` extends the P8 scripted model
with deterministic answers for the two role #10 prompts:

* ``sdlc_factor_proposal`` - by default it proposes a +1 adjustment of
  ``need_for_formal_verification`` citing a reference that was shown, and one
  proposal for a risk-derived factor (which validation must reject);
* ``sdlc_explanation`` - it reads the computed ranking from the request's
  fenced ranking block and writes a consistent explanation: the first
  candidate, its scores copied exactly, inline factor markers, a counter-argument
  for the runner-up with a reversal condition.

``overrides[kind]`` replaces either answer (a discrepancy, a failure, a hostile
output). Every answer still passes through the real gateway, schema validation,
the deterministic validators and the consistency check, exactly like a model's.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any
from unittest import mock

from sqlalchemy.orm import Session

from reqpilot.domain.enums import ApprovalDecisionType, ApprovalTaskStatus, Gate, Role
from reqpilot.domain.policy import Actor
from reqpilot.graph.sdlc_runner import SdlcRunner, SdlcRunSummary
from reqpilot.llm import LLMRequest
from reqpilot.services.approval.service import ApprovalService
from reqpilot.services.sdlc.service import SdlcService
from tests.p3_helpers import TEST_SETTINGS, member
from tests.p7_helpers import risk_rules
from tests.p8_helpers import P8World, ScriptedP8Model, make_p8_world

#: What B1 holds (as in the P8 exit test).
B1_KEYS = ["L01", "L03", "L05", "L06", "L08"]

_RANK_LINE = re.compile(r"^(\d+)\. ([a-z_]+) \(.*?\) \| score=([0-9.]+) \| mcda=([0-9.]+)", re.M)
_REF = re.compile(
    r"(?:requirement_version|risk|baseline|compliance_mapping|stakeholder):[0-9a-f-]{36}"
)
_REVERSAL = re.compile(r"^([a-z_]+) (\d) -> (\d) makes ([a-z_]+) first$", re.M)


def ranking_of(request: LLMRequest) -> list[tuple[str, float, float]]:
    text = request.untrusted_content.get("ranking", "")
    return [(m.group(2), float(m.group(3)), float(m.group(4))) for m in _RANK_LINE.finditer(text)]


def refs_of(request: LLMRequest) -> list[str]:
    return list(dict.fromkeys(_REF.findall(request.untrusted_content.get("factor_profile", ""))))


def consistent_explanation(request: LLMRequest) -> dict[str, Any]:
    ranking = ranking_of(request)
    assert ranking, "the explanation request carries the computed ranking"
    (top, top_score, _), (runner, runner_score, _) = ranking[0], ranking[1]
    reversal = _REVERSAL.search(request.untrusted_content.get("ranking", ""))
    reversal_text = (
        f"If [factor:{reversal.group(1)}] moved from {reversal.group(2)} to "
        f"{reversal.group(3)}, {runner} would rank first."
        if reversal
        else "No single-factor change on the 1-5 scale reverses the ranking."
    )
    refs = refs_of(request)[:2]
    cited = " ".join(f"[ref:{r}]" for r in refs)
    return {
        "narrative": (
            f"{top} ranks first because of the approved profile: "
            "[factor:need_for_formal_verification] and [factor:regulatory_criticality] "
            f"weigh towards verification-heavy delivery. {cited}"
        ),
        "counter_arguments": [
            {
                "candidate": runner,
                "why_not_selected": (
                    f"{runner} scored {runner_score:g} against {top_score:g}; "
                    "[factor:expected_frequency_of_change] does not favour it enough."
                ),
                "reversal_condition": reversal_text,
                "cited_factors": ["expected_frequency_of_change"],
            }
        ],
        "cited_factors": ["need_for_formal_verification", "regulatory_criticality"],
        "cited_evidence_refs": refs,
        "asserted_top_candidate": top,
        "asserted_scores": {top: top_score, runner: runner_score},
    }


def proposals(request: LLMRequest) -> dict[str, Any]:
    refs = refs_of(request)
    return {
        "proposals": [
            {
                "factor": "need_for_formal_verification",
                "proposed_score": _derived(request, "need_for_formal_verification") + 1
                if _derived(request, "need_for_formal_verification") < 5
                else 4,
                "rationale": "Dual control on disbursement implies independent verification.",
                "evidence_refs": refs[:1],
            },
            {
                "factor": "security_risk",
                "proposed_score": 1,
                "rationale": "Scripted attempt to lower a risk-derived factor.",
                "evidence_refs": refs[:1],
            },
        ]
    }


def _derived(request: LLMRequest, factor: str) -> int:
    match = re.search(
        rf"\[factor:{factor}\] score=(\d)", request.untrusted_content["factor_profile"]
    )
    assert match, factor
    return int(match.group(1))


class ScriptedP9Model(ScriptedP8Model):
    def __call__(self, request: LLMRequest) -> Any:
        kind = request.prompt_template_id.split("@", 1)[0]
        if kind in self.overrides:
            self.calls[kind] += 1
            return self.overrides[kind](request)
        if kind == "sdlc_factor_proposal":
            self.calls[kind] += 1
            return json.dumps(proposals(request))
        if kind == "sdlc_explanation":
            self.calls[kind] += 1
            return json.dumps(consistent_explanation(request))
        return super().__call__(request)


@dataclass
class P9World:
    p8: P8World
    architect: Actor
    baseline_id: uuid.UUID

    @property
    def session(self) -> Session:
        return self.p8.session

    @property
    def project_id(self) -> Any:
        return self.p8.project_id

    @property
    def model(self) -> ScriptedP9Model:
        model = self.p8.model
        assert isinstance(model, ScriptedP9Model)
        return model

    def runner(self) -> SdlcRunner:
        return SdlcRunner(
            self.session, self.p8.gateway, settings=TEST_SETTINGS, risk_rules=risk_rules()
        )

    def service(self, actor: Actor | None = None) -> SdlcService:
        from reqpilot.rules.sdlc import packaged_sdlc_rules

        return SdlcService(self.session, actor or self.p8.analyst, packaged_sdlc_rules())

    def start(self, *, semantic: bool | None = True) -> SdlcRunSummary:
        return self.runner().start(
            actor=self.p8.analyst,
            project_id=self.project_id,
            baseline_id=self.baseline_id,
            semantic=semantic,
        )

    def decider_for(self, role: Role) -> Actor:
        if role is Role.ARCHITECT:
            return self.architect
        return self.p8.decider_for(role)

    def g6_tasks(self, status: ApprovalTaskStatus | None = None) -> list[Any]:
        return self.p8.tasks(gate=Gate.G6_SDLC_SELECTION, status=status)

    def decide(
        self,
        task: Any,
        decision: ApprovalDecisionType = ApprovalDecisionType.APPROVE,
        *,
        actor: Actor | None = None,
        role: Role | None = None,
        justification: str = "G6 review (synthetic P9 world).",
    ) -> Any:
        return ApprovalService(self.session, actor or self.decider_for(task.required_role)).decide(
            project_id=self.project_id,
            task_id=task.id,
            decision=decision,
            role_exercised=role or task.required_role,
            justification=justification,
        )


def make_p9_world(session: Session, name: str = "P9 loan origination (synthetic)") -> P9World:
    from reqpilot.domain.models.identity import Project

    # ``tests/p8_helpers.py`` is the P8-TRACE-SYNTHETIC-v1 scenario fixture and its
    # hash is frozen in that benchmark's manifest, so it cannot grow a parameter.
    # ScriptedP9Model answers every P3-P8 prompt exactly as ScriptedP8Model does
    # and only adds the two SDLC prompts, so the P8 world is built with it by
    # substituting the class for this one call.
    with mock.patch("tests.p8_helpers.ScriptedP8Model", ScriptedP9Model):
        p8 = make_p8_world(session, name)
    baseline_id = p8.govern_and_baseline(B1_KEYS, "B1")
    project = session.get(Project, p8.project_id)
    architect = member(
        session, project, Role.ARCHITECT, f"arch-{uuid.uuid4().hex[:6]}@example.test"
    )
    return P9World(p8=p8, architect=architect, baseline_id=baseline_id)
