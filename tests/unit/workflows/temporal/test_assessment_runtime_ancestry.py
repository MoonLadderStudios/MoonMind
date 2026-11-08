"""Legacy session controls resolve only server-proven admitted ancestors."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio.api.failure.v1 import Failure
from temporalio.api.workflowservice.v1 import (
    DescribeNamespaceResponse,
    DescribeWorkflowExecutionResponse,
)
from temporalio.converter import DefaultFailureConverter, DefaultPayloadConverter
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError, RPCStatusCode

from api_service.db.models import TemporalExecutionCanonicalRecord
from moonmind.schemas.managed_session_models import CodexManagedSessionTurnResponse
from moonmind.workflows.temporal import activity_runtime
from tests.unit.services.test_assessment_model_delivery import source


def session_control(
    monkeypatch, *, policy=None, fault=None, root=("source", "source-run")
):
    canonical = source(policy)
    if policy is None:
        canonical.parameters = {}
    parents = {
        "actual-session": ("session-run", "agent-run", "agent-run-id"),
        "agent-run": ("agent-run-id", *root),
    }
    if fault == "cycle":
        parents["agent-run"] = ("agent-run-id", "actual-session", "session-run")
    if fault == "depth":
        parents = {
            f"level-{i}": (f"run-{i}", f"level-{i + 1}", f"run-{i + 1}")
            for i in range(20)
        }
    current = (
        ("level-0", "run-0") if fault == "depth" else ("actual-session", "session-run")
    )
    reads = []

    async def get(kind, workflow_id):
        reads.append((kind, workflow_id))
        if kind is TemporalExecutionCanonicalRecord and workflow_id == "source":
            return canonical
        if (
            fault in {"invalid-source", "wrong-source"}
            and kind is TemporalExecutionCanonicalRecord
            and workflow_id == "agent-run"
        ):
            return SimpleNamespace(
                workflow_id="unrelated" if fault == "wrong-source" else "agent-run",
                run_id="agent-run-id",
                parameters={"assessmentProject": {"private-source-canary": True}},
            )
        return None

    session = SimpleNamespace(get=AsyncMock(side_effect=get))

    @asynccontextmanager
    async def sessions():
        yield session

    async def describe(request, **_kwargs):
        identity = request.execution
        row = parents.get(identity.workflow_id)
        if row is None:
            return DescribeWorkflowExecutionResponse(
                workflow_execution_info={"execution": identity}
            )
        run, parent_id, parent_run = row
        if identity.workflow_id == "agent-run":
            if fault in {"temporary", "denied"}:
                status = (
                    RPCStatusCode.UNAVAILABLE
                    if fault == "temporary"
                    else RPCStatusCode.PERMISSION_DENIED
                )
                raise RPCError("private-parent-transport-canary", status, b"")
            if fault == "missing-parent":
                return DescribeWorkflowExecutionResponse(
                    workflow_execution_info={"execution": identity}
                )
        record = {
            "execution": {"workflow_id": identity.workflow_id, "run_id": run},
            "type": {
                "name": "MoonMind.AgentSession"
                if identity.workflow_id == "actual-session"
                else "MoonMind.AgentRun"
            },
            "parent_execution": {"workflow_id": parent_id, "run_id": parent_run},
            "parent_namespace_id": "namespace-id",
        }
        if identity.workflow_id == "agent-run":
            if fault == "wrong-workflow":
                record["execution"]["workflow_id"] = "unrelated"
            elif fault == "wrong-run":
                record["execution"]["run_id"] = "unrelated-run"
            elif fault == "cross-namespace":
                record["parent_namespace_id"] = "unrelated-namespace"
            elif fault == "missing-owner":
                record["type"]["name"] = "MoonMind.UserWorkflow"
            elif fault == "unknown-type":
                record["type"]["name"] = "MoonMind.UnregisteredWorkflow"
        return DescribeWorkflowExecutionResponse(workflow_execution_info=record)

    describe_call = AsyncMock(side_effect=describe)
    client = SimpleNamespace(
        namespace="default",
        workflow_service=SimpleNamespace(
            describe_workflow_execution=describe_call,
            describe_namespace=AsyncMock(
                return_value=DescribeNamespaceResponse(
                    namespace_info={"id": "namespace-id"}
                )
            ),
        ),
    )
    monkeypatch.setattr(activity_runtime.temporal_activity, "client", lambda: client)
    monkeypatch.setattr(
        activity_runtime.temporal_activity,
        "info",
        lambda: SimpleNamespace(
            workflow_id=current[0], workflow_run_id=current[1], namespace="default"
        ),
    )
    response = CodexManagedSessionTurnResponse(
        sessionState={
            "sessionId": "session",
            "sessionEpoch": 1,
            "containerId": "container",
            "threadId": "thread",
        },
        turnId="turn",
        status="accepted",
    )
    controller = SimpleNamespace(
        send_turn=AsyncMock(return_value=response),
        steer_turn=AsyncMock(return_value=response),
    )
    owner = activity_runtime.TemporalAgentRuntimeActivities(
        session_factory=sessions, session_controller=controller
    )
    return owner, controller, describe_call, reads, canonical


async def invoke(owner, route):
    name = f"agent_runtime_{route}_turn"
    handler = activity_runtime._bind_activity_handler(
        owner,
        func=getattr(type(owner), name),
        activity_type=f"agent_runtime.{route}_turn",
    )
    request = {
        "sessionId": "session",
        "sessionEpoch": 1,
        "containerId": "container",
        "threadId": "thread",
        "instructions": "Use the retained authorized evidence.",
    }
    if route == "steer":
        request.update(
            turnId="turn",
            metadata={
                "parentWorkflowId": "forged-unrestricted",
                "parentRunId": "forged-run",
                "assessmentProject": None,
            },
        )
    return await handler(request)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["send", "steer"])
@pytest.mark.parametrize(
    "policy", [None, {"modelDelivery": "allowed"}], ids=["ordinary", "allowed"]
)
async def test_legacy_session_controls_reach_admitted_grandparent(
    monkeypatch, route, policy
):
    owner, controller, describe, reads, _ = session_control(monkeypatch, policy=policy)
    result = await invoke(owner, route)
    assert result.status == "accepted"
    getattr(controller, f"{route}_turn").assert_awaited_once()
    assert [
        (c.args[0].execution.workflow_id, c.args[0].execution.run_id)
        for c in describe.await_args_list
    ] == [("actual-session", "session-run"), ("agent-run", "agent-run-id")]
    assert reads[-1] == (TemporalExecutionCanonicalRecord, "source")
    assert not any(workflow_id.startswith("forged") for _, workflow_id in reads)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["send", "steer"])
@pytest.mark.parametrize(
    "policy,reason",
    [
        ({"modelDelivery": "disabled"}, "disabled"),
        ({"modelDelivery": "local_only"}, "Local-only"),
        ({"allowedProfileRefs": ["selected"]}, "cannot verify"),
    ],
    ids=["disabled", "local", "restricted"],
)
async def test_legacy_session_controls_keep_grandparent_constraints(
    monkeypatch, route, policy, reason
):
    owner, controller, describe, _, canonical = session_control(
        monkeypatch, policy=policy
    )
    with pytest.raises(ApplicationError, match=reason) as raised:
        await invoke(owner, route)
    assert raised.value.non_retryable is True
    assert describe.await_count == 2
    assert canonical.parameters["assessmentProject"]["configuration"][
        "evidenceProcessing"
    ] == {
        "modelDelivery": policy.get("modelDelivery", "allowed"),
        "allowedProfileRefs": policy.get("allowedProfileRefs", []),
    }
    controller.send_turn.assert_not_awaited()
    controller.steer_turn.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["send", "steer"])
@pytest.mark.parametrize(
    "fault",
    [
        "cycle",
        "depth",
        "wrong-workflow",
        "wrong-run",
        "cross-namespace",
        "missing-parent",
        "denied",
        "temporary",
        "invalid-source",
        "wrong-source",
        "missing-owner",
        "unknown-type",
    ],
)
async def test_session_ancestry_failures_never_send_or_expose_source(
    monkeypatch, route, fault
):
    owner, controller, describe, _, _ = session_control(monkeypatch, fault=fault)
    with pytest.raises(ApplicationError) as raised:
        await invoke(owner, route)
    assert raised.value.non_retryable is (fault != "temporary")
    wire = Failure()
    DefaultFailureConverter().to_failure(raised.value, DefaultPayloadConverter(), wire)
    assert "private-parent-transport-canary" not in str(wire)
    assert "private-source-canary" not in str(wire)
    assert describe.await_count <= 8
    if fault in {"invalid-source", "wrong-source"}:
        assert describe.await_count == 1
    controller.send_turn.assert_not_awaited()
    controller.steer_turn.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["send", "steer"])
async def test_session_controls_do_not_reuse_a_previous_policy_lookup(
    monkeypatch, route
):
    owner, controller, describe, _, canonical = session_control(monkeypatch)
    assert (await invoke(owner, route)).status == "accepted"
    # Simulate newly unavailable canonical source. An earlier successful proof
    # does not permit another turn or substitution with authored ancestry.
    canonical.parameters = {"assessmentProject": {"private-source-canary": True}}
    with pytest.raises(ApplicationError, match="unavailable") as raised:
        await invoke(owner, route)
    assert raised.value.non_retryable is True
    getattr(controller, f"{route}_turn").assert_awaited_once()
    assert describe.await_count == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["send", "steer"])
async def test_session_controls_retain_scheduled_original_start_policy(
    monkeypatch, route
):
    from tests.unit.workflows.temporal.test_assessment_processing_continuation import (
        scheduled_history,
    )

    workflow_id, admitted, history_reads = await scheduled_history(monkeypatch)
    owner, controller, describe, _, _ = session_control(
        monkeypatch, root=(workflow_id, "continued-run")
    )
    # The continued start omits policy. Only the original admitted snapshot
    # can constrain this model route; a session locator proves no profile.
    with pytest.raises(ApplicationError, match="cannot verify") as raised:
        await invoke(owner, route)
    assert raised.value.non_retryable is True
    assert describe.await_count == 2
    assert history_reads == ["continued-run", "original-run"]
    assert admitted["assessmentProject"]["configuration"]["evidenceProcessing"][
        "allowedProfileRefs"
    ] == ["approved-profile"]
    controller.send_turn.assert_not_awaited()
    controller.steer_turn.assert_not_awaited()
