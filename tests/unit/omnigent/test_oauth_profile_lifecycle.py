from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    ProviderCredentialSource,
    ProviderProfileAuthMethod,
    ProviderProfileAuthState,
    RuntimeMaterializationMode,
)

from moonmind.omnigent.checkpoints import (
    OmnigentCheckpointIdentity,
    OmnigentRecoveryMode,
    recovery_mode,
    validate_branch_identity,
    validate_cold_restore_target,
)
from moonmind.omnigent.oauth_hosts import (
    OmnigentOAuthHostError,
    OmnigentOAuthHostRepository,
    validate_preflight_result,
)
from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from moonmind.omnigent.mounted_tool_preflight import MountedToolPreflightError
from moonmind.omnigent.profile_bound_execution import (
    OmnigentProfileBoundExecutionCoordinator,
)
from moonmind.provider_profiles.lease_client import (
    CredentialLeasePurpose,
    ProviderProfileLeaseClient,
    deterministic_lease_owner_id,
)
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
    AuthVolumeRef,
    CredentialMountRef,
    OmnigentHostLease,
    OmnigentOAuthHostBinding,
)
from moonmind.schemas.temporal_models import WorkspaceCheckpointEvidenceModel
from moonmind.workflows.temporal.runtime.git_auth import build_github_token_git_environment


def _binding() -> OmnigentOAuthHostBinding:
    return OmnigentOAuthHostBinding(
        bindingRef="omnigent-oauth:codex",
        providerProfileId="codex",
        endpointRef="default",
        harness="codex-native",
        credentialMountRef=CredentialMountRef(
            authVolumeRef=AuthVolumeRef(
                providerProfileId="codex",
                runtimeId="codex_cli",
                providerId="openai",
                volumeRef="codex_auth_volume",
                credentialGeneration=3,
                ownerUserId="user-1",
            ),
            targetPath="/home/app/.codex",
            runtimeUid=1000,
            runtimeGid=1000,
        ),
        staticHostId="host-1",
    )


def _host_lease() -> OmnigentHostLease:
    now = datetime(2026, 7, 12, tzinfo=UTC)
    return OmnigentHostLease(
        leaseId="host-lease-1",
        providerProfileId="codex",
        providerLeaseId="provider-lease-1",
        bindingRef="omnigent-oauth:codex",
        credentialGeneration=3,
        omnigentHostId="host-1",
        status="ready",
        acquiredAt=now,
        lastHeartbeatAt=now,
        expiresAt=now + timedelta(hours=1),
    )


def _checkpoint() -> OmnigentCheckpointIdentity:
    return OmnigentCheckpointIdentity(
        providerProfileId="codex",
        credentialGeneration=3,
        providerLeaseRef="provider-lease-1",
        hostBindingRef="omnigent-oauth:codex",
        hostLeaseRef="host-lease-1",
        endpointRef="default",
        omnigentHostId="host-1",
        omnigentSessionId="session-1",
        bridgeSessionId="bridge-1",
        externalStateRef="artifact-external-state",
        idempotencyKey="idem-1",
    )


def test_oauth_host_runtime_defaults_to_published_image(monkeypatch) -> None:
    monkeypatch.delenv("OMNIGENT_HOST_IMAGE", raising=False)
    monkeypatch.delenv("OMNIGENT_HOST_IMAGE_TAG", raising=False)

    runtime = OmnigentOAuthHostRuntime(client=SimpleNamespace())

    assert runtime._image == "ghcr.io/omnigent-ai/omnigent-host:latest"


def test_oauth_host_runtime_respects_image_tag_override(monkeypatch) -> None:
    monkeypatch.setenv("OMNIGENT_HOST_IMAGE", "ghcr.io/omnigent-ai/omnigent-host")
    monkeypatch.setenv("OMNIGENT_HOST_IMAGE_TAG", "0.2.12")

    runtime = OmnigentOAuthHostRuntime(client=SimpleNamespace())

    assert runtime._image == "ghcr.io/omnigent-ai/omnigent-host:0.2.12"


