"""The elicitation ruleset and the role-specific interview templates (P4).

Loaded from two versioned data files (architecture DQ-03):

* ``elicitation.yaml`` - the follow-up bound, question and clarification
  validation limits, and the approved topic taxonomy (problem statement §7);
* ``interview_templates.yaml`` - role templates: which topics apply to a
  stakeholder role, in what priority, and which are required (``FR-ELI-001``).

Validation fails closed: a template naming an unknown topic, a duplicate topic,
or a non-positive follow-up bound refuses to load.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from reqpilot.domain.errors import RuleConfigurationError
from reqpilot.rules.loader import RuleSet, load_ruleset

RULESET_FILE = "elicitation.yaml"
TEMPLATES_FILE = "interview_templates.yaml"


@dataclass(frozen=True)
class TopicSpec:
    topic_id: str
    title: str
    description: str
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class TemplateTopic:
    topic_id: str
    priority: int
    required: bool
    expected_answer_shape: str


@dataclass(frozen=True)
class InterviewTemplate:
    """A topic framework for one stakeholder role - not a questionnaire."""

    template_id: str
    version: str
    stakeholder_role: str
    title: str
    topics: tuple[TemplateTopic, ...]

    @property
    def ref(self) -> str:
        return f"{self.template_id}@{self.version}"

    def topic(self, topic_id: str) -> TemplateTopic | None:
        return next((t for t in self.topics if t.topic_id == topic_id), None)

    @property
    def topic_ids(self) -> tuple[str, ...]:
        return tuple(t.topic_id for t in self.topics)


@dataclass(frozen=True)
class ElicitationRules:
    name: str
    version: str
    templates_version: str
    max_followups_per_topic: int
    recent_turns_in_prompt: int
    max_question_chars: int
    min_question_words: int
    max_answer_chars: int
    max_question_attempts: int
    clarification_max_question_chars: int
    clarification_min_question_words: int
    clarification_max_answer_shape_chars: int
    clarification_max_question_attempts: int
    clarification_max_answer_chars: int
    generic_questions: tuple[str, ...]
    authority_phrases: tuple[str, ...]
    topics: dict[str, TopicSpec]
    templates: dict[str, InterviewTemplate]

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}+templates@{self.templates_version}"

    def template(self, template_id: str) -> InterviewTemplate:
        try:
            return self.templates[template_id]
        except KeyError as exc:
            raise RuleConfigurationError(f"unknown interview template {template_id!r}") from exc

    def templates_for_role(self, stakeholder_role: str) -> list[InterviewTemplate]:
        return [t for t in self.templates.values() if t.stakeholder_role == stakeholder_role]

    @property
    def stakeholder_roles(self) -> tuple[str, ...]:
        return tuple(sorted({t.stakeholder_role for t in self.templates.values()}))

    @classmethod
    def from_rulesets(cls, ruleset: RuleSet, templates: RuleSet) -> ElicitationRules:
        try:
            rules = ruleset.data
            interview, clarification = rules["interview"], rules["clarification"]
            topics: dict[str, TopicSpec] = {}
            for raw in rules["topics"]:
                topic = TopicSpec(
                    topic_id=str(raw["id"]),
                    title=str(raw["title"]),
                    description=str(raw["description"]),
                    keywords=tuple(str(k).lower() for k in raw["keywords"]),
                )
                if topic.topic_id in topics or not topic.keywords:
                    raise RuleConfigurationError(f"topic {topic.topic_id!r} is duplicated or empty")
                topics[topic.topic_id] = topic

            parsed: dict[str, InterviewTemplate] = {}
            for raw in templates["templates"]:
                entries = tuple(
                    TemplateTopic(
                        topic_id=str(t["topic_id"]),
                        priority=int(t["priority"]),
                        required=bool(t["required"]),
                        expected_answer_shape=str(t.get("expected_answer_shape", "")),
                    )
                    for t in raw["topics"]
                )
                template = InterviewTemplate(
                    template_id=str(raw["template_id"]),
                    version=str(raw["version"]),
                    stakeholder_role=str(raw["stakeholder_role"]),
                    title=str(raw["title"]),
                    topics=entries,
                )
                ids = template.topic_ids
                if not ids or len(set(ids)) != len(ids):
                    raise RuleConfigurationError(f"{template.ref} lists no or duplicate topics")
                unknown = sorted(set(ids) - set(topics))
                if unknown:
                    raise RuleConfigurationError(f"{template.ref} names unknown topics {unknown}")
                if template.template_id in parsed:
                    raise RuleConfigurationError(f"template {template.template_id} is duplicated")
                parsed[template.template_id] = template

            built = cls(
                name=ruleset.name,
                version=ruleset.version,
                templates_version=templates.version,
                max_followups_per_topic=int(interview["max_followups_per_topic"]),
                recent_turns_in_prompt=int(interview["recent_turns_in_prompt"]),
                max_question_chars=int(interview["max_question_chars"]),
                min_question_words=int(interview["min_question_words"]),
                max_answer_chars=int(interview["max_answer_chars"]),
                max_question_attempts=int(interview["max_question_attempts"]),
                clarification_max_question_chars=int(clarification["max_question_chars"]),
                clarification_min_question_words=int(clarification["min_question_words"]),
                clarification_max_answer_shape_chars=int(clarification["max_answer_shape_chars"]),
                clarification_max_question_attempts=int(clarification["max_question_attempts"]),
                clarification_max_answer_chars=int(clarification["max_answer_chars"]),
                generic_questions=tuple(
                    str(q).strip().lower() for q in clarification["generic_questions"]
                ),
                authority_phrases=tuple(str(p).lower() for p in rules["authority_phrases"]),
                topics=topics,
                templates=parsed,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuleConfigurationError(f"elicitation rules are malformed: {exc}") from exc

        if built.max_followups_per_topic < 0:
            raise RuleConfigurationError("max_followups_per_topic must be 0 or more")
        for field in (
            "recent_turns_in_prompt",
            "max_question_chars",
            "min_question_words",
            "max_answer_chars",
            "max_question_attempts",
            "clarification_max_question_attempts",
        ):
            if getattr(built, field) < 1:
                raise RuleConfigurationError(f"{field} must be at least 1")
        return built


def load_elicitation_rules(rules_dir: str | Path) -> ElicitationRules:
    directory = Path(rules_dir)
    return ElicitationRules.from_rulesets(
        load_ruleset(directory / RULESET_FILE), load_ruleset(directory / TEMPLATES_FILE)
    )
