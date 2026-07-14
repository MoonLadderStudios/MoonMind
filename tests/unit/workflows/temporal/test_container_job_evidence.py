"""Bounded logs/artifacts/status/diagnostics coverage for MoonMind#3258."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

import moonmind.workflows.temporal.container_job_backend as backend_module
from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobEvidenceManifest,
    ContainerJobLogEntry,
    ContainerJobWorkflowInput,
    EvidenceCollectionStatus,
)
from moonmind.workflows.temporal.container_job_backend import (
    ContainerJobWorkspaceNotVisibleError,
    DockerContainerJobBackend,
)

JOB_ID = "container-job:0123456789abcdef0123456789abcdef"


def _request(tmp_path, *, outputs=None, workspace_dir="art_workspace"):
    workspace = tmp_path / workspace_dir
    workspace.mkdir(exist_ok=True)
    raw = {
        "jobId": JOB_ID,
        "request": {
            "idempotencyKey": "issue-3258",
            "source": {"source": "workflow", "workflowId": "mm:3258"},
            "spec": {
                "image": "python:3.13",
                "workspaceRef": {"kind": "external_state", "artifactRef": "art_workspace"},
                "command": ["python", "-V"],
                "resources": {"cpuMillis": 1000, "memoryMiB": 512},
                "timeoutSeconds": 60,
                "outputs": outputs or [],
            },
        },
    }
    inp = ContainerJobWorkflowInput.model_validate(raw)
    return (
        ContainerJobActivityRequest(
            jobId=JOB_ID,
            ownershipToken=inp.ownership_token,
            request=inp.request,
            resolvedWorkspaceRef=str(workspace),
            containerRef="owned:3258",
            state="succeeded",
            terminalState="succeeded",
            exitCode=0,
        ),
        workspace,
    )


class _Recorder:
    """Records every published evidence artifact by declared name."""

    def __init__(self) -> None:
        self.published: dict[str, tuple[bytes, str]] = {}

    async def __call__(self, request, name, payload, content_type="text/plain"):
        self.published[name] = (payload, content_type)
        return f"art:{name}"


@pytest.mark.asyncio
async def test_publish_evidence_emits_separate_streams_and_manifest(tmp_path) -> None:
    request, workspace = _request(
        tmp_path, outputs=[{"name": "report", "relativePath": "out/report.txt"}]
    )
    (workspace / "out").mkdir()
    (workspace / "out" / "report.txt").write_text("done")
    recorder = _Recorder()

    async def runner(args):
        args = tuple(args)
        if args[0] == "logs":
            return 0, b"hello stdout\ntoken=ghp_" + b"a" * 36 + b"\n", b"warn stderr\n"
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=runner, evidence_publisher=recorder
    )
    result = await backend.publish_evidence(request)

    names = set(recorder.published)
    assert names == {
        f"{JOB_ID}-stdout.log",
        f"{JOB_ID}-stderr.log",
        f"{JOB_ID}-diagnostics.json",
        f"{JOB_ID}-outputs.tar.gz",
        f"{JOB_ID}-manifest.json",
    }
    # Secret redaction is applied before durable persistence (AC11).
    stdout_payload, stdout_type = recorder.published[f"{JOB_ID}-stdout.log"]
    assert b"ghp_" not in stdout_payload and b"[REDACTED]" in stdout_payload
    assert stdout_type == "text/plain"

    # logs_ref points at stdout; artifacts_ref/manifest_ref index everything (AC5).
    assert result.logs_ref == f"art:{JOB_ID}-stdout.log"
    assert result.manifest_ref == f"art:{JOB_ID}-manifest.json"
    assert result.artifacts_ref == result.manifest_ref
    assert result.diagnostics_ref == f"art:{JOB_ID}-diagnostics.json"

    manifest_bytes, manifest_type = recorder.published[f"{JOB_ID}-manifest.json"]
    assert manifest_type == "application/json"
    manifest = ContainerJobEvidenceManifest.model_validate_json(manifest_bytes)
    kinds = {(e.kind, e.name): e for e in manifest.entries}
    assert ("stdout", "stdout.log") in kinds
    assert ("stderr", "stderr.log") in kinds
    assert ("diagnostics", "diagnostics.json") in kinds
    output_entry = kinds[("output", "report")]
    assert output_entry.relative_path == "out/report.txt"
    assert output_entry.size_bytes == 4
    assert output_entry.sha256 is not None
    assert output_entry.media_type == "text/plain"
    assert output_entry.collection_status == EvidenceCollectionStatus.COLLECTED
    assert output_entry.artifact_ref == f"art:{JOB_ID}-outputs.tar.gz"


@pytest.mark.asyncio
async def test_missing_output_is_recorded_not_fatal(tmp_path) -> None:
    request, _ = _request(
        tmp_path, outputs=[{"name": "absent", "relativePath": "never/there.txt"}]
    )
    recorder = _Recorder()

    async def runner(args):
        args = tuple(args)
        if args[0] == "logs":
            return 0, b"ok\n", b""
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=runner, evidence_publisher=recorder
    )
    result = await backend.publish_evidence(request)

    # A missing declared output does not fail publication (AC7/AC8); it is a
    # manifest entry and no outputs bundle is emitted.
    assert f"{JOB_ID}-outputs.tar.gz" not in recorder.published
    assert result.manifest_ref
    manifest = ContainerJobEvidenceManifest.model_validate_json(
        recorder.published[f"{JOB_ID}-manifest.json"][0]
    )
    absent = next(e for e in manifest.entries if e.name == "absent")
    assert absent.collection_status == EvidenceCollectionStatus.MISSING


@pytest.mark.asyncio
async def test_symlink_escape_is_rejected(tmp_path) -> None:
    request, workspace = _request(
        tmp_path, outputs=[{"name": "escape", "relativePath": "escape.txt"}]
    )
    secret = tmp_path / "outside.txt"
    secret.write_text("private")
    (workspace / "escape.txt").symlink_to(secret)
    recorder = _Recorder()

    async def runner(args):
        args = tuple(args)
        if args[0] == "logs":
            return 0, b"ok\n", b""
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=runner, evidence_publisher=recorder
    )
    await backend.publish_evidence(request)

    assert f"{JOB_ID}-outputs.tar.gz" not in recorder.published
    manifest = ContainerJobEvidenceManifest.model_validate_json(
        recorder.published[f"{JOB_ID}-manifest.json"][0]
    )
    escape = next(e for e in manifest.entries if e.name == "escape")
    assert escape.collection_status == EvidenceCollectionStatus.REJECTED


@pytest.mark.asyncio
async def test_output_size_ceiling_is_enforced(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(backend_module, "MAX_OUTPUT_FILE_BYTES", 4)
    request, workspace = _request(
        tmp_path, outputs=[{"name": "big", "relativePath": "big.bin"}]
    )
    (workspace / "big.bin").write_bytes(b"x" * 16)
    recorder = _Recorder()

    async def runner(args):
        args = tuple(args)
        if args[0] == "logs":
            return 0, b"ok\n", b""
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=runner, evidence_publisher=recorder
    )
    await backend.publish_evidence(request)

    manifest = ContainerJobEvidenceManifest.model_validate_json(
        recorder.published[f"{JOB_ID}-manifest.json"][0]
    )
    big = next(e for e in manifest.entries if e.name == "big")
    assert big.collection_status == EvidenceCollectionStatus.REJECTED
    assert "size" in (big.detail or "")


@pytest.mark.asyncio
async def test_incremental_capture_orders_redacts_and_advances_cursor(tmp_path) -> None:
    request, _ = _request(tmp_path)
    captured: list[ContainerJobLogEntry] = []

    async def live_publisher(_request, entries):
        captured.extend(entries)

    logs = {"value": b"line one\ntoken=ghp_" + b"b" * 36 + b"\n"}

    async def runner(args):
        args = tuple(args)
        if args[0] == "logs":
            return 0, logs["value"], b""
        if args[:2] == ("inspect", "--format"):
            return 0, b'{"Running":true}', b""
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=runner,
        live_log_publisher=live_publisher,
    )

    first = await backend.observe_container(request)
    assert first.running is True
    assert [e.text for e in captured] == ["line one", "token=[REDACTED]"]
    assert [e.sequence for e in captured] == [1, 2]

    # Thread the cursor forward; a second cycle only forwards the newly appended
    # line and keeps the sequence monotonic (AC2/AC4).
    request.log_stdout_offset = first.log_stdout_offset
    request.log_stderr_offset = first.log_stderr_offset
    request.log_sequence = first.log_sequence
    logs["value"] = logs["value"] + b"line three\n"
    captured.clear()
    second = await backend.observe_container(request)
    assert [e.text for e in captured] == ["line three"]
    assert [e.sequence for e in captured] == [3]
    assert second.log_sequence == 3


@pytest.mark.asyncio
async def test_acquire_image_records_cache_hit_and_pull(tmp_path) -> None:
    request, _ = _request(tmp_path)

    async def cached_runner(args):
        args = tuple(args)
        if args[:2] == ("image", "inspect"):
            return 0, b"sha256:" + b"a" * 64, b""
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=cached_runner
    )
    cached = await backend.acquire_image(request)
    assert cached.image.cache_present is True
    assert cached.image.cache_hit is True
    assert cached.image.pull_duration_ms is None

    pulled_flag = {"done": False}

    async def pull_runner(args):
        args = tuple(args)
        if args[0] == "pull":
            pulled_flag["done"] = True
            return 0, b"", b""
        if args[:2] == ("image", "inspect"):
            if pulled_flag["done"]:
                return 0, b"sha256:" + b"a" * 64, b""
            return 1, b"", b"missing"
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=pull_runner
    )
    pulled = await backend.acquire_image(request)
    assert pulled.image.cache_present is False
    assert pulled.image.cache_hit is False
    assert pulled.image.pull_duration_ms is not None


@pytest.mark.asyncio
async def test_resolve_workspace_missing_raises_not_visible(tmp_path) -> None:
    request, _ = _request(tmp_path, workspace_dir="present")
    request.request.spec.workspace_ref.artifact_ref = "does_not_exist"
    backend = DockerContainerJobBackend(workspace_root=tmp_path)
    with pytest.raises(ContainerJobWorkspaceNotVisibleError):
        await backend.resolve_workspace(request)


@pytest.mark.asyncio
async def test_live_log_publisher_writes_shared_spool_transport(tmp_path) -> None:
    """AC2/AC12: live entries flow onto the shared Live Logs spool transport."""
    import json

    from moonmind.workflows.temporal.worker_runtime import (
        _container_job_live_log_publisher,
    )

    workspace = tmp_path / "ws"
    workspace.mkdir()
    request = SimpleNamespace(
        resolved_workspace_ref=str(workspace), job_id=JOB_ID
    )
    now = datetime.now(timezone.utc)
    entries = [
        ContainerJobLogEntry(sequence=1, timestamp=now, stream="stdout", text="a"),
        ContainerJobLogEntry(sequence=2, timestamp=now, stream="stderr", text="b"),
    ]
    publish = _container_job_live_log_publisher(str(tmp_path))
    await publish(request, entries)

    spool = workspace / "live_streams.spool"
    lines = [json.loads(line) for line in spool.read_text().splitlines() if line]
    assert [(evt["sequence"], evt["stream"], evt["text"]) for evt in lines] == [
        (1, "stdout", "a"),
        (2, "stderr", "b"),
    ]
    assert all(evt["runId"] == JOB_ID for evt in lines)


class _FakeArtifact:
    def __init__(self, artifact_id: str) -> None:
        self.artifact_id = artifact_id


class _FakeArtifactService:
    def __init__(self) -> None:
        self.writes: dict[str, bytes] = {}

    async def create(self, *, principal, content_type=None, metadata_json=None):
        return _FakeArtifact(metadata_json["name"]), None

    async def write_complete(self, *, artifact_id, principal, payload, content_type=None):
        self.writes[artifact_id] = payload
        return _FakeArtifact(artifact_id)


@pytest.mark.asyncio
async def test_evidence_publisher_redacts_text_before_persistence(tmp_path) -> None:
    """AC11: the durable evidence publisher redacts text payloads defensively."""
    from moonmind.workflows.temporal.worker_runtime import (
        _container_job_evidence_publisher,
    )

    service = _FakeArtifactService()
    publish = _container_job_evidence_publisher(service)
    request = SimpleNamespace(
        job_id=JOB_ID,
        owner=SimpleNamespace(principal_type="system", principal_id="container_job"),
    )
    ref = await publish(
        request, "stdout.log", b"password=hunter2 tail", "text/plain"
    )
    assert b"hunter2" not in service.writes[ref]
    assert b"[REDACTED]" in service.writes[ref]
