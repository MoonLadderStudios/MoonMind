"""A requested same-epoch context cannot quietly become a new conversation."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from moonmind.omnigent.session_launch import prepare_workflow_session_create
from moonmind.workflows.adapters.omnigent_agent_adapter import (
    OmnigentResolvedTarget,
    build_omnigent_selection,
)
from tests.unit.omnigent.test_session_launch_reconciliation import _request


def _fork_fixture():
    phases = {
        "runtimeContextContinuation": {
            "sourceSessionId": "source",
            "sourceRuntimeBindingRef": "binding-source",
            "epoch": "epoch",
        }
    }
    sink = SimpleNamespace(
        binding=SimpleNamespace(
            bindingId="new-binding", phaseResults=phases, omnigentSessionId=None
        )
    )

    async def record(phase, value):
        if phase in phases:
            assert phases[phase] == value
        phases[phase] = value

    async def own(session_id):
        assert sink.binding.omnigentSessionId in (None, session_id)
        sink.binding.omnigentSessionId = session_id

    sink.record_phase, sink.session_created = AsyncMock(side_effect=record), AsyncMock(
        side_effect=own
    )
    snapshot = {
        "id": "fork",
        "agent_id": "agent",
        "host_id": None,
        "runner_id": None,
        "labels": {
            "omnigent.fork.source_id": "source",
            "omnigent.fork.carry_history": "1",
        },
    }
    sessions = []

    async def fork(source, payload):
        assert source == "source"
        snapshot["title"] = payload["title"]
        sessions.append(snapshot)
        return snapshot

    async def launch(host_id, *, session_id, workspace):
        snapshot.update(host_id=host_id, runner_id="runner")
        return {"runner_id": "runner"}

    client = SimpleNamespace(
        list_sessions=AsyncMock(
            side_effect=lambda **_: {"data": sessions, "has_more": False}
        ),
        fork_session=AsyncMock(side_effect=fork),
        get_session=AsyncMock(side_effect=lambda _: snapshot),
        update_session=AsyncMock(return_value=snapshot),
        launch_runner=AsyncMock(side_effect=launch),
    )
    store = SimpleNamespace(attach_session=AsyncMock())

    async def prepare():
        request = _request()
        return await prepare_workflow_session_create(
            request=request,
            selection=build_omnigent_selection(request),
            target=OmnigentResolvedTarget(agent_id="agent", source="agent_id"),
            client=client,
            bridge=SimpleNamespace(metadata_={}, omnigent_session_id=None),
            run_store=store,
            provider_idempotency_key="new",
            session_authority_sink=sink,
        )

    return client, sink, snapshot, prepare


@pytest.mark.asyncio
async def test_unknown_fork_ack_is_reconciled_without_another_fork():
    client, sink, snapshot, prepare = _fork_fixture()
    create = client.fork_session.side_effect

    async def lost_ack(*args):
        await create(*args)
        raise httpx.ReadTimeout("ack unavailable")

    client.fork_session.side_effect = lost_ack
    with pytest.raises(httpx.ReadTimeout):
        await prepare()
    _payload, attached = await prepare()
    assert attached == "fork"
    assert (
        client.fork_session.await_count == 1 and client.launch_runner.await_count == 1
    )
    assert sink.binding.omnigentSessionId == "fork"


@pytest.mark.asyncio
async def test_unconfirmed_fork_is_not_blindly_repeated():
    from moonmind.omnigent.session_launch import OmnigentLaunchReconciliationError

    client, _sink, _snapshot, prepare = _fork_fixture()
    client.fork_session.side_effect = httpx.ReadTimeout("unknown effect")
    with pytest.raises(httpx.ReadTimeout):
        await prepare()
    with pytest.raises(OmnigentLaunchReconciliationError, match="unconfirmed"):
        await prepare()
    assert client.fork_session.await_count == 1
    client.launch_runner.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_runner_ack_reconciles_its_existing_host_binding():
    client, sink, _snapshot, prepare = _fork_fixture()
    launch = client.launch_runner.side_effect

    async def lost_ack(*args, **kwargs):
        await launch(*args, **kwargs)
        raise httpx.ReadTimeout("runner ack unavailable")

    client.launch_runner.side_effect = lost_ack
    with pytest.raises(httpx.ReadTimeout):
        await prepare()
    _payload, attached = await prepare()
    assert attached == "fork"
    assert (
        client.fork_session.await_count == 1 and client.launch_runner.await_count == 1
    )
    assert sink.binding.phaseResults["runtimeContextForkBound"]["runnerId"] == "runner"


@pytest.mark.asyncio
async def test_incomplete_lookup_never_authorizes_a_new_fork():
    from moonmind.omnigent.session_launch import OmnigentLaunchReconciliationError

    client, sink, _snapshot, prepare = _fork_fixture()
    client.list_sessions.side_effect = None
    client.list_sessions.return_value = {"data": []}
    with pytest.raises(OmnigentLaunchReconciliationError, match="incomplete"):
        await prepare()
    client.fork_session.assert_not_awaited()
    assert "runtimeContextForkIntent" not in sink.binding.phaseResults


@pytest.mark.asyncio
async def test_continuation_forks_history_and_binds_only_the_new_attempt_host():
    request = _request()
    source = {
        "sourceSessionId": "old-session",
        "sourceRuntimeBindingRef": "old-binding",
        "epoch": "epoch-1",
    }
    phases = {"runtimeContextContinuation": source}
    sink = SimpleNamespace(
        binding=SimpleNamespace(bindingId="new-binding", phaseResults=phases)
    )

    async def record(phase, value):
        phases[phase] = value

    sink.record_phase = AsyncMock(side_effect=record)
    sink.session_created = AsyncMock()
    snapshot = {
        "id": "fork",
        "title": None,
        "agent_id": "agent",
        "host_id": None,
        "runner_id": None,
        "labels": {
            "omnigent.fork.source_id": "old-session",
            "omnigent.fork.carry_history": "1",
        },
    }
    client = SimpleNamespace(
        list_sessions=AsyncMock(return_value={"data": [], "has_more": False}),
        fork_session=AsyncMock(return_value=snapshot),
        get_session=AsyncMock(return_value=snapshot),
        update_session=AsyncMock(return_value=snapshot),
        launch_runner=AsyncMock(
            return_value={"runner_id": "runner-new", "status": "launching"}
        ),
    )
    store = SimpleNamespace(attach_session=AsyncMock())
    payload, attached = await prepare_workflow_session_create(
        request=request,
        selection=build_omnigent_selection(request),
        target=OmnigentResolvedTarget(agent_id="agent", source="agent_id"),
        client=client,
        bridge=SimpleNamespace(metadata_={}, omnigent_session_id=None),
        run_store=store,
        provider_idempotency_key="canonical-new",
        session_authority_sink=sink,
    )
    assert attached == "fork"
    assert client.fork_session.await_args.args[0] == "old-session"
    assert client.fork_session.await_args.args[1]["agent_id"] == "agent"
    assert client.fork_session.await_args.args[1]["model_override"] == "default"
    assert client.fork_session.await_args.args[1]["reasoning_effort"] == "default"
    client.launch_runner.assert_awaited_once_with(
        "host", session_id="fork", workspace="/repo"
    )
    assert client.update_session.await_args.args[0] == "fork"
    assert client.update_session.await_args.args[1]["labels"] == payload["labels"]
    sink.session_created.assert_awaited_once_with("fork")
    store.attach_session.assert_awaited_once_with("step", "fork")
    assert phases["runtimeContextForkBound"]["hostId"] == "host"


def test_native_launch_requires_a_real_rebuilt_rollout_when_context_was_requested(
    tmp_path, monkeypatch
):
    from moonmind.provider_profiles.codex_configuration import require_reusable_context

    monkeypatch.chdir(tmp_path)
    home = tmp_path / "private-home"
    home.mkdir()
    with pytest.raises(ValueError, match="context"):
        require_reusable_context(home, ["app-server"])
    sessions = home / "sessions" / "2026" / "11" / "01"
    sessions.mkdir(parents=True)
    rollout = sessions / "rollout.jsonl"
    rollout.write_text(
        json.dumps(
            {"type": "session_meta", "payload": {"cwd": str(tmp_path), "id": "thread"}}
        )
        + "\n"
    )
    with pytest.raises(ValueError, match="context"):
        require_reusable_context(home, ["app-server"])
    with rollout.open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "Already completed work."}
                        ],
                    },
                }
            )
            + "\n"
        )
    require_reusable_context(home, ["app-server"])
    # Ordinary version/catalog probes must remain usable before restoration.
    require_reusable_context(tmp_path / "absent", ["--version"])


def test_compacted_native_context_remains_reusable(tmp_path, monkeypatch):
    from moonmind.provider_profiles.codex_configuration import require_reusable_context

    monkeypatch.chdir(tmp_path)
    root = tmp_path / "home" / "sessions"
    root.mkdir(parents=True)
    records = [
        {"type": "session_meta", "payload": {"cwd": str(tmp_path), "id": "thread"}},
        {
            "type": "compacted",
            "payload": {
                "message": "",
                "replacement_history": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "Verified completed work and current objective.",
                            }
                        ],
                    }
                ],
            },
        },
    ]
    (root / "rollout.jsonl").write_text(
        "\n".join(json.dumps(item) for item in records) + "\n"
    )
    require_reusable_context(tmp_path / "home", ["app-server"])


async def _context_bindings():
    from moonmind.omnigent.runtime_bindings import (
        InMemoryStableRuntimeBindingStore,
        RuntimeBindingState,
    )
    from moonmind.omnigent.runtime_context import (
        record_runtime_context_authority,
        runtime_context_reference,
    )
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    store = InMemoryStableRuntimeBindingStore()
    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": "wf",
            "idempotencyKey": "first",
            "stepExecution": {
                "workflowId": "wf",
                "runId": "run",
                "logicalStepId": "first",
                "executionOrdinal": 1,
                "stepExecutionId": "wf:run:first:execution:1",
                "runtimeContextPolicy": "fresh_agent_run",
            },
        }
    )
    leases = {
        "primary-model": {"providerProfileRef": "profile", "credentialGeneration": 3}
    }
    plan = SimpleNamespace(
        payload=SimpleNamespace(harnessId="codex-native", endpointRef="endpoint")
    )
    source = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "1" * 64,
        idempotency_key="first",
        provider_leases=leases,
    )
    source = await record_runtime_context_authority(
        request=request, plan=plan, binding=source, store=store
    )
    source = await store.update(
        source.bindingId,
        expected_revision=source.revision,
        expected_fencing_generation=source.fencingGeneration,
        state=RuntimeBindingState.failed,
        updates={
            "omnigentSessionId": "source-session",
            "terminalResult": {"summary": "first turn recorded"},
        },
    )
    source = await store.update(
        source.bindingId,
        expected_revision=source.revision,
        expected_fencing_generation=source.fencingGeneration,
        state=RuntimeBindingState.cleaned,
    )
    value = request.model_dump(mode="json", by_alias=True)
    value["idempotencyKey"] = "second"
    value["stepExecution"].update(
        logicalStepId="second",
        stepExecutionId="wf:run:second:execution:1",
        runtimeContextPolicy="reuse_session_same_epoch",
        runtimeSessionContext=runtime_context_reference(source),
    )
    successor = AgentExecutionRequest.model_validate(value)
    target = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "2" * 64,
        idempotency_key="second",
        provider_leases=leases,
    )
    return store, plan, source, target, successor


@pytest.mark.asyncio
async def test_context_reuses_no_old_resource_authority_and_preserves_the_source():
    from moonmind.omnigent.runtime_context import record_runtime_context_authority

    store, plan, source, target, request = await _context_bindings()
    bound = await record_runtime_context_authority(
        request=request, plan=plan, binding=target, store=store
    )
    assert (
        bound.phaseResults["runtimeContextContinuation"]["sourceSessionId"]
        == "source-session"
    )
    assert (
        bound.phaseResults["runtimeContextAuthority"]["epoch"]
        == "wf:run:first:execution:1"
    )
    assert bound.hostLeaseRef is None and bound.omnigentSessionId is None
    assert await store.get(source.bindingId) == source
    assert (
        await record_runtime_context_authority(
            request=request, plan=plan, binding=bound, store=store
        )
        == bound
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "workflow",
        "profile",
        "generation",
        "harness",
        "endpoint",
        "epoch",
        "active",
        "fresh",
    ],
)
async def test_context_cannot_cross_its_recorded_authority(change):
    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
    from moonmind.omnigent.runtime_context import record_runtime_context_authority
    from moonmind.omnigent.runtime_bindings import RuntimeBindingState

    store, plan, source, target, request = await _context_bindings()
    if change == "workflow":
        request = request.model_copy(
            update={
                "step_execution": request.step_execution.model_copy(
                    update={"workflow_id": "other"}
                )
            }
        )
    elif change in {"profile", "generation"}:
        leases = {
            "primary-model": {
                "providerProfileRef": "other" if change == "profile" else "profile",
                "credentialGeneration": 4 if change == "generation" else 3,
            }
        }
        target = await store.create_initial(
            execution_plan_ref=target.executionPlanRef,
            idempotency_key="changed-target",
            provider_leases=leases,
        )
    elif change == "harness":
        plan.payload.harnessId = "claude-native"
    elif change == "endpoint":
        plan.payload.endpointRef = "other"
    elif change == "epoch":
        reference = request.step_execution.runtime_session_context.model_copy(
            update={"epoch": "other"}
        )
        request = request.model_copy(
            update={
                "step_execution": request.step_execution.model_copy(
                    update={"runtime_session_context": reference}
                )
            }
        )
    elif change == "active":
        # A different live source is not a completed context, even when its
        # reference has the same well-formed shape.
        source = await store.create_initial(
            execution_plan_ref=source.executionPlanRef,
            idempotency_key="active",
            provider_leases=source.providerLeases,
        )
        reference = request.step_execution.runtime_session_context.model_copy(
            update={"runtime_binding_ref": source.bindingId}
        )
        request = request.model_copy(
            update={
                "step_execution": request.step_execution.model_copy(
                    update={"runtime_session_context": reference}
                )
            }
        )
        assert source.state is RuntimeBindingState.credentials_acquired
    else:
        request = request.model_copy(
            update={
                "step_execution": request.step_execution.model_copy(
                    update={"runtime_context_policy": "fresh_agent_run"}
                )
            }
        )
    with pytest.raises(HarnessPlatformError):
        await record_runtime_context_authority(
            request=request, plan=plan, binding=target, store=store
        )


def test_workflow_carries_context_only_for_explicit_reuse(monkeypatch):
    from moonmind.workflows.temporal.workflows import run
    from moonmind.schemas.agent_runtime_models import RuntimeSessionContextReference

    monkeypatch.setattr(run.workflow, "patched", lambda _: True)
    flow = run.MoonMindUserWorkflow()
    request = _request().model_copy(update={"step_execution": None})
    from moonmind.schemas.agent_runtime_models import AgentRuntimeStepExecutionLaunch

    step = AgentRuntimeStepExecutionLaunch(
        workflowId="wf",
        runId="run",
        logicalStepId="step",
        executionOrdinal=1,
        stepExecutionId="wf:run:step:execution:1",
        runtimeContextPolicy="reuse_session_same_epoch",
    )
    reference = RuntimeSessionContextReference(
        workflowId="wf",
        runId="run",
        stepExecutionId="first",
        runtimeBindingRef="omnigent-runtime-binding:sha256:" + "1" * 64,
        epoch="first",
    )
    flow._shared_runtime_context = reference
    request = request.model_copy(update={"step_execution": step})
    assert (
        flow._bind_shared_runtime_context(
            request
        ).step_execution.runtime_session_context
        == reference
    )
    flow._record_shared_runtime_context(request, {"metadata": {}})
    assert flow._shared_runtime_context == reference
    request = request.model_copy(
        update={
            "step_execution": step.model_copy(
                update={"runtime_context_policy": "fresh_agent_run"}
            )
        }
    )
    assert (
        flow._bind_shared_runtime_context(
            request
        ).step_execution.runtime_session_context
        is None
    )
    assert flow._shared_runtime_context is None


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupted_write", ["metadata", "runner_ack"])
async def test_execution_retry_finishes_attached_context_setup(
    monkeypatch, tmp_path, interrupted_write
):
    from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
    from moonmind.omnigent.execute import run_omnigent_execution
    from tests.unit.omnigent.test_execute import _RecordingBridgeStore

    class DeliveryLost(BaseException):
        """A dead Activity process cannot return a terminal result."""

    class SetupRecovered(BaseException):
        """Stop at the durable launch receipt before dispatching a turn."""

    client, sink, snapshot, _prepare = _fork_fixture()
    snapshot.update(status="completed", summary="reattached native context", items=[])
    store = _RecordingBridgeStore()

    async def attach(_key, session_id):
        store.row.omnigent_session_id = session_id
        return store.row

    store.attach_session = attach
    if interrupted_write == "metadata":
        client.update_session.side_effect = [DeliveryLost(), snapshot]
    else:
        launch = client.launch_runner.side_effect

        async def lose_runner_ack(*args, **kwargs):
            await launch(*args, **kwargs)
            raise DeliveryLost()

        client.launch_runner.side_effect = lose_runner_ack

    client.post_event = AsyncMock(side_effect=AssertionError("premature turn dispatch"))
    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr(
        "moonmind.omnigent.execute.OmnigentHttpClient", lambda **_: client
    )
    gateway = LocalOmnigentArtifactGateway(root=tmp_path)
    with pytest.raises(DeliveryLost):
        await run_omnigent_execution(
            _request(),
            artifact_gateway=gateway,
            run_store=store,
            session_authority_sink=sink,
        )
    assert store.row.omnigent_session_id == "fork"
    client.post_event.assert_not_awaited()
    record = sink.record_phase.side_effect

    async def observe_recovered_setup(phase, value):
        await record(phase, value)
        if phase == "runtimeContextForkBound":
            raise SetupRecovered()

    sink.record_phase.side_effect = observe_recovered_setup
    with pytest.raises(SetupRecovered):
        await run_omnigent_execution(
            _request(),
            artifact_gateway=gateway,
            run_store=store,
            session_authority_sink=sink,
        )
    assert sink.binding.phaseResults["runtimeContextForkBound"] == {
        "sessionId": "fork",
        "hostId": "host",
        "workspace": "/repo",
        "runnerId": "runner",
    }
    client.fork_session.assert_awaited_once()
    client.launch_runner.assert_awaited_once()
    client.post_event.assert_not_awaited()


@pytest.mark.parametrize("receipt_present", [False, True])
def test_controller_distinguishes_sessionless_receipts_from_pre_upgrade_results(
    monkeypatch, receipt_present
):
    from moonmind.schemas.agent_runtime_models import AgentRuntimeStepExecutionLaunch
    from moonmind.workflows.temporal.workflows import run

    monkeypatch.setattr(run.workflow, "patched", lambda _: True)
    flow = run.MoonMindUserWorkflow()
    request = _request().model_copy(
        update={
            "step_execution": AgentRuntimeStepExecutionLaunch(
                workflowId="wf",
                runId="run",
                logicalStepId="step",
                executionOrdinal=1,
                stepExecutionId="wf:run:step:execution:1",
                runtimeContextPolicy="fresh_agent_run",
            )
        }
    )
    metadata = {"runtimeBindingRef": "omnigent-runtime-binding:sha256:" + "2" * 64}
    if receipt_present:
        metadata["runtimeSessionContext"] = None
    flow._record_shared_runtime_context(request, {"metadata": metadata})
    if receipt_present:
        assert flow._shared_runtime_context is None
    else:
        assert (
            flow._shared_runtime_context.runtime_binding_ref
            == metadata["runtimeBindingRef"]
        )
