"""Runtime image acquisition decoupled from source credentials (#4012).

Production pull inventory (traced from the real image-selection and pull
boundary, per #4004):

* container jobs (sole authenticated registry pull path):
  caller ``container_job.acquire_image`` activity
  (``workflows/temporal/workflows/container_job.py``) ->
  ``DockerContainerJobBackend.acquire_image`` ->
  ``_acquire_private_image`` (private, explicit ``registryCredentialRef``) or
  the plain public ``docker pull`` path. Docker backend identity is the
  deployment-selected daemon (``backend_ref`` + ``DOCKER_HOST`` endpoint) reached
  through the injected ``command_runner``. Exact selected image is
  ``spec.image`` / ``RegistryImageSource.image``; auth source is exactly one
  ``registryCredentialRef`` resolved via ``registry_auth_resolve`` through the
  existing managed-secret backends. Docker auth is materialized only as a
  per-job ``docker --config <auth_dir>`` directory (0700/0600), removed
  immediately after the pull and again by ``cleanup``; no shared HOME/config
  mutation, no global login/logout, no helper mounted into the agent.
* managed sessions (``runtime/managed_session_controller.py``): session
  containers are created from the deployment-configured ``image_ref`` against
  the already-provisioned daemon image; the controller performs no registry
  pull and compiles no registry auth from source/model credentials.
* generic/profile-bound Omnigent hosts: launched from digest-pinned identities
  published by ``omnigent/bootstrap/image_resolution.py``; resolution uses
  anonymous ``docker pull``/``inspect`` of deployment-configured images only.
* Compose/bootstrap (``workflows/skills/deployment_execution.py``):
  ``docker compose pull`` with deployment-owned compose configuration; no
  per-job credential injection and no source-token plumbing.

Source PAT A (``GITHUB_TOKEN``/model profile token) must never reach registry
calls/config, and no GitHub username (``/user`` actor) lookup may run for
registry auth, with or without an explicit registry identity B.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobBackendError,
    ContainerJobFailureClass,
    RegistryAuthorization,
)
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from moonmind.workflows.temporal.runtime import registry_auth_resolve
from moonmind.workflows.temporal.runtime.registry_auth_resolve import (
    RegistryAuthResolutionError,
    RegistryCredential,
)

JOB_ID = "container-job:" + "e" * 32
IMAGE = "ghcr.io/org/app:1"
DIGEST_ID = "sha256:" + "c" * 64
SOURCE_PAT_A = "ghp_source-pat-A-should-never-be-registry-auth"
REGISTRY_USER_B = "registry-identity-b"
REGISTRY_TOKEN_B = "registry-token-b-should-be-the-only-secret"


def _private_request(*, workspace: Path) -> ContainerJobActivityRequest:
    return ContainerJobActivityRequest.model_validate(
        {
            "jobId": JOB_ID,
            "ownershipToken": f"{JOB_ID}:v1",
            "resolvedWorkspaceRef": str(workspace),
            "request": {
                "idempotencyKey": "issue-4012",
                "source": {"source": "workflow"},
                "spec": {
                    "image": IMAGE,
                    "workspaceRef": {"kind": "sandbox", "workspaceId": "workspace"},
                    "registryCredentialRef": "db://registry-b",
                    "pullPolicy": "if-missing",
                    "resources": {"cpuMillis": 1000, "memoryMiB": 512},
                },
            },
            "registryAuthorization": RegistryAuthorization(
                authorized=True,
                registry="ghcr.io",
                repository="org/app",
                reference=IMAGE,
                credentialRef="db://registry-b",
                scope="org/*",
            ).model_dump(by_alias=True, exclude_none=True),
        }
    )


def _public_request(*, workspace: Path) -> ContainerJobActivityRequest:
    return ContainerJobActivityRequest.model_validate(
        {
            "jobId": JOB_ID,
            "ownershipToken": f"{JOB_ID}:v1",
            "resolvedWorkspaceRef": str(workspace),
            "request": {
                "idempotencyKey": "issue-4012-public",
                "source": {"source": "workflow"},
                "spec": {
                    "image": "alpine",
                    "workspaceRef": {"kind": "sandbox", "workspaceId": "workspace"},
                    "resources": {"cpuMillis": 1000, "memoryMiB": 512},
                },
            },
        }
    )


def _install_github_lookup_tripwire(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if any GitHub actor lookup transport is touched."""

    def _forbidden_urlopen(*args, **kwargs):  # pragma: no cover - tripwire
        raise AssertionError("GitHub username lookup must not run for registry auth")

    monkeypatch.setattr("urllib.request.urlopen", _forbidden_urlopen)


