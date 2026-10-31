"""A failed provider stop never substitutes for fenced host-stop evidence."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.omnigent.bridge_proxy import OmnigentBridgeError
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.runtime_bindings import RuntimeBindingState, stable_binding_id
from moonmind.omnigent.session_cleanup import OmnigentSessionCleanupService
from moonmind.schemas.agent_runtime_models import AgentRunResult
from tests.unit.omnigent.test_generic_platform_production_services import (
    _PUSHED_PUBLICATION,
    _generic_publication_harness,
    _plan,
    _prime_attested_host_binding,
)


async def _unreachable_session(monkeypatch, mode, *, host_error=None):
    monkeypatch.setattr(
        "moonmind.omnigent.realizers.generic_host."
        "_PREDECESSOR_STOP_RETRY_DELAYS_SECONDS",
        (0, 0, 0),
    )
    harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
    plan = _plan("opencode-go/model")
    if mode != "execute":
        await _prime_attested_host_binding(harness, plan)

    async def stop_session(session_id):
        assert session_id == "session-1"
        harness.events.append("session-stop-unavailable")
        # Upstream now returns this after the attempt's host goes offline.
        raise OmnigentBridgeError(
            "Couldn't confirm the session's runner stopped. Please try again.",
            status_code=503,
        )

    async def host_cleanup(**kwargs):
        harness.events.append("host-stop-attempted")
        assert kwargs["host_context"]["containerName"] == "mm-host-1"
        assert kwargs["host_lease_generation"] == 1
        if host_error is not None:
            raise host_error
        harness.events.append("host-cleaned")
        return {"containerRemoved": True, "hostLogs": "saved runner output"}

    async def driver(_request, *, session_authority_sink):
        await session_authority_sink.session_created("session-1")
        return AgentRunResult(
            summary="The Omnigent host was lost before the turn finished.",
            failureClass="integration_error",
            providerErrorCode="OMNIGENT_SESSION_HOST_LOST",
            retryRecommendation="retry_step_execution",
        )

    evidence = []

    async def write_json(**kwargs):
        evidence.append(kwargs)
        return "artifact:cleanup-evidence"

    artifacts = harness.realizer._artifacts or SimpleNamespace()
    artifacts.write_json = write_json
    artifacts.write_text = AsyncMock(return_value="artifact:host-logs")
    harness.realizer._artifacts = artifacts
    harness.realizer._session_driver = driver
    harness.realizer._session_cleanup = OmnigentSessionCleanupService(
        SimpleNamespace(stop_session=stop_session)
    )
    harness.realizer._host_runtime.cleanup = host_cleanup
    binding_id = stable_binding_id(
        execution_plan_ref=plan.planRef,
        idempotency_key=harness.publish_request.idempotency_key,
    )

    async def run():
        if mode != "reconcile":
            return await harness.realizer.execute(harness.publish_request, plan)
        binding = await harness.runtime_store.get(binding_id)
        binding = await harness.runtime_store.update(
            binding_id,
            expected_revision=binding.revision,
            expected_fencing_generation=binding.fencingGeneration,
            state=RuntimeBindingState.session_creating,
        )
        await harness.runtime_store.update(
            binding_id,
            expected_revision=binding.revision,
            expected_fencing_generation=binding.fencingGeneration,
            state=RuntimeBindingState.session_active,
            updates={
                "omnigentSessionId": "session-1",
                "phaseResults": {
                    **(binding.phaseResults or {}),
                    "workspace": harness.publish_request.model_dump(
                        mode="json", by_alias=True, exclude_none=True
                    ),
                },
            },
        )
        harness.realizer._provider_leases.release_from_binding = AsyncMock(
            side_effect=lambda _leases: harness.events.append("provider-released")
        )
        return await harness.realizer.reconcile(plan.planRef, binding_id)

    return harness, binding_id, evidence, run


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["execute", "resume", "reconcile"])
async def test_unreachable_session_uses_confirmed_owned_host_stop(monkeypatch, mode):
    harness, binding_id, evidence, run = await _unreachable_session(monkeypatch, mode)
    result = await run()
    binding = await harness.runtime_store.get(binding_id)
    lease = await harness.host_leases.get(binding.hostLeaseRef)

    assert binding.state is RuntimeBindingState.cleaned
    assert lease.status == "cleaned"
    assert binding.phaseResults["saved"]["archiveRef"] == "artifact://saved"
    assert harness.events.count("host-cleaned") == 1
    assert harness.events.count("provider-released") == 1
    assert (
        harness.events.index("host-cleaned")
        < harness.events.index("credentials-cleaned")
        < harness.events.index("provider-released")
    )
    if mode != "reconcile":
        assert result.retry_recommendation == "retry_step_execution"
        assert "successorBlocked" not in binding.phaseResults
        cleanup = next(
            item for item in evidence if item["name"] == "generic-host-cleanup.json"
        )
        session = cleanup["payload"]["results"]["session"]
        assert session == {
            "drained": False,
            "sessionId": "session-1",
            "reason": "host_removed_after_drain_failed",
            "errorCode": "OMNIGENT_CLEANUP_DEFERRED",
            "hostLeaseRef": lease.leaseRef,
            "hostLeaseGeneration": lease.launchGeneration,
        }
        assert (
            cleanup["payload"]["results"]["host"]["hostLogsRef"] == "artifact:host-logs"
        )
        assert (
            harness.realizer._artifacts.write_text.await_args.kwargs["payload"]
            == "saved runner output"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["execute", "resume", "reconcile"])
@pytest.mark.parametrize(
    "host_error",
    [
        RuntimeError("docker daemon unavailable"),
        HarnessPlatformError(
            "stale owner cannot clean a newer Omnigent host",
            code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
        ),
    ],
)
async def test_unreachable_session_holds_capacity_without_host_stop(
    monkeypatch, mode, host_error
):
    harness, binding_id, _evidence, run = await _unreachable_session(
        monkeypatch, mode, host_error=host_error
    )
    if mode == "reconcile":
        with pytest.raises(type(host_error), match=str(host_error)):
            await run()
    else:
        result = await run()
        assert result.retry_recommendation == "do_not_retry"
        assert result.metadata["successorBlocked"]["reason"] == (
            "predecessor_host_stop_unconfirmed"
        )
    binding = await harness.runtime_store.get(binding_id)
    lease = await harness.host_leases.get(binding.hostLeaseRef)
    assert binding.state is RuntimeBindingState.cleanup_pending
    assert lease.status == "cleanup_pending"
    assert "credentials-cleaned" not in harness.events
    assert "provider-released" not in harness.events


@pytest.mark.asyncio
async def test_janitor_resumes_workspace_save_after_fallback_host_removal(monkeypatch):
    harness, binding_id, _evidence, run = await _unreachable_session(
        monkeypatch, "reconcile"
    )
    save = harness.realizer._save_or_retain
    harness.realizer._save_or_retain = AsyncMock(
        side_effect=RuntimeError("worker stopped before workspace save")
    )
    with pytest.raises(RuntimeError, match="before workspace save"):
        await run()
    pending = await harness.runtime_store.get(binding_id)
    assert pending.state is RuntimeBindingState.cleanup_pending
    assert (await harness.host_leases.get(pending.hostLeaseRef)).status == "cleaned"
    assert "saved" not in pending.phaseResults
    assert "credentials-cleaned" not in harness.events
    assert "provider-released" not in harness.events

    harness.realizer._save_or_retain = save
    await harness.realizer.reconcile(pending.executionPlanRef, binding_id)

    cleaned = await harness.runtime_store.get(binding_id)
    assert cleaned.state is RuntimeBindingState.cleaned
    assert cleaned.phaseResults["saved"]["archiveRef"] == "artifact://saved"
    assert harness.events.count("host-cleaned") == 1
    assert harness.events.count("provider-released") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("has_lease", [False, True])
async def test_failed_drain_without_owned_host_authority_stays_deferred(
    monkeypatch, has_lease
):
    harness, binding_id, _evidence, _run = await _unreachable_session(
        monkeypatch, "resume"
    )
    binding = (await harness.runtime_store.get(binding_id)).model_copy(
        update={"omnigentSessionId": "session-1"}
    )
    lease = await harness.host_leases.get(binding.hostLeaseRef) if has_lease else None
    with pytest.raises(HarnessPlatformError) as exc:
        await harness.realizer._drain_bound_session(binding, lease, None)
    assert exc.value.code == HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED
    assert "host-stop-attempted" not in harness.events
    assert "credentials-cleaned" not in harness.events
    assert "provider-released" not in harness.events
