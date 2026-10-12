"""MoonMind#4627: an interrupted generic Omnigent attempt hands off safely.

Host loss ends the attempt with ``retry_step_execution``. Cleanup already saves
the caller-owned workspace before removing the host; the typed failure must
carry that verified saved archive so the successor Step Execution can restore
it. A successor may only be recommended once the old host's stop is confirmed;
an unconfirmed stop is reconciled within bounds, never raced by new compute.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from moonmind.omnigent.execute import OmnigentSessionHostLostError
from moonmind.omnigent.host_leases import InMemoryOmnigentHostLeaseRepository
from moonmind.omnigent.realizers.generic_host import (
    GenericOmnigentHostRealizer,
    carry_saved_workspace_checkpoint,
)
from moonmind.omnigent.runtime_bindings import (
    InMemoryStableRuntimeBindingStore,
    RuntimeBindingSessionAuthoritySink,
    RuntimeBindingState,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.activities.omnigent_activities import (
    _typed_platform_failure_result,
)
from moonmind.workflows.temporal.step_executions import (
    verified_saved_workspace_restore_ref,
)

_SAVED = {
    "kind": "worktree_archive",
    "baseCommit": "b" * 40,
    "archiveRef": "artifact://saved-worktree",
    "archiveDigest": "sha256:" + "a" * 64,
    "manifestRef": "artifact://saved-manifest",
    "workspaceDigest": "sha256:" + "c" * 64,
    "checkpointRef": "artifact://saved-step-checkpoint",
    "recoveryEvidence": {"large": "x" * 4096},
}


def _request() -> AgentExecutionRequest:
    return AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": "workflow-1",
            "idempotencyKey": "wf-1:step:1",
        }
    )


def _realizer(store, saves: list[str]):
    realizer = object.__new__(GenericOmnigentHostRealizer)
    realizer._runtime_bindings = store
    realizer._turn_commands = None
    realizer._cleanup_authority = None
    realizer._artifacts = None

    async def save_request_workspace(request):
        saves.append(request.idempotency_key)
        return dict(_SAVED)

    async def noop(*_args, **_kwargs):
        return None

    async def cleanup_all(_handles):
        return []

    realizer._session_cleanup = SimpleNamespace(drain=noop)
    realizer._workspace_publisher = SimpleNamespace(
        save_request_workspace=save_request_workspace
    )
    realizer._host_runtime = SimpleNamespace(
        cleanup_prepared=noop, cleanup_authorities=noop
    )
    realizer._credentials = SimpleNamespace(cleanup_all=cleanup_all)
    realizer._provider_leases = SimpleNamespace(release_all=noop)
    realizer._host_leases = SimpleNamespace()
    return realizer


@pytest.mark.asyncio
async def test_host_loss_failure_carries_verified_saved_workspace_to_successor():
    store = InMemoryStableRuntimeBindingStore()
    request = _request()
    binding = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "d" * 64,
        idempotency_key=request.idempotency_key,
        provider_leases={},
    )
    sink = RuntimeBindingSessionAuthoritySink(store, binding)
    await sink.record_phase(
        "workspace", {"workspaceSpec": {"workspaceLocator": {"workspaceId": "ws-1"}}}
    )
    saves: list[str] = []
    realizer = _realizer(store, saves)

    cleaned, _ = await realizer._cleanup(
        request=request,
        binding=sink.binding,
        host_lease=None,
        host_context=None,
        prepared=None,
        credential_handles=(),
        acquired=(),
    )
    host_lost = OmnigentSessionHostLostError(
        "session host lost",
        snapshot={
            "runner_online": False,
            "host_online": False,
            "host_resumable": False,
        },
    )
    carry_saved_workspace_checkpoint(host_lost, cleaned)
    result = _typed_platform_failure_result(host_lost)

    assert cleaned.state is RuntimeBindingState.cleaned
    assert saves == [request.idempotency_key]
    assert result is not None
    assert result.retry_recommendation == "retry_step_execution"
    carried = result.metadata["savedWorkspaceCheckpoint"]
    # Only compact restore identity crosses workflow history.
    assert "recoveryEvidence" not in carried
    assert carried["checkpointRef"] == _SAVED["checkpointRef"]
    assert verified_saved_workspace_restore_ref(carried) == _SAVED["archiveRef"]


@pytest.mark.asyncio
async def test_unsaved_interruption_carries_no_restore_claim():
    store = InMemoryStableRuntimeBindingStore()
    request = _request()
    binding = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "e" * 64,
        idempotency_key=request.idempotency_key,
        provider_leases={},
    )
    host_lost = OmnigentSessionHostLostError(
        "session host lost",
        snapshot={
            "runner_online": False,
            "host_online": False,
            "host_resumable": False,
        },
    )
    carry_saved_workspace_checkpoint(host_lost, binding)
    result = _typed_platform_failure_result(host_lost)

    assert result is not None
    assert "savedWorkspaceCheckpoint" not in result.metadata


@pytest.mark.parametrize(
    "receipt",
    [
        None,
        {"kind": "worktree_archive", "archiveRef": "/work/dead-container/repo"},
        {"kind": "worktree_archive", "archiveRef": "artifact://x"},
        {
            "kind": "git_commit",
            "archiveRef": "artifact://x",
            "archiveDigest": "sha256:1",
        },
        {
            "kind": "worktree_archive",
            "archiveRef": "artifact://",
            "archiveDigest": "sha256:1",
        },
    ],
    ids=["missing", "local-path", "no-digest", "wrong-kind", "empty-ref"],
)
def test_only_durable_verified_archives_are_restore_inputs(receipt):
    assert verified_saved_workspace_restore_ref(receipt) is None


class _FlakyHostRuntime:
    """Container removal that fails ``failures`` times (partition / lost ack)."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.removals = 0

    async def cleanup(self, **_kwargs):
        self.removals += 1
        if self.removals <= self.failures:
            raise ConnectionError("docker transport unavailable")
        return {"removed": True}

    async def cleanup_prepared(self, _prepared):
        return None

    async def cleanup_authorities(self, _refs):
        return None


