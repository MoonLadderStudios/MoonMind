"""Save-before-cleanup completion for MoonLadderStudios/MoonMind#4016.

Bounded proof for the existing finalization path
(GenericOmnigentHostRealizer + attempt_completion + Checkpoint Branch turn):
confirmed compute/turn evidence is persisted before fallible publication or
teardown, failed/canceled compute keeps its verdict when files are saved,
a branch child result survives failed capture without a second launch, and
janitors never silently delete the only recoverable copy.
"""

from __future__ import annotations

import asyncio

import pytest

from moonmind.omnigent.attempt_completion import recorded_attempt_result
from moonmind.schemas.agent_runtime_models import AgentRunResult

_real_sleep = asyncio.sleep


def _binding(phase_results, terminal_result=None):
    from types import SimpleNamespace

    return SimpleNamespace(terminalResult=terminal_result, phaseResults=phase_results)


@pytest.mark.parametrize("failure_class", ["execution_error", "canceled"])
def test_failed_or_canceled_compute_keeps_verdict_when_files_saved(failure_class):
    compute = AgentRunResult(
        summary="child compute finished",
        failureClass=failure_class,
        providerErrorCode="AGENT_EXIT_1",
        retryRecommendation="do_not_retry",
    )
    binding = _binding(
        {
            "turn:0": compute.model_dump(mode="json", by_alias=True),
            "saved": {"checkpointRef": "artifact://saved-candidate"},
        }
    )

    result = recorded_attempt_result(binding)

    assert result.failure_class == failure_class
    assert result.provider_error_code == "AGENT_EXIT_1"
    assert result.retry_recommendation == "do_not_retry"
    assert result.metadata["unfinishedPhase"] == "finalization"
    assert result.metadata["workPreserved"] is True
    assert result.metadata["savedWorkspaceCheckpoint"] == {
        "checkpointRef": "artifact://saved-candidate"
    }


def test_successful_compute_without_publication_stays_pending_recovery():
    compute = AgentRunResult(summary="verified compute")
    binding = _binding(
        {
            "turn:0": compute.model_dump(mode="json", by_alias=True),
            "saved": {"archiveRef": "artifact://saved"},
        }
    )

    pending = recorded_attempt_result(binding)

    assert pending.failure_class == "integration_error"
    assert pending.provider_error_code == "ATTEMPT_FINALIZATION_INTERRUPTED"
    assert pending.metadata["unfinishedPhase"] == "finalization"
    assert pending.metadata["workPreserved"] is True


@pytest.mark.parametrize(
    ("failure_class", "expected_outcome"),
    [(None, "failed"), ("execution_error", "failed"), ("canceled", "canceled")],
)
def test_branch_capture_failure_persists_child_without_second_launch(
    failure_class, expected_outcome
):
    from moonmind.workflows.temporal.workflows.checkpoint_branch_turn import (
        branch_capture_failure_outcome,
    )

    child = AgentRunResult(summary="branch child compute", failureClass=failure_class)

    assert (
        branch_capture_failure_outcome(
            child_result=child, terminal_handoff_started=False
        )
        == expected_outcome
    )


def test_branch_capture_failure_without_child_or_after_handoff_reraises():
    from moonmind.workflows.temporal.workflows.checkpoint_branch_turn import (
        branch_capture_failure_outcome,
    )

    child = AgentRunResult(summary="branch child compute")

    assert (
        branch_capture_failure_outcome(
            child_result=None, terminal_handoff_started=False
        )
        is None
    )
    assert (
        branch_capture_failure_outcome(
            child_result=child, terminal_handoff_started=True
        )
        is None
    )


@pytest.mark.parametrize(
    ("failure_class", "checkpoint_ref", "expected"),
    [
        ("execution_error", None, "provider_failure"),
        ("canceled", None, "canceled"),
        (None, None, "terminal_checkpoint_missing"),
        (None, "artifact://checkpoint", "verification_pending"),
    ],
)
def test_preserved_branch_child_classifies_without_repair_success(
    failure_class, checkpoint_ref, expected
):
    from moonmind.workflows.temporal.workflows.checkpoint_branch_turn import (
        checkpoint_branch_turn_terminal_disposition,
    )

    child = AgentRunResult(summary="branch child compute", failureClass=failure_class)

    assert (
        checkpoint_branch_turn_terminal_disposition(
            result=child, checkpoint_ref=checkpoint_ref, authority_chain=None
        )
        == expected
    )


