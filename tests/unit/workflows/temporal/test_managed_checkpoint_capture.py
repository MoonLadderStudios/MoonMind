from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import tarfile
import time
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from moonmind.schemas.agent_runtime_models import ManagedRunRecord
from moonmind.schemas.managed_checkpoint_models import ManagedWorkspaceCheckpointCaptureInput
from moonmind.workflows.executions.runtime_capabilities import resolve_runtime_execution_capabilities
from moonmind.workflows.temporal.activity_catalog import AGENT_RUNTIME_FLEET, build_default_activity_catalog
from moonmind.workflows.temporal import activity_runtime as activity_runtime_module
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.runtime.store import ManagedRunStore


def _request(*, digest: str) -> dict[str, object]:
    return {
        "schemaVersion": "v1",
        "identity": {"workflowId": "wf-1", "runId": "run-1", "logicalStepId": "implement", "executionOrdinal": 1},
        "boundary": "after_execution",
        "checkpointKind": "worktree_archive",
        "workspaceLocator": {"kind": "managed_runtime", "runtimeId": "codex_cli", "agentRunId": "agent-run-1", "relativePath": "repo"},
        "expectedRuntimeId": "codex_cli",
        "capabilitySetVersion": "runtime-execution-capabilities-v1",
        "capabilityDigest": digest,
        "artifactNamespace": "step-checkpoints/implement",
        "idempotencyKey": "checkpoint-1:capture",
        "capturePolicy": {"includeTracked": True, "includeUntracked": True, "includeIgnored": False, "redactionProfile": "managed-code-workspace-v1"},
    }


def test_managed_capture_contract_and_catalog_reject_other_authorities() -> None:
    digest = resolve_runtime_execution_capabilities("codex_cli").capability_digest
    ManagedWorkspaceCheckpointCaptureInput.model_validate(_request(digest=digest))
    invalid = _request(digest=digest)
    invalid["workspaceLocator"] = {"kind": "sandbox", "workspaceId": "sandbox-1"}
    with pytest.raises(ValidationError):
        ManagedWorkspaceCheckpointCaptureInput.model_validate(invalid)
    route = build_default_activity_catalog().resolve_activity(
        "agent_runtime.capture_workspace_checkpoint"
    )
    assert route.fleet == AGENT_RUNTIME_FLEET
    assert route.task_queue == "mm.activity.agent_runtime"


@pytest.mark.asyncio
async def test_managed_checkpoint_artifact_uses_content_addressed_writer() -> None:
    calls: list[tuple[str, bytes, str]] = []

    class ArtifactService:
        async def put_content_addressed_payload_complete(
            self, *, payload: bytes, scope: str, **_kwargs: object
        ) -> tuple[object, bool]:
            calls.append(("art-checkpoint", payload, scope))
            artifact = SimpleNamespace(
                artifact_id="art-checkpoint",
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
                content_type="application/vnd.moonmind.worktree-archive",
                encryption=SimpleNamespace(value="none"),
            )
            return artifact, False

    activities = TemporalAgentRuntimeActivities(
        run_store=object(),
        artifact_service=ArtifactService(),
        client_adapter=object(),
    )
    payload = b"large checkpoint payload"

    artifact_ref = await activities._put_managed_checkpoint_artifact(
        payload,
        "application/vnd.moonmind.worktree-archive",
        "checkpoint_archive",
    )

    assert artifact_ref == "art-checkpoint"
    assert calls == [("art-checkpoint", payload, "checkpoint_archive")]


