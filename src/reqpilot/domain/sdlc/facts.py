"""The approved project facts a factor profile is derived from (``FR-SDL-001``, ``-002``).

A :class:`FactorFacts` is what architecture C.5's ``collect_factor_evidence``
node produces: deterministic counts over the **approved** scope - the requirement
versions in force as of one baseline, the governed risk register, the compliance
and security records of those versions, the project's stakeholders - each count
carrying the typed evidence references of the rows behind it
(``"<table>:<uuid>"``). Nothing here is read from a model.

The product builds one from persisted rows (``services/sdlc/evidence.py``); the
E9 harness builds one from a frozen synthetic case. Both hand it to the same
pure derivation, so the evaluation measures the product's own logic.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


def ref(kind: str, identifier: object) -> str:
    """A typed evidence reference: ``"requirement_version:<uuid>"``, ``"risk:<uuid>"``..."""
    return f"{kind}:{identifier}"


@dataclass(frozen=True)
class Signal:
    """A count over the approved scope and the rows that make it up."""

    count: int
    refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.count < 0:
            raise ValueError("a signal count cannot be negative")


EMPTY = Signal(0)


@dataclass(frozen=True)
class RiskAggregate:
    """One architecture I.6 aggregate from the P7 register (``FR-RSK-009``).

    ``value`` is the P7 formula's 1-5 value over the *approved* register rows;
    ``refs`` are exactly the risk rows it was computed from.
    """

    value: int
    refs: tuple[str, ...] = ()
    counts: Mapping[str, int] = field(default_factory=dict)
    description: str = ""


NO_RISK = RiskAggregate(1)


@dataclass(frozen=True)
class FactorFacts:
    """Everything the derivation may read, with its provenance."""

    #: The approved scope examined - ``"baseline:<uuid>"`` - cited when a factor's
    #: evidence is the *absence* of a signal in that scope.
    scope_ref: str
    scope_label: str
    requirements: Signal
    revised_requirements: Signal = EMPTY
    conflicts: Signal = EMPTY
    integration_requirements: Signal = EMPTY
    legacy_requirements: Signal = EMPTY
    change_signals: Signal = EMPTY
    delivery_signals: Signal = EMPTY
    verification_signals: Signal = EMPTY
    documentation_signals: Signal = EMPTY
    schedule_signals: Signal = EMPTY
    acceptance_criteria: Signal = EMPTY
    stakeholders: Signal = EMPTY
    stakeholders_interviewed: Signal = EMPTY
    open_clarifications: Signal = EMPTY
    normative_sources: Signal = EMPTY
    open_compliance_gaps: Signal = EMPTY
    security_risk: RiskAggregate = NO_RISK
    consequences_of_failure: RiskAggregate = NO_RISK
    regulatory_risk: RiskAggregate = NO_RISK
    technical_risk: RiskAggregate = NO_RISK

    def all_refs(self) -> frozenset[str]:
        """Every evidence reference these facts carry - the citable set."""
        out: set[str] = {self.scope_ref}
        for value in self.__dict__.values():
            if isinstance(value, Signal | RiskAggregate):
                out.update(value.refs)
        return frozenset(out)
