"""Tests for Temporal boundary inventory contract models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.schemas.temporal_boundary_models import (
    TemporalBoundaryContract,
    TemporalBoundaryInventory,
    TemporalBoundaryModelRef,
)


def _model_ref(name: str = "ExampleRequest") -> TemporalBoundaryModelRef:
    return TemporalBoundaryModelRef(
        module="moonmind.schemas.temporal_activity_models",
        name=name,
        role="request",
        schemaHome="moonmind.schemas.temporal_activity_models",
    )


def test_model_ref_rejects_unknown_fields_and_normalizes_identifiers() -> None:
    ref = TemporalBoundaryModelRef(
        module=" moonmind.schemas.temporal_activity_models ",
        name=" ArtifactReadInput ",
        role="request",
        schemaHome=" moonmind.schemas.temporal_activity_models ",
    )

    assert ref.module == "moonmind.schemas.temporal_activity_models"
    assert ref.name == "ArtifactReadInput"
    assert ref.schema_home == "moonmind.schemas.temporal_activity_models"
    assert ref.model_dump(by_alias=True)["schemaHome"] == (
        "moonmind.schemas.temporal_activity_models"
    )

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TemporalBoundaryModelRef(
            module="moonmind.schemas.temporal_activity_models",
            name="ArtifactReadInput",
            role="request",
            schemaHome="moonmind.schemas.temporal_activity_models",
            unexpected=True,
        )

    with pytest.raises(ValidationError, match="name must not be blank"):
        TemporalBoundaryModelRef(
            module="moonmind.schemas.temporal_activity_models",
            name="   ",
            role="request",
            schemaHome="moonmind.schemas.temporal_activity_models",
        )


def test_contract_requires_rationale_for_tracking_status() -> None:
    with pytest.raises(ValidationError, match="rationale is required"):
        TemporalBoundaryContract(
            kind="activity",
            name="agent_runtime.launch",
            owner="agent_runtime",
            requestModel=_model_ref("LaunchAgentRuntimeRequest"),
            status="tracking_only",
            schemaHome="moonmind.schemas.agent_runtime_models",
            coverageIds=["DESIGN-REQ-001"],
        )

    contract = TemporalBoundaryContract(
        kind="activity",
        name="agent_runtime.launch",
        owner="agent_runtime",
        requestModel=_model_ref("LaunchAgentRuntimeRequest"),
        status="tracking_only",
        schemaHome="moonmind.schemas.agent_runtime_models",
        coverageIds=["DESIGN-REQ-001"],
        rationale="Boundary is inventoried before broad call-site migration.",
    )

    assert contract.rationale == "Boundary is inventoried before broad call-site migration."


def test_inventory_preserves_jira_source_and_rejects_duplicate_contracts() -> None:
    contract = TemporalBoundaryContract(
        kind="activity",
        name="artifact.read",
        owner="artifact",
        requestModel=_model_ref("ArtifactReadInput"),
        responseModel=TemporalBoundaryModelRef(
            module="moonmind.schemas.temporal_activity_models",
            name="ArtifactReadOutput",
            role="response",
            schemaHome="moonmind.schemas.temporal_activity_models",
        ),
        status="modeled",
        schemaHome="moonmind.schemas.temporal_activity_models",
        coverageIds=["DESIGN-REQ-001", "DESIGN-REQ-002"],
    )

    inventory = TemporalBoundaryInventory(
        sourceIssueKey=" MM-327 ",
        boardScope=" TOOL ",
        contracts=[contract],
    )

    assert inventory.source_issue_key == "MM-327"
    assert inventory.board_scope == "TOOL"

    with pytest.raises(ValidationError, match="duplicate boundary contract"):
        TemporalBoundaryInventory(
            sourceIssueKey="MM-327",
            boardScope="TOOL",
            contracts=[contract, contract],
        )
