"""A replacement delivery resumes only the unfinished finalization phases.

MoonLadderStudios/MoonMind#4627: durable compute, save and publication
receipts are read before anything retries. A worker replaced after the
provider turn finished never submits another turn or saves twice, and a push
whose acknowledgement was lost reconciles through the same publisher.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.runtime_bindings import (
    RuntimeBindingSessionAuthoritySink,
    RuntimeBindingState,
)
from moonmind.schemas.agent_runtime_models import AgentRunResult
from tests.unit.omnigent.test_generic_platform_production_services import (
    _PUSHED_PUBLICATION,
    _generic_publication_harness,
    _plan,
)

pytestmark = pytest.mark.asyncio


async def _interrupted_after(harness, plan, phases):
    """Persist the receipts a replaced worker left before publication."""

    store = harness.runtime_store
    binding = await store.create_initial(
        execution_plan_ref=plan.planRef,
        idempotency_key=harness.publish_request.idempotency_key,
        provider_leases={},
    )
    sink = RuntimeBindingSessionAuthoritySink(store, binding)
    await sink.record_phase(
        "workspace",
        {"workspaceSpec": dict(harness.publish_request.workspace_spec)},
    )
    await sink.record_phase(
        "compute",
        AgentRunResult(summary="verified candidate").model_dump(
            mode="json", by_alias=True
        ),
    )
    if "saved" in phases:
        await sink.record_phase(
            "saved",
            {"kind": "worktree_archive", "archiveRef": "artifact://saved-before-loss"},
        )
    binding = sink.binding
    for state in (RuntimeBindingState.cleanup_pending, RuntimeBindingState.cleaned):
        binding = await store.update(
            binding.bindingId,
            expected_revision=binding.revision,
            expected_fencing_generation=binding.fencingGeneration,
            state=state,
        )
    return binding


@pytest.mark.parametrize("phases", [("compute",), ("compute", "saved")])
async def test_replacement_after_compute_resumes_only_unfinished_phases(phases):
    harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
    plan = _plan("opencode-go/model")
    binding = await _interrupted_after(harness, plan, phases)

    result = await harness.realizer.execute(harness.publish_request, plan)

    events = harness.events
    # No new provider turn, host or provider capacity for completed compute.
    assert "message-completed" not in events
    assert "host-ready" not in events
    assert "provider-acquired" not in events
    assert events.count("workspace-saved") == (0 if "saved" in phases else 1)
    assert events.count("workspace-published") == 1
    assert result.metadata["push_status"] == "pushed"
    saved = (await harness.runtime_store.get(binding.bindingId)).phaseResults["saved"]
    expected_archive = (
        "artifact://saved-before-loss" if "saved" in phases else "artifact://saved"
    )
    assert saved["archiveRef"] == expected_archive

    # A further redelivery reads the publication receipt and publishes nothing.
    again = await harness.realizer.execute(harness.publish_request, plan)
    assert events.count("workspace-published") == 1
    assert again == result


async def test_lost_push_acknowledgement_reconciles_through_the_same_publisher(
    monkeypatch,
):
    monkeypatch.setattr(
        "moonmind.omnigent.realizers.generic_host.asyncio.sleep",
        _no_sleep,
    )
    harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
    plan = _plan("opencode-go/model")
    binding = await _interrupted_after(harness, plan, ("compute", "saved"))
    publisher = harness.realizer._workspace_publisher
    publish = publisher.publish_request_workspace
    calls = []

    async def push_then_lose_ack(**kwargs):
        calls.append(kwargs)
        result = await publish(**kwargs)
        if len(calls) == 1:
            # The remote accepted the push; its acknowledgement never arrived.
            # The publisher raises this code when it cannot verify the head.
            raise HarnessPlatformError(
                "repository publication could not be verified",
                code="OMNIGENT_REPOSITORY_PUBLICATION_UNVERIFIED",
            )
        return result

    publisher.publish_request_workspace = push_then_lose_ack

    result = await harness.realizer.execute(harness.publish_request, plan)

    assert len(calls) == 2
    assert result.failure_class is None
    assert result.metadata["push_status"] == "pushed"
    phases = (await harness.runtime_store.get(binding.bindingId)).phaseResults
    assert phases["publication_failure:0"] == {
        "code": "OMNIGENT_REPOSITORY_PUBLICATION_UNVERIFIED"
    }
    assert "publication" in phases
    assert "message-completed" not in harness.events


async def _no_sleep(_seconds):
    return None
