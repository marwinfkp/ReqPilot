"""Generation provenance: what produced a proposal (architecture F.1, G.7, R.2).

Defined in the domain, not the gateway, because the services that persist run
records may not import the LLM layer: the gateway produces a
:class:`ModelMeta`, and the run recorder stores it, without either depending on
the other.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


def params_hash(params: dict[str, Any]) -> str:
    blob = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ModelMeta:
    """What produced an output: provider, model, prompt, and what it cost (F.1).

    Recorded on the agent run for every invocation (``FR-AUD-001``, R.2).
    """

    provider: str
    model_id: str
    is_model: bool
    prompt_name: str
    prompt_version: str
    prompt_sha256: str
    contract_version: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    #: Provider calls made: first call, transient retries, and the one repair.
    attempts: int = 0
    repaired: bool = False
    cost_estimate: float | None = None
    params: dict[str, Any] = field(default_factory=dict)
    #: The provider's response identifiers, one per successful call, in order.
    response_ids: tuple[str, ...] = ()

    @property
    def prompt_ref(self) -> str:
        return f"{self.prompt_name}@{self.prompt_version}"

    @property
    def params_hash(self) -> str:
        return params_hash(self.params)
