"""Project-specific SDLC workflow generation - the pure core (roadmap phase P10).

``FR-WFL-001``..``-008``; architecture C.5 ``generate_workflow``, G.8, M.4, N.2
#24-#26. No I/O, no database, no model: the service layer loads the approved
records into :mod:`~reqpilot.domain.workflow.inputs`, and this package derives,
validates and edits a :class:`~reqpilot.domain.workflow.plan.WorkflowPlan`.
"""

from reqpilot.domain.workflow.derivation import derive_workflow
from reqpilot.domain.workflow.edits import (
    AddActivity,
    RemoveActivity,
    UpdateActivity,
    UpdateGate,
    UpdatePhase,
    WorkflowEdit,
    apply_edit,
)
from reqpilot.domain.workflow.inputs import (
    GapInput,
    MappingInput,
    MitigationInput,
    RiskInput,
    SecurityFindingInput,
    WorkflowInputs,
)
from reqpilot.domain.workflow.plan import (
    ActivityPlan,
    Finding,
    GatePlan,
    PhasePlan,
    SourceLink,
    WorkflowPlan,
)
from reqpilot.domain.workflow.templates import WorkflowTemplates
from reqpilot.domain.workflow.validation import mandatory_sources, validate_workflow

__all__ = [
    "ActivityPlan",
    "AddActivity",
    "Finding",
    "GapInput",
    "GatePlan",
    "MappingInput",
    "MitigationInput",
    "PhasePlan",
    "RemoveActivity",
    "RiskInput",
    "SecurityFindingInput",
    "SourceLink",
    "UpdateActivity",
    "UpdateGate",
    "UpdatePhase",
    "WorkflowEdit",
    "WorkflowInputs",
    "WorkflowPlan",
    "WorkflowTemplates",
    "apply_edit",
    "derive_workflow",
    "mandatory_sources",
    "validate_workflow",
]
