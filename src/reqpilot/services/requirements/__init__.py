"""The requirements repository service (roadmap phase P1).

The deterministic system of record: create a requirement, version it immutably,
move it through the approved lifecycle, and hand it to the approval gate.
No model, no prompt, no orchestration.
"""

from reqpilot.services.requirements.service import (
    RequirementContent,
    RequirementService,
    assert_version_unmodified,
)

__all__ = ["RequirementContent", "RequirementService", "assert_version_unmodified"]
