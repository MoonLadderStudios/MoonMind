"""Pre-persistence scanner boundaries for GitHub issue #5040."""

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from moonmind.security.repository_scanners import (
    RepositoryScan,
    ScannerExecution,
    build_scanner_job,
)
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from tests.unit.workflows.temporal.test_container_job_evidence import (
    _Publisher,
    _request,
)

MARKER = "synthetic-marker-never-publish-5040"


def scanner_request(
    tmp_path, *, terminal="succeeded", limit=100000, output="artifacts/gitleaks"
):
    scan = RepositoryScan(
        scanner="gitleaks", outputDirectory=output, maxReportBytes=limit
    )
    request = _request(tmp_path, image=None, **build_scanner_job(scan))
    request.terminal_state = terminal
    request.exit_code = 0 if terminal == "succeeded" else 2
    root = tmp_path / "art_workspace" / scan.output_directory
    root.mkdir(parents=True)
    execution = ScannerExecution(
        inputDigest="a" * 64,
        assets=["tracked.txt"],
        version="8.30.0",
        exits={"history": 1, "worktree": 1},
        states={"history": "succeeded", "worktree": "succeeded"},
        gitMetadata=True,
        shallow=False,
        gitRefs=["b" * 40],
    )
    (root / "execution.json").write_text(execution.model_dump_json(by_alias=True))
    native = [
        {
            "RuleID": "credential",
            "File": "tracked.txt",
            "StartLine": 1,
            "Secret": MARKER,
            "Match": MARKER,
            "Author": MARKER,
        }
    ]
    for mode in scan.modes:
        (root / f"{mode}.json").write_text(json.dumps(native))
    return request, root


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal", ["succeeded", "failed", "timed_out", "canceled", "rejected"]
)
async def test_scanner_ingestion_preserves_partial_findings_and_safe_logs(
    tmp_path, terminal
):
    request, _ = scanner_request(tmp_path, terminal=terminal)
    publisher = _Publisher()
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    async def runner(argv):
        return 0, f"{now} {MARKER}\n".encode(), f"{now} {MARKER}\n".encode()

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=runner,
        evidence_publisher=publisher,
        log_spool_root=tmp_path / "live",
    )
    await backend._collect_live_logs(request, request.container_ref)
    await backend.publish_evidence(request)
    assert all(MARKER.encode() not in body for body in publisher.artifacts.values())
    assert all(
        MARKER not in path.read_text() for path in (tmp_path / "live").rglob("*.jsonl")
    )
    summary = json.loads(publisher.artifacts[f"{request.job_id}-output-summary.json"])
    assert summary["results"]["history"]["findings"]
    assert summary["verification"] == "not_performed"
    assert summary["coverage"] == ("complete" if terminal == "succeeded" else "partial")
    assert summary["nativeRefs"]["history"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    ["{", "null", '{"Secret":"' + MARKER + '"}', " " * 200000],
    ids=["truncated", "null", "wrong-schema", "oversized"],
)
async def test_unsafe_native_outputs_retain_safe_incomplete_diagnostics(tmp_path, body):
    request, root = scanner_request(tmp_path)
    (root / "history.json").write_text(body)
    publisher = _Publisher()
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, evidence_publisher=publisher
    )
    await backend._collect_declared_outputs(request)
    assert all(MARKER.encode() not in value for value in publisher.artifacts.values())
    summary = json.loads(publisher.artifacts[f"{request.job_id}-output-summary.json"])
    assert summary["coverage"] == "partial"
    assert summary["results"]["worktree"]["findings"]


@pytest.mark.asyncio
async def test_sanitizer_failure_publishes_no_native_bytes(tmp_path, monkeypatch):
    request, _ = scanner_request(tmp_path)
    publisher = _Publisher()

    def broken(*args, **kwargs):
        raise ValueError(MARKER)

    monkeypatch.setattr(
        "moonmind.workflows.temporal.container_job_backend.sanitize_native", broken
    )
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, evidence_publisher=publisher
    )
    await backend._collect_declared_outputs(request)
    assert all(MARKER.encode() not in value for value in publisher.artifacts.values())
    summary = json.loads(publisher.artifacts[f"{request.job_id}-output-summary.json"])
    assert summary["coverage"] == "partial"
    assert not summary["nativeRefs"]


@pytest.mark.asyncio
async def test_arbitrary_execution_diagnostic_is_never_published(tmp_path):
    request, root = scanner_request(tmp_path)
    metadata = json.loads((root / "execution.json").read_bytes())
    metadata["limitations"] = [MARKER]
    metadata["gitRefs"] = [MARKER]
    (root / "execution.json").write_text(json.dumps(metadata))
    publisher = _Publisher()
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, evidence_publisher=publisher
    )
    await backend._collect_declared_outputs(request)
    assert all(MARKER.encode() not in body for body in publisher.artifacts.values())
    summary = json.loads(publisher.artifacts[f"{request.job_id}-output-summary.json"])
    assert summary["coverage"] == "partial"
    assert summary["results"]["worktree"]["findings"]


@pytest.mark.asyncio
@pytest.mark.parametrize("ceiling", ["bytes", "files"])
async def test_generated_summary_shares_the_job_output_ceiling(tmp_path, ceiling):
    request, _ = scanner_request(tmp_path)
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, evidence_publisher=_Publisher()
    )
    initial = await backend._collect_declared_outputs(request)
    if ceiling == "bytes":
        bound = sum(item.size_bytes or 0 for item in initial) - 1
        backend._settings = replace(backend._settings, max_output_total_bytes=bound)
    else:
        bound = 1
        backend._settings = replace(backend._settings, max_output_files=bound)
    manifest = await backend._collect_declared_outputs(request)
    collected = [item for item in manifest if item.collection_status == "collected"]
    if ceiling == "bytes":
        assert sum(item.size_bytes or 0 for item in collected) <= bound
    else:
        assert len(collected) <= bound