async def _interrupted_attempt(*, removal_failures: int, credential_failures: int = 0):
    store = InMemoryStableRuntimeBindingStore()
    request = _request()
    binding = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "f" * 64,
        idempotency_key=request.idempotency_key,
        provider_leases={},
    )
    leases = InMemoryOmnigentHostLeaseRepository()
    lease = await leases.acquire(
        execution_plan_ref=binding.executionPlanRef,
        runtime_binding_id=binding.bindingId,
        host_class_ref="omnigent-host-class:codex@1",
        launch_policy_ref="codex-on-demand@1",
        harness_id="codex",
        harness_implementation_ref="codex@1",
        provider_profile_refs=(),
    )
    lease = await leases.mark_ready(
        lease.leaseRef,
        expected_generation=lease.generation,
        omnigent_host_id="host-1",
        cleanup_handle={"container": "old-host"},
    )
    sink = RuntimeBindingSessionAuthoritySink(store, binding)
    await sink.record_phase(
        "workspace", {"workspaceSpec": {"workspaceLocator": {"workspaceId": "ws-2"}}}
    )
    binding = sink.binding
    saves: list[str] = []
    realizer = _realizer(store, saves)
    realizer._host_leases = leases
    host_runtime = _FlakyHostRuntime(removal_failures)
    realizer._host_runtime = host_runtime
    realizer._INTERRUPTED_CLEANUP_RETRY_DELAYS = (0.0, 0.0, 0.0)
    released: list[tuple] = []
    credential_calls = {"count": 0}

    async def cleanup_all(_handles):
        credential_calls["count"] += 1
        if credential_calls["count"] <= credential_failures:
            raise ConnectionError("credential store unavailable")
        return []

    async def release_all(acquired):
        released.append(tuple(acquired))

    realizer._credentials = SimpleNamespace(cleanup_all=cleanup_all)
    realizer._provider_leases = SimpleNamespace(release_all=release_all)
    host_lost = OmnigentSessionHostLostError(
        "session host lost",
        snapshot={
            "runner_online": False,
            "host_online": False,
            "host_resumable": False,
        },
    )
    cleanup_error = None
    try:
        await realizer._cleanup(
            request=request,
            binding=binding,
            host_lease=lease,
            host_context=None,
            prepared=None,
            credential_handles=("cred-1",),
            acquired=("provider-lease-1",),
        )
    except Exception as exc:  # noqa: BLE001 - the first cleanup is interrupted
        cleanup_error = exc
    assert cleanup_error is not None
    reconciled, remaining_error = await realizer._reconcile_interrupted_attempt(
        host_lost,
        request=request,
        binding=binding,
        host_lease=lease,
        host_context=None,
        prepared=None,
        credential_handles=("cred-1",),
        acquired=("provider-lease-1",),
        cleanup_error=cleanup_error,
    )
    return SimpleNamespace(
        error=host_lost,
        binding=reconciled,
        remaining_error=remaining_error,
        lease=await leases.get(lease.leaseRef),
        host_runtime=host_runtime,
        released=released,
        saves=saves,
    )


