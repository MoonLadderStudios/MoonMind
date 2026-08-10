from datetime import timezone
from unittest.mock import AsyncMock, patch

import pytest

from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactActivities,
    TemporalArtifactValidationError,
)


@pytest.mark.asyncio
async def test_persist_observed_row_activity_forwards_trusted_measurements() -> None:
    service = AsyncMock()
    activities = TemporalArtifactActivities(service)
    observations = {"authoredRequest": {"ref": "request"}}
    expected = {"ref": "artifact://temporal/row"}

    with patch(
        "moonmind.omnigent.operator_remediation_gate.persist_observed_row",
        new=AsyncMock(return_value=expected),
    ) as persist:
        result = await activities.operator_remediation_persist_observed_row(
            {
                "principal": "workflow:wf-1",
                "rowId": "diagnosis.observe-only",
                "observations": observations,
                "measurements": {
                    "startedAt": "2026-08-10T10:00:00Z",
                    "completedAt": "2026-08-10T10:01:00Z",
                    "hostMode": "on_demand",
                    "architecture": "amd64",
                    "remainingLiveResources": 0,
                    "secretFindings": 0,
                    "prohibitedAuthorityFindings": 0,
                },
            }
        )

    assert result == expected
    assert persist.await_args.args == (service,)
    call = persist.await_args.kwargs
    assert call["principal"] == "workflow:wf-1"
    assert call["row_id"] == "diagnosis.observe-only"
    assert call["observations"] == observations
    assert call["measurements"].started_at.tzinfo is timezone.utc
    assert call["measurements"].remaining_live_resources == 0


@pytest.mark.asyncio
async def test_projection_activity_forwards_only_durable_row_refs() -> None:
    service = AsyncMock()
    activities = TemporalArtifactActivities(service)
    row_refs = [{"ref": "artifact://temporal/row", "sha256": "a" * 64}]
    release_inputs = {"immutable": True, "version": "release-1"}
    expected = {"ref": "artifact://temporal/release"}

    with patch(
        "moonmind.omnigent.operator_remediation_gate.publish_release_projection",
        new=AsyncMock(return_value=expected),
    ) as publish:
        result = await activities.operator_remediation_publish_release_projection(
            {
                "principal": "workflow:wf-1",
                "rowRefs": row_refs,
                "releaseInputs": release_inputs,
            }
        )

    assert result == expected
    publish.assert_awaited_once_with(
        service,
        principal="workflow:wf-1",
        row_refs=row_refs,
        release_inputs=release_inputs,
    )


@pytest.mark.asyncio
async def test_operator_remediation_activity_rejects_incomplete_request() -> None:
    activities = TemporalArtifactActivities(AsyncMock())
    with pytest.raises(TemporalArtifactValidationError):
        await activities.operator_remediation_persist_observed_row({"rowId": "x"})