@pytest.mark.asyncio
async def test_private_pull_never_uses_source_pat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Accept-1 with identity B: only the explicit credential reaches the pull."""
    monkeypatch.setenv("GITHUB_TOKEN", SOURCE_PAT_A)
    monkeypatch.setenv("GHCR_PULL_USER", "ambient-user")
    monkeypatch.setenv("GHCR_PULL_TOKEN", SOURCE_PAT_A)
    _install_github_lookup_tripwire(monkeypatch)

    seen_commands: list[tuple[str, ...]] = []
    seen_configs: list[dict] = []

    async def resolver(ref: str) -> RegistryCredential:
        assert ref == "db://registry-b"
        return RegistryCredential(username=REGISTRY_USER_B, secret=REGISTRY_TOKEN_B)

    async def runner(args):
        cmd = tuple(args)
        seen_commands.append(cmd)
        if cmd[:2] == ("image", "inspect"):
            return (1, b"", b"") if not seen_configs else (0, DIGEST_ID.encode(), b"")
        if "pull" in cmd:
            config_dir = Path(cmd[cmd.index("--config") + 1])
            seen_configs.append(json.loads((config_dir / "config.json").read_text()))
            return 0, b"", b""
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path / "workspaces",
        command_runner=runner,
        registry_auth_resolver=resolver,
        auth_root=tmp_path / "auth",
    )
    result = await backend.acquire_image(_private_request(workspace=tmp_path))

    assert result.resolved_image_ref == DIGEST_ID
    assert seen_configs, "expected one authorized pull"
    auth_entry = seen_configs[0]["auths"]["ghcr.io"]
    assert auth_entry == {"username": REGISTRY_USER_B, "password": REGISTRY_TOKEN_B}
    serialized = json.dumps(seen_configs) + " ".join(" ".join(c) for c in seen_commands)
    assert SOURCE_PAT_A not in serialized
    assert "ambient-user" not in serialized
    assert any("--config" in c and "pull" in c for c in seen_commands)


@pytest.mark.asyncio
async def test_public_pull_without_identity_b_uses_no_source_or_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Accept-1 without identity B: public-anonymous pull carries no auth."""
    monkeypatch.setenv("GITHUB_TOKEN", SOURCE_PAT_A)
    monkeypatch.setenv("GHCR_PULL_USER", "ambient-user")
    monkeypatch.setenv("GHCR_PULL_TOKEN", SOURCE_PAT_A)
    _install_github_lookup_tripwire(monkeypatch)

    async def forbidden_resolver(ref: str) -> RegistryCredential:
        raise AssertionError("public-anonymous path must not query the secret store")

    seen_commands: list[tuple[str, ...]] = []
    pulled = {"done": False}

    async def runner(args):
        cmd = tuple(args)
        seen_commands.append(cmd)
        if cmd[:2] == ("image", "inspect"):
            if pulled["done"]:
                return 0, DIGEST_ID.encode(), b""
            return 1, b"", b"No such image"
        if cmd[0] == "pull":
            assert "--config" not in cmd, "public pull must not materialize auth"
            assert SOURCE_PAT_A.encode() not in b" ".join(cmd)
            pulled["done"] = True
            return 0, b"pulled", b""
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path / "workspaces",
        command_runner=runner,
        registry_auth_resolver=forbidden_resolver,
        auth_root=tmp_path / "auth",
    )
    # Cold cache: image absent so the anonymous pull actually runs.
    result = await backend.acquire_image(_public_request(workspace=tmp_path))

    assert result.resolved_image_ref
    assert any(c[0] == "pull" for c in seen_commands)
    assert all("--config" not in c for c in seen_commands)
    assert not (tmp_path / "auth").exists() or not any(
        (tmp_path / "auth").iterdir()
    )


