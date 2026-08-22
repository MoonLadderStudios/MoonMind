"""Hermetic product-boundary authority journey for issue #3706."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from temporalio import activity
from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import AddSearchAttributesRequest
from temporalio.converter import DataConverter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from api_service.api.routers import executions as executions_router
from api_service.db import base as db_base
from api_service.db.base import get_async_session
from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    OmnigentCommand,
    OmnigentCredentialRuntimeRecord,
    OmnigentExecutionPlanRecord,
    TemporalArtifact,
)
from api_service.services import omnigent_execution_plan_service
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.omnigent.conformance import assert_secret_free
from moonmind.omnigent.control_plane import OmnigentControlPlaneStore, compute_digest
from moonmind.omnigent.harness_platform.attestation import (
    HostHarnessAttestation,
    compute_attestation_ref,
)
from moonmind.omnigent.harness_platform.catalog import HarnessImplementationIdentity
from moonmind.omnigent.harness_platform.host_classes import get_host_class
from moonmind.omnigent.harness_platform.stores import (
    DbExecutionPlanStore,
    DbRuntimeBindingStore,
)
from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from moonmind.omnigent.oauth_hosts import OmnigentOAuthHostRepository
from moonmind.omnigent.profile_bound_execution import (
    OmnigentProfileBoundExecutionCoordinator,
)
from moonmind.omnigent.realizers import deployment_adapters
from moonmind.omnigent.realizers.codex_profile_bound import (
    CodexProfileBoundRealizer,
)
from moonmind.omnigent.realizers.registry import (
    OmnigentExecutionRealizerRegistry,
)
from moonmind.provider_profiles import lease_client as lease_client_module
from moonmind.provider_profiles.lease_client import (
    CredentialLease,
    CredentialLeasePurpose,
)
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
    OmnigentExecutionPlanBinding,
)
from moonmind.schemas.omnigent_session_models import (
    OmnigentSessionAdmissionRequest,
    OmnigentSessionSignal,
)
from moonmind.workflows.temporal.activities import omnigent_session_activities
from moonmind.workflows.temporal.activity_catalog import (
    AGENT_RUNTIME_TASK_QUEUE,
    INTEGRATIONS_TASK_QUEUE,
    TemporalActivityCatalog,
    build_default_activity_catalog,
    get_workflow_task_queue,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
    TemporalIntegrationActivities,
    build_activity_bindings,
)
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from moonmind.workflows.temporal.workflows.omnigent_session import (
    MoonMindOmnigentSessionWorkflow,
    canonical_omnigent_session_id,
    omnigent_session_workflow_id,
)
from tests.unit.api.routers.test_executions import (
    _build_execution_record,
    _override_user_dependencies,
)
from tests.unit.services.test_omnigent_execution_plan_service import (
    _policy_snapshot,
    _protected_support_evidence,
    _snapshot,
)


pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@activity.defn(name="integration.resolve_adapter_metadata")
async def _resolve_omnigent_adapter_metadata(_agent_id: str) -> dict[str, Any]:
    """Use the production streaming route without contacting a provider."""

    return {
        "agent_id": "omnigent",
        "execution_style": "streaming_gateway",
        "supports_callbacks": False,
        "callback_base_url": None,
    }


def _production_agent_runtime_activities() -> list[Any]:
    """Bind the same Agent Runtime handlers registered by a production worker."""

    runtime = TemporalAgentRuntimeActivities()
    complete_catalog = build_default_activity_catalog()
    required = {
        "integration.omnigent.profile_bound_execute",
        "agent_runtime.publish_artifacts",
    }
    selected_catalog = TemporalActivityCatalog(
        activities=tuple(
            definition
            for definition in complete_catalog.activities
            if definition.activity_type.startswith("omnigent.")
            or definition.activity_type in required
        ),
        fleets=complete_catalog.fleets,
    )
    bindings = build_activity_bindings(
        selected_catalog,
        agent_runtime_activities=runtime,
        fleets=("agent_runtime",),
    )
    handlers = [binding.handler for binding in bindings]
    registered = {
        activity._Definition.must_from_callable(handler).name
        for handler in handlers
    }
    assert required <= registered
    assert "omnigent.evaluate_session_admission" in registered
    return handlers


def _production_integration_activities() -> list[Any]:
    """Bind the production integration owner for recorded Codex plans."""

    complete_catalog = build_default_activity_catalog()
    selected_catalog = TemporalActivityCatalog(
        activities=tuple(
            definition
            for definition in complete_catalog.activities
            if definition.activity_type == "integration.omnigent.execute"
        ),
        fleets=complete_catalog.fleets,
    )
    bindings = build_activity_bindings(
        selected_catalog,
        integration_activities=TemporalIntegrationActivities(),
        fleets=("integrations",),
    )
    handlers = [binding.handler for binding in bindings]
    assert {
        activity._Definition.must_from_callable(handler).name
        for handler in handlers
    } == {"integration.omnigent.execute"}
    return handlers


async def _decoded_activity_inputs(history: Any, activity_name: str) -> list[Any]:
    decoded: list[Any] = []
    for event in history.events:
        if not event.HasField("activity_task_scheduled_event_attributes"):
            continue
        scheduled = event.activity_task_scheduled_event_attributes
        if scheduled.activity_type.name != activity_name:
            continue
        decoded.extend(
            await DataConverter.default.decode(scheduled.input.payloads)
        )
    return decoded


async def _register_session_search_attributes(
    environment: WorkflowEnvironment,
) -> None:
    """Install the visibility schema required by the production supervisor."""

    await environment.client.operator_service.add_search_attributes(
        AddSearchAttributesRequest(
            namespace=environment.client.namespace,
            search_attributes={
                "AgentRunId": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "SessionId": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "SessionStatus": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "IsDegraded": IndexedValueType.INDEXED_VALUE_TYPE_BOOL,
            },
        )
    )


async def _run_product_agent_workflow(
    request: AgentExecutionRequest,
    *,
    agent_run_id: str,
    expect_session_child: bool,
    cancel_active_turn: bool = False,
) -> tuple[AgentRunResult, dict[str, Any]]:
    """Run and replay the real AgentRun/session workflow over real Activities."""

    workflow_queue = get_workflow_task_queue()
    evidence: dict[str, Any] = {}
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        await _register_session_search_attributes(environment)
        async with (
            Worker(
                environment.client,
                task_queue=workflow_queue,
                workflows=[MoonMindAgentRun, MoonMindOmnigentSessionWorkflow],
                activities=[_resolve_omnigent_adapter_metadata],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            Worker(
                environment.client,
                task_queue=AGENT_RUNTIME_TASK_QUEUE,
                activities=_production_agent_runtime_activities(),
            ),
            Worker(
                environment.client,
                task_queue=INTEGRATIONS_TASK_QUEUE,
                activities=_production_integration_activities(),
            ),
        ):
            handle = await environment.client.start_workflow(
                MoonMindAgentRun.run,
                request,
                id=agent_run_id,
                task_queue=workflow_queue,
            )
            cancel_task = None
            if cancel_active_turn:
                assert expect_session_child

                async def cancel_when_turn_is_active() -> None:
                    session_id = canonical_omnigent_session_id(
                        workflow_id=request.correlation_id,
                        step_execution_id=request.idempotency_key,
                        agent_run_id=agent_run_id,
                    )
                    child_handle = environment.client.get_workflow_handle(
                        omnigent_session_workflow_id(session_id)
                    )
                    for _attempt in range(200):
                        try:
                            state = await child_handle.query(
                                "omnigent_session.state"
                            )
                        except Exception:
                            await asyncio.sleep(0.025)
                            continue
                        if (
                            state.get("phase") == "turn_in_flight"
                            and int(state.get("turnAttemptCount") or 0) >= 1
                        ):
                            await child_handle.signal(
                                "cancel_or_interrupt_requested",
                                OmnigentSessionSignal(
                                    requestId=(
                                        f"{session_id}:product-cancellation"
                                    ),
                                    reasonCode="product_cancellation_test",
                                ),
                            )
                            return
                        await asyncio.sleep(0.025)
                    raise AssertionError(
                        "product cancellation never observed an active turn"
                    )

                cancel_task = asyncio.create_task(cancel_when_turn_is_active())
            try:
                raw_result = await asyncio.wait_for(handle.result(), timeout=30)
            except TimeoutError as exc:
                timed_out_history = await handle.fetch_history()
                child_diagnostics: dict[str, Any] = {}
                child_handle = None
                if expect_session_child:
                    session_id = canonical_omnigent_session_id(
                        workflow_id=request.correlation_id,
                        step_execution_id=request.idempotency_key,
                        agent_run_id=agent_run_id,
                    )
                    child_handle = environment.client.get_workflow_handle(
                        omnigent_session_workflow_id(session_id)
                    )
                    child_history = await child_handle.fetch_history()
                    scheduled_names = [
                        event.activity_task_scheduled_event_attributes.activity_type.name
                        for event in child_history.events
                        if event.HasField(
                            "activity_task_scheduled_event_attributes"
                        )
                    ]
                    diagnostic_store = OmnigentControlPlaneStore(
                        db_base.async_session_maker
                    )
                    async with diagnostic_store.transaction() as repositories:
                        observations = (
                            await repositories.observations.list_for_session(
                                session_id,
                                limit=6,
                                latest=True,
                            )
                        )
                    child_diagnostics = {
                        "state": await child_handle.query(
                            "omnigent_session.state"
                        ),
                        "scheduledCount": len(scheduled_names),
                        "scheduledTail": scheduled_names[-40:],
                        "observationTail": [
                            {
                                "type": observation.observation_type,
                                "index": observation.bounded_index,
                            }
                            for observation in observations
                        ],
                    }
                tail = [
                    {
                        "eventId": event.event_id,
                        "eventType": str(event.event_type),
                        "activityFailure": (
                            event.activity_task_failed_event_attributes.failure.message
                            if event.HasField(
                                "activity_task_failed_event_attributes"
                            )
                            else None
                        ),
                        "scheduledActivity": (
                            event.activity_task_scheduled_event_attributes.activity_type.name
                            if event.HasField(
                                "activity_task_scheduled_event_attributes"
                            )
                            else None
                        ),
                        "scheduledQueue": (
                            event.activity_task_scheduled_event_attributes.task_queue.name
                            if event.HasField(
                                "activity_task_scheduled_event_attributes"
                            )
                            else None
                        ),
                    }
                    for event in timed_out_history.events[-40:]
                ]
                await handle.terminate("product journey diagnostic timeout")
                if child_handle is not None:
                    await child_handle.terminate(
                        "product journey diagnostic timeout"
                    )
                pytest.fail(
                    "product AgentRun did not terminate; history tail="
                    + json.dumps(tail, sort_keys=True)
                    + "; child="
                    + json.dumps(child_diagnostics, sort_keys=True),
                    pytrace=False,
                )
            if cancel_task is not None:
                await cancel_task
            result = AgentRunResult.model_validate(raw_result)
            agent_history = await handle.fetch_history()
            evidence["agentRunHistory"] = agent_history.to_json_dict()
            evidence["resolveIntentInputs"] = await _decoded_activity_inputs(
                agent_history, "omnigent.resolve_intent"
            )
            session_history = None
            if expect_session_child:
                session_id = canonical_omnigent_session_id(
                    workflow_id=request.correlation_id,
                    step_execution_id=request.idempotency_key,
                    agent_run_id=agent_run_id,
                )
                session_handle = environment.client.get_workflow_handle(
                    omnigent_session_workflow_id(session_id)
                )
                session_history = await session_handle.fetch_history()
                evidence["sessionHistory"] = session_history.to_json_dict()

    await Replayer(
        workflows=[MoonMindAgentRun],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(agent_history)
    if session_history is not None:
        await Replayer(
            workflows=[MoonMindOmnigentSessionWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ).replay_workflow(session_history)
    return result, evidence


@pytest.mark.parametrize(
    (
        "harness",
        "launch_policy",
        "execution_target",
        "runtime_id",
        "provider_id",
        "expected_realizer",
    ),
    [
        (
            "opencode-native",
            "opencode-on-demand@1",
            "omnigent-opencode@1",
            "opencode",
            "opencode-go",
            "generic-omnigent-host@1",
        ),
        (
            "codex-native",
            "codex-on-demand@1",
            "omnigent-codex@1",
            "codex_cli",
            "openai",
            "codex-profile-bound@1",
        ),
    ],
)
async def test_post_execution_persists_and_admits_exact_runtime_authority(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    harness: str,
    launch_policy: str,
    execution_target: str,
    runtime_id: str,
    provider_id: str,
    expected_realizer: str,
) -> None:
    """POST uses the real compiler, artifact store, DB store, and admission gate."""

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/omnigent-product-authority.db"
    )
    session_maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    provider_profile_id = f"provider-{harness}"
    async with session_maker() as session:
        profile_values = {
            "profile_id": provider_profile_id,
            "runtime_id": runtime_id,
            "provider_id": provider_id,
            "credential_generation": 5,
            "enabled": True,
            "auth_state": "connected",
        }
        if harness == "codex-native":
            profile_values.update(
                {
                    "credential_source": "oauth_volume",
                    "runtime_materialization_mode": "oauth_home",
                    "volume_ref": "codex-product-auth",
                    "volume_mount_path": "/home/app/.codex",
                }
            )
        else:
            profile_values["secret_refs"] = {
                "api_key": {"kind": "managed", "ref": "test-key"}
            }
        session.add(ManagedAgentProviderProfile(**profile_values))
        await session.commit()

    monkeypatch.setattr(db_base, "async_session_maker", session_maker)
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setattr(
        TemporalArtifactService,
        "_build_store_from_settings",
        staticmethod(lambda: LocalTemporalArtifactStore(artifact_root)),
    )
    host_image_ref = None
    if harness == "opencode-native":
        host_image_ref = (
            "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:"
            + "7" * 64
        )
        monkeypatch.setenv("OMNIGENT_OPENCODE_HOST_IMAGE_REF", host_image_ref)

    snapshot = _snapshot(
        harness=harness,
        policy=launch_policy,
        provider_id=provider_profile_id,
    )

    async def resolve_policy(**_kwargs):
        return _policy_snapshot(
            harness=harness,
            policy=launch_policy,
            host_image_ref=host_image_ref,
        )

    monkeypatch.setattr(
        omnigent_execution_plan_service,
        "_resolve_runtime_policy_snapshot",
        resolve_policy,
    )
    monkeypatch.setattr(
        omnigent_execution_plan_service,
        "load_protected_execution_support_evidence",
        _protected_support_evidence,
    )
    monkeypatch.setattr(
        executions_router,
        "resolve_agent_profile_snapshot",
        AsyncMock(return_value=snapshot),
    )

    execution_service = AsyncMock()
    execution_service.create_execution.return_value = _build_execution_record()
    app = FastAPI()
    app.include_router(executions_router.router)
    app.dependency_overrides[executions_router._get_service] = (
        lambda: execution_service
    )
    app.dependency_overrides[executions_router.get_temporal_client] = AsyncMock

    async def session_override():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_async_session] = session_override
    _override_user_dependencies(app, is_superuser=False)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/executions",
            json={
                "type": "workflow",
                "payload": {
                    "repository": "MoonLadderStudios/MoonMind",
                    "targetRuntime": "omnigent",
                    "agentProfile": {
                        "profileId": snapshot["profileId"],
                        "providerProfileRef": provider_profile_id,
                    },
                    "omnigent": {
                            "executionTargetRef": execution_target,
                            "launchPolicyRef": launch_policy,
                    },
                    "workflow": {
                            "instructions": f"Exercise persisted {harness} authority.",
                        "runtime": {"mode": "omnigent"},
                    },
                },
            },
        )

    assert response.status_code == 201, response.text
    authored = execution_service.create_execution.await_args.kwargs[
        "initial_parameters"
    ]
    binding = OmnigentExecutionPlanBinding.model_validate(
        authored["omnigentExecutionPlan"]
    )
    assert authored["resolvedSkillsetRef"]

    async with session_maker() as session:
        stored_plan = await session.get(
            OmnigentExecutionPlanRecord, binding.plan_ref
        )
        assert stored_plan is not None
        artifacts = list((await session.scalars(select(TemporalArtifact))).all())
    assert binding.plan_digest == "sha256:" + binding.plan_ref.rsplit(":", 1)[-1]
    assert stored_plan.execution_realizer_ref == expected_realizer
    assert any(
        (artifact.metadata_json or {}).get("artifact_class")
        == "omnigent.execution_support_evidence"
        for artifact in artifacts
    )
    assert_secret_free(stored_plan.payload_json)

    decision = await (
        omnigent_session_activities.omnigent_evaluate_session_admission_activity(
            OmnigentSessionAdmissionRequest(
                workflowId=f"workflow-{harness}-product",
                stepExecutionId=f"step-{harness}-product",
                agentRunId=f"agent-run-{harness}-product",
                executionProfileRef=provider_profile_id,
                omnigentExecutionPlan=binding,
            ).model_dump(mode="json", by_alias=True)
        )
    )
    assert decision["admitted"] is True
    assert decision["executionRealizerRef"] == expected_realizer

    if harness == "codex-native":
        # Codex admission deliberately preserves its recorded coordinator; the
        # normal AgentRun dispatcher must invoke that exact realizer and its
        # production coordinator/store boundaries.
        assert (
            await DbRuntimeBindingStore(session_maker).get_current_state(
                binding.plan_ref, f"workflow-{harness}-product"
            )
            is None
        )
        plan = await DbExecutionPlanStore(session_maker).load(
            binding.plan_ref
        )
        assert plan is not None
        workflow_id = f"workflow-{harness}-product"
        step_execution_id = f"step-{harness}-product"
        monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(tmp_path))
        workspace_id = hashlib.sha256(
            f"{workflow_id}:{step_execution_id}".encode("utf-8")
        ).hexdigest()[:24]
        workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
        workspace.mkdir(parents=True)
        SandboxWorkspaceRecordStore(tmp_path).ensure(
            SandboxWorkspaceRecord(
                workspace_id=workspace_id,
                workflow_id=workflow_id,
                step_execution_id=step_execution_id,
                relative_path="repo",
            )
        )

        lifecycle: list[str] = []

        class CodexLeaseClient:
            async def acquire_execution_lease(self, **kwargs):
                lifecycle.append("provider_lease_acquired")
                return CredentialLease(
                    profile_id=kwargs["profile_id"],
                    runtime_id=kwargs["runtime_id"],
                    lease_id="provider-lease-codex-product",
                    owner_id=kwargs["owner_id"],
                    purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
                )

            async def release_lease(self, _lease):
                lifecycle.append("provider_lease_released")

            async def record_cooldown(self, **_kwargs):
                return None

        class CodexHostRuntime:
            async def prepare_host(self, **_kwargs):
                lifecycle.append("host_attested")
                return {
                    "hostId": "host-codex-product",
                    "workspacePath": str(workspace),
                    "egressAttestation": {
                        "attachmentIdentity": "host-codex-product"
                    },
                    "egressEvidenceRef": "artifact://codex-egress",
                    "workspaceResolution": {
                        "workspaceLocator": {
                            "kind": "sandbox",
                            "workspaceId": workspace_id,
                            "relativePath": "repo",
                        }
                    },
                }

            async def stop_host(self, **_kwargs):
                lifecycle.append("host_cleaned")
                return {"evidenceRef": "artifact://codex-cleanup"}

            async def publish_workspace(self, **kwargs):
                lifecycle.append("workspace_published")
                return {
                    "push_status": "pushed",
                    "push_branch": "codex-product",
                    "push_base_branch": kwargs.get("base_branch") or "main",
                    "push_head_sha": "a" * 40,
                    "push_commit_count": 1,
                    "remote_verified": True,
                    "pushRef": "git://codex-product@" + "a" * 40,
                }

            async def inspect_session_completion(self, _session_id):
                return {
                    "sessionStatus": "completed",
                    "itemCount": 2,
                    "assistantMessageCount": 1,
                    "toolResultCount": 0,
                    "terminalAssistantAfterWork": True,
                }

        async def execute_codex_session(request, **_kwargs):
            lifecycle.append("session_turn_terminal")
            return AgentRunResult(
                outputRefs=["artifact://codex-terminal"],
                diagnosticsRef="artifact://codex-diagnostics",
                summary="Codex product execution completed",
                metadata={
                    "omnigentSessionId": "provider-session-codex-product",
                    "captureManifestRef": "artifact://codex-capture",
                    "externalStateRef": "artifact://codex-state",
                },
            )

        async def recorded_policy(_self, policy_ref):
            assert policy_ref == launch_policy
            return _policy_snapshot(
                harness=harness,
                policy=launch_policy,
                host_image_ref=plan.payload.hostImageRef,
            )

        monkeypatch.setattr(
            OmnigentProfileBoundExecutionCoordinator,
            "_resolve_policy_snapshot",
            recorded_policy,
        )

        def coordinator_factory(
            *, session_factory, run_store, artifact_gateway, execution_plan
        ):
            return OmnigentProfileBoundExecutionCoordinator(
                session_factory=session_factory,
                lease_client=CodexLeaseClient(),
                host_repository=OmnigentOAuthHostRepository(session_factory),
                host_runtime=CodexHostRuntime(),
                run_store=run_store,
                execution_runner=execute_codex_session,
                artifact_gateway=artifact_gateway,
                execution_plan=execution_plan,
            )

        registry = OmnigentExecutionRealizerRegistry()
        registry.register(
            CodexProfileBoundRealizer(
                session_factory=session_maker,
                coordinator_factory=coordinator_factory,
            )
        )
        monkeypatch.setattr(
            "moonmind.omnigent.realizers.registry.get_default_registry",
            lambda: registry,
        )
        request = AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            executionProfileRef=provider_profile_id,
            correlationId=workflow_id,
            idempotencyKey=step_execution_id,
            omnigentExecutionPlan=binding,
            resolvedSkillsetRef=authored["resolvedSkillsetRef"],
            workspaceSpec={
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": workspace_id,
                    "relativePath": "repo",
                },
                "repository": "MoonLadderStudios/MoonMind",
                "startingBranch": "main",
                "targetBranch": "codex-product",
            },
            parameters={
                "repository": "MoonLadderStudios/MoonMind",
                "targetRuntime": "omnigent",
                "workflow": authored["workflow"],
                "omnigent": {
                    "executionTargetRef": execution_target,
                    "launchPolicyRef": launch_policy,
                },
                "executionPlanRef": binding.plan_ref,
                "publishMode": "branch",
            },
        )
        result, workflow_evidence = await _run_product_agent_workflow(
            request,
            agent_run_id=f"agent-run-{harness}-product",
            expect_session_child=False,
        )
        assert result.failure_class is None, result
        assert result.summary == "Codex product execution completed"
        codex_binding_state = await DbRuntimeBindingStore(
            session_maker
        ).get_current_state(binding.plan_ref, workflow_id)
        assert codex_binding_state is not None
        assert codex_binding_state.state == "cleanup_complete"
        assert codex_binding_state.binding.executionPlanRef == binding.plan_ref
        assert codex_binding_state.binding.omnigentHostId == (
            "host-codex-product"
        )
        assert codex_binding_state.binding.omnigentSessionId == (
            "provider-session-codex-product"
        )
        assert codex_binding_state.binding.chatBindingRef
        assert codex_binding_state.binding.hostHarnessAttestationRef
        assert codex_binding_state.binding.exactHostCapabilityDecisionRef
        assert result.metadata["executionPlanRef"] == binding.plan_ref
        assert result.metadata["runtimeBindingRef"] == (
            codex_binding_state.binding.runtimeBindingRef
        )
        assert result.metadata["runtimeBindingState"] == "cleanup_complete"
        assert lifecycle.index("provider_lease_acquired") < lifecycle.index(
            "host_attested"
        )
        assert lifecycle.index("host_cleaned") < lifecycle.index(
            "provider_lease_released"
        )
        assert "workspace_published" in lifecycle
        async with session_maker() as session:
            codex_artifacts = list(
                (
                    await session.scalars(
                        select(TemporalArtifact).where(
                            TemporalArtifact.content_type
                            == "application/json"
                        )
                    )
                ).all()
            )
        codex_artifact_payloads = [
            await omnigent_session_activities._read_json_artifact(
                artifact.artifact_id
            )
            for artifact in codex_artifacts
        ]
        codex_authority_scan = {
            "agentRunInput": request.model_dump(
                mode="json", by_alias=True
            ),
            "result": result.model_dump(mode="json", by_alias=True),
            "lifecycle": lifecycle,
            "persistedPlan": stored_plan.payload_json,
            "runtimeBinding": codex_binding_state.binding.model_dump(
                mode="json", by_alias=True
            ),
            "artifacts": codex_artifact_payloads,
            "workflowEvidence": workflow_evidence,
            "containerMetadata": {
                "executionPlanRef": binding.plan_ref,
                "runtimeBindingRef": (
                    codex_binding_state.binding.runtimeBindingRef
                ),
                "hostLeaseRef": (
                    codex_binding_state.binding.hostLeaseRef
                ),
            },
        }
        assert_secret_free(
            codex_authority_scan
        )
        serialized_codex_authority = json.dumps(
            codex_authority_scan, sort_keys=True
        )
        assert str(tmp_path) not in serialized_codex_authority
        assert "/workspaces/" not in serialized_codex_authority
        assert "/var/run/docker.sock" not in serialized_codex_authority
        assert '"providerPayload"' not in serialized_codex_authority
        assert workflow_evidence["resolveIntentInputs"] == []
        replayed = await DbExecutionPlanStore(session_maker).load(
            binding.plan_ref
        )
        assert replayed == plan
        await engine.dispose()
        return

    workflow_id = f"workflow-{harness}-product"
    resolve_handoff = {
        "workflowId": workflow_id,
        "stepExecutionId": f"step-{harness}-product",
        "agentRunId": f"agent-run-{harness}-product",
        "omnigentExecutionPlan": binding.model_dump(
            mode="json", by_alias=True
        ),
    }
    assert "request" not in resolve_handoff
    resolved = await omnigent_session_activities.omnigent_resolve_intent_activity(
        resolve_handoff
    )

    class FakeLeaseClient:
        acquisition_count = 0
        released: list[str] = []

        def __init__(self, _adapter) -> None:
            pass

        async def acquire_execution_lease(self, **kwargs):
            self.__class__.acquisition_count += 1
            return SimpleNamespace(
                lease_id=f"provider-lease-{self.acquisition_count}",
                owner_id=kwargs["owner_id"],
            )

        async def release_lease(self, lease) -> None:
            self.__class__.released.append(lease.lease_id)

    monkeypatch.setattr(
        lease_client_module, "ProviderProfileLeaseClient", FakeLeaseClient
    )

    control_store = OmnigentControlPlaneStore(session_maker)

    async def acquire_with_current_authority(command_id: str) -> dict:
        async with control_store.transaction() as repositories:
            session = await repositories.sessions.get(resolved["sessionId"])
            assert session is not None
            await repositories.commands.record(
                command_id=command_id,
                session_id=session.session_id,
                command_type="acquire_provider_profile",
                idempotency_key=command_id,
                payload_digest=compute_digest(
                    {"planRef": binding.plan_ref, "commandId": command_id}
                ),
                expected_session_revision=session.revision,
                fencing_generation=session.fencing_generation,
                owner_class="omnigent_session_workflow",
                retry_policy={"maxAttempts": 3},
            )
        current_binding = await DbRuntimeBindingStore(
            session_maker
        ).get_current_state(binding.plan_ref, workflow_id)
        return await (
            omnigent_session_activities.omnigent_ensure_provider_profile_lease_activity(
                {
                    "sessionId": resolved["sessionId"],
                    "compiledExecutionIntentRef": resolved[
                        "compiledExecutionIntentRef"
                    ],
                    "compiledExecutionIntentDigest": resolved[
                        "compiledExecutionIntentDigest"
                    ],
                    "omnigentExecutionPlan": binding.model_dump(
                        mode="json", by_alias=True
                    ),
                    "expectedRevision": session.revision,
                    "fencingGeneration": session.fencing_generation,
                    "commandId": command_id,
                    **(
                        {
                            "runtimeBindingRef": (
                                current_binding.binding.runtimeBindingRef
                            ),
                            "runtimeBindingRevision": current_binding.revision,
                            "runtimeBindingFencingGeneration": (
                                current_binding.fencing_generation
                            ),
                        }
                        if current_binding is not None
                        else {}
                    ),
                }
            )
        )

    first_acquisition = await acquire_with_current_authority("command-lease-1")
    binding_store = DbRuntimeBindingStore(session_maker)
    first_state = await binding_store.get_current_state(
        binding.plan_ref, workflow_id
    )
    assert first_state is not None
    assert first_state.binding.executionScopeRef == workflow_id
    assert len(first_state.binding.providerLeases) == 1
    first_provider_lease = next(
        iter(first_state.binding.providerLeases.values())
    )
    assert first_provider_lease.credentialGeneration == 5
    assert first_acquisition["runtimeBindingRef"] == (
        first_state.binding.runtimeBindingRef
    )

    async with session_maker() as session:
        profile = await session.get(
            ManagedAgentProviderProfile, provider_profile_id
        )
        assert profile is not None
        profile.credential_generation = 6
        await session.commit()

    second_acquisition = await acquire_with_current_authority(
        "command-lease-rotation"
    )
    rotated_state = await binding_store.get_current_state(
        binding.plan_ref, workflow_id
    )
    assert rotated_state is not None
    assert rotated_state.binding.executionPlanRef == binding.plan_ref
    assert len(rotated_state.binding.providerLeases) == 1
    rotated_provider_lease = next(
        iter(rotated_state.binding.providerLeases.values())
    )
    assert rotated_provider_lease.credentialGeneration == 6
    assert rotated_state.revision == first_state.revision + 1
    assert (
        rotated_state.fencing_generation
        == first_state.fencing_generation + 1
    )
    assert second_acquisition["runtimeBindingRef"] == (
        rotated_state.binding.runtimeBindingRef
    )

    # Continue through the production Activity/store owners. Only the remote
    # host and provider APIs are hermetic adapters; no phase is replaced with a
    # synthetic workflow implementation.
    plan = await DbExecutionPlanStore(session_maker).load(binding.plan_ref)
    assert plan is not None
    host_class = get_host_class(plan.payload.hostClassRef)
    declared = next(
        item
        for item in host_class.declaredHarnessImplementations
        if item.harnessId == plan.payload.harnessId
    )
    implementation = HarnessImplementationIdentity(
        sourceKind="core",
        package="omnigent",
        version="1.0.0",
        digest="sha256:" + "a" * 64,
    )
    assert implementation.implementation_ref() == declared.implementationRef
    exact_attestation = HostHarnessAttestation.model_validate(
        {
            "hostId": "host-opencode-product",
            "hostClassRef": host_class.ref,
            "hostImageRef": host_class.imageRef,
            "omnigentVersion": host_class.omnigentVersion,
            "omnigentBuildDigest": host_class.omnigentBuildDigest,
            "harnessId": plan.payload.harnessId,
            "harnessImplementation": implementation.model_dump(
                mode="json", by_alias=True
            ),
            "runtimeDependencies": list(declared.runtimeDependencies),
            "configured": True,
            "capabilities": {
                capability: True
                for capability in plan.payload.classAdmissionDecision.get(
                    "required", []
                )
            },
            "architecture": plan.payload.supportIdentity.architecture,
            "attestationGeneration": 1,
            "observedAt": datetime.now(UTC),
        }
    )
    exact_attestation = exact_attestation.model_copy(
        update={
            "attestationRef": compute_attestation_ref(exact_attestation)
        }
    )
    launched_containers: dict[str, str] = {}
    latest_container_name: str | None = None

    class FakeHttp:
        async def aclose(self) -> None:
            return None

    class FakeOmnigentClient:
        stopped_sessions: list[str] = []
        submitted_events: list[tuple[str, dict]] = []
        created_sessions = 0
        messages: dict[str, dict[str, Any]] = {}
        hold_new_sessions = False
        held_sessions: set[str] = set()

        async def list_hosts(self) -> list[dict]:
            assert latest_container_name is not None
            return [
                {
                    "id": "host-opencode-product",
                    "name": latest_container_name,
                    "status": "online",
                    "attestation": exact_attestation.model_dump(
                        mode="json", by_alias=True
                    ),
                }
            ]

        async def get_host_model_options(self, host_id: str) -> dict:
            assert host_id == "host-opencode-product"
            return {"models": [{"qualifiedId": "example/model"}]}

        async def list_agents(self) -> list[dict]:
            return [{"id": snapshot["agentId"], "name": "OpenCode"}]

        async def create_session(self, payload: dict) -> dict:
            assert payload["host_id"] == "host-opencode-product"
            self.__class__.created_sessions += 1
            suffix = (
                ""
                if self.__class__.created_sessions == 1
                else f"-{self.__class__.created_sessions}"
            )
            session_id = f"provider-session-opencode-product{suffix}"
            if self.__class__.hold_new_sessions:
                self.__class__.held_sessions.add(session_id)
            return {"id": session_id}

        async def get_session(self, session_id: str) -> dict:
            message = self.__class__.messages.get(session_id)
            if (
                message is not None
                and session_id not in self.__class__.held_sessions
            ):
                return {
                    "id": session_id,
                    "status": "completed",
                    "active_response_id": "",
                    "capabilities": {"interrupt": True, "terminate": True},
                    "items": [
                        {
                            "id": f"{session_id}-user",
                            "type": "message",
                            "data": {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "input_text",
                                        "text": json.dumps(message, sort_keys=True),
                                    }
                                ],
                            },
                        },
                        {
                            "id": f"{session_id}-assistant",
                            "type": "message",
                            "data": {
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": "Completed the product journey.",
                                    }
                                ],
                            },
                        },
                    ],
                }
            return {
                "id": session_id,
                "status": "running",
                "active_response_id": f"response-{session_id}",
                "items": [],
                "capabilities": {"interrupt": True, "terminate": True},
            }

        async def post_event(self, session_id: str, payload: dict) -> dict:
            self.__class__.submitted_events.append((session_id, payload))
            self.__class__.messages[session_id] = payload
            return {"id": f"message-{session_id}"}

        async def stream_events(self, _session_id: str):
            if False:
                yield {}

        async def interrupt(self, _session_id: str) -> None:
            return None

        async def stop_session(self, session_id: str) -> None:
            self.__class__.stopped_sessions.append(session_id)

    fake_client = FakeOmnigentClient()

    async def fake_client_context():
        return FakeHttp(), fake_client

    monkeypatch.setattr(
        omnigent_session_activities,
        "_omnigent_client_context",
        fake_client_context,
    )

    docker_calls: list[tuple[str, ...]] = []

    async def run_docker(argv, *, env=None):
        nonlocal latest_container_name
        del env
        call = tuple(argv)
        docker_calls.append(call)
        if argv[0] == "inspect":
            if ".Config.Image" in argv[2]:
                return 0, json.dumps(host_class.imageRef).encode(), b""
            lease_ref = launched_containers.get(argv[-1])
            if lease_ref:
                return 0, lease_ref.encode(), b""
            return 1, b"", b"No such object"
        if argv[0] == "run":
            container_name = argv[argv.index("--name") + 1]
            labels = [
                argv[index + 1]
                for index, value in enumerate(argv)
                if value == "--label"
            ]
            lease_label = next(
                value
                for value in labels
                if value.startswith("moonmind.host_lease_id=")
            )
            launched_containers[container_name] = lease_label.split("=", 1)[1]
            latest_container_name = container_name
            return 0, b"container-id", b""
        if argv[0] == "rm":
            launched_containers.pop(argv[-1], None)
            if latest_container_name == argv[-1]:
                latest_container_name = None
            return 0, b"", b""
        raise AssertionError(f"unexpected Docker transport call: {argv!r}")

    async def attest_egress(**_kwargs):
        return SimpleNamespace(network_ref="omnigent-egress")

    async def attest_workload_egress(**kwargs):
        return {
            "attachmentIdentity": kwargs["attachment_identity"],
            "endpointIdentity": "restricted-egress",
            "validationResult": "passed",
        }

    monkeypatch.setattr(deployment_adapters, "_run_docker", run_docker)
    monkeypatch.setattr(
        deployment_adapters, "attest_docker_egress", attest_egress
    )
    monkeypatch.setattr(
        deployment_adapters,
        "attest_docker_workload_egress",
        attest_workload_egress,
    )
    monkeypatch.setattr(
        deployment_adapters,
        "OmnigentHttpClient",
        lambda **_kwargs: fake_client,
    )

    async def resolve_secret(_secret_ref, *, field_name):
        assert "api_key SecretRef" in field_name
        return "integration-only-opencode-secret"

    async def publish_workspace(_self, **kwargs) -> dict:
        assert kwargs["publish_mode"] in {"branch", "pr"}
        return {
            "status": "published",
            "publishMode": kwargs["publish_mode"],
            "pushRef": "git://MoonLadderStudios/MoonMind/refs/heads/test@abc123",
            "remoteVerified": True,
        }

    monkeypatch.setattr(
        OmnigentOAuthHostRuntime,
        "publish_workspace",
        publish_workspace,
    )
    monkeypatch.setattr(
        "moonmind.omnigent.realizers.deployment_adapters."
        "resolve_managed_api_key_reference",
        resolve_secret,
    )
    monkeypatch.setenv(
        "WORKFLOW_WORKSPACE_ROOT",
        str(tmp_path),
    )
    monkeypatch.setenv(
        "OMNIGENT_GENERIC_RUNTIME_ROOT",
        str(tmp_path / "generic-runtime"),
    )
    workspace_id = hashlib.sha256(
        f"{workflow_id}:step-{harness}-product".encode("utf-8")
    ).hexdigest()[:24]
    workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    workspace.mkdir(parents=True)
    SandboxWorkspaceRecordStore(tmp_path).ensure(
        SandboxWorkspaceRecord(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            step_execution_id=f"step-{harness}-product",
            relative_path="repo",
        )
    )

    async def command_payload(
        command_id: str,
        command_kind: str,
        *,
        turn_attempt_id: str | None = None,
        terminal_outcome: str | None = None,
    ) -> dict:
        async with control_store.transaction() as repositories:
            current = await repositories.sessions.get(resolved["sessionId"])
            assert current is not None
            await repositories.commands.record(
                command_id=command_id,
                session_id=current.session_id,
                command_type=command_kind,
                idempotency_key=command_id,
                payload_digest=compute_digest(
                    {"kind": command_kind, "commandId": command_id}
                ),
                turn_attempt_id=turn_attempt_id,
                expected_session_revision=current.revision,
                fencing_generation=current.fencing_generation,
                owner_class="omnigent_session_workflow",
                retry_policy={"maxAttempts": 3},
            )
        runtime = await binding_store.get_current_state(
            binding.plan_ref, workflow_id
        )
        assert runtime is not None
        return {
            "sessionId": resolved["sessionId"],
            "compiledExecutionIntentRef": resolved["compiledExecutionIntentRef"],
            "compiledExecutionIntentDigest": resolved[
                "compiledExecutionIntentDigest"
            ],
            "omnigentExecutionPlan": binding.model_dump(
                mode="json", by_alias=True
            ),
            "expectedRevision": current.revision,
            "fencingGeneration": current.fencing_generation,
            "commandId": command_id,
            "runtimeBindingRef": runtime.binding.runtimeBindingRef,
            "runtimeBindingRevision": runtime.revision,
            "runtimeBindingFencingGeneration": runtime.fencing_generation,
            **(
                {"turnAttemptId": turn_attempt_id}
                if turn_attempt_id is not None
                else {}
            ),
            **(
                {"terminalOutcome": terminal_outcome}
                if terminal_outcome is not None
                else {}
            ),
        }

    ensure_host_payload = await command_payload("command-host", "ensure_host")
    await omnigent_session_activities.omnigent_ensure_host_activity(
        ensure_host_payload
    )
    ensure_session_payload = await command_payload(
        "command-session", "ensure_provider_session"
    )
    await omnigent_session_activities.omnigent_ensure_provider_session_activity(
        ensure_session_payload
    )
    submit_payload = await command_payload(
        "command-submit",
        "submit_turn",
        turn_attempt_id=resolved["initialTurnAttemptId"],
    )
    await omnigent_session_activities.omnigent_submit_turn_activity(submit_payload)
    terminal_payload = await command_payload(
        "command-terminal",
        "record_provider_terminal",
        turn_attempt_id=resolved["initialTurnAttemptId"],
        terminal_outcome="success",
    )
    await omnigent_session_activities.omnigent_record_terminal_activity(
        terminal_payload
    )
    harvest_payload = await command_payload(
        "command-harvest", "harvest_evidence"
    )
    await omnigent_session_activities.omnigent_harvest_evidence_activity(
        harvest_payload
    )
    # The one reconciler command owns the ordered harvest/publication phases.
    harvest_runtime = await binding_store.get_current_state(
        binding.plan_ref, workflow_id
    )
    assert harvest_runtime is not None
    harvest_payload.update(
        {
            "runtimeBindingRevision": harvest_runtime.revision,
            "runtimeBindingFencingGeneration": harvest_runtime.fencing_generation,
        }
    )
    publication = await (
        omnigent_session_activities.omnigent_publish_workspace_activity(
            harvest_payload
        )
    )
    assert publication["terminalResultRef"]

    cleanup_payload = await command_payload("command-cleanup", "begin_cleanup")
    await omnigent_session_activities.omnigent_stop_provider_session_activity(
        cleanup_payload
    )
    await omnigent_session_activities.omnigent_stop_host_activity(cleanup_payload)
    release_payload = await command_payload(
        "command-release", "release_leases"
    )
    await omnigent_session_activities.omnigent_release_leases_activity(
        release_payload
    )
    completed_state = await binding_store.get_current_state(
        binding.plan_ref, workflow_id
    )
    assert completed_state is not None
    assert completed_state.state == "cleanup_complete"
    assert FakeOmnigentClient.submitted_events
    assert FakeOmnigentClient.stopped_sessions == [
        "provider-session-opencode-product"
    ]
    assert FakeLeaseClient.released
    assert any(call[:2] == ("rm", "-f") for call in docker_calls)
    credential_root = tmp_path / "generic-runtime" / "credentials"
    assert not credential_root.exists() or not list(credential_root.rglob("auth.json"))
    chat_resolution = await OmnigentBridgeSessionStore(
        session_maker
    ).resolve_chat_binding(workflow_id=workflow_id)
    assert chat_resolution.chat_binding_id == (
        completed_state.binding.chatBindingRef
    )
    assert chat_resolution.read_only is True

    # Prove the same API-produced authority through the actual AgentRun and
    # OmnigentSession workflows. The registered Activities are the production
    # worker bindings above; only provider/host transports are hermetic.
    from moonmind.omnigent import execute as omnigent_execute

    submitted_before_workflow = len(FakeOmnigentClient.submitted_events)
    monkeypatch.setattr(omnigent_execute, "_MARKED_TURN_QUIET_PERIOD_SECONDS", 0)
    monkeypatch.setattr(
        omnigent_execute, "_MARKED_TOOL_ONLY_QUIET_PERIOD_SECONDS", 0
    )
    workflow_product_id = f"workflow-{harness}-temporal-product"
    workflow_step_id = f"step-{harness}-temporal-product"
    workflow_agent_run_id = f"agent-run-{harness}-temporal-product"
    workflow_workspace_id = hashlib.sha256(
        f"{workflow_product_id}:{workflow_step_id}".encode("utf-8")
    ).hexdigest()[:24]
    workflow_workspace = (
        tmp_path / "temporal_sandbox" / workflow_workspace_id / "repo"
    )
    workflow_workspace.mkdir(parents=True)
    SandboxWorkspaceRecordStore(tmp_path).ensure(
        SandboxWorkspaceRecord(
            workspace_id=workflow_workspace_id,
            workflow_id=workflow_product_id,
            step_execution_id=workflow_step_id,
            relative_path="repo",
        )
    )
    workflow_request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef=provider_profile_id,
        correlationId=workflow_product_id,
        idempotencyKey=workflow_step_id,
        instructionRef=binding.task_input_snapshot_ref,
        omnigentExecutionPlan=binding,
        resolvedSkillsetRef=authored["resolvedSkillsetRef"],
        workspaceSpec={
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": workflow_workspace_id,
                "relativePath": "repo",
            },
            "repository": "MoonLadderStudios/MoonMind",
            "startingBranch": "main",
            "targetBranch": "opencode-product-temporal",
        },
        parameters={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "omnigent",
            "workflow": authored["workflow"],
            "omnigent": {
                "executionTargetRef": execution_target,
                "launchPolicyRef": launch_policy,
            },
            "executionPlanRef": binding.plan_ref,
            "publishMode": "branch",
        },
    )
    workflow_result, workflow_evidence = await _run_product_agent_workflow(
        workflow_request,
        agent_run_id=workflow_agent_run_id,
        expect_session_child=True,
    )
    assert workflow_result.failure_class is None, workflow_result
    workflow_runtime = await binding_store.get_current_state(
        binding.plan_ref, workflow_product_id
    )
    assert workflow_runtime is not None
    assert workflow_runtime.state == "cleanup_complete"
    assert len(workflow_evidence["resolveIntentInputs"]) == 1
    compact_history_input = workflow_evidence["resolveIntentInputs"][0]
    assert compact_history_input["omnigentExecutionPlan"] == (
        binding.model_dump(mode="json", by_alias=True)
    )
    assert "request" not in compact_history_input
    assert any(
        f"Exercise persisted {harness} authority." in json.dumps(payload)
        for _, payload in FakeOmnigentClient.submitted_events[
            submitted_before_workflow:
        ]
    )

    # A separate execution scope reuses the immutable plan, acquires its own
    # fenced binding, and sends the real durable cancellation signal while the
    # provider turn is active. Cancellation must still harvest, publish, and
    # clean up through that scope's current binding.
    FakeOmnigentClient.hold_new_sessions = True
    cancellation_workflow_id = f"workflow-{harness}-cancel-product"
    cancellation_step_id = f"step-{harness}-cancel-product"
    cancellation_agent_run_id = f"agent-run-{harness}-cancel-product"
    cancellation_workspace_id = hashlib.sha256(
        f"{cancellation_workflow_id}:{cancellation_step_id}".encode("utf-8")
    ).hexdigest()[:24]
    cancellation_workspace = (
        tmp_path
        / "temporal_sandbox"
        / cancellation_workspace_id
        / "repo"
    )
    cancellation_workspace.mkdir(parents=True)
    SandboxWorkspaceRecordStore(tmp_path).ensure(
        SandboxWorkspaceRecord(
            workspace_id=cancellation_workspace_id,
            workflow_id=cancellation_workflow_id,
            step_execution_id=cancellation_step_id,
            relative_path="repo",
        )
    )
    cancellation_request = workflow_request.model_copy(
        update={
            "correlation_id": cancellation_workflow_id,
            "idempotency_key": cancellation_step_id,
            "workspace_spec": {
                **dict(workflow_request.workspace_spec or {}),
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": cancellation_workspace_id,
                    "relativePath": "repo",
                },
                "targetBranch": "opencode-product-cancel",
            },
        }
    )
    cancellation_result, cancellation_evidence = (
        await _run_product_agent_workflow(
            cancellation_request,
            agent_run_id=cancellation_agent_run_id,
            expect_session_child=True,
            cancel_active_turn=True,
        )
    )
    cancellation_runtime = await binding_store.get_current_state(
        binding.plan_ref,
        cancellation_workflow_id,
    )
    assert cancellation_runtime is not None
    assert cancellation_runtime.state == "cleanup_complete"
    assert cancellation_runtime.binding.executionPlanRef == binding.plan_ref
    assert cancellation_runtime.binding.runtimeBindingRef not in {
        completed_state.binding.runtimeBindingRef,
        workflow_runtime.binding.runtimeBindingRef,
    }
    assert cancellation_result.metadata["executionPlanRef"] == binding.plan_ref
    assert cancellation_result.metadata["runtimeBindingRef"] == (
        cancellation_runtime.binding.runtimeBindingRef
    )
    assert cancellation_result.failure_class == "canceled"
    assert cancellation_result.metadata["terminalState"] == "canceled"
    assert "request" not in cancellation_evidence["resolveIntentInputs"][0]
    FakeOmnigentClient.hold_new_sessions = False

    async with control_store.transaction() as repositories:
        durable_session = await repositories.sessions.get(resolved["sessionId"])
    assert durable_session is not None
    assert harvest_runtime.revision < completed_state.revision
    with pytest.raises(
        ValueError,
        match="failure recorder runtime-binding authority is obsolete",
    ):
        await omnigent_session_activities.omnigent_persist_failure_activity(
            {
                "sessionId": durable_session.session_id,
                "compiledExecutionIntentRef": resolved[
                    "compiledExecutionIntentRef"
                ],
                "compiledExecutionIntentDigest": resolved[
                    "compiledExecutionIntentDigest"
                ],
                "omnigentExecutionPlan": binding.model_dump(
                    mode="json", by_alias=True
                ),
                "expectedRevision": durable_session.revision,
                "fencingGeneration": durable_session.fencing_generation,
                "runtimeBindingRef": harvest_runtime.binding.runtimeBindingRef,
                "runtimeBindingRevision": harvest_runtime.revision,
                "runtimeBindingFencingGeneration": (
                    harvest_runtime.fencing_generation
                ),
                "status": "integration_unavailable",
                "failedActivity": "omnigent.ensure_host",
                "reasonCode": "bounded_activity_exhausted",
            }
        )
    async with session_maker() as session:
        all_artifacts = list(
            (await session.scalars(select(TemporalArtifact))).all()
        )
        commands = list((await session.scalars(select(OmnigentCommand))).all())
        credential_runtimes = list(
            (await session.scalars(select(OmnigentCredentialRuntimeRecord))).all()
        )
    artifact_payloads = [
        await omnigent_session_activities._read_json_artifact(
            artifact.artifact_id
        )
        for artifact in all_artifacts
    ]
    terminal_evidence = next(
        payload
        for payload in artifact_payloads
        if payload.get("schemaVersion")
        == "omnigent-session-terminal-evidence/v1"
        and payload.get("sessionId") == resolved["sessionId"]
    )
    checkpoint_capture = terminal_evidence["terminalResult"]["result"][
        "metadata"
    ]["omnigentCheckpointCapture"]
    assert checkpoint_capture["executionPlanRef"] == binding.plan_ref
    assert checkpoint_capture["runtimeBindingRef"].startswith(
        "omnigent-runtime-binding:sha256:"
    )
    assert checkpoint_capture["runtimeBindingRevision"] < completed_state.revision
    assert checkpoint_capture["runtimeBindingFencingGeneration"] <= (
        completed_state.fencing_generation
    )
    assert checkpoint_capture["credentialRef"] == (
        f"credential://provider-profile/{provider_profile_id}/generation/6"
    )
    container_runtime_metadata: list[dict[str, Any]] = []
    for call in docker_calls:
        if call[0] == "run":
            labels = [
                call[index + 1]
                for index, value in enumerate(call)
                if value == "--label"
            ]
            environment_names = sorted(
                {
                    call[index + 1].split("=", 1)[0]
                    for index, value in enumerate(call)
                    if value == "--env"
                }
            )
            mounts = []
            for index, value in enumerate(call):
                if value != "--mount":
                    continue
                fields = {
                    item.split("=", 1)[0]: item.split("=", 1)[1]
                    for item in call[index + 1].split(",")
                    if "=" in item
                }
                mounts.append(
                    {
                        "destination": fields.get("dst"),
                        "readOnly": "readonly" in call[index + 1].split(","),
                    }
                )
            container_runtime_metadata.append(
                {
                    "containerName": call[call.index("--name") + 1],
                    "imageRef": call[-1],
                    "labels": labels,
                    "environmentVariableNames": environment_names,
                    "mounts": mounts,
                }
            )
        elif call[:2] == ("rm", "-f"):
            container_runtime_metadata.append(
                {
                    "containerName": call[-1],
                    "cleanupAction": "removed",
                }
            )
    # Scan the actual compact history handoff, every persisted JSON artifact,
    # the durable control-plane projection, and the fenced runtime binding.
    scanned_authority = {
        "temporalHistoryInput": resolve_handoff,
        "childWorkflowInput": resolved,
        "artifacts": artifact_payloads,
        "sessionMetadata": durable_session.metadata,
        "runtimeBinding": completed_state.binding.model_dump(
            mode="json", by_alias=True
        ),
        "commandJournal": [
            {
                "commandId": command.command_id,
                "kind": command.command_type,
                "payloadDigest": command.payload_digest,
                "status": command.status,
                "resultRef": command.result_ref,
            }
            for command in commands
        ],
        "containerMetadata": container_runtime_metadata,
        "credentialRuntimeMetadata": [
            {
                "credentialRuntimeRef": runtime.credential_runtime_ref,
                "providerLeaseRef": runtime.provider_lease_ref,
                "credentialGeneration": runtime.credential_generation,
                "materializerRef": runtime.materializer_ref,
            }
            for runtime in credential_runtimes
        ],
        "providerLogIndex": {
            "submittedEventCount": len(FakeOmnigentClient.submitted_events),
            "stoppedSessionIds": FakeOmnigentClient.stopped_sessions,
        },
        "workflowEvidence": workflow_evidence,
        "cancellationWorkflowEvidence": cancellation_evidence,
    }
    assert_secret_free(scanned_authority)
    serialized_authority = json.dumps(scanned_authority, sort_keys=True)
    assert "integration-only-opencode-secret" not in serialized_authority
    assert str(tmp_path) not in serialized_authority
    # The host boundary may expose its fixed in-container destination
    # (``/workspaces/run``), but it must not persist the mutable host source.
    assert all(
        "source" not in mount
        for metadata in container_runtime_metadata
        for mount in metadata.get("mounts", [])
    )
    assert "/work/agent_jobs/" not in serialized_authority
    assert "/var/run/docker.sock" not in serialized_authority
    assert '"providerPayload"' not in serialized_authority
    serialized_history = json.dumps(
        {"resolve": resolve_handoff, "child": resolved}, sort_keys=True
    )
    assert "/workspaces/" not in serialized_history
    assert '"workspacePath"' not in serialized_history
    assert "providerPayload" not in serialized_history
    assert len(serialized_history) < 8_000

    await engine.dispose()