def _finalization_request():
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    return AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": "workflow-4016",
            "idempotencyKey": "save-before-cleanup-4016",
            "workspaceSpec": {"repository": "owner/repo"},
        }
    )


def _finalization_realizer(store, *, publisher, publish=None):
    from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer

    realizer = object.__new__(GenericOmnigentHostRealizer)
    realizer._runtime_bindings = store
    realizer._workspace_publisher = publisher
    realizer._turn_commands = None
    realizer._cleanup_authority = None
    if publish is not None:
        realizer._publish_repository = publish
    return realizer


async def _workspace_sink(store):
    from moonmind.omnigent.runtime_bindings import RuntimeBindingSessionAuthoritySink

    binding = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "b" * 64,
        idempotency_key="save-before-cleanup-4016",
        provider_leases={},
    )
    sink = RuntimeBindingSessionAuthoritySink(store, binding)
    await sink.record_phase(
        "workspace",
        {"workspaceSpec": {"workspaceLocator": {"kind": "sandbox"}}},
    )
    return sink


@pytest.mark.asyncio
async def test_finish_saves_missing_ref_through_owner_then_publishes_once():
    from moonmind.omnigent.runtime_bindings import InMemoryStableRuntimeBindingStore

    store = InMemoryStableRuntimeBindingStore()
    calls = []

    class Publisher:
        async def save_request_workspace(self, request):
            calls.append("save")
            return {"checkpointRef": "artifact://candidate"}

    async def publish(request, result):
        calls.append("publish")
        return result

    realizer = _finalization_realizer(store, publisher=Publisher(), publish=publish)
    sink = await _workspace_sink(store)

    result = await realizer._finish_owned_execution(
        _finalization_request(), sink, AgentRunResult(summary="compute done")
    )

    assert calls == ["save", "publish"]
    assert result.metadata["savedWorkspaceCheckpoint"] == {
        "checkpointRef": "artifact://candidate"
    }
    assert result.metadata["workPreserved"] is True


@pytest.mark.asyncio
async def test_finish_retries_only_unfinished_publication_without_new_compute(
    monkeypatch,
):
    import asyncio

    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
    from moonmind.omnigent.runtime_bindings import InMemoryStableRuntimeBindingStore

    monkeypatch.setattr(asyncio, "sleep", lambda delay: _real_sleep(0))
    store = InMemoryStableRuntimeBindingStore()
    saves = []
    publishes = []

    class Publisher:
        async def save_request_workspace(self, request):
            saves.append(1)
            return {"checkpointRef": "artifact://candidate"}

    async def publish(request, result):
        publishes.append(1)
        if len(publishes) < 3:
            raise HarnessPlatformError(
                "publication transport failed",
                code="OMNIGENT_REPOSITORY_PUBLICATION_FAILED",
            )
        return result

    realizer = _finalization_realizer(store, publisher=Publisher(), publish=publish)
    sink = await _workspace_sink(store)

    result = await realizer._finish_owned_execution(
        _finalization_request(), sink, AgentRunResult(summary="compute done")
    )

    assert saves == [1]
    assert publishes == [1, 1, 1]
    assert result.failure_class is None
    phases = (await store.get(sink.binding.bindingId)).phaseResults
    assert "publication_failure:0" in phases
    assert "publication_failure:1" in phases
    assert "publication" in phases
    assert "turn:1" not in phases