@pytest.mark.asyncio
async def test_managed_capture_trusts_the_resolved_workspace_for_every_git_command(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "agent-run-1" / "repo"
    repo.mkdir(parents=True)
    (repo / "tracked.txt").write_text("checkpoint evidence\n")
    resolved_repo = str(repo.resolve())
    expected_prefix = [
        "git",
        "-c",
        f"safe.directory={resolved_repo}",
        "-C",
        resolved_repo,
    ]
    commands: list[list[str]] = []

    async def run_git(command, **_kwargs):
        normalized = [str(part) for part in command]
        commands.append(normalized)
        if normalized[: len(expected_prefix)] != expected_prefix:
            raise RuntimeError(
                f"fatal: detected dubious ownership in repository at '{resolved_repo}'"
            )
        operation = normalized[len(expected_prefix) :]
        if operation[0] == "ls-files":
            return SimpleNamespace(stdout="tracked.txt\0")
        if operation[:2] == ["rev-parse", "HEAD"]:
            return SimpleNamespace(stdout="abc123\n")
        if operation[:2] == ["branch", "--show-current"]:
            return SimpleNamespace(stdout="main\n")
        if operation[0] == "status":
            return SimpleNamespace(stdout="")
        raise AssertionError(f"unexpected git command: {operation}")

    monkeypatch.setattr(activity_runtime_module, "_run_command", run_git)
    activities = TemporalAgentRuntimeActivities(
        run_store=object(), artifact_service=object(), client_adapter=object()
    )

    async def put(payload: bytes, _content_type: str, _kind: str) -> str:
        return "artifact://" + hashlib.sha256(payload).hexdigest()

    activities._put_managed_checkpoint_artifact = put
    model = ManagedWorkspaceCheckpointCaptureInput.model_validate(
        _request(
            digest=resolve_runtime_execution_capabilities(
                "codex_cli"
            ).capability_digest
        )
    )
    now = datetime.now(UTC)

    result = await activities._capture_managed_worktree(
        model,
        repo,
        SimpleNamespace(started_at=now, finished_at=now),
    )

    assert result["workspace"]["archiveRef"].startswith("artifact://")
    assert len(commands) == 4
    assert all(command[: len(expected_prefix)] == expected_prefix for command in commands)


@pytest.mark.asyncio
async def test_managed_capture_is_binary_safe_and_idempotent(tmp_path) -> None:
    repo = tmp_path / "agent-run-1" / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    executable = repo / "tracked.sh"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    subprocess.run(["git", "add", "tracked.sh"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base"],
        cwd=repo,
        check=True,
    )
    (repo / "binary.bin").write_bytes(b"\x00\xff\x01")
    os.symlink("tracked.sh", repo / "safe-link")
    store = ManagedRunStore(tmp_path / "managed_runs")
    store.save(
        ManagedRunRecord(
            runId="agent-run-1", workflowId="wf-1", agentId="codex_cli",
            ownerRunId="run-1", logicalStepId="implement", executionOrdinal=1,
            runtimeId="codex_cli", status="completed", startedAt=datetime.now(UTC),
            finishedAt=datetime.now(UTC), workspacePath=str(repo),
        )
    )
    activities = TemporalAgentRuntimeActivities(
        run_store=store, artifact_service=object(), client_adapter=object()
    )
    artifacts: dict[str, bytes] = {}

    async def put(payload: bytes, _content_type: str, _kind: str) -> str:
        ref = "artifact://" + hashlib.sha256(payload).hexdigest()
        artifacts[ref] = payload
        return ref

    activities._put_managed_checkpoint_artifact = put
    request = _request(
        digest=resolve_runtime_execution_capabilities("codex_cli").capability_digest
    )
    first = await activities.agent_runtime_capture_workspace_checkpoint(request)
    assert first["workspace"]["workspaceIdentityDigest"].startswith("sha256:")
    assert first["workspace"]["archiveBytes"] == len(
        artifacts[first["workspace"]["archiveRef"]]
    )
    (repo / "binary.bin").write_bytes(b"changed after capture")
    assert await activities.agent_runtime_capture_workspace_checkpoint(request) == first
    with tarfile.open(
        fileobj=BytesIO(artifacts[first["workspace"]["archiveRef"]]),
        mode="r:gz",
    ) as archive:
        assert archive.extractfile("binary.bin").read() == b"\x00\xff\x01"
        assert archive.getmember("tracked.sh").mode & 0o111
        assert archive.getmember("safe-link").issym()


@pytest.mark.asyncio
async def test_managed_capture_reuses_pre_source_identity_idempotency_record(
    tmp_path,
) -> None:
    store = ManagedRunStore(tmp_path / "managed_runs")
    activities = TemporalAgentRuntimeActivities(
        run_store=store, artifact_service=object(), client_adapter=object()
    )
    request = _request(
        digest=resolve_runtime_execution_capabilities("codex_cli").capability_digest
    )
    model = ManagedWorkspaceCheckpointCaptureInput.model_validate(request)
    legacy_immutable = model.model_dump(
        by_alias=True,
        mode="json",
        exclude={"source_identity"},
    )
    immutable_digest = hashlib.sha256(
        activity_runtime_module._json_bytes(legacy_immutable)
    ).hexdigest()
    expected = {
        "status": "captured",
        "idempotencyKey": model.idempotency_key,
    }
    record_root = store.store_root / "checkpoint_captures"
    record_root.mkdir(parents=True)
    record_name = hashlib.sha256(model.idempotency_key.encode()).hexdigest()
    (record_root / f"{record_name}.json").write_text(
        json.dumps(
            {"immutableDigest": immutable_digest, "result": expected}
        ),
        encoding="utf-8",
    )

    assert (
        await activities.agent_runtime_capture_workspace_checkpoint(request)
        == expected
    )


@pytest.mark.asyncio
async def test_managed_capture_coalesces_temporal_heartbeat_backpressure(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "agent-run-1" / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("checkpoint evidence\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(
        [
            "git", "-c", "user.name=test", "-c",
            "user.email=test@example.invalid", "commit", "-qm", "base",
        ],
        cwd=repo,
        check=True,
    )
    now = datetime.now(UTC)
    store = ManagedRunStore(tmp_path / "managed_runs")
    store.save(
        ManagedRunRecord(
            runId="agent-run-1", workflowId="wf-1", agentId="codex_cli",
            ownerRunId="run-1", logicalStepId="implement", executionOrdinal=1,
            runtimeId="codex_cli", status="completed", startedAt=now,
            finishedAt=now, workspacePath=str(repo),
        )
    )
    activities = TemporalAgentRuntimeActivities(
        run_store=store, artifact_service=object(), client_adapter=object()
    )

    async def put(payload: bytes, _content_type: str, _kind: str) -> str:
        await asyncio.sleep(0.01)
        return "artifact://" + hashlib.sha256(payload).hexdigest()

    heartbeat_attempts = 0

    def heartbeat(_details: object) -> None:
        nonlocal heartbeat_attempts
        heartbeat_attempts += 1
        raise asyncio.QueueFull

    activities._put_managed_checkpoint_artifact = put
    monkeypatch.setattr(activity_runtime_module.temporal_activity, "in_activity", lambda: True)
    monkeypatch.setattr(activity_runtime_module.temporal_activity, "heartbeat", heartbeat)
    monkeypatch.setattr(
        activity_runtime_module,
        "_SESSION_CONTROLLER_HEARTBEAT_INTERVAL_SECONDS",
        0.001,
    )

    result = await activities.agent_runtime_capture_workspace_checkpoint(
        _request(
            digest=resolve_runtime_execution_capabilities(
                "codex_cli"
            ).capability_digest
        )
    )

    assert result["status"] == "captured"
    assert heartbeat_attempts > 0


@pytest.mark.asyncio
async def test_managed_capture_heartbeats_during_slow_archive_file(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "agent-run-1" / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("checkpoint evidence\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(
        [
            "git", "-c", "user.name=test", "-c",
            "user.email=test@example.invalid", "commit", "-qm", "base",
        ],
        cwd=repo,
        check=True,
    )
    now = datetime.now(UTC)
    store = ManagedRunStore(tmp_path / "managed_runs")
    store.save(
        ManagedRunRecord(
            runId="agent-run-1", workflowId="wf-1", agentId="codex_cli",
            ownerRunId="run-1", logicalStepId="implement", executionOrdinal=1,
            runtimeId="codex_cli", status="completed", startedAt=now,
            finishedAt=now, workspacePath=str(repo),
        )
    )
    activities = TemporalAgentRuntimeActivities(
        run_store=store, artifact_service=object(), client_adapter=object()
    )

    async def put(payload: bytes, _content_type: str, _kind: str) -> str:
        return "artifact://" + hashlib.sha256(payload).hexdigest()

    original_addfile = tarfile.TarFile.addfile
    archive_write_active = False
    heartbeat_during_archive = False

    def slow_addfile(self, *args, **kwargs) -> None:
        nonlocal archive_write_active
        archive_write_active = True
        try:
            time.sleep(0.03)
            original_addfile(self, *args, **kwargs)
        finally:
            archive_write_active = False

    def heartbeat(_details: object) -> None:
        nonlocal heartbeat_during_archive
        heartbeat_during_archive = (
            heartbeat_during_archive or archive_write_active
        )

    activities._put_managed_checkpoint_artifact = put
    monkeypatch.setattr(tarfile.TarFile, "addfile", slow_addfile)
    monkeypatch.setattr(activity_runtime_module.temporal_activity, "in_activity", lambda: True)
    monkeypatch.setattr(activity_runtime_module.temporal_activity, "heartbeat", heartbeat)
    monkeypatch.setattr(
        activity_runtime_module,
        "_SESSION_CONTROLLER_HEARTBEAT_INTERVAL_SECONDS",
        0.001,
    )

    result = await activities.agent_runtime_capture_workspace_checkpoint(
        _request(
            digest=resolve_runtime_execution_capabilities(
                "codex_cli"
            ).capability_digest
        )
    )

    assert result["status"] == "captured"
    assert heartbeat_during_archive is True


@pytest.mark.asyncio
async def test_managed_capture_archive_digest_is_deterministic(tmp_path) -> None:
    repo = tmp_path / "agent-run-1" / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("stable\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base"],
        cwd=repo,
        check=True,
    )
    now = datetime.now(UTC)
    store = ManagedRunStore(tmp_path / "managed_runs")
    store.save(
        ManagedRunRecord(
            runId="agent-run-1", workflowId="wf-1", agentId="codex_cli",
            ownerRunId="run-1", logicalStepId="implement", executionOrdinal=1,
            runtimeId="codex_cli", status="completed", startedAt=now,
            finishedAt=now, workspacePath=str(repo),
        )
    )
    activities = TemporalAgentRuntimeActivities(
        run_store=store, artifact_service=object(), client_adapter=object()
    )

    async def put(payload: bytes, _content_type: str, _kind: str) -> str:
        return "artifact://" + hashlib.sha256(payload).hexdigest()

    activities._put_managed_checkpoint_artifact = put
    digest = resolve_runtime_execution_capabilities("codex_cli").capability_digest
    first = await activities.agent_runtime_capture_workspace_checkpoint(
        _request(digest=digest)
    )
    second_request = _request(digest=digest)
    second_request["idempotencyKey"] = "checkpoint-2:capture"
    second = await activities.agent_runtime_capture_workspace_checkpoint(second_request)
    assert first["workspace"]["archiveDigest"] == second["workspace"]["archiveDigest"]
    assert first["workspace"]["workspaceDigest"] == second["workspace"]["workspaceDigest"]


@pytest.mark.asyncio
async def test_managed_capture_skips_deleted_sensitive_and_gitlink_paths(tmp_path) -> None:
    repo = tmp_path / "agent-run-1" / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "kept.txt").write_text("kept")
    (repo / "deleted.txt").write_text("deleted")
    subprocess.run(["git", "add", "kept.txt", "deleted.txt"], cwd=repo, check=True)
    subprocess.run(
        [
            "git", "-c", "user.name=test", "-c",
            "user.email=test@example.invalid", "commit", "-qm", "base",
        ],
        cwd=repo,
        check=True,
    )
    (repo / "deleted.txt").unlink()
    (repo / "credentials").mkdir()
    (repo / "credentials" / "token.json").write_text("secret")
    (repo / ".agents" / "skills").mkdir(parents=True)
    (repo / ".agents" / "skills" / "runtime.txt").write_text("runtime")
    (repo / "nested-repo").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo / "nested-repo", check=True)
    subprocess.run(
        [
            "git", "-c", "user.name=test", "-c",
            "user.email=test@example.invalid", "commit", "--allow-empty",
            "-qm", "base",
        ],
        cwd=repo / "nested-repo",
        check=True,
    )
    subprocess.run(
        [
            "git", "-c", "protocol.file.allow=always", "submodule", "add",
            "./nested-repo", "module",
        ],
        cwd=repo,
        check=True,
    )
    store = ManagedRunStore(tmp_path / "managed_runs")
    store.save(
        ManagedRunRecord(
            runId="agent-run-1", workflowId="wf-1", agentId="codex_cli",
            ownerRunId="run-1", logicalStepId="implement", executionOrdinal=1,
            runtimeId="codex_cli", status="completed", startedAt=datetime.now(UTC),
            finishedAt=datetime.now(UTC), workspacePath=str(repo),
        )
    )
    activities = TemporalAgentRuntimeActivities(
        run_store=store, artifact_service=object(), client_adapter=object()
    )
    artifacts: dict[str, bytes] = {}

    async def put(payload: bytes, _content_type: str, _kind: str) -> str:
        ref = "artifact://" + hashlib.sha256(payload).hexdigest()
        artifacts[ref] = payload
        return ref

    activities._put_managed_checkpoint_artifact = put
    result = await activities.agent_runtime_capture_workspace_checkpoint(
        _request(
            digest=resolve_runtime_execution_capabilities(
                "codex_cli"
            ).capability_digest
        )
    )
    with tarfile.open(
        fileobj=BytesIO(artifacts[result["workspace"]["archiveRef"]]),
        mode="r:gz",
    ) as archive:
        assert set(archive.getnames()) == {".gitmodules", "kept.txt"}


@pytest.mark.asyncio
async def test_managed_capture_accepts_session_record_bound_to_parent_workflow(
    tmp_path,
) -> None:
    repo = tmp_path / "wf-1" / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        [
            "git", "-c", "user.name=test", "-c",
            "user.email=test@example.invalid", "commit", "--allow-empty",
            "-qm", "base",
        ],
        cwd=repo,
        check=True,
    )
    store = ManagedRunStore(tmp_path / "managed_runs")
    store.save(
        ManagedRunRecord(
            runId="wf-1",
            workflowId="wf-1:agent:implement",
            sessionId="session-1",
            ownerRunId="run-1",
            logicalStepId="implement",
            executionOrdinal=1,
            agentId="codex_cli",
            runtimeId="codex_cli",
            status="completed",
            startedAt=datetime.now(UTC),
            finishedAt=datetime.now(UTC),
            workspacePath=str(repo),
        )
    )
    activities = TemporalAgentRuntimeActivities(
        run_store=store, artifact_service=object(), client_adapter=object()
    )

    async def put(payload: bytes, _content_type: str, _kind: str) -> str:
        return "artifact://" + hashlib.sha256(payload).hexdigest()

    activities._put_managed_checkpoint_artifact = put
    request = _request(
        digest=resolve_runtime_execution_capabilities("codex_cli").capability_digest
    )
    request["workspaceLocator"] = {
        "kind": "managed_runtime",
        "runtimeId": "codex_cli",
        "agentRunId": "wf-1",
        "relativePath": "repo",
    }
    result = await activities.agent_runtime_capture_workspace_checkpoint(request)

    assert result["status"] == "captured"
    assert result["sourceWorkspaceLocator"] == {
        "kind": "managed_runtime",
        "runtimeId": "codex_cli",
        "agentRunId": "wf-1",
        "relativePath": "repo",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["after_prepare", "before_execution"])
