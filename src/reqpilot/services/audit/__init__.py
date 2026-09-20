"""Module M10 - append-only audit and trace service (architecture ADR-010, section O).

Public surface is deliberately append + read + verify. There is no update and
no delete, by design.
"""

from reqpilot.services.audit.hashing import compute_row_hash, verify_chain
from reqpilot.services.audit.service import AuditService

__all__ = ["AuditService", "compute_row_hash", "verify_chain"]
