import hashlib
import json
from unittest.mock import AsyncMock

import pytest

from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobActivityResult,
)
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from tests.unit.services.test_repository_scanner_readiness import configured


def request():
    return ContainerJobActivityRequest.model_validate(
        {
            "jobId": "container-job:" + "a" * 32,
            "ownershipToken": "readiness",
            "owner": {"principalId": "operator", "principalType": "user"},
            "request": {
                "idempotencyKey": "readiness",
                "source": {"source": "workflow"},
                "spec": {
                    "imageSourceRef": "repository-security",
                    "inputAttachments": [],
                    "resources": {"memoryMiB": 1024},
                },
            },
        }
    )


@pytest.mark.asyncio
async def test_preflight_prepares_image_then_probes_without_target_authority(tmp_path):
    calls = []

    async def runner(args):
        calls.append(list(args))
        if args[0] == "run":
            return (
                0,
                json.dumps(
                    {
                        "schemaVersion": "scanner-readiness/v1",
                        "status": "ready",
                        "selectionDigest": hashlib.sha256(
                            json.dumps(
                                {
                                    "repositoryScan": {
                                        "scanner": "gitleaks",
                                        "outputDirectory": "reports",
                                    }
                                },
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode()
                        ).hexdigest(),
                        "checks": [
                            {
                                "component": "gitleaks",
                                "code": "scanner_available",
                                "status": "ready",
                            },
                            {
                                "component": "rules",
                                "code": "scanner_rules_available",
                                "status": "ready",
                            },
                        ],
                    }
                ).encode(),
                b"",
            )
        return 0, b"[]", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, settings=configured(), command_runner=runner
    )
    backend.acquire_image = AsyncMock(
        return_value=ContainerJobActivityResult(resolvedImageRef="sha256:" + "b" * 64)
    )
    result = await backend.assessment_readiness(
        request(),
        kind="repository-scan",
        selection={"scanner": "gitleaks", "outputDirectory": "reports"},
        caches=[],
    )
    assert result["status"] == "ready"
    backend.acquire_image.assert_awaited_once()
    command = next(c for c in calls if c[0] == "run")
    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command and "--pull=never" in command
    assert "--cap-drop=ALL" in command and "--security-opt=no-new-privileges" in command
    assert "--mount" not in command
    assert (
        command[command.index("--tmpfs") + 1]
        == "/tmp:rw,exec,nosuid,nodev,size=536870912"
    )
    assert command[command.index("--hostname") + 1] == "localhost"
    assert not any("credential" in part or "/workspace" in part for part in command)
    assert calls[-1][:2] == ["rm", "-f"]


@pytest.mark.asyncio
async def test_preflight_missing_feed_volume_never_creates_it(tmp_path):
    calls = []

    async def runner(args):
        calls.append(list(args))
        return 1, b"", b"private daemon details"

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, settings=configured(), command_runner=runner
    )
    backend.acquire_image = AsyncMock(
        return_value=ContainerJobActivityResult(resolvedImageRef="sha256:" + "b" * 64)
    )
    result = await backend.assessment_readiness(
        request(),
        kind="repository-scan",
        selection={"scanner": "trivy", "outputDirectory": "reports"},
        caches=[
            {
                "cacheRef": "scanner-db",
                "target": "/opt/moonmind-scanner-db",
                "readOnly": True,
            }
        ],
    )
    assert result["status"] == "blocked"
    assert result["diagnostics"][0]["code"] == "feed_cache_unavailable"
    assert calls == [["volume", "inspect", "feeds"]]
    assert "private daemon" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output",
    [b"{}", b"not json", b'{"status":"ready","checks":[],"schemaVersion":"bad"}'],
)
async def test_bad_probe_output_is_not_readiness(tmp_path, output):
    async def runner(args):
        return 0, output, b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, settings=configured(), command_runner=runner
    )
    backend.acquire_image = AsyncMock(
        return_value=ContainerJobActivityResult(resolvedImageRef="sha256:" + "b" * 64)
    )
    result = await backend.assessment_readiness(
        request(),
        kind="repository-scan",
        selection={"scanner": "gitleaks", "outputDirectory": "reports"},
        caches=[],
    )
    assert result["status"] == "unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("cached_old_image", [False, True])
