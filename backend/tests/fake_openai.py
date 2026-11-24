"""A fake OpenAI client so that "OpenAI mode" can be tested without the network.

ReqPilot only talks to OpenAI through two methods (see app/llm.py):

* ``client.responses.parse(model=..., input=[...], text_format=Schema)`` must return an
  object with ``.output_parsed`` (an instance of ``Schema``) and ``.usage``
  (``input_tokens`` / ``output_tokens``);
* ``client.embeddings.create(model=..., input=[...], dimensions=...)`` must return an
  object whose ``.data`` items have an ``.embedding`` list.

For every Structured-Output schema used by an agent there is a small *factory* below
that builds a plausible answer from the prompt text. On purpose, every factory also
"hallucinates": it returns requirement IDs, knowledge-base chunk IDs and quotes that do
not exist. The tests then check that ReqPilot rejects these and counts them in
``ctx["reference_checks"]`` (the hallucination metric).

Switches on the fake (set them in a test):
* ``fail_schemas``    - schema names (or "*") for which an ``openai.OpenAIError`` is raised
* ``refuse_schemas``  - schema names (or "*") for which ``output_parsed`` is ``None``
* ``fail_embeddings`` - raise an ``openai.OpenAIError`` from ``embeddings.create``
"""
import hashlib
import re
import threading
from types import SimpleNamespace

import openai

# Values that never exist in a real analysis.
INVENTED_REQ_ID = "FR-ZZZ-999"
INVENTED_LINE = 99_999
INVENTED_CHUNK_ID = 999_999
INVENTED_STATEMENT = "The system shall mine cryptocurrency on customer phones."
FABRICATED_QUOTE = "Banks must store every customer password in plain text on public servers."
FABRICATED_EVIDENCE = "this phrase does not appear in any requirement"
VALID_PHASE_MARKER = "Fake tailored activity"

_REQ_ID = r"(?:FR|NFR|BR|CON)-[A-Z]{3}-\d{3}"


# --------------------------------------------------------------------------
# Helpers that read the prompt the agent sent
# --------------------------------------------------------------------------
def _user_text(messages: list[dict]) -> str:
    return "\n".join(m["content"] for m in messages if m["role"] == "user")


def _requirement_ids(text: str) -> list[str]:
    """Requirement IDs at the start of a line, in order of appearance."""
    return list(dict.fromkeys(re.findall(rf"^({_REQ_ID})\b", text, re.M)))


def _passages(text: str) -> dict[int, str]:
    """{chunk_id: passage text} from the compliance agent's prompt."""
    pattern = re.compile(r"\[chunk_id=(\d+)\][^\n]*\n(.*?)(?=\n\n\[chunk_id=|\n</untrusted_knowledge_base_passages>)",
                         re.S)
    return {int(cid): body for cid, body in pattern.findall(text)}


def _verbatim_quote(passage: str) -> str:
    """A quote that really is in the passage (first 80 characters, whitespace normalised)."""
    return " ".join(passage.split())[:80]


# --------------------------------------------------------------------------
# One factory per Structured-Output schema (keyed by the schema's class name)
# --------------------------------------------------------------------------
def _extraction(text, schema):
    items = []
    for number, line in re.findall(r"^L(\d+): (.*)$", text, re.M):
        body = re.sub(r"^\s*(?:[-*]\s*)?(?:\[+[^\]]*\]+\s*)?", "", line)  # bullet and [Stakeholder] tag
        if re.search(r"\b(shall|must|should|may)\b", body, re.I) and len(body.split()) >= 4:
            items.append({"line_numbers": [int(number)], "statement": body,
                          "kind": "non_functional" if re.search(r"\d+(\.\d+)?\s*%|seconds?|encrypt", body, re.I)
                          else "functional",
                          "source": "Unspecified"})
    # Hallucination: a requirement that points to a line that does not exist.
    items.append({"line_numbers": [INVENTED_LINE], "statement": INVENTED_STATEMENT, "kind": "functional",
                  "source": "Unspecified"})
    return schema.model_validate({"requirements": items})


