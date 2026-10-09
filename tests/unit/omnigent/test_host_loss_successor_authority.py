"""A host-loss successor waits for the lost attempt's confirmed stop (#4627).

``retry_step_execution`` lets the Run workflow start a new Step Execution of
the same logical step. An expired lease or offline projection does not prove
the lost attempt's container stopped writing, so the real generic realizer
grants that authority only after its host container is confirmed removed. A
lost stop acknowledgement resumes the same cleanup within bounds; persistent
failure withholds the successor instead of starting parallel compute.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.realizers import generic_host
from moonmind.omnigent.runtime_bindings import RuntimeBindingState
from moonmind.schemas.agent_runtime_models import AgentRunResult
from tests.unit.omnigent.test_generic_platform_production_services import (
    _PUSHED_PUBLICATION,
    _generic_publication_harness,
    _plan,
)


async def _host_lost_harness(monkeypatch):
    monkeypatch.setattr(
        generic_host, "_STOP_RECONCILIATION_RETRY_DELAYS_SECONDS", (0, 0), raising=False
    )
    harness = await _generic_publication_harness(_PUSHED_PUBLICATION)

    async def host_lost_session(_request, *, session_authority_sink, **_kwargs):
        await session_authority_sink.session_created("session-1")
        harness.events.append("host-lost")
        return AgentRunResult(
            summary="Omnigent session host was lost before the turn finished.",
            failureClass="integration_error",
            providerErrorCode="OMNIGENT_SESSION_HOST_LOST",
            retryRecommendation="retry_step_execution",
            metadata={"omnigentSessionId": "session-1"},
        )

    harness.realizer._session_driver = host_lost_session
    return harness


def _host_cleanup(harness, failures: int):
    calls: list[int] = []

    async def cleanup(**_kwargs):
        calls.append(len(calls) + 1)
        if len(calls) <= failures:
            # The Docker daemon or its acknowledgement is unavailable: the
            # container may still be running with workspace and credentials.
            raise RuntimeError("docker daemon unavailable")
        harness.events.append("host-cleaned")
        return {"containerRemoved": True}

    harness.realizer._host_runtime.cleanup = cleanup
    return calls


async def _binding_and_lease(harness, result):
    binding = await harness.runtime_store.get(result.metadata["runtimeBindingRef"])
    lease = await harness.host_leases.get(binding.hostLeaseRef)
    return binding, lease


@pytest.mark.asyncio
async def test_lost_stop_ack_resumes_cleanup_before_successor_authority(monkeypatch):
    harness = await _host_lost_harness(monkeypatch)
    calls = _host_cleanup(harness, failures=1)

    result = await harness.realizer._execute_lifecycle(
        harness.publish_request, _plan("opencode-go/model")
    )

    assert result.retry_recommendation == "retry_step_execution"
    assert result.provider_error_code == "OMNIGENT_SESSION_HOST_LOST"
    assert result.metadata["savedWorkspaceCheckpoint"]["archiveRef"] == (
        "artifact://saved"
    )
    assert len(calls) == 2
    binding, lease = await _binding_and_lease(harness, result)
    assert lease.status == "cleaned"
    assert binding.state is RuntimeBindingState.cleaned
    # The workspace was saved before the host was released, exactly once.
    assert harness.events.count("workspace-saved") == 1


@pytest.mark.asyncio
async def test_unconfirmed_stop_withholds_successor_and_preserves_saved_work(
    monkeypatch,
):
    harness = await _host_lost_harness(monkeypatch)
    calls = _host_cleanup(harness, failures=99)

    result = await harness.realizer._execute_lifecycle(
        harness.publish_request, _plan("opencode-go/model")
    )

    # Bounded: the original cleanup plus one resume per configured delay.
    assert len(calls) == 3
    assert result.retry_recommendation == "delegate_to_janitor"
    assert result.provider_error_code == "OMNIGENT_CLEANUP_DEFERRED"
    assert result.metadata["successorAuthority"] == "withheld"
    assert result.metadata["interruptedProviderErrorCode"] == (
        "OMNIGENT_SESSION_HOST_LOST"
    )
    assert result.metadata["unfinishedPhase"] == "cleanup"
    assert result.metadata["workPreserved"] is True
    assert result.metadata["savedWorkspaceCheckpoint"]["archiveRef"] == (
        "artifact://saved"
    )
    binding, lease = await _binding_and_lease(harness, result)
    # Recovery stays with the existing janitor owner; nothing is released early.
    assert lease.status == "cleanup_pending"
    assert binding.state is RuntimeBindingState.cleanup_pending
    assert "provider-released" not in harness.events


@pytest.mark.asyncio
async def test_auxiliary_cleanup_failure_after_removal_keeps_successor_authority(
    monkeypatch,
):
    harness = await _host_lost_harness(monkeypatch)
    calls = _host_cleanup(harness, failures=0)
    released: list[int] = []

    async def failing_release(_leases):
        released.append(1)
        raise RuntimeError("provider capacity store unavailable")

    harness.realizer._provider_leases.release_all = failing_release

    result = await harness.realizer._execute_lifecycle(
        harness.publish_request, _plan("opencode-go/model")
    )

    # The container is confirmed removed; capacity release is the janitor's.
    assert result.retry_recommendation == "retry_step_execution"
    assert "successorAuthority" not in result.metadata
    assert len(calls) == 1
    assert released == [1]
    _binding, lease = await _binding_and_lease(harness, result)
    assert lease.status == "cleaned"


@pytest.mark.asyncio
async def test_replacement_worker_finishes_lost_attempt_stop_and_returns_its_result(
    monkeypatch,
):
    """An update replaced the worker while the lost attempt was cleaning up."""

    harness = await _host_lost_harness(monkeypatch)
    calls = _host_cleanup(harness, failures=99)
    plan = _plan("opencode-go/model")
    first = await harness.realizer._execute_lifecycle(harness.publish_request, plan)
    assert first.metadata["successorAuthority"] == "withheld"

    async def no_new_turn(*_args, **_kwargs):
        raise AssertionError("a finished attempt must not start another turn")

    harness.realizer._session_driver = no_new_turn
    calls_before = len(calls)
    _host_cleanup(harness, failures=0)

    retried = await harness.realizer._execute_lifecycle(harness.publish_request, plan)

    assert calls_before == 3
    assert retried.retry_recommendation == "retry_step_execution"
    assert retried.provider_error_code == "OMNIGENT_SESSION_HOST_LOST"
    assert retried.metadata["savedWorkspaceCheckpoint"]["archiveRef"] == (
        "artifact://saved"
    )
    binding, lease = await _binding_and_lease(harness, retried)
    assert lease.status == "cleaned"
    assert binding.state is RuntimeBindingState.cleaned
    assert harness.events.count("host-lost") == 1
