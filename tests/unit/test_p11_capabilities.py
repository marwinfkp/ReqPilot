"""P11 unit tests: agent capability tokens (architecture P.1, ``[DESIGN] D10``, E.1).

The per-role capability matrix is asserted *through the policy* - the function
every repository read and write calls - for every agent role, every resource
type and every kind of access, and through the gateway for every model call.
"""

from __future__ import annotations

import dataclasses
import json
import time
import uuid

import pytest
from tests.p11_helpers import agent, token_for

from reqpilot.agents.contracts.classification import ClassificationOutput
from reqpilot.config import Settings
from reqpilot.domain.capabilities import (
    NEVER_MINTED,
    NEVER_WRITABLE,
    ROLE_CAPABILITIES,
    Access,
    CapabilityToken,
    mint_capability,
    model_call_problem,
    token_problem,
)
from reqpilot.domain.enums import Action, ActorKind, AgentRole, Gate, ResourceType, Role
from reqpilot.domain.errors import (
    AuthorizationError,
    CapabilityEgressError,
    CapabilityError,
    EgressRefusedError,
    ProjectIsolationError,
)
from reqpilot.domain.ids import ActorId, ProjectId
from reqpilot.domain.policy import Actor, ResourceRef, can, require
from reqpilot.llm import ContentBlock, LLMGateway, ScriptedProvider, TrustClass

pytestmark = pytest.mark.unit

SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]
LABELS = json.dumps({"labels": [{"category": "security", "review_signal": 0.8, "rationale": "r"}]})
MINTABLE = [r for r in AgentRole if r not in NEVER_MINTED]

#: One read action and one write action per resource type, for the matrix.
READ_ACTION = Action.REQUIREMENT_READ
WRITE_ACTION = Action.REQUIREMENT_CREATE


# --- the static table (E.1) ----------------------------------------------------------


def test_the_table_is_the_e1_matrix_read_column_wise() -> None:
    assert set(ROLE_CAPABILITIES) == set(AgentRole) - {AgentRole.HUMAN_APPROVAL}
    retrieving = {r for r, c in ROLE_CAPABILITIES.items() if c.may_retrieve}
    assert retrieving == {AgentRole.COMPLIANCE, AgentRole.SECURITY_PRIVACY}, "exactly two retrieve"
    llm = {r for r, c in ROLE_CAPABILITIES.items() if c.may_call_model}
    assert len(llm) == 10, "E.0: ten roles use an LLM"
    assert AgentRole.COORDINATOR not in llm and AgentRole.VALIDATION not in llm
    writers = {r: c.may_write for r, c in ROLE_CAPABILITIES.items() if c.may_write}
    assert writers == {
        AgentRole.COORDINATOR: frozenset({ResourceType.GRAPH_RUN}),
        AgentRole.VALIDATION: frozenset({ResourceType.REVIEW_ITEM}),
    }, "every LLM role proposes only"
    for row in ROLE_CAPABILITIES.values():
        assert not row.may_write & NEVER_WRITABLE
        assert ResourceType.APPROVAL_TASK not in row.may_write
        assert ResourceType.AUDIT_EVENT not in row.may_read


def test_human_approval_is_never_minted() -> None:
    with pytest.raises(ValueError, match="no capability is ever minted"):
        mint_capability(run_id=uuid.uuid4(), project_id=uuid.uuid4(), role=AgentRole.HUMAN_APPROVAL)


def test_only_a_retrieving_role_holds_an_allowlist() -> None:
    mint_capability(
        run_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        role=AgentRole.COMPLIANCE,
        allowlisted_sources=["src-1"],
    )
    with pytest.raises(ValueError, match="does not retrieve"):
        mint_capability(
            run_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            role=AgentRole.RISK_ANALYSIS,
            allowlisted_sources=["src-1"],
        )


# --- a token cannot be forged, widened, reused or kept -------------------------------------


def test_a_token_is_immutable() -> None:
    token = token_for(AgentRole.CLASSIFICATION)
    with pytest.raises(dataclasses.FrozenInstanceError):
        token.may_write = frozenset({ResourceType.APPROVAL_TASK})  # type: ignore[misc]