def _classification(text, schema):
    items = []
    for line in re.findall(rf"^({_REQ_ID}) \((\w+)\):", text, re.M):
        req_id, kind = line
        categories = ["non_functional", "security"] if kind in ("non_functional", "constraint") \
            else ["functional", "business"]
        items.append({"req_id": req_id, "categories": categories, "priority": "High",
                      "confidence": 1.7,  # out of range on purpose -> must be clamped to 1.0
                      "dependencies": [INVENTED_REQ_ID]})
    items.append({"req_id": INVENTED_REQ_ID, "categories": ["functional"], "priority": "Low", "confidence": 0.5,
                  "dependencies": []})
    return schema.model_validate({"items": items})


def _quality(text, schema):
    rows = re.findall(rf"^({_REQ_ID}) \[source: [^\]]*\]: (.*)$", text, re.M)
    issues = []
    if rows:
        first_id, first_statement = rows[0]
        last_id = rows[-1][0]
        issues.append({"issue_type": "untestable", "severity": "Medium", "requirement_ids": [first_id],
                       "explanation": "Fake LLM issue with real evidence.",
                       "evidence": " ".join(first_statement.split()[:4]),  # copied from the requirement
                       "suggested_correction": "Add a measurable criterion.",
                       "clarification_question": "How will this be tested?"})
        issues.append({"issue_type": "untestable", "severity": "Medium", "requirement_ids": [last_id],
                       "explanation": "Fake LLM issue with invented evidence.", "evidence": FABRICATED_EVIDENCE,
                       "suggested_correction": "Clarify.", "clarification_question": "What is meant?"})
    issues.append({"issue_type": "conflict", "severity": "High", "requirement_ids": [INVENTED_REQ_ID],
                   "explanation": "Conflict with a requirement that does not exist.", "evidence": "",
                   "suggested_correction": "", "clarification_question": ""})
    assumptions = [{"assumption": "Customers use a smartphone.",
                    "requirement_ids": ([rows[0][0]] if rows else []) + [INVENTED_REQ_ID]}]
    return schema.model_validate({"issues": issues, "assumptions": assumptions})


def _compliance(text, schema):
    ids = _requirement_ids(text)
    passages = _passages(text)
    mappings = []
    if ids and passages:
        chunk_id, passage = next(iter(passages.items()))
        mappings += [
            # 1. a correct mapping with a verbatim quote
            {"requirement_id": ids[0], "chunk_id": chunk_id, "evidence_quote": _verbatim_quote(passage),
             "rationale": "Fake mapping with a real quote.", "confidence": 0.9},
            # 2. a chunk that was never retrieved
            {"requirement_id": ids[0], "chunk_id": INVENTED_CHUNK_ID, "evidence_quote": "anything",
             "rationale": "Invented chunk.", "confidence": 0.9},
            # 3. a requirement that does not exist
            {"requirement_id": INVENTED_REQ_ID, "chunk_id": chunk_id, "evidence_quote": _verbatim_quote(passage),
             "rationale": "Invented requirement.", "confidence": 0.9},
            # 4. a real requirement and chunk, but a quote that is not in the passage
            {"requirement_id": ids[-1], "chunk_id": chunk_id, "evidence_quote": FABRICATED_QUOTE,
             "rationale": "Fake mapping with an invented quote.", "confidence": 0.95},
        ]
    gaps = [{"title": "Fake gap without evidence", "explanation": "Invented obligation.",
             "requirement_ids": [INVENTED_REQ_ID], "supporting_chunk_ids": [INVENTED_CHUNK_ID]}]
    return schema.model_validate({"mappings": mappings, "gaps": gaps})


