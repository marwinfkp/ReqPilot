"""The P10 synthetic world: a high-regulation loan-origination project, through G6.

Built on the P9 world (``tests/p9_helpers.py``), which is built on the P8 world
(``tests/p8_helpers.py`` - the frozen P8-TRACE-SYNTHETIC-v1 scenario fixture, not
modified here). The difference is the baseline: P9's exit story baselines five
requirements; the P10 world baselines **every governed requirement** of the
workshop (all but L07, which the P5 conflict resolution withdraws), so the approved
set carries what a high-regulation project carries - four eligible compliance
mappings (a retention obligation, an approval checkpoint and two controls), two
HIGH risks with recorded mitigations, and derived security requirements for
authentication, transaction integrity, retention and session management - and the
latest compliance run still reports open gaps.

Everything reaches the workflow through the real P3-P9 pathways: extraction,
analysis, G2/G3/G8 decided, G4/G5 signed, G1 co-approved, the SDLC run ranked and
explained by the scripted role #10, a factor override by the analyst (which makes
the regulated V-Model first, as in the P9 exit story), and G6 co-approved by the
four roles. Nothing is a model; nothing reaches a network.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any
from unittest import mock

from sqlalchemy.orm import Session

from reqpilot.domain.enums import ApprovalTaskStatus, GraphRunStatus, Role, SdlcRunStatus
from reqpilot.domain.models.sdlc import SdlcRun
from reqpilot.domain.policy import Actor
from reqpilot.graph.sdlc_runner import SdlcRunSummary
from reqpilot.services.workflow import WorkflowService
from tests.p3_helpers import member
from tests.p8_helpers import make_p8_world
from tests.p9_helpers import P9World, ScriptedP9Model

#: Every governed requirement of the P8 workshop (L07 is withdrawn by the G4 decision).
REGULATED_KEYS = ["L01", "L02", "L03", "L04", "L05", "L06", "L08"]


@dataclass
class P10World:
    p9: P9World

    @property
    def session(self) -> Session:
        return self.p9.session

    @property
    def project_id(self) -> Any:
        return self.p9.project_id

    @property
    def analyst(self) -> Actor:
        return self.p9.p8.analyst

    @property
    def manager(self) -> Actor:
        return self.p9.p8.project_manager

    def rank(self, *, override: bool = True) -> SdlcRun:
        """A ranked, explained run awaiting G6 (optionally after the analyst's override)."""
        first = self.p9.start()
        assert first.status is GraphRunStatus.COMPLETED, first.errors
        run_id = first.sdlc_run_id
        if override:
            again = self.p9.runner().override(
                actor=self.analyst,
                project_id=self.project_id,
                run_id=run_id,
                factor="requirement_stability",
                new_score=2,
                reason="Two regulator consultations are still open (synthetic).",
                role=Role.ANALYST,
            )
            assert again.status is GraphRunStatus.COMPLETED, again.errors
            run_id = again.sdlc_run_id
        run = self.p9.service().get(self.project_id, run_id)
        assert run is not None and run.status is SdlcRunStatus.AWAITING_G6
        return run

    def approve_g6(self, run: SdlcRun) -> SdlcRun:
        for task in self.p9.g6_tasks(ApprovalTaskStatus.OPEN):
            self.p9.decide(task)
        self.session.refresh(run)
        assert run.status is SdlcRunStatus.SELECTED, run.status
        return run

    def select(self, *, override: bool = True) -> SdlcRun:
        return self.approve_g6(self.rank(override=override))

    def generate(self, run_id: uuid.UUID, actor: Actor | None = None) -> SdlcRunSummary:
        return self.p9.runner().generate_workflow(
            actor=actor or self.analyst, project_id=self.project_id, run_id=run_id
        )

    def workflows(self, actor: Actor | None = None) -> WorkflowService:
        return WorkflowService(self.session, actor or self.analyst)


def make_p10_world(
    session: Session,
    name: str = "P10 loan origination - high regulation (synthetic)",
    keys: list[str] | None = None,
) -> P10World:
    from reqpilot.domain.models.identity import Project

    # The P8 fixture is frozen (P8-TRACE-SYNTHETIC-v1), so the P9 model is
    # substituted for this one call, exactly as ``make_p9_world`` does.
    with mock.patch("tests.p8_helpers.ScriptedP8Model", ScriptedP9Model):
        p8 = make_p8_world(session, name)
    baseline_id = p8.govern_and_baseline(keys or REGULATED_KEYS, "B-REG")
    project = session.get(Project, p8.project_id)
    architect = member(
        session, project, Role.ARCHITECT, f"arch-{uuid.uuid4().hex[:6]}@example.test"
    )
    return P10World(p9=P9World(p8=p8, architect=architect, baseline_id=baseline_id))
