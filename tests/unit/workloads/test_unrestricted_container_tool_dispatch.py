"""The caller route for generic NVIDIA GPU container requests.

Source: MoonLadderStudios/MoonMind#3775, epic #3774 ("Immediate unrestricted
path"). The issue's Objective is that *any repository-owned workflow or skill*
can submit an ordinary unrestricted container request carrying GPU resources.
These tests pin that route at the production boundaries it crosses:

* executable-tool discovery (``_default_skill_registry_payload``), gated by the
  deployment-owned ``MOONMIND_WORKFLOW_DOCKER_MODE``;
* plan validation against the pinned registry snapshot;
* the parent workflow's injected run authority
  (``MoonMindRunWorkflow._container_execution_context``);
* MoonMind-owned workspace resolution against a real ``ManagedRunStore``;
* ``mm.tool.execute`` dispatch through the real ``ToolActivityDispatcher`` into
  the real ``RunnerProfileRegistry`` and ``DockerWorkloadLauncher``.

No test-only request model, dictionary stand-in, or hard-coded identity is
substituted for those boundaries; only the Docker CLI subprocess is faked.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

import pytest

from moonmind.schemas.agent_runtime_models import ManagedRunRecord
from moonmind.schemas.workspace_locator_models import (
    WorkspaceLocatorResolutionError,
)
from moonmind.workflows.skills.plan_validation import (
    PlanValidationError,
    validate_plan_payload,
)
from moonmind.workflows.skills.tool_dispatcher import (
    ToolActivityDispatcher,
    execute_tool_activity,
)
from moonmind.workflows.skills.tool_plan_contracts import ToolFailure, ToolResult
from moonmind.workflows.temporal.activity_catalog import (
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import TemporalSkillActivities
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.runtime.workspace_locators import (
    resolve_unrestricted_container_workspace,
)
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
from moonmind.workloads.docker_launcher import DockerWorkloadLauncher
from moonmind.workloads.registry import RunnerProfileRegistry
from moonmind.workloads.unrestricted_container_tool import (
    CONTAINER_EXECUTION_CONTEXT_KEY,
    CONTAINER_RUN_CONTAINER_TOOL,
    build_unrestricted_container_tool_definition_payload,
    register_unrestricted_container_tool_handler,
)

CALLER_IMAGE = "ghcr.io/example/caller-owned:1.4.2"
CALLER_COMMAND = ("caller-owned-doctor", "--report", "gpu.json")
CALLER_INPUTS: dict[str, Any] = {
    "image": CALLER_IMAGE,
    "command": list(CALLER_COMMAND),
    "resources": {"gpu": {"vendor": "nvidia", "count": "all"}},
}
RUNTIME_ID = "claude_code"
AGENT_RUN_ID = "wf-gpu-run"
STEP_ID = "render"


# --------------------------------------------------------------------------- #
# Production discovery helpers, re-imported per Docker mode
# --------------------------------------------------------------------------- #


def _activity_runtime_for_mode(monkeypatch: pytest.MonkeyPatch, mode: str):
    """Return the production discovery module bound to a deployment mode.

    ``_default_skill_registry_payload`` reads the deployment-owned setting, so
    the test drives the real setting rather than a parallel switch.
    """

    from moonmind.workflows.temporal import activity_runtime

    monkeypatch.setattr(
        activity_runtime.settings.workflow,
        "workflow_docker_mode",
        mode,
        raising=False,
    )
    return activity_runtime


def _registry_snapshot(monkeypatch: pytest.MonkeyPatch, mode: str = "unrestricted"):
    activity_runtime = _activity_runtime_for_mode(monkeypatch, mode)
    payload = activity_runtime._default_skill_registry_payload(
        parameters={
            "workflow": {"steps": [{"tool": {"name": CONTAINER_RUN_CONTAINER_TOOL}}]}
        }
    )
    return activity_runtime._temporal_snapshot_from_payload(
        payload, artifact_locator="artifact://art_registry_snapshot"
    )


def _plan_payload(snapshot, inputs: dict[str, Any]) -> dict[str, Any]:
    return {
        "plan_version": "1.0",
        "metadata": {
            "title": "generic gpu container",
            "created_at": "2026-08-26T00:00:00Z",
            "registry_snapshot": {
                "digest": snapshot.digest,
                "artifact_ref": snapshot.artifact_ref,
            },
        },
        "policy": {},
        "nodes": [
            {
                "id": STEP_ID,
                "tool": {"type": "skill", "name": CONTAINER_RUN_CONTAINER_TOOL},
                "inputs": inputs,
            }
        ],
        "edges": [],
    }


# --------------------------------------------------------------------------- #
# Real workspace, registry, and Docker CLI double
# --------------------------------------------------------------------------- #


def _managed_run_store(tmp_path: Path) -> tuple[ManagedRunStore, Path]:
    """Build a real managed run store over a real workspace layout."""

    runtime_root = tmp_path / "agent_jobs"
    repo_dir = runtime_root / "workspaces" / AGENT_RUN_ID / "repo"
    repo_dir.mkdir(parents=True)
    store = ManagedRunStore(runtime_root / "managed_runs")
    store.save(
        ManagedRunRecord(
            runId=AGENT_RUN_ID,
            agentId="claude",
            runtimeId=RUNTIME_ID,
            status="running",
            startedAt=datetime.now(UTC),
            workspacePath=str(repo_dir),
        )
    )
    return store, runtime_root


def _profile_registry(tmp_path: Path, workspace_root: Path) -> RunnerProfileRegistry:
    registry_path = tmp_path / "profiles.json"
    registry_path.write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "id": "local-python",
                        "kind": "one_shot",
                        "image": "python:3.12-slim",
                        "workdirTemplate": (
                            f"{workspace_root}/workspaces/${{agent_run_id}}/repo"
                        ),
                        "requiredMounts": [
                            {
                                "type": "volume",
                                "source": "agent_workspaces",
                                "target": str(workspace_root),
                            }
                        ],
                        "envAllowlist": ["CI"],
                        "networkPolicy": "none",
                        "timeoutSeconds": 300,
                        "devicePolicy": {"mode": "none"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return RunnerProfileRegistry.load_file(
        registry_path, workspace_root=workspace_root
    )


class _Pipe:
    def __init__(self, data: bytes, closed: asyncio.Event) -> None:
        self._data = bytearray(data)
        self._closed = closed

    async def read(self, size: int = -1) -> bytes:
        if not self._data:
            await self._closed.wait()
            return b""
        if size is None or size < 0:
            size = len(self._data)
        chunk = bytes(self._data[:size])
        del self._data[:size]
        return chunk


class _Process:
    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: bytes = b"",
        never_complete: bool = False,
    ) -> None:
        self.returncode = None if never_complete else returncode
        self.terminated = False
        self.closed = asyncio.Event()
        self._stdout = stdout
        self._stderr = b""
        if not never_complete:
            self.closed.set()
        self.stdout = _Pipe(stdout, self.closed)
        self.stderr = _Pipe(b"", self.closed)

    async def communicate(self) -> tuple[bytes, bytes]:
        if self.returncode is None:
            await self.closed.wait()
        return self._stdout, self._stderr

    async def wait(self) -> int:
        if self.returncode is None:
            await self.closed.wait()
        return int(self.returncode or 0)

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15
        self.closed.set()

    def kill(self) -> None:
        self.returncode = -9
        self.closed.set()


def _fake_docker(
    monkeypatch: pytest.MonkeyPatch,
    created: list[list[str]],
    *,
    run_exit_code: int = 0,
    run_stdout: bytes = b"",
    container_writes: Mapping[str, bytes] | None = None,
    run_never_completes: bool = False,
) -> list[_Process]:
    """Stand in for the Docker CLI, optionally acting as the caller's image.

    ``container_writes`` lets the fake container write files under the artifacts
    directory MoonMind bound into it, so declared-output collection is exercised
    against paths the production launcher chose rather than test constants.
    """

    run_processes: list[_Process] = []

    async def _create(*args: str, **_kwargs: Any) -> _Process:
        created.append(list(args))
        if len(args) > 1 and args[1] == "run":
            if container_writes:
                artifacts_dir = _bound_artifacts_dir(list(args))
                for relative, payload in container_writes.items():
                    target = artifacts_dir / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(payload)
            process = _Process(
                returncode=run_exit_code,
                stdout=run_stdout,
                never_complete=run_never_completes,
            )
            run_processes.append(process)
            return process
        return _Process(returncode=0)

    monkeypatch.setattr(
        "moonmind.workloads.docker_launcher.asyncio.create_subprocess_exec",
        _create,
    )
    return run_processes


def _bound_artifacts_dir(run_args: list[str]) -> Path:
    sources = [
        item.split("source=", 1)[1].split(",", 1)[0]
        for index, item in enumerate(run_args)
        if index and run_args[index - 1] == "--mount" and "type=bind" in item
    ]
    artifacts = [
        source for source in sources if Path(source).parent.name == "artifacts"
    ]
    assert artifacts, run_args
    return Path(artifacts[0])


def _dispatcher(
    *,
    registry: RunnerProfileRegistry,
    store: ManagedRunStore,
    workflow_docker_mode: str = "unrestricted",
) -> ToolActivityDispatcher:
    dispatcher = ToolActivityDispatcher()
    register_unrestricted_container_tool_handler(
        dispatcher,
        registry=registry,
        launcher=DockerWorkloadLauncher(),
        workspace_resolver=lambda execution: (
            resolve_unrestricted_container_workspace(execution, store=store)
        ),
        workflow_docker_mode=workflow_docker_mode,
    )
    return dispatcher


def _invocation_payload(inputs: dict[str, Any]) -> dict[str, Any]:
    """The exact shape ``MoonMind.UserWorkflow`` sends to ``mm.tool.execute``."""

    return {
        "id": STEP_ID,
        "tool": {"type": "skill", "name": CONTAINER_RUN_CONTAINER_TOOL},
        "skill": {"name": CONTAINER_RUN_CONTAINER_TOOL},
        "inputs": inputs,
        "options": {},
    }


def _workflow_context(*, execution_ordinal: int = 1) -> dict[str, Any]:
    """The context the run workflow builds, via the production helper."""

    workflow = MoonMindRunWorkflow()
    workflow._target_runtime = "claude"
    return {
        "namespace": "default",
        "workflow_id": AGENT_RUN_ID,
        "run_id": "temporal-run-1",
        "node_id": STEP_ID,
        **workflow._container_execution_context(
            tool_name=CONTAINER_RUN_CONTAINER_TOOL,
            workflow_id=AGENT_RUN_ID,
            node_id=STEP_ID,
            execution_ordinal=execution_ordinal,
        ),
    }


# --------------------------------------------------------------------------- #
# 1. Discovery
# --------------------------------------------------------------------------- #


def test_unrestricted_mode_publishes_the_container_tool_to_plan_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plan step can name the tool and reach the Docker-capable fleet."""

    activity_runtime = _activity_runtime_for_mode(monkeypatch, "unrestricted")
    payload = activity_runtime._default_skill_registry_payload(
        parameters={
            "workflow": {
                "steps": [
                    {"tool": {"name": CONTAINER_RUN_CONTAINER_TOOL}},
                    {"tool": {"name": "container.run_job"}},
                ]
            }
        }
    )
    definitions = {entry["name"]: entry for entry in payload["skills"]}

    unrestricted = definitions[CONTAINER_RUN_CONTAINER_TOOL]
    assert unrestricted["executor"]["activity_type"] == "mm.tool.execute"
    assert unrestricted["requirements"]["capabilities"] == ["docker_workload"]
    gpu_schema = unrestricted["inputs"]["schema"]["properties"]["resources"][
        "properties"
    ]["gpu"]
    assert gpu_schema["properties"]["vendor"] == {"enum": ["nvidia"]}

    # The canonical container-job contract is untouched; carrying the same
    # request into it is MoonLadderStudios/MoonMind#3779.
    canonical = definitions["container.run_job"]["inputs"]["schema"]["properties"][
        "spec"
    ]
    assert "gpu" not in canonical["properties"]["resources"]["properties"]


