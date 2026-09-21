"""Repair: make two P1 PostgreSQL enum types match what the application writes.

**P1 defects, found and repaired during P2.** Neither could be seen before P2,
because no live PostgreSQL was available and SQLite stores these enums as
unconstrained strings. The first run against a real server exposed both:

1. ``audit_event_type_enum`` was created by the foundation migration with the
   foundation's twelve event types. P1 added twelve more to the Python enum but
   never to the PostgreSQL type, so every P1 audit write - and therefore every
   P1 service action, each of which audits in its own transaction - failed with
   ``invalid input value for enum audit_event_type_enum``.
2. ``gate_enum`` was created with the labels ``'G1'`` ... ``'G8'``, but the ORM
   stores enum member *names* (``'G1_REQUIREMENT_BASELINE'`` ...), as it does
   for every other enum column. So no approval task could be inserted on
   PostgreSQL, and the G1 workflow could not run there at all.

The repair is additive for (1) and a label rename for (2); no data exists that
could be affected, because neither statement could ever have succeeded. The P1
migration itself is left untouched: history is not rewritten.

Two regression tests now guard this: every Python enum value must be accepted by
its PostgreSQL type, and the P1 requirement-to-baseline workflow runs end to end
against PostgreSQL.

Revision ID: 0003_p1_postgres_enum_repair
Revises: 0002_p1_requirements_repository
Create Date: P2
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_p1_postgres_enum_repair"
down_revision: str | None = "0002_p1_requirements_repository"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Frozen here, not imported from the application: a migration must describe the
#: schema as it was at this revision, whatever the enum later becomes.
P1_AUDIT_EVENT_TYPES = (
    "REQUIREMENT_CREATED",
    "REQUIREMENT_VERSION_CREATED",
    "REQUIREMENT_WITHDRAWN",
    "REQUIREMENT_SUPERSEDED",
    "STATE_TRANSITION",
    "APPROVAL_TASK_CREATED",
    "APPROVAL_GRANTED",
    "APPROVAL_REJECTED",
    "APPROVAL_MODIFIED",
    "GATE_PASSED",
    "BASELINE_COMMITTED",
    "BASELINE_MEMBER_ADDED",
)

#: The label P1's migration created -> the member name the ORM actually writes.
GATE_LABELS = (
    ("G1", "G1_REQUIREMENT_BASELINE"),
    ("G2", "G2_REGULATORY_INTERPRETATION"),
    ("G3", "G3_HIGH_RISK_SECURITY"),
    ("G4", "G4_STAKEHOLDER_CONFLICT"),
    ("G5", "G5_ARCHITECTURE_CRITICAL"),
    ("G6", "G6_SDLC_SELECTION"),
    ("G7", "G7_APPROVED_REQUIREMENT_CHANGE"),
    ("G8", "G8_HIGH_SEVERITY_RISK"),
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for value in P1_AUDIT_EVENT_TYPES:
        op.execute(f"ALTER TYPE audit_event_type_enum ADD VALUE IF NOT EXISTS '{value}'")
    for old, new in GATE_LABELS:
        op.execute(f"ALTER TYPE gate_enum RENAME VALUE '{old}' TO '{new}'")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for old, new in GATE_LABELS:
        op.execute(f"ALTER TYPE gate_enum RENAME VALUE '{new}' TO '{old}'")
    # PostgreSQL cannot remove a value from an enum type. The added audit event
    # types are inert without the P1 tables, and the foundation downgrade drops
    # the whole type.