@pytest.mark.parametrize(
    "image",
    [
        "localhost:5000/omnigent-host:stable",
        "ghcr.io/omnigent-ai/omnigent-host@sha256:1234",
    ],
)
def test_oauth_host_runtime_preserves_complete_image_reference(
    monkeypatch, image: str
) -> None:
    monkeypatch.setenv("OMNIGENT_HOST_IMAGE", image)
    monkeypatch.setenv("OMNIGENT_HOST_IMAGE_TAG", "ignored")

    runtime = OmnigentOAuthHostRuntime(client=SimpleNamespace())

    assert runtime._image == image


@pytest.mark.asyncio
async def test_runtime_preflight_uses_stock_runner_environment_constructor() -> None:
    runtime = OmnigentOAuthHostRuntime(client=SimpleNamespace())
    calls: list[tuple[str, ...]] = []

    async def run(*args, **_kwargs):
        calls.append(args)
        return 0, "ready", ""

    runtime._run = run  # type: ignore[method-assign]
    binding = _binding().model_copy(update={"host_launch_profile_ref": "codex"})
    lease = _host_lease().model_copy(update={"container_name": "host-mm-1215"})

    result = await runtime._preflight_mounted_tools(
        binding=binding,
        host_lease=lease,
        required_capabilities=("gh",),
        repository="owner/repo",
        mutation_required=True,
    )

    assert result["status"] == "ready"
    runner_calls = [call for call in calls if "python" in call]
    assert len(runner_calls) == 6
    assert all("from omnigent.host.connect import _build_runner_env" in call[5] for call in runner_calls)
    assert all(call[:4] == ("docker", "exec", "host-mm-1215", "python") for call in runner_calls)


def test_deterministic_owner_reuses_activity_retry_identity() -> None:
    kwargs = {
        "profile_id": "codex",
        "purpose": CredentialLeasePurpose.EXECUTION_OMNIGENT,
        "workflow_id": "wf-1",
        "step_execution_id": "step-1",
        "idempotency_key": "idem-1",
    }
    owner_id = deterministic_lease_owner_id(**kwargs)
    assert owner_id == deterministic_lease_owner_id(**kwargs)
    assert owner_id.startswith("profile-lease:execution_omnigent:")
    assert all(value not in owner_id for value in ("codex", "wf-1", "idem-1"))
    assert CredentialLeasePurpose.OAUTH_RECONNECT.is_maintenance is True
    assert CredentialLeasePurpose.EXECUTION_DIRECT.is_maintenance is False


@pytest.mark.asyncio
async def test_activity_lease_client_marks_deterministic_owner_as_non_workflow() -> (
    None
):
    class Adapter:
        def __init__(self) -> None:
            self.payload = None

        async def get_client(self):
            return self

        async def start_workflow(self, *_args, **_kwargs):
            return None

        async def update_workflow(self, _workflow_id, _update_name, payload):
            self.payload = payload
            return {
                "profile_id": "codex",
                "lease_id": payload["requester_workflow_id"],
            }

    adapter = Adapter()
    lease = await ProviderProfileLeaseClient(adapter).acquire_execution_lease(
        runtime_id="codex_cli",
        profile_id="codex",
        owner_id="profile-lease:execution_omnigent:retry",
        purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
        metadata={"ownerIsWorkflow": True, "workflowId": "workflow-1"},
    )
    assert lease.profile_id == "codex"
    assert adapter.payload["metadata"]["ownerIsWorkflow"] is False
    assert adapter.payload["metadata"]["workflowId"] == "workflow-1"


@pytest.mark.asyncio
async def test_activity_lease_client_preserves_delegating_workflow_owner() -> None:
    class Adapter:
        def __init__(self) -> None:
            self.payload = None

        async def get_client(self):
            return self

        async def start_workflow(self, *_args, **_kwargs):
            return None

        async def update_workflow(self, _workflow_id, _update_name, payload):
            self.payload = payload
            return {
                "profile_id": "codex",
                "lease_id": payload["requester_workflow_id"],
            }

    adapter = Adapter()
    await ProviderProfileLeaseClient(adapter).acquire_maintenance_lease(
        runtime_id="codex_cli",
        profile_id="codex",
        owner_id="oauth-session:oas-1",
        purpose=CredentialLeasePurpose.OAUTH_RECONNECT,
        metadata={"workflowId": "oauth-session:oas-1"},
        owner_is_workflow=True,
    )

    assert adapter.payload["metadata"] == {
        "workflowId": "oauth-session:oas-1",
        "ownerIsWorkflow": True,
    }


