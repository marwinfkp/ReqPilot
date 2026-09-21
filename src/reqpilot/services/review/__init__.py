"""The review queue for AI proposals (architecture M.5). Not approval: that is G1.

Only the primitives are exported here. The resolving service lives in
:mod:`reqpilot.services.review.service` and is imported from there, because it
depends on the classification and merge services, which themselves raise and
close review items through these primitives.
"""

from reqpilot.services.review.queue import ReviewQueue

__all__ = ["ReviewQueue"]