async def test_managed_capture_accepts_cross_step_workflow_session_baseline(
    tmp_path, boundary: str,
) -> None:
    repo = tmp_path / "managed_runs" / "wf-1" / "custom-checkout"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        [
            "git", "-c", "user.name=test", "-c",
            "user.email=test@example.invalid", "commit", "--allow-empty",
            "-qm", "base",
        ],
        cwd=repo,
        check=True,
    )
    now = datetime.now(UTC)
    store = ManagedRunStore(tmp_path / "managed_runs")
    store.save(
        ManagedRunRecord(
            runId="wf-1",
            workflowId="wf-1:agent:verify",
            sessionId="sess:wf-1:codex_cli",
            ownerRunId="source-run",
            logicalStepId="verify",
            executionOrdinal=1,
            agentId="codex_cli",
            runtimeId="codex_cli",
            status="completed",
            startedAt=now,
            finishedAt=now,
            workspacePath=str(repo),
        )
    )
    activities = TemporalAgentRuntimeActivities(
        run_store=store, artifact_service=object(), client_adapter=object()
    )

    async def put(payload: bytes, _content_type: str, _kind: str) -> str:
        return "artifact://" + hashlib.sha256(payload).hexdigest()

    activities._put_managed_checkpoint_artifact = put
    request = _request(
        digest=resolve_runtime_execution_capabilities(
            "codex_cli"
        ).capability_digest
    )
    request["identity"] = {
        "workflowId": "wf-1",
        "runId": "continued-run",
        "logicalStepId": "remediation-1",
        "executionOrdinal": 1,
    }
    request["boundary"] = boundary
    request["workspaceLocator"] = {
        "kind": "managed_runtime",
        "runtimeId": "codex_cli",
        "agentRunId": "wf-1",
        "relativePath": ".",
    }
    request["idempotencyKey"] = f"remediation-1:{boundary}:capture"

    result = await activities.agent_runtime_capture_workspace_checkpoint(request)

    assert result["status"] == "captured"
    assert result["sourceWorkspaceLocator"]["relativePath"] == "."


