"""The API application (architecture ADR-002, section S).

This factory builds the **API only**. The demonstration UI is assembled on top
of it by :mod:`reqpilot.main`, which is the composition root.

Keeping the two apart is not tidiness for its own sake: the layered dependency
rule runs ``web → api → services → repositories → domain``, one way. If this
module imported the web package the direction would reverse and the contract
would - correctly - fail.
"""

from __future__ import annotations

from fastapi import FastAPI

from reqpilot import __version__
from reqpilot.api.errors import install_error_handlers
from reqpilot.api.routes import (
    compliance,
    elicitation,
    extraction,
    governance,
    health,
    knowledge,
    quality,
    requirements,
    risk,
    traceability,
)

DESCRIPTION = (
    "Agentic requirements engineering and SDLC recommendation assistant. "
    "Roadmap phases P1 (requirements repository: deterministic system of record, "
    "immutable versions, lifecycle, G1 approval, baselines) and P2 (knowledge base "
    "and retrieval: typed curated corpus, allowlisted hybrid retrieval, evidence, "
    "citations) and P3 (batch extraction and classification: every model call through "
    "one gateway, typed proposals, deterministic validation, a review queue that is "
    "not approval) and P4 (elicitation and clarification: adaptive, role-specific "
    "interviews on a checkpointed LangGraph interrupt/resume loop with deterministic "
    "coverage and bounded follow-ups; clarifications bound to a requirement and a defect, "
    "whose answers create a new requirement version) and P5 (quality and conflict "
    "detection: deterministic quality rules plus validated model proposals, a bounded "
    "conflict shortlist before any pairwise model call, conflicts as transition guards "
    "that only a human resolves) and P6 (compliance and security analysis: evidence-grounded "
    "candidate mappings whose every citation resolves, rule-engine gap detection, deterministic "
    "mandated-language enforcement and a standing advisory notice, derived security and privacy "
    "requirements with a deterministic authoritative risk level, and blocking G2/G3 gates that "
    "only the Compliance Officer or Security Reviewer decides) and P7 (risk analysis: a "
    "deterministic severity matrix, a risk register, and a blocking G8 gate) and P8 (approval, "
    "traceability and documents: gates G4, G5 and G7 through the one approval service, baseline "
    "readiness enforced at G1, a typed append-only trace graph, the RTM and coverage (E6), and "
    "deterministic, versioned SRS, RTM, risk register, user stories, use cases, compliance "
    "matrix, assumptions/dependency register and open-issues list, exported as Markdown and "
    "DOCX, generated only from an approved baseline). Model calls go to the configured "
    "provider: the offline stub by default, "
    "or OpenAI when LLM_PROVIDER=openai."
)


def create_app() -> FastAPI:
    """Build the API application.

    A factory rather than a module-level singleton so tests can construct an app
    with overridden dependencies without mutating global state.
    """
    app = FastAPI(title="ReqPilot", version=__version__, description=DESCRIPTION)
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(requirements.router)
    app.include_router(governance.router)
    app.include_router(knowledge.router)
    app.include_router(extraction.router)
    app.include_router(elicitation.router)
    app.include_router(quality.router)
    app.include_router(compliance.router)
    app.include_router(risk.router)
    app.include_router(traceability.router)
    return app


app = create_app()
