"""Codex consumes admitted capacity without acquiring a second lease."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.profile_bound_execution import (
    OmnigentProfileBoundExecutionCoordinator,
)
from moonmind.provider_profiles.lease_client import (
    CredentialLease,
    CredentialLeasePurpose,
)
from moonmind.schemas.agent_runtime_models import AdmittedProviderCapacity
from moonmind.workflows.temporal.activities.omnigent_activities import (
    _typed_platform_failure_result,
)
from moonmind.workflows.temporal.activities.omnigent_session_activities import (
    _plan_capacity_authority,
)
from tests.unit.omnigent import test_profile_bound_plan_authority
from tests.unit.omnigent.test_oauth_profile_lifecycle import (
    _run_coordinator_failure_case,
)
from tests.unit.omnigent.test_provider_leases_workflow_owned_capacity import (
    _inspection,
    _session_factory,
)

admitted_codex_plan = test_profile_bound_plan_authority.admitted_codex_plan


@pytest.mark.asyncio
async def test_codex_admission_records_the_profile_capacity_authority(
    admitted_codex_plan,
    monkeypatch,
):
    import api_service.db.base as db_base

    _coordinator, request, plan, _snapshot, _launch = admitted_codex_plan
    monkeypatch.setattr(
        db_base,
        "async_session_maker",
        _session_factory(
            {
                "codex": SimpleNamespace(
                    runtime_id="codex_cli",
                    capacity_scope_ref="provider-profile:codex",
                    credential_generation=3,
                ),
            }
        ),
    )
    authority = await _plan_capacity_authority(
        plan,
        execution_profile_ref=request.execution_profile_ref,
    )
    assert authority["capacityAcquisitionOwner"] == "workflow"
    assert authority["capacityProfiles"] == [
        {
            "providerProfileRef": "codex",
            "providerRuntimeId": "codex_cli",
            "capacityScopeRef": "provider-profile:codex",
            "credentialGeneration": 3,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [True, False])
async def test_codex_consumes_the_admitted_lease_by_inspection(
    admitted_codex_plan,
    active,
):
    coordinator, request, plan, _snapshot, _launch = admitted_codex_plan
    request = request.model_copy(
        update={
            "admitted_provider_capacity": AdmittedProviderCapacity(
                leaseOwnerId="agent-run-1",
                executionPlanRef=plan.planRef,
                agentRunWorkflowId="agent-run-1",
                agentRunRunId="run-1",
                stepExecutionId="step-1",
                idempotencyKey=request.idempotency_key,
                admissionEpoch=1,
                profiles=(
                    {
                        "providerProfileRef": "codex",
                        "providerRuntimeId": "codex_cli",
                        "capacityScopeRef": "provider-profile:codex",
                        "credentialGeneration": 3,
                    },
                ),
            ),
        }
    )
    coordinator._session_factory = _session_factory(
        {
            "codex": SimpleNamespace(
                enabled=True,
                auth_state="connected",
                runtime_id="codex_cli",
                capacity_scope_ref="provider-profile:codex",
                credential_generation=3,
            ),
        }
    )
    coordinator._lease_client = SimpleNamespace(
        inspect_lease=AsyncMock(
            return_value=_inspection(
                profile_ref="codex",
                plan_ref=plan.planRef,
                idempotency_key=request.idempotency_key,
                active=active,
            )
        ),
        acquire_execution_lease=AsyncMock(
            side_effect=AssertionError(
                "admitted Codex execution must never wait for or acquire another slot"
            )
        ),
    )
    if active:
        lease = await coordinator._acquire_provider_capacity(
            request=request,
            plan=plan,
            runtime_id="codex_cli",
            profile_id="codex",
            workflow_id="workflow-1",
            step_execution_id="step-1",
        )
        assert lease.lease_id == lease.owner_id == "agent-run-1"
        assert lease.already_held is True
    else:
        with pytest.raises(HarnessPlatformError) as error:
            await coordinator._acquire_provider_capacity(
                request=request,
                plan=plan,
                runtime_id="codex_cli",
                profile_id="codex",
                workflow_id="workflow-1",
                step_execution_id="step-1",
            )
        assert error.value.code == "OMNIGENT_PROVIDER_LEASE_UNAVAILABLE"
    coordinator._lease_client.acquire_execution_lease.assert_not_awaited()
    coordinator._lease_client.inspect_lease.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fail_at,cleanup_completed",
    [
        ("none", True),
        ("session_create", True),
        ("host_stop", False),
        ("container_start", False),
    ],
)
async def test_codex_cleanup_receipt_leaves_release_to_the_workflow(
    monkeypatch,
    fail_at,
    cleanup_completed,
):
    """Real teardown controls the receipt even when execution or cleanup fails."""
    execute = OmnigentProfileBoundExecutionCoordinator.execute
    captured = {}

    async def execute_admitted(self, request):
        # Admission itself is exercised above. Isolate the lifecycle owner here
        # using the existing fake host ports, which actually stop or fail to stop.
        ticket = AdmittedProviderCapacity(
            leaseOwnerId="agent-run-1",
            agentRunWorkflowId="agent-run-1",
            agentRunRunId="run-1",
            executionPlanRef="omnigent-execution-plan:sha256:" + "a" * 64,
            stepExecutionId=request.idempotency_key,
            idempotencyKey=request.idempotency_key,
            profiles=(
                {"providerProfileRef": "codex", "providerRuntimeId": "codex_cli"},
            ),
        )
        try:
            result = await execute(
                self,
                request.model_copy(
                    update={
                        "admitted_provider_capacity": ticket,
                    }
                ),
            )
            captured["receipt"] = result.metadata[
                "admittedProviderCapacityCleanupCompleted"
            ]
            return result
        except BaseException as exc:
            captured["receipt"] = exc.admitted_provider_capacity_cleanup_completed
            if not isinstance(exc, asyncio.CancelledError):
                projected = _typed_platform_failure_result(exc)
                assert projected is not None
                assert (
                    projected.metadata["admittedProviderCapacityCleanupCompleted"]
                    is cleanup_completed
                )
            raise

    monkeypatch.setattr(
        OmnigentProfileBoundExecutionCoordinator, "execute", execute_admitted
    )
    monkeypatch.setattr(
        OmnigentProfileBoundExecutionCoordinator,
        "_acquire_provider_capacity",
        AsyncMock(
            return_value=CredentialLease(
                runtime_id="codex_cli",
                profile_id="codex",
                lease_id="agent-run-1",
                owner_id="agent-run-1",
                purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
                already_held=True,
            )
        ),
    )
    _events, actions, _owners = await _run_coordinator_failure_case(
        fail_at=fail_at,
        code="OMNIGENT_HOST_LAUNCH_FAILED",
        injected_error=(
            asyncio.CancelledError() if fail_at == "container_start" else None
        ),
    )
    assert captured["receipt"] is cleanup_completed
    assert ("host_stopped" in actions) is cleanup_completed
    assert "provider_released" not in actions
