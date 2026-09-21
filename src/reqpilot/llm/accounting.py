"""Token and cost accounting hooks (architecture ADR-006; Phase 0 ET-08).

The gateway reports every structured call's :class:`ModelMeta` to a sink.
:class:`UsageLedger` is the in-memory sink a run uses to total its cost; the
same metadata is persisted on each agent run.

Prices are configuration (``LLM_PRICE_INPUT_PER_1K`` / ``LLM_PRICE_OUTPUT_PER_1K``):
they belong to whichever model is configured, and ReqPilot assumes none. With no
price configured the estimate is ``None`` - unknown - rather than a made-up number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from reqpilot.llm.types import ModelMeta


def estimate_cost(
    tokens_in: int,
    tokens_out: int,
    *,
    price_in_per_1k: float | None,
    price_out_per_1k: float | None,
) -> float | None:
    if price_in_per_1k is None or price_out_per_1k is None:
        return None
    return round(tokens_in / 1000 * price_in_per_1k + tokens_out / 1000 * price_out_per_1k, 6)


class UsageSink(Protocol):
    def record(self, meta: ModelMeta) -> None: ...


@dataclass
class UsageLedger:
    """Collects the metadata of every call made during one run."""

    entries: list[ModelMeta] = field(default_factory=list)

    def record(self, meta: ModelMeta) -> None:
        self.entries.append(meta)

    @property
    def tokens_in(self) -> int:
        return sum(m.tokens_in for m in self.entries)

    @property
    def tokens_out(self) -> int:
        return sum(m.tokens_out for m in self.entries)

    @property
    def calls(self) -> int:
        return sum(m.attempts for m in self.entries)

    @property
    def cost_estimate(self) -> float | None:
        costs = [m.cost_estimate for m in self.entries]
        if not costs or any(c is None for c in costs):
            return None
        return round(sum(c for c in costs if c is not None), 6)
