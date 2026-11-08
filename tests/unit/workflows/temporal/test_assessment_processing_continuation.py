"""Assessment policy follows verified Continue-As-New, never run-ID hints."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio.api.history.v1 import HistoryEvent
from temporalio.client import WorkflowExecutionStatus, WorkflowHistoryEventFilterType
from temporalio.service import RPCError, RPCStatusCode

from api_service.services.assessment_model_delivery import (
    assessment_processing_for_execution,
)
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactStateError,
)
from moonmind.workflows.temporal.client import TemporalClientAdapter
from tests.unit.services.test_assessment_model_delivery import source


def continuation_history(monkeypatch, change=None):
    previous = {"current-run": "middle-run", "middle-run": "source-run"}
    starts = {
        run: HistoryEvent(
            event_id=1,
            workflow_execution_started_event_attributes={
                "workflow_id": "source",
                "workflow_type": {"name": "MoonMind.UserWorkflow"},
                "first_execution_run_id": "source-run",
                "continued_execution_run_id": prior,
            },
        )
        for run, prior in previous.items()
    }
    starts["source-run"] = HistoryEvent(
        event_id=1,
        workflow_execution_started_event_attributes={
            "workflow_id": "source",
            "workflow_type": {"name": "MoonMind.UserWorkflow"},
            "first_execution_run_id": "source-run",
        },
    )
    closes = {
        prior: HistoryEvent(
            event_id=5,
            workflow_execution_continued_as_new_event_attributes={
                "new_execution_run_id": run
            },
        )
        for run, prior in previous.items()
    }
    descriptions = {
        run: SimpleNamespace(
            id="source", run_id=run, status=WorkflowExecutionStatus.CONTINUED_AS_NEW
        )
        for run in closes
    }
    if change in {"failed", "completed", "running", "canceled", "timed_out"}:
        descriptions["source-run"].status = getattr(
            WorkflowExecutionStatus, change.upper()
        )
    elif change == "wrong-workflow":
        starts[
            "current-run"
        ].workflow_execution_started_event_attributes.workflow_id = "another-workflow"
    elif change == "wrong-run":
        descriptions["source-run"].run_id = "another-run"
    elif change == "wrong-description-workflow":
        descriptions["source-run"].id = "another-workflow"
    elif change == "unrelated":
        starts[
            "current-run"
        ].workflow_execution_started_event_attributes.continued_execution_run_id = ""
    elif change == "wrong-successor":
        closes[
            "middle-run"
        ].workflow_execution_continued_as_new_event_attributes.new_execution_run_id = (
            "another-run"
        )
    elif change == "cycle":
        starts[
            "middle-run"
        ].workflow_execution_started_event_attributes.continued_execution_run_id = (
            "current-run"
        )
    reads = []

    async def handle(self, workflow_id, *, run_id=None, **kwargs):
        assert workflow_id == "source"
        assert run_id is not None

        async def events(**options):
            reads.append((run_id, options))
            if change == "temporary":
                raise RPCError(
                    "private-transport-canary", RPCStatusCode.UNAVAILABLE, b""
                )
            if (
                options.get("event_filter_type")
                == WorkflowHistoryEventFilterType.CLOSE_EVENT
            ):
                yield closes[run_id]
            else:
                yield starts[run_id]

        return SimpleNamespace(
            fetch_history_events=events,
            describe=AsyncMock(return_value=descriptions.get(run_id)),
        )

    monkeypatch.setattr(TemporalClientAdapter, "get_workflow_handle", handle)
    return reads


async def scheduled_history(monkeypatch):
    from uuid import uuid4

    from temporalio.api.history.v1 import WorkflowExecutionStartedEventAttributes
    from temporalio.converter import DataConverter

    definition = uuid4()
    workflow_id = f"mm:{definition}-2026-11-08T12:00:00Z"
    admitted = source({"allowedProfileRefs": ["approved-profile"]}).parameters
    admitted["system"] = {"recurrence": {"definitionId": str(definition)}}
    start = {"workflow_type": "MoonMind.UserWorkflow", "initial_parameters": admitted}
    original = WorkflowExecutionStartedEventAttributes(
        workflow_id=workflow_id,
        workflow_type={"name": "MoonMind.UserWorkflow"},
        first_execution_run_id="original-run",
    )
    original.input.payloads.extend(await DataConverter.default.encode([start]))
    continued = WorkflowExecutionStartedEventAttributes(
        first_execution_run_id="original-run"
    )
    continued.input.payloads.extend(
        await DataConverter.default.encode([{**start, "initial_parameters": {}}])
    )
    reads = []

    async def attributes(self, workflow, *, run_id):
        assert workflow == workflow_id
        reads.append(run_id)
        assert run_id in {"continued-run", "original-run"}
        return continued if run_id == "continued-run" else original

    async def client(self):
        return SimpleNamespace(data_converter=DataConverter.default)

    monkeypatch.setattr(
        TemporalClientAdapter, "_read_workflow_start_attributes", attributes
    )
    monkeypatch.setattr(TemporalClientAdapter, "get_client", client)
    return workflow_id, admitted, reads


@pytest.mark.asyncio
async def test_scheduled_continuation_retains_original_processing_not_projection(
    monkeypatch,
):
    from api_service.db.models import TemporalExecutionCanonicalRecord

    workflow_id, _admitted, reads = await scheduled_history(monkeypatch)
    projection = SimpleNamespace(run_id="stale-projection-run", parameters={})
    session = SimpleNamespace(
        get=AsyncMock(
            side_effect=lambda model, _: (
                None if model is TemporalExecutionCanonicalRecord else projection
            )
        )
    )
    processing = await assessment_processing_for_execution(
        session, workflow_id, "continued-run"
    )
    assert processing == {
        "modelDelivery": "allowed",
        "allowedProfileRefs": ["approved-profile"],
    }
    assert reads == ["continued-run", "original-run"]
    assert session.get.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["review", "launch"])
async def test_ordinary_continuation_keeps_existing_model_path_available(
    monkeypatch, route
):
    from moonmind.workflows.temporal import activity_runtime
    from moonmind.workflows.temporal.activities.step_review import (
        clear_committed_reviews,
    )
    from tests.unit.workflows.temporal.test_agent_runtime_activities import (
        _tier_policy_launch_payload,
    )

    clear_committed_reviews()
    canonical = source()
    canonical.parameters = {}

    @asynccontextmanager
    async def sessions():
        yield SimpleNamespace(get=AsyncMock(return_value=canonical))

    continuation_history(monkeypatch)
    monkeypatch.setattr(
        activity_runtime.temporal_activity,
        "info",
        lambda: SimpleNamespace(workflow_id="source", workflow_run_id="current-run"),
    )
    if route == "review":
        send = AsyncMock(
            return_value='{"verdict":"PASS","confidence":0.8,"feedback":"Reviewed"}'
        )
        owner = activity_runtime.TemporalReviewActivities(
            reviewer=SimpleNamespace(review=send), session_factory=sessions
        )
        method, payload, activity_type = (
            type(owner).step_review,
            {"node_id": "continued-review"},
            "step.review",
        )
    else:
        record = SimpleNamespace(model_dump=lambda **_: {"status": "launching"})
        send = AsyncMock(return_value=(record, None, [], []))
        owner = activity_runtime.TemporalAgentRuntimeActivities(
            run_launcher=SimpleNamespace(launch=send),
            run_supervisor=object(),
            session_factory=sessions,
        )
        method, payload, activity_type = (
            type(owner).agent_runtime_launch,
            _tier_policy_launch_payload(),
            "agent_runtime.launch",
        )
    handler = activity_runtime._bind_activity_handler(
        owner, func=method, activity_type=activity_type
    )
    result = await handler(payload)
    assert result["verdict" if route == "review" else "status"] == (
        "FULLY_IMPLEMENTED" if route == "review" else "launching"
    )
    send.assert_awaited_once()
    clear_committed_reviews()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "policy",
    [None, {"modelDelivery": "disabled"}, {"allowedProfileRefs": ["approved-profile"]}],
    ids=["legacy", "disabled", "profile-bound"],
)
async def test_continuation_retains_canonical_policy(monkeypatch, policy):
    canonical = source(policy)
    if policy is None:
        canonical.parameters = {}
    original = dict(canonical.parameters)
    session = SimpleNamespace(get=AsyncMock(return_value=canonical))
    reads = continuation_history(monkeypatch)
    actual = await assessment_processing_for_execution(session, "source", "current-run")
    assert actual == (
        None
        if policy is None
        else {"modelDelivery": "allowed", "allowedProfileRefs": [], **policy}
    )
    assert canonical.parameters == original
    assert canonical.run_id == "source-run"
    assert {run for run, _ in reads} == {"current-run", "middle-run", "source-run"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "failed",
        "completed",
        "running",
        "canceled",
        "timed_out",
        "wrong-workflow",
        "wrong-run",
        "wrong-description-workflow",
        "unrelated",
        "wrong-successor",
        "cycle",
    ],
)
async def test_unproven_continuation_never_receives_policy(monkeypatch, change):
    session = SimpleNamespace(
        get=AsyncMock(return_value=source({"allowedProfileRefs": ["approved-profile"]}))
    )
    continuation_history(monkeypatch, change)
    with pytest.raises(TemporalArtifactStateError):
        await assessment_processing_for_execution(session, "source", "current-run")


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["review", "launch"])
async def test_temporary_continuation_history_failure_retries_without_model_send(
    monkeypatch, route
):
    from temporalio.api.failure.v1 import Failure
    from temporalio.converter import DefaultFailureConverter, DefaultPayloadConverter
    from temporalio.exceptions import ApplicationError

    from moonmind.workflows.temporal import activity_runtime
    from tests.unit.workflows.temporal.test_agent_runtime_activities import (
        _tier_policy_launch_payload,
    )

    session = SimpleNamespace(
        get=AsyncMock(return_value=source({"modelDelivery": "disabled"}))
    )

    @asynccontextmanager
    async def sessions():
        yield session

    continuation_history(monkeypatch, "temporary")
    monkeypatch.setattr(
        activity_runtime.temporal_activity,
        "info",
        lambda: SimpleNamespace(workflow_id="source", workflow_run_id="current-run"),
    )
    send = AsyncMock()
    if route == "review":
        owner = activity_runtime.TemporalReviewActivities(
            reviewer=SimpleNamespace(review=send), session_factory=sessions
        )
        method, payload = type(owner).step_review, {"node_id": "review"}
        activity_type = "step.review"
    else:
        owner = activity_runtime.TemporalAgentRuntimeActivities(
            run_launcher=SimpleNamespace(launch=send),
            run_supervisor=object(),
            session_factory=sessions,
        )
        method, payload = (
            type(owner).agent_runtime_launch,
            _tier_policy_launch_payload(),
        )
        activity_type = "agent_runtime.launch"
    handler = activity_runtime._bind_activity_handler(
        owner, func=method, activity_type=activity_type
    )
    with pytest.raises(ApplicationError) as raised:
        await handler(payload)
    assert raised.value.non_retryable is False
    failure = Failure()
    DefaultFailureConverter().to_failure(
        raised.value, DefaultPayloadConverter(), failure
    )
    assert "private-transport-canary" not in str(failure)
    assert "temporarily unavailable" in failure.message
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [RPCStatusCode.UNAVAILABLE, RPCStatusCode.PERMISSION_DENIED],
    ids=["temporary", "denied"],
)
async def test_parent_history_failure_preserves_safe_retry_classification(
    monkeypatch, status
):
    from temporalio.api.failure.v1 import Failure
    from temporalio.converter import DefaultFailureConverter, DefaultPayloadConverter
    from temporalio.exceptions import ApplicationError

    from moonmind.workflows.temporal import activity_runtime
    from tests.unit.workflows.temporal.test_agent_runtime_activities import (
        _tier_policy_launch_payload,
    )

    @asynccontextmanager
    async def sessions():
        yield SimpleNamespace(get=AsyncMock(return_value=None))

    monkeypatch.setattr(
        activity_runtime.temporal_activity,
        "info",
        lambda: SimpleNamespace(
            workflow_id="actual-child", workflow_run_id="child-run", namespace="default"
        ),
    )
    describe = AsyncMock(
        side_effect=RPCError("private-parent-transport-canary", status, b"")
    )
    monkeypatch.setattr(
        activity_runtime.temporal_activity,
        "client",
        lambda: SimpleNamespace(
            namespace="default",
            workflow_service=SimpleNamespace(describe_workflow_execution=describe),
        ),
    )
    send = AsyncMock()
    owner = activity_runtime.TemporalAgentRuntimeActivities(
        run_launcher=SimpleNamespace(launch=send),
        run_supervisor=object(),
        session_factory=sessions,
    )
    handler = activity_runtime._bind_activity_handler(
        owner,
        func=type(owner).agent_runtime_launch,
        activity_type="agent_runtime.launch",
    )
    with pytest.raises(ApplicationError) as raised:
        await handler(_tier_policy_launch_payload())
    assert raised.value.non_retryable is (status != RPCStatusCode.UNAVAILABLE)
    failure = Failure()
    DefaultFailureConverter().to_failure(
        raised.value, DefaultPayloadConverter(), failure
    )
    assert "private-parent-transport-canary" not in str(failure)
    describe.assert_awaited_once()
    queried = describe.await_args.args[0].execution
    assert (queried.workflow_id, queried.run_id) == ("actual-child", "child-run")
    send.assert_not_awaited()
