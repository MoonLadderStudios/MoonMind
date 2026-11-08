"""Legacy runtime launches retain canonical processing constraints."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.workflows.temporal import activity_runtime
from moonmind.workflows.temporal.artifacts import TemporalArtifactError
from tests.unit.services.test_assessment_model_delivery import source
from tests.unit.workflows.temporal.test_agent_runtime_activities import (
    _tier_policy_launch_payload,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["launch", "session"])
@pytest.mark.parametrize(
    "state",
    ["disabled", "unavailable", "forged-parent"],
    ids=["disabled", "unavailable", "forged-parent"],
)
async def test_legacy_launch_cannot_replace_source_policy(monkeypatch, route, state):
    canonical = source({"modelDelivery": "disabled"}) if state == "disabled" else None
    session = SimpleNamespace(get=AsyncMock(return_value=canonical))

    @asynccontextmanager
    async def sessions():
        yield session

    monkeypatch.setattr(
        activity_runtime.temporal_activity,
        "info",
        lambda: SimpleNamespace(
            workflow_id="source", workflow_run_id="source-run", namespace="default"
        ),
    )
    parent_check = AsyncMock(return_value=None)
    monkeypatch.setattr(
        activity_runtime, "_verified_temporal_parent_identity", parent_check
    )
    launcher = SimpleNamespace(launch=AsyncMock())
    controller = SimpleNamespace(launch_session=AsyncMock())
    activities = activity_runtime.TemporalAgentRuntimeActivities(
        run_launcher=launcher,
        run_supervisor=object(),
        session_controller=controller,
        session_factory=sessions,
    )
    shape = AsyncMock()
    monkeypatch.setattr(activities, "_shape_launch_session_request", shape)
    with pytest.raises(Exception, match="[Aa]ssessment|[Mm]odel delivery"):
        if route == "launch":
            payload = _tier_policy_launch_payload()
            payload["workflow_id"] = "forged"
            payload["request"]["parameters"] = {"assessmentProject": None}
            await activities.agent_runtime_launch(payload)
        else:
            await activities.agent_runtime_launch_session(
                {
                    "agentRunId": "agent-run",
                    "workflowId": "forged",
                    "sessionId": "session",
                    "threadId": "thread",
                    "workspacePath": "/work/repo",
                    "sessionWorkspacePath": "/work/session",
                    "artifactSpoolPath": "/work/artifacts",
                    "codexHomePath": "/work/home",
                    "imageRef": "moonmind:latest",
                    "metadata": {
                        "parentWorkflowId": "forged",
                        "parentRunId": "forged-run",
                        "assessmentProject": None,
                    },
                }
            )
    assert session.get.await_args_list[0].args[1] == "source"
    launcher.launch.assert_not_awaited()
    controller.launch_session.assert_not_awaited()
    shape.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_nonassessment_canonical_source_remains_launchable(monkeypatch):
    canonical = source()
    canonical.parameters = {}
    session = SimpleNamespace(get=AsyncMock(return_value=canonical))

    @asynccontextmanager
    async def sessions():
        yield session

    monkeypatch.setattr(
        activity_runtime.temporal_activity,
        "info",
        lambda: SimpleNamespace(workflow_id="source", workflow_run_id="source-run"),
    )
    record = SimpleNamespace(model_dump=lambda **_: {"status": "launching"})
    launcher = SimpleNamespace(launch=AsyncMock(return_value=(record, None, [], [])))
    activities = activity_runtime.TemporalAgentRuntimeActivities(
        run_launcher=launcher, run_supervisor=object(), session_factory=sessions
    )
    result = await activities.agent_runtime_launch(_tier_policy_launch_payload())
    assert result["status"] == "launching"
    launcher.launch.assert_awaited_once()


@pytest.mark.asyncio
async def test_legacy_child_uses_verified_parent_source(monkeypatch):
    canonical = source({"modelDelivery": "disabled"})
    session = SimpleNamespace(get=AsyncMock(side_effect=[None, None, canonical]))

    @asynccontextmanager
    async def sessions():
        yield session

    identity = SimpleNamespace(
        workflow_id="actual-child", workflow_run_id="child-run", namespace="default"
    )
    monkeypatch.setattr(activity_runtime.temporal_activity, "info", lambda: identity)
    ancestry = AsyncMock(return_value=("source", "source-run"))
    monkeypatch.setattr(
        activity_runtime, "_verified_temporal_parent_identity", ancestry
    )
    launcher = SimpleNamespace(launch=AsyncMock())
    activities = activity_runtime.TemporalAgentRuntimeActivities(
        run_launcher=launcher, run_supervisor=object(), session_factory=sessions
    )
    with pytest.raises(TemporalArtifactError, match="disabled"):
        await activities.agent_runtime_launch(_tier_policy_launch_payload())
    ancestry.assert_awaited_once_with(info=identity, require_operator_workflow=True)
    assert session.get.await_args.args[1] == "source"
    launcher.launch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["send", "steer"])
async def test_reused_session_cannot_send_when_assessment_delivery_is_blocked(
    monkeypatch, route
):
    canonical = source({"modelDelivery": "local_only"})
    session = SimpleNamespace(get=AsyncMock(return_value=canonical))

    @asynccontextmanager
    async def sessions():
        yield session

    monkeypatch.setattr(
        activity_runtime.temporal_activity,
        "info",
        lambda: SimpleNamespace(workflow_id="source", workflow_run_id="source-run"),
    )
    controller = SimpleNamespace(send_turn=AsyncMock(), steer_turn=AsyncMock())
    activities = activity_runtime.TemporalAgentRuntimeActivities(
        session_controller=controller, session_factory=sessions
    )
    request = {
        "sessionId": "session",
        "containerId": "container",
        "threadId": "thread",
        "instructions": "Retained assessment evidence",
    }
    request["sessionEpoch"] = 1
    if route == "steer":
        request["turnId"] = "turn"
    with pytest.raises(TemporalArtifactError, match="Local-only"):
        if route == "send":
            await activities.agent_runtime_send_turn(request)
        else:
            await activities.agent_runtime_steer_turn(request)
    controller.send_turn.assert_not_awaited()
    controller.steer_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_canonical_processing_does_not_serialize_source_body(monkeypatch):
    from temporalio.api.failure.v1 import Failure
    from temporalio.converter import DefaultFailureConverter, DefaultPayloadConverter
    from temporalio.exceptions import ApplicationError

    marker = "private-source-body-canary"
    canonical = source({"modelDelivery": "allowed"})
    canonical.parameters["assessmentProject"]["configuration"]["evidenceProcessing"][
        "modelDelivery"
    ] = marker
    session = SimpleNamespace(get=AsyncMock(return_value=canonical))

    @asynccontextmanager
    async def sessions():
        yield session

    monkeypatch.setattr(
        activity_runtime.temporal_activity,
        "info",
        lambda: SimpleNamespace(workflow_id="source", workflow_run_id="source-run"),
    )
    launcher = SimpleNamespace(launch=AsyncMock())
    activities = activity_runtime.TemporalAgentRuntimeActivities(
        run_launcher=launcher, run_supervisor=object(), session_factory=sessions
    )
    handler = activity_runtime._bind_activity_handler(
        activities,
        func=activity_runtime.TemporalAgentRuntimeActivities.agent_runtime_launch,
        activity_type="agent_runtime.launch",
    )
    with pytest.raises(ApplicationError) as raised:
        await handler(_tier_policy_launch_payload())
    wire = Failure()
    DefaultFailureConverter().to_failure(raised.value, DefaultPayloadConverter(), wire)
    assert wire.application_failure_info.non_retryable is True
    assert marker not in str(wire)
    assert "processing context is unavailable" in wire.message
    assert marker in str(canonical.parameters)
    launcher.launch.assert_not_awaited()


@pytest.mark.asyncio
async def test_processing_denial_uses_existing_non_retryable_activity_failure(
    monkeypatch,
):
    from temporalio.exceptions import ApplicationError

    canonical = source({"modelDelivery": "disabled"})
    session = SimpleNamespace(get=AsyncMock(return_value=canonical))

    @asynccontextmanager
    async def sessions():
        yield session

    monkeypatch.setattr(
        activity_runtime.temporal_activity,
        "info",
        lambda: SimpleNamespace(workflow_id="source", workflow_run_id="source-run"),
    )
    launcher = SimpleNamespace(launch=AsyncMock())
    activities = activity_runtime.TemporalAgentRuntimeActivities(
        run_launcher=launcher, run_supervisor=object(), session_factory=sessions
    )
    handler = activity_runtime._bind_activity_handler(
        activities,
        func=activity_runtime.TemporalAgentRuntimeActivities.agent_runtime_launch,
        activity_type="agent_runtime.launch",
    )
    with pytest.raises(ApplicationError) as raised:
        await handler(_tier_policy_launch_payload())
    assert raised.value.non_retryable is True
    assert "disabled" in str(raised.value)
    launcher.launch.assert_not_awaited()
