"""The deterministic 3x3 severity matrix (``FR-RSK-004``; architecture I.3).

The *disposing* half of P7. A model proposes a likelihood and an impact, each
with a written rationale; this module turns that pair into the authoritative
severity, and it is the only thing in the system that does.

Three properties make that authority structural rather than conventional:

1. **The proposal schema has no severity field** (``[DESIGN] D4``). There is
   nowhere for a model to put one, so none can be read.
2. **The lookup is total and pure.** Nine cells, no defaults, no thresholds, no
   configuration a caller can pass. The same pair always yields the same
   severity, and every pair is defined.
3. **The matrix is versioned data, not a literal.** It lives in
   ``rules/data/risk_matrix.yaml`` and, at the database, in the ``risk_matrix``
   table (``DQ-03``). Every risk row records the ``matrix_version`` it was rated
   under and is pinned to that table's row by a composite foreign key, so a
   stored severity cannot disagree with the matrix it claims, and changing the
   matrix never silently re-rates history.

The approved matrix (architecture I.3), restated here because a test asserts
this docstring's table against the loaded ruleset and against the database:

======  ============  ===============  ============
        **I1 Minor**  **I2 Moderate**  **I3 Major**
**L3**  Medium        High             High
**L2**  Low           Medium           High
**L1**  Low           Low              Medium
======  ============  ===============  ============

Note what the matrix does *not* do: it never lowers a rating, and it has no
override path. If both ratings are absent or malformed the proposal is dropped
by validation, never rated by default - a made-up pair would be a fabricated
judgement, and an inflated one would be noise in the review queue.
"""

from __future__ import annotations

from dataclasses import dataclass

from reqpilot.domain.enums import RiskImpact, RiskLikelihood, RiskSeverity

#: The approved I.3 matrix as a literal, used to seed and to verify the
#: versioned ruleset and the database table. Code reads the *loaded* matrix;
#: this exists so that a change to the data is caught by a test rather than
#: shipped silently.
APPROVED_CELLS: dict[tuple[RiskLikelihood, RiskImpact], RiskSeverity] = {
    (RiskLikelihood.L1, RiskImpact.I1): RiskSeverity.LOW,
    (RiskLikelihood.L1, RiskImpact.I2): RiskSeverity.LOW,
    (RiskLikelihood.L1, RiskImpact.I3): RiskSeverity.MEDIUM,
    (RiskLikelihood.L2, RiskImpact.I1): RiskSeverity.LOW,
    (RiskLikelihood.L2, RiskImpact.I2): RiskSeverity.MEDIUM,
    (RiskLikelihood.L2, RiskImpact.I3): RiskSeverity.HIGH,
    (RiskLikelihood.L3, RiskImpact.I1): RiskSeverity.MEDIUM,
    (RiskLikelihood.L3, RiskImpact.I2): RiskSeverity.HIGH,
    (RiskLikelihood.L3, RiskImpact.I3): RiskSeverity.HIGH,
}

#: The severity that requires gate G8 (``FR-RSK-007``; architecture I.5).
ESCALATING_SEVERITY: RiskSeverity = RiskSeverity.HIGH

#: Ordinal ranks, for the register's ordering and the SDLC aggregates (I.6).
#: They are presentation and aggregation helpers - never an arithmetic route to
#: a severity, which is always the table lookup.
LIKELIHOOD_RANK: dict[RiskLikelihood, int] = {
    RiskLikelihood.L1: 1,
    RiskLikelihood.L2: 2,
    RiskLikelihood.L3: 3,
}
IMPACT_RANK: dict[RiskImpact, int] = {RiskImpact.I1: 1, RiskImpact.I2: 2, RiskImpact.I3: 3}
SEVERITY_RANK: dict[RiskSeverity, int] = {
    RiskSeverity.LOW: 1,
    RiskSeverity.MEDIUM: 2,
    RiskSeverity.HIGH: 3,
}