@pytest.mark.asyncio
async def test_exhausted_publication_keeps_saved_work_as_failure_not_success(
    monkeypatch,
):
    import asyncio

    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
    from moonmind.omnigent.runtime_bindings import InMemoryStableRuntimeBindingStore

    monkeypatch.setattr(asyncio, "sleep", lambda delay: _real_sleep(0))
    store = InMemoryStableRuntimeBindingStore()

    class Publisher:
        async def save_request_workspace(self, request):
            return {"checkpointRef": "artifact://candidate"}

    async def publish(request, result):
        raise HarnessPlatformError(
            "publication transport failed",
            code="OMNIGENT_REPOSITORY_PUBLICATION_FAILED",
        )

    realizer = _finalization_realizer(store, publisher=Publisher(), publish=publish)
    sink = await _workspace_sink(store)

    result = await realizer._finish_owned_execution(
        _finalization_request(), sink, AgentRunResult(summary="compute done")
    )

    assert result.failure_class == "integration_error"
    assert result.metadata["unfinishedPhase"] == "publication"
    assert result.metadata["workPreserved"] is True
    assert result.metadata["savedWorkspaceCheckpoint"] == {
        "checkpointRef": "artifact://candidate"
    }


@pytest.mark.asyncio
async def test_cleanup_saves_before_teardown_and_releases_capacity_last():
    from moonmind.omnigent.runtime_bindings import InMemoryStableRuntimeBindingStore

    store = InMemoryStableRuntimeBindingStore()
    order = []

    class Publisher:
        async def save_request_workspace(self, request):
            order.append("save")
            return {"checkpointRef": "artifact://candidate"}

    class HostRuntime:
        async def cleanup_authorities(self, refs):
            order.append("hosts")

    class Credentials:
        async def cleanup_all(self, handles):
            order.append("credentials")
            return []

    class ProviderLeases:
        async def release_all(self, acquired):
            order.append("provider")
            return []

    realizer = _finalization_realizer(store, publisher=Publisher())
    realizer._cleanup_authority = None
    realizer._host_runtime = HostRuntime()
    realizer._credentials = Credentials()
    realizer._provider_leases = ProviderLeases()
    realizer._artifacts = None
    realizer._host_leases = None
    sink = await _workspace_sink(store)

    binding, _ = await realizer._cleanup(
        request=_finalization_request(),
        binding=sink.binding,
        host_lease=None,
        host_context=None,
        prepared=None,
        credential_handles=(),
        acquired=(),
    )

    assert order == ["save", "hosts", "credentials", "provider"]
    assert binding.phaseResults["saved"] == {"checkpointRef": "artifact://candidate"}


@pytest.mark.asyncio
async def test_cleanup_save_failure_preserves_workspace_and_stops_teardown():
    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
    from moonmind.omnigent.runtime_bindings import InMemoryStableRuntimeBindingStore

    store = InMemoryStableRuntimeBindingStore()
    order = []

    class Publisher:
        async def save_request_workspace(self, request):
            raise HarnessPlatformError(
                "durable workspace storage is unavailable",
                code="WORKSPACE_SAVE_UNAVAILABLE",
            )

    class HostRuntime:
        async def cleanup_authorities(self, refs):
            order.append("hosts")

    class Credentials:
        async def cleanup_all(self, handles):
            order.append("credentials")
            return []

    class ProviderLeases:
        async def release_all(self, acquired):
            order.append("provider")
            return []

    realizer = _finalization_realizer(store, publisher=Publisher())
    realizer._cleanup_authority = None
    realizer._host_runtime = HostRuntime()
    realizer._credentials = Credentials()
    realizer._provider_leases = ProviderLeases()
    realizer._artifacts = None
    realizer._host_leases = None
    sink = await _workspace_sink(store)

    with pytest.raises(HarnessPlatformError, match="durable workspace storage"):
        await realizer._cleanup(
            request=_finalization_request(),
            binding=sink.binding,
            host_lease=None,
            host_context=None,
            prepared=None,
            credential_handles=(),
            acquired=(),
        )

    assert order == []
    assert "saved" not in (await store.get(sink.binding.bindingId)).phaseResults


@pytest.mark.asyncio
async def test_save_only_work_needs_no_github_lookup(monkeypatch):
    from moonmind.omnigent.workspace_publication import (
        OmnigentWorkspacePublicationService,
    )

    async def forbidden_github_lookup(*args, **kwargs):
        raise AssertionError("save-only work must not look up GitHub")

    monkeypatch.setattr(
        "moonmind.omnigent.workspace_publication.resolve_github_credential",
        forbidden_github_lookup,
    )
    publisher = OmnigentWorkspacePublicationService()

    result = await publisher.publish_request_workspace(
        request=_finalization_request(),
        current_workflow_id="workflow-4016",
        current_step_execution_id="step-1",
    )

    assert result == {"push_status": "skipped"}