@pytest.mark.asyncio
async def test_managed_capture_accepts_terminal_prior_execution_as_retry_baseline(
    tmp_path,
) -> None:
    repo = tmp_path / "agent-run-1" / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        [
            "git", "-c", "user.name=test", "-c",
            "user.email=test@example.invalid", "commit", "--allow-empty",
            "-qm", "base",
        ],
        cwd=repo,
        check=True,
    )
    now = datetime.now(UTC)
    store = ManagedRunStore(tmp_path / "managed_runs")
    store.save(
        ManagedRunRecord(
            runId="agent-run-1",
            workflowId="wf-1",
            ownerRunId="run-1",
            logicalStepId="implement",
            executionOrdinal=1,
            agentId="codex_cli",
            runtimeId="codex_cli",
            status="completed",
            startedAt=now,
            finishedAt=now,
            workspacePath=str(repo),
        )
    )
    activities = TemporalAgentRuntimeActivities(
        run_store=store, artifact_service=object(), client_adapter=object()
    )

    async def put(payload: bytes, _content_type: str, _kind: str) -> str:
        return "artifact://" + hashlib.sha256(payload).hexdigest()

    activities._put_managed_checkpoint_artifact = put
    request = _request(
        digest=resolve_runtime_execution_capabilities(
            "codex_cli"
        ).capability_digest
    )
    request["identity"] = {
        "workflowId": "wf-1",
        "runId": "run-1",
        "logicalStepId": "implement",
        "executionOrdinal": 2,
    }
    request["boundary"] = "before_execution"
    request["idempotencyKey"] = "checkpoint-2:before_execution:capture"

    result = await activities.agent_runtime_capture_workspace_checkpoint(request)

    assert result["status"] == "captured"
    assert result["workspace"]["kind"] == "worktree_archive"


