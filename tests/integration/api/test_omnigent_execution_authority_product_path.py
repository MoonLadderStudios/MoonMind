"""Hermetic product-boundary authority journey for issue #3706."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api_service.api.routers import executions as executions_router
from api_service.db import base as db_base
from api_service.db.base import get_async_session
from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    OmnigentExecutionPlanRecord,
    TemporalArtifact,
)
from api_service.services import omnigent_execution_plan_service
from moonmind.omnigent.conformance import assert_secret_free
from moonmind.omnigent.control_plane import OmnigentControlPlaneStore, compute_digest
from moonmind.omnigent.harness_platform.stores import DbRuntimeBindingStore
from moonmind.provider_profiles import lease_client as lease_client_module
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    OmnigentExecutionPlanBinding,
)
from moonmind.schemas.omnigent_session_models import OmnigentSessionAdmissionRequest
from moonmind.workflows.temporal.activities import omnigent_session_activities
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactService,
)
from tests.unit.api.routers.test_executions import (
    _build_execution_record,
    _override_user_dependencies,
)
from tests.unit.services.test_omnigent_execution_plan_service import (
    _policy_snapshot,
    _snapshot,
)


pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


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
        session.add(
            ManagedAgentProviderProfile(
                profile_id=provider_profile_id,
                runtime_id=runtime_id,
                provider_id=provider_id,
                credential_generation=5,
                enabled=True,
                auth_state="connected",
            )
        )
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
        # Codex admission deliberately preserves its recorded coordinator; it
        # must not be routed through the generic host/session realizer.
        assert (
            await DbRuntimeBindingStore(session_maker).get_current_state(
                binding.plan_ref, f"workflow-{harness}-product"
            )
            is None
        )
        await engine.dispose()
        return

    workflow_id = f"workflow-{harness}-product"
    resolved = await omnigent_session_activities.omnigent_resolve_intent_activity(
        {
            "workflowId": workflow_id,
            "stepExecutionId": f"step-{harness}-product",
            "agentRunId": f"agent-run-{harness}-product",
            "request": AgentExecutionRequest(
                agentKind="external",
                agentId="omnigent",
                executionProfileRef=provider_profile_id,
                omnigentExecutionPlan=binding,
                correlationId=workflow_id,
                idempotencyKey="opencode-product-authority",
                instructionRef=binding.task_input_snapshot_ref,
                resolvedSkillsetRef=authored["resolvedSkillsetRef"],
                parameters={
                    "repository": "MoonLadderStudios/MoonMind",
                    "publishMode": "none",
                    "omnigent": {
                        "executionTargetRef": execution_target,
                        "launchPolicyRef": launch_policy,
                        "agent": {
                            "agentId": snapshot["agentId"],
                            "harnessOverride": harness,
                        },
                        "session": {
                            "hostType": "managed",
                            "allowEmptyWorkspace": True,
                        },
                    },
                },
            ).model_dump(mode="json", by_alias=True, exclude_none=True),
        }
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

    async with control_store.transaction() as repositories:
        durable_session = await repositories.sessions.get(resolved["sessionId"])
    assert durable_session is not None
    async with session_maker() as session:
        all_artifacts = list(
            (await session.scalars(select(TemporalArtifact))).all()
        )
    artifact_payloads = [
        await omnigent_session_activities._read_json_artifact(
            artifact.artifact_id
        )
        for artifact in all_artifacts
    ]
    # Scan the actual compact history handoff, every persisted JSON artifact,
    # the durable control-plane projection, and the fenced runtime binding.
    assert_secret_free(
        {
            "temporalHistoryInput": resolved,
            "artifacts": artifact_payloads,
            "sessionMetadata": durable_session.metadata,
            "runtimeBinding": rotated_state.binding.model_dump(
                mode="json", by_alias=True
            ),
        }
    )

    await engine.dispose()
