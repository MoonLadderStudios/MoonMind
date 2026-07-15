"""Evidence, observation, and live-log coverage for MoonLadderStudios/MoonMind#3258.

These tests exercise the trusted Docker backend's terminal evidence publication
(redacted split logs, runtime diagnostics, output manifest with validation and
partial evidence) and the bounded incremental live-log plane (monotonic cursor,
per-stream attribution, durable terminal journal).
"""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from moonmind.config.container_backend_settings import (
    resolve_container_backend_settings,
)
from moonmind.schemas.container_job_models import (
    MAX_LOG_PAGE_ENTRIES,
    ArtifactCollectionStatus,
    ContainerJobActivityRequest,
    ContainerJobArtifactPage,
)
from moonmind.workflows.temporal.container_job_backend import (
    DockerContainerJobBackend,
)

JOB_ID = "container-job:0123456789abcdef0123456789abcdef"


def _request(tmp_path, **spec_overrides) -> ContainerJobActivityRequest:
    workspace = tmp_path / "art_workspace"
    workspace.mkdir(exist_ok=True)
    spec = {
        "image": "python:3.13",
        "workspaceRef": {"kind": "sandbox", "workspaceId": "art_workspace"},
        "command": ["python", "-V"],
        "resources": {"cpuMillis": 1000, "memoryMiB": 512},
        "timeoutSeconds": 60,
    }
    spec.update(spec_overrides)
    payload = {
        "jobId": JOB_ID,
        "ownershipToken": f"{JOB_ID}:v1",
        "request": {
            "idempotencyKey": "issue-3258",
            "source": {"source": "workflow", "workflowId": "mm:3258"},
            "spec": spec,
        },
        "resolvedWorkspaceRef": str(workspace),
        "containerRef": "moonmind-container-job-x",
    }
    return ContainerJobActivityRequest.model_validate(payload)


class _Publisher:
    """Records every published evidence artifact by name."""

    def __init__(self) -> None:
        self.artifacts: dict[str, bytes] = {}

    async def __call__(self, request, name: str, payload: bytes) -> str:
        self.artifacts[name] = payload
        return f"artifact:{name}"


# --------------------------------------------------------------- terminal logs


@pytest.mark.asyncio
async def test_publish_evidence_redacts_and_splits_streams(tmp_path) -> None:
    publisher = _Publisher()

    async def runner(args):
        if args[0] == "logs":
            return (
                0,
                b"starting\ntoken=ghp_" + b"a" * 36 + b"\n",
                b"a warning line\n",
            )
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=runner,
        evidence_publisher=publisher,
    )
    result = await backend.publish_evidence(_request(tmp_path))

    # Separate deterministic stdout/stderr artifacts plus a combined log.
    assert f"{JOB_ID}-stdout.txt" in publisher.artifacts
    assert f"{JOB_ID}-stderr.txt" in publisher.artifacts
    assert f"{JOB_ID}-logs.txt" in publisher.artifacts
    assert result.logs_ref == f"artifact:{JOB_ID}-logs.txt"
    assert result.diagnostics_ref == f"artifact:{JOB_ID}-diagnostics.json"

    stdout_bytes = publisher.artifacts[f"{JOB_ID}-stdout.txt"]
    # The container-emitted secret must never reach durable storage.
    assert b"ghp_" not in stdout_bytes
    assert b"[REDACTED]" in stdout_bytes
    assert b"a warning line" in publisher.artifacts[f"{JOB_ID}-stderr.txt"]


@pytest.mark.asyncio
async def test_publish_evidence_runtime_diagnostics_carry_exit_metadata(
    tmp_path,
) -> None:
    publisher = _Publisher()

    async def runner(args):
        if args[0] == "logs":
            return 0, b"done\n", b""
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=runner,
        evidence_publisher=publisher,
    )
    request = _request(tmp_path)
    request.exit_code = 3
    request.terminal_state = "failed"
    request.message = "boom"
    await backend.publish_evidence(request)

    diagnostics = json.loads(publisher.artifacts[f"{JOB_ID}-diagnostics.json"])
    assert diagnostics["exitCode"] == 3
    assert diagnostics["terminalState"] == "failed"
    assert diagnostics["message"] == "boom"
    assert diagnostics["stdoutRef"] == f"artifact:{JOB_ID}-stdout.txt"


