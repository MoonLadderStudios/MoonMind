"""Decouple runtime image acquisition from source credentials (#4012).

Inventory-backed production-pull proof for
MoonLadderStudios/MoonMind#4012. The deployed pull boundary, traced from the
real image-selection and pull composition (not from the GHCR helper alone):

- managed sessions: DinD sidecar plus session images via
  ``DockerCodexManagedSessionController`` against the deployment Docker
  backend (``backend_ref="system"``); auth source is the explicit GHCR pair
  or ambient daemon config for public images only.
- generic/profile-bound Omnigent: digest-pinned refs resolved by
  ``moonmind/omnigent/bootstrap/image_resolution.py``
  (``_resolve_via_docker_pull`` / ``_image_build_identity``) via bare
  ``docker pull`` against the deployment backend; no source PAT is passed.
- container jobs: ``container_job_backend.py`` + ``registry_auth_resolve.py``
  (``registry_authorization`` with an explicit ``registryCredentialRef``,
  per-job ephemeral ``--config`` auth dirs, immediate post-pull cleanup).
- Compose/bootstrap: ``docker-compose.yaml`` image refs and stock-image
  acquisition via bare ``docker pull``; no source PAT.
- legacy worker container path: ``moonmind/agents/codex_worker/worker.py``
  ``_ensure_container_image`` via bare ``docker pull``; no source PAT.

These tests drive the real composition (container-job backend acquire paths
and the Omnigent pull helper) with a source PAT A present and assert it never
reaches registry calls/config and no GitHub username lookup runs, with and
without an explicit registry identity B.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobBackendError,
    ContainerJobFailureClass,
    RegistryAuthorization,
)
from moonmind.workflows.temporal.container_image_acquisition import (
    ImageAcquisitionError,
    classify_pull_failure,
)
from moonmind.workflows.temporal.container_job_backend import (
    DockerContainerJobBackend,
)
from moonmind.workflows.temporal.runtime.managed_api_key_resolve import (
    resolve_ghcr_pull_credentials_for_launch,
)
from moonmind.workflows.temporal.runtime.registry_auth_resolve import (
    RegistryCredential,
)

SOURCE_PAT_A = "ghp-source-PAT-A-0000-not-a-real-token"
IDENTITY_B_USER = "registry-B-user"
IDENTITY_B_TOKEN = "registry-B-token-value-0000"
IMAGE = "ghcr.io/org/app:1"
DIGEST = "sha256:" + "c" * 64


# --------------------------------------------------------------------------- #
# Fakes: managed-secret store sessions and Docker CLI runners.
# --------------------------------------------------------------------------- #


class _FakeAsyncSessionCtx:
    def __init__(self, session: object) -> None:
        self._session = session

    async def __aenter__(self) -> object:
        return self._session

    async def __aexit__(self, *_args: object) -> bool:
        return False


class _FakeScalarResult:
    def __init__(self, secret: object | None) -> None:
        self._secret = secret

    def scalar_one_or_none(self) -> object | None:
        return self._secret


class _FakeLookupSession:
    """Stable in-memory managed-secret store keyed by slug."""

    def __init__(
        self,
        *,
        values: dict[str, str | None] | None = None,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self._values = values or {}
        self._errors = errors or {}
        self.seen_slugs: list[str] = []

    async def execute(self, query) -> _FakeScalarResult:
        slug = str(query.compile().params["slug_1"])
        self.seen_slugs.append(slug)
        if slug in self._errors:
            raise self._errors[slug]
        value = self._values.get(slug)
        secret = None if value is None else SimpleNamespace(ciphertext=value)
        return _FakeScalarResult(secret)


class _FakeSessionMaker:
    def __init__(self, session: _FakeLookupSession | object) -> None:
        self._session = session
        self.calls = 0

    def __call__(self) -> _FakeAsyncSessionCtx:
        self.calls += 1
        return _FakeAsyncSessionCtx(self._session)


class _RotatingLookupSession:
    """Store where the pull token rotates between the paired reads."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, query) -> _FakeScalarResult:
        slug = str(query.compile().params["slug_1"])
        self.calls.append(slug)
        token_reads = sum(1 for seen in self.calls if seen == "GHCR_PULL_TOKEN")
        if slug == "GHCR_PULL_USER":
            value: str | None = "pull-user"
        elif token_reads <= 1:
            value = "pull-token-v1"
        else:
            value = "pull-token-v2"
        return _FakeScalarResult(SimpleNamespace(ciphertext=value))