@pytest.mark.asyncio
async def test_on_demand_host_initializes_state_before_unprivileged_launch(
    tmp_path,
) -> None:
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(),
        scripts_dir=tmp_path,
        workspace_root=tmp_path / "workspaces",
    )
    runtime.container_exists = AsyncMock(return_value=False)
    runtime._discover_upstream_path = AsyncMock(
        return_value="/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"
    )
    runtime._run = AsyncMock(return_value=(0, "", ""))
    binding = _binding().model_copy(
        update={"static_host_id": None, "host_launch_profile_ref": "codex-oauth-v1"}
    )
    lease = _host_lease().model_copy(update={"container_name": "mm-host-lease-1"})

    await runtime._launch_on_demand(
        binding=binding,
        host_lease=lease,
        container_name="mm-host-lease-1",
        workspace_source=tmp_path,
    )

    commands = [call.args for call in runtime._run.await_args_list]
    validation = commands[0]
    assert validation[:3] == ("docker", "run", "--rm")
    assert validation[validation.index("--entrypoint") + 1] == "/usr/bin/test"
    assert validation[-2:] == ("-f", "/etc/profile.d/moonmind-tools.sh")
    assert commands[1][:4] == ("docker", "rm", "-f", "mm-host-lease-1")
    assert "/opt/moonmind/init-codex-oauth-host.sh" in commands[2]
    assert commands[3][:3] == ("docker", "run", "-d")
    assert commands[2][commands[2].index("--user") + 1] == "0:0"
    assert commands[3][commands[3].index("--workdir") + 1] == "/home/app"
    launch = commands[3]
    assert (
        "type=volume,src=moonmind-omnigent-tools-gh-2.74.2-1,"
        "dst=/opt/moonmind-tools,readonly"
        in launch
    )
    assert any(
        value.endswith(",dst=/etc/profile.d/moonmind-tools.sh,readonly")
        for value in launch
    )
    assert (
        "PATH=/opt/moonmind-tools/bin:/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"
        in launch
    )


@pytest.mark.asyncio
async def test_private_workspace_clone_uses_in_memory_github_credentials(tmp_path) -> None:
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), workspace_root=tmp_path / "workspaces"
    )
    runtime._run = AsyncMock(return_value=(0, "", ""))

    await runtime._prepare_workspace(
        workspace_key="private",
        repository_url="https://github.com/owner/private.git",
        github_token="test-token-value",
    )

    call = runtime._run.await_args
    assert call.args[:3] == ("git", "clone", "--")
    expected = build_github_token_git_environment(
        "test-token-value", base_env=call.kwargs["env"]
    )
    assert call.kwargs["env"]["GITHUB_TOKEN"] == "test-token-value"
    assert call.kwargs["env"]["GIT_CONFIG_VALUE_1"] == expected["GIT_CONFIG_VALUE_1"]


@pytest.mark.asyncio
async def test_failed_workspace_clone_is_removed_for_retry(tmp_path) -> None:
    workspace_root = tmp_path / "workspaces"
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), workspace_root=workspace_root
    )

    async def fail_after_partial_clone(*_args, **_kwargs):
        workspace = next(workspace_root.iterdir())
        (workspace / ".git").mkdir()
        raise OmnigentOAuthHostError("clone failed")

    runtime._run = AsyncMock(side_effect=fail_after_partial_clone)

    with pytest.raises(OmnigentOAuthHostError, match="clone failed"):
        await runtime._prepare_workspace(
            workspace_key="private",
            repository_url="https://github.com/owner/private.git",
            github_token="test-token-value",
        )

    assert not any(workspace_root.iterdir())