# --------------------------------------------------------------- output manifest


@pytest.mark.asyncio
async def test_output_manifest_records_size_digest_media_type(tmp_path) -> None:
    workspace = tmp_path / "art_workspace"
    workspace.mkdir()
    (workspace / "report.txt").write_bytes(b"result-bytes")
    publisher = _Publisher()

    async def runner(args):
        if args[0] == "logs":
            return 0, b"", b""
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=runner,
        evidence_publisher=publisher,
    )
    request = _request(
        tmp_path,
        outputs=[{"name": "report", "relativePath": "report.txt"}],
    )
    result = await backend.publish_evidence(request)

    assert result.artifacts_ref == f"artifact:{JOB_ID}-artifacts.json"
    manifest = ContainerJobArtifactPage.model_validate_json(
        publisher.artifacts[f"{JOB_ID}-artifacts.json"].decode()
    )
    assert manifest.publication.state == "succeeded"
    entry = next(item for item in manifest.artifacts if item.name == "report")
    assert entry.collection_status == ArtifactCollectionStatus.COLLECTED
    assert entry.size_bytes == len(b"result-bytes")
    assert entry.sha256 == hashlib.sha256(b"result-bytes").hexdigest()
    assert entry.media_type == "text/plain"
    assert entry.relative_path == "report.txt"


@pytest.mark.asyncio
async def test_missing_declared_output_is_partial_not_fatal(tmp_path) -> None:
    publisher = _Publisher()

    async def runner(args):
        if args[0] == "logs":
            return 0, b"partial run\n", b""
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=runner,
        evidence_publisher=publisher,
    )
    request = _request(
        tmp_path,
        outputs=[{"name": "never", "relativePath": "does/not/exist"}],
    )
    # A missing declared output must not turn evidence publication into a raise;
    # partial log evidence is still captured (cancellation/timeout preservation).
    result = await backend.publish_evidence(request)
    assert result.logs_ref is not None

    manifest = ContainerJobArtifactPage.model_validate_json(
        publisher.artifacts[f"{JOB_ID}-artifacts.json"].decode()
    )
    entry = next(item for item in manifest.artifacts if item.name == "never")
    assert entry.collection_status == ArtifactCollectionStatus.MISSING
    assert entry.artifact_ref is None
    assert manifest.publication.state == "failed"


@pytest.mark.asyncio
async def test_output_collection_rejects_symlink_escape(tmp_path) -> None:
    workspace = tmp_path / "art_workspace"
    workspace.mkdir()
    secret = tmp_path / "outside_secret"
    secret.write_bytes(b"top-secret")
    escape = workspace / "escape"
    escape.symlink_to(secret)
    publisher = _Publisher()

    async def runner(args):
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=runner,
        evidence_publisher=publisher,
    )
    request = _request(
        tmp_path,
        outputs=[{"name": "escape", "relativePath": "escape"}],
    )
    await backend.publish_evidence(request)

    manifest = ContainerJobArtifactPage.model_validate_json(
        publisher.artifacts[f"{JOB_ID}-artifacts.json"].decode()
    )
    entry = next(item for item in manifest.artifacts if item.name == "escape")
    assert entry.collection_status == ArtifactCollectionStatus.REJECTED
    # The escaping target bytes must never be published.
    assert all(b"top-secret" not in blob for blob in publisher.artifacts.values())


@pytest.mark.asyncio
async def test_output_collection_rejects_unsupported_file_type(tmp_path) -> None:
    workspace = tmp_path / "art_workspace"
    workspace.mkdir()
    fifo_path = workspace / "pipe"
    try:
        os.mkfifo(fifo_path)
    except (AttributeError, NotImplementedError, OSError):
        pytest.skip("named pipes are unavailable on this platform")
    publisher = _Publisher()

    async def runner(args):
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=runner,
        evidence_publisher=publisher,
    )
    request = _request(
        tmp_path,
        outputs=[{"name": "pipe", "relativePath": "pipe"}],
    )
    await backend.publish_evidence(request)

    manifest = ContainerJobArtifactPage.model_validate_json(
        publisher.artifacts[f"{JOB_ID}-artifacts.json"].decode()
    )
    entry = next(item for item in manifest.artifacts if item.name == "pipe")
    assert entry.collection_status == ArtifactCollectionStatus.REJECTED
    assert "unsupported file type" in (entry.detail or "")