@pytest.mark.parametrize("mode", ["profiles", "disabled"])
def test_other_docker_modes_do_not_publish_the_container_tool(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    """Discovery never advertises a surface dispatch would refuse."""

    activity_runtime = _activity_runtime_for_mode(monkeypatch, mode)
    payload = activity_runtime._default_skill_registry_payload(
        parameters={
            "workflow": {"steps": [{"tool": {"name": CONTAINER_RUN_CONTAINER_TOOL}}]}
        }
    )
    definition = payload["skills"][0]

    assert definition["requirements"]["capabilities"] == ["sandbox"]
    assert set(definition["inputs"]["schema"]["properties"]) == {
        "instructions",
        "runtime",
    }


def test_caller_schema_is_closed_against_moonmind_owned_authority() -> None:
    definition = build_unrestricted_container_tool_definition_payload(
        name=CONTAINER_RUN_CONTAINER_TOOL
    )
    schema = definition["inputs"]["schema"]

    assert schema["additionalProperties"] is False
    for forbidden in (
        "repoDir",
        "artifactsDir",
        "scratchDir",
        "dockerHost",
        "privileged",
        "agentRunId",
        "stepId",
        "attempt",
        "toolName",
        "labels",
    ):
        assert forbidden not in json.dumps(schema)


# --------------------------------------------------------------------------- #
# 2. Plan validation against the pinned snapshot
# --------------------------------------------------------------------------- #


def test_plan_validation_accepts_a_generic_gpu_container_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _registry_snapshot(monkeypatch)

    validated = validate_plan_payload(
        payload=_plan_payload(snapshot, CALLER_INPUTS), registry_snapshot=snapshot
    )

    node = validated.plan.nodes[0]
    assert node.skill_name == CONTAINER_RUN_CONTAINER_TOOL
    assert node.inputs["resources"]["gpu"] == {"vendor": "nvidia", "count": "all"}


@pytest.mark.parametrize(
    ("label", "inputs"),
    [
        ("host path", {**CALLER_INPUTS, "repoDir": "/etc"}),
        ("run correlation", {**CALLER_INPUTS, "agentRunId": "someone-else"}),
        (
            "unknown vendor",
            {**CALLER_INPUTS, "resources": {"gpu": {"vendor": "amd"}}},
        ),
        (
            "unknown capability",
            {
                **CALLER_INPUTS,
                "resources": {"gpu": {"vendor": "nvidia", "capabilities": ["render"]}},
            },
        ),
        (
            "raw device authority",
            {
                **CALLER_INPUTS,
                "resources": {
                    "gpu": {"vendor": "nvidia", "devices": ["/dev/nvidia0"]}
                },
            },
        ),
        (
            "unknown contract version",
            {
                **CALLER_INPUTS,
                "resources": {"gpu": {"vendor": "nvidia", "contractVersion": "v2"}},
            },
        ),
    ],
)
def test_plan_validation_rejects_malformed_or_authority_bearing_inputs(
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    inputs: dict[str, Any],
) -> None:
    snapshot = _registry_snapshot(monkeypatch)

    with pytest.raises(PlanValidationError):
        validate_plan_payload(
            payload=_plan_payload(snapshot, inputs), registry_snapshot=snapshot
        )


# --------------------------------------------------------------------------- #
# 3. Workflow-injected run authority
# --------------------------------------------------------------------------- #


def test_run_workflow_injects_the_container_execution_authority() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._target_runtime = "claude"

    context = workflow._container_execution_context(
        tool_name=CONTAINER_RUN_CONTAINER_TOOL,
        workflow_id=AGENT_RUN_ID,
        node_id=STEP_ID,
        execution_ordinal=3,
    )

    assert context[CONTAINER_EXECUTION_CONTEXT_KEY] == {
        "agentRunId": AGENT_RUN_ID,
        "stepId": STEP_ID,
        "attempt": 3,
        "workspaceRef": {
            "kind": "managed_runtime",
            "runtimeId": RUNTIME_ID,
            "agentRunId": AGENT_RUN_ID,
            "relativePath": "repo",
        },
    }
    # Every other tool keeps the unchanged execution context.
    assert (
        workflow._container_execution_context(
            tool_name="fix-ci",
            workflow_id=AGENT_RUN_ID,
            node_id=STEP_ID,
            execution_ordinal=3,
        )
        == {}
    )


@pytest.mark.asyncio
async def test_an_unresolvable_workspace_fails_the_step_not_the_workflow_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workflow code stays total; the trusted Activity owns the refusal.

    Raising in the workflow's step loop would fail the workflow task and wedge
    the run on retry. Instead the locator is omitted, and the Activity refuses
    with a stable error that the normal step-failure path handles.
    """

    workflow = MoonMindRunWorkflow()
    workflow._target_runtime = ""
    context = workflow._container_execution_context(
        tool_name=CONTAINER_RUN_CONTAINER_TOOL,
        workflow_id=AGENT_RUN_ID,
        node_id=STEP_ID,
        execution_ordinal=1,
    )
    assert "workspaceRef" not in context[CONTAINER_EXECUTION_CONTEXT_KEY]

    store, runtime_root = _managed_run_store(tmp_path)
    snapshot = _registry_snapshot(monkeypatch)
    created: list[list[str]] = []
    _fake_docker(monkeypatch, created)

    with pytest.raises(ToolFailure) as exc:
        await execute_tool_activity(
            invocation_payload=_invocation_payload(CALLER_INPUTS),
            registry_snapshot=snapshot,
            dispatcher=_dispatcher(
                registry=_profile_registry(tmp_path, runtime_root), store=store
            ),
            context={"workflow_id": AGENT_RUN_ID, "node_id": STEP_ID, **context},
        )

    assert exc.value.error_code == "INVALID_INPUT"
    assert created == []


# --------------------------------------------------------------------------- #
# 4. MoonMind-owned workspace resolution
# --------------------------------------------------------------------------- #


def test_moonmind_resolves_the_current_authorized_workspace(tmp_path: Path) -> None:
    store, runtime_root = _managed_run_store(tmp_path)
    execution = _workflow_context()[CONTAINER_EXECUTION_CONTEXT_KEY]

    workspace = resolve_unrestricted_container_workspace(execution, store=store)

    run_root = runtime_root / "workspaces" / AGENT_RUN_ID
    assert workspace.repo_dir == str(run_root / "repo")
    assert workspace.artifacts_dir == str(run_root / "artifacts" / STEP_ID)
    assert workspace.scratch_dir == str(run_root / "scratch" / STEP_ID)
    assert Path(workspace.artifacts_dir).is_dir()
    assert Path(workspace.scratch_dir).is_dir()


def test_workspace_resolution_rejects_a_foreign_run_identity(tmp_path: Path) -> None:
    store, _runtime_root = _managed_run_store(tmp_path)
    execution = _workflow_context()[CONTAINER_EXECUTION_CONTEXT_KEY]
    execution = {
        **execution,
        "workspaceRef": {
            **execution["workspaceRef"],
            "agentRunId": "another-run",
        },
    }

    with pytest.raises(WorkspaceLocatorResolutionError) as exc:
        resolve_unrestricted_container_workspace(execution, store=store)

    assert exc.value.code == "WORKSPACE_IDENTITY_MISMATCH"


def test_workspace_resolution_rejects_a_traversing_step_identity(
    tmp_path: Path,
) -> None:
    store, runtime_root = _managed_run_store(tmp_path)
    execution = _workflow_context()[CONTAINER_EXECUTION_CONTEXT_KEY]
    execution = {**execution, "stepId": "../../escape"}

    workspace = resolve_unrestricted_container_workspace(execution, store=store)

    run_root = runtime_root / "workspaces" / AGENT_RUN_ID
    assert Path(workspace.scratch_dir).is_relative_to(run_root)
    assert Path(workspace.artifacts_dir).is_relative_to(run_root)


# --------------------------------------------------------------------------- #
# 5. Dispatch into the trusted Docker construction
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_plan_step_gpu_request_reaches_the_docker_launch_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Objective, end to end: a plan step's resources.gpu becomes --gpus."""

    store, runtime_root = _managed_run_store(tmp_path)
    snapshot = _registry_snapshot(monkeypatch)
    created: list[list[str]] = []
    _fake_docker(monkeypatch, created)

    result = await execute_tool_activity(
        invocation_payload=_invocation_payload(
            {
                **CALLER_INPUTS,
                "workdir": "app",
                "envOverrides": {"CI": "1"},
                "declaredOutputs": {"output.report": "gpu.json"},
            }
        ),
        registry_snapshot=snapshot,
        dispatcher=_dispatcher(
            registry=_profile_registry(tmp_path, runtime_root), store=store
        ),
        context=_workflow_context(),
    )

    run_args = next(args for args in created if args[1] == "run")
    assert run_args[run_args.index("--gpus") + 1] == "all"
    # The caller owns the image and argv; MoonMind appends nothing.
    assert run_args[-len(CALLER_COMMAND) - 1 :] == [CALLER_IMAGE, *CALLER_COMMAND]
    # MoonMind owns the workspace: the workspace-relative workdir is resolved
    # against the run's authorized repo directory, not a caller-supplied path.
    run_root = runtime_root / "workspaces" / AGENT_RUN_ID
    assert run_args[run_args.index("--workdir") + 1] == str(run_root / "repo" / "app")
    # GPU support grants no additional daemon authority.
    assert "--privileged=false" in run_args
    for forbidden in ("--device", "--runtime", "--pid", "--ipc", "--host"):
        assert forbidden not in run_args

    assert result.status == "COMPLETED"
    assert result.outputs["launchOutcome"] == "succeeded"
    assert result.outputs["workloadMetadata"]["gpu"] == {
        "requested": True,
        "contractVersion": "v1",
        "vendor": "nvidia",
        "count": "all",
        "capabilities": [],
        "dockerAccepted": True,
    }


@pytest.mark.asyncio
async def test_caller_owned_doctor_output_is_collected_without_interpretation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC8: the caller runs its own doctor command and gets its output back."""

    store, runtime_root = _managed_run_store(tmp_path)
    snapshot = _registry_snapshot(monkeypatch)
    created: list[list[str]] = []
    doctor_stdout = b'{"callerOwnedVerdict": "pass", "devices": 1}\n'
    doctor_report = b'{"callerOwnedVerdict": "pass"}'
    _fake_docker(
        monkeypatch,
        created,
        run_stdout=doctor_stdout,
        container_writes={"gpu.json": doctor_report},
    )

    result = await execute_tool_activity(
        invocation_payload=_invocation_payload(
            {**CALLER_INPUTS, "declaredOutputs": {"output.report": "gpu.json"}}
        ),
        registry_snapshot=snapshot,
        dispatcher=_dispatcher(
            registry=_profile_registry(tmp_path, runtime_root), store=store
        ),
        context=_workflow_context(),
    )

    run_root = runtime_root / "workspaces" / AGENT_RUN_ID
    report_path = run_root / "artifacts" / STEP_ID / "gpu.json"
    assert result.outputs["outputRefs"]["output.report"] == str(report_path)
    # Verbatim: MoonMind neither parses the report nor derives a verdict from it.
    assert report_path.read_bytes() == doctor_report
    assert result.outputs["workloadResult"]["metadata"]["stdout"] == (
        doctor_stdout.decode()
    )
    assert "callerOwnedVerdict" not in json.dumps(
        result.outputs["workloadMetadata"]
    )
    # Cleanup removes only the run-owned container.
    control_verbs = {args[1] for args in created[1:] if len(args) > 1}
    assert control_verbs.isdisjoint({"rmi", "image", "volume", "system"})


@pytest.mark.asyncio
async def test_docker_rejection_stays_distinguishable_at_the_caller_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """125 is the daemon refusing the device request, not a process failure."""

    store, runtime_root = _managed_run_store(tmp_path)
    snapshot = _registry_snapshot(monkeypatch)
    created: list[list[str]] = []
    _fake_docker(monkeypatch, created, run_exit_code=125)

    result = await execute_tool_activity(
        invocation_payload=_invocation_payload(CALLER_INPUTS),
        registry_snapshot=snapshot,
        dispatcher=_dispatcher(
            registry=_profile_registry(tmp_path, runtime_root), store=store
        ),
        context=_workflow_context(),
    )

    assert result.status == "FAILED"
    assert result.outputs["launchOutcome"] == "docker_request_rejected"
    assert result.outputs["workloadMetadata"]["gpu"]["dockerAccepted"] is False
    assert result.outputs["exitCode"] == 125


@pytest.mark.asyncio
async def test_retry_of_the_same_step_execution_keeps_one_container_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, runtime_root = _managed_run_store(tmp_path)
    snapshot = _registry_snapshot(monkeypatch)
    created: list[list[str]] = []
    _fake_docker(monkeypatch, created)
    dispatcher = _dispatcher(
        registry=_profile_registry(tmp_path, runtime_root), store=store
    )

    first = await execute_tool_activity(
        invocation_payload=_invocation_payload(CALLER_INPUTS),
        registry_snapshot=snapshot,
        dispatcher=dispatcher,
        context=_workflow_context(execution_ordinal=2),
    )
    second = await execute_tool_activity(
        invocation_payload=_invocation_payload(CALLER_INPUTS),
        registry_snapshot=snapshot,
        dispatcher=dispatcher,
        context=_workflow_context(execution_ordinal=2),
    )
    third = await execute_tool_activity(
        invocation_payload=_invocation_payload(CALLER_INPUTS),
        registry_snapshot=snapshot,
        dispatcher=dispatcher,
        context=_workflow_context(execution_ordinal=3),
    )

    assert first.outputs["requestId"] == second.outputs["requestId"]
    assert third.outputs["requestId"] != first.outputs["requestId"]
    run_args = [args for args in created if args[1] == "run"]
    names = [args[args.index("--name") + 1] for args in run_args]
    assert names[0] == names[1] == first.outputs["requestId"]
    assert names[2] == third.outputs["requestId"]


@pytest.mark.asyncio
async def test_cancelling_the_dispatch_stops_only_the_run_owned_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation propagates through the caller route to the trusted launch."""

    store, runtime_root = _managed_run_store(tmp_path)
    snapshot = _registry_snapshot(monkeypatch)
    created: list[list[str]] = []
    run_processes = _fake_docker(monkeypatch, created, run_never_completes=True)

    task = asyncio.create_task(
        execute_tool_activity(
            invocation_payload=_invocation_payload(CALLER_INPUTS),
            registry_snapshot=snapshot,
            dispatcher=_dispatcher(
                registry=_profile_registry(tmp_path, runtime_root), store=store
            ),
            context=_workflow_context(),
        )
    )
    while not run_processes:
        await asyncio.sleep(0)
    task.cancel()
    done, pending = await asyncio.wait({task})

    assert done == {task} and pending == set()
    assert task.cancelled()
    container = "mm-workload-wf-gpu-run-render-1"
    assert ["docker", "stop", "-t", "30", container] in created
    assert ["docker", "kill", container] in created
    assert run_processes[0].terminated
    # Only the run-owned container is torn down.
    control_verbs = {args[1] for args in created[1:] if len(args) > 1}
    assert control_verbs.isdisjoint({"rmi", "image", "volume", "system"})


@pytest.mark.asyncio
async def test_cpu_only_container_step_is_unchanged_by_gpu_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, runtime_root = _managed_run_store(tmp_path)
    snapshot = _registry_snapshot(monkeypatch)
    created: list[list[str]] = []
    _fake_docker(monkeypatch, created)

    result = await execute_tool_activity(
        invocation_payload=_invocation_payload(
            {"image": CALLER_IMAGE, "command": ["python", "-V"]}
        ),
        registry_snapshot=snapshot,
        dispatcher=_dispatcher(
            registry=_profile_registry(tmp_path, runtime_root), store=store
        ),
        context=_workflow_context(),
    )

    run_args = next(args for args in created if args[1] == "run")
    assert "--gpus" not in run_args
    assert result.outputs["workloadMetadata"]["gpu"] == {"requested": False}


@pytest.mark.asyncio
async def test_gpu_container_runs_through_the_real_mm_tool_execute_activity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the Activity the worker actually binds, not just the dispatcher.

    ``mm.tool.execute`` is bound to ``TemporalSkillActivities.mm_tool_execute``
    on every non-workflow fleet, and a ``docker_workload`` capability routes the
    step to the agent_runtime fleet. Drive that wrapper with the invocation
    envelope ``MoonMind.UserWorkflow`` sends.
    """

    store, runtime_root = _managed_run_store(tmp_path)
    snapshot = _registry_snapshot(monkeypatch)
    created: list[list[str]] = []
    _fake_docker(monkeypatch, created)
    activities = TemporalSkillActivities(
        dispatcher=_dispatcher(
            registry=_profile_registry(tmp_path, runtime_root), store=store
        )
    )

    result = await activities.mm_tool_execute(
        invocation_payload=_invocation_payload(CALLER_INPUTS),
        registry_snapshot=snapshot,
        context=_workflow_context(),
        idempotency_key="wf:run:render:1:execute",
    )

    run_args = next(args for args in created if args[1] == "run")
    assert run_args[run_args.index("--gpus") + 1] == "all"
    assert result.status == "COMPLETED"
    assert result.outputs["workloadMetadata"]["gpu"]["requested"] is True


@pytest.mark.asyncio
async def test_a_snapshot_pinned_before_the_route_opened_keeps_its_fleet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-flight plans keep the route their pinned snapshot recorded.

    A plan generated while the deployment ran in ``profiles`` pinned the generic
    runtime CLI definition for this name, which carries the ``sandbox``
    capability. Dispatch resolves the fleet from that pinned definition, so
    switching the deployment to ``unrestricted`` mid-flight cannot turn a
    recorded CLI step into a container launch: it still lands on the sandbox
    fleet, whose dispatcher has no container handler, and the additive
    execution context is inert there.
    """

    catalog = build_default_activity_catalog()
    pinned = _registry_snapshot(monkeypatch, mode="profiles")
    reopened = _registry_snapshot(monkeypatch, mode="unrestricted")

    pinned_route = catalog.resolve_skill(
        pinned.get_skill(name=CONTAINER_RUN_CONTAINER_TOOL)
    )
    reopened_route = catalog.resolve_skill(
        reopened.get_skill(name=CONTAINER_RUN_CONTAINER_TOOL)
    )
    assert pinned_route.fleet == "sandbox"
    assert reopened_route.fleet == "agent_runtime"

    created: list[list[str]] = []
    _fake_docker(monkeypatch, created)
    # The sandbox fleet never registers the container handler, so the pinned
    # step keeps reaching the generic runtime CLI fallback.
    sandbox_dispatcher = ToolActivityDispatcher()
    generic_calls: list[Mapping[str, Any]] = []

    async def _generic_runtime_handler(inputs, context):
        generic_calls.append(dict(context or {}))
        return ToolResult(status="COMPLETED", outputs={"summary": "cli"})

    sandbox_dispatcher.register_default_skill_handler(handler=_generic_runtime_handler)

    result = await execute_tool_activity(
        invocation_payload=_invocation_payload({"instructions": "run the doctor"}),
        registry_snapshot=pinned,
        dispatcher=sandbox_dispatcher,
        context=_workflow_context(),
    )

    assert result.status == "COMPLETED"
    assert created == []
    assert CONTAINER_EXECUTION_CONTEXT_KEY in generic_calls[0]


# --------------------------------------------------------------------------- #
# 6. Refusals at the dispatch boundary
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("mode", ["profiles", "disabled"])
@pytest.mark.asyncio
async def test_dispatch_refuses_a_gpu_container_outside_unrestricted_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    store, runtime_root = _managed_run_store(tmp_path)
    snapshot = _registry_snapshot(monkeypatch)
    created: list[list[str]] = []
    _fake_docker(monkeypatch, created)

    with pytest.raises(ToolFailure) as exc:
        await execute_tool_activity(
            invocation_payload=_invocation_payload(CALLER_INPUTS),
            registry_snapshot=snapshot,
            dispatcher=_dispatcher(
                registry=_profile_registry(tmp_path, runtime_root),
                store=store,
                workflow_docker_mode=mode,
            ),
            context=_workflow_context(),
        )

    assert exc.value.error_code == "PERMISSION_DENIED"
    assert created == []


@pytest.mark.asyncio
async def test_dispatch_requires_the_workflow_injected_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, runtime_root = _managed_run_store(tmp_path)
    snapshot = _registry_snapshot(monkeypatch)
    created: list[list[str]] = []
    _fake_docker(monkeypatch, created)

    with pytest.raises(ToolFailure) as exc:
        await execute_tool_activity(
            invocation_payload=_invocation_payload(CALLER_INPUTS),
            registry_snapshot=snapshot,
            dispatcher=_dispatcher(
                registry=_profile_registry(tmp_path, runtime_root), store=store
            ),
            context={"workflow_id": AGENT_RUN_ID, "node_id": STEP_ID},
        )

    assert exc.value.error_code == "INVALID_INPUT"
    assert exc.value.details["reason"] == "container_execution_context_required"
    assert created == []


@pytest.mark.parametrize(
    "injected",
    [
        {"repoDir": "/etc"},
        {"scratchDir": "/tmp/anything"},
        {"agentRunId": "another-run"},
        {"toolName": "container.run_docker"},
    ],
)
@pytest.mark.asyncio
async def test_dispatch_refuses_caller_supplied_moonmind_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    injected: dict[str, Any],
) -> None:
    """The handler refuses authority injection even if a plan bypassed the schema."""

    store, runtime_root = _managed_run_store(tmp_path)
    snapshot = _registry_snapshot(monkeypatch)
    created: list[list[str]] = []
    _fake_docker(monkeypatch, created)

    with pytest.raises(ToolFailure) as exc:
        await execute_tool_activity(
            invocation_payload=_invocation_payload({**CALLER_INPUTS, **injected}),
            registry_snapshot=snapshot,
            dispatcher=_dispatcher(
                registry=_profile_registry(tmp_path, runtime_root), store=store
            ),
            context=_workflow_context(),
        )

    assert exc.value.error_code == "INVALID_INPUT"
    assert created == []