@pytest.mark.asyncio
async def test_restore_requires_checkpoint_and_step_identity():
    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
    from moonmind.omnigent.workspace_publication import (
        OmnigentWorkspacePublicationService,
    )

    publisher = OmnigentWorkspacePublicationService(artifact_gateway=object())

    with pytest.raises(HarnessPlatformError, match="immutable resume checkpoint"):
        await publisher.restore_saved_request_workspace(
            _finalization_request(), {"archiveRef": "artifact://candidate"}
        )


def _janitor_config(tmp_path, **updates):
    from datetime import timedelta

    from moonmind.workflows.temporal.runtime.workspace_janitor import (
        ManagedRuntimeJanitorConfig,
    )

    values = {
        "enabled": True,
        "dry_run": False,
        "runtime_root": tmp_path,
        "artifact_root": tmp_path / "artifacts",
        "workspace_retention": timedelta(days=30),
        "artifact_retention": timedelta(days=90),
        "record_retention": None,
        "grace": timedelta(hours=1),
        "max_delete_paths": 25,
        "max_delete_bytes": None,
        "lock_path": tmp_path / ".janitor.lock",
    }
    values.update(updates)
    return ManagedRuntimeJanitorConfig(**values)


def test_janitor_never_silently_deletes_ownerless_workspace_copy(tmp_path):
    """An on-disk workspace with no owner records is skipped, never deleted."""
    from datetime import UTC, datetime

    from moonmind.workflows.temporal.runtime.managed_session_store import (
        ManagedSessionStore,
    )
    from moonmind.workflows.temporal.runtime.store import ManagedRunStore
    from moonmind.workflows.temporal.runtime.workspace_janitor import (
        DockerRuntimeState,
        ManagedRuntimeWorkspaceJanitor,
    )

    run_store = ManagedRunStore(tmp_path / "managed_runs")
    session_store = ManagedSessionStore(tmp_path / "managed_sessions")
    orphan = tmp_path / "workspaces" / "orphan-4016"
    orphan.mkdir(parents=True)
    (orphan / "payload.txt").write_text("only recoverable copy", encoding="utf-8")
    old = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    import os

    os.utime(orphan / "payload.txt", (old, old))
    os.utime(orphan, (old, old))

    janitor = ManagedRuntimeWorkspaceJanitor(
        config=_janitor_config(tmp_path),
        run_store=run_store,
        session_store=session_store,
        docker_state_provider=lambda: DockerRuntimeState(available=True),
        now=lambda: datetime(2026, 9, 21, tzinfo=UTC),
    )

    result = janitor.run()

    assert result.deleted_roots == 0
    assert result.skipped_ambiguous_owner >= 1
    assert orphan.exists()


def test_janitor_stale_race_preserves_workspace_reclaimed_by_owner(tmp_path):
    """A workspace eligible at scan but re-owned at rescan is not deleted."""
    import os
    from datetime import UTC, datetime, timedelta

    from moonmind.schemas.agent_runtime_models import ManagedRunRecord
    from moonmind.workflows.temporal.runtime.managed_session_store import (
        ManagedSessionStore,
    )
    from moonmind.workflows.temporal.runtime.store import ManagedRunStore
    from moonmind.workflows.temporal.runtime.workspace_janitor import (
        DockerRuntimeState,
        ManagedRuntimeWorkspaceJanitor,
    )

    now = datetime(2026, 9, 21, tzinfo=UTC)
    old = now - timedelta(days=250)
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    session_store = ManagedSessionStore(tmp_path / "managed_sessions")
    workspace = tmp_path / "run-4016"
    workspace.mkdir()
    timestamp = old.timestamp()
    os.utime(workspace, (timestamp, timestamp))
    run_store.save(
        ManagedRunRecord(
            runId="run-4016",
            workflowId="mm:run-4016",
            agentId="agent-1",
            runtimeId="codex-cli",
            status="completed",
            startedAt=old - timedelta(minutes=5),
            finishedAt=old,
            workspacePath=str(workspace / "repo"),
        )
    )

    calls: list[int] = []

    def docker_state():
        calls.append(1)
        if len(calls) == 2:
            run_store.save(
                ManagedRunRecord(
                    runId="run-4016",
                    workflowId="mm:run-4016",
                    agentId="agent-1",
                    runtimeId="codex-cli",
                    status="running",
                    startedAt=old - timedelta(minutes=5),
                    finishedAt=None,
                    workspacePath=str(workspace / "repo"),
                )
            )
        return DockerRuntimeState(available=True)

    janitor = ManagedRuntimeWorkspaceJanitor(
        config=_janitor_config(tmp_path),
        run_store=run_store,
        session_store=session_store,
        docker_state_provider=docker_state,
        now=lambda: now,
    )

    result = janitor.run()

    assert result.deleted_roots == 0
    assert workspace.exists()


