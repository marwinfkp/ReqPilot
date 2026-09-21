"""The sensitive-data masking seam (``FR-ING-003``; architecture J.2, P #5).

**Masking is not implemented in P3.** The approved roadmap builds it in P11
("Guardrails hardening: masking ... masking test on synthetic financial
identifiers"). What P3 provides, and what this module is, is the *position* of
the masking stage in the ingestion pipeline - after parsing, before anything is
segmented, stored for prompting, embedded or sent to a model (J.2) - and an
honest implementation of "nothing was masked" that the rest of the system
cannot mistake for protection.

Two consequences, both enforced elsewhere and both deliberate:

* Every source document records ``masking_status`` and ``masker_id``, so which
  text was and was not masked is a fact in the database, not an assumption.
* The LLM gateway refuses to send unmasked project content to any provider
  that leaves the machine unless the uploader declared the source **synthetic**
  (see :mod:`reqpilot.llm.guards`). Real data therefore cannot reach an
  external model before masking exists.

When P11 implements masking it replaces :class:`NoMasking` with a protective
masker behind the same :class:`Masker` protocol, masks *before* segmentation
(so stored offsets and prompt text agree), and keeps the unmasking map apart
from anything a prompt can reach (J.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from reqpilot.domain.enums import MaskingStatus


@dataclass(frozen=True)
class MaskResult:
    """The text after the masking stage, and what the stage actually did."""

    text: str
    status: MaskingStatus
    masker_id: str
    #: How many values were replaced. Counts only, never the values.
    replacements: int = 0


@runtime_checkable
class Masker(Protocol):
    """A masking stage. ``is_protective`` must be true only if it really masks."""

    @property
    def masker_id(self) -> str: ...

    @property
    def is_protective(self) -> bool: ...

    def mask(self, text: str) -> MaskResult: ...


class NoMasking:
    """The P3 stage: returns the text unchanged and says so.

    Not a placeholder that pretends: ``is_protective`` is false and every result
    is ``NOT_MASKED``, which is what the gateway's egress rule reads.
    """

    masker_id = "none"
    is_protective = False

    def mask(self, text: str) -> MaskResult:
        return MaskResult(text=text, status=MaskingStatus.NOT_MASKED, masker_id=self.masker_id)


def default_masker() -> Masker:
    """The masker the ingestion pipeline uses. P3: none exists yet."""
    return NoMasking()
