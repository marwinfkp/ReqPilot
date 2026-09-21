"""Shared fixtures and a scripted model for the P4 tests.

Nothing here is a model. :class:`ScriptedPersonaModel` answers the gateway's
four P4/P3 prompts - interview question, answer assessment, extraction,
classification, clarification - from the synthetic persona fixture
``data/dev/personas/p4_product_owner_synthetic.yaml``, which seeds clear, vague
and incomplete answers, an injection line and a clarification-worthy
requirement. Every scripted response still passes through the real gateway,
schema validation and deterministic checks.
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

from reqpilot.domain.enums import DataSensitivity, Role, StakeholderAuthority
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.elicitation import Stakeholder
from reqpilot.domain.policy import Actor
from reqpilot.graph.elicitation_runner import ElicitationRunner, InterviewTurn
from reqpilot.llm import LLMGateway, LLMRequest, ScriptedProvider
from reqpilot.rules.elicitation import ElicitationRules, load_elicitation_rules
from reqpilot.services.elicitation import StakeholderService
from tests.p3_helpers import RULES_DIR, TEST_SETTINGS, extraction_rules, member, segment_id

REPO_ROOT = Path(__file__).resolve().parents[1]
PERSONA_FILE = REPO_ROOT / "data" / "dev" / "personas" / "p4_product_owner_synthetic.yaml"


def elicitation_rules() -> ElicitationRules:
    return load_elicitation_rules(RULES_DIR)


def persona() -> dict[str, Any]:
    return yaml.safe_load(PERSONA_FILE.read_text(encoding="utf-8"))


_TOPIC = re.compile(r"topic_id: ([a-z_]+)")
_DEPTH = re.compile(r"Follow-up depth for this topic: (\d+)")
_DEFECT = re.compile(r"id ([0-9a-f-]{36})")


@dataclass
class ScriptedPersonaModel:
    """A deterministic stand-in for the model, driven by the persona fixture."""

    data: dict[str, Any] = field(default_factory=persona)
    calls: Counter = field(default_factory=Counter)
    #: Optional per-kind overrides: kind -> responder returning text or an exception.
    overrides: dict[str, Callable[[LLMRequest], Any]] = field(default_factory=dict)

    def __call__(self, request: LLMRequest) -> Any:
        kind = request.prompt_template_id.split("@", 1)[0]
        self.calls[kind] += 1
        if kind in self.overrides:
            return self.overrides[kind](request)
        return {
            "stakeholder_interview_question": self.question,
            "stakeholder_answer_assessment": self.assessment,
            "requirement_extraction": self.extraction,
            "requirement_classification": self.classification,
            "clarification_question": self.clarification,
        }[kind](request)

    # -- role #2 -----------------------------------------------------------
    def question(self, request: LLMRequest) -> str:
        topic = _TOPIC.search(request.instructions).group(1)  # type: ignore[union-attr]
        depth = int(_DEPTH.search(request.instructions).group(1))  # type: ignore[union-attr]
        followup = "exactly one follow-up question" in request.instructions
        questions = self.data["topics"][topic]["questions"]
        return json.dumps(
            {
                "question": questions[min(depth, len(questions) - 1)],
                "topic_id": topic,
                "is_followup": followup,
                "rationale": "scripted",
            }
        )

    def assessment(self, request: LLMRequest) -> str:
        topic = _TOPIC.search(request.instructions).group(1)  # type: ignore[union-attr]
        answer_block = request.untrusted_content["answer"]
        for answer in self.data["topics"][topic]["answers"]:
            if answer["text"] in answer_block:
                return json.dumps(
                    {
                        "topic_id": topic,
                        "status": answer["assessment"],
                        "rationale": "scripted",
                        "detected_issue": answer.get("issue"),
                    }
                )
        return json.dumps({"topic_id": topic, "status": "complete", "rationale": "unscripted"})

    # -- P3 roles, over utterances -------------------------------------------
    def extraction(self, request: LLMRequest) -> str:
        segments = request.untrusted_content["segments"]
        clarification = self.data["clarification"]
        requirements = []
        if clarification["answer"] in segments:
            revised = clarification["revised"]
            requirements.append(
                {
                    "candidate_key": "r1",
                    "statement": revised["statement"],
                    "requirement_type": "functional",
                    "evidence": [
                        {
                            "segment_id": segment_id(request, revised["quote"]),
                            "quote": revised["quote"],
                        }
                    ],
                    "review_signal": 0.9,
                }
            )
            return json.dumps({"requirements": requirements})
        for index, item in enumerate(self.data["extraction"], start=1):
            if item["quote"] not in segments:
                continue
            requirements.append(
                {
                    "candidate_key": f"c{index}",
                    "statement": item["statement"],
                    "requirement_type": item["type"],
                    "evidence": [
                        {"segment_id": segment_id(request, item["quote"]), "quote": item["quote"]}
                    ],
                    "review_signal": 0.9,
                }
            )
        return json.dumps({"requirements": requirements})

    def classification(self, request: LLMRequest) -> str:
        statement = request.untrusted_content["requirement"]
        for item in [*self.data["extraction"]]:
            if item["statement"] in statement:
                labels = item["categories"]
                break
        else:
            labels = ["functional"]
        return json.dumps(
            {"labels": [{"category": c, "review_signal": 0.9, "rationale": "r"} for c in labels]}
        )

    # -- role #4 -----------------------------------------------------------
    def clarification(self, request: LLMRequest) -> str:
        defect = _DEFECT.search(request.instructions).group(1)  # type: ignore[union-attr]
        item = self.data["clarification"]
        return json.dumps(
            {
                "question": item["question"],
                "expected_answer_shape": item["expected_answer_shape"],
                "defect_id": defect,
            }
        )


def scripted_gateway(
    model: ScriptedPersonaModel | None = None,
) -> tuple[LLMGateway, ScriptedPersonaModel, ScriptedProvider]:
    model = model or ScriptedPersonaModel()
    provider = ScriptedProvider(model)
    return LLMGateway(provider, settings=TEST_SETTINGS, sleep=lambda _s: None), model, provider


@dataclass
class P4World:
    session: Session
    project_id: ProjectId
    analyst: Actor
    stakeholder_user: Actor
    stakeholder: Stakeholder
    gateway: LLMGateway
    model: ScriptedPersonaModel
    provider: ScriptedProvider
    rules: ElicitationRules

    def runner(self) -> ElicitationRunner:
        return ElicitationRunner(self.session, self.gateway, self.rules, settings=TEST_SETTINGS)

    def start(self, **overrides: Any) -> InterviewTurn:
        return self.runner().start(
            actor=overrides.pop("actor", self.analyst),
            project_id=self.project_id,
            stakeholder_id=overrides.pop("stakeholder_id", self.stakeholder.id),
            sensitivity=overrides.pop("sensitivity", DataSensitivity.SYNTHETIC),
            **overrides,
        )

    def answer(
        self, session_id: uuid.UUID, text: str, *, actor: Actor | None = None
    ) -> InterviewTurn:
        return self.runner().answer(
            actor=actor or self.stakeholder_user,
            project_id=self.project_id,
            session_id=session_id,
            text=text,
        )

    def persona_answer(self, turn: InterviewTurn) -> str:
        """The persona's answer to the pending question: by topic and follow-up depth."""
        assert turn.question is not None, "no question is pending"
        topic = turn.question.topic_id
        answers = self.model.data["topics"][topic]["answers"]
        depth = turn.session.followups_this_topic
        return answers[min(depth, len(answers) - 1)]["text"]


