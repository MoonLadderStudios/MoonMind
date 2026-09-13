"""An interrupted allocation must prove cleanup before another admission."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
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
@pytest.mark.parametrize("cleanup_proven", [True, False])
async def test_interrupted_allocation_requires_durable_cleanup(
    monkeypatch, cleanup_proven
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
    if not cleanup_proven:
        harness.realizer._host_runtime.cleanup = AsyncMock(
            side_effect=RuntimeError("daemon unavailable")
        )
        with pytest.raises(HarnessPlatformError):
            await harness.realizer._execute_lifecycle(request, plan)
        assert (
            await harness.runtime_store.get(key)
        ).state is RuntimeBindingState.cleanup_pending
        assert "provider-released" not in harness.events
        return

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
    "mismatch", [None, "plan", "epoch", "binding", "missing_receipt", "success"]
)
async def test_cleanup_receipt_controls_workflow_readmission(monkeypatch, mismatch):
    _configure_workflow_runtime(monkeypatch)

    class Run(_ExecutingRun):
        async def _execute_routed_activity(self, name, payload=None, **kwargs):
            if name.startswith("integration.omnigent.") and not self.executions:
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
    if mismatch:
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
