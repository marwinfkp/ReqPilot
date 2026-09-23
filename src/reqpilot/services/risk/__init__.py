"""Risk analysis and the risk register (roadmap phase P7; architecture I).

* :class:`~reqpilot.services.risk.engine.RiskEngine` - the deterministic half of
  the pipeline: scope, prior-phase context, the severity the matrix computes,
  what is recorded, and the G8 fan-out.
* :class:`~reqpilot.services.risk.gates.RiskGateService` - G8 as a subject of the
  one approval service, with the same binding and staleness rules as G2/G3.
* :class:`~reqpilot.services.risk.service.RiskService` - the human half:
  adding, accepting, mitigating, rejecting and closing a risk with a recorded
  rationale (``FR-RSK-010``).
* :class:`~reqpilot.services.risk.register.RiskRegisterService` - the register
  itself (``FR-RSK-008``), a query over the persisted rows, and the aggregate
  measures that feed the SDLC factor profile (``FR-RSK-009``).
"""

from reqpilot.services.risk.engine import PriorSignal, RiskContext, RiskEngine
from reqpilot.services.risk.gates import RISK_SUBJECT, RiskGateService
from reqpilot.services.risk.register import (
    REGISTER_NOTICE,
    FactorInput,
    RegisterView,
    RiskRegisterService,
    RiskView,
)
from reqpilot.services.risk.service import RiskService

__all__ = [
    "REGISTER_NOTICE",
    "RISK_SUBJECT",
    "FactorInput",
    "PriorSignal",
    "RegisterView",
    "RiskContext",
    "RiskEngine",
    "RiskGateService",
    "RiskRegisterService",
    "RiskService",
    "RiskView",
]