@pytest.mark.asyncio
async def test_lost_stop_acknowledgment_is_reconciled_before_successor():
    outcome = await _interrupted_attempt(removal_failures=2)
    result = _typed_platform_failure_result(outcome.error)

    assert outcome.remaining_error is None
    assert outcome.lease.status == "cleaned"
    assert outcome.binding.state is RuntimeBindingState.cleaned
    assert outcome.host_runtime.removals == 3
    # Saved once; capacity released once, only after the stop was confirmed.
    assert outcome.saves == [_request().idempotency_key]
    assert outcome.released == [("provider-lease-1",)]
    assert result.retry_recommendation == "retry_step_execution"
    assert result.metadata["predecessorStopConfirmed"] is True
    assert (
        result.metadata["savedWorkspaceCheckpoint"]["archiveRef"]
        == _SAVED["archiveRef"]
    )


@pytest.mark.asyncio
async def test_unconfirmed_predecessor_stop_starts_no_parallel_successor():
    outcome = await _interrupted_attempt(removal_failures=99)
    result = _typed_platform_failure_result(outcome.error)

    assert outcome.remaining_error is not None
    assert outcome.lease.status == "cleanup_pending"
    # Bounded: the first cleanup plus one attempt per configured delay.
    assert outcome.host_runtime.removals == 4
    # Capacity of a possibly-live host is not released to a successor.
    assert outcome.released == []
    assert result.retry_recommendation != "retry_step_execution"
    assert result.metadata["predecessorStopConfirmed"] is False


@pytest.mark.asyncio
async def test_auxiliary_cleanup_trouble_does_not_block_a_stopped_predecessor():
    outcome = await _interrupted_attempt(removal_failures=0, credential_failures=99)
    result = _typed_platform_failure_result(outcome.error)

    assert outcome.lease.status == "cleaned"
    assert outcome.host_runtime.removals == 1
    assert outcome.remaining_error is not None
    assert result.retry_recommendation == "retry_step_execution"
    assert result.metadata["predecessorStopConfirmed"] is True


