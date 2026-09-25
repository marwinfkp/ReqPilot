"""Deterministic governance rules P8 adds (architecture M.3, M.5; ``FR-HIL-001``, ``-006``).

Pure functions and value objects - no I/O, no model, no orchestration - so every
rule here is exhaustively unit-testable:

* :func:`architecture_critical_reasons` - the **G5 trigger**, exactly as
  architecture M.3 states it: *"classification includes
  integration/performance/availability **and** review_signal below threshold,
  or analyst flag"*. No new taxonomy is introduced: the three categories are
  M.3's, the threshold is the existing P3 classification review threshold
  (``extraction.yaml``: ``classification.review_threshold``), and the analyst
  flag is a recorded human action.
* :class:`Blocker` - one reason a governed step is refused, with its gate.
* :func:`queue_sort_key` - the **single review queue's ordering**
  (``FR-HIL-006``: "ordered by risk severity and confidence"; architecture M.5
  adds blocking first and age). Total and deterministic: ties end on the item id.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, field

from reqpilot.domain.enums import Gate, RequirementCategory

#: The three categories architecture M.3 names for G5.
ARCHITECTURE_CRITICAL_CATEGORIES: frozenset[RequirementCategory] = frozenset(
    {
        RequirementCategory.INTEGRATION,
        RequirementCategory.PERFORMANCE,
        RequirementCategory.AVAILABILITY,
    }
)


@dataclass(frozen=True)
class LabelSignal:
    """One current classification label and its review signal (P3)."""

    category: RequirementCategory
    review_signal: float | None


def architecture_critical_reasons(
    labels: Iterable[LabelSignal],
    *,
    threshold: float,
    analyst_flagged: bool,
) -> tuple[str, ...]:
    """Why a version needs G5, or ``()`` if it does not (architecture M.3).

    A label with **no** review signal (a human-set label, or a P1 manual
    category) is not "below threshold": nothing low-confidence was proposed, so
    the automatic trigger does not fire for it. The analyst flag covers every
    case the predicate cannot see.
    """
    reasons: list[str] = []
    for label in labels:
        if label.category not in ARCHITECTURE_CRITICAL_CATEGORIES:
            continue
        if label.review_signal is not None and label.review_signal < threshold:
            reasons.append(
                f"classified {label.category.value} with review signal "
                f"{label.review_signal:.2f} below the {threshold:.2f} threshold"
            )
    if analyst_flagged:
        reasons.append("flagged architecture-critical by an analyst")
    return tuple(sorted(set(reasons)))


@dataclass(frozen=True)
class Blocker:
    """One deterministic reason a governed step is refused.

    ``code`` is stable and machine-checkable; ``message`` is for the human.
    Neither is model text.
    """

    code: str
    message: str
    gate: Gate | None = None
    subject_type: str | None = None
    subject_id: str | None = None

    def render(self) -> str:
        gate = f"[{self.gate.value}] " if self.gate is not None else ""
        return f"{gate}{self.code}: {self.message}"


#: Severity ranks for queue ordering. ``None`` (no severity applies) ranks last.
SEVERITY_ORDER: dict[str | None, int] = {"high": 3, "medium": 2, "low": 1, None: 0}


@dataclass(frozen=True)
class QueueEntry:
    """One item of the single review queue (``FR-HIL-006``). Presentation only.

    The queue has no authority. An entry that is an approval task is decided
    through the approval service; an entry that is a finding, a conflict or a
    risk is closed through the service that owns it.
    """

    kind: str
    item_id: str
    title: str
    project_id: str
    blocking: bool
    severity: str | None
    review_signal: float | None
    created_at: dt.datetime
    gate: Gate | None = None
    required_role: str | None = None
    subject_type: str | None = None
    subject_id: str | None = None
    subject_label: str | None = None
    actionable: bool = False
    link: str | None = None
    detail: dict[str, str] = field(default_factory=dict)


def queue_sort_key(entry: QueueEntry) -> tuple[int, int, float, str, str, str]:
    """The documented, total ordering of the single review queue.

    1. blocking items first (architecture M.5);
    2. higher risk severity first (``FR-HIL-006``);
    3. lower confidence first - a missing review signal counts as the lowest
       confidence, because nothing vouches for the item (``FR-HIL-006``);
    4. older first (architecture M.5: age);
    5. then kind and id, so equal items can never swap between two reads.
    """
    signal = -1.0 if entry.review_signal is None else float(entry.review_signal)
    created = entry.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=dt.UTC)
    return (
        0 if entry.blocking else 1,
        -SEVERITY_ORDER.get(entry.severity, 0),
        signal,
        created.isoformat(),
        entry.kind,
        entry.item_id,
    )