@pytest.mark.asyncio
async def test_output_collection_enforces_file_count_ceiling(tmp_path) -> None:
    workspace = tmp_path / "art_workspace"
    workspace.mkdir()
    bundle = workspace / "dist"
    bundle.mkdir()
    (bundle / "a.txt").write_bytes(b"a")
    (bundle / "b.txt").write_bytes(b"b")
    (bundle / "c.txt").write_bytes(b"c")
    publisher = _Publisher()

    async def runner(args):
        return 0, b"", b""

    tiny = resolve_container_backend_settings(
        {"MOONMIND_CONTAINER_BACKEND_MAX_OUTPUT_FILES": "2"}
    )
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        settings=tiny,
        command_runner=runner,
        evidence_publisher=publisher,
    )
    request = _request(
        tmp_path,
        outputs=[{"name": "dist", "relativePath": "dist"}],
    )
    await backend.publish_evidence(request)

    manifest = ContainerJobArtifactPage.model_validate_json(
        publisher.artifacts[f"{JOB_ID}-artifacts.json"].decode()
    )
    entry = next(item for item in manifest.artifacts if item.name == "dist")
    assert entry.collection_status == ArtifactCollectionStatus.REJECTED
    assert "ceiling" in (entry.detail or "")


@pytest.mark.asyncio
async def test_directory_output_is_collected_as_archive(tmp_path) -> None:
    workspace = tmp_path / "art_workspace"
    workspace.mkdir()
    bundle = workspace / "dist"
    bundle.mkdir()
    (bundle / "a.txt").write_bytes(b"alpha")
    publisher = _Publisher()

    async def runner(args):
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=runner,
        evidence_publisher=publisher,
    )
    request = _request(
        tmp_path, outputs=[{"name": "dist", "relativePath": "dist"}]
    )
    await backend.publish_evidence(request)

    manifest = ContainerJobArtifactPage.model_validate_json(
        publisher.artifacts[f"{JOB_ID}-artifacts.json"].decode()
    )
    entry = next(item for item in manifest.artifacts if item.name == "dist")
    assert entry.collection_status == ArtifactCollectionStatus.COLLECTED
    assert entry.media_type == "application/gzip"
    assert f"{JOB_ID}-output-dist.tar.gz" in publisher.artifacts


# --------------------------------------------------------------- observations


@pytest.mark.asyncio
async def test_resolve_workspace_reports_visibility_probe(tmp_path) -> None:
    (tmp_path / "temporal_sandbox" / "run-1" / "repo").mkdir(parents=True)
    backend = DockerContainerJobBackend(workspace_root=tmp_path)
    request = ContainerJobActivityRequest.model_validate(
        {
            "jobId": JOB_ID,
            "ownershipToken": f"{JOB_ID}:v1",
            "request": {
                "idempotencyKey": "issue-3258",
                "source": {"source": "workflow", "workflowId": "mm:3258"},
                "spec": {
                    "image": "python:3.13",
                    "workspaceRef": {"kind": "sandbox", "workspaceId": "run-1"},
                    "resources": {"cpuMillis": 1000, "memoryMiB": 512},
                },
            },
        }
    )
    result = await backend.resolve_workspace(request)
    assert result.workspace_probe == "visible"


@pytest.mark.asyncio
async def test_observe_captures_container_timing(tmp_path) -> None:
    async def runner(args):
        if args[:2] == ("inspect", "--format"):
            return (
                0,
                json.dumps(
                    {
                        "Running": False,
                        "ExitCode": 0,
                        "StartedAt": "2024-01-01T00:00:01.000000000Z",
                        "FinishedAt": "2024-01-01T00:00:03.000000000Z",
                    }
                ).encode(),
                b"",
            )
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=runner
    )
    result = await backend.observe_container(_request(tmp_path))
    assert result.terminal_state == "succeeded"
    assert result.started_at is not None and result.finished_at is not None
    assert result.duration_ms == 2000


# ---------------------------------------------------------------- live logs


