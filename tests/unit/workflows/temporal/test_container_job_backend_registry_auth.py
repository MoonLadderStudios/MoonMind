"""Ephemeral private-image auth in the Docker backend (MoonLadderStudios/MoonMind#3257)."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobBackendError,
    ContainerJobFailureClass,
    RegistryAuthorization,
)
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from moonmind.workflows.temporal.runtime.registry_auth_resolve import RegistryCredential

JOB_ID = "container-job:" + "d" * 32
IMAGE = "ghcr.io/org/app:1"
DIGEST_ID = "sha256:" + "a" * 64
TOKEN = "ghp_topsecret_value"


def _request(
    *,
    workspace: Path,
    authorized: bool = True,
    image: str = IMAGE,
    pull_policy: str = "if-missing",
    scope: str = "org/*",
    registry: str = "ghcr.io",
    repository: str = "org/app",
) -> ContainerJobActivityRequest:
    return ContainerJobActivityRequest.model_validate(
        {
            "jobId": JOB_ID,
            "ownershipToken": f"{JOB_ID}:v1",
            "request": {
                "idempotencyKey": "issue-3257",
                "source": {"source": "workflow"},
                "spec": {
                    "image": image,
                    "workspaceRef": {
                        "kind": "artifact-workspace",
                        "artifactRef": "art_workspace",
                    },
                    "registryCredentialRef": "db://ghcr",
                    "pullPolicy": pull_policy,
                    "resources": {"cpuMillis": 1000, "memoryMiB": 512},
                },
            },
            "registryAuthorization": RegistryAuthorization(
                authorized=authorized,
                registry=registry,
                repository=repository,
                reference=image,
                credentialRef="db://ghcr",
                scope=scope,
            ).model_dump(by_alias=True, exclude_none=True),
        }
    )


def _backend(tmp_path: Path, runner, *, resolver=None):
    async def default_resolver(ref):
        return RegistryCredential(username="octo", secret=TOKEN)

    return DockerContainerJobBackend(
        workspace_root=tmp_path / "workspaces",
        command_runner=runner,
        registry_auth_resolver=resolver or default_resolver,
        auth_root=tmp_path / "auth",
    )


@pytest.mark.asyncio
async def test_authorized_pull_materializes_restricted_config_and_cleans_up(
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    commands: list[tuple[str, ...]] = []

    async def runner(args):
        args = tuple(args)
        commands.append(args)
        if args[:2] == ("image", "inspect"):
            # Missing until pulled.
            return (1, b"", b"") if "pull" not in captured else (0, DIGEST_ID.encode(), b"")
        if "pull" in args:
            captured["pull"] = True
            config_dir = Path(args[args.index("--config") + 1])
            config = json.loads((config_dir / "config.json").read_text())
            captured["auth"] = config
            captured["dir_mode"] = stat.S_IMODE(config_dir.stat().st_mode)
            captured["file_mode"] = stat.S_IMODE((config_dir / "config.json").stat().st_mode)
            return 0, b"", b""
        return 0, b"", b""

    backend = _backend(tmp_path, runner)
    result = await backend.acquire_image(_request(workspace=tmp_path))

    assert result.resolved_image_ref == DIGEST_ID
    # Pull used --config pointing at a per-job ephemeral directory.
    assert any("--config" in cmd and "pull" in cmd for cmd in commands)
    assert captured["auth"]["auths"]["ghcr.io"] == {
        "username": "octo",
        "password": TOKEN,
    }
    assert captured["dir_mode"] == 0o700
    assert captured["file_mode"] == 0o600
    # Ephemeral auth is removed immediately after the pull.
    auth_dir = backend._auth_dir(_request(workspace=tmp_path))
    assert not auth_dir.exists()


@pytest.mark.asyncio
async def test_docker_hub_auth_uses_cli_index_key(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def runner(args):
        args = tuple(args)
        if args[:2] == ("image", "inspect"):
            return (1, b"", b"") if not captured else (0, DIGEST_ID.encode(), b"")
        if "pull" in args:
            config_dir = Path(args[args.index("--config") + 1])
            captured.update(json.loads((config_dir / "config.json").read_text()))
        return 0, b"", b""

    backend = _backend(tmp_path, runner)
    await backend.acquire_image(
        _request(
            workspace=tmp_path,
            image="docker.io/org/app:1",
            registry="docker.io",
        )
    )
    assert "https://index.docker.io/v1/" in captured["auths"]


@pytest.mark.asyncio
async def test_cache_hit_enforces_authorization_without_pulling(tmp_path: Path) -> None:
    commands: list[tuple[str, ...]] = []

    async def runner(args):
        args = tuple(args)
        commands.append(args)
        if args[:2] == ("image", "inspect"):
            return 0, DIGEST_ID.encode(), b""
        return 0, b"", b""

    backend = _backend(tmp_path, runner)
    # Authorized cache hit: image present, no pull, no credential materialization.
    result = await backend.acquire_image(_request(workspace=tmp_path))
    assert result.resolved_image_ref == DIGEST_ID
    assert not any("pull" in cmd for cmd in commands)


@pytest.mark.asyncio
async def test_cache_hit_denied_when_not_authorized(tmp_path: Path) -> None:
    async def runner(args):
        # Image is cached, but policy must still block it.
        if tuple(args)[:2] == ("image", "inspect"):
            return 0, DIGEST_ID.encode(), b""
        return 0, b"", b""

    backend = _backend(tmp_path, runner)
    with pytest.raises(ContainerJobBackendError) as excinfo:
        await backend.acquire_image(_request(workspace=tmp_path, authorized=False))
    assert excinfo.value.failure_class == ContainerJobFailureClass.IMAGE_USE_DENIED


@pytest.mark.asyncio
async def test_scope_mismatch_fails_closed_before_pull(tmp_path: Path) -> None:
    async def runner(args):
        return 1, b"", b"missing"

    backend = _backend(tmp_path, runner)
    request = _request(workspace=tmp_path, repository="other/app")
    with pytest.raises(ContainerJobBackendError) as excinfo:
        await backend.acquire_image(request)
    assert (
        excinfo.value.failure_class
        == ContainerJobFailureClass.REPOSITORY_SCOPE_MISMATCH
    )


@pytest.mark.asyncio
async def test_registry_auth_failure_is_classified_and_redacted(tmp_path: Path) -> None:
    async def runner(args):
        args = tuple(args)
        if args[:2] == ("image", "inspect"):
            return 1, b"", b""
        if "pull" in args:
            return 1, b"", f"denied for {TOKEN}".encode()
        return 0, b"", b""

    backend = _backend(tmp_path, runner)
    with pytest.raises(ContainerJobBackendError) as excinfo:
        await backend.acquire_image(_request(workspace=tmp_path))
    assert excinfo.value.failure_class == ContainerJobFailureClass.REGISTRY_AUTH_FAILED
    assert TOKEN not in str(excinfo.value)
    assert "[redacted]" in str(excinfo.value)


@pytest.mark.asyncio
async def test_unresolved_credential_is_classified(tmp_path: Path) -> None:
    from moonmind.workflows.temporal.runtime.registry_auth_resolve import (
        RegistryAuthResolutionError,
    )

    async def failing_resolver(ref):
        raise RegistryAuthResolutionError("nope")

    async def runner(args):
        return 1, b"", b""

    backend = _backend(tmp_path, runner, resolver=failing_resolver)
    with pytest.raises(ContainerJobBackendError) as excinfo:
        await backend.acquire_image(_request(workspace=tmp_path))
    assert excinfo.value.failure_class == ContainerJobFailureClass.CREDENTIAL_UNRESOLVED


@pytest.mark.asyncio
async def test_cleanup_removes_ephemeral_auth_and_reports_failure(tmp_path: Path) -> None:
    async def runner(args):
        return 0, b"", b""

    backend = _backend(tmp_path, runner)
    request = _request(workspace=tmp_path)
    auth_dir = backend._auth_dir(request)
    auth_dir.mkdir(parents=True)
    (auth_dir / "config.json").write_text("{}")

    # Deterministic cleanup removes the directory on any terminal path.
    await backend.cleanup(request)
    assert not auth_dir.exists()
    # Idempotent: cleaning an already-removed directory succeeds.
    await backend.cleanup(request)


@pytest.mark.asyncio
async def test_public_image_never_materializes_auth(tmp_path: Path) -> None:
    commands: list[tuple[str, ...]] = []

    async def runner(args):
        args = tuple(args)
        commands.append(args)
        if args[:2] == ("image", "inspect"):
            return 0, DIGEST_ID.encode(), b""
        return 0, b"", b""

    backend = _backend(tmp_path, runner)
    request = ContainerJobActivityRequest.model_validate(
        {
            "jobId": JOB_ID,
            "ownershipToken": f"{JOB_ID}:v1",
            "request": {
                "idempotencyKey": "k",
                "source": {"source": "workflow"},
                "spec": {
                    "image": "alpine",
                    "workspaceRef": {
                        "kind": "artifact-workspace",
                        "artifactRef": "art_workspace",
                    },
                    "resources": {"cpuMillis": 1000, "memoryMiB": 512},
                },
            },
        }
    )
    result = await backend.acquire_image(request)
    assert result.resolved_image_ref == DIGEST_ID
    assert all("--config" not in cmd for cmd in commands)
