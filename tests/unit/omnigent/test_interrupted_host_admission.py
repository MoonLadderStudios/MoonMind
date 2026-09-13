"""An interrupted allocation must prove cleanup before another admission."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.control_plane import OmnigentControlPlaneStore, TurnSource
from moonmind.omnigent.control_plane.cleanup_authority import CanonicalCleanupAuthority
from tests.unit.omnigent.test_canonical_turn_routing import (
    _engine,
    session_factory,
    service,
    _claim,
)
from moonmind.omnigent.runtime_bindings import RuntimeBindingState, stable_binding_id
from moonmind.schemas.agent_runtime_models import (
    AdmittedProviderCapacity,
    AgentRunResult,
)
from tests.unit.omnigent.test_generic_platform_production_services import (
    _PUSHED_PUBLICATION,
    _generic_publication_harness,
    _plan,
)
from tests.unit.workflows.temporal.workflows.test_agent_run_omnigent_capacity_admission import (
    _ExecutingRun,
    _capture_release_signals,
    _configure_workflow_runtime,
    _run_execution,
)
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun


def test_historical_cleanup_hint_is_not_readmission_authority():
    fixture = (
        Path(__file__).resolve().parents[2]
        / "fixtures/reliability/interrupted-host-admission.json"
    )
    recorded = AgentRunResult.model_validate(
        json.loads(fixture.read_text())["activityResult"]
    )
    assert recorded.failure_class == "integration_error"
    assert MoonMindAgentRun._omnigent_capacity_requeue_reason(recorded) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cleanup_mode", ["proven", "daemon_failure", "fenced", "lost_ack"]
)
async def test_interrupted_allocation_requires_durable_cleanup(
    monkeypatch, cleanup_mode, service, session_factory
):
    harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
    plan = _plan("opencode-go/model")
    request = harness.publish_request
    request = request.model_copy(
        update={
            "admitted_provider_capacity": AdmittedProviderCapacity(
                leaseOwnerId="agent-run-1",
                profiles=[
                    {
                        "providerProfileRef": "opencode-go-primary",
                        "providerRuntimeId": "opencode",
                    }
                ],
                executionPlanRef=plan.planRef,
                stepExecutionId="step-1",
                idempotencyKey=request.idempotency_key,
                admissionEpoch=1,
            )
        }
    )
    harness.realizer._artifacts = SimpleNamespace(
        write_json=AsyncMock(return_value="artifact:verified-cleanup"),
    )

    async def interrupted(**kwargs):
        await kwargs["authority_sink"](
            {
                "kind": "host",
                "containerName": "owned-host",
                "stateVolumeRef": "owned-state",
                "launchGeneration": 1,
            }
        )
        raise asyncio.CancelledError()

    harness.realizer._host_runtime.realize = interrupted
    monkeypatch.setattr(
        "moonmind.omnigent.activity_ownership.delivery_was_revoked", lambda: True
    )
    with pytest.raises(asyncio.CancelledError):
        await harness.realizer._execute_lifecycle(request, plan)
    key = stable_binding_id(
        execution_plan_ref=plan.planRef,
        idempotency_key=request.idempotency_key,
        admission_epoch=1,
    )
    before = await harness.runtime_store.get(key)
    assert before.state is RuntimeBindingState.host_allocating
    assert not before.omnigentSessionId
    assert "provider-released" not in harness.events
    canonical = await _claim(
        service, idempotency_key="run-1", turn_source=TurnSource.INITIAL
    )
    authority = CanonicalCleanupAuthority(OmnigentControlPlaneStore(session_factory))
    harness.realizer._cleanup_authority = authority
    monkeypatch.setattr(
        "moonmind.omnigent.realizers.generic_host.resolve_admission_session_id",
        AsyncMock(return_value=canonical.session_id),
    )
    if cleanup_mode == "fenced":
        cleanup_credentials = harness.realizer._credentials.cleanup_all

        async def cleanup_and_fence(handles):
            result = await cleanup_credentials(handles)
            await _claim(
                service,
                idempotency_key="continuation-1",
                turn_source=TurnSource.REPOSITORY_CONTINUATION,
            )
            return result

        harness.realizer._credentials.cleanup_all = cleanup_and_fence
    if cleanup_mode == "daemon_failure":
        harness.realizer._host_runtime.cleanup = AsyncMock(
            side_effect=RuntimeError("daemon unavailable")
        )
    if cleanup_mode in {"daemon_failure", "fenced"}:
        with pytest.raises(HarnessPlatformError):
            await harness.realizer._execute_lifecycle(request, plan)
        assert (
            await harness.runtime_store.get(key)
        ).state is RuntimeBindingState.cleanup_pending
        assert "provider-released" not in harness.events
        return

    if cleanup_mode == "lost_ack":
        complete = authority.complete

        async def lose_completion_ack(claim):
            assert await complete(claim)
            raise RuntimeError("cleanup settlement acknowledgement lost")

        authority.complete = lose_completion_ack
        with pytest.raises(HarnessPlatformError):
            await harness.realizer._execute_lifecycle(request, plan)
        pending = await harness.runtime_store.get(key)
        assert pending.state is RuntimeBindingState.cleanup_pending
        assert (
            pending.phaseResults["cleanupClaim"]["session_id"] == canonical.session_id
        )
        assert "provider-released" not in harness.events
        authority.complete = complete

    result = await harness.realizer._execute_lifecycle(request, plan)
    result = AgentRunResult.model_validate_json(result.model_dump_json(by_alias=True))
    recovery = result.metadata["admissionRecovery"]
    assert recovery == {
        "schemaVersion": "agent-admission-recovery/v1",
        "executionPlanRef": plan.planRef,
        "admissionEpoch": 1,
        "runtimeBindingRef": key,
        "cleanupAttestationRef": "artifact:verified-cleanup",
    }
    assert (await harness.runtime_store.get(key)).state is RuntimeBindingState.cleaned
    assert harness.events.index("host-cleaned") < harness.events.index(
        "provider-released"
    )
    # Lost result acknowledgement reuses the cleanup receipt, with no host or turn.
    replayed = await harness.realizer.execute(request, plan)
    assert replayed.metadata["admissionRecovery"] == recovery
    assert harness.events.count("host-cleaned") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mismatch",
    [
        None,
        "plan",
        "epoch",
        "binding",
        "missing_receipt",
        "success",
        "slow_cleanup",
        "exhausted",
    ],
)
async def test_cleanup_receipt_controls_workflow_readmission(monkeypatch, mismatch):
    _configure_workflow_runtime(monkeypatch)
    clock = [datetime(2026, 9, 13, tzinfo=timezone.utc)]
    monkeypatch.setattr(
        "moonmind.workflows.temporal.workflows.agent_run.workflow.now", lambda: clock[0]
    )

    class Run(_ExecutingRun):
        async def _execute_routed_activity(self, name, payload=None, **kwargs):
            if name.startswith("integration.omnigent.") and not self.executions:
                clock[0] += timedelta(
                    seconds={"slow_cleanup": 575, "exhausted": 600}.get(mismatch, 0)
                )
                capacity = payload.admitted_provider_capacity
                proof = {
                    "schemaVersion": "agent-admission-recovery/v1",
                    "executionPlanRef": capacity.execution_plan_ref,
                    "admissionEpoch": capacity.admission_epoch,
                    "runtimeBindingRef": stable_binding_id(
                        execution_plan_ref=capacity.execution_plan_ref,
                        idempotency_key=payload.idempotency_key,
                        admission_epoch=capacity.admission_epoch,
                    ),
                    "cleanupAttestationRef": "artifact:verified-cleanup",
                }
                if mismatch == "plan":
                    proof["executionPlanRef"] = "other-plan"
                if mismatch == "epoch":
                    proof["admissionEpoch"] += 1
                if mismatch == "binding":
                    proof["runtimeBindingRef"] = "other-binding"
                if mismatch == "missing_receipt":
                    proof.pop("cleanupAttestationRef")
                self.results[0] = {
                    "failureClass": "integration_error",
                    "providerErrorCode": "OMNIGENT_CLEANUP_DEFERRED",
                    "metadata": {"admissionRecovery": proof},
                }
                if mismatch == "success":
                    self.results[0].pop("failureClass")
            return await super()._execute_routed_activity(name, payload, **kwargs)

    run = Run([{}, {"summary": "done"}])
    _capture_release_signals(monkeypatch, run)
    result, _ = await _run_execution(run)
    if mismatch not in {None, "slow_cleanup"}:
        assert result.get("failureClass") == (
            None if mismatch == "success" else "integration_error"
        )
        assert len(run.executions) == 1
    else:
        assert result == {"summary": "done"}
        first, second = run.executions
        assert first.admitted_provider_capacity.admission_epoch == 1
        assert second.admitted_provider_capacity.admission_epoch == 2
        assert first.idempotency_key == second.idempotency_key
        assert first.parameters == second.parameters
        assert first.workspace_spec == second.workspace_spec
        expected = 25 if mismatch == "slow_cleanup" else 600
        assert (
            run.execution_options[1]["start_to_close_timeout"].total_seconds()
            == expected
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "revoked,cancelled", [(True, True), (False, True), (True, False)]
)
async def test_only_revoked_delivery_preserves_turn_settlement(
    monkeypatch, session_factory, revoked, cancelled
):
    from moonmind.omnigent.control_plane.turn_commands import (
        CanonicalTurnCommandService,
    )
    from moonmind.omnigent.realizers.turn_delivery import (
        deliver_canonical_turn,
        resolve_admission_session_id,
    )
    from tests.unit.omnigent.test_capacity_readmission_turn import admitted_request

    harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
    plan = _plan("opencode-go/model")
    request = admitted_request(harness.publish_request, plan, 1)
    store = OmnigentControlPlaneStore(session_factory)
    commands = CanonicalTurnCommandService(store)
    monkeypatch.setattr(
        "moonmind.omnigent.activity_ownership.delivery_was_revoked", lambda: revoked
    )
    failure = asyncio.CancelledError if cancelled else RuntimeError
    operation = AsyncMock(side_effect=failure("delivery interrupted"))
    with pytest.raises(failure):
        await deliver_canonical_turn(
            commands,
            request=request,
            plan=plan,
            command_type="execute_admitted_plan",
            operation=operation,
        )
    session_id = await resolve_admission_session_id(commands, request)
    async with store.transaction() as repos:
        journal = await repos.commands.list_for_session(session_id)
    assert len(journal) == 1
    assert journal[0].delivery_ambiguous is not (revoked and cancelled)
    assert journal[0].status == (
        "claimed" if revoked and cancelled else "delivery_unknown"
    )
