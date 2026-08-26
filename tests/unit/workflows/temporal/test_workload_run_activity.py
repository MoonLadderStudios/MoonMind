from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from temporalio import exceptions as temporal_exceptions

from moonmind.schemas.workload_models import RunnerProfile, WorkloadResult
from moonmind.security.egress_conformance_evidence import (
    parse_and_verify_conformance_evidence,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalActivityRuntimeError,
    TemporalAgentRuntimeActivities,
    _default_skill_registry_payload,
)
from moonmind.workloads.docker_launcher import DockerWorkloadLauncher
from moonmind.workloads.registry import RunnerProfileRegistry

WORKSPACE_ROOT = Path("/work/agent_jobs")

def _profile_payload() -> dict[str, object]:
    return {
        "id": "local-python",
        "kind": "one_shot",
        "image": "python:3.12-slim",
        "workdir_template": "/work/agent_jobs/${agent_run_id}/repo",
        "required_mounts": [
            {
                "type": "volume",
                "source": "agent_workspaces",
                "target": "/work/agent_jobs",
            }
        ],
        "env_allowlist": ["CI"],
        "network_policy": "none",
        "timeout_seconds": 300,
        "max_timeout_seconds": 600,
    }

def _helper_profile_payload() -> dict[str, object]:
    return {
        "id": "redis-helper",
        "kind": "bounded_service",
        "image": "redis:7.2-alpine",
        "workdir_template": "/work/agent_jobs/${agent_run_id}/repo",
        "required_mounts": [
            {
                "type": "volume",
                "source": "agent_workspaces",
                "target": "/work/agent_jobs",
            }
        ],
        "env_allowlist": ["CI"],
        "network_policy": "restricted_egress",
        "timeout_seconds": 60,
        "max_timeout_seconds": 120,
        "helper_ttl_seconds": 300,
        "max_helper_ttl_seconds": 900,
        "readiness_probe": {
            "type": "exec",
            "command": ["redis-cli", "ping"],
            "interval_seconds": 0,
            "timeout_seconds": 1,
            "retries": 3,
        },
        "cleanup": {
            "remove_container_on_exit": True,
            "kill_grace_seconds": 3,
        },
    }

def _request_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "profileId": "local-python",
        "agentRunId": "task-1",
        "stepId": "step-test",
        "attempt": 1,
        "toolName": "container.run_workload",
        "repoDir": "/work/agent_jobs/task-1/repo",
        "artifactsDir": "/work/agent_jobs/task-1/artifacts/step-test",
        "command": ["python", "-V"],
        "envOverrides": {"CI": "1"},
    }
    payload.update(overrides)
    return payload

class _FakeLauncher:
    def __init__(self) -> None:
        self.validated: Any | None = None
        self.reason: str | None = None

    @staticmethod
    def _session_context(validated: Any) -> dict[str, object] | None:
        if validated.request.session_id is None:
            return None
        context: dict[str, object] = {"sessionId": validated.request.session_id}
        if validated.request.session_epoch is not None:
            context["sessionEpoch"] = validated.request.session_epoch
        if validated.request.source_turn_id is not None:
            context["sourceTurnId"] = validated.request.source_turn_id
        return context

    async def run(self, validated: Any) -> WorkloadResult:
        self.validated = validated
        return WorkloadResult(
            requestId=validated.container_name,
            profileId=validated.profile.id,
            status="succeeded",
            labels=validated.ownership.labels,
            exitCode=0,
            metadata={
                "containerName": validated.container_name,
                "workload": {
                    "agentRunId": validated.request.agent_run_id,
                    "stepId": validated.request.step_id,
                    "attempt": validated.request.attempt,
                    "toolName": validated.request.tool_name,
                    "profileId": validated.profile.id,
                    "sessionContext": self._session_context(validated),
                },
                "artifactPublication": {"status": "complete"},
            },
        )

    async def start_helper(self, validated: Any) -> WorkloadResult:
        self.validated = validated
        return WorkloadResult(
            requestId=validated.container_name,
            profileId=validated.profile.id,
            status="ready",
            labels=validated.ownership.labels,
            metadata={
                "helper": {
                    "containerName": validated.container_name,
                    "status": "ready",
                    "readiness": {"status": "ready", "attempts": 1},
                },
            },
        )

    async def stop_helper(
        self,
        validated: Any,
        *,
        reason: str = "bounded_window_complete",
    ) -> WorkloadResult:
        self.validated = validated
        self.reason = reason
        return WorkloadResult(
            requestId=validated.container_name,
            profileId=validated.profile.id,
            status="stopped",
            labels=validated.ownership.labels,
            metadata={
                "helper": {
                    "containerName": validated.container_name,
                    "status": "stopped",
                    "teardown": {"status": "complete", "reason": reason},
                },
            },
        )