async def test_fresh_install_uses_existing_image_acquisition_owner(
    tmp_path, cached_old_image
):
    from dataclasses import replace

    from moonmind.config.container_backend_settings import RegistryImageSource
    from tests.unit.services.test_assessment_runtime_readiness import probe_payload

    calls, installed = [], cached_old_image
    settings = configured()
    if cached_old_image:
        settings = replace(
            settings,
            image_sources=(
                RegistryImageSource(
                    "repository-security",
                    "registry.example/scanner:custom",
                    pull_policy="always",
                ),
            ),
        )

    async def runner(args):
        nonlocal installed
        calls.append(list(args))
        if list(args[:2]) == ["image", "inspect"]:
            return (
                (0, ("sha256:" + "b" * 64 + "\t[]").encode(), b"")
                if installed
                else (1, b"", b"No such image")
            )
        if args[0] == "pull":
            installed = True
            return 0, b"", b""
        if args[0] == "run":
            return (
                0,
                probe_payload(
                    {"scanner": "gitleaks", "outputDirectory": "reports"},
                    [
                        {
                            "component": "gitleaks",
                            "code": "scanner_available",
                            "status": "ready",
                        },
                        {
                            "component": "rules",
                            "code": "scanner_rules_available",
                            "status": "ready",
                        },
                    ],
                ),
                b"",
            )
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, settings=settings, command_runner=runner
    )
    result = await backend.assessment_readiness(
        request(),
        kind="repository-scan",
        selection={"scanner": "gitleaks", "outputDirectory": "reports"},
        caches=[],
    )
    assert result["status"] == "ready"
    pull = next(i for i, c in enumerate(calls) if c[0] == "pull")
    run = next(i for i, c in enumerate(calls) if c[0] == "run")
    assert pull < run
    assert calls[pull][-1] == "registry.example/scanner:custom"
    assert "sha256:" + "b" * 64 in calls[run]
    assert "--kill-after=5s" in calls[run] and "75s" in calls[run]


@pytest.mark.asyncio
async def test_cancel_still_removes_only_its_diagnostic_container(tmp_path):
    import asyncio

    calls = []

    async def runner(args):
        calls.append(list(args))
        if args[0] == "run":
            raise asyncio.CancelledError
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, settings=configured(), command_runner=runner
    )
    backend.acquire_image = AsyncMock(
        return_value=ContainerJobActivityResult(resolvedImageRef="sha256:" + "b" * 64)
    )
    with pytest.raises(asyncio.CancelledError):
        await backend.assessment_readiness(
            request(),
            kind="repository-scan",
            selection={"scanner": "gitleaks", "outputDirectory": "reports"},
            caches=[],
        )
    assert calls[-1][:2] == ["rm", "-f"]
    assert calls[-1][2] == calls[0][calls[0].index("--name") + 1]


@pytest.mark.asyncio
async def test_selected_feed_is_existing_readonly_volume_and_not_target_mount(tmp_path):
    from tests.unit.services.test_assessment_runtime_readiness import probe_payload

    calls = []
    selection = {
        "scanner": "trivy",
        "outputDirectory": "reports",
        "databaseCacheRef": "scanner-db",
    }
    checks = [
        {"component": "trivy", "code": "scanner_available", "status": "ready"},
        {"component": "rules", "code": "scanner_rules_available", "status": "ready"},
        {"component": "feeds", "code": "feed_snapshot_available", "status": "ready"},
        {"component": "feeds", "code": "feed_freshness_verified", "status": "ready"},
    ]

    async def runner(args):
        calls.append(list(args))
        return (
            (0, probe_payload(selection, checks), b"")
            if args[0] == "run"
            else (0, b"[]", b"")
        )

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, settings=configured(), command_runner=runner
    )
    backend.acquire_image = AsyncMock(
        return_value=ContainerJobActivityResult(resolvedImageRef="sha256:" + "b" * 64)
    )
    result = await backend.assessment_readiness(
        request(),
        kind="repository-scan",
        selection=selection,
        caches=[
            {
                "cacheRef": "scanner-db",
                "target": "/opt/moonmind-scanner-db",
                "readOnly": True,
            }
        ],
    )
    assert result["status"] == "ready"
    command = next(c for c in calls if c[0] == "run")
    assert command.count("--mount") == 1
    assert (
        command[command.index("--mount") + 1]
        == "type=volume,src=feeds,dst=/opt/moonmind-scanner-db,readonly,volume-nocopy"
    )
    assert calls[0] == ["volume", "inspect", "feeds"]