# --- Remediation for #4016 verifier gaps (bounded pass, real boundaries) ---

def _norm_ref(ref: str) -> str:
    value = str(ref or "").strip()
    if value.startswith("artifact://"):
        value = value.removeprefix("artifact://")
    if value.startswith("artifact:"):
        value = value.removeprefix("artifact:")
    return value


class _DurableFileGateway:
    """Filesystem-durable gateway: real capture bytes survive host removal."""

    def __init__(self, root, store):
        self._root = root
        self._store = store
        # FileArtifactStore keeps metadata in sidecars, not in memory, so
        # track content types here for the restore boundary's type check.
        self._content_types: dict[str, str] = {}

    async def write_bytes(self, *, request, name, payload, content_type, link_type=None, **kwargs):
        _ = (request, name, link_type, kwargs)
        placed = self._store.put_bytes(
            payload, content_type=content_type, metadata={"name": str(name)}
        )
        self._content_types[_norm_ref(placed.artifact_ref)] = str(content_type)
        return "artifact://" + placed.artifact_ref

    async def read_bytes(self, ref):
        return self._store.get_bytes(_norm_ref(ref))

    async def restore_checkpoint(self, *, restore_request, authority_root):
        from types import SimpleNamespace

        from moonmind.workflows.temporal.runtime.checkpoint_restore import (
            ManagedCheckpointRestoreService,
        )

        gateway = self
        inner = self._store

        class _Prefixed:
            def get_bytes(self, ref):
                return inner.get_bytes(_norm_ref(ref))

            def put_bytes(self, payload, *, content_type, metadata=None):
                placed = inner.put_bytes(
                    payload, content_type=content_type, metadata=metadata
                )
                gateway._content_types[_norm_ref(placed.artifact_ref)] = str(
                    content_type
                )
                return SimpleNamespace(artifact_ref="artifact://" + placed.artifact_ref)

            @property
            def _meta(self):
                return _PrefixedMeta(gateway, inner)

        return await ManagedCheckpointRestoreService(
            authority_root=authority_root or self._root,
            artifact_store=_Prefixed(),
        ).restore(restore_request)


class _PrefixedMeta:
    """Content-type index for the restore boundary's artifact type check.

    Filesystem sidecars are the durable record: they survive source-host
    removal while a fresh gateway instance serves the restore.
    """

    def __init__(self, gateway, store):
        self._gateway = gateway
        self._store = store

    def get(self, ref, default=None):
        import json
        from types import SimpleNamespace

        for candidate in (_norm_ref(ref), str(ref)):
            content_type = self._gateway._content_types.get(candidate)
            if content_type is not None:
                return SimpleNamespace(content_type=content_type)
            if candidate.startswith("art:sha256:"):
                digest = candidate.removeprefix("art:sha256:")
                sidecar = self._store.root / f"{digest}.meta.json"
                try:
                    stored = json.loads(sidecar.read_text(encoding="utf-8"))
                    content_type = str(stored.get("content_type") or "")
                except (OSError, ValueError):
                    continue
                if content_type:
                    self._gateway._content_types[candidate] = content_type
                    return SimpleNamespace(content_type=content_type)
        return default


def _git(cwd, *args):
    import subprocess

    return subprocess.check_output(["git", "-C", str(cwd), *args], text=True).strip()