@pytest.mark.asyncio
async def test_on_demand_host_fails_before_launch_when_profile_is_not_daemon_visible(
    tmp_path,
) -> None:
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(),
        scripts_dir=tmp_path,
        workspace_root=tmp_path / "workspaces",
        tools_profile_path=tmp_path / "worker-only-profile.sh",
    )
    runtime.container_exists = AsyncMock(return_value=False)
    runtime._run = AsyncMock(
        side_effect=OmnigentOAuthHostError("profile bind is not daemon-visible")
    )

    with pytest.raises(OmnigentOAuthHostError, match="not daemon-visible"):
        await runtime._launch_on_demand(
            binding=_binding().model_copy(
                update={
                    "static_host_id": None,
                    "host_launch_profile_ref": "codex-oauth-v1",
                }
            ),
            host_lease=_host_lease(),
            container_name="mm-host-lease-1",
            workspace_source=tmp_path,
        )

    assert runtime._run.await_count == 1
    assert runtime._run.await_args.args[:3] == ("docker", "run", "--rm")


def test_tools_path_prepend_is_idempotent_and_preserves_upstream_path() -> None:
    upstream = "/custom/bin:/usr/local/bin:/usr/bin:/bin"
    expected = "/opt/moonmind-tools/bin:/custom/bin:/usr/local/bin:/usr/bin:/bin"

    assert OmnigentOAuthHostRuntime._prepend_tools_path(upstream) == expected
    assert OmnigentOAuthHostRuntime._prepend_tools_path(expected) == expected


def test_github_write_probe_uses_publish_or_skill_side_effect() -> None:
    base = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        idempotencyKey="idem",
        correlationId="corr",
        parameters={"requiredCapabilities": ["gh"], "publishMode": "none"},
    )

    assert not OmnigentProfileBoundExecutionCoordinator._github_mutation_required(base)
    assert OmnigentProfileBoundExecutionCoordinator._github_mutation_required(
        base.model_copy(update={"parameters": {**base.parameters, "publishMode": "pr"}})
    )
    assert OmnigentProfileBoundExecutionCoordinator._github_mutation_required(
        base.model_copy(
            update={
                "parameters": {
                    **base.parameters,
                    "skill": {"sideEffect": {"kind": "merge_pull_request"}},
                }
            }
        )
    )


@pytest.mark.asyncio
async def test_tools_path_discovery_falls_back_when_image_is_not_local(tmp_path) -> None:
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(),
        scripts_dir=tmp_path,
        workspace_root=tmp_path / "workspaces",
    )
    runtime._run = AsyncMock(return_value=(1, "", "No such image"))

    assert await runtime._discover_upstream_path() == (
        "/opt/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin"
    )
    assert runtime._run.await_args.kwargs["check"] is False


def test_default_tools_profile_uses_daemon_visible_project_root(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("MOONMIND_DEPLOYMENT_LOCAL_PROJECT_DIR", str(tmp_path))

    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(),
        scripts_dir=tmp_path,
        workspace_root=tmp_path / "workspaces",
    )

    assert runtime._tools_profile_path == (
        tmp_path / "services/omnigent/profile/moonmind-tools.sh"
    )


@pytest.mark.asyncio
async def test_tools_check_uses_host_login_shell_and_manifest(tmp_path) -> None:
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(),
        scripts_dir=tmp_path,
        workspace_root=tmp_path / "workspaces",
    )
    runtime._run = AsyncMock(return_value=(0, "", ""))

    await runtime._exec_tools_check("mm-host-lease-1")

    command = runtime._run.await_args.args
    assert command[:5] == ("docker", "exec", "mm-host-lease-1", "bash", "-lc")
    assert "test -f /opt/moonmind-tools/manifest.json" in command[-1]
    assert "command -v gh" in command[-1]
    assert "gh --version" in command[-1]


