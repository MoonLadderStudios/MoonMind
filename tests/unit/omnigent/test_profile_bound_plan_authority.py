"""Exercise admitted Codex policy authority across planning and dispatch."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from api_service.services import omnigent_execution_plan_service as service
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.profile_bound_execution import (
    OmnigentProfileBoundExecutionCoordinator,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from tests.unit.services.test_omnigent_execution_plan_service import (
    _ArtifactService,
    _PlanStore,
    _configure_ready_host_image_pair,
    _policy_snapshot,
    _protected_support_evidence,
    _snapshot,
)


@pytest_asyncio.fixture
async def admitted_codex_plan(monkeypatch, request):
    policy = getattr(request, "param", "codex-on-demand@16")
    _configure_ready_host_image_pair(monkeypatch)
    monkeypatch.setenv(
        "OMNIGENT_SHARED_HOST_IMAGE_REF",
        "ghcr.io/example/omnigent-host@sha256:" + "f" * 64,
    )
    snapshot = _policy_snapshot(harness="codex-native", policy=policy)
    monkeypatch.setattr(service, "DbExecutionPlanStore", _PlanStore)
    monkeypatch.setattr(
        service, "_resolve_runtime_policy_snapshot", AsyncMock(return_value=snapshot)
    )
    monkeypatch.setattr(
        service,
        "resolve_execution_evidence",
        lambda payload, **_kwargs: (_protected_support_evidence(payload), "supported"),
    )
    artifacts = _ArtifactService()
    parameters = {
        "targetRuntime": "omnigent",
        "publishMode": "auto",
        "omnigent": {
            "executionTargetRef": "omnigent-codex@1",
            "launchPolicyRef": policy,
        },
    }
    admitted = await service.compile_and_persist_execution_plan(
        session_factory=object(),
        artifact_service=artifacts,
        principal="user-1",
        workflow_id="mm:pr-resolver-policy-regression",
        agent_profile_snapshot=_snapshot(
            harness="codex-native", policy=policy, provider_id="codex"
        ),
        provider_profile=SimpleNamespace(
            profile_id="codex", runtime_id="codex_cli", provider_id="openai"
        ),
        initial_parameters=parameters,
        authored_request_ref="art_request_1",
        authored_request_digest="sha256:" + "1" * 64,
        task_input_snapshot_ref="art_request_1",
        task_input_snapshot_digest="sha256:" + "1" * 64,
    )
    coordinator = OmnigentProfileBoundExecutionCoordinator(
        session_factory=lambda: None,
        lease_client=object(),
        host_repository=object(),
        host_runtime=object(),
        run_store=object(),
        execution_runner=AsyncMock(),
        artifact_gateway=artifacts,
        execution_plan=admitted.envelope,
    )
    execution_request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="codex",
        correlationId="workflow-1",
        idempotencyKey="attempt-1",
        parameters=parameters,
        omnigentExecutionPlan=admitted.binding,
    )
    launch = json.loads(
        artifacts.payloads[
            admitted.envelope.payload.effectiveLaunchSnapshotRef.removeprefix(
                "artifact:"
            )
        ]
    )
    return coordinator, execution_request, admitted.envelope, snapshot, launch


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "admitted_codex_plan",
    ["codex-on-demand@1", "codex-on-demand@16", "operator-codex@2"],
    indirect=True,
)
async def test_dispatch_accepts_admitted_policy_independent_of_target_default(
    admitted_codex_plan,
):
    coordinator, request, plan, snapshot, launch = admitted_codex_plan
    assert coordinator._require_recorded_plan_request(request) is plan
    coordinator._require_recorded_launch(
        policy_snapshot=snapshot, effective_launch=launch
    )
    assert (
        launch["launchPolicyRef"] == request.parameters["omnigent"]["launchPolicyRef"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("launchPolicyRef", "codex-on-demand@1", "launch policy conflicts"),
        ("executionTargetRef", "omnigent-claude@1", "execution target conflicts"),
        ("executionTargetRef", "unknown-target@1", "execution target conflicts"),
    ],
)
async def test_dispatch_rejects_conflicting_selection_before_effects(
    admitted_codex_plan, field, value, message
):
    coordinator, request, _plan, _snapshot, _launch = admitted_codex_plan
    request.parameters["omnigent"][field] = value
    # execute validates the admitted request before touching the inert ports.
    with pytest.raises(HarnessPlatformError, match=message) as error:
        await coordinator.execute(request)
    assert error.value.code == "OMNIGENT_EXECUTION_PLAN_CONFLICT"


class _PolicySelected(Exception):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("admitted_codex_plan", "omitted"),
    [
        ("codex-on-demand@16", ("launchPolicyRef",)),
        ("operator-codex@2", ("launchPolicyRef",)),
        ("codex-on-demand@16", ("launchPolicyRef", "executionTargetRef")),
    ],
    indirect=["admitted_codex_plan"],
)
async def test_dispatch_selects_admitted_policy_when_request_omits_it(
    admitted_codex_plan, omitted
):
    coordinator, request, plan, _snapshot, _launch = admitted_codex_plan
    for field in omitted:
        request.parameters["omnigent"].pop(field)
    selected: list[str] = []

    async def resolve_runtime_snapshot(policy_ref: str):
        selected.append(policy_ref)
        raise _PolicySelected

    coordinator._run_store = AsyncMock()
    coordinator._hosts = SimpleNamespace(
        get_binding_for_profile=AsyncMock(return_value=None)
    )
    coordinator._profile_authority = SimpleNamespace(
        resolve=AsyncMock(
            return_value=SimpleNamespace(runtime_id="codex_cli", launch_ready=True)
        )
    )
    coordinator._policy_authority = SimpleNamespace(
        resolve_runtime_snapshot=resolve_runtime_snapshot
    )

    # Stop at the first policy lookup: selection precedes every lease and host
    # effect, and the admitted policy is the one the plan's snapshots record.
    with pytest.raises(_PolicySelected):
        await coordinator.execute(request)
    assert selected == [plan.payload.launchPolicyRef]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_snapshot", ["policy", "launch"])
async def test_dispatch_still_rejects_changed_admitted_launch(
    admitted_codex_plan, changed_snapshot
):
    coordinator, request, _plan, snapshot, launch = admitted_codex_plan
    coordinator._require_recorded_plan_request(request)
    if changed_snapshot == "policy":
        snapshot["boundaries"]["execution"]["profileRef"] = "omnigent-claude@1"
    else:
        launch["launchPolicyRef"] = "codex-on-demand@1"
    with pytest.raises(HarnessPlatformError, match="launch authority has drifted"):
        coordinator._require_recorded_launch(
            policy_snapshot=snapshot, effective_launch=launch
        )


class _EffectiveLaunchCompiled(Exception):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("request_names_policy", [True, False])
async def test_recovered_successor_keeps_admitted_launch_after_update_cutover(
    monkeypatch, tmp_path, request_names_policy
):
    """A host-loss successor survives an update that cut its binding over.

    The Run admitted ``codex-on-demand@1`` on the first installed host image.
    An update installs a compatible newer image; startup bootstrap versions the
    policy and moves the profile's idle host binding (the lost attempt's lease
    is stopped) to the new default. The ``runtime_recovered`` successor under
    the same admitted plan must still compile the admitted launch on the image
    the plan recorded instead of conflicting with the cut-over binding.
    """

    from datetime import UTC, datetime, timedelta

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from api_service.db.models import (
        Base,
        ManagedAgentProviderProfile,
        OmnigentOAuthHostBindingRecord,
        OmnigentOAuthHostLeaseRecord,
        ProviderCredentialSource,
        RuntimeMaterializationMode,
    )
    from api_service.services.omnigent_policies import seed_bootstrap_policies
    from moonmind.omnigent.execution_adapters import DbExecutionPolicyAuthority
    from moonmind.omnigent.oauth_hosts import OmnigentOAuthHostRepository
    from tests.unit.services.test_omnigent_execution_plan_service import (
        _SERVER_IMAGE_REF,
    )

    first_host = "ghcr.io/example/omnigent-host@sha256:" + "f" * 64
    installed_host = "ghcr.io/example/omnigent-host@sha256:" + "e" * 64
    _configure_ready_host_image_pair(monkeypatch)
    monkeypatch.setenv("OMNIGENT_SHARED_HOST_IMAGE_REF", first_host)

    async def resolve_image(image_ref: str) -> str:
        return _SERVER_IMAGE_REF if "server" in image_ref else first_host

    async def live_server(_image_ref: str) -> str:
        return _SERVER_IMAGE_REF

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/update.db")
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as session:
            await seed_bootstrap_policies(
                session,
                image_resolver=resolve_image,
                live_server_image_resolver=live_server,
            )
        policies = DbExecutionPolicyAuthority(sessions)
        admitted_snapshot = await policies.resolve_runtime_snapshot("codex-on-demand@1")
        assert admitted_snapshot["boundaries"]["host"]["hostImageRef"] == first_host

        monkeypatch.setattr(service, "DbExecutionPlanStore", _PlanStore)
        monkeypatch.setattr(
            service,
            "_resolve_runtime_policy_snapshot",
            AsyncMock(return_value=admitted_snapshot),
        )
        monkeypatch.setattr(
            service,
            "resolve_execution_evidence",
            lambda payload, **_kwargs: (
                _protected_support_evidence(payload),
                "supported",
            ),
        )
        artifacts = _ArtifactService()
        parameters = {
            "targetRuntime": "omnigent",
            "publishMode": "auto",
            "omnigent": {
                "executionTargetRef": "omnigent-codex@1",
                "launchPolicyRef": "codex-on-demand@1",
            },
        }
        admitted = await service.compile_and_persist_execution_plan(
            session_factory=object(),
            artifact_service=artifacts,
            principal="user-1",
            workflow_id="workflow-1",
            agent_profile_snapshot=_snapshot(
                harness="codex-native",
                policy="codex-on-demand@1",
                provider_id="codex",
            ),
            provider_profile=SimpleNamespace(
                profile_id="codex", runtime_id="codex_cli", provider_id="openai"
            ),
            initial_parameters=parameters,
            authored_request_ref="art_request_1",
            authored_request_digest="sha256:" + "1" * 64,
            task_input_snapshot_ref="art_request_1",
            task_input_snapshot_digest="sha256:" + "1" * 64,
        )
        assert admitted.envelope.payload.hostImageRef == first_host

        # The lost first Step Execution ran on the admitted binding; its host
        # lease is stopped by the host-loss cleanup before any successor.
        now = datetime.now(UTC)
        async with sessions() as session:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id="codex",
                    runtime_id="codex_cli",
                    provider_id="openai",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref="codex_auth_volume",
                    volume_mount_path="/home/app/.codex",
                    max_parallel_runs=1,
                    credential_generation=1,
                )
            )
            session.add(
                OmnigentOAuthHostBindingRecord(
                    binding_ref="codex-binding",
                    provider_profile_id="codex",
                    endpoint_ref="default",
                    harness="codex-native",
                    credential_mount_template_json={
                        "authVolumeRef": {
                            "providerProfileId": "codex",
                            "runtimeId": "codex_cli",
                            "providerId": "openai",
                            "volumeRef": "codex_auth_volume",
                            "credentialGeneration": 1,
                            "ownerUserId": "profile:codex",
                        },
                        "targetPath": "/home/app/.codex",
                        "accessMode": "read_write",
                        "runtimeUid": 1000,
                        "runtimeGid": 1000,
                    },
                    host_launch_profile_ref="codex-on-demand@1",
                    execution_profile_ref="omnigent-codex@1",
                    launch_policy_ref="codex-on-demand@1",
                    effective_launch_snapshot_json={"snapshotRef": "lost-attempt"},
                )
            )
            await session.commit()
            await session.execute(
                OmnigentOAuthHostLeaseRecord.__table__.insert().values(
                    lease_id="lost-attempt-lease",
                    provider_profile_id="codex",
                    provider_lease_id="lost-provider-lease",
                    binding_ref="codex-binding",
                    credential_generation=1,
                    holder_workflow_id="workflow-1",
                    idempotency_key=(
                        "workflow-1:run-1:implement:execution:1:agent_execute"
                    ),
                    lease_purpose="execution",
                    status="stopped",
                    acquired_at=now,
                    last_heartbeat_at=now,
                    stopped_at=now,
                    host_capabilities_json={},
                    expires_at=now + timedelta(hours=1),
                )
            )
            await session.commit()

        # The update installs a compatible newer shared host image; startup
        # bootstrap versions the policy and cuts the idle binding over.
        monkeypatch.setenv("OMNIGENT_SHARED_HOST_IMAGE_REF", installed_host)
        async with sessions() as session:
            await seed_bootstrap_policies(
                session,
                image_resolver=resolve_image,
                live_server_image_resolver=live_server,
            )
        hosts = OmnigentOAuthHostRepository(sessions)
        binding = await hosts.get_binding_for_profile("codex")
        assert binding is not None
        assert binding.launch_policy_ref == "codex-on-demand@2"

        compiled: list[dict] = []

        async def record_lifecycle_event(_key, *, event_type, metadata=None, **_kw):
            if event_type == "effective_launch_compiled":
                compiled.append(dict((metadata or {})["effectiveLaunch"]))
                raise _EffectiveLaunchCompiled

        run_store = AsyncMock()
        run_store.record_lifecycle_event = record_lifecycle_event
        coordinator = OmnigentProfileBoundExecutionCoordinator(
            session_factory=sessions,
            lease_client=object(),
            host_repository=hosts,
            host_runtime=object(),
            run_store=run_store,
            execution_runner=AsyncMock(),
            artifact_gateway=artifacts,
            execution_plan=admitted.envelope,
            provider_profile_authority=SimpleNamespace(
                resolve=AsyncMock(
                    return_value=SimpleNamespace(
                        runtime_id="codex_cli",
                        launch_ready=True,
                        credential_generation=1,
                    )
                )
            ),
            policy_authority=policies,
            execution_attempts=SimpleNamespace(current_attempt=lambda: 1),
        )
        successor_parameters = json.loads(json.dumps(parameters))
        if not request_names_policy:
            successor_parameters["omnigent"].pop("launchPolicyRef")
        successor = AgentExecutionRequest.model_validate(
            {
                "agentKind": "external",
                "agentId": "omnigent",
                "executionProfileRef": "codex",
                "correlationId": "workflow-1",
                "idempotencyKey": (
                    "workflow-1:run-1:implement:execution:2:agent_execute"
                ),
                "parameters": successor_parameters,
                "omnigentExecutionPlan": admitted.binding.model_dump(
                    mode="json", by_alias=True
                ),
                "stepExecution": {
                    "workflowId": "workflow-1",
                    "runId": "run-1",
                    "logicalStepId": "implement",
                    "executionOrdinal": 2,
                    "stepExecutionId": "workflow-1:run-1:implement:execution:2",
                    "runtimeContextPolicy": "fresh_agent_run",
                    "reason": "runtime_recovered",
                },
            }
        )

        # Stop at the compiled launch: it precedes every lease or host effect.
        with pytest.raises(_EffectiveLaunchCompiled):
            await coordinator.execute(successor)
    finally:
        await engine.dispose()

    [launch] = compiled
    assert launch["launchPolicyRef"] == "codex-on-demand@1"
    assert launch["hostImageRef"] == first_host
    assert launch["hostImageRef"] == admitted.envelope.payload.hostImageRef