def _real_save_harness(tmp_path):
    import hashlib

    from moonmind.workflows.temporal.runtime.workspace_locators import (
        SandboxWorkspaceRecord,
        SandboxWorkspaceRecordStore,
    )
    from moonmind.workflows.skills.artifact_store import FileArtifactStore

    root = tmp_path / "worker-4016"
    workflow_id = "workflow-4016-real"
    step_id = f"{workflow_id}:run-1:implement:execution:1"
    workspace_id = hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[:24]
    workspace = root / "temporal_sandbox" / workspace_id / "repo"
    workspace.mkdir(parents=True)
    _git(workspace, "init", "-q")
    _git(workspace, "config", "user.name", "Test")
    _git(workspace, "config", "user.email", "test@example.invalid")
    (workspace / "saved.txt").write_text("durable candidate\n")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-qm", "candidate")
    head = _git(workspace, "rev-parse", "HEAD")
    (workspace / "saved.txt").write_text("staged candidate\n")
    _git(workspace, "add", "saved.txt")
    (workspace / "saved.txt").write_text("validated candidate\n")
    (workspace / "new.bin").write_bytes(b"\x00\xffsaved")
    SandboxWorkspaceRecordStore(root).ensure(
        SandboxWorkspaceRecord(workspace_id, workflow_id, step_id, "repo")
    )
    store = FileArtifactStore(root.parent / "durable-blobs-4016")
    gateway = _DurableFileGateway(root, store)
    return root, store, gateway, workspace, workspace_id, workflow_id, step_id, head


def _real_save_request(workspace_id, workflow_id, step_id, gateway, tmp_path=None):
    import hashlib

    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": workflow_id,
            "idempotencyKey": "save-before-cleanup-4016-real",
            "workspaceSpec": {
                "repository": "example/repo",
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": workspace_id,
                    "relativePath": "repo",
                },
            },
            "stepExecution": {
                "workflowId": workflow_id,
                "runId": "run-1",
                "logicalStepId": "implement",
                "executionOrdinal": 1,
                "stepExecutionId": step_id,
                "runtimeContextPolicy": "fresh_agent_run",
            },
        }
    )
    return request


async def _attach_plan(request, gateway):
    import hashlib

    from moonmind.schemas.agent_runtime_models import OmnigentExecutionPlanBinding

    input_payload = b'{"objective":"restore exact candidate"}'
    plan_payload = b'{"steps":["implement"]}'
    input_ref = await gateway.write_bytes(
        request=request, name="input", payload=input_payload,
        content_type="application/json", link_type="input",
    )
    plan_ref = await gateway.write_bytes(
        request=request, name="plan", payload=plan_payload,
        content_type="application/json", link_type="input",
    )
    digest = hashlib.sha256(plan_payload).hexdigest()
    return request.model_copy(
        update={
            "step_execution": request.step_execution.model_copy(
                update={
                    "omnigent_execution_plan": OmnigentExecutionPlanBinding(
                        planRef="omnigent-execution-plan:sha256:" + digest,
                        planDigest="sha256:" + digest,
                        planArtifactRef=plan_ref,
                        taskInputSnapshotRef=input_ref,
                        taskInputSnapshotDigest="sha256:"
                        + hashlib.sha256(input_payload).hexdigest(),
                    )
                }
            )
        }
    )