@pytest.mark.asyncio
async def test_static_host_runtime_uses_only_canonical_compose_file(tmp_path) -> None:
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(),
        scripts_dir=tmp_path,
        workspace_root=tmp_path / "workspaces",
    )
    runtime._run = AsyncMock(return_value=(0, "", ""))

    await runtime._compose_static_check()
    await runtime.stop_static_host()

    commands = [call.args for call in runtime._run.await_args_list]
    assert commands == [
        (
            "docker",
            "compose",
            "-f",
            "docker-compose.yaml",
            "--profile",
            "omnigent-host-codex",
            "up",
            "-d",
            "omnigent-host-codex",
        ),
        (
            "docker",
            "compose",
            "-f",
            "docker-compose.yaml",
            "--profile",
            "omnigent-host-codex",
            "exec",
            "-T",
            "omnigent-host-codex",
            "/opt/moonmind/check-codex-oauth-host.sh",
        ),
        (
            "docker",
            "compose",
            "-f",
            "docker-compose.yaml",
            "--profile",
            "omnigent-host-codex",
            "stop",
            "omnigent-host-codex",
        ),
    ]
    assert all(
        "docker-compose.codex-host.yaml" not in command for command in commands
    )


def test_exact_host_preflight_rejects_generation_mismatch() -> None:
    result = {
        "providerProfileId": "codex",
        "runtimeId": "codex_cli",
        "providerId": "openai",
        "credentialGeneration": 4,
        "mountPath": "/home/app/.codex",
        "runtimeUid": 1000,
        "runtimeGid": 1000,
        "loginStatus": "authenticated",
        "hostId": "host-1",
        "harness": "codex-native",
        "competingCredentialsPresent": False,
    }
    with pytest.raises(OmnigentOAuthHostError) as exc_info:
        validate_preflight_result(
            result=result, binding=_binding(), host_lease=_host_lease()
        )
    assert exc_info.value.code == "CODEX_OAUTH_GENERATION_STALE"


def test_checkpoint_live_reattach_requires_every_original_authority() -> None:
    checkpoint = _checkpoint()
    assert (
        recovery_mode(
            checkpoint,
            provider_lease={"active": True, "leaseId": "provider-lease-1"},
            host_lease={
                "status": "assigned",
                "leaseId": "host-lease-1",
                "credentialGeneration": 3,
            },
            host_registered=True,
            session_valid=True,
            first_message_consistent=True,
        )
        == OmnigentRecoveryMode.LIVE_REATTACH
    )
    assert (
        recovery_mode(
            checkpoint,
            provider_lease={"active": True, "leaseId": "provider-lease-1"},
            host_lease={
                "status": "assigned",
                "leaseId": "host-lease-1",
                "credentialGeneration": 4,
            },
            host_registered=True,
            session_valid=True,
            first_message_consistent=True,
        )
        == OmnigentRecoveryMode.COLD_RESTORE
    )


def test_cold_restore_and_branch_preserve_profile_and_exclusive_identity() -> None:
    checkpoint = _checkpoint()
    validate_cold_restore_target(
        checkpoint, provider_profile_id="codex", credential_generation=3
    )
    validate_branch_identity(
        checkpoint, new_host_lease_ref="host-lease-2", new_session_id="session-2"
    )
    with pytest.raises(ValueError, match="new host lease"):
        validate_branch_identity(
            checkpoint,
            new_host_lease_ref="host-lease-1",
            new_session_id="session-2",
        )


def test_checkpoint_rejects_raw_credentials_and_accepts_safe_identity_refs() -> None:
    evidence = WorkspaceCheckpointEvidenceModel(
        kind="external_state_ref",
        externalStateRef="artifact-external-state",
        providerProfileId="codex",
        credentialGeneration=3,
        providerLeaseRef="provider-lease-1",
        hostBindingRef="omnigent-oauth:codex",
        hostLeaseRef="host-lease-1",
        endpointRef="default",
        omnigentHostId="host-1",
        omnigentSessionId="session-1",
        bridgeSessionId="bridge-1",
        idempotencyKey="idem-1",
    )
    assert evidence.credential_generation == 3
    with pytest.raises(ValidationError, match="raw credentials"):
        WorkspaceCheckpointEvidenceModel(
            kind="external_state_ref",
            externalStateRef="bearer access-token-value",
            providerProfileId="codex",
            credentialGeneration=3,
            hostBindingRef="omnigent-oauth:codex",
            endpointRef="default",
            bridgeSessionId="bridge-1",
            idempotencyKey="idem-1",
        )