def test_a_widened_copy_does_not_verify() -> None:
    token = token_for(AgentRole.CLASSIFICATION)
    assert token_problem(token) is None
    for change in (
        {"may_write": frozenset({ResourceType.REQUIREMENT})},
        {"may_read": token.may_read | {ResourceType.RISK}},
        {"may_retrieve": True},
        {"role": AgentRole.COMPLIANCE},
        {"project_id": str(uuid.uuid4())},
        {"run_id": str(uuid.uuid4())},
        {"expires_at": token.expires_at + 3600},
    ):
        forged = dataclasses.replace(token, **change)
        assert "seal" in (token_problem(forged) or ""), change


def test_a_token_built_from_data_does_not_verify() -> None:
    """What a model or a client could supply: every field, and a made-up seal."""
    real = token_for(AgentRole.CLASSIFICATION)
    fields = {f.name: getattr(real, f.name) for f in dataclasses.fields(real)}
    fields["seal"] = "0" * 64
    assert "seal" in (token_problem(CapabilityToken(**fields)) or "")
    assert token_problem({"role": "classification"}) is not None
    assert token_problem(None) == "no capability token was presented"

    class Imitation(CapabilityToken):
        pass

    assert token_problem(Imitation(**{**fields, "seal": real.seal})) is not None


def test_a_sealed_token_granting_more_than_its_row_is_refused(monkeypatch) -> None:
    """Even a correctly sealed token is re-checked against the static table."""
    import reqpilot.domain.capabilities as caps

    token = token_for(AgentRole.CLASSIFICATION)
    widened = dict(caps.ROLE_CAPABILITIES)
    widened[AgentRole.CLASSIFICATION] = caps.RoleCapability(
        token.may_read, frozenset(), False, True
    )
    assert token_problem(token) is None
    monkeypatch.setattr(
        caps,
        "ROLE_CAPABILITIES",
        {
            **widened,
            AgentRole.CLASSIFICATION: caps.RoleCapability(frozenset(), frozenset(), False, True),
        },
    )
    assert "more than its role" in (token_problem(token) or "")


def test_expired_and_future_dated_tokens_are_refused() -> None:
    old = mint_capability(
        run_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        role=AgentRole.CLASSIFICATION,
        ttl_seconds=10,
        now=time.time() - 60,
    )
    assert "expired" in (token_problem(old) or "")
    future = mint_capability(
        run_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        role=AgentRole.CLASSIFICATION,
        now=time.time() + 3600,
    )
    assert "future" in (token_problem(future) or "")


def test_the_seal_is_never_printed() -> None:
    token = token_for(AgentRole.CLASSIFICATION)
    assert token.seal not in repr(token)


def test_only_an_agent_actor_carries_a_token() -> None:
    token = token_for(AgentRole.CLASSIFICATION)
    for kind in (ActorKind.HUMAN, ActorKind.SYSTEM):
        with pytest.raises(ValueError, match="only an agent-role actor"):
            Actor(actor_id=ActorId(uuid.uuid4()), kind=kind, capability=token)


# --- the per-role matrix, through the policy ----------------------------------------------------


def _ref(project: uuid.UUID, resource_type: ResourceType, resource_id: str | None = None):
    return ResourceRef(
        resource_type=resource_type, project_id=ProjectId(project), resource_id=resource_id
    )


@pytest.mark.parametrize("role", MINTABLE, ids=lambda r: r.value)
def test_every_role_reads_and_writes_exactly_its_row(role: AgentRole) -> None:
    project = uuid.uuid4()
    row = ROLE_CAPABILITIES[role]
    bot = agent(project, token_for(role, project))
    for resource_type in ResourceType:
        read = can(bot, READ_ACTION, _ref(project, resource_type)).allowed
        write = can(bot, WRITE_ACTION, _ref(project, resource_type)).allowed
        assert read == (resource_type in row.may_read), (role, resource_type, "read")
        assert write == (resource_type in row.may_write), (role, resource_type, "write")
    retrieve = can(bot, Action.KB_RETRIEVE, _ref(project, ResourceType.KNOWLEDGE_CHUNK)).allowed
    assert retrieve == row.may_retrieve


