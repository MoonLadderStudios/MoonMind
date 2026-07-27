import hashlib
import json
import subprocess
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api_service.db.models import (
    ProviderCredentialSource,
    ProviderProfileAuthState,
    RuntimeMaterializationMode,
)
from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from moonmind.omnigent.profile_bound_execution import (
    OmnigentProfileBoundExecutionCoordinator,
)
from moonmind.provider_profiles.lease_client import (
    CredentialLease,
    CredentialLeasePurpose,
)
from moonmind.schemas.agent_runtime_models import (
    AgentRunResult,
    AuthVolumeRef,
    CredentialMountRef,
    OmnigentHostLease,
    OmnigentOAuthHostBinding,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
)
from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner
from moonmind.workflows.temporal.workflows import run as run_workflow


pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


class _Artifacts:
    def __init__(self) -> None:
        self.reads: list[str] = []

    async def read(self, *, artifact_id: str, **_kwargs):
        self.reads.append(artifact_id)
        return {}, f"payload:{artifact_id}".encode()


def _git(*args: str, cwd) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, text=True, capture_output=True
    ).stdout.strip()


def _binding(*, on_demand: bool) -> OmnigentOAuthHostBinding:
    return OmnigentOAuthHostBinding(
        bindingRef=f"omnigent-oauth:codex:{'dynamic' if on_demand else 'static'}",
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
        staticHostId=None if on_demand else "host-1",
        hostLaunchProfileRef="codex" if on_demand else None,
    )


class _Hosts:
    def __init__(self, *, on_demand: bool, events: list[str]) -> None:
        self.binding = _binding(on_demand=on_demand)
        self.events = events
        self.attempt = 0
        self.lease: OmnigentHostLease | None = None

    async def get_binding_for_profile(self, _profile_id):
        return self.binding

    async def create_or_get_host_lease(self, **_kwargs):
        self.attempt += 1
        now = datetime(2026, 7, 12, tzinfo=UTC)
        self.lease = OmnigentHostLease(
            leaseId=f"host-lease-{self.attempt}",
            providerProfileId="codex",
            providerLeaseId=f"provider-lease-{self.attempt}",
            bindingRef=self.binding.binding_ref,
            credentialGeneration=3,
            status="allocating",
            acquiredAt=now,
            lastHeartbeatAt=now,
            expiresAt=now + timedelta(hours=1),
        )
        return self.lease

    async def transition_host_lease(
        self, _lease_id, *, expected_status, new_status, fields=None
    ):
        assert self.lease is not None and self.lease.status == expected_status
        self.lease = self.lease.model_copy(
            update={"status": new_status, **dict(fields or {})}
        )
        return self.lease

    async def create_or_update_static_binding(self, **_kwargs):
        return self.binding

    async def mark_host_lease_stopped(self, _lease_id):
        assert self.lease is not None
        self.lease = self.lease.model_copy(update={"status": "stopped"})
        self.events.append("host_stopped")
        return self.lease

    async def mark_host_lease_failed(self, *_args, **_kwargs):
        raise AssertionError("normal journey must not require janitor recovery")


class _Runtime(OmnigentOAuthHostRuntime):
    def __init__(self, *, workspace_root, events: list[str]) -> None:
        super().__init__(client=SimpleNamespace(), workspace_root=workspace_root)
        self.events = events
        self.prepared_modes: list[str] = []

    async def _prepare_skill_projection(self, **kwargs):
        projection = self._workspace_root / "skill-projections" / hashlib.sha256(
            kwargs["workspace_key"].encode()
        ).hexdigest()[:12]
        projection.mkdir(parents=True, exist_ok=True)
        (projection / "skill.ref").write_text(
            kwargs["resolved_skillset_ref"], encoding="utf-8"
        )
        return projection

    async def _launch_on_demand(self, **_kwargs):
        self.prepared_modes.append("on_demand_docker")

    async def _compose_static_check(self, **_kwargs):
        self.prepared_modes.append("static_compose")

    async def _exec_check(self, _container_name):
        return None

    async def _exec_tools_check(self, _container_name):
        return None

    async def _resolve_exact_host(self, **_kwargs):
        return {"id": "host-1", "harnesses": ["codex-native"]}

    async def _preflight_mounted_tools(self, **_kwargs):
        return {"status": "not_required", "boundaries": []}

    async def stop_host(self, **_kwargs):
        self.events.append("host_cleanup")


class _Store:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.lifecycle: list[tuple[str, str | None, dict]] = []

    async def get_or_create(self, **_kwargs):
        return SimpleNamespace(bridge_session_id="bridge-1")

    async def bind_profile_authorization(self, **_kwargs):
        return SimpleNamespace(bridge_session_id="bridge-1")

    async def record_lifecycle_event(self, _key, *, event_type, **kwargs):
        metadata = dict(kwargs.get("metadata") or {})
        self.lifecycle.append((event_type, kwargs.get("status"), metadata))
        if event_type in {"host_cleanup", "profile_lease_release", "terminal"}:
            self.events.append(event_type)