@pytest.mark.asyncio
async def test_host_repository_creates_idempotent_binding_and_lease(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/hosts.db")

    @event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with factory() as session:
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
                    credential_generation=3,
                    enabled=True,
                    auth_state=ProviderProfileAuthState.CONNECTED,
                    last_auth_method=ProviderProfileAuthMethod.OAUTH_VOLUME,
                )
            )
            await session.commit()
        repository = OmnigentOAuthHostRepository(factory)
        binding = await repository.create_or_update_static_binding(
            profile_id="codex",
            endpoint_ref="default",
            static_host_id="host-1",
        )
        first = await repository.create_or_get_host_lease(
            binding=binding,
            provider_lease_id="provider-lease-1",
            holder_workflow_id="workflow-1",
            agent_run_id="step-1",
            idempotency_key="idem-1",
        )
        second = await repository.create_or_get_host_lease(
            binding=binding,
            provider_lease_id="provider-lease-1",
            holder_workflow_id="workflow-1",
            agent_run_id="step-1",
            idempotency_key="idem-1",
        )
        assert first.lease_id == second.lease_id
        starting = await repository.transition_host_lease(
            first.lease_id,
            expected_status="allocating",
            new_status="starting",
        )
        assert starting.status == "starting"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_coordinator_releases_provider_lease_after_host_cleanup() -> None:
    actions: list[str] = []
    provider_lease = SimpleNamespace(
        profile_id="codex",
        runtime_id="codex_cli",
        lease_id="provider-lease-1",
        owner_id="owner-1",
        purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
    )

    class LeaseClient:
        async def acquire_execution_lease(self, **_kwargs):
            actions.append("provider_acquired")
            return provider_lease

        async def release_lease(self, _lease):
            actions.append("provider_released")

        async def record_cooldown(self, **_kwargs):
            actions.append("cooldown")

    class Hosts:
        def __init__(self):
            self.lease = _host_lease().model_copy(
                update={"status": "allocating", "omnigent_host_id": None}
            )

        async def get_binding_for_profile(self, _profile_id):
            return _binding()

        async def create_or_get_host_lease(self, **_kwargs):
            actions.append("host_lease_created")
            return self.lease

        async def transition_host_lease(
            self, _lease_id, *, expected_status, new_status, fields=None
        ):
            assert self.lease.status == expected_status
            self.lease = self.lease.model_copy(
                update={"status": new_status, **dict(fields or {})}
            )
            actions.append(f"host_{new_status}")
            return self.lease

        async def mark_host_lease_stopped(self, _lease_id):
            actions.append("host_stopped")
            self.lease = self.lease.model_copy(update={"status": "stopped"})
            return self.lease

        async def mark_host_lease_failed(self, *_args, **_kwargs):
            actions.append("host_failed")

    class Runtime:
        async def prepare_host(self, **_kwargs):
            actions.append("preflight")
            return {"hostId": "host-1", "workspacePath": "/workspaces/run"}

        async def stop_host(self, **_kwargs):
            actions.append("host_cleanup")

    class Store:
        async def bind_profile_authorization(self, **_kwargs):
            actions.append("bridge_bound")
            return SimpleNamespace(bridge_session_id="bridge-1")

        async def record_lifecycle_event(self, _key, *, event_type, **_kwargs):
            actions.append(event_type)

    async def execute(request, **_kwargs):
        assert request.parameters["omnigent"]["session"] == {
            "hostType": "external",
            "hostId": "host-1",
            "workspace": "/workspaces/run",
        }
        actions.append("executed")
        return AgentRunResult(summary="done")

    coordinator = OmnigentProfileBoundExecutionCoordinator(
        session_factory=lambda: None,
        lease_client=LeaseClient(),
        host_repository=Hosts(),
        host_runtime=Runtime(),
        run_store=Store(),
        execution_runner=execute,
        artifact_gateway=object(),
    )
    coordinator._resolve_profile = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(cooldown_after_429_seconds=900)
    )
    result = await coordinator.execute(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            executionProfileRef="codex",
            correlationId="workflow-1",
            idempotencyKey="idem-1",
            parameters={
                "omnigent": {"session": {"workspace": "https://example.com/repo.git"}}
            },
        )
    )
    assert result.summary == "done"
    assert actions[-3:] == ["host_stopped", "cleanup_completed", "provider_released"]


