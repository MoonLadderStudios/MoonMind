"""A host-loss retry starts a successor only after the old host has stopped.

MoonLadderStudios/MoonMind#4627: an offline projection, expired lease or lost
heartbeat does not stop a partitioned host that still holds the workspace and
credentials. ``retry_step_execution`` is what lets MoonMind.Run start a new
Step Execution, so the generic realizer issues it only after its own fenced
cleanup confirmed the old host container was removed.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.runtime_bindings import RuntimeBindingState
from moonmind.schemas.agent_runtime_models import AgentRunResult
from moonmind.workflows.temporal.workflows import run as run_workflow_module
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
from tests.unit.omnigent.test_generic_platform_production_services import (
    _PUSHED_PUBLICATION,
    _exact_plan,
    _generic_publication_harness,
    _plan,
    _prime_attested_host_binding,
)

pytestmark = pytest.mark.asyncio


async def _host_lost_harness(monkeypatch, cleanup_outcomes):
    """Drive one attempt whose host is lost, with scripted Docker cleanup."""

    monkeypatch.setattr(
        "moonmind.omnigent.realizers.generic_host._SUCCESSOR_FENCE_RETRY_DELAYS",
        (0.0, 0.0),
        raising=False,
    )
    harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
    outcomes = list(cleanup_outcomes)

    async def host_lost(request, *, session_authority_sink):
        await session_authority_sink.session_created("session-1")
        harness.events.append("host-lost")
        return AgentRunResult(
            summary="Omnigent session host was lost before the turn finished",
            failureClass="integration_error",
            providerErrorCode="OMNIGENT_SESSION_HOST_LOST",
            retryRecommendation="retry_step_execution",
            metadata={"omnigentSessionId": "session-1"},
        )

    async def cleanup(**kwargs):
        outcome = outcomes.pop(0) if outcomes else "removed"
        harness.events.append(f"host-cleanup:{outcome}")
        if outcome == "unanswered":
            # The Docker daemon or a partitioned host never answered the
            # label-fenced inspect/remove: stop is unconfirmed.
            raise HarnessPlatformError(
                "host container cleanup inspection is deferred",
                code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
            )
        assert kwargs["host_context"]["containerName"] == "mm-host-1"
        return {"containerRemoved": True}

    harness.realizer._session_driver = host_lost
    harness.realizer._host_runtime.cleanup = cleanup
    return harness


def _binding(harness):
    (binding,) = harness.runtime_store._bindings.values()
    return binding


async def test_host_loss_authorizes_successor_after_confirmed_stop(monkeypatch):
    harness = await _host_lost_harness(monkeypatch, ["removed"])

    result = await harness.realizer.execute(
        harness.publish_request, _plan("opencode-go/model")
    )

    assert result.provider_error_code == "OMNIGENT_SESSION_HOST_LOST"
    assert result.retry_recommendation == "retry_step_execution"
    # The workspace is saved before the dead host is removed, so the
    # successor restores bytes instead of an empty retry.
    assert result.metadata["workPreserved"] is True
    assert result.metadata["savedWorkspaceCheckpoint"]["archiveRef"] == (
        "artifact://saved"
    )
    events = harness.events
    assert events.index("workspace-saved") < events.index("host-cleanup:removed")
    assert events.index("host-cleanup:removed") < events.index("provider-released")
    assert _binding(harness).state is RuntimeBindingState.cleaned


async def test_lost_stop_acknowledgement_resumes_cleanup_before_successor(
    monkeypatch,
):
    harness = await _host_lost_harness(monkeypatch, ["unanswered", "removed"])

    result = await harness.realizer.execute(
        harness.publish_request, _plan("opencode-go/model")
    )

    assert result.retry_recommendation == "retry_step_execution"
    events = harness.events
    assert events.count("host-cleanup:unanswered") == 1
    assert events.count("host-cleanup:removed") == 1
    # Capacity is released once, only after the confirmed removal.
    assert events.count("provider-released") == 1
    assert events.index("host-cleanup:removed") < events.index("provider-released")
    assert events.count("message-completed") == 0
    assert events.count("host-lost") == 1
    assert _binding(harness).state is RuntimeBindingState.cleaned


async def test_unconfirmed_host_stop_starts_no_successor(monkeypatch):
    harness = await _host_lost_harness(
        monkeypatch, ["unanswered", "unanswered", "unanswered"]
    )

    result = await harness.realizer.execute(
        harness.publish_request, _plan("opencode-go/model")
    )

    # A partition never answered: the old host may still be writing, so no
    # successor is authorized and its capacity stays owned for the janitor.
    assert result.failure_class == "integration_error"
    assert result.provider_error_code == "OMNIGENT_SESSION_HOST_LOST"
    assert result.retry_recommendation == "delegate_to_janitor"
    authority = result.metadata["successorAuthority"]
    assert authority == {
        "established": False,
        "unfinishedPhase": "cleanup",
        "cleanupFailureCode": "OMNIGENT_CLEANUP_DEFERRED",
        "cleanupAttempts": 3,
    }
    assert result.metadata["workPreserved"] is True
    assert harness.events.count("host-cleanup:unanswered") == 3
    assert "provider-released" not in harness.events
    binding = _binding(harness)
    assert binding.state is RuntimeBindingState.cleanup_pending
    assert binding.terminalResult["providerErrorCode"] == "OMNIGENT_SESSION_HOST_LOST"

    # MoonMind.Run's real classifier: an integration failure without the
    # explicit recommendation is not retried, so no parallel attempt starts.
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch: True)
    outputs = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert not MoonMindRunWorkflow()._activity_result_retryable(
        {"status": "FAILED", "outputs": {"error": "integration_error", **outputs}},
        failure_message="integration_error",
        tool_type="agent_runtime",
    )


@pytest.mark.parametrize(
    ("cleanup_outcomes", "expected_recommendation"),
    [
        (["unanswered", "removed"], "retry_step_execution"),
        (["unanswered"] * 3, "delegate_to_janitor"),
    ],
)
async def test_resumed_host_loss_uses_the_same_successor_fence(
    monkeypatch, cleanup_outcomes, expected_recommendation
):
    # A replacement worker resumed the attested host, which was then lost.
    harness = await _host_lost_harness(monkeypatch, cleanup_outcomes)
    plan = _exact_plan("opencode-go/model")
    await _prime_attested_host_binding(harness, plan)

    result = await harness.realizer._execute_lifecycle(harness.publish_request, plan)

    assert "host-ready" not in harness.events
    assert result.provider_error_code == "OMNIGENT_SESSION_HOST_LOST"
    assert result.retry_recommendation == expected_recommendation
    released = "provider-released" in harness.events
    assert released is (expected_recommendation == "retry_step_execution")


async def test_late_janitor_cleanup_completes_the_unconfirmed_attempt(monkeypatch):
    harness = await _host_lost_harness(
        monkeypatch, ["unanswered", "unanswered", "unanswered", "removed"]
    )
    plan = _plan("opencode-go/model")
    await harness.realizer.execute(harness.publish_request, plan)
    binding = _binding(harness)

    async def release_from_binding(provider_leases):
        assert provider_leases == binding.providerLeases
        harness.events.append("provider-released")

    harness.realizer._provider_leases.release_from_binding = release_from_binding

    # The independent janitor later observes the removal through the same
    # label- and generation-fenced cleanup owner and releases only this
    # attempt's recorded capacity.
    await harness.realizer.reconcile(plan.planRef, binding.bindingId)

    assert harness.events.count("host-cleanup:removed") == 1
    assert harness.events.count("provider-released") == 1
    assert _binding(harness).state is RuntimeBindingState.cleaned


async def test_late_predecessor_cleanup_cannot_remove_successor_host():
    from moonmind.omnigent.host_services.cleanup import (
        DockerOmnigentHostCleanupService,
    )

    commands = []

    class Backend:
        async def run(self, args, check=True):
            commands.append(list(args))
            if args[:3] == ["docker", "container", "inspect"]:
                # The name now belongs to the successor's host lease.
                return 0, "omnigent-host-lease:successor|1\n", ""
            raise AssertionError(f"unexpected Docker command {args}")

    with pytest.raises(HarnessPlatformError) as exc:
        await DockerOmnigentHostCleanupService(Backend()).cleanup(
            container_name="mm-host-1",
            host_lease_ref="omnigent-host-lease:predecessor",
            host_lease_generation=1,
            state_volume_ref="mm-state-1",
        )

    assert exc.value.code == (
        HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT.value
    )
    assert not any(command[:2] == ["docker", "rm"] for command in commands)
    assert not any(command[:3] == ["docker", "volume", "rm"] for command in commands)
