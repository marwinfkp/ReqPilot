"""Turning one extraction run into E1 predictions (architecture R.1, R.2).

Reads what the run persisted - the requirement versions its accepted proposals
became, their source spans, and the provider, model and prompt of every
extraction call - through the project-scoped repositories. A version's spans are
attributed to a gold transcript when its source document is that transcript:
the document's content hash equals the hash of the transcript's normalised text.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from reqpilot.domain.enums import CandidateStatus
from reqpilot.domain.errors import EvaluationError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.policy import Actor
from reqpilot.repositories.extraction import (
    CandidateRepository,
    RunRepository,
    SourceDocumentRepository,
)
from reqpilot.repositories.requirements import RequirementRepository, RequirementVersionRepository
from reqpilot.services.evaluation.extraction_eval import GoldSet, Prediction, RunFacts

EXTRACTION_NODE = "extract_requirements"


def predictions_for_run(
    session: Session, actor: Actor, project_id: ProjectId, run_id: uuid.UUID, gold: GoldSet
) -> tuple[list[Prediction], RunFacts]:
    runs = RunRepository(session, actor)
    run = runs.get_run(project_id, run_id)
    if run is None:
        raise EvaluationError("run not found in this project")

    providers: set[str] = set()
    models: set[str] = set()
    prompts: set[str] = set()
    all_real = True
    extraction_calls = 0
    for agent_run in runs.agent_runs(project_id, run.id):
        if agent_run.node != EXTRACTION_NODE:
            continue
        extraction_calls += 1
        model = (
            runs.get_model_version(project_id, uuid.UUID(agent_run.model_version_id))
            if agent_run.model_version_id
            else None
        )
        template = (
            runs.get_prompt_template(project_id, uuid.UUID(agent_run.prompt_template_id))
            if agent_run.prompt_template_id
            else None
        )
        if model is None or not model.is_model:
            all_real = False
        if model is not None:
            providers.add(model.provider)
            models.add(model.model_id)
        if template is not None:
            prompts.add(f"{template.name}@{template.version}")
    if extraction_calls == 0:
        all_real = False

    by_hash = {digest: name for name, digest in gold.transcript_hashes.items()}
    documents = SourceDocumentRepository(session, actor)
    versions = RequirementVersionRepository(session, actor)
    requirements = RequirementRepository(session, actor)
    predictions: list[Prediction] = []
    for candidate in CandidateRepository(session, actor).list_for_run(project_id, run.id):
        if (
            candidate.status is not CandidateStatus.ACCEPTED
            or candidate.requirement_version_id is None
        ):
            continue
        version = versions.get(project_id, candidate.requirement_version_id)
        if version is None:  # pragma: no cover - FK integrity
            continue
        requirement = requirements.get(project_id, version.requirement_id)
        transcript = ""
        spans: list[tuple[int, int]] = []
        for ref in version.source_refs or []:
            if not ref.get("document"):
                continue  # an interview utterance (P4) is never a benchmark transcript
            document = documents.get(project_id, uuid.UUID(str(ref["document"])))
            name = by_hash.get(document.content_hash) if document else None
            if name is not None:
                transcript = name
                spans.append((int(ref["span"][0]), int(ref["span"][1])))
        predictions.append(
            Prediction(
                ref=requirement.human_id if requirement else str(version.id),
                statement=version.statement,
                transcript=transcript,
                spans=tuple(spans),
            )
        )

    facts = RunFacts(
        run_id=str(run.id),
        providers=tuple(sorted(providers)),
        models=tuple(sorted(models)),
        prompt_versions=tuple(sorted(prompts)),
        all_real_models=all_real,
    )
    return predictions, facts
