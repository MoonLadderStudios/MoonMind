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
    reconciled, cleanup_left, stopped = await realizer._reconcile_interrupted_attempt(
        request=request,
        binding=binding,
        host_lease=lease,
        host_context=None,
        prepared=None,
        credential_handles=("cred-1",),
        acquired=("provider-lease-1",),
        cleanup_error=cleanup_error,
    )
    host_lost.predecessor_stop_confirmed = stopped
    carry_saved_workspace_checkpoint(host_lost, reconciled)
    return SimpleNamespace(
        error=host_lost,
        binding=reconciled,
        remaining_error=cleanup_left,
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


async def _returned_host_loss_harness(*, removal_failures: int):
    """The real realizer around the production returned host-loss outcome.

    ``run_omnigent_execution`` returns (does not raise) the typed
    OMNIGENT_SESSION_HOST_LOST result once the host is gone; see
    ``tests/unit/omnigent/test_execute.py`` host-loss cases.
    """

    from moonmind.omnigent.harness_platform.failures import remediation_for
    from moonmind.schemas.agent_runtime_models import AgentRunResult
    from tests.unit.omnigent.test_generic_platform_production_services import (
        _PUSHED_PUBLICATION,
        _generic_publication_harness,
    )

    harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
    realizer = harness.realizer
    realizer._INTERRUPTED_CLEANUP_RETRY_DELAYS = (0.0, 0.0, 0.0)
    harness.removals = 0

    async def remove_host(**_kwargs):
        harness.removals += 1
        harness.events.append("host-removal-attempted")
        if harness.removals <= removal_failures:
            # A partitioned Docker transport: the old host may still be live.
            raise ConnectionError("docker transport unavailable")
        harness.events.append("host-cleaned")
        return {"containerRemoved": True}

    realizer._host_runtime.cleanup = remove_host

    async def host_lost_driver(request, *, session_authority_sink, **_kwargs):
        await session_authority_sink.session_created("session-1")
        harness.events.append("host-lost")
        return AgentRunResult(
            summary="The Omnigent host running this session is gone.",
            failureClass="integration_error",
            providerErrorCode="OMNIGENT_SESSION_HOST_LOST",
            retryRecommendation=remediation_for("OMNIGENT_SESSION_HOST_LOST"),
            metadata={"normalizedStatus": "failed", "omnigentSessionId": "session-1"},
        )

    realizer._session_driver = host_lost_driver
    return harness


@pytest.mark.asyncio
async def test_returned_host_loss_waits_for_confirmed_predecessor_stop():
    from tests.unit.omnigent.test_generic_platform_production_services import _plan

    harness = await _returned_host_loss_harness(removal_failures=2)

    result = await harness.realizer.execute(
        harness.publish_request, _plan("opencode-go/model")
    )

    assert result.provider_error_code == "OMNIGENT_SESSION_HOST_LOST"
    assert result.retry_recommendation == "retry_step_execution"
    assert result.metadata["predecessorStopConfirmed"] is True
    assert result.metadata["savedWorkspaceCheckpoint"]["archiveRef"] == (
        "artifact://saved"
    )
    assert harness.removals == 3
    # Capacity is released once, only after the old host's removal confirmed.
    assert harness.events.count("provider-released") == 1
    assert harness.events.index("host-cleaned") < harness.events.index(
        "provider-released"
    )
    assert "workspace-published" not in harness.events


@pytest.mark.asyncio
async def test_returned_host_loss_with_unconfirmed_stop_starts_no_successor():
    from tests.unit.omnigent.test_generic_platform_production_services import _plan

    harness = await _returned_host_loss_harness(removal_failures=99)

    result = await harness.realizer.execute(
        harness.publish_request, _plan("opencode-go/model")
    )

    assert result.provider_error_code == "OMNIGENT_SESSION_HOST_LOST"
    assert result.retry_recommendation == "delegate_to_janitor"
    assert result.metadata["predecessorStopConfirmed"] is False
    # Bounded: the first removal plus one attempt per configured delay.
    assert harness.removals == 4
    # A possibly-live host keeps its capacity; the janitor owns the rest.
    assert "provider-released" not in harness.events
    lease = next(iter(harness.host_leases._leases.values()))
    assert lease.status == "cleanup_pending"


@pytest.mark.asyncio
async def test_redelivered_host_loss_result_is_gated_on_predecessor_stop():
    """A replacement worker resuming a recorded host-loss result is gated too."""

    from moonmind.omnigent.runtime_bindings import stable_binding_id
    from tests.unit.omnigent.test_generic_platform_production_services import (
        _plan,
        _prime_attested_host_binding,
    )

    harness = await _returned_host_loss_harness(removal_failures=99)
    plan = _plan("opencode-go/model")
    await _prime_attested_host_binding(harness, plan)
    binding = await harness.runtime_store.get(
        stable_binding_id(
            execution_plan_ref=plan.planRef,
            idempotency_key=harness.publish_request.idempotency_key,
            admission_epoch=0,
        )
    )
    await harness.runtime_store.update(
        binding.bindingId,
        expected_revision=binding.revision,
        expected_fencing_generation=binding.fencingGeneration,
        updates={
            "omnigentSessionId": "session-1",
            "terminalResult": {
                "summary": "The Omnigent host running this session is gone.",
                "failureClass": "integration_error",
                "providerErrorCode": "OMNIGENT_SESSION_HOST_LOST",
                "retryRecommendation": "retry_step_execution",
                "metadata": {"omnigentSessionId": "session-1"},
            },
        },
    )

    result = await harness.realizer.execute(harness.publish_request, plan)

    assert "host-lost" not in harness.events
    assert result.retry_recommendation == "delegate_to_janitor"
    assert result.metadata["predecessorStopConfirmed"] is False
    assert "provider-released" not in harness.events


@pytest.mark.asyncio
async def test_persisted_plan_dispatch_failure_with_unconfirmed_stop_starts_no_successor():
    from types import SimpleNamespace as Realizer

    from moonmind.omnigent.harness_platform.failures import (
        HarnessPlatformError,
        HarnessPlatformFailure,
    )
    from moonmind.omnigent.harness_platform.planner import compile_execution_plan
    from moonmind.omnigent.harness_platform.stores import InMemoryExecutionPlanStore
    from moonmind.omnigent.realizers.registry import OmnigentExecutionRealizerRegistry
    from moonmind.workflows.temporal.activities.omnigent_activities import (
        _try_generic_realizer_dispatch,
    )
    from tests.unit.omnigent.test_plan_and_realizer_dispatch import (
        _codex_authority_dispatch_request,
        _codex_plan_inputs,
    )

    plan = compile_execution_plan(**_codex_plan_inputs())
    plans = InMemoryExecutionPlanStore()
    await plans.persist(plan)
    request = _codex_authority_dispatch_request(plan)

    async def interrupted(_request, _plan):
        error = HarnessPlatformError(
            "the interrupted host's removal is unconfirmed",
            code=HarnessPlatformFailure.OMNIGENT_EXTERNAL_SERVICE_UNAVAILABLE,
        )
        error.predecessor_stop_confirmed = False
        raise error

    registry = OmnigentExecutionRealizerRegistry()
    registry.register(
        Realizer(ref=plan.payload.executionRealizerRef, execute=interrupted)
    )

    result = await _try_generic_realizer_dispatch(
        request, plan_store=plans, realizer_registry=registry
    )

    assert result.provider_error_code == "OMNIGENT_EXTERNAL_SERVICE_UNAVAILABLE"
    assert result.retry_recommendation == "delegate_to_janitor"
    assert result.metadata["predecessorStopConfirmed"] is False


@pytest.mark.asyncio
async def test_successor_attempt_reconciles_the_predecessors_publication(
    tmp_path, monkeypatch
):
    """A lost push acknowledgment does not fork the step's branch/PR effect.

    The predecessor attempt pushed before its host was lost and the workflow
    never saw the acknowledgment. The successor Step Execution inherits the
    logical publication identity, so the real publisher reconciles the same
    remote branch and PR instead of orphaning it under a new name.
    """

    from unittest.mock import AsyncMock

    from moonmind.config.settings import settings
    from moonmind.omnigent.workspace_intent import request_publication_identity
    from moonmind.omnigent.workspace_publication import (
        OmnigentWorkspacePublicationService,
    )
    from moonmind.workflows.temporal.runtime.workspace_locators import (
        SandboxWorkspaceRecord,
        SandboxWorkspaceRecordStore,
    )

    def git(cwd, *args):
        return subprocess.run(
            ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    monkeypatch.setattr(settings.security, "high_security_mode", False)
    for prefix in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{prefix}_NAME", "Restart")
        monkeypatch.setenv(f"GIT_{prefix}_EMAIL", "restart@example.invalid")
    monkeypatch.setattr(
        "moonmind.omnigent.workspace_publication.resolve_github_credential",
        AsyncMock(return_value=SimpleNamespace(token="fixture-credential")),
    )
    resolve_pr = AsyncMock(
        return_value=SimpleNamespace(
            resolved=True, pr_url="https://github.com/example/repository/pull/7"
        )
    )
    monkeypatch.setattr(
        "moonmind.omnigent.workspace_publication.GitHubService."
        "resolve_pull_request_selector",
        resolve_pr,
    )
    origin = tmp_path / "origin.git"
    subprocess.check_call(
        ["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)]
    )
    seed = tmp_path / "seed"
    subprocess.check_call(["git", "init", "-q", "--initial-branch=main", str(seed)])
    (seed / "file.txt").write_text("base\n")
    git(seed, "add", ".")
    git(seed, "commit", "-qm", "base")
    git(seed, "push", "-q", str(origin), "main")

    workflow_id = "wf-publication-restart"

    def step(ordinal: int, *, inherited: str | None = None):
        step_id = f"{workflow_id}:run-1:implement:execution:{ordinal}"
        workspace_id = hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[
            :24
        ]
        workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
        workspace.parent.mkdir(parents=True)
        subprocess.check_call(["git", "clone", "-q", str(origin), str(workspace)])
        SandboxWorkspaceRecordStore(tmp_path).ensure(
            SandboxWorkspaceRecord(workspace_id, workflow_id, step_id, "repo")
        )
        parameters = {"publishMode": "pr", "repository": "example/repository"}
        if inherited:
            parameters["publicationIdentity"] = inherited
        request = AgentExecutionRequest.model_validate(
            {
                "agentKind": "external",
                "agentId": "omnigent",
                "correlationId": workflow_id,
                "idempotencyKey": f"{step_id}:agent_execute",
                "workspaceSpec": {
                    "workspaceLocator": {
                        "kind": "sandbox",
                        "workspaceId": workspace_id,
                        "relativePath": "repo",
                    },
                    "repository": "example/repository",
                    "startingBranch": "main",
                },
                "parameters": parameters,
            }
        )
        return request, step_id, workspace

    publisher = OmnigentWorkspacePublicationService(tmp_path)
    predecessor, predecessor_step, predecessor_workspace = step(1)
    (predecessor_workspace / "file.txt").write_text("interrupted work\n")
    git(predecessor_workspace, "commit", "-qam", "interrupted work")
    first = await publisher.publish_request_workspace(
        request=predecessor,
        current_workflow_id=workflow_id,
        current_step_execution_id=predecessor_step,
    )
    # The push committed remotely; its acknowledgment never reached the
    # workflow before the host was lost.
    assert first["push_status"] == "pushed"

    successor, successor_step, successor_workspace = step(
        2, inherited=request_publication_identity(predecessor)
    )
    # The successor restored the predecessor's saved work, then continued.
    git(successor_workspace, "pull", "-q", str(predecessor_workspace), "main")
    (successor_workspace / "more.txt").write_text("successor work\n")
    git(successor_workspace, "add", ".")
    git(successor_workspace, "commit", "-qm", "successor work")
    successor_head = git(successor_workspace, "rev-parse", "HEAD")
    for _delivery in range(2):
        # The second delivery replays a lost acknowledgment of the first.
        second = await publisher.publish_request_workspace(
            request=successor,
            current_workflow_id=workflow_id,
            current_step_execution_id=successor_step,
        )
        assert second["push_branch"] == first["push_branch"]
        assert second["push_head_sha"] == successor_head
        assert second["pull_request_url"].endswith("/pull/7")

    branch = first["push_branch"]
    assert git(origin, "branch", "--format=%(refname:short)").splitlines() == [
        "main",
        branch,
    ]
    assert git(origin, "show", f"{branch}:file.txt") == "interrupted work"
    assert git(origin, "show", f"{branch}:more.txt") == "successor work"
    assert [call.kwargs["selector"] for call in resolve_pr.await_args_list] == [
        branch
    ] * 3
