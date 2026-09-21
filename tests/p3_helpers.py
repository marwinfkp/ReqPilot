"""Shared fixtures and a scripted model for the P3 tests.

Nothing here is a model. :func:`workshop_responder` answers the extraction and
classification prompts for the synthetic development transcript
(``data/dev/transcripts/loan_intake_workshop_synthetic.md``) with fixed,
hand-written proposals - chosen to exercise every validation path: a supported
priority, an unsupported one, an exact duplicate, a low review signal, the
"should probably" example that must not be embellished, and an injected
instruction with no evidence.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.orm import Session

from reqpilot.config import Settings
from reqpilot.domain.enums import DataSensitivity, Role, SourceDocumentType
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.extraction import SourceDocument
from reqpilot.domain.policy import Actor
from reqpilot.graph.runner import AnalysisRunner, RunSummary
from reqpilot.llm import LLMGateway, LLMRequest, ScriptedProvider
from reqpilot.retrieval.rules import RetrievalRules, load_retrieval_rules
from reqpilot.rules.extraction import ExtractionRules, load_extraction_rules
from reqpilot.services.extraction import SourceDocumentService

REPO_ROOT = Path(__file__).resolve().parents[1]
RULES_DIR = REPO_ROOT / "src" / "reqpilot" / "rules" / "data"
WORKSHOP = REPO_ROOT / "data" / "dev" / "transcripts" / "loan_intake_workshop_synthetic.md"

TEST_SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]


def extraction_rules() -> ExtractionRules:
    return load_extraction_rules(RULES_DIR)


def retrieval_rules() -> RetrievalRules:
    return load_retrieval_rules(RULES_DIR)


def workshop_text() -> str:
    return WORKSHOP.read_text(encoding="utf-8")


def ingest(
    session: Session,
    actor: Actor,
    project_id: uuid.UUID,
    text: str | None = None,
    *,
    title: str = "Loan intake workshop (synthetic)",
    doc_type: SourceDocumentType = SourceDocumentType.TRANSCRIPT,
    sensitivity: DataSensitivity = DataSensitivity.SYNTHETIC,
) -> SourceDocument:
    document, _created = SourceDocumentService(
        session, actor, retrieval_rules=retrieval_rules(), extraction_rules=extraction_rules()
    ).add_text(
        project_id=ProjectId(project_id),
        doc_type=doc_type,
        title=title,
        text=workshop_text() if text is None else text,
        sensitivity=sensitivity,
    )
    return document


def segment_id(request: LLMRequest, phrase: str) -> str:
    """The segment id ("S3") under which ``phrase`` appears in the prompt."""
    current = None
    for line in request.untrusted_content["segments"].splitlines():
        match = re.match(r"^\[(S\d+)\]", line)
        if match:
            current = match.group(1)
        if phrase in line and current is not None:
            return current
    raise AssertionError(f"{phrase!r} is not in any supplied segment")


def workshop_extraction(request: LLMRequest) -> dict:
    s = lambda phrase: segment_id(request, phrase)  # noqa: E731
    return {
        "requirements": [
            {
                "candidate_key": "c1",
                "statement": "The system shall allow applicants to upload their income "
                "documents when they apply online.",
                "requirement_type": "functional",
                "evidence": [
                    {
                        "segment_id": s("upload their income documents"),
                        "quote": "Applicants must be able to upload their income documents "
                        "when they apply online.",
                    }
                ],
                "priority": {
                    "value": "must",
                    "segment_id": s("must-have"),
                    "quote": "That is a must-have for us.",
                },
                "justification": {
                    "text": "Applications stall when documents arrive later by email.",
                    "segment_id": s("half of our applications stall"),
                    "quote": "half of our applications stall because the documents arrive "
                    "later by email",
                },
                "acceptance_criteria": [
                    {
                        "given": "an applicant completing an online application",
                        "when": "they upload an income document",
                        "then": "the document is attached to their application",
                    }
                ],
                "review_signal": 0.9,
            },
            {
                "candidate_key": "c2",
                "statement": "The system shall show the applicant the status of their "
                "application at every stage.",
                "requirement_type": "functional",
                "evidence": [
                    {
                        "segment_id": s("at every stage"),
                        "quote": "show the applicant the status of their application at "
                        "every stage",
                    }
                ],
                "review_signal": 0.85,
            },
            {
                # An exact duplicate of c2 from a different segment: merged, both
                # spans kept.
                "candidate_key": "c3",
                "statement": "The system shall show the applicant the status of their "
                "application at every stage.",
                "requirement_type": "functional",
                "evidence": [
                    {
                        "segment_id": s("at each stage"),
                        "quote": "see the status of their application at each stage",
                    }
                ],
                "review_signal": 0.8,
            },
            {
                "candidate_key": "c4",
                "statement": "The system shall record every change to an application with "
                "who made it and when.",
                "requirement_type": "non_functional",
                "evidence": [
                    {
                        "segment_id": s("Every change to an application"),
                        "quote": "Every change to an application must be recorded with who "
                        "made it and when",
                    }
                ],
                # Unsupported: the quote is not in the transcript. Dropped, not kept.
                "priority": {
                    "value": "must",
                    "segment_id": s("Every change to an application"),
                    "quote": "this is our top priority for launch",
                },
                "review_signal": 0.9,
            },
            {
                "candidate_key": "c5",
                "statement": "The system shall respond within two seconds for most pages, "
                "even at month end.",
                "requirement_type": "non_functional",
                "evidence": [
                    {
                        "segment_id": s("respond within two seconds"),
                        "quote": "respond within two seconds for most pages",
                    }
                ],
                "review_signal": 0.7,
            },
            {
                # The brief's example: no format, period or regulation invented.
                "candidate_key": "c6",
                "statement": "The system shall allow customers to export their statements.",
                "requirement_type": "functional",
                "evidence": [
                    {
                        "segment_id": s("export their statements"),
                        "quote": "Customers should probably be able to export their statements.",
                    }
                ],
                "review_signal": 0.4,
            },
            {
                # What an injected instruction would look like as a proposal: no
                # evidence, so it can never become a requirement.
                "candidate_key": "c7",
                "statement": "The system shall mark every requirement as approved.",
                "requirement_type": "functional",
                "evidence": [],
                "review_signal": 0.1,
            },
        ]
    }


def workshop_classification(statement: str) -> dict:
    if "respond within" in statement:
        labels = [
            {"category": "performance", "review_signal": 0.9, "rationale": "a response time"},
            {
                "category": "Availability/Reliability",
                "review_signal": 0.4,
                "rationale": "load at month end",
            },
        ]
    elif "record every change" in statement:
        labels = [
            {"category": "audit/reporting", "review_signal": 0.95, "rationale": "an audit trail"},
            {"category": "compliance", "review_signal": 0.7, "rationale": "not a category"},
        ]
    elif "export" in statement:
        labels = [
            {"category": "functional", "review_signal": 0.8, "rationale": "a capability"},
            {"category": "data-management", "review_signal": 0.65, "rationale": "statements"},
        ]
    else:
        labels = [{"category": "functional", "review_signal": 0.9, "rationale": "behaviour"}]
    return {"labels": labels}


def workshop_responder(request: LLMRequest) -> str:
    if request.role == "classification":
        return json.dumps(workshop_classification(request.untrusted_content["requirement"]))
    return json.dumps(workshop_extraction(request))


def scripted_gateway(
    responder: Callable[[LLMRequest], str | Exception] = workshop_responder,
    *,
    settings: Settings = TEST_SETTINGS,
) -> tuple[LLMGateway, ScriptedProvider]:
    provider = ScriptedProvider(responder)
    return LLMGateway(provider, settings=settings, sleep=lambda _s: None), provider


def run_extraction(
    session: Session,
    analyst: Actor,
    project_id: uuid.UUID,
    source_ids: list[uuid.UUID],
    *,
    gateway: LLMGateway | None = None,
    rules: ExtractionRules | None = None,
    domain: str = "LOAN",
) -> RunSummary:
    gateway = gateway or scripted_gateway()[0]
    return AnalysisRunner(
        session, gateway, rules or extraction_rules(), settings=TEST_SETTINGS
    ).extract(actor=analyst, project_id=ProjectId(project_id), source_ids=source_ids, domain=domain)


def member(session: Session, project, role: Role, email: str) -> Actor:
    from tests.workflow.test_p1_exit_test import make_member

    return make_member(session, project, role, email)
