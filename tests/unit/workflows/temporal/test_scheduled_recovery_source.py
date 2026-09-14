"""Recovery consumes exact scheduled inputs, never compact projection parameters."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api_service.db.models import (
    TemporalExecutionRecord,
    TemporalExecutionProjectionSourceMode,
    TemporalWorkflowType,
    MoonMindWorkflowState,
)
from moonmind.workflows.temporal.service import (
    TemporalExecutionService,
    TemporalExecutionValidationError,
)


def source():
    return TemporalExecutionRecord(
        workflow_id="mm:scheduled",
        run_id="reset-run",
        owner_id="owner",
        source_mode=TemporalExecutionProjectionSourceMode.TEMPORAL_AUTHORITATIVE,
        workflow_type=TemporalWorkflowType.USER_WORKFLOW,
        state=MoonMindWorkflowState.FAILED,
        parameters={"model": "compact projection only"},
        memo={"failed_run_recovery_manifest_ref": "art_manifest"},
        plan_ref="art_compiled_plan",
        finish_summary_json={"outcome": "failed"},
    )


def admitted():
    return {
        "owner_user_id": "owner",
        "workflow_type": "MoonMind.UserWorkflow",
        "input_artifact_ref": "art_original",
        "initial_parameters": {
            "task": {"instructions": "saved intent"},
            "model": "exact-model",
            "effort": "xhigh",
            "omnigentExecutionPlan": {
                "planRef": "omnigent-execution-plan:sha256:" + "a" * 64,
                "planDigest": "sha256:" + "a" * 64,
                "planArtifactRef": "art_plan",
                "taskInputSnapshotRef": "art_snapshot",
                "taskInputSnapshotDigest": "sha256:" + "b" * 64,
            },
        },
    }


@pytest.mark.asyncio
async def test_scheduled_source_preserves_start_authority_and_current_terminal_evidence():
    projection = source()
    reader = AsyncMock(return_value=admitted())
    session = AsyncMock()
    service = TemporalExecutionService(
        session, client_adapter=SimpleNamespace(read_workflow_start_input=reader)
    )
    result = await service.read_scheduled_execution_source(projection)
    reader.assert_awaited_once_with("mm:scheduled", run_id="reset-run")
    assert result.parameters == admitted()["initial_parameters"]
    assert result.memo["task_input_snapshot_ref"] == "art_snapshot"
    assert result.memo["failed_run_recovery_manifest_ref"] == "art_manifest"
    assert result.input_ref == "art_original" and result.plan_ref == "art_compiled_plan"
    assert (
        result.state == projection.state
        and result.finish_summary_json == projection.finish_summary_json
    )
    assert projection.parameters == {"model": "compact projection only"}
    assert not session.mock_calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    ["foreign_owner", "wrong_type", "no_inputs", "unavailable", "projection_only"],
)
async def test_unverifiable_scheduled_source_cannot_authorize_recovery(invalid):
    projection = source()
    payload = admitted()
    if invalid == "foreign_owner":
        payload["owner_user_id"] = "someone-else"
    if invalid == "wrong_type":
        payload["workflow_type"] = "foreign.workflow"
    if invalid == "no_inputs":
        payload.pop("initial_parameters")
    if invalid == "projection_only":
        projection.source_mode = TemporalExecutionProjectionSourceMode.PROJECTION_ONLY
    reader = AsyncMock(
        return_value=payload,
        side_effect=(
            RuntimeError("history unavailable") if invalid == "unavailable" else None
        ),
    )
    service = TemporalExecutionService(
        AsyncMock(), client_adapter=SimpleNamespace(read_workflow_start_input=reader)
    )
    with pytest.raises(TemporalExecutionValidationError):
        await service.read_scheduled_execution_source(projection)