def _clear_ghcr_deployment_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "GHCR_PULL_USER",
        "GHCR_PULL_TOKEN",
        "MOONMIND_GHCR_PULL_USER_SECRET_REF",
        "MOONMIND_GHCR_PULL_TOKEN_SECRET_REF",
        "WORKFLOW_GHCR_PULL_USER_SECRET_REF",
        "WORKFLOW_GHCR_PULL_TOKEN_SECRET_REF",
    ):
        monkeypatch.delenv(var, raising=False)


def _forbid_source_token_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _unexpected_github_token(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("source GitHub token resolution must not run")

    async def _unexpected_managed_token(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("managed source token lookup must not run")

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve.resolve_github_token_for_launch",
        _unexpected_github_token,
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve.resolve_managed_github_token_from_store",
        _unexpected_managed_token,
    )


def _public_request(
    *, image: str = IMAGE, policy: str = "if-missing", workspace: Path
) -> ContainerJobActivityRequest:
    return ContainerJobActivityRequest.model_validate(
        {
            "jobId": "container-job:" + "a" * 32,
            "ownershipToken": "container-job:" + "a" * 32 + ":v1",
            "resolvedWorkspaceRef": str(workspace),
            "request": {
                "idempotencyKey": "issue-4012-public",
                "source": {"source": "workflow", "workflowId": "mm:4012"},
                "spec": {
                    "image": image,
                    "pullPolicy": policy,
                    "workspaceRef": {"kind": "sandbox", "workspaceId": "workspace"},
                    "resources": {"cpuMillis": 1000, "memoryMiB": 512},
                },
            },
        }
    )


def _private_request(
    *,
    image: str = IMAGE,
    policy: str = "always",
    workspace: Path,
    job_suffix: str,
    credential_ref: str = "db://ghcr",
    authorized: bool = True,
    registry: str = "ghcr.io",
    repository: str = "org/app",
    reference: str | None = None,
) -> ContainerJobActivityRequest:
    job_id = f"container-job:{job_suffix}"
    return ContainerJobActivityRequest.model_validate(
        {
            "jobId": job_id,
            "ownershipToken": f"{job_id}:v1",
            "resolvedWorkspaceRef": str(workspace),
            "request": {
                "idempotencyKey": "issue-4012-private",
                "source": {"source": "workflow", "workflowId": "mm:4012"},
                "spec": {
                    "image": image,
                    "pullPolicy": policy,
                    "workspaceRef": {"kind": "sandbox", "workspaceId": "workspace"},
                    "registryCredentialRef": credential_ref,
                    "resources": {"cpuMillis": 1000, "memoryMiB": 512},
                },
            },
            "registryAuthorization": RegistryAuthorization(
                authorized=authorized,
                registry=registry,
                repository=repository,
                reference=reference or image,
                credentialRef=credential_ref,
                scope="org/*",
            ).model_dump(by_alias=True, exclude_none=True),
        }
    )


class _RecordingDaemon:
    """In-memory Docker CLI stand-in recording every argv and auth config."""

    def __init__(self) -> None:
        self.present: set[str] = set()
        self.commands: list[tuple[str, ...]] = []
        self.pulled_configs: list[dict[str, object]] = []
        self.pull_count = 0
        self.deny_pulls_with: bytes | None = None
        self.pull_delay = 0.0

    async def runner(self, args) -> tuple[int, bytes, bytes]:
        argv = tuple(args)
        self.commands.append(argv)
        if argv[:2] == ("image", "inspect"):
            image = argv[-1]
            if image in self.present:
                return 0, DIGEST.encode(), b""
            return 1, b"", b"No such image"
        if "--config" in argv and "pull" in argv:
            return await self._authorized_pull(argv)
        if argv[0] == "pull":
            image = argv[1]
            self.pull_count += 1
            self.present.add(image)
            return 0, b"pull progress", b""
        return 0, b"", b""

    async def _authorized_pull(
        self, argv: tuple[str, ...]
    ) -> tuple[int, bytes, bytes]:
        config_dir = Path(argv[argv.index("--config") + 1])
        config = json.loads((config_dir / "config.json").read_text())
        self.pulled_configs.append(
            {"dir": str(config_dir), "auth": config["auths"]}
        )
        self.pull_count += 1
        if self.pull_delay:
            await asyncio.sleep(self.pull_delay)
        if self.deny_pulls_with is not None:
            return 1, b"", self.deny_pulls_with
        self.present.add(argv[-1])
        return 0, b"pull progress", b""

    def argv_text(self) -> str:
        return "\n".join(" ".join(cmd) for cmd in self.commands)

    def config_text(self) -> str:
        return json.dumps(self.pulled_configs)


def _backend(
    daemon: _RecordingDaemon, tmp_path: Path, *, resolver=None
) -> DockerContainerJobBackend:
    async def default_resolver(ref: str) -> RegistryCredential:
        return RegistryCredential(username=IDENTITY_B_USER, secret=IDENTITY_B_TOKEN)

    return DockerContainerJobBackend(
        workspace_root=tmp_path / "workspaces",
        command_runner=daemon.runner,
        registry_auth_resolver=resolver or default_resolver,
        auth_root=tmp_path / "auth",
        image_lock_root=tmp_path / "locks",
        pull_lock_poll_seconds=0.01,
        pull_lock_max_wait_seconds=5.0,
    )


# --------------------------------------------------------------------------- #
# acc-1: inventory-backed production pull proof (with and without identity B).
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_production_private_pull_never_sees_source_pat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With explicit registry identity B, source PAT A never reaches pulls."""

    _clear_ghcr_deployment_env(monkeypatch)
    monkeypatch.setenv("GHCR_PULL_USER", IDENTITY_B_USER)
    monkeypatch.setenv("GHCR_PULL_TOKEN", IDENTITY_B_TOKEN)
    monkeypatch.setenv("GITHUB_TOKEN", SOURCE_PAT_A)
    store = _FakeLookupSession()
    monkeypatch.setattr(
        "api_service.db.base.async_session_maker", _FakeSessionMaker(store)
    )
    _forbid_source_token_resolution(monkeypatch)
    launch = {
        "GITHUB_TOKEN": SOURCE_PAT_A,
        "GHCR_PULL_USER": "agent-user",
        "GHCR_PULL_TOKEN": "agent-token",
    }

    creds = await resolve_ghcr_pull_credentials_for_launch(launch)

    assert creds == (IDENTITY_B_USER, IDENTITY_B_TOKEN)
    assert SOURCE_PAT_A not in creds

    daemon = _RecordingDaemon()
    backend = _backend(daemon, tmp_path)
    result = await backend.acquire_image(
        _private_request(workspace=tmp_path, job_suffix="b" * 32)
    )

    assert result.resolved_image_ref == DIGEST
    assert daemon.pull_count == 1
    # The single pull used the authorized per-job transport.
    assert any("--config" in cmd and "pull" in cmd for cmd in daemon.commands)
    assert SOURCE_PAT_A not in daemon.argv_text()
    assert SOURCE_PAT_A not in daemon.config_text()
    # Agent-smuggled launch fields are not registry authentication.
    assert "agent-user" not in daemon.config_text()
    assert "agent-token" not in daemon.config_text()
    assert daemon.pulled_configs[0]["auth"]["ghcr.io"] == {
        "username": IDENTITY_B_USER,
        "password": IDENTITY_B_TOKEN,
    }
    # Ephemeral auth is removed immediately after the pull: only the job
    # directory is removed, so assert this job's directory specifically.
    assert not backend._auth_dir(
        _private_request(workspace=tmp_path, job_suffix="b" * 32)
    ).exists()


@pytest.mark.asyncio
async def test_production_public_pull_without_identity_uses_no_auth(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without identity B, cold public acquisition uses no credentials at all."""

    _clear_ghcr_deployment_env(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", SOURCE_PAT_A)
    store = _FakeLookupSession()
    monkeypatch.setattr(
        "api_service.db.base.async_session_maker", _FakeSessionMaker(store)
    )
    _forbid_source_token_resolution(monkeypatch)

    assert (
        await resolve_ghcr_pull_credentials_for_launch(
            {"GITHUB_TOKEN": SOURCE_PAT_A}
        )
        is None
    )
    # The omitted path queries no secret store beyond the GHCR slug check.
    assert set(store.seen_slugs) <= {"GHCR_PULL_USER", "GHCR_PULL_TOKEN"}

    daemon = _RecordingDaemon()
    backend = _backend(daemon, tmp_path)
    result = await backend.acquire_image(
        _public_request(workspace=tmp_path)
    )

    assert result.resolved_image_ref == DIGEST
    assert daemon.pull_count == 1
    assert all("--config" not in cmd for cmd in daemon.commands)
    assert daemon.pulled_configs == []
    assert SOURCE_PAT_A not in daemon.argv_text()
    assert result.image_observation.provision_action == "pull"
    assert result.image_observation.cache_hit is False


@pytest.mark.asyncio
async def test_omnigent_bare_pull_carries_no_source_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Omnigent pull transport issues bare pulls with no PAT argv."""

    import moonmind.omnigent.bootstrap.image_resolution as image_resolution

    seen: list[list[str]] = []

    async def fake_run(cmd: list[str], timeout: int = 30):
        seen.append(list(cmd))
        if list(cmd)[:2] == ["docker", "pull"]:
            return 0, "", ""
        if list(cmd)[:3] == ["docker", "image", "inspect"]:
            return 0, json.dumps([f"ghcr.io/org/host@{DIGEST}"]), ""
        return 1, "", "unexpected"

    monkeypatch.setattr(image_resolution, "_run", fake_run)
    monkeypatch.setenv("GITHUB_TOKEN", SOURCE_PAT_A)

    async def _forbidden(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("source token resolution must not run")

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve.resolve_github_token_for_launch",
        _forbidden,
    )

    resolved = await image_resolution._resolve_via_docker_pull(
        "ghcr.io/org/host", "1.0"
    )

    assert resolved == f"ghcr.io/org/host@{DIGEST}"
    assert any(cmd[:2] == ["docker", "pull"] for cmd in seen)
    flat = "\n".join(" ".join(cmd) for cmd in seen)
    assert SOURCE_PAT_A not in flat
    assert "--password" not in flat
    assert "--username" not in flat


# --------------------------------------------------------------------------- #
# acc-2: fail-closed rotation and denial without fallback.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_managed_slug_rotation_between_reads_raises_not_mixed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two slug reads straddling a rotation raise instead of mixing a pair."""

    _clear_ghcr_deployment_env(monkeypatch)
    session = _RotatingLookupSession()
    maker = _FakeSessionMaker(session)
    monkeypatch.setattr("api_service.db.base.async_session_maker", maker)
    _forbid_source_token_resolution(monkeypatch)

    with pytest.raises(ValueError, match="mixed|rotation|changed"):
        await resolve_ghcr_pull_credentials_for_launch({})

    assert maker.calls == 1


@pytest.mark.asyncio
async def test_stable_managed_slug_pair_resolves_coherently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_ghcr_deployment_env(monkeypatch)
    session = _FakeLookupSession(
        values={"GHCR_PULL_USER": "pull-user", "GHCR_PULL_TOKEN": "pull-token"}
    )
    maker = _FakeSessionMaker(session)
    monkeypatch.setattr("api_service.db.base.async_session_maker", maker)
    _forbid_source_token_resolution(monkeypatch)

    assert await resolve_ghcr_pull_credentials_for_launch({}) == (
        "pull-user",
        "pull-token",
    )
    assert maker.calls == 1
    assert set(session.seen_slugs) <= {"GHCR_PULL_USER", "GHCR_PULL_TOKEN"}


@pytest.mark.asyncio
async def test_denied_private_credentials_fail_closed_without_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A denied pull raises once: no anonymous retry, no second identity."""

    _clear_ghcr_deployment_env(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", SOURCE_PAT_A)

    daemon = _RecordingDaemon()
    daemon.deny_pulls_with = f"denied: invalid token {IDENTITY_B_TOKEN}".encode()
    backend = _backend(daemon, tmp_path)
    request = _private_request(workspace=tmp_path, job_suffix="d" * 32)

    with pytest.raises(ContainerJobBackendError) as excinfo:
        await backend.acquire_image(request)

    assert excinfo.value.failure_class == ContainerJobFailureClass.REGISTRY_AUTH_FAILED
    # Exactly one registry attempt, on the authorized transport.
    assert daemon.pull_count == 1
    assert all("--config" in cmd for cmd in daemon.commands if "pull" in cmd)
    # Secret-safe: the denied token is redacted, the source PAT never appears.
    assert IDENTITY_B_TOKEN not in str(excinfo.value)
    assert "[redacted]" in str(excinfo.value)
    assert SOURCE_PAT_A not in str(excinfo.value)
    assert SOURCE_PAT_A not in daemon.argv_text()
    # The owning operation's ephemeral config is gone.
    assert not backend._auth_dir(request).exists()


# --------------------------------------------------------------------------- #
# acc-4: concurrent-pull isolation and cancellation.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_concurrent_private_pulls_isolate_ephemeral_config(
    tmp_path: Path,
) -> None:
    """Two pulls with different identities isolate temp config and digests."""

    daemon = _RecordingDaemon()
    daemon.pull_delay = 0.05
    calls: list[str] = []

    async def resolver(ref: str) -> RegistryCredential:
        calls.append(ref)
        if ref == "db://ghcr-a":
            return RegistryCredential(username="alice", secret="tok-a-0000")
        if ref == "db://ghcr-b":
            return RegistryCredential(username="bob", secret="tok-b-0000")
        raise AssertionError(f"unexpected credential ref {ref}")

    backend = _backend(daemon, tmp_path, resolver=resolver)
    request_a = _private_request(
        workspace=tmp_path, job_suffix="a" * 32, credential_ref="db://ghcr-a"
    )
    request_b = _private_request(
        workspace=tmp_path, job_suffix="b" * 32, credential_ref="db://ghcr-b"
    )

    result_a, result_b = await asyncio.gather(
        backend.acquire_image(request_a),
        backend.acquire_image(request_b),
    )

    assert daemon.pull_count == 2
    assert sorted(calls) == ["db://ghcr-a", "db://ghcr-b"]
    dirs = [entry["dir"] for entry in daemon.pulled_configs]
    assert len(set(dirs)) == 2
    by_user = {
        entry["auth"]["ghcr.io"]["username"]: entry["auth"]["ghcr.io"]["password"]
        for entry in daemon.pulled_configs
    }
    assert by_user == {"alice": "tok-a-0000", "bob": "tok-b-0000"}
    # Exact selected image/backend preserved for both; no substitution.
    assert result_a.resolved_image_ref == DIGEST
    assert result_b.resolved_image_ref == DIGEST
    assert not backend._auth_dir(request_a).exists()
    assert not backend._auth_dir(request_b).exists()


@pytest.mark.asyncio
async def test_cancelled_private_pull_cleans_up_only_own_config(
    tmp_path: Path,
) -> None:
    """Cancellation removes the owning config and preserves other operations."""

    daemon = _RecordingDaemon()
    release = asyncio.Event()

    async def blocking_runner(args):
        argv = tuple(args)
        daemon.commands.append(argv)
        if argv[:2] == ("image", "inspect"):
            return 1, b"", b"No such image"
        if "pull" in argv:
            await release.wait()
            return 0, b"", b""
        return 0, b"", b""

    async def resolving_runner(ref: str) -> RegistryCredential:
        return RegistryCredential(username="alice", secret="tok-a-0000")

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path / "workspaces",
        command_runner=blocking_runner,
        registry_auth_resolver=resolving_runner,
        auth_root=tmp_path / "auth",
        image_lock_root=tmp_path / "locks",
    )
    request = _private_request(workspace=tmp_path, job_suffix="e" * 32)
    sibling = _private_request(workspace=tmp_path, job_suffix="f" * 32)
    sibling_dir = backend._auth_dir(sibling)
    sibling_dir.mkdir(parents=True)
    (sibling_dir / "config.json").write_text('{"auths": {}}')

    task = asyncio.create_task(backend.acquire_image(request))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()

    assert not backend._auth_dir(request).exists()
    assert (sibling_dir / "config.json").read_text() == '{"auths": {}}'


# --------------------------------------------------------------------------- #
# acc-5: transport edge cases with bounded, secret-safe outcomes.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        (
            "Error response from daemon: unauthorized: authentication required",
            ContainerJobFailureClass.IMAGE_PULL_AUTH_FAILED,
        ),
        (
            "denied: requested access to the resource is denied",
            ContainerJobFailureClass.IMAGE_PULL_AUTH_FAILED,
        ),
        (
            "no matching manifest for linux/arm64 in the manifest list",
            ContainerJobFailureClass.IMAGE_PLATFORM_MISMATCH,
        ),
        (
            "WARNING: platform (linux/arm64) does not match the host (linux/amd64)",
            ContainerJobFailureClass.IMAGE_PLATFORM_MISMATCH,
        ),
        (
            "Cannot connect to the Docker daemon at unix:///var/run/docker.sock",
            ContainerJobFailureClass.IMAGE_BACKEND_UNAVAILABLE,
        ),
        (
            "dial tcp 10.0.0.1:443: connection refused",
            ContainerJobFailureClass.IMAGE_BACKEND_UNAVAILABLE,
        ),
        (
            "net/http: request canceled (Client.Timeout exceeded)",
            ContainerJobFailureClass.IMAGE_PULL_TIMEOUT,
        ),
        (
            "manifest unknown: blob unknown to registry",
            ContainerJobFailureClass.IMAGE_NOT_FOUND,
        ),
        (
            "repository does not exist or may require 'docker login'",
            ContainerJobFailureClass.IMAGE_PULL_AUTH_FAILED,
        ),
        ("too many redirects when pulling image", ContainerJobFailureClass.IMAGE),
        (
            "x509: certificate signed by unknown authority",
            ContainerJobFailureClass.IMAGE,
        ),
        ("", ContainerJobFailureClass.IMAGE),
    ],
)
def test_registry_transport_failures_classify_without_echoing_secrets(
    stderr: str, expected: ContainerJobFailureClass
) -> None:
    planted = "tok-secret-planted-0000"
    outcome = classify_pull_failure(f"{stderr} {planted}")
    assert outcome == expected
    assert planted not in outcome.value


@pytest.mark.asyncio
async def test_wrong_registry_endpoint_rejected_before_pull(tmp_path: Path) -> None:
    """Credentials are never sent to an endpoint outside the authorized scope."""

    async def _unexpected_resolver(ref: str) -> RegistryCredential:
        raise AssertionError("credential must not resolve for a wrong endpoint")

    daemon = _RecordingDaemon()
    backend = _backend(daemon, tmp_path, resolver=_unexpected_resolver)
    request = _private_request(
        workspace=tmp_path,
        job_suffix="1" * 32,
        image="docker.io/library/alpine:3",
        registry="ghcr.io",
        repository="org/app",
        reference="docker.io/library/alpine:3",
    )

    with pytest.raises(ContainerJobBackendError) as excinfo:
        await backend.acquire_image(request)

    assert (
        excinfo.value.failure_class
        == ContainerJobFailureClass.REPOSITORY_SCOPE_MISMATCH
    )
    assert daemon.pull_count == 0
    assert daemon.commands == []


@pytest.mark.asyncio
async def test_lost_pull_acknowledgment_is_bounded(tmp_path: Path) -> None:
    """A pull that completes but leaves the image absent fails closed."""

    async def lying_runner(args):
        argv = tuple(args)
        if argv[:2] == ("image", "inspect"):
            return 1, b"", b"No such image"
        if argv[0] == "pull":
            return 0, b"pull progress", b""
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path / "workspaces",
        command_runner=lying_runner,
        image_lock_root=tmp_path / "locks",
        pull_lock_poll_seconds=0.01,
        pull_lock_max_wait_seconds=5.0,
    )

    with pytest.raises(ImageAcquisitionError) as excinfo:
        await backend.acquire_image(_public_request(workspace=tmp_path))

    assert excinfo.value.failure_class == ContainerJobFailureClass.IMAGE
    assert "still absent after a completed pull" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# acc-6: cold acquisition and warm cache qualified separately per consumer.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_public_cold_and_warm_qualified_separately(tmp_path: Path) -> None:
    daemon = _RecordingDaemon()
    backend = _backend(daemon, tmp_path)

    cold = await backend.acquire_image(_public_request(workspace=tmp_path))
    assert daemon.pull_count == 1
    assert cold.image_observation.cache_present is False
    assert cold.image_observation.cache_hit is False
    assert cold.image_observation.provision_action == "pull"
    assert cold.image_observation.resolved_digest == DIGEST
    assert cold.resolved_image_ref == DIGEST

    warm = await backend.acquire_image(_public_request(workspace=tmp_path))
    assert daemon.pull_count == 1
    assert warm.image_observation.cache_present is True
    assert warm.image_observation.cache_hit is True
    assert warm.image_observation.provision_action == "reuse"
    assert warm.resolved_image_ref == cold.resolved_image_ref


@pytest.mark.asyncio
async def test_private_warm_hit_never_validates_credentials(tmp_path: Path) -> None:
    """A warm cached image is not authorization evidence: no pull, no resolve."""

    daemon = _RecordingDaemon()
    daemon.present.add(IMAGE)
    resolver_calls: list[str] = []

    async def counting_resolver(ref: str) -> RegistryCredential:
        resolver_calls.append(ref)
        return RegistryCredential(username=IDENTITY_B_USER, secret=IDENTITY_B_TOKEN)

    backend = _backend(daemon, tmp_path, resolver=counting_resolver)
    result = await backend.acquire_image(
        _private_request(
            workspace=tmp_path, job_suffix="c" * 32, policy="if-missing"
        )
    )

    assert result.resolved_image_ref == DIGEST
    assert daemon.pull_count == 0
    assert resolver_calls == []


@pytest.mark.asyncio
async def test_private_cold_pull_pins_exact_digest(tmp_path: Path) -> None:
    daemon = _RecordingDaemon()
    backend = _backend(daemon, tmp_path)
    result = await backend.acquire_image(
        _private_request(workspace=tmp_path, job_suffix="9" * 32, policy="always")
    )

    assert daemon.pull_count == 1
    assert result.resolved_image_ref == DIGEST
