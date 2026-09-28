"""The audit viewer and replay (``FR-AUD-003``, ``FR-AUD-004``; architecture O.3-O.5; P11).

**Viewer.** :class:`AuditViewer` reads one project's append-only audit trail -
in chain order, filterable by subject (a requirement and all its versions, a
risk and its mitigations, or any subject), user, role, event type and time -
and returns plain read models, never ORM rows, so nothing a caller does with a
result can reach an ``audit_event`` row. The trail of a deleted project is
redacted on every read (:mod:`reqpilot.security.redaction`).

**Replay.** :class:`ReplayService` reconstructs *how a requirement or a risk
reached its current state* from the persisted events alone (O.3, O.4): each
event is applied, in chain order, to a reconstructed state, and each step keeps
the actor, time, action, subject and the change it made. The reconstruction is
then compared with the current persisted record, and every disagreement or
missing link is reported as a **gap** - a history is shown as complete only if
there are none and the project's hash chain verifies. Replay is read-only: it
writes nothing, raises no task, and moves no state.

The model's proposal and the matrix's result stay distinct in a risk's replay,
as O.4 requires: ``RISK_SEVERITY_COMPUTED`` carries the matrix version and the
lookup result, separately from what was proposed.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from reqpilot.domain.enums import Action, AuditEventType, ResourceType
from reqpilot.domain.errors import ProjectIsolationError
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.models.audit import AuditEvent
from reqpilot.domain.models.base import as_utc
from reqpilot.domain.models.identity import Project
from reqpilot.domain.models.runs import AgentRun
from reqpilot.domain.policy import Actor, ResourceRef, require
from reqpilot.repositories.requirements import RequirementRepository, RequirementVersionRepository
from reqpilot.repositories.risk import RiskMitigationRepository, RiskRepository
from reqpilot.security.redaction import redact_payload
from reqpilot.services.audit.service import AuditService

# ---------------------------------------------------------------------------
# read models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditEntry:
    """One audit event as a reader sees it (redacted when the project was deleted)."""

    id: uuid.UUID
    seq: int
    occurred_at: dt.datetime
    actor_kind: str
    actor_ref: str
    event_type: str
    subject_type: str | None
    subject_id: str | None
    subject_version: str | None
    graph_run_id: uuid.UUID | None
    agent_run_id: uuid.UUID | None
    payload: dict[str, Any]


@dataclass(frozen=True)
class AuditFilter:
    subject_type: str | None = None
    subject_id: str | None = None
    #: A requirement: its own events, its versions', and events that name them.
    requirement_id: uuid.UUID | None = None
    #: A risk: its own events, its mitigations', and events that name it.
    risk_id: uuid.UUID | None = None
    actor_ref: str | None = None
    #: A human role exercised or required, or the agent role of the agent run.
    role: str | None = None
    event_type: str | None = None
    since: dt.datetime | None = None
    until: dt.datetime | None = None


@dataclass(frozen=True)
class AuditView:
    project_id: uuid.UUID
    entries: tuple[AuditEntry, ...]
    redacted: bool
    chain_ok: bool
    first_divergence: int | None


@dataclass(frozen=True)
class ReplayStep:
    seq: int
    occurred_at: dt.datetime
    actor_kind: str
    actor_ref: str
    event_type: str
    subject_type: str | None
    subject_id: str | None
    #: What this event changed in the reconstruction (empty: related, no change).
    change: dict[str, Any]
    #: The reconstructed state after this event.
    state_after: dict[str, Any]


@dataclass(frozen=True)
class ReplayResult:
    entity_type: str
    entity_id: uuid.UUID
    project_id: uuid.UUID
    steps: tuple[ReplayStep, ...]
    reconstructed: dict[str, Any]
    #: The current persisted record, or ``None`` when it no longer exists.
    current: dict[str, Any] | None
    gaps: tuple[str, ...]
    redacted: bool
    chain_ok: bool

    @property
    def complete(self) -> bool:
        """True only when every step applied, the result matches the persisted
        record, and the chain verifies. Never assumed."""
        return not self.gaps and self.chain_ok and self.current is not None


def _mentions(value: Any, needles: set[str]) -> bool:
    if isinstance(value, str):
        return value in needles
    if isinstance(value, dict):
        return any(_mentions(v, needles) for v in value.values())
    if isinstance(value, list | tuple):
        return any(_mentions(v, needles) for v in value)
    return False


# ---------------------------------------------------------------------------
# viewer
# ---------------------------------------------------------------------------


class AuditViewer:
    """Read-only access to one project's audit trail (``AUDIT_READ``)."""

    def __init__(self, session: Session, actor: Actor) -> None:
        self._session = session
        self._actor = actor

    def authorize(self, project_id: ProjectId, action: Action = Action.AUDIT_READ) -> None:
        require(
            self._actor,
            action,
            ResourceRef(resource_type=ResourceType.AUDIT_EVENT, project_id=project_id),
        )

    def is_deleted(self, project_id: ProjectId) -> bool:
        project = self._session.get(Project, project_id)
        return project is not None and project.deleted_at is not None

    def entries(self, project_id: ProjectId) -> list[AuditEntry]:
        """Every event of the project, in chain order, redacted if it was deleted."""
        self.authorize(project_id)
        redact = self.is_deleted(project_id)
        rows = self._session.scalars(
            select(AuditEvent).where(AuditEvent.project_id == project_id).order_by(AuditEvent.seq)
        )
        return [self._entry(row, redact) for row in rows]

    def view(self, project_id: ProjectId, flt: AuditFilter | None = None) -> AuditView:
        self.authorize(project_id)
        flt = flt or AuditFilter()
        entries = self.entries(project_id)
        ok, first_bad = AuditService(self._session).verify_project_chain(project_id)
        selected = [e for e in entries if self._matches(project_id, e, flt, entries)]
        return AuditView(
            project_id=uuid.UUID(str(project_id)),
            entries=tuple(selected),
            redacted=self.is_deleted(project_id),
            chain_ok=ok,
            first_divergence=first_bad,
        )

    def verify(self, project_id: ProjectId) -> tuple[bool, int | None, int]:
        """Hash-chain verification (O.5) - the Auditor's (``AUDIT_VERIFY``)."""
        self.authorize(project_id, Action.AUDIT_VERIFY)
        ok, first_bad = AuditService(self._session).verify_project_chain(project_id)
        count = len(AuditService(self._session).list_for_project(project_id))
        return ok, first_bad, count

    # -- filters ---------------------------------------------------------------
    def _matches(
        self,
        project_id: ProjectId,
        entry: AuditEntry,
        flt: AuditFilter,
        entries: list[AuditEntry],
    ) -> bool:
        if flt.subject_type and entry.subject_type != flt.subject_type:
            return False
        if flt.subject_id and entry.subject_id != flt.subject_id:
            return False
        if flt.actor_ref and entry.actor_ref != flt.actor_ref:
            return False
        if flt.event_type and entry.event_type != flt.event_type:
            return False
        if flt.since and as_utc(entry.occurred_at) < as_utc(flt.since):
            return False
        if flt.until and as_utc(entry.occurred_at) > as_utc(flt.until):
            return False
        if flt.requirement_id is not None:
            ids = requirement_subject_ids(
                entries, flt.requirement_id, self._version_ids(project_id, flt.requirement_id)
            )
            if not _related(entry, ids):
                return False
        if flt.risk_id is not None:
            ids = {str(flt.risk_id)} | self._mitigation_ids(project_id, flt.risk_id, entries)
            if not _related(entry, ids):
                return False
        return not flt.role or self._role_matches(project_id, entry, flt.role)

    def _role_matches(self, project_id: ProjectId, entry: AuditEntry, role: str) -> bool:
        payload = entry.payload
        if role in (payload.get("role_exercised"), payload.get("required_role")):
            return True
        if entry.agent_run_id is not None:
            agent_run = self._session.get(AgentRun, entry.agent_run_id)
            return agent_run is not None and str(agent_run.role) == role
        return False

    def _version_ids(self, project_id: ProjectId, requirement_id: uuid.UUID) -> set[str]:
        # A deleted project's rows are gone (the read finds none); its trail remains.
        versions = RequirementVersionRepository(self._session, self._actor)
        return {str(v.id) for v in versions.list_for_requirement(project_id, requirement_id)}

    def _mitigation_ids(
        self, project_id: ProjectId, risk_id: uuid.UUID, entries: list[AuditEntry]
    ) -> set[str]:
        ids = {
            e.subject_id
            for e in entries
            if e.subject_type == ResourceType.RISK_MITIGATION.value
            and e.payload.get("risk_id") == str(risk_id)
            and e.subject_id
        }
        return {i for i in ids if i}

    @staticmethod
    def _entry(row: AuditEvent, redact: bool) -> AuditEntry:
        payload = dict(row.payload or {})
        return AuditEntry(
            id=row.id,
            seq=row.seq,
            occurred_at=row.occurred_at,
            actor_kind=str(row.actor_kind),
            actor_ref=row.actor_ref,
            event_type=str(row.event_type),
            subject_type=row.subject_type,
            subject_id=row.subject_id,
            subject_version=row.subject_version,
            graph_run_id=row.graph_run_id,
            agent_run_id=row.agent_run_id,
            payload=redact_payload(payload) if redact else payload,
        )