def _security(text, schema):
    ids = _requirement_ids(text)
    return schema.model_validate({
        "threats": [{"title": "Fake credential-stuffing threat", "description": "Generated by the fake model.",
                     "stride_category": "Spoofing", "severity": "High",
                     "requirement_ids": ids[:1] + [INVENTED_REQ_ID], "safeguard": "MFA"}],
        "privacy_considerations": [{"title": "Fake profiling concern", "description": "Generated by the fake model.",
                                    "requirement_ids": [INVENTED_REQ_ID], "recommendation": "Explain decisions."}],
    })


def _risk(text, schema):
    ids = _requirement_ids(text)
    return schema.model_validate({"risks": [{
        "description": "Fake vendor lock-in risk", "category": "technical",
        "likelihood": 7, "impact": 0,  # out of range on purpose -> clamped to 5 and 1
        "mitigation": "Use open standards.", "requirement_ids": ids[:1] + [INVENTED_REQ_ID]}]})


def _factor_review(text, schema):
    ids = _requirement_ids(text)
    return schema.model_validate({"adjustments": [
        {"factor_id": "legacy_dependence", "adjustment": 3,  # must be bounded to +1
         "reason": "Fake reason", "requirement_ids": ids[:1] + [INVENTED_REQ_ID]},
        {"factor_id": "system_size", "adjustment": 0, "reason": "No change", "requirement_ids": []},
    ]})


def _narrative(text, schema):
    return schema.model_validate({"justification": "Fake narrative from the model.",
                                  "trade_offs": ["Fake trade-off"],
                                  "alternatives": [{"model": "Agile", "reason": "Fake alternative note"}]})


def _workflow(text, schema):
    ids = _requirement_ids(text)
    names = re.findall(r"^- (.+?) \((?:plan|design|build|verify|accept|release|operate)\):", text, re.M)
    phases = [{"phase_name": "Imaginary Phase", "project_specific_description": "Must be ignored.",
               "additional_activities": ["Ignored"], "requirement_ids": []}]
    if names:
        phases.append({"phase_name": names[0], "project_specific_description": "Fake tailored description.",
                       "additional_activities": [f"{VALID_PHASE_MARKER} {i}" for i in range(1, 5)],  # max 3 kept
                       "requirement_ids": ids[:1] + [INVENTED_REQ_ID]})
    return schema.model_validate({"phases": phases})


def _stories(text, schema):
    items = [{"req_id": rid, "user_story": f"As a tester, I want {rid} so that it is verified.",
              "acceptance_criteria": ["Given a precondition, when an action happens, then a result follows."],
              "test_recommendations": ["Fake functional test"]}
             for rid in _requirement_ids(text) + [INVENTED_REQ_ID]]
    return schema.model_validate({"items": items})


def _use_cases(text, schema):
    ids = _requirement_ids(text)
    return schema.model_validate({"use_cases": [{
        "name": "Fake use case", "actor": "Customer", "preconditions": ["Logged in"], "main_flow": ["Step 1"],
        "alternate_flows": ["Error"], "postconditions": ["Done"], "requirement_ids": ids[:2] + [INVENTED_REQ_ID]}]})


def _summary(text, schema):
    return schema.model_validate({"summary": "Fake executive summary.",
                                  "key_findings": [f"Fake finding {i}" for i in range(1, 9)]})  # max 6 kept


def _questions(text, schema):
    """Clarification questions. Deliberately returns too many (14), one invented
    requirement ID, one duplicate, one unknown topic and a choice question with a
    single option - the app must cap, validate and repair these."""
    ids = _requirement_ids(text) or ["FR-XXX-001"]
    base = {"category": "ambiguity", "issue_type": "vague_wording", "reason": "Fake reason.", "priority": "High",
            "impact": "Fake impact.", "answer_format": "short_text", "options": [], "stakeholder_role": "Business Analyst",
            "topic": ""}
    items = [dict(base, question=f"Fake question {i} about {ids[i % len(ids)]}?", priority="Medium" if i > 5 else "High",
                  related_requirement_ids=[ids[i % len(ids)]]) for i in range(10)]
    items.append(dict(base, question="Which session timeout applies?", category="conflict", issue_type="contradiction",
                      priority="Critical", answer_format="single_choice", options=["5 minutes", "30 minutes"],
                      related_requirement_ids=ids[:2]))
    items.append(dict(base, question="Question with an invented requirement?", related_requirement_ids=["FR-ZZZ-999"],
                      topic="bogus_topic"))
    items.append(dict(base, question="Fake question 0 about " + ids[0] + "?", related_requirement_ids=[ids[0]]))
    items.append(dict(base, question="Pick one?", answer_format="single_choice", options=["Only option"],
                      related_requirement_ids=[ids[0]]))
    return schema.model_validate({"questions": items})


