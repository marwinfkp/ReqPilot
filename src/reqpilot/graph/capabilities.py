"""The Coordinator mints a capability per agent invocation (architecture P.1, ``[DESIGN] D10``).

Every place a node hands the gateway to an agent role goes through
:func:`agent_gateway`: the Coordinator mints the role's token - from the static
table, for this run of this project only - and binds it to the gateway the role
receives. The role never sees the token; the gateway checks it on every model
call, and refuses a call by any other role, in any other run, or after expiry.
Nothing a model returns, and nothing in graph state, reaches this function:
its inputs are the run record's id, the run's project and a role enum the node
names in code.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from reqpilot.domain.capabilities import mint_capability
from reqpilot.domain.enums import AgentRole
from reqpilot.llm.gateway import LLMGateway


def agent_gateway(
    gateway: LLMGateway,
    *,
    run_id: uuid.UUID,
    project_id: uuid.UUID,
    role: AgentRole,
    allowlisted_sources: Iterable[str] = (),
) -> LLMGateway:
    """``gateway`` bound to a freshly minted token for ``role`` in this run."""
    token = mint_capability(
        run_id=run_id,
        project_id=project_id,
        role=role,
        allowlisted_sources=allowlisted_sources,
    )
    return gateway.with_capability(token)


def for_role(ctx: Any, role: AgentRole) -> LLMGateway:
    """The node context's gateway, bound for ``role`` in the context's run."""
    return agent_gateway(ctx.gateway, run_id=ctx.log.run.id, project_id=ctx.project_id, role=role)