@pytest.mark.asyncio
async def test_managed_capture_accepts_terminal_verifier_as_remediation_baseline(
    tmp_path,
) -> None:
    repo = tmp_path / "managed_runs" / "wf-1" / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        [
            "git", "-c", "user.name=test", "-c",
            "user.email=test@example.invalid", "commit", "--allow-empty",
            "-qm", "base",
        ],
        cwd=repo,
        check=True,
    )
    now = datetime.now(UTC)
    store = ManagedRunStore(tmp_path / "managed_runs")
    store.save(
        ManagedRunRecord(
            runId="wf-1",
            workflowId="wf-1:agent:verify",
            sessionId="sess:wf-1:codex_cli",
            ownerRunId="source-run",
            logicalStepId="initial-verification",
            executionOrdinal=1,
            agentId="codex_cli",
            runtimeId="codex_cli",
            status="completed",
            startedAt=now,
            finishedAt=now,
            workspacePath=str(repo),
        )
    )
    activities = TemporalAgentRuntimeActivities(
        run_store=store, artifact_service=object(), client_adapter=object()
    )

    async def put(payload: bytes, _content_type: str, _kind: str) -> str:
        return "artifact://" + hashlib.sha256(payload).hexdigest()

    activities._put_managed_checkpoint_artifact = put
    request = _request(
        digest=resolve_runtime_execution_capabilities("codex_cli").capability_digest
    )
    request["identity"] = {
        "workflowId": "wf-1",
        "runId": "continued-run",
        "logicalStepId": "remediation-1",
        "executionOrdinal": 1,
    }
    request["sourceIdentity"] = {
        "workflowId": "wf-1",
        "runId": "source-run",
        "logicalStepId": "initial-verification",
        "executionOrdinal": 1,
    }
    request["boundary"] = "before_execution"
    request["workspaceLocator"] = {
        "kind": "managed_runtime",
        "runtimeId": "codex_cli",
        "agentRunId": "wf-1",
        "relativePath": ".",
    }
    request["idempotencyKey"] = "remediation-1:before_execution:capture"

    result = await activities.agent_runtime_capture_workspace_checkpoint(request)

    assert result["status"] == "captured"
    assert result["workspace"]["kind"] == "worktree_archive"

    request["sourceIdentity"] = {
        **request["sourceIdentity"],
        "runId": "unrelated-run",
    }
    request["idempotencyKey"] = "remediation-1:wrong-source:capture"
    with pytest.raises(Exception, match="source Step Execution"):
        await activities.agent_runtime_capture_workspace_checkpoint(request)