@pytest.mark.asyncio
async def test_coordinator_records_runner_preflight_block_before_execution() -> None:
    events: list[tuple[str, dict]] = []
    execute = AsyncMock()
    provider_lease = SimpleNamespace(
        profile_id="codex",
        runtime_id="codex_cli",
        lease_id="provider-lease-1",
        owner_id="owner-1",
        purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
    )

    class LeaseClient:
        async def acquire_execution_lease(self, **_kwargs):
            return provider_lease

        async def release_lease(self, _lease):
            return None

    class Hosts:
        def __init__(self):
            self.lease = _host_lease().model_copy(
                update={"status": "allocating", "omnigent_host_id": None}
            )

        async def get_binding_for_profile(self, _profile_id):
            return _binding()

        async def create_or_get_host_lease(self, **_kwargs):
            return self.lease

        async def transition_host_lease(
            self, _lease_id, *, expected_status, new_status, fields=None
        ):
            assert self.lease.status == expected_status
            self.lease = self.lease.model_copy(
                update={"status": new_status, **dict(fields or {})}
            )
            return self.lease

        async def mark_host_lease_stopped(self, _lease_id):
            self.lease = self.lease.model_copy(update={"status": "stopped"})
            return self.lease

        async def mark_host_lease_failed(self, *_args, **_kwargs):
            return None

    class Runtime:
        async def prepare_host(self, **kwargs):
            assert kwargs["required_capabilities"] == ("gh",)
            raise MountedToolPreflightError(
                "Mounted gh preflight failed during runner authentication",
                code="github_auth_unavailable",
                evidence={
                    "tool": "gh",
                    "phase": "authentication",
                    "probes": [{"boundary": "runner", "status": "failed"}],
                },
            )

        async def stop_host(self, **_kwargs):
            return None

    class Store:
        async def bind_profile_authorization(self, **_kwargs):
            return SimpleNamespace(bridge_session_id="bridge-1")

        async def record_lifecycle_event(self, _key, *, event_type, **kwargs):
            events.append((event_type, kwargs))

    coordinator = OmnigentProfileBoundExecutionCoordinator(
        session_factory=lambda: None,
        lease_client=LeaseClient(),
        host_repository=Hosts(),
        host_runtime=Runtime(),
        run_store=Store(),
        execution_runner=execute,
        artifact_gateway=object(),
    )
    coordinator._resolve_profile = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(cooldown_after_429_seconds=900)
    )
    coordinator._github_token = AsyncMock(return_value="resolved-token")  # type: ignore[method-assign]

    with pytest.raises(MountedToolPreflightError):
        await coordinator.execute(
            AgentExecutionRequest(
                agentKind="external",
                agentId="omnigent",
                executionProfileRef="codex",
                correlationId="workflow-1",
                idempotencyKey="idem-1",
                parameters={
                    "repository": "owner/repo",
                    "requiredCapabilities": ["gh"],
                    "omnigent": {
                        "session": {"workspace": "https://github.com/owner/repo.git"}
                    },
                },
            )
        )

    execute.assert_not_awaited()
    blocked = next(kwargs for name, kwargs in events if name == "mounted_tool_preflight_blocked")
    assert blocked["code"] == "github_auth_unavailable"
    assert blocked["metadata"] == {
        "tool": "gh",
        "phase": "authentication",
        "probes": [{"boundary": "runner", "status": "failed"}],
    }