@dataclass(frozen=True)
class RiskMatrix:
    """One version of the severity matrix: nine cells and nothing else."""

    version: str
    cells: dict[tuple[RiskLikelihood, RiskImpact], RiskSeverity]

    def __post_init__(self) -> None:
        missing = [
            (likelihood, impact)
            for likelihood in RiskLikelihood
            for impact in RiskImpact
            if (likelihood, impact) not in self.cells
        ]
        if missing:
            raise ValueError(
                f"risk matrix {self.version} is not total; missing cells: "
                f"{[(str(likelihood), str(impact)) for likelihood, impact in missing]}"
            )

    def severity(self, likelihood: RiskLikelihood, impact: RiskImpact) -> RiskSeverity:
        """The authoritative severity for one rated pair. A pure table lookup."""
        return self.cells[(likelihood, impact)]

    def requires_gate(self, severity: RiskSeverity) -> bool:
        """Whether this severity must be escalated to G8 (``FR-RSK-007``)."""
        return severity is ESCALATING_SEVERITY

    def rows(self) -> list[tuple[str, str, str]]:
        """The matrix as ``(likelihood, impact, severity)`` triples, sorted."""
        return sorted(
            (str(likelihood), str(impact), str(severity))
            for (likelihood, impact), severity in self.cells.items()
        )


@dataclass(frozen=True)
class SeverityComputation:
    """What the matrix decided, and everything needed to explain it.

    Kept as a value object so the node that records a risk, the audit event and
    the register all report the same computation rather than each re-deriving
    it.
    """

    likelihood: RiskLikelihood
    impact: RiskImpact
    severity: RiskSeverity
    matrix_version: str
    requires_gate: bool

    @property
    def explanation(self) -> str:
        """A one-line, deterministic explanation. No model wrote this."""
        return (
            f"likelihood {self.likelihood} x impact {self.impact} = {self.severity} "
            f"by risk matrix {self.matrix_version}"
        )


def compute_severity(
    matrix: RiskMatrix, likelihood: RiskLikelihood, impact: RiskImpact
) -> SeverityComputation:
    """Rate one proposal. The only path to an authoritative severity.

    Takes the two ordinal ratings and nothing else: no proposed severity, no
    free text, no confidence and no override. A caller that wanted to force a
    severity would have to change the matrix data, which is versioned, seeded
    and asserted.
    """
    severity = matrix.severity(likelihood, impact)
    return SeverityComputation(
        likelihood=likelihood,
        impact=impact,
        severity=severity,
        matrix_version=matrix.version,
        requires_gate=matrix.requires_gate(severity),
    )


def parse_likelihood(value: object) -> RiskLikelihood | None:
    """Interpret a proposed likelihood, or ``None`` if it is not one of the three.

    Deliberately *not* forgiving in the direction of inventing a rating: an
    unrecognised value yields ``None`` and the proposal is dropped by
    validation. Contrast I.7's security levels, which are normalised **upward**
    to MEDIUM - there the finding exists either way and only its level is in
    question, whereas here an unrated proposal is not a risk assessment at all.
    """
    if isinstance(value, RiskLikelihood):
        return value
    if not isinstance(value, str):
        return None
    token = value.strip().upper()
    aliases = {"1": "L1", "2": "L2", "3": "L3", "LOW": "L1", "MEDIUM": "L2", "HIGH": "L3"}
    token = aliases.get(token, token)
    try:
        return RiskLikelihood(token)
    except ValueError:
        return None


def parse_impact(value: object) -> RiskImpact | None:
    """Interpret a proposed impact, or ``None`` if it is not one of the three."""
    if isinstance(value, RiskImpact):
        return value
    if not isinstance(value, str):
        return None
    token = value.strip().upper()
    aliases = {"1": "I1", "2": "I2", "3": "I3", "MINOR": "I1", "MODERATE": "I2", "MAJOR": "I3"}
    token = aliases.get(token, token)
    try:
        return RiskImpact(token)
    except ValueError:
        return None