@pytest.mark.asyncio
async def test_managed_capture_rejects_active_prior_execution_as_retry_baseline(
    tmp_path,
) -> None:
    repo = tmp_path / "agent-run-1" / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    store = ManagedRunStore(tmp_path / "managed_runs")
    store.save(
        ManagedRunRecord(
            runId="agent-run-1",
            workflowId="wf-1",
            ownerRunId="run-1",
            logicalStepId="implement",
            executionOrdinal=1,
            agentId="codex_cli",
            runtimeId="codex_cli",
            status="running",
            startedAt=datetime.now(UTC),
            workspacePath=str(repo),
        )
    )
    activities = TemporalAgentRuntimeActivities(
        run_store=store, artifact_service=object(), client_adapter=object()
    )
    request = _request(
        digest=resolve_runtime_execution_capabilities(
            "codex_cli"
        ).capability_digest
    )
    request["identity"] = {
        "workflowId": "wf-1",
        "runId": "run-1",
        "logicalStepId": "implement",
        "executionOrdinal": 2,
    }
    request["boundary"] = "before_execution"
    request["idempotencyKey"] = "checkpoint-2:before_execution:capture"

    with pytest.raises(Exception, match="source Step Execution"):
        await activities.agent_runtime_capture_workspace_checkpoint(request)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workflowId", "other-workflow"),
        ("ownerRunId", "other-run"),
        ("logicalStepId", "other-step"),
        ("executionOrdinal", 2),
    ],
)
async def test_managed_capture_rejects_step_execution_mismatch(
    tmp_path, field: str, value: object
) -> None:
    repo = tmp_path / "agent-run-1" / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "base"],
        cwd=repo,
        check=True,
    )
    record = {
        "runId": "agent-run-1", "workflowId": "wf-1", "ownerRunId": "run-1",
        "logicalStepId": "implement", "executionOrdinal": 1, "agentId": "codex_cli",
        "runtimeId": "codex_cli", "status": "completed", "startedAt": datetime.now(UTC),
        "workspacePath": str(repo),
    }
    record[field] = value
    store = ManagedRunStore(tmp_path / "managed_runs")
    store.save(ManagedRunRecord(**record))
    activities = TemporalAgentRuntimeActivities(
        run_store=store, artifact_service=object(), client_adapter=object()
    )
    with pytest.raises(Exception, match="source Step Execution"):
        await activities.agent_runtime_capture_workspace_checkpoint(
            _request(digest=resolve_runtime_execution_capabilities("codex_cli").capability_digest)
        )
