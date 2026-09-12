"""Replay capacity rejection through the real turn, lifecycle and cleanup owners."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from moonmind.omnigent.control_plane import OmnigentControlPlaneStore
from moonmind.omnigent.control_plane.cleanup_authority import CanonicalCleanupAuthority
from moonmind.omnigent.control_plane.identities import canonical_omnigent_session_id
from moonmind.omnigent.control_plane.turn_commands import (
    CanonicalSessionBootstrap,
    CanonicalTurnCommandService,
)
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.realizers.registry import OmnigentExecutionRealizerRegistry
from moonmind.omnigent.runtime_bindings import RuntimeBindingState, stable_binding_id
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.activities.omnigent_activities import (
    omnigent_profile_bound_execute_activity,
)
from tests.unit.omnigent.test_canonical_turn_routing import (  # noqa: F401
    _engine,
    session_factory,
)
from tests.unit.omnigent.test_generic_platform_production_services import (
    _exact_plan,
    _generic_publication_harness,
    _plan,
    _prime_attested_host_binding,
)


REPLAY = json.loads(
    (
        Path(__file__).parents[2] / "integration/reliability/replays/"
        "omnigent-capacity-readmission-turn/manifest.json"
    ).read_text()
)


def admitted_request(request, plan, epoch):
    payload = request.model_dump(by_alias=True, mode="json")
    payload["omnigentExecutionPlan"] = {
        "planRef": plan.planRef,
        "planDigest": "sha256:" + plan.planRef.rsplit(":", 1)[-1],
        "planArtifactRef": "artifact:plan",
        "taskInputSnapshotRef": "artifact:input",
        "taskInputSnapshotDigest": "sha256:" + "a" * 64,
    }
    payload["admittedProviderCapacity"] = {
        "schemaVersion": "admitted-provider-capacity/v2",
        "leaseOwnerId": "agent-run-1",
        "profiles": [
            {
                "providerProfileRef": "opencode-go-primary",
                "providerRuntimeId": "opencode",
                "capacityScopeRef": "provider-profile:opencode-go-primary",
                "credentialGeneration": 4,
            }
        ],
        "executionPlanRef": plan.planRef,
        "agentRunWorkflowId": "agent-run-1",
        "agentRunRunId": "run-1",
        "stepExecutionId": request.correlation_id,
        "idempotencyKey": request.idempotency_key,
        "admissionEpoch": epoch,
    }
    return AgentExecutionRequest.model_validate(payload)


async def replay_capacity_readmission(store, monkeypatch):
    """Only external host/provider services are simulated; owners persist real state."""

    harness = await _generic_publication_harness({})
    harness.publish_request = harness.publish_request.model_copy(
        update={
            "parameters": {**harness.publish_request.parameters, "publishMode": "none"},
        }
    )
    realizer = harness.realizer
    realizer._turn_commands = CanonicalTurnCommandService(store)
    realizer._cleanup_authority = CanonicalCleanupAuthority(store)
    plan = _plan("opencode-go/model")
    registry = OmnigentExecutionRealizerRegistry()
    registry.register(realizer)

    class PlanStore:
        async def load(self, ref):
            assert ref == plan.planRef
            return plan

    monkeypatch.setattr(
        "moonmind.omnigent.harness_platform.stores.DbExecutionPlanStore",
        lambda _session_factory: PlanStore(),
    )
    monkeypatch.setattr(
        "moonmind.omnigent.realizers.registry.get_default_registry",
        lambda: registry,
    )

    async def execute(request):
        return await ActivityEnvironment().run(
            omnigent_profile_bound_execute_activity, request
        )

    acquire = harness.host_leases.acquire

    async def reject_capacity(**kwargs):
        raise HarnessPlatformError(
            REPLAY["capacityFailure"]["message"], code=REPLAY["capacityFailure"]["code"]
        )

    monkeypatch.setattr(harness.host_leases, "acquire", reject_capacity)
    first = admitted_request(harness.publish_request, plan, REPLAY["epochs"][0])
    rejected = await execute(first)
    assert rejected.provider_error_code == REPLAY["capacityFailure"]["code"]
    assert rejected.retry_recommendation == "wait_for_host_capacity"
    first_session_id = realizer._canonical_session_id(first)
    async with store.transaction() as repos:
        first_session = await repos.sessions.get(first_session_id)
        first_cleanup = await repos.cleanup.get(first_session_id)
    assert first_session.provider_session_ref is None
    assert first_cleanup.state == "complete"
    assert "host-ready" not in harness.events
    assert "credentials-cleaned" in harness.events
    assert "provider-released" in harness.events

    # The same immutable input is re-admitted; only the workflow-owned epoch changes.
    monkeypatch.setattr(harness.host_leases, "acquire", acquire)
    second = admitted_request(harness.publish_request, plan, REPLAY["epochs"][1])
    result = await execute(second)
    assert result.failure_class is None
    assert result.metadata["omnigentSessionId"] == "session-1"
    second_session_id = realizer._canonical_session_id(second)
    assert second_session_id != first_session_id
    async with store.transaction() as repos:
        assert await repos.sessions.get(first_session_id) == first_session
        assert await repos.cleanup.get(first_session_id) == first_cleanup
        second_session = await repos.sessions.get(second_session_id)
        second_cleanup = await repos.cleanup.get(second_session_id)
        commands = await repos.commands.list_for_session(second_session_id)
    assert second_session.provider_session_ref == "session-1"
    assert second_cleanup.state == "complete"
    assert len(commands) == 1
    assert commands[0].provider_receipt_id == "session-1"

    for epoch in REPLAY["epochs"]:
        binding = await harness.runtime_store.get(
            stable_binding_id(
                execution_plan_ref=plan.planRef,
                idempotency_key=first.idempotency_key,
                admission_epoch=epoch,
            )
        )
        assert binding.state is RuntimeBindingState.cleaned

    # Activity redelivery reads the terminal receipt without another billed turn.
    before = list(harness.events)
    duplicate = await execute(second)
    assert duplicate.failure_class is None
    assert harness.events == before
    assert harness.events.count("host-ready") == 1
    assert harness.events.count("message-completed") == 1


@pytest.mark.asyncio
async def test_capacity_readmission_survives_completed_cleanup(
    session_factory, monkeypatch
):
    await replay_capacity_readmission(
        OmnigentControlPlaneStore(session_factory), monkeypatch
    )


@pytest.mark.parametrize("epoch", [None, 0, 1])
def test_historical_and_first_admission_keep_durable_identity(epoch):
    from moonmind.omnigent.realizers.turn_delivery import canonical_turn_idempotency_key

    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="workflow-1",
        idempotencyKey="idem-1",
    )
    if epoch is not None:
        request = admitted_request(request, _plan("opencode-go/model"), epoch)
    assert canonical_turn_idempotency_key(request) == "idem-1"
    assert (
        canonical_omnigent_session_id(
            workflow_id="workflow-1",
            step_execution_id="workflow-1",
            agent_run_id="workflow-1",
            admission_epoch=epoch or 0,
        )
        == "oms_d75bad6fe38fe89bfaf524e9fe4f1965dad05d43"
    )


@pytest.mark.asyncio
async def test_existing_later_epoch_keeps_legacy_session_and_command(session_factory):
    """An old Activity can resume its durable result after the identity cutover."""

    harness = await _generic_publication_harness({})
    plan = _exact_plan("opencode-go/model")
    request = admitted_request(harness.publish_request, plan, 2)
    store = OmnigentControlPlaneStore(session_factory)
    commands = CanonicalTurnCommandService(store)
    harness.realizer._turn_commands = commands
    harness.realizer._cleanup_authority = CanonicalCleanupAuthority(store)
    claim = await commands.claim(
        workflow_id=request.correlation_id,
        provider_session_ref="",
        chat_binding_id=None,
        command_type="execute_admitted_plan",
        turn_source="initial",
        idempotency_key=request.idempotency_key,
        payload_digest=plan.planRef,
        bootstrap=CanonicalSessionBootstrap(
            provider="omnigent",
            step_execution_id=request.correlation_id,
            agent_run_id=request.correlation_id,
            source_idempotency_key=request.idempotency_key,
            execution_plan_ref=plan.planRef,
        ),
    )
    await commands.attach_provider_session(
        session_id=claim.session_id,
        provider_session_ref="session-1",
        fencing_generation=claim.fencing_generation,
    )
    # The worker died after persisting terminal evidence but before settlement.
    await _prime_attested_host_binding(harness, plan, admission_epoch=2)
    binding = await harness.runtime_store.get(
        stable_binding_id(
            execution_plan_ref=plan.planRef,
            idempotency_key=request.idempotency_key,
            admission_epoch=2,
        )
    )
    await harness.runtime_store.update(
        binding.bindingId,
        expected_revision=binding.revision,
        expected_fencing_generation=binding.fencingGeneration,
        updates={
            "omnigentSessionId": "session-1",
            "terminalResult": {
                "summary": "previously completed",
                "metadata": {"omnigentSessionId": "session-1"},
            },
        },
    )
    result = await harness.realizer.execute(request, plan)
    assert result.summary == "previously completed"
    assert "host-ready" not in harness.events
    assert "message-completed" not in harness.events
    async with store.transaction() as repos:
        assert (
            await repos.sessions.get(harness.realizer._canonical_session_id(request))
            is None
        )
        cleanup = await repos.cleanup.get(claim.session_id)
        journal = await repos.commands.list_for_session(claim.session_id)
    assert cleanup.state == "complete"
    assert len(journal) == 1
    assert journal[0].provider_receipt_id == "session-1"