def make_world(session: Session, name: str = "P4 loan origination (synthetic)") -> P4World:
    from tests.workflow.test_p1_exit_test import make_project

    project = make_project(session, name)
    analyst = member(session, project, Role.ANALYST, f"analyst-{uuid.uuid4().hex[:6]}@example.test")
    stakeholder_user = member(
        session, project, Role.STAKEHOLDER, f"dana-{uuid.uuid4().hex[:6]}@example.test"
    )
    rules = elicitation_rules()
    data = persona()["persona"]
    stakeholder = StakeholderService(session, analyst, rules).create(
        project_id=ProjectId(project.id),
        name=data["name"],
        stakeholder_role=data["stakeholder_role"],
        authority_level=StakeholderAuthority(data["authority_level"]),
        user_id=stakeholder_user.actor_id,
    )
    gateway, model, provider = scripted_gateway()
    return P4World(
        session=session,
        project_id=ProjectId(project.id),
        analyst=analyst,
        stakeholder_user=stakeholder_user,
        stakeholder=stakeholder,
        gateway=gateway,
        model=model,
        provider=provider,
        rules=rules,
    )


def run_persona_interview(world: P4World, turn: InterviewTurn, *, limit: int = 40) -> InterviewTurn:
    """Answer every question from the persona until the interview completes."""
    for _ in range(limit):
        if turn.complete or turn.question is None:
            return turn
        turn = world.answer(turn.session.id, world.persona_answer(turn))
    raise AssertionError("the interview did not complete within the turn limit")


__all__ = [
    "PERSONA_FILE",
    "P4World",
    "ScriptedPersonaModel",
    "elicitation_rules",
    "extraction_rules",
    "make_world",
    "persona",
    "run_persona_interview",
    "scripted_gateway",
]
