"""The knowledge-base rows of the policy matrix (ADR-009).

Same single ``policy.can``; P2 only added actions to its grant table. These
tests pin who may curate the shared corpus, who may set a project's grounding
scope, and who may retrieve and read evidence - and that isolation still comes
before permission.
"""

from __future__ import annotations

import pytest
from tests.conftest import make_actor

from reqpilot.domain.enums import Action, ActorKind, ResourceType, Role
from reqpilot.domain.ids import ProjectId
from reqpilot.domain.policy import Actor, ResourceRef, can

pytestmark = pytest.mark.unit

GLOBAL = ResourceRef(resource_type=ResourceType.KNOWLEDGE_ITEM)


def scoped(project_id: ProjectId) -> ResourceRef:
    return ResourceRef(resource_type=ResourceType.SOURCE_ALLOWLIST, project_id=project_id)


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("action", [Action.KB_READ, Action.KB_ADMINISTER])
def test_only_the_kb_administrator_may_read_or_curate_the_corpus(
    role: Role, action: Action, project_id: ProjectId
) -> None:
    actor = make_actor(project_id=project_id, roles={role})
    assert can(actor, action, GLOBAL).allowed is (role is Role.KB_ADMIN)


def test_kb_administration_is_not_tied_to_one_project(
    project_id: ProjectId, other_project_id: ProjectId
) -> None:
    """The corpus is shared, so the role held in any project suffices (unscoped action)."""
    admin = make_actor(project_id=other_project_id, roles={Role.KB_ADMIN})
    assert can(admin, Action.KB_ADMINISTER, GLOBAL)
    assert can(admin, Action.KB_ADMINISTER, ResourceRef(ResourceType.KNOWLEDGE_ITEM, project_id))


def test_a_user_with_no_roles_cannot_touch_the_corpus() -> None:
    from reqpilot.domain.ids import ActorId, new_uuid

    nobody = Actor(actor_id=ActorId(new_uuid()))
    assert not can(nobody, Action.KB_READ, GLOBAL)


@pytest.mark.parametrize("role", list(Role))
def test_only_the_projects_kb_administrator_may_change_its_scope(
    role: Role, project_id: ProjectId
) -> None:
    actor = make_actor(project_id=project_id, roles={role})
    assert can(actor, Action.KB_SCOPE_MANAGE, scoped(project_id)).allowed is (role is Role.KB_ADMIN)


def test_the_analyst_cannot_widen_the_sources_that_ground_their_analysis(
    project_id: ProjectId,
) -> None:
    analyst = make_actor(project_id=project_id, roles={Role.ANALYST})
    assert can(analyst, Action.KB_RETRIEVE, scoped(project_id))
    assert not can(analyst, Action.KB_SCOPE_MANAGE, scoped(project_id))


def test_a_kb_administrator_elsewhere_cannot_touch_this_projects_scope(
    project_id: ProjectId, other_project_id: ProjectId
) -> None:
    """Isolation precedes permission, exactly as for every other scoped action."""
    elsewhere = make_actor(project_id=other_project_id, roles={Role.KB_ADMIN})
    for action in (
        Action.KB_SCOPE_MANAGE,
        Action.KB_SCOPE_READ,
        Action.KB_RETRIEVE,
        Action.EVIDENCE_READ,
    ):
        decision = can(elsewhere, action, scoped(project_id))
        assert not decision.allowed
        assert "project isolation" in decision.reason


EXPECTED = {
    Action.KB_RETRIEVE: {Role.ANALYST, Role.COMPLIANCE_OFFICER, Role.SECURITY_REVIEWER},
    Action.EVIDENCE_CREATE: {Role.ANALYST, Role.COMPLIANCE_OFFICER, Role.SECURITY_REVIEWER},
    Action.EVIDENCE_READ: {
        Role.ANALYST,
        Role.COMPLIANCE_OFFICER,
        Role.SECURITY_REVIEWER,
        Role.PROJECT_MANAGER,
        Role.AUDITOR,
    },
}


@pytest.mark.parametrize("action", list(EXPECTED))
@pytest.mark.parametrize("role", list(Role))
def test_retrieval_and_evidence_matrix(role: Role, action: Action, project_id: ProjectId) -> None:
    actor = make_actor(project_id=project_id, roles={role})
    assert can(actor, action, scoped(project_id)).allowed is (role in EXPECTED[action])


def test_the_auditor_reads_evidence_but_creates_nothing(
    auditor: Actor, project_id: ProjectId
) -> None:
    assert can(auditor, Action.EVIDENCE_READ, scoped(project_id))
    assert can(auditor, Action.KB_SCOPE_READ, scoped(project_id))
    assert not can(auditor, Action.EVIDENCE_CREATE, scoped(project_id))
    assert not can(auditor, Action.KB_RETRIEVE, scoped(project_id))


def test_an_agent_role_has_no_standing_to_curate(project_id: ProjectId) -> None:
    """An agent actor granted nothing gets nothing: deny by default."""
    agent = make_actor(project_id=project_id, roles=set(), kind=ActorKind.AGENT_ROLE)
    for action in (Action.KB_ADMINISTER, Action.KB_SCOPE_MANAGE, Action.KB_RETRIEVE):
        assert not can(agent, action, scoped(project_id))


@pytest.mark.parametrize("kind", [ActorKind.AGENT_ROLE, ActorKind.SYSTEM])
def test_curation_and_scope_are_human_only_whatever_roles_are_held(
    kind: ActorKind, project_id: ProjectId
) -> None:
    """E.1: agent roles write proposals only - holding KB_ADMIN changes nothing."""
    agent = make_actor(project_id=project_id, roles={Role.KB_ADMIN}, kind=kind)
    for action in (Action.KB_ADMINISTER, Action.KB_SCOPE_MANAGE):
        decision = can(agent, action, scoped(project_id))
        assert not decision.allowed
        assert "human decision" in decision.reason
    superuser_agent = Actor(
        actor_id=agent.actor_id,
        kind=kind,
        roles_by_project=agent.roles_by_project,
        is_superuser=True,
    )
    assert not can(superuser_agent, Action.KB_ADMINISTER, GLOBAL), "no flag overrides it"