class _FailingRegistry:
    def validate_request(self, _request: object) -> object:
        raise AssertionError("registry validation should not run")

class _FailingLauncher:
    async def run(self, _validated: object) -> object:
        raise AssertionError("launcher should not run")

    async def start_helper(self, _validated: object) -> object:
        raise AssertionError("launcher should not run")

    async def stop_helper(self, _validated: object, *, reason: str) -> object:
        raise AssertionError("launcher should not run")

@pytest.mark.asyncio
async def test_workload_run_activity_validates_request_and_calls_launcher() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_profile_payload())],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _FakeLauncher()
    activities = TemporalAgentRuntimeActivities(
        workload_registry=registry,
        workload_launcher=launcher,
    )

    result = await activities.workload_run({"request": _request_payload()})

    assert launcher.validated is not None
    assert launcher.validated.container_name == "mm-workload-task-1-step-test-1"
    assert result["requestId"] == "mm-workload-task-1-step-test-1"
    assert result["profileId"] == "local-python"
    assert result["status"] == "succeeded"
    assert result["labels"]["moonmind.kind"] == "workload"

@pytest.mark.asyncio
async def test_workload_run_activity_preserves_session_context_as_workload_metadata() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_profile_payload())],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _FakeLauncher()
    activities = TemporalAgentRuntimeActivities(
        workload_registry=registry,
        workload_launcher=launcher,
    )

    result = await activities.workload_run(
        {
            "request": _request_payload(
                sessionId="session-1",
                sessionEpoch=3,
                sourceTurnId="turn-7",
            )
        }
    )

    assert result["metadata"]["workload"]["sessionContext"] == {
        "sessionId": "session-1",
        "sessionEpoch": 3,
        "sourceTurnId": "turn-7",
    }
    assert "session.summary" not in (result.get("outputRefs") or {})

@pytest.mark.asyncio
async def test_workload_run_activity_starts_helper_by_tool_name() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_helper_profile_payload())],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _FakeLauncher()
    activities = TemporalAgentRuntimeActivities(
        workload_registry=registry,
        workload_launcher=launcher,
    )

    result = await activities.workload_run(
        {
            "request": _request_payload(
                profileId="redis-helper",
                toolName="container.start_helper",
                repoDir="/work/agent_jobs/task-1/repo",
                artifactsDir="/work/agent_jobs/task-1/artifacts/step-service",
                command=["--appendonly", "no"],
                ttlSeconds=300,
            )
        }
    )

    assert launcher.validated is not None
    assert launcher.validated.container_name == "mm-helper-task-1-step-test-1"
    assert result["status"] == "ready"
    assert result["labels"]["moonmind.kind"] == "bounded_service"
    assert result["metadata"]["helper"]["readiness"]["status"] == "ready"

@pytest.mark.asyncio
async def test_workload_run_activity_stops_helper_by_tool_name() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_helper_profile_payload())],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _FakeLauncher()
    activities = TemporalAgentRuntimeActivities(
        workload_registry=registry,
        workload_launcher=launcher,
    )

    result = await activities.workload_run(
        {
            "request": _request_payload(
                profileId="redis-helper",
                toolName="container.stop_helper",
                repoDir="/work/agent_jobs/task-1/repo",
                artifactsDir="/work/agent_jobs/task-1/artifacts/step-service",
                command=["stop"],
                ttlSeconds=300,
                reason="owner_task_canceled",
            )
        }
    )

    assert launcher.validated is not None
    assert launcher.reason == "owner_task_canceled"
    assert result["status"] == "stopped"
    assert result["labels"]["moonmind.kind"] == "bounded_service"
    assert result["metadata"]["helper"]["teardown"]["reason"] == "owner_task_canceled"