FACTORIES = {
    "QuestionOutput": _questions,
    "ExtractionOutput": _extraction,
    "ClassificationOutput": _classification,
    "QualityOutput": _quality,
    "ComplianceOutput": _compliance,
    "SecurityOutput": _security,
    "RiskOutput": _risk,
    "FactorReview": _factor_review,
    "Narrative": _narrative,
    "WorkflowTailoring": _workflow,
    "StoryOutput": _stories,
    "UseCaseOutput": _use_cases,
    "Summary": _summary,
}


def fake_embedding(text: str, dimensions: int = 1536) -> list[float]:
    """Deterministic bag-of-words vector: texts sharing words get similar vectors."""
    vector = [0.0] * dimensions
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % dimensions] += 1.0
    return vector


# --------------------------------------------------------------------------
# The fake client
# --------------------------------------------------------------------------
class _Responses:
    def __init__(self, owner: "FakeOpenAI"):
        self._owner = owner

    def parse(self, *, model, input, text_format):  # noqa: A002  (same keyword as the SDK)
        return self._owner._parse(model, input, text_format)


class _Embeddings:
    def __init__(self, owner: "FakeOpenAI"):
        self._owner = owner

    def create(self, *, model, input, dimensions=1536):  # noqa: A002
        return self._owner._embed(model, input, dimensions)


class FakeOpenAI:
    def __init__(self):
        self.calls: list[dict] = []  # one entry per responses.parse call
        self.embedding_calls: list[dict] = []
        self.fail_schemas: set[str] = set()
        self.refuse_schemas: set[str] = set()
        self.fail_embeddings = False
        self._lock = threading.Lock()  # agents call the client from a thread pool
        self.responses = _Responses(self)
        self.embeddings = _Embeddings(self)

    # -- helpers for assertions ------------------------------------------
    def schemas_called(self) -> set[str]:
        return {c["schema"] for c in self.calls}

    def all_prompts(self) -> str:
        return "\n".join(m["content"] for c in self.calls for m in c["input"])

    # -- implementation ---------------------------------------------------
    @staticmethod
    def _matches(name: str, names: set[str]) -> bool:
        return "*" in names or name in names

    def _parse(self, model, messages, schema):
        name = schema.__name__
        with self._lock:
            self.calls.append({"schema": name, "model": model, "input": messages})
        if self._matches(name, self.fail_schemas):
            raise openai.OpenAIError(f"simulated OpenAI outage for {name}")
        if name not in FACTORIES:
            raise AssertionError(f"The fake OpenAI client has no factory for schema {name}; add one to "
                                 "tests/fake_openai.py")
        text = _user_text(messages)
        parsed = None if self._matches(name, self.refuse_schemas) else FACTORIES[name](text, schema)
        usage = SimpleNamespace(input_tokens=len(text) // 4 + 1, output_tokens=50)
        return SimpleNamespace(output_parsed=parsed, usage=usage)

    def _embed(self, model, texts, dimensions):
        with self._lock:
            self.embedding_calls.append({"model": model, "count": len(texts), "dimensions": dimensions})
        if self.fail_embeddings:
            raise openai.OpenAIError("simulated embedding outage")
        return SimpleNamespace(data=[SimpleNamespace(embedding=fake_embedding(t, dimensions)) for t in texts])
