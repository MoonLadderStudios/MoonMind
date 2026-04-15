"""Deterministic inventory of covered public Temporal boundary contracts."""

from __future__ import annotations

from moonmind.schemas.temporal_boundary_models import (
    TemporalBoundaryContract,
    TemporalBoundaryInventory,
    TemporalBoundaryModelRef,
    TemporalBoundaryModelRole,
)


def _model(
    *,
    module: str,
    name: str,
    role: TemporalBoundaryModelRole,
) -> TemporalBoundaryModelRef:
    return TemporalBoundaryModelRef(
        module=module,
        name=name,
        role=role,
        schemaHome=module,
    )


_TEMPORAL_ACTIVITY_MODELS = "moonmind.schemas.temporal_activity_models"
_TEMPORAL_MODELS = "moonmind.schemas.temporal_models"
_MANAGED_SESSION_MODELS = "moonmind.schemas.managed_session_models"
_AGENT_RUNTIME_MODELS = "moonmind.schemas.agent_runtime_models"


_CONTRACTS: tuple[TemporalBoundaryContract, ...] = (
    TemporalBoundaryContract(
        kind="activity",
        name="artifact.read",
        owner="artifact",
        requestModel=_model(
            module=_TEMPORAL_ACTIVITY_MODELS,
            name="ArtifactReadInput",
            role="request",
        ),
        responseModel=_model(
            module=_TEMPORAL_ACTIVITY_MODELS,
            name="ArtifactReadOutput",
            role="response",
        ),
        status="modeled",
        schemaHome=_TEMPORAL_ACTIVITY_MODELS,
        coverageIds=("DESIGN-REQ-001", "DESIGN-REQ-002", "DESIGN-REQ-003"),
    ),
    TemporalBoundaryContract(
        kind="activity",
        name="artifact.write_complete",
        owner="artifact",
        requestModel=_model(
            module=_TEMPORAL_ACTIVITY_MODELS,
            name="ArtifactWriteCompleteInput",
            role="request",
        ),
        status="modeled",
        schemaHome=_TEMPORAL_ACTIVITY_MODELS,
        coverageIds=("DESIGN-REQ-001", "DESIGN-REQ-002", "DESIGN-REQ-003"),
        rationale="Activity returns an existing artifact reference domain object.",
    ),
    TemporalBoundaryContract(
        kind="activity",
        name="plan.generate",
        owner="plan",
        requestModel=_model(
            module=_TEMPORAL_ACTIVITY_MODELS,
            name="PlanGenerateInput",
            role="request",
        ),
        responseModel=_model(
            module=_TEMPORAL_ACTIVITY_MODELS,
            name="PlanGenerateOutput",
            role="response",
        ),
        status="modeled",
        schemaHome=_TEMPORAL_ACTIVITY_MODELS,
        coverageIds=("DESIGN-REQ-001", "DESIGN-REQ-002", "DESIGN-REQ-007"),
        metadataFields=("parameters", "executionRef"),
    ),
    TemporalBoundaryContract(
        kind="activity",
        name="integration.jules.status",
        owner="integration",
        requestModel=_model(
            module=_TEMPORAL_ACTIVITY_MODELS,
            name="ExternalAgentRunInput",
            role="request",
        ),
        responseModel=_model(
            module=_AGENT_RUNTIME_MODELS,
            name="AgentRunStatus",
            role="response",
        ),
        status="modeled",
        schemaHome=_TEMPORAL_ACTIVITY_MODELS,
        coverageIds=("DESIGN-REQ-001", "DESIGN-REQ-002", "DESIGN-REQ-007"),
    ),
    TemporalBoundaryContract(
        kind="activity",
        name="agent_runtime.status",
        owner="agent_runtime",
        requestModel=_model(
            module=_TEMPORAL_ACTIVITY_MODELS,
            name="AgentRuntimeStatusInput",
            role="request",
        ),
        responseModel=_model(
            module=_AGENT_RUNTIME_MODELS,
            name="AgentRunStatus",
            role="response",
        ),
        status="modeled",
        schemaHome=_TEMPORAL_ACTIVITY_MODELS,
        coverageIds=("DESIGN-REQ-001", "DESIGN-REQ-002", "DESIGN-REQ-007"),
    ),
    TemporalBoundaryContract(
        kind="workflow",
        name="MoonMind.AgentSession",
        owner="MoonMind.AgentSession",
        requestModel=_model(
            module=_MANAGED_SESSION_MODELS,
            name="CodexManagedSessionWorkflowInput",
            role="request",
        ),
        responseModel=_model(
            module=_MANAGED_SESSION_MODELS,
            name="CodexManagedSessionSnapshot",
            role="snapshot",
        ),
        status="modeled",
        schemaHome=_MANAGED_SESSION_MODELS,
        coverageIds=("DESIGN-REQ-001", "DESIGN-REQ-002", "DESIGN-REQ-007"),
    ),
    TemporalBoundaryContract(
        kind="continue_as_new",
        name="MoonMind.AgentSession",
        owner="MoonMind.AgentSession",
        requestModel=_model(
            module=_MANAGED_SESSION_MODELS,
            name="CodexManagedSessionWorkflowInput",
            role="continuation",
        ),
        responseModel=_model(
            module=_MANAGED_SESSION_MODELS,
            name="CodexManagedSessionSnapshot",
            role="snapshot",
        ),
        status="modeled",
        schemaHome=_MANAGED_SESSION_MODELS,
        coverageIds=("DESIGN-REQ-001", "DESIGN-REQ-002", "DESIGN-REQ-003"),
    ),
    TemporalBoundaryContract(
        kind="update",
        name="SendFollowUp",
        owner="MoonMind.AgentSession",
        requestModel=_model(
            module=_MANAGED_SESSION_MODELS,
            name="CodexManagedSessionSendFollowUpRequest",
            role="request",
        ),
        responseModel=_model(
            module=_MANAGED_SESSION_MODELS,
            name="CodexManagedSessionSnapshot",
            role="snapshot",
        ),
        status="modeled",
        schemaHome=_MANAGED_SESSION_MODELS,
        coverageIds=("DESIGN-REQ-001", "DESIGN-REQ-002", "DESIGN-REQ-008"),
    ),
    TemporalBoundaryContract(
        kind="query",
        name="snapshot",
        owner="MoonMind.AgentSession",
        requestModel=_model(
            module=_MANAGED_SESSION_MODELS,
            name="CodexManagedSessionWorkflowInput",
            role="metadata",
        ),
        responseModel=_model(
            module=_MANAGED_SESSION_MODELS,
            name="CodexManagedSessionSnapshot",
            role="snapshot",
        ),
        status="modeled",
        schemaHome=_MANAGED_SESSION_MODELS,
        coverageIds=("DESIGN-REQ-001", "DESIGN-REQ-002", "DESIGN-REQ-008"),
        rationale="The query takes no payload; the workflow input model owns the session identity context.",
    ),
    TemporalBoundaryContract(
        kind="signal",
        name="DependencyResolved",
        owner="MoonMind.Run",
        requestModel=_model(
            module=_TEMPORAL_MODELS,
            name="DependencyResolvedSignalPayload",
            role="request",
        ),
        status="modeled",
        schemaHome=_TEMPORAL_MODELS,
        coverageIds=("DESIGN-REQ-001", "DESIGN-REQ-002", "DESIGN-REQ-008"),
        rationale="Fire-and-forget signal has no response payload.",
    ),
    TemporalBoundaryContract(
        kind="update",
        name="UpdateInputs",
        owner="MoonMind.Run",
        requestModel=_model(
            module=_TEMPORAL_MODELS,
            name="UpdateExecutionRequest",
            role="request",
        ),
        responseModel=_model(
            module=_TEMPORAL_MODELS,
            name="UpdateExecutionResponse",
            role="response",
        ),
        status="compatibility_shim",
        schemaHome=_TEMPORAL_MODELS,
        coverageIds=("DESIGN-REQ-001", "DESIGN-REQ-002", "DESIGN-REQ-008"),
        rationale="Existing API-facing update model still carries bounded patch fields while workflow call sites migrate.",
        metadataFields=("parametersPatch",),
    ),
)


_INVENTORY = TemporalBoundaryInventory(
    sourceIssueKey="MM-327",
    boardScope="TOOL",
    contracts=_CONTRACTS,
)


def get_temporal_boundary_inventory() -> TemporalBoundaryInventory:
    """Return the deterministic MM-327 Temporal boundary inventory."""

    return _INVENTORY


def iter_temporal_boundary_contracts() -> tuple[TemporalBoundaryContract, ...]:
    """Return covered Temporal boundary contracts in deterministic order."""

    return _INVENTORY.contracts


__all__ = [
    "get_temporal_boundary_inventory",
    "iter_temporal_boundary_contracts",
]
