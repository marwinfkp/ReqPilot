"""Which exact requirement versions an artefact or a coverage report is about.

Two scopes, both computed from persisted rows only:

* **Baseline scope** - *the requirement set in force as of baseline B*. P1
  baselines are incremental: each G1 submission freezes the versions it
  contained, so a later baseline holding one changed requirement does not
  repeat the unchanged ones. The set in force as of B is therefore, for each
  requirement, its member version in the most recent baseline at or before B
  (ordered by ``frozen_at``, then id). Every version in it is a real
  ``baseline_member`` row bound to an approval decision - this is a projection
  over existing baselines, **not** a second baseline concept, and it never
  includes a version that did not itself pass G1 and enter a baseline. A
  requirement's newer, unapproved version cannot appear in it: only baseline
  members can (``FR-HIL-004``).
* **Project scope** - the current version of every requirement that is still
  live (not withdrawn or invalid). Used for coverage of work in progress; it
  is never used to render an authoritative artefact.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from reqpilot.domain.enums import Action, ResourceType
from reqpilot.domain.errors import ArtifactError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.lifecycle import RequirementState
from reqpilot.domain.models.base import as_utc
from reqpilot.domain.models.baseline import Baseline, BaselineMember
from reqpilot.domain.models.requirements import Requirement, RequirementVersion
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.repositories.baseline import BaselineRepository
from reqpilot.repositories.requirements import (
    RequirementRepository,
    RequirementVersionRepository,
)


@dataclass(frozen=True)
class ScopedVersion:
    requirement: Requirement
    version: RequirementVersion
    #: The baseline whose member row admits this version (baseline scope only).
    baseline: Baseline | None
    member: BaselineMember | None

    @property
    def human_id(self) -> str:
        return self.requirement.human_id


@dataclass(frozen=True)
class RequirementScope:
    kind: str
    project_id: ProjectId
    items: tuple[ScopedVersion, ...]
    baseline: Baseline | None = None
    #: Baselines contributing members, oldest first (baseline scope only).
    contributing: tuple[Baseline, ...] = ()

    @property
    def version_ids(self) -> frozenset[uuid.UUID]:
        return frozenset(i.version.id for i in self.items)

    def item(self, version_id: uuid.UUID) -> ScopedVersion | None:
        return next((i for i in self.items if i.version.id == version_id), None)


def _baseline_order(b: Baseline) -> tuple[str, str]:
    return (as_utc(b.frozen_at).isoformat(), str(b.id))


class ScopeService:
    def __init__(self, session: Session, actor: Actor) -> None:
        self._actor = actor
        self._baselines = BaselineRepository(session, actor)
        self._versions = RequirementVersionRepository(session, actor)
        self._requirements = RequirementRepository(session, actor)

    def baseline_scope(self, project_id: ProjectId, baseline_id: uuid.UUID) -> RequirementScope:
        require(
            self._actor,
            Action.BASELINE_READ,
            ResourceRef(resource_type=ResourceType.BASELINE, project_id=project_id),
        )
        target = self._baselines.get(project_id, baseline_id)
        if target is None:
            raise ArtifactError("baseline not found in this project")
        ordered = sorted(self._baselines.list_for_project(project_id), key=_baseline_order)
        cutoff = _baseline_order(target)
        chosen: dict[uuid.UUID, tuple[RequirementVersion, Baseline, BaselineMember]] = {}
        contributing: list[Baseline] = []
        for baseline in ordered:
            if _baseline_order(baseline) > cutoff:
                break
            members = self._baselines.list_members(project_id, baseline.id)
            if members:
                contributing.append(baseline)
            for member in members:
                version = self._versions.get(project_id, member.requirement_version_id)
                if version is None:  # pragma: no cover - foreign key
                    continue
                chosen[version.requirement_id] = (version, baseline, member)
        items: list[ScopedVersion] = []
        for requirement_id, (version, baseline, member) in chosen.items():
            requirement = self._requirements.get(project_id, requirement_id)
            if requirement is None:  # pragma: no cover - foreign key
                continue
            items.append(ScopedVersion(requirement, version, baseline, member))
        items.sort(key=lambda i: (i.human_id, i.version.version_no))
        return RequirementScope(
            kind="baseline",
            project_id=project_id,
            items=tuple(items),
            baseline=target,
            contributing=tuple(contributing),
        )

    def project_scope(self, project_id: ProjectId) -> RequirementScope:
        items: list[ScopedVersion] = []
        for requirement in self._requirements.list_for_project(project_id):
            if requirement.current_version_id is None:
                continue
            version = self._versions.get(project_id, requirement.current_version_id)
            if version is None or version.state in (
                RequirementState.WITHDRAWN,
                RequirementState.INVALID,
            ):
                continue
            items.append(ScopedVersion(requirement, version, None, None))
        items.sort(key=lambda i: (i.human_id, i.version.version_no))
        return RequirementScope(kind="project", project_id=project_id, items=tuple(items))
