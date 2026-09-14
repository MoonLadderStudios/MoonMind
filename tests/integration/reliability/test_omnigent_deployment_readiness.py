"""Replay the replacement-worker incident through the public AgentRun entrypoint.

Source: mm:07161650-68db-4325-8583-c6711e2cb8bc-2026-09-13T17:25:00Z.
The replacement worker consumed its single-attempt Activity after bootstrap
reported success with no server identity; the independent reconciler later
restored discovery. This fixture preserves that ordering and replays history.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio import activity, workflow
from temporalio.client import WorkflowFailureError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.omnigent import deployment_identity
from moonmind.omnigent.bootstrap import infrastructure
from moonmind.omnigent.bootstrap.models import ResolvedOmnigentDeploymentState
from moonmind.omnigent.control_plane import OmnigentControlPlaneStore
from moonmind.omnigent.control_plane.identities import canonical_omnigent_session_id
from moonmind.omnigent.control_plane.turn_commands import CanonicalTurnCommandService
from moonmind.omnigent.harness_platform.execution_plan import (
    OmnigentExecutionPlanEnvelope,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal.activities.omnigent_activities import (
    omnigent_profile_bound_execute_activity,
)
from tests.unit.omnigent.test_canonical_turn_routing import (  # noqa: F401
    _engine,
    session_factory,
)
from tests.unit.omnigent.test_generic_platform_production_services import (
    _generic_publication_harness,
)
from tests.unit.omnigent.test_harness_platform import (  # noqa: F401 -- autouse fixture for immutable host authority
    _compile_opencode_plan,
    _test_owned_host_classes,
)
from tests.unit.workflows.temporal.workflows.test_agent_run_omnigent_capacity_admission import (
    _admission,
    _omnigent_request,
    _RecordingRun,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@workflow.defn(name="MoonMind.AgentRun")
class ReadinessReplayRun(_RecordingRun):
    @workflow.run
    async def run(self, request: AgentExecutionRequest) -> AgentRunResult:
        return await super().run(request)

    async def _execute_routed_activity(self, name, payload=None, **kwargs):
        if name == "integration.resolve_adapter_metadata":
            return {"agent_id": "omnigent", "execution_style": "streaming_gateway"}
        if name == "omnigent.evaluate_session_admission":
            # Retained one-Activity execution, as in the incident's history.
            return (
                _admission()
                .model_copy(update={"admitted": False})
                .model_dump(by_alias=True, mode="json")
            )
        if name.startswith("integration.omnigent."):
            return await workflow.execute_activity(
                name,
                payload,
                **kwargs,
            )
        return await super()._execute_routed_activity(name, payload, **kwargs)

    async def _release_omnigent_provider_capacity(self, **kwargs):
        # Provider service fixture records release; production ownership stays
        # in _execute_omnigent_with_admitted_capacity throughout the wait.
        assert self._omnigent_capacity_state == "granted"
        self._omnigent_capacity_state = "released"

    async def _publish_terminal_result_with_compacted_replay_cleanup(
        self, *, result, **kwargs
    ):
        return result.model_copy(
            update={
                "metadata": {
                    **result.metadata,
                    "parentStates": self.parent_states,
                    "capacityRequests": len(
                        [s for s in self.signals if s[0] == "request_slot"]
                    ),
                }
            }
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("recover", [True, False])
@pytest.mark.parametrize("cached_registry", [True, False])
async def test_replacement_worker_waits_for_discovery_without_readmission(
    tmp_path,
    monkeypatch,
    recover,
    cached_registry,
    session_factory,  # noqa: F811 -- imported fixture
):
    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_RESOLVED_IMAGES_PATH", str(tmp_path / "resolved.json")
    )
    monkeypatch.delenv("OMNIGENT_BUILD_DIGEST", raising=False)
    monkeypatch.delenv("OMNIGENT_IMAGE_REF", raising=False)
    plan = _compile_opencode_plan()
    plan = OmnigentExecutionPlanEnvelope.model_validate_json(
        plan.model_dump_json(by_alias=True)
    )
    attempts = []
    starts = []
    budget = []

    canonical_store = OmnigentControlPlaneStore(session_factory)
    harness = await _generic_publication_harness({})
    harness.realizer._deployment_validator = (
        deployment_identity.assert_plan_matches_deployed_runtime
    )
    harness.realizer._turn_commands = CanonicalTurnCommandService(canonical_store)

    async def provider_execute(request, *, session_authority_sink):
        starts.append(request.idempotency_key)
        await session_authority_sink.session_created("readiness-provider-session")
        return AgentRunResult(
            summary="Provider fixture completed one admitted turn",
            metadata={"omnigentSessionId": "readiness-provider-session"},
        )

    async def drain(session_id):
        return {"sessionId": session_id, "stopped": True}

    harness.realizer._session_driver = provider_execute
    harness.realizer._session_cleanup.drain = drain

    def registry():
        if not cached_registry:
            # A fresh registry resolves deployment services eagerly. The cached
            # case must reach the real realizer and canonical command owner.
            deployment_identity.resolve_deployed_server_build_digest()
        return SimpleNamespace(require=lambda _: harness.realizer)

    monkeypatch.setattr(
        "moonmind.omnigent.realizers.registry.get_default_registry", registry
    )
    monkeypatch.setattr(
        "moonmind.omnigent.harness_platform.stores.DbExecutionPlanStore",
        lambda *_: SimpleNamespace(load=AsyncMock(return_value=plan)),
    )

    async def resolve():
        if recover and len(attempts) >= 3:
            digest = plan.payload.supportIdentity.omnigentServerBuildRef
            return ResolvedOmnigentDeploymentState(
                serverImageRef="server@" + digest, omnigentBuildDigest=digest
            )
        return ResolvedOmnigentDeploymentState()

    monkeypatch.setattr(infrastructure, "resolve_omnigent_images", resolve)
    await infrastructure.bootstrap_infrastructure()

    @activity.defn(name="integration.omnigent.profile_bound_execute")
    async def execute(request: AgentExecutionRequest) -> AgentRunResult:
        attempts.append(request.model_dump(mode="json", by_alias=True))
        budget.append(activity.info().start_to_close_timeout.total_seconds())
        if len(attempts) == 3 and recover:
            # The independent infrastructure owner re-observes the server.
            await infrastructure.bootstrap_infrastructure()
        return await omnigent_profile_bound_execute_activity(request)

    request = _omnigent_request(plan_binding=plan.planRef, plan_ref_parameter=None)
    request = request.model_copy(update={"timeout_policy": {"timeout_seconds": 120}})
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env, Worker(
        env.client,
        task_queue="readiness-replay",
        workflows=[ReadinessReplayRun],
        activities=[execute],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        handle = await env.client.start_workflow(
            ReadinessReplayRun.run,
            request,
            id="mm:07161650-readiness-replay",
            task_queue="readiness-replay",
            execution_timeout=timedelta(minutes=5),
        )
        if recover:
            result = await handle.result()
        else:
            with pytest.raises(WorkflowFailureError) as failure:
                await handle.result()
            assert failure.value.cause.type == "OmnigentDeploymentReadinessTimeout"
        history = await handle.fetch_history()

    session_id = canonical_omnigent_session_id(
        workflow_id=request.correlation_id,
        step_execution_id=request.correlation_id,
        agent_run_id=request.correlation_id,
    )
    async with canonical_store.transaction() as repositories:
        commands = await repositories.commands.list_for_session(session_id)
    assert len(commands) == (1 if recover else 0)
    if recover:
        assert commands[0].status == "applied"
        assert result.failure_class is None
        assert len(starts) == 1
        assert len(attempts) == 3
        assert result.metadata["capacityRequests"] == 1
        assert (
            sum(s[0] == "awaiting_callback" for s in result.metadata["parentStates"])
            == 2
        )
        assert budget[0] > budget[1] > budget[2]
    else:
        assert not starts
        assert len(attempts) <= 4
    assert all(
        row["admittedProviderCapacity"] == attempts[0]["admittedProviderCapacity"]
        for row in attempts
    )
    assert all(row["idempotencyKey"] == request.idempotency_key for row in attempts)
    assert any(
        event.HasField("timer_started_event_attributes") for event in history.events
    )

    await Replayer(
        workflows=[ReadinessReplayRun],
        workflow_runner=UnsandboxedWorkflowRunner(),
        data_converter=pydantic_data_converter,
    ).replay_workflow(history)
