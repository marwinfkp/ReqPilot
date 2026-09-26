"""Project-scoped persistence for SDLC recommendations (P9; architecture G.8).

Every method authorises the actor for the action in the row's project before it
touches the session (the second layer of ADR-009). Writes need ``SDLC_RECORD``,
which only the pipeline's system actor holds; reads need ``SDLC_READ``.

There is no method that edits a factor, a candidate or a rule application, and
no delete at all: a run's inputs and ranking are history. The one mutating
method, :meth:`SdlcRunRepository.save_lifecycle`, flushes the lifecycle columns
the ORM guard (and migration 0011's trigger) allow to move.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select

from reqpilot.domain.enums import Action, ResourceType, SdlcRunStatus
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.base import utc_now
from reqpilot.domain.models.sdlc import SdlcCandidate, SdlcFactor, SdlcRuleApplication, SdlcRun
from reqpilot.repositories.base import ProjectScopedRepository


class SdlcRunRepository(ProjectScopedRepository[SdlcRun]):
    resource_type = ResourceType.SDLC_RUN

    def add(
        self,
        run: SdlcRun,
        factors: Sequence[SdlcFactor],
        candidates: Sequence[SdlcCandidate],
        rules: Sequence[SdlcRuleApplication],
    ) -> SdlcRun:
        """One complete recommendation - the run and all its rows - in one flush."""
        project_id = ProjectId(run.project_id)
        self.authorize(Action.SDLC_RECORD, project_id)
        self._session.add(run)
        self._session.flush()
        rows: list[SdlcFactor | SdlcCandidate | SdlcRuleApplication] = [
            *factors,
            *candidates,
            *rules,
        ]
        for row in rows:
            if row.project_id != project_id:  # pragma: no cover - the service sets it
                raise ValueError("an SDLC row belongs to the project of its run")
            row.sdlc_run_id = run.id
            self._session.add(row)
        self._session.flush()
        return run

    def save_lifecycle(self, run: SdlcRun) -> None:
        """Flush a lifecycle change (status, explanation, G6 binding, selection).

        Authorised as a read: the write is authorised by the recorded decision or
        the pipeline action that caused it, exactly as a gate-consequent
        transition is (P1 precedent); the ORM guard refuses anything else.
        """
        self.authorize(Action.SDLC_READ, ProjectId(run.project_id))
        run.updated_at = utc_now()
        self._session.flush()

    def get(self, project_id: ProjectId, run_id: uuid.UUID) -> SdlcRun | None:
        self.authorize(Action.SDLC_READ, project_id)
        stmt = select(SdlcRun).where(SdlcRun.id == run_id)
        return self._session.scalars(self.scoped(stmt, SdlcRun.project_id, project_id)).first()

    def list_for_project(
        self, project_id: ProjectId, *, status: SdlcRunStatus | None = None
    ) -> list[SdlcRun]:
        self.authorize(Action.SDLC_READ, project_id)
        stmt = self.scoped(select(SdlcRun), SdlcRun.project_id, project_id)
        if status is not None:
            stmt = stmt.where(SdlcRun.status == status)
        return list(self._session.scalars(stmt.order_by(SdlcRun.created_at, SdlcRun.id)))

    def factors(self, project_id: ProjectId, run_id: uuid.UUID) -> list[SdlcFactor]:
        self.authorize(Action.SDLC_READ, project_id)
        stmt = self.scoped(select(SdlcFactor), SdlcFactor.project_id, project_id).where(
            SdlcFactor.sdlc_run_id == run_id
        )
        return list(self._session.scalars(stmt))

    def candidates(self, project_id: ProjectId, run_id: uuid.UUID) -> list[SdlcCandidate]:
        self.authorize(Action.SDLC_READ, project_id)
        stmt = self.scoped(select(SdlcCandidate), SdlcCandidate.project_id, project_id).where(
            SdlcCandidate.sdlc_run_id == run_id
        )
        return list(self._session.scalars(stmt.order_by(SdlcCandidate.rank)))

    def rule_applications(
        self, project_id: ProjectId, run_id: uuid.UUID
    ) -> list[SdlcRuleApplication]:
        self.authorize(Action.SDLC_READ, project_id)
        stmt = self.scoped(
            select(SdlcRuleApplication), SdlcRuleApplication.project_id, project_id
        ).where(SdlcRuleApplication.sdlc_run_id == run_id)
        return list(
            self._session.scalars(
                stmt.order_by(SdlcRuleApplication.created_at, SdlcRuleApplication.rule_id)
            )
        )