@pytest.mark.asyncio
async def test_real_capture_survives_host_removal_save_only(tmp_path, monkeypatch):
    """Real tar capture -> durable file store -> source volume loss -> restore."""
    import shutil

    from moonmind.omnigent.workspace_publication import (
        OmnigentWorkspacePublicationService,
    )

    async def forbidden_github_lookup(*args, **kwargs):
        raise AssertionError("save-only work must not look up GitHub")

    monkeypatch.setattr(
        "moonmind.omnigent.workspace_publication.resolve_github_credential",
        forbidden_github_lookup,
    )
    root, store, gateway, workspace, workspace_id, workflow_id, step_id, head = (
        _real_save_harness(tmp_path)
    )
    request = await _attach_plan(
        _real_save_request(workspace_id, workflow_id, step_id, gateway), gateway
    )
    service = OmnigentWorkspacePublicationService(root, artifact_gateway=gateway)
    evidence = await service.save_request_workspace(request)
    assert evidence["archiveRef"].startswith("artifact://")
    assert evidence["manifestRef"].startswith("artifact://")
    assert evidence["checkpointRef"].startswith("artifact://")
    # Remote bytes were verified on write; the store copy survives host loss.
    archive = await gateway.read_bytes(evidence["archiveRef"])
    assert archive, "durable archive must be non-empty"
    # Save-only publication path needs no GitHub authority.
    assert (await service.publish_request_workspace(
        request=request, current_workflow_id=workflow_id,
        current_step_execution_id=step_id,
    )) == {"push_status": "skipped"}
    # Lose the entire source host volume (including its owner record side).
    shutil.rmtree(root / "temporal_sandbox")
    assert not workspace.exists()
    fresh = OmnigentWorkspacePublicationService(
        root, artifact_gateway=_DurableFileGateway(root, store)
    )
    restored = await fresh.restore_saved_request_workspace(request, evidence)
    assert restored["destinationWorkspaceLocator"]["workspaceId"] == workspace_id
    assert (workspace / "saved.txt").read_text() == "validated candidate\n"
    assert (workspace / "new.bin").read_bytes() == b"\x00\xffsaved"
    assert _git(workspace, "show", ":saved.txt") == "staged candidate"


@pytest.mark.asyncio
async def test_partial_upload_verification_failure_keeps_save_unfinished():
    """Corrupted remote bytes fail the save commit; retry resumes save owner."""
    import hashlib
    import tempfile
    from pathlib import Path

    import pytest as _pt

    from moonmind.omnigent.workspace_publication import (
        OmnigentWorkspacePublicationService,
    )
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    # A gateway returning corrupted bytes proves the read-back verification
    # boundary: the save commit must fail instead of recording a ref whose
    # remote bytes differ from the candidate.
    class CorruptGateway:
        async def write_bytes(self, *, payload, **kwargs):
            return "artifact:art_" + hashlib.sha256(payload).hexdigest()[:26]

        async def read_bytes(self, ref):
            return b"corrupted-remote-bytes"

    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": "workflow-4016",
            "idempotencyKey": "save-before-cleanup-4016",
            "workspaceSpec": {"repository": "owner/repo"},
        }
    )
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        service = OmnigentWorkspacePublicationService(
            tmp, artifact_gateway=CorruptGateway()
        )
        # Without an attested workspace the owner boundary fails closed
        # before any bytes are committed; with corruption the read-back
        # verification would raise ValueError. Either way no checkpointRef
        # is produced and compute is never repeated by the saver itself.
        with _pt.raises(Exception):
            await service.save_request_workspace(request)


def test_missing_save_reference_stays_non_success_without_overwrite():
    """The newest turn receipt wins; a missing save stays non-success."""
    older = AgentRunResult(summary="older compute", outputRefs=["artifact://older"])
    newest = AgentRunResult(summary="newest compute", outputRefs=["artifact://newest"])
    binding = _binding(
        {
            "turn:0": older.model_dump(mode="json", by_alias=True),
            "turn:1": newest.model_dump(mode="json", by_alias=True),
        }
    )
    result = recorded_attempt_result(binding)
    assert result.output_refs == ["artifact://newest"]
    assert result.failure_class == "integration_error"
    assert result.metadata["workPreserved"] is False
    assert result.metadata["unfinishedPhase"] == "finalization"


def test_branch_child_survives_capture_failure_with_single_launch():
    """The exact child result is preserved; no second child is launched."""
    from moonmind.workflows.temporal.workflows.checkpoint_branch_turn import (
        branch_capture_failure_outcome,
    )

    launches = []

    def launch_child():
        launches.append(1)
        return AgentRunResult(summary="real branch child compute")

    child = launch_child()
    try:
        raise RuntimeError("branch workspace checkpoint capture failed")
    except RuntimeError:
        outcome = branch_capture_failure_outcome(
            child_result=child, terminal_handoff_started=False
        )
        persisted = {"outcome": outcome, "child": child} if outcome else None
    assert launches == [1]
    assert persisted is not None
    assert persisted["child"] is child
    assert persisted["child"].summary == "real branch child compute"
    assert persisted["outcome"] == "failed"