@pytest.mark.parametrize("role", MINTABLE, ids=lambda r: r.value)
def test_no_token_ever_reaches_a_gate_or_a_human_decision(role: AgentRole) -> None:
    project = uuid.uuid4()
    bot = agent(project, token_for(role, project), *Role)  # human roles too: irrelevant
    for gate in Gate:
        for held in Role:
            ref = ResourceRef(
                resource_type=ResourceType.APPROVAL_TASK,
                project_id=ProjectId(project),
                gate=gate,
                role_exercised=held,
            )
            assert not can(bot, Action.APPROVAL_DECIDE, ref).allowed
    for action in (
        Action.REQUIREMENT_SUBMIT,
        Action.BASELINE_CREATE,
        Action.RISK_MANAGE,
        Action.PROJECT_DELETE,
        Action.MEMBER_ADD,
        Action.KB_SCOPE_MANAGE,
        Action.AUDIT_READ,
    ):
        assert not can(bot, action, _ref(project, ResourceType.PROJECT)).allowed, action


def test_a_token_opens_nothing_in_another_project_or_another_run() -> None:
    here, there, run = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    bot = agent(here, token_for(AgentRole.COORDINATOR, here, run))
    assert can(bot, Action.RUN_READ, _ref(here, ResourceType.GRAPH_RUN, str(run))).allowed
    with pytest.raises(ProjectIsolationError):
        require(bot, Action.RUN_READ, _ref(there, ResourceType.GRAPH_RUN, str(run)))
    with pytest.raises(CapabilityError, match="another run"):
        require(bot, Action.RUN_READ, _ref(here, ResourceType.GRAPH_RUN, str(uuid.uuid4())))


def test_an_agent_without_a_token_holds_nothing_whatever_its_roles() -> None:
    project = uuid.uuid4()
    for bot in (
        agent(project, None, *Role),
        Actor(
            actor_id=ActorId(uuid.uuid4()),
            kind=ActorKind.AGENT_ROLE,
            roles_by_project={ProjectId(project): frozenset(Role)},
            is_superuser=True,
        ),
    ):
        decision = can(bot, READ_ACTION, _ref(project, ResourceType.REQUIREMENT_VERSION))
        assert not decision.allowed and "no capability token" in decision.reason
        with pytest.raises(CapabilityError):
            require(bot, READ_ACTION, _ref(project, ResourceType.REQUIREMENT_VERSION))


def test_capability_denials_are_authorization_errors() -> None:
    """So every handler that audits PERMISSION_DENIED audits these too."""
    assert issubclass(CapabilityError, AuthorizationError)
    assert issubclass(CapabilityEgressError, CapabilityError)
    assert issubclass(CapabilityEgressError, EgressRefusedError)


# --- the gateway: a model call needs the calling role's own token ---------------------------


def _classify(gateway: LLMGateway):
    return gateway.generate(
        role=AgentRole.CLASSIFICATION,
        prompt_name="requirement_classification",
        params={},
        content=[
            ContentBlock("requirement", "The system shall log in.", TrustClass.PROJECT_CONTENT)
        ],
        schema=ClassificationOutput,
    )


def _gateway() -> tuple[LLMGateway, ScriptedProvider]:
    provider = ScriptedProvider.queue([LABELS])
    return LLMGateway(provider, settings=SETTINGS, sleep=lambda _s: None), provider


def test_the_calling_roles_token_opens_the_gateway() -> None:
    gateway, provider = _gateway()
    result = _classify(gateway.with_capability(token_for(AgentRole.CLASSIFICATION)))
    assert result.ok and len(provider.requests) == 1


@pytest.mark.parametrize(
    "token",
    [
        None,
        "classification",
        token_for(AgentRole.COMPLIANCE),  # another role's token
        token_for(AgentRole.COORDINATOR),  # a role that makes no model call
        dataclasses.replace(token_for(AgentRole.CLASSIFICATION), run_id="forged"),
    ],
    ids=["none", "a-string", "another-role", "no-model-role", "altered"],
)
def test_anything_else_is_refused_before_the_provider(token) -> None:
    gateway, provider = _gateway()
    bound = gateway if token is None else gateway.with_capability(token)
    with pytest.raises(CapabilityEgressError):
        _classify(bound)
    assert provider.requests == [], "nothing reached a provider"
    assert model_call_problem(token, AgentRole.CLASSIFICATION) is not None


def test_binding_survives_the_usage_ledger_and_is_the_only_way_in() -> None:
    from reqpilot.llm import UsageLedger

    gateway, _provider = _gateway()
    bound = gateway.with_capability(token_for(AgentRole.CLASSIFICATION)).with_usage(UsageLedger())
    assert bound.capability is not None and _classify(bound).ok
    assert gateway.capability is None, "binding returns a new gateway; the original is unbound"
    assert Access.READ.value == "read"