@pytest.mark.asyncio
async def test_dispatch_rejects_a_workdir_that_escapes_the_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, runtime_root = _managed_run_store(tmp_path)
    snapshot = _registry_snapshot(monkeypatch)
    created: list[list[str]] = []
    _fake_docker(monkeypatch, created)

    with pytest.raises(ToolFailure) as exc:
        await execute_tool_activity(
            invocation_payload=_invocation_payload(
                {**CALLER_INPUTS, "workdir": "../../etc"}
            ),
            registry_snapshot=snapshot,
            dispatcher=_dispatcher(
                registry=_profile_registry(tmp_path, runtime_root), store=store
            ),
            context=_workflow_context(),
        )

    assert exc.value.error_code == "INVALID_INPUT"
    assert created == []


@pytest.mark.asyncio
async def test_dispatch_rejects_a_malformed_gpu_request_before_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, runtime_root = _managed_run_store(tmp_path)
    snapshot = _registry_snapshot(monkeypatch)
    created: list[list[str]] = []
    _fake_docker(monkeypatch, created)

    with pytest.raises(ToolFailure) as exc:
        await execute_tool_activity(
            invocation_payload=_invocation_payload(
                {
                    **CALLER_INPUTS,
                    "resources": {"gpu": {"vendor": "nvidia", "count": 0}},
                }
            ),
            registry_snapshot=snapshot,
            dispatcher=_dispatcher(
                registry=_profile_registry(tmp_path, runtime_root), store=store
            ),
            context=_workflow_context(),
        )

    assert exc.value.error_code == "INVALID_INPUT"
    assert created == []