@pytest.mark.asyncio
async def test_unresolved_credential_fails_closed_without_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Accept-2: secret-store outage never falls back to source/ambient/anonymous."""
    monkeypatch.setenv("GITHUB_TOKEN", SOURCE_PAT_A)
    _install_github_lookup_tripwire(monkeypatch)
    pulls: list[tuple[str, ...]] = []

    async def outage_resolver(ref: str) -> RegistryCredential:
        raise RegistryAuthResolutionError("store unavailable")

    async def runner(args):
        cmd = tuple(args)
        if cmd[:2] == ("image", "inspect"):
            return 1, b"", b""
        if "pull" in cmd or cmd[0] == "pull":
            pulls.append(cmd)
            return 0, b"", b""
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path / "workspaces",
        command_runner=runner,
        registry_auth_resolver=outage_resolver,
        auth_root=tmp_path / "auth",
    )
    with pytest.raises(ContainerJobBackendError) as excinfo:
        await backend.acquire_image(_private_request(workspace=tmp_path))
    assert excinfo.value.failure_class == ContainerJobFailureClass.CREDENTIAL_UNRESOLVED
    assert pulls == [], "no pull may run after an unresolvable credential"


@pytest.mark.asyncio
async def test_malformed_selected_pair_fails_closed(tmp_path: Path) -> None:
    """Accept-2: an incomplete selected pair is rejected, not combined/retried."""

    async def runner(args):
        if tuple(args)[:2] == ("image", "inspect"):
            return 1, b"", b""
        raise AssertionError("no pull may run with a malformed credential")

    async def malformed_resolver(ref: str) -> RegistryCredential:
        raise RegistryAuthResolutionError("incomplete pair")

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path / "workspaces",
        command_runner=runner,
        registry_auth_resolver=malformed_resolver,
        auth_root=tmp_path / "auth",
    )
    with pytest.raises(ContainerJobBackendError) as excinfo:
        await backend.acquire_image(_private_request(workspace=tmp_path))
    assert excinfo.value.failure_class == ContainerJobFailureClass.CREDENTIAL_UNRESOLVED


@pytest.mark.asyncio
async def test_denied_private_credential_fails_without_anonymous_retry(
    tmp_path: Path,
) -> None:
    """Accept-2: registry denial fails at the boundary; no anonymous downgrade."""
    pulls: list[tuple[str, ...]] = []

    async def resolver(ref: str) -> RegistryCredential:
        return RegistryCredential(username=REGISTRY_USER_B, secret=REGISTRY_TOKEN_B)

    async def runner(args):
        cmd = tuple(args)
        if cmd[:2] == ("image", "inspect"):
            return 1, b"", b""
        if "pull" in cmd:
            pulls.append(cmd)
            return 1, b"", b"unauthorized: authentication required"
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path / "workspaces",
        command_runner=runner,
        registry_auth_resolver=resolver,
        auth_root=tmp_path / "auth",
    )
    with pytest.raises(ContainerJobBackendError) as excinfo:
        await backend.acquire_image(_private_request(workspace=tmp_path))
    assert excinfo.value.failure_class == ContainerJobFailureClass.REGISTRY_AUTH_FAILED
    assert len(pulls) == 1, "exactly one authorized attempt, no anonymous retry"
    assert REGISTRY_TOKEN_B not in str(excinfo.value)


@pytest.mark.asyncio
async def test_public_cold_cache_pull_needs_no_credentials_or_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Accept-3: explicit public-anonymous cold-cache acquisition works standalone."""
    for var in ("GITHUB_TOKEN", "GHCR_PULL_USER", "GHCR_PULL_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    _install_github_lookup_tripwire(monkeypatch)

    async def forbidden_resolver(ref: str) -> RegistryCredential:
        raise AssertionError("no managed-secret access for public-anonymous pulls")

    pulls: list[tuple[str, ...]] = []
    present = {"done": False}

    async def runner(args):
        cmd = tuple(args)
        if cmd[:2] == ("image", "inspect"):
            if present["done"]:
                return 0, DIGEST_ID.encode(), b""
            return 1, b"", b"No such image"
        if cmd[0] == "pull":
            pulls.append(cmd)
            present["done"] = True
            return 0, b"pulled", b""
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path / "workspaces",
        command_runner=runner,
        registry_auth_resolver=forbidden_resolver,
        auth_root=tmp_path / "auth",
    )
    result = await backend.acquire_image(_public_request(workspace=tmp_path))

    assert result.resolved_image_ref
    assert len(pulls) == 1
    assert all("--config" not in c for c in pulls)


def test_production_pull_path_has_no_source_credential_reads() -> None:
    """Inventory guard: the authenticated pull path cannot see source credentials."""
    import moonmind.workflows.temporal.container_job_backend as backend_mod

    source = inspect.getsource(backend_mod)
    for token in ("GITHUB_TOKEN", "GHCR_PULL_USER", "GHCR_PULL_TOKEN", "resolve_github"):
        assert token not in source, f"production pull path must not reference {token}"

    resolver_source = inspect.getsource(registry_auth_resolve)
    for token in ("GITHUB_TOKEN", "GHCR_PULL", "resolve_github_token", "/user"):
        assert token not in resolver_source, (
            f"registry resolver must not reference {token}"
        )


def test_single_secret_coherent_pair_has_no_split_reads() -> None:
    """Impl-4 guard: one credential_ref resolves atomically; no split user/token reads."""
    source = inspect.getsource(registry_auth_resolve.resolve_registry_pull_credentials)
    assert "credential_ref" in source or "credential-ref" in source or "reference" in source
    assert "GHCR_PULL_USER" not in source
    assert "GHCR_PULL_TOKEN" not in source
    # The resolver performs exactly one secret-backend read per call.
    assert source.count("resolve_managed_api_key_reference") == 1