@pytest.mark.asyncio
async def test_every_declared_scanner_output_has_a_retained_manifest_entry(tmp_path):
    request, _ = scanner_request(tmp_path)
    publisher = _Publisher()
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, evidence_publisher=publisher
    )
    manifest = await backend._collect_declared_outputs(request)
    assert {entry.name for entry in manifest} == {
        entry.name for entry in request.request.spec.outputs
    }
    execution = next(entry for entry in manifest if entry.name == "execution")
    assert execution.collection_status == "collected"
    assert execution.artifact_ref


@pytest.mark.asyncio
async def test_native_secret_is_removed_from_metadata_before_any_publish(tmp_path):
    request, root = scanner_request(
        tmp_path, terminal="timed_out", output=f"artifacts/{MARKER}"
    )
    metadata = json.loads((root / "execution.json").read_bytes())
    metadata["assets"].append(f"{MARKER}.txt")
    metadata["untrackedFiles"] = [f"{MARKER}.txt"]
    (root / "execution.json").write_text(json.dumps(metadata))
    # Only the later mode identifies the secret. The earlier artifact's path
    # must be sanitized before its first publication too.
    history = [{"RuleID": "credential", "File": f"{MARKER}.txt", "StartLine": 1}]
    (root / "history.json").write_text(json.dumps(history))
    publisher = _Publisher()

    async def publish(request, name, payload):
        assert MARKER not in name
        assert MARKER.encode() not in payload
        return await publisher(request, name, payload)

    async def runner(argv):
        return 0, MARKER.encode(), MARKER.encode()

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=runner, evidence_publisher=publish
    )
    await backend.publish_evidence(request)
    summary = json.loads(publisher.artifacts[f"{request.job_id}-output-summary.json"])
    assert summary["coverage"] == "partial"
    assert summary["results"]["worktree"]["findings"][0]["asset"] == "tracked.txt"
    assert summary["execution"]["untrackedFiles"]
    assert summary["results"]["history"]["findings"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    ["truncated", "wrong-schema", "missing", "sanitizer", "metadata-sanitizer"],
)
@pytest.mark.parametrize("valid_sibling", [False, True])
async def test_unreadable_native_evidence_withholds_unknown_sensitive_metadata(
    tmp_path, monkeypatch, failure, valid_sibling
):
    request, root = scanner_request(tmp_path, output=f"artifacts/{MARKER}")
    metadata = json.loads((root / "execution.json").read_bytes())
    metadata["assets"].append(f"{MARKER}.txt")
    metadata["untrackedFiles"] = [f"{MARKER}.txt"]
    (root / "execution.json").write_text(json.dumps(metadata))
    # Full scanner redaction leaves ingestion unable to discover the original
    # value in admission metadata, even when the sibling mode remains useful.
    for mode in request.request.spec.repository_scan.modes:
        path = root / f"{mode}.json"
        if valid_sibling and mode == "worktree":
            path.write_text(
                json.dumps(
                    [
                        {
                            "RuleID": "credential",
                            "File": "tracked.txt",
                            "StartLine": 1,
                            "Secret": "[REDACTED]",
                        }
                    ]
                )
            )
        elif failure == "missing":
            path.unlink()
        else:
            path.write_text("[" if failure == "truncated" else "{}")
    if failure == "sanitizer":
        from moonmind.workflows.temporal import container_job_backend as backend_module

        original = backend_module.sanitize_native

        # Parsing can succeed while subsequent sanitization fails.
        for mode in request.request.spec.repository_scan.modes:
            if not (valid_sibling and mode == "worktree"):
                (root / f"{mode}.json").write_text("[]")

        def broken(scanner, body, *args, **kwargs):
            if body == b"[]":
                raise ValueError(MARKER)
            return original(scanner, body, *args, **kwargs)

        monkeypatch.setattr(backend_module, "sanitize_native", broken)
    elif failure == "metadata-sanitizer":
        from moonmind.security.repository_scanners import ScannerOutputSanitizer

        for mode in request.request.spec.repository_scan.modes:
            if not (valid_sibling and mode == "worktree"):
                (root / f"{mode}.json").write_text("[]")

        def broken(*args, **kwargs):
            raise ValueError(MARKER)

        monkeypatch.setattr(ScannerOutputSanitizer, "metadata", broken)
    publisher = _Publisher()

    async def publish(request, name, payload):
        assert MARKER not in name
        assert MARKER.encode() not in payload
        return await publisher(request, name, payload)

    async def runner(argv):
        return 0, MARKER.encode(), MARKER.encode()

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=runner,
        evidence_publisher=publish,
        log_spool_root=tmp_path / "live",
    )
    await backend._collect_live_logs(request, request.container_ref)
    await backend.publish_evidence(request)
    assert all(
        MARKER not in path.read_text() for path in (tmp_path / "live").rglob("*.jsonl")
    )
    summary = json.loads(publisher.artifacts[f"{request.job_id}-output-summary.json"])
    assert summary["coverage"] == "partial"
    assert summary["limitations"]
    assert summary["admission"] is None
    if summary["execution"] is not None:
        assert not summary["execution"]["assets"]
        assert not summary["execution"]["untrackedFiles"]
    if valid_sibling:
        assert summary["results"]["worktree"]["findings"]
        assert summary["nativeRefs"]["worktree"]