def _spool_events(spool_root, ownership_token="container-job:0123456789abcdef0123456789abcdef:v1"):
    suffix = hashlib.sha256(ownership_token.encode()).hexdigest()[:20]
    spool = spool_root / f"job-{suffix}" / "live_streams.spool"
    if not spool.is_file():
        return []
    return [json.loads(line) for line in spool.read_text().splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_observe_publishes_bounded_incremental_live_logs(tmp_path) -> None:
    spool_root = tmp_path / "spool"
    calls: list[tuple[str, ...]] = []
    # A running container that produces three timestamped lines on the first
    # poll and a fourth newer line on the second poll.
    log_state = {
        "stdout": (
            "2024-01-01T00:00:01.000000000Z hello\n"
            "2024-01-01T00:00:02.000000000Z token=ghp_" + "a" * 36 + "\n"
        ),
        "stderr": "2024-01-01T00:00:03.000000000Z warn\n",
    }

    async def runner(args):
        args = tuple(args)
        calls.append(args)
        if args[:2] == ("inspect", "--format"):
            return 0, b'{"Running":true}', b""
        if args[0] == "logs":
            return 0, log_state["stdout"].encode(), log_state["stderr"].encode()
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=runner,
        log_spool_root=spool_root,
    )
    request = _request(tmp_path)

    first = await backend.observe_container(request)
    assert first.running is True
    assert first.log_cursor is not None and first.log_cursor.split("|")[1] == "3"

    events = _spool_events(spool_root)
    assert [event["sequence"] for event in events] == [1, 2, 3]
    assert [event["stream"] for event in events] == ["stdout", "stdout", "stderr"]
    # Container-emitted secrets are redacted before they reach the live plane.
    assert all("ghp_" not in event["text"] for event in events)

    # Poll again with the carried cursor: already-seen lines are not re-emitted.
    request.log_cursor = first.log_cursor
    second = await backend.observe_container(request)
    assert second.log_cursor == first.log_cursor
    assert len(_spool_events(spool_root)) == 3

    # A newer line advances the cursor and appends exactly one event.
    log_state["stdout"] += "2024-01-01T00:00:04.000000000Z again\n"
    third = await backend.observe_container(request)
    assert third.log_cursor.split("|")[1] == "4"
    events = _spool_events(spool_root)
    assert len(events) == 4
    assert events[-1]["text"] == "again"
    # The incremental fetch used a resumable --since cursor, not a full re-read.
    assert any("--since" in call for call in calls)
    assert all(
        "--tail" in call and str(MAX_LOG_PAGE_ENTRIES) in call
        for call in calls
        if call and call[0] == "logs"
    )


@pytest.mark.asyncio
async def test_live_logs_are_noop_without_spool_root(tmp_path) -> None:
    calls: list[tuple[str, ...]] = []

    async def runner(args):
        args = tuple(args)
        calls.append(args)
        if args[:2] == ("inspect", "--format"):
            return 0, b'{"Running":true}', b""
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=runner
    )
    result = await backend.observe_container(_request(tmp_path))
    assert result.running is True
    assert result.log_cursor is None
    # Live logging is opt-in: no docker logs call is issued when disabled.
    assert not any(call and call[0] == "logs" for call in calls)


@pytest.mark.asyncio
async def test_publish_evidence_persists_live_events_journal(tmp_path) -> None:
    spool_root = tmp_path / "spool"
    publisher = _Publisher()

    async def runner(args):
        args = tuple(args)
        if args[:2] == ("inspect", "--format"):
            return 0, b'{"Running":true}', b""
        if args[0] == "logs" and "--timestamps" in args:
            return 0, b"2024-01-01T00:00:01.000000000Z hi\n", b""
        if args[0] == "logs":
            return 0, b"hi\n", b""
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=runner,
        evidence_publisher=publisher,
        log_spool_root=spool_root,
    )
    request = _request(tmp_path)
    # Produce at least one live event, then publish terminal evidence.
    await backend.observe_container(request)
    result = await backend.publish_evidence(request)

    assert result.events_ref == f"artifact:{JOB_ID}-observability.events.jsonl"
    journal = publisher.artifacts[f"{JOB_ID}-observability.events.jsonl"]
    assert b'"sequence":1' in journal.replace(b" ", b"")
    assert _spool_events(spool_root) == []