class _MemoryArtifacts:
    """Durable artifact storage that outlives the interrupted host."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    async def write_bytes(self, *, request, name, payload, content_type, link_type):
        ref = f"artifact://art_{len(self.blobs)}_{name}"
        self.blobs[ref] = bytes(payload)
        return ref

    async def read_bytes(self, ref):
        return self.blobs[ref]

    async def read(self, *, artifact_id: str, **_kwargs):
        return {}, self.blobs[f"artifact://{artifact_id}"]


def _step_request(root, *, workflow_id: str, ordinal: int, restore_ref=None):
    from moonmind.workflows.temporal.runtime.workspace_locators import (
        SandboxWorkspaceRecord,
        SandboxWorkspaceRecordStore,
    )

    step_id = f"{workflow_id}:run-1:implement:execution:{ordinal}"
    workspace_id = hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[:24]
    SandboxWorkspaceRecordStore(root).ensure(
        SandboxWorkspaceRecord(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            step_execution_id=step_id,
            relative_path="repo",
        )
    )
    workspace_spec = {
        "workspaceLocator": {
            "kind": "sandbox",
            "workspaceId": workspace_id,
            "relativePath": "repo",
        },
        "repository": "MoonLadderStudios/MoonMind",
        "branch": "main",
    }
    if restore_ref:
        workspace_spec["workspaceCheckpointRestoreRef"] = restore_ref
    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": workflow_id,
            "idempotencyKey": step_id,
            "workspaceSpec": workspace_spec,
            "stepExecution": {
                "workflowId": workflow_id,
                "runId": "run-1",
                "logicalStepId": "implement",
                "executionOrdinal": ordinal,
                "stepExecutionId": step_id,
                "runtimeContextPolicy": "fresh_agent_run",
            },
        }
    )
    return request, root / "temporal_sandbox" / workspace_id / "repo"


@pytest.mark.asyncio
async def test_successor_workspace_restores_verified_bytes_after_old_host_removal(
    tmp_path,
):
    """The interrupted attempt's real save becomes the successor's workspace."""

    from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer
    from moonmind.omnigent.workspace_publication import (
        OmnigentWorkspacePublicationService,
    )

    def git(cwd, *args):
        return subprocess.check_output(
            ["git", "-C", str(cwd), *args], text=True
        ).strip()

    root = tmp_path / "worker"
    artifacts = _MemoryArtifacts()
    predecessor, predecessor_workspace = _step_request(
        root, workflow_id="wf-restart", ordinal=1
    )
    predecessor_workspace.mkdir(parents=True)
    git(predecessor_workspace, "init", "-q", "-b", "main")
    git(predecessor_workspace, "config", "user.name", "Restart")
    git(predecessor_workspace, "config", "user.email", "restart@example.invalid")
    (predecessor_workspace / "file.txt").write_text("original\n")
    git(predecessor_workspace, "add", ".")
    git(predecessor_workspace, "commit", "-qm", "base")
    origin = tmp_path / "origin.git"
    subprocess.check_call(
        ["git", "clone", "-q", "--bare", str(predecessor_workspace), str(origin)]
    )
    # Unpublished progress made by the interrupted agent attempt.
    (predecessor_workspace / "file.txt").write_text("interrupted candidate\n")
    (predecessor_workspace / "new.bin").write_bytes(b"\x00\xff\x80")

    saved = await OmnigentWorkspacePublicationService(
        root, artifact_gateway=artifacts
    ).save_request_workspace(predecessor)
    restore_ref = verified_saved_workspace_restore_ref(saved)
    assert restore_ref is not None
    # The old host and its local workspace are gone; only durable bytes remain.
    shutil.rmtree(predecessor_workspace.parent)

    successor, successor_workspace = _step_request(
        root, workflow_id="wf-restart", ordinal=2, restore_ref=restore_ref
    )
    assert successor_workspace != predecessor_workspace
    successor_workspace.parent.mkdir(parents=True)
    subprocess.check_call(["git", "clone", "-q", str(origin), str(successor_workspace)])

    async def no_clone(*_args, **_kwargs):
        raise AssertionError("the successor checkout already exists")

    owner = successor_workspace.stat()
    await OmnigentWorkspaceMaterializer(
        command_runner=no_clone,
        workspace_root=root,
        artifact_service=artifacts,
    ).materialize(successor, runtime_uid=owner.st_uid, runtime_gid=owner.st_gid)

    assert (successor_workspace / "file.txt").read_text() == "interrupted candidate\n"
    assert (successor_workspace / "new.bin").read_bytes() == b"\x00\xff\x80"
    assert hashlib.sha256(artifacts.blobs[restore_ref]).hexdigest() == (
        saved["archiveDigest"].removeprefix("sha256:")
    )