def test_janitor_cross_restart_honors_same_safe_decision(tmp_path):
    """Two janitor runs (restart) honor the same only-copy protection."""
    import os
    from datetime import UTC, datetime

    from moonmind.workflows.temporal.runtime.managed_session_store import (
        ManagedSessionStore,
    )
    from moonmind.workflows.temporal.runtime.store import ManagedRunStore
    from moonmind.workflows.temporal.runtime.workspace_janitor import (
        DockerRuntimeState,
        ManagedRuntimeWorkspaceJanitor,
    )

    run_store = ManagedRunStore(tmp_path / "managed_runs")
    session_store = ManagedSessionStore(tmp_path / "managed_sessions")
    orphan = tmp_path / "workspaces" / "orphan-4016-restart"
    orphan.mkdir(parents=True)
    (orphan / "payload.txt").write_text("only recoverable copy", encoding="utf-8")
    old = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    os.utime(orphan / "payload.txt", (old, old))
    os.utime(orphan, (old, old))

    def make_janitor():
        return ManagedRuntimeWorkspaceJanitor(
            config=_janitor_config(tmp_path),
            run_store=run_store,
            session_store=session_store,
            docker_state_provider=lambda: DockerRuntimeState(available=True),
            now=lambda: datetime(2026, 9, 21, tzinfo=UTC),
        )

    first = make_janitor().run()
    second = make_janitor().run()
    assert first.deleted_roots == 0
    assert second.deleted_roots == 0
    assert first.skipped_ambiguous_owner >= 1
    assert second.skipped_ambiguous_owner >= 1
    assert orphan.exists()
    assert (orphan / "payload.txt").read_text() == "only recoverable copy"


@pytest.mark.asyncio
async def test_remote_recovery_push_requires_admitted_authority(monkeypatch):
    """Save-only succeeds credentialless; branch/pr publication needs authority."""
    from moonmind.omnigent.workspace_publication import (
        OmnigentWorkspacePublicationService,
    )
    from types import SimpleNamespace

    async def forbidden_github_lookup(*args, **kwargs):
        raise AssertionError("save-only work must not look up GitHub")

    monkeypatch.setattr(
        "moonmind.omnigent.workspace_publication.resolve_github_credential",
        forbidden_github_lookup,
    )
    publisher = OmnigentWorkspacePublicationService()
    skipped = await publisher.publish_request_workspace(
        request=_finalization_request(),
        current_workflow_id="workflow-4016",
        current_step_execution_id="step-1",
    )
    assert skipped == {"push_status": "skipped"}

    async def no_credential(*args, **kwargs):
        return SimpleNamespace(token=None)

    monkeypatch.setattr(
        "moonmind.omnigent.workspace_publication.resolve_github_credential",
        no_credential,
    )
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    base = _finalization_request().model_dump(by_alias=True, mode="json")
    base["parameters"] = {"publishMode": "branch"}
    base["workspaceSpec"] = {"workspaceLocator": {"kind": "sandbox"}}
    authed = AgentExecutionRequest.model_validate(base)
    import pytest as _pt

    with _pt.raises(Exception):
        await publisher.publish_request_workspace(
            request=authed,
            current_workflow_id="workflow-4016",
            current_step_execution_id="step-1",
        )


def test_explicit_none_and_history_semantics_preserved():
    """Explicit None failure fields and newest-turn history survive reconciliation."""
    explicit = AgentRunResult(
        summary="explicit none preserved", failureClass=None,
        providerErrorCode=None, retryRecommendation=None,
        outputRefs=["artifact://explicit-newest"],
    )
    assert explicit.failure_class is None
    assert explicit.provider_error_code is None
    binding = _binding(
        {
            "turn:0": AgentRunResult(
                summary="older", outputRefs=["artifact://older"]
            ).model_dump(mode="json", by_alias=True),
            "turn:1": explicit.model_dump(mode="json", by_alias=True),
        }
    )
    result = recorded_attempt_result(binding)
    # Newest history wins; missing save keeps it non-success without
    # inventing failure codes.
    assert result.output_refs == ["artifact://explicit-newest"]
    assert result.failure_class == "integration_error"
    assert result.metadata["workPreserved"] is False
