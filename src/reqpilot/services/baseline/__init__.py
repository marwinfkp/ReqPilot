"""Baseline commit service and its invariants (architecture H.4).

No unapproved requirement version may enter a baseline. This package is the
first of the three independent layers that enforce it.
"""

from reqpilot.services.baseline.service import BaselineService

__all__ = ["BaselineService"]