@pytest.mark.asyncio
async def test_workload_activity_owner_reconciles_durable_helper_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cross the real Activity and launcher owners on start and cleanup."""

    # Reuse the daemon fixture exercised by the launcher suite. Only the Docker
    # process boundary is simulated; request validation, launch attestation,
    # authority persistence, Activity routing, cleanup verification, and lease
    # release all execute their production implementations here.
    from tests.unit.workloads.test_docker_workload_launcher import (
        _healthy_egress_run_process,
    )

    workspace_root = tmp_path / "agent-workspaces"
    repository = workspace_root / "task-1" / "repo"
    artifacts = workspace_root / "task-1" / "artifacts" / "step-service"
    repository.mkdir(parents=True)
    artifacts.mkdir(parents=True)
    profile_payload = _helper_profile_payload()
    profile_payload["workdir_template"] = str(
        workspace_root / "${agent_run_id}" / "repo"
    )
    profile_payload["required_mounts"] = [
        {
            "type": "volume",
            "source": "agent_workspaces",
            "target": str(workspace_root),
        }
    ]
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(profile_payload)],
        workspace_root=workspace_root,
    )
    launcher = DockerWorkloadLauncher()

    async def fake_subprocess(*args: str, **_kwargs: Any):
        return _healthy_egress_run_process(list(args))

    async def fake_control(args):
        process = _healthy_egress_run_process(["docker", *args])
        stdout, stderr = await process.communicate()
        return stdout, stderr, int(process.returncode or 0)

    monkeypatch.setattr(
        "moonmind.workloads.docker_launcher.asyncio.create_subprocess_exec",
        fake_subprocess,
    )
    monkeypatch.setattr(launcher._janitor, "_run_control", fake_control)
    activities = TemporalAgentRuntimeActivities(
        workload_registry=registry,
        workload_launcher=launcher,
    )
    start_request = _request_payload(
        profileId="redis-helper",
        toolName="container.start_helper",
        repoDir=str(repository),
        artifactsDir=str(artifacts),
        command=["--appendonly", "no"],
        ttlSeconds=300,
    )

    started = await activities.workload_run({"request": start_request})
    stopped = await activities.workload_run(
        {
            "request": {
                **start_request,
                "toolName": "container.stop_helper",
                "command": ["stop"],
                "reason": "durable_activity_reconciliation",
            }
        }
    )

    attached = parse_and_verify_conformance_evidence(
        Path(started["outputRefs"]["security.egress.authority"]).read_bytes(),
        location="activity-helper-attached-authority",
    )
    terminal = parse_and_verify_conformance_evidence(
        Path(stopped["outputRefs"]["security.egress.authority"]).read_bytes(),
        location="activity-helper-terminal-authority",
    )
    reconciliation = parse_and_verify_conformance_evidence(
        (
            artifacts
            / "workload"
            / started["requestId"]
            / "egress-helper-authority-cleanup_validated.json"
        ).read_bytes(),
        location="activity-helper-reconciliation-authority",
    )
    assert attached["state"] == "attached"
    assert attached["leaseAuthority"]["state"] == "held"
    assert terminal["state"] == "stopped"
    assert terminal["leaseAuthority"]["state"] == "released"
    assert terminal["cleanupResult"] == "succeeded"
    assert reconciliation["leaseAuthority"]["state"] == "held"
    assert reconciliation["reconciliationOwner"]["toolName"] == (
        "container.stop_helper"
    )


@pytest.mark.asyncio
async def test_workload_run_activity_requires_runtime_dependencies() -> None:
    activities = TemporalAgentRuntimeActivities()

    with pytest.raises(TemporalActivityRuntimeError, match="workload registry"):
        await activities.workload_run(_request_payload())

@pytest.mark.asyncio
async def test_workload_run_activity_denies_when_workflow_docker_disabled() -> None:
    activities = TemporalAgentRuntimeActivities(
        workload_registry=_FailingRegistry(),
        workload_launcher=_FailingLauncher(),
        workflow_docker_mode="disabled",
    )

    with pytest.raises(temporal_exceptions.ApplicationError) as exc_info:
        await activities.workload_run({"request": _request_payload()})

    message = str(exc_info.value)
    assert "docker_workflows_disabled" in message
    assert "policy_denied" in message
    assert exc_info.value.type == "docker_workflows_disabled"
    assert exc_info.value.non_retryable is True

@pytest.mark.asyncio
async def test_workload_run_activity_denies_unrestricted_tool_when_mode_is_profiles() -> None:
    activities = TemporalAgentRuntimeActivities(
        workload_registry=_FailingRegistry(),
        workload_launcher=_FailingLauncher(),
        workflow_docker_mode="profiles",
    )

    with pytest.raises(temporal_exceptions.ApplicationError) as exc_info:
        await activities.workload_run(
            {
                "request": {
                    "agentRunId": "task-1",
                    "stepId": "step-test",
                    "attempt": 1,
                    "toolName": "container.run_docker",
                    "repoDir": "/work/agent_jobs/task-1/repo",
                    "artifactsDir": "/work/agent_jobs/task-1/artifacts/step-test",
                    "command": ["docker", "ps"],
                }
            }
        )

    assert exc_info.value.type == "docker_workflow_mode_forbidden"
    assert "profiles" in str(exc_info.value)


@pytest.mark.asyncio
async def test_workload_run_activity_rejects_raw_docker_even_when_legacy_flag_enabled() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_profile_payload())],
        workspace_root=WORKSPACE_ROOT,
    )
    validated = registry.validate_request(
        _request_payload(), workflow_docker_mode="unrestricted"
    )
    capturing_registry = _CapturingRegistry(validated)
    launcher = _FakeLauncher()
    activities = TemporalAgentRuntimeActivities(
        workload_registry=capturing_registry,
        workload_launcher=launcher,
        workflow_docker_mode="unrestricted",
        raw_docker_cli_enabled=True,
    )
    request = _request_payload(toolName="container.run_docker")
    request.pop("profileId")
    request["command"] = ["docker", "ps"]
    with pytest.raises(temporal_exceptions.ApplicationError) as exc_info:
        await activities.workload_run({"request": request})
    assert exc_info.value.type == "docker_workflow_mode_forbidden"
    assert capturing_registry.calls == []
    assert launcher.validated is None

class _CapturingRegistry:
    def __init__(self, validated: object) -> None:
        self.validated = validated
        self.calls: list[tuple[object, str | None]] = []

    def validate_request(
        self,
        request: object,
        *,
        workflow_docker_mode: str | None = None,
    ) -> object:
        self.calls.append((request, workflow_docker_mode))
        return self.validated

@pytest.mark.asyncio
async def test_workload_run_activity_passes_active_workflow_mode_to_registry() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_profile_payload())],
        workspace_root=WORKSPACE_ROOT,
    )
    validated = registry.validate_request(_request_payload(), workflow_docker_mode="unrestricted")
    capturing_registry = _CapturingRegistry(validated)
    launcher = _FakeLauncher()
    activities = TemporalAgentRuntimeActivities(
        workload_registry=capturing_registry,
        workload_launcher=launcher,
        workflow_docker_mode="unrestricted",
    )

    result = await activities.workload_run({"request": _request_payload()})

    assert capturing_registry.calls == [(validated.request, "unrestricted")]
    assert launcher.validated is validated
    assert result["labels"]["moonmind.workflow_docker_mode"] == "unrestricted"


def _unrestricted_gpu_request_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "agentRunId": "task-gpu",
        "stepId": "step-render",
        "attempt": 1,
        "toolName": "container.run_container",
        "repoDir": "/work/agent_jobs/task-gpu/repo",
        "artifactsDir": "/work/agent_jobs/task-gpu/artifacts/step-render",
        "scratchDir": "/work/agent_jobs/task-gpu/scratch/step-render",
        "image": "ghcr.io/example/caller-owned:1.4.2",
        "command": ["caller-owned-doctor", "--json"],
        "resources": {"gpu": {"vendor": "nvidia", "count": "all"}},
    }
    payload.update(overrides)
    return payload


class _ArgCapturingLauncher:
    """Trusted-worker double that runs the real Docker argument construction."""

    def __init__(self) -> None:
        self._launcher = DockerWorkloadLauncher()
        self.validated: Any | None = None
        self.run_args: list[str] | None = None

    async def run(self, validated: Any) -> WorkloadResult:
        self.validated = validated
        self.run_args = self._launcher.build_run_args(validated)
        return WorkloadResult(
            requestId=validated.container_name,
            profileId=validated.request.tool_name,
            status="succeeded",
            labels=validated.ownership.labels,
            exitCode=0,
            metadata={"containerName": validated.container_name},
        )


@pytest.mark.asyncio
async def test_workload_run_activity_carries_generic_gpu_resources_to_docker() -> None:
    """GPU fields survive the activity payload, validation, and trusted launch."""

    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_profile_payload())],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _ArgCapturingLauncher()
    activities = TemporalAgentRuntimeActivities(
        workload_registry=registry,
        workload_launcher=launcher,
        workflow_docker_mode="unrestricted",
    )

    result = await activities.workload_run(
        {"request": _unrestricted_gpu_request_payload()}
    )

    assert launcher.validated is not None
    # No runner profile and therefore no profile device policy is involved.
    assert launcher.validated.profile is None
    gpu = launcher.validated.request.resources.gpu
    assert (gpu.vendor, gpu.count, gpu.contract_version) == ("nvidia", "all", "v1")
    assert launcher.run_args is not None
    assert "--gpus" in launcher.run_args
    assert launcher.run_args[launcher.run_args.index("--gpus") + 1] == "all"
    # The caller owns the image and command through the whole dispatch.
    assert launcher.run_args[-3:] == [
        "ghcr.io/example/caller-owned:1.4.2",
        "caller-owned-doctor",
        "--json",
    ]
    assert result["status"] == "succeeded"
    assert result["labels"]["moonmind.workload_access"] == "unrestricted_container"
    assert result["labels"]["moonmind.workflow_docker_mode"] == "unrestricted"


@pytest.mark.asyncio
async def test_workload_run_activity_retry_reuses_the_run_owned_gpu_container() -> None:
    """A retry attempt keeps the deterministic run-owned container identity."""

    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_profile_payload())],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _ArgCapturingLauncher()
    activities = TemporalAgentRuntimeActivities(
        workload_registry=registry,
        workload_launcher=launcher,
        workflow_docker_mode="unrestricted",
    )
    payload = {"request": _unrestricted_gpu_request_payload(attempt=2)}

    first = await activities.workload_run(payload)
    second = await activities.workload_run(payload)

    assert first["requestId"] == second["requestId"]
    assert first["requestId"] == "mm-workload-task-gpu-step-render-2"
    assert launcher.run_args is not None
    assert launcher.run_args[launcher.run_args.index("--name") + 1] == first["requestId"]


@pytest.mark.asyncio
async def test_workload_run_activity_denies_gpu_container_when_mode_is_profiles() -> None:
    """A GPU request does not bypass the deployment-owned Docker mode switch."""

    activities = TemporalAgentRuntimeActivities(
        workload_registry=_FailingRegistry(),
        workload_launcher=_FailingLauncher(),
        workflow_docker_mode="profiles",
    )

    with pytest.raises(temporal_exceptions.ApplicationError) as exc_info:
        await activities.workload_run(
            {"request": _unrestricted_gpu_request_payload()}
        )

    assert exc_info.value.type == "docker_workflow_mode_forbidden"


def test_unrestricted_gpu_container_has_no_new_plan_dispatch_route() -> None:
    """A plan step cannot reach the GPU-capable unrestricted container request.

    docs/Workflows/GpuContainerResourcesContract.md section 3.1 states that
    ``container.run_container`` is absent from executable tool discovery and
    new dispatch, and that ``resources.gpu`` therefore has no live caller route
    until MoonLadderStudios/MoonMind#3779. Pin that at the production
    registry-snapshot builder: naming the legacy tool must yield the generic
    runtime CLI handler rather than a ``workload.run`` container launch, and the
    one discoverable container tool must expose no GPU field.
    """

    payload = _default_skill_registry_payload(
        parameters={
            "workflow": {
                "steps": [
                    {"tool": {"name": "container.run_container"}},
                    {"tool": {"name": "container.run_job"}},
                ]
            }
        }
    )
    definitions = {entry["name"]: entry for entry in payload["skills"]}
    assert set(definitions) == {"container.run_container", "container.run_job"}

    unrestricted = definitions["container.run_container"]
    assert unrestricted["executor"]["activity_type"] == "mm.tool.execute"
    assert unrestricted["requirements"]["capabilities"] == ["sandbox"]
    assert "workload.run" not in str(unrestricted)
    assert set(unrestricted["inputs"]["schema"]["properties"]) == {
        "instructions",
        "runtime",
    }

    canonical_spec = definitions["container.run_job"]["inputs"]["schema"][
        "properties"
    ]["spec"]
    assert "gpu" not in canonical_spec["properties"]["resources"]["properties"]