def _request_from_normal_workflow(monkeypatch, authored, *, resolved_skillset_ref):
    workflow = run_workflow.MoonMindRunWorkflow()
    monkeypatch.setattr(
        run_workflow.workflow,
        "info",
        lambda: SimpleNamespace(workflow_id="workflow-3507", run_id="run-1"),
    )
    return workflow._build_agent_execution_request(
        node_inputs=authored,
        node_id="normal-workspace",
        tool_name="agent_runtime",
        resolved_skillset_ref=resolved_skillset_ref,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("on_demand", [False, True], ids=["static", "on-demand"])
async def test_normal_workflow_runs_complete_owned_omnigent_journey(
    tmp_path, monkeypatch, on_demand
) -> None:
    workspace_root = tmp_path / "jobs"
    seed = workspace_root / "sources" / "seed"
    source = workspace_root / "sources" / "repository.git"
    seed.mkdir(parents=True)
    _git("init", "-b", "main", cwd=seed)
    _git("config", "user.email", "test@example.invalid", cwd=seed)
    _git("config", "user.name", "MoonMind Test", cwd=seed)
    (seed / "README.md").write_text("source\n", encoding="utf-8")
    _git("add", "README.md", cwd=seed)
    _git("commit", "-m", "initial", cwd=seed)
    _git("clone", "--bare", str(seed), str(source), cwd=workspace_root)

    plan = _build_runtime_planner()(
        inputs={
            "task": {
                "instructions": "Update the repository",
                "title": "Normal Omnigent workflow",
                "repository": str(source),
                "git": {"branch": "main"},
                "runtime": {
                    "mode": "omnigent",
                    "profileId": "codex",
                    "executionProfileRef": "codex",
                },
                "workspaceSpec": {"targetBranch": "issue-3507"},
                "publish": {
                    "mode": "pr",
                    "baseBranch": "main",
                    "commitMessage": "Complete #3507",
                },
            }
        },
        parameters={},
        snapshot=SimpleNamespace(
            digest="reg:sha256:test", artifact_ref="artifact:registry"
        ),
    )
    authored = plan["nodes"][0]["inputs"]
    request = _request_from_normal_workflow(
        monkeypatch, authored, resolved_skillset_ref="artifact:skills-1"
    )
    step_id = request.idempotency_key
    workspace_id = hashlib.sha256(
        f"{request.correlation_id}:{step_id}".encode()
    ).hexdigest()[:24]
    request = request.model_copy(
        update={
            "input_refs": ("artifact:attachment-1", "checkpoint:restore-1"),
            "workspace_spec": {
                **request.workspace_spec,
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": workspace_id,
                    "relativePath": "repo",
                },
            },
            "parameters": {
                **request.parameters,
                "repositoryMutationRequired": True,
                "omnigent": {
                    "executionTargetRef": "omnigent-codex@1",
                    "launchPolicyRef": (
                        "codex-on-demand@1" if on_demand else "codex-static@1"
                    ),
                },
            },
        }
    )

    events: list[str] = []
    artifacts = _Artifacts()
    runtime = _Runtime(workspace_root=workspace_root, events=events)
    hosts = _Hosts(on_demand=on_demand, events=events)
    store = _Store(events)
    released: list[str] = []

    class _Leases:
        async def acquire_execution_lease(self, **_kwargs):
            attempt = len(released) + 1
            return CredentialLease(
                profile_id="codex",
                runtime_id="codex_cli",
                lease_id=f"provider-lease-{attempt}",
                owner_id="owner-1",
                purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
            )

        async def release_lease(self, lease):
            released.append(lease.lease_id)
            events.append("provider_released")

        async def record_cooldown(self, **_kwargs):
            raise AssertionError("normal journey must not enter cooldown")

    execution_requests = []

    async def execute(bound_request, **_kwargs):
        execution_requests.append(bound_request)
        workspace = workspace_root / workspace_id / "repo"
        assert _git("branch", "--show-current", cwd=workspace) == "issue-3507"
        evidence = json.loads(
            (workspace.parent / ".moonmind-workspace.json").read_text(
                encoding="utf-8"
            )
        )
        inputs = {
            item["artifactRef"]: workspace / item["localPath"]
            for item in evidence["materializedInputRefs"]
        }
        assert inputs["artifact:attachment-1"].read_text(
            encoding="utf-8"
        ) == "payload:attachment-1"
        assert inputs["checkpoint:restore-1"].read_text(
            encoding="utf-8"
        ) == "payload:restore-1"
        (workspace / "result.txt").write_text("saved work\n", encoding="utf-8")
        return AgentRunResult(
            summary="done",
            outputRefs=["artifact:declared-output"],
        )

    coordinator = OmnigentProfileBoundExecutionCoordinator(
        session_factory=lambda: None,
        lease_client=_Leases(),
        host_repository=hosts,
        host_runtime=runtime,
        run_store=store,
        execution_runner=execute,
        artifact_gateway=artifacts,
        artifact_service=artifacts,
    )

    async def resolve_profile(_profile_id):
        return SimpleNamespace(
            enabled=True,
            auth_state=ProviderProfileAuthState.CONNECTED,
            disabled_reason=None,
            max_parallel_runs=1,
            cooldown_after_429_seconds=900,
            runtime_id="codex_cli",
            credential_source=ProviderCredentialSource.OAUTH_VOLUME,
            runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
            volume_ref="codex_auth_volume",
            volume_mount_path="/home/app/.codex",
            secret_refs={},
            command_behavior={},
        )

    coordinator._resolve_profile = resolve_profile  # type: ignore[method-assign]

    execution_result = await coordinator.execute(request)
    await coordinator.execute(request)
    workspace = workspace_root / workspace_id / "repo"
    run_record = SimpleNamespace(
        run_id=step_id,
        agent_id="codex",
        runtime_id="omnigent",
        status="completed",
        workspace_path=str(workspace),
        stdout_artifact_ref=None,
        stderr_artifact_ref=None,
        merged_log_artifact_ref=None,
        diagnostics_ref=None,
    )
    publication_store = SimpleNamespace(load=lambda _run_id: run_record)
    publication = TemporalAgentRuntimeActivities(run_store=publication_store)
    fetch_request = {
        "runId": step_id,
        "agentId": "omnigent",
        "publishMode": request.parameters["publishMode"],
        "targetBranch": request.parameters["publishBaseBranch"],
        "headBranch": request.workspace_spec["targetBranch"],
        "commitMessage": request.parameters["commitMessage"],
    }
    with (
        patch(
            "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter"
        ) as adapter,
        patch.object(
            publication,
            "_resolve_workspace_push_github_token",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch.object(publication, "_detect_pr_url_from_workspace", return_value=None),
    ):
        adapter.return_value.fetch_result = AsyncMock(return_value=execution_result)
        first = await publication.agent_runtime_fetch_result(fetch_request)
        first_head = _git("rev-parse", "HEAD", cwd=workspace)
        second = await publication.agent_runtime_fetch_result(fetch_request)
        second_head = _git("rev-parse", "HEAD", cwd=workspace)

    assert runtime.prepared_modes == [
        "on_demand_docker" if on_demand else "static_compose"
    ] * 2
    assert first.output_refs == second.output_refs == ["artifact:declared-output"]
    assert first.metadata["push_status"] == second.metadata["push_status"] == "pushed"
    assert first.metadata["push_branch"] == second.metadata["push_branch"] == "issue-3507"
    assert first.metadata["push_base_branch"] == "main"
    assert first.metadata["remote_verified"] is True
    assert first.metadata["acceptedRepositoryEvidence"]["branch"] == "issue-3507"
    assert first.metadata["acceptedRepositoryEvidence"]["headSha"] == first_head
    assert first_head == second_head
    assert _git("log", "-1", "--format=%s", cwd=workspace) == "Complete #3507"
    assert _git("rev-parse", "refs/heads/issue-3507", cwd=source) == first_head
    assert _git("rev-list", "--count", "main..issue-3507", cwd=source) == "1"
    assert (workspace / "result.txt").read_text(encoding="utf-8") == "saved work\n"
    assert request.workspace_spec["repository"] == authored["repository"]
    assert request.workspace_spec["startingBranch"] == authored["startingBranch"]
    assert request.workspace_spec["targetBranch"] == authored["targetBranch"]
    assert request.parameters["publishMode"] == authored["publishMode"]
    assert request.parameters["publishBaseBranch"] == authored["publishBaseBranch"]
    assert request.parameters["commitMessage"] == authored["commitMessage"]
    assert execution_requests[0].resolved_skillset_ref == "artifact:skills-1"
    assert artifacts.reads == [
        "attachment-1",
        "restore-1",
        "attachment-1",
        "restore-1",
    ]
    assert events.count("host_cleanup") == 2
    assert events.count("host_stopped") == 2
    assert events.count("provider_released") == 2
    attempts: list[list[str]] = []
    start = 0
    for terminal_index in (
        index for index, event in enumerate(events) if event == "terminal"
    ):
        attempts.append(events[start : terminal_index + 1])
        start = terminal_index + 1
    assert len(attempts) == 2
    for attempt_events in attempts:
        assert attempt_events[-1] == "terminal"
        assert (
            attempt_events.index("host_stopped")
            < attempt_events.index("provider_released")
            < len(attempt_events) - 1
        )
        assert attempt_events[-2] == "profile_lease_release"
    terminal_events = [item for item in store.lifecycle if item[0] == "terminal"]
    assert len(terminal_events) == 2
    assert all(
        metadata == {
            "workflowId": "workflow-3507",
            "stepExecutionId": step_id,
            "cleanupCompleted": True,
            "leaseReleased": True,
            "janitorRequired": False,
        }
        for _, status, metadata in terminal_events
        if status == "completed"
    )