@pytest.mark.asyncio
async def test_cleanup_deadline_cancels_underlying_cli(tmp_path, monkeypatch):
    import asyncio

    from moonmind.workflows.temporal import container_job_backend as module

    original_timeout = asyncio.timeout
    monkeypatch.setattr(
        module.asyncio,
        "timeout",
        lambda seconds: original_timeout(0.01 if seconds == 15 else seconds),
    )
    cancelled = False

    async def runner(args):
        nonlocal cancelled
        if args[0] == "rm":
            try:
                await asyncio.Event().wait()
            finally:
                cancelled = True
        raise TimeoutError

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, settings=configured(), command_runner=runner
    )
    backend.acquire_image = AsyncMock(
        return_value=ContainerJobActivityResult(resolvedImageRef="sha256:" + "b" * 64)
    )
    result = await backend.assessment_readiness(
        request(),
        kind="repository-scan",
        selection={"scanner": "gitleaks", "outputDirectory": "reports"},
        caches=[],
    )
    assert result["status"] == "unavailable" and cancelled


@pytest.mark.asyncio
async def test_default_probe_capture_streams_with_bounded_retention(
    tmp_path, monkeypatch
):
    from moonmind.workflows.temporal import container_job_backend as module

    calls = []

    async def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return 0, b"{}", b""

    monkeypatch.setattr(module, "run_runtime_command", run)
    backend = DockerContainerJobBackend(workspace_root=tmp_path, settings=configured())
    backend.acquire_image = AsyncMock(
        return_value=ContainerJobActivityResult(resolvedImageRef="sha256:" + "b" * 64)
    )
    await backend.assessment_readiness(
        request(),
        kind="repository-scan",
        selection={"scanner": "gitleaks", "outputDirectory": "reports"},
        caches=[],
    )
    _, options = next(c for c in calls if c[0][1] == "run")
    assert options["on_output"] is not None
    assert options["output_limit_bytes"] == 16385
    assert options["timeout_seconds"] == 90


@pytest.mark.asyncio
async def test_cloud_probe_uses_same_offline_command_as_image_qualification(
    tmp_path, monkeypatch
):
    from tools.ci import qualify_repository_security_image as qualification

    calls = []

    async def runner(args):
        calls.append(list(args))
        return 0, b"Prowler 5.0.0", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, settings=configured(), command_runner=runner
    )
    backend.acquire_image = AsyncMock(
        return_value=ContainerJobActivityResult(resolvedImageRef="sha256:" + "b" * 64)
    )
    await backend.assessment_readiness(
        request(), kind="cloud-assessment", selection={}, caches=[]
    )
    production = next(args for args in calls if args[0] == "run")
    qualified = []
    monkeypatch.setattr(
        qualification,
        "invoke",
        lambda args, *a, **kw: qualified.append(args) or "Prowler 5.0.0",
    )
    qualification.qualify_readiness("upstream:stable", tmp_path, kind="cloud")
    assert production[-3:] == qualified[0][-3:]
    assert production[-3:-1] == ["sh", "-c"]
    assert "/home/*/.venv/bin/prowler" in production[-1]
    assert production[-1].endswith('exec "$p" --version')