def requirement_subject_ids(
    entries: Iterable[AuditEntry], requirement_id: uuid.UUID, known_versions: set[str]
) -> set[str]:
    """The requirement's id and its versions' ids - from the record and from the
    trail itself, so a deleted requirement's versions are still found."""
    ids = {str(requirement_id), *known_versions}
    for entry in entries:
        if (
            entry.event_type == AuditEventType.REQUIREMENT_VERSION_CREATED.value
            and entry.payload.get("requirement_id") == str(requirement_id)
            and entry.subject_id
        ):
            ids.add(entry.subject_id)
    return ids


def _related(entry: AuditEntry, ids: set[str]) -> bool:
    return (entry.subject_id in ids) or _mentions(entry.payload, ids)


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


@dataclass
class _State:
    data: dict[str, Any] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)


class ReplayService:
    """Reconstruct a requirement's or a risk's history from its audit trail."""

    def __init__(self, session: Session, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._viewer = AuditViewer(session, actor)

    # -- requirement (O.3) -----------------------------------------------------
    def requirement(self, project_id: ProjectId, requirement_id: uuid.UUID) -> ReplayResult:
        self._viewer.authorize(project_id)
        deleted = self._viewer.is_deleted(project_id)
        current = None if deleted else self._current_requirement(project_id, requirement_id)
        entries = self._viewer.entries(project_id)
        known = set(current["versions"]) if current else set()
        ids = requirement_subject_ids(entries, requirement_id, known)
        related = [e for e in entries if _related(e, ids)]
        if not related and current is None:
            raise ProjectIsolationError("not found")

        state = _State({"requirement_id": str(requirement_id), "versions": {}})
        steps = [self._apply_requirement(state, e, ids) for e in related]
        versions: dict[str, dict[str, Any]] = state.data["versions"]
        if versions:
            latest = max(versions.items(), key=lambda kv: kv[1].get("version_no") or 0)
            state.data["current_version_id"] = latest[0]
        self._compare_requirement(state, current, deleted)
        return self._result("requirement", requirement_id, project_id, steps, state, current)

    def _apply_requirement(self, state: _State, e: AuditEntry, ids: set[str]) -> ReplayStep:
        versions: dict[str, dict[str, Any]] = state.data["versions"]
        p = e.payload
        change: dict[str, Any] = {}
        kind = e.event_type
        if kind == AuditEventType.REQUIREMENT_CREATED.value and e.subject_id in ids:
            change = {"human_id": p.get("human_id"), "kind": p.get("kind")}
            state.data.update({k: v for k, v in change.items() if v is not None})
        elif kind == AuditEventType.REQUIREMENT_VERSION_CREATED.value and e.subject_id in ids:
            if not isinstance(p.get("version_no"), int):
                state.gaps.append(f"event {e.seq} ({kind}) is malformed: no version number")
            else:
                versions[str(e.subject_id)] = {
                    "version_no": p["version_no"],
                    "content_hash": p.get("content_hash"),
                    "state": p.get("state"),
                }
                change = {"version": p["version_no"], "created_in": p.get("state")}
        elif (
            kind
            in (
                AuditEventType.STATE_TRANSITION.value,
                AuditEventType.REQUIREMENT_WITHDRAWN.value,
                AuditEventType.REQUIREMENT_SUPERSEDED.value,
            )
            and e.subject_type == "requirement_version"
            and e.subject_id in ids
        ):
            version = versions.get(str(e.subject_id))
            target = {
                AuditEventType.REQUIREMENT_WITHDRAWN.value: "withdrawn",
                AuditEventType.REQUIREMENT_SUPERSEDED.value: "superseded",
            }.get(kind, p.get("to"))
            source = p.get("from")
            if version is None:
                state.gaps.append(
                    f"event {e.seq} ({kind}) moves version {e.subject_version} whose creation "
                    "is not in the trail"
                )
                version = versions.setdefault(
                    str(e.subject_id),
                    {"version_no": _int(e.subject_version), "content_hash": None, "state": None},
                )
            if not isinstance(target, str):
                state.gaps.append(f"event {e.seq} ({kind}) is malformed: no target state")
            else:
                if version["state"] is None and isinstance(source, str):
                    version["state"] = source
                if isinstance(source, str) and version["state"] != source:
                    state.gaps.append(
                        f"event {e.seq}: version {version['version_no']} left {source} but the "
                        f"replay had it in {version['state']}"
                    )
                change = {"version": version["version_no"], "from": source, "to": target}
                if "via_gate" in p:
                    change["via_gate"] = p["via_gate"]
                version["state"] = target
        else:
            change = {}
        return self._step(e, change, state.data)

    def _current_requirement(
        self, project_id: ProjectId, requirement_id: uuid.UUID
    ) -> dict[str, Any] | None:
        requirement = RequirementRepository(self._session, self._actor).get(
            project_id, requirement_id
        )
        if requirement is None:
            return None
        versions = RequirementVersionRepository(self._session, self._actor).list_for_requirement(
            project_id, requirement_id
        )
        return {
            "requirement_id": str(requirement.id),
            "human_id": requirement.human_id,
            "current_version_id": str(requirement.current_version_id)
            if requirement.current_version_id
            else None,
            "versions": {
                str(v.id): {
                    "version_no": v.version_no,
                    "content_hash": v.content_hash,
                    "state": str(v.state),
                }
                for v in versions
            },
        }

    @staticmethod
    def _compare_requirement(state: _State, current: dict[str, Any] | None, deleted: bool) -> None:
        if current is None:
            state.gaps.append(
                "the project was deleted: the current record no longer exists, so the "
                "history is rebuilt from the retained, redacted audit trail only"
                if deleted
                else "the requirement no longer exists in the repository"
            )
            return
        replayed: dict[str, dict[str, Any]] = state.data["versions"]
        for vid, now in current["versions"].items():
            then = replayed.get(vid)
            if then is None:
                state.gaps.append(f"version {now['version_no']} has no creation event in the trail")
                continue
            if then["state"] is None:
                # No transition was ever recorded and the creation event predates P11's
                # "state" field: the version has not moved since it was created.
                then["state"] = now["state"]
                then["initial_state_from_record"] = True
            for key in ("version_no", "content_hash", "state"):
                if then.get(key) != now[key]:
                    state.gaps.append(
                        f"version {now['version_no']}: replayed {key} {then.get(key)!r} "
                        f"differs from the record's {now[key]!r}"
                    )
        for vid in set(replayed) - set(current["versions"]):
            state.gaps.append(f"the trail creates version {vid} that the record does not have")
        if (
            current["current_version_id"]
            and state.data.get("current_version_id") != current["current_version_id"]
        ):
            state.gaps.append("the replayed current version differs from the record's")
        if current.get("human_id") and state.data.get("human_id") not in (
            None,
            current["human_id"],
        ):
            state.gaps.append("the replayed identifier differs from the record's")

    # -- risk (O.4) --------------------------------------------------------------
    def risk(self, project_id: ProjectId, risk_id: uuid.UUID) -> ReplayResult:
        self._viewer.authorize(project_id)
        deleted = self._viewer.is_deleted(project_id)
        current = None if deleted else self._current_risk(project_id, risk_id)
        entries = self._viewer.entries(project_id)
        ids = {str(risk_id)} | self._viewer._mitigation_ids(project_id, risk_id, entries)
        if current:
            ids |= set(current["mitigations"])
        related = [e for e in entries if _related(e, ids)]
        if not related and current is None:
            raise ProjectIsolationError("not found")
        state = _State({"risk_id": str(risk_id), "mitigations": {}, "decisions": []})
        steps = [self._apply_risk(state, e, str(risk_id)) for e in related]
        self._compare_risk(state, current, deleted)
        return self._result("risk", risk_id, project_id, steps, state, current)

    def _apply_risk(self, state: _State, e: AuditEntry, risk_id: str) -> ReplayStep:
        p = e.payload
        data = state.data
        change: dict[str, Any] = {}
        kind = e.event_type
        own = e.subject_type == "risk" and e.subject_id == risk_id
        if kind == AuditEventType.RISK_RECORDED.value and own:
            change = {
                k: p[k]
                for k in ("status", "scope", "category", "severity", "likelihood", "impact")
                if k in p
            }
            if "status" not in change:
                state.gaps.append(
                    f"event {e.seq} (RISK_RECORDED) predates the recorded initial status"
                )
            data.update(change)
        elif kind == AuditEventType.RISK_SEVERITY_COMPUTED.value and own:
            change = {
                k: p[k] for k in ("likelihood", "impact", "severity", "matrix_version") if k in p
            }
            if "severity" not in change:
                state.gaps.append(f"event {e.seq} ({kind}) is malformed: no severity")
            data.update(change)
            data["severity_source"] = "matrix"
        elif kind == AuditEventType.RISK_ESCALATED.value and own:
            change = {"g8_task_id": p.get("task_id"), "blocking": p.get("blocking")}
            data.update(change)
        elif kind == AuditEventType.RISK_DECISION_RECORDED.value and own:
            target = p.get("to", p.get("status"))
            if not isinstance(target, str):
                state.gaps.append(f"event {e.seq} ({kind}) is malformed: no resulting status")
            else:
                source = p.get("from")
                if isinstance(source, str) and data.get("status") not in (None, source):
                    state.gaps.append(
                        f"event {e.seq}: the risk left {source} but the replay had it in "
                        f"{data.get('status')}"
                    )
                change = {"from": source or data.get("status"), "to": target}
                if p.get("gate"):
                    change["gate"] = p["gate"]
                    change["decision"] = p.get("decision")
                data["status"] = target
                data["decisions"] = [*data["decisions"], change]
        elif kind == AuditEventType.RISK_MITIGATION_DECIDED.value and p.get("risk_id") == risk_id:
            if not isinstance(p.get("status"), str) or not e.subject_id:
                state.gaps.append(f"event {e.seq} ({kind}) is malformed")
            else:
                change = {"mitigation": e.subject_id, "status": p["status"]}
                data["mitigations"] = {**data["mitigations"], e.subject_id: p["status"]}
        return self._step(e, change, data)

    def _current_risk(self, project_id: ProjectId, risk_id: uuid.UUID) -> dict[str, Any] | None:
        risk = RiskRepository(self._session, self._actor).get(project_id, risk_id)
        if risk is None:
            return None
        mitigations = RiskMitigationRepository(self._session, self._actor).list_for_risk(
            project_id, risk_id
        )
        return {
            "risk_id": str(risk.id),
            "status": str(risk.status),
            "severity": str(risk.severity),
            "likelihood": str(risk.likelihood),
            "impact": str(risk.impact),
            "matrix_version": risk.matrix_version,
            "category": str(risk.category),
            "scope": str(risk.scope),
            "mitigations": {str(m.id): str(m.status) for m in mitigations},
        }

    @staticmethod
    def _compare_risk(state: _State, current: dict[str, Any] | None, deleted: bool) -> None:
        if current is None:
            state.gaps.append(
                "the project was deleted: the current record no longer exists, so the "
                "history is rebuilt from the retained, redacted audit trail only"
                if deleted
                else "the risk no longer exists in the register"
            )
            return
        data = state.data
        for key in ("status", "severity", "likelihood", "impact", "matrix_version"):
            if key in data and data[key] != current[key]:
                state.gaps.append(
                    f"replayed {key} {data[key]!r} differs from the record's {current[key]!r}"
                )
            if key not in data:
                state.gaps.append(f"the trail never records the risk's {key}")
        replayed = data["mitigations"]
        for mid, status in current["mitigations"].items():
            if mid in replayed and replayed[mid] != status:
                state.gaps.append(f"mitigation {mid}: replayed {replayed[mid]} vs {status}")
            if mid not in replayed and status != "suggested":
                state.gaps.append(f"mitigation {mid} is {status} but no decision is in the trail")

    # -- shared ------------------------------------------------------------------
    @staticmethod
    def _step(e: AuditEntry, change: dict[str, Any], data: dict[str, Any]) -> ReplayStep:
        return ReplayStep(
            seq=e.seq,
            occurred_at=e.occurred_at,
            actor_kind=e.actor_kind,
            actor_ref=e.actor_ref,
            event_type=e.event_type,
            subject_type=e.subject_type,
            subject_id=e.subject_id,
            change=change,
            state_after=_snapshot(data),
        )

    def _result(
        self,
        entity: str,
        entity_id: uuid.UUID,
        project_id: ProjectId,
        steps: list[ReplayStep],
        state: _State,
        current: dict[str, Any] | None,
    ) -> ReplayResult:
        ok, first_bad = AuditService(self._session).verify_project_chain(project_id)
        gaps = list(state.gaps)
        if not ok:
            gaps.append(
                f"the project's hash chain does not verify (first divergence at {first_bad})"
            )
        return ReplayResult(
            entity_type=entity,
            entity_id=entity_id,
            project_id=uuid.UUID(str(project_id)),
            steps=tuple(steps),
            reconstructed=_snapshot(state.data),
            current=current,
            gaps=tuple(gaps),
            redacted=self._viewer.is_deleted(project_id),
            chain_ok=ok,
        )


def _snapshot(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _snapshot(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_snapshot(v) for v in value]
    return value


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
