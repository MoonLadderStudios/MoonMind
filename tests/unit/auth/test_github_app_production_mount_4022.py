"""Production-mount coverage for MoonLadderStudios/MoonMind#4022.

Proves the three verifier gaps are closed through production call sites,
not helper isolation:

* R1/R5: ``RepositoryConnectionService.create_github_app_connection``
  enrolls a verified installation through the existing writer with
  ``request_id`` identity (ambiguous saves converge, forged setup fails).
* R2/R6: ``build_bound_acquirer_for_connection`` is the production
  construction site (``issuer_for_connection`` + ``revision_reader_for``);
  acquisition through it sends exact restrictions and validates scope/expiry.
* R3/R8: ``GitHubService.read_repository_target`` consumes an App
  connection, and ``PublishService.publish`` consumes a bound credential,
  with opaque-token/redaction checks on the real flows.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    RepositoryConnectionAssignment,
    RepositoryConnectionAuditEvent,
    RepositoryConnectionRecord,
    RepositoryRouteDefault,
)
from api_service.services.repository_connections import RepositoryConnectionService

APP_REF = "github-app:moonmind-test"
INSTALLATION_REF = "installation:123"
INSTALLATION_ID = "123"
ACCOUNT = "acme-org"
REPOS = ["acme/repo"]


def _app_connection(**overrides):
    from moonmind.workflows.executions.repository_contract import RepositoryConnection

    payload = {
        "schemaVersion": "moonmind.repository-connection.v1",
        "id": "repository-connection:app",
        "provider": "git",
        "displayName": "App connection",
        "endpointRef": "https://github.com",
        "allowedOperations": ["read", "write"],
        "allowedRepositoryIds": list(REPOS),
        "clientPolicy": {
            "pinnedVersion": "2.46.0",
            "toolBundleRef": "tool-bundle:git-2.46",
            "executableSha256": "sha256:git",
        },
        "credential": {
            "source": "github_app",
            "appRef": APP_REF,
            "installationRef": INSTALLATION_REF,
        },
        "lifecycle": "active",
        "policyRevision": 1,
        "credentialRevision": 1,
        "ownership": {
            "ownerRef": "owner:team-a",
            "scopeType": "system",
            "allowedPrincipalRefs": ["principal:alice"],
        },
        "hostingService": "github",
    }
    payload.update(overrides)
    return RepositoryConnection.model_validate(payload)


def _pat_connection():
    from moonmind.workflows.executions.repository_contract import RepositoryConnection

    return RepositoryConnection.model_validate(
        {
            "schemaVersion": "moonmind.repository-connection.v1",
            "id": "repository-connection:pat",
            "provider": "git",
            "displayName": "PAT connection",
            "endpointRef": "https://github.com",
            "allowedOperations": ["read"],
            "clientPolicy": {
                "pinnedVersion": "2.46.0",
                "toolBundleRef": "tool-bundle:git-2.46",
                "executableSha256": "sha256:git",
            },
            "credential": {
                "source": "secret_ref",
                "credentialRef": {"provider": "db", "key": "team-a-pat"},
            },
            "lifecycle": "active",
            "policyRevision": 1,
            "credentialRevision": 1,
            "ownership": {
                "ownerRef": "owner:team-a",
                "scopeType": "system",
                "allowedPrincipalRefs": ["principal:alice"],
            },
            "hostingService": "github",
        }
    )


def _provider_edge(token="ghs_opaque_mount_xyz", account=ACCOUNT, repos=None):
    async def _post_ok(*, jwt: str, payload: dict):
        assert str(jwt or "").strip()
        # Exact restrictions: repositories + permissions, never omitted.
        assert set(payload.keys()) == {"repositories", "permissions"}
        assert payload["repositories"] == list(REPOS)
        assert payload["permissions"]
        return {
            "token": token,
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(minutes=55)
            ).isoformat(),
            "permissions": dict(payload["permissions"]),
            "repositories": list(payload["repositories"]),
        }

    async def _get_installation(*, jwt: str):
        assert str(jwt or "").strip()
        return {
            "app_ref": APP_REF,
            "installation_ref": INSTALLATION_REF,
            "account": account,
            "repositories": list(REPOS if repos is None else repos),
            "suspended": False,
        }

    return _post_ok, _get_installation


def _factory_kwargs(token="ghs_opaque_mount_xyz", **overrides):
    post, get = _provider_edge(token)
    kwargs = {
        "resolve_secret": lambda _ref: b"fake-pem",
        "make_jwt": lambda _key: "test-jwt",
        "http_post": post,
        "get_installation": get,
        # Operator-configured numeric refs (explicit configuration wins over
        # ref parsing in the production factory).
        "app_id": "123456",
        "installation_id": INSTALLATION_ID,
        "expected_account": ACCOUNT,
        "permitted_repositories": list(REPOS),
    }
    kwargs.update(overrides)
    return kwargs


def test_factory_acquires_app_credential_through_production_site() -> None:
    """R2/R6: the factory (not the helpers) builds the issuing acquirer."""

    from moonmind.auth.bound_acquisition import (
        AccessMode,
        AcquisitionRequest,
        select_repository_authority,
    )
    from moonmind.auth.github_app_wiring import build_bound_acquirer_for_connection
    from moonmind.workflows.executions.repository_contract import (
        RepositoryAssignment,
        RepositoryIdentity,
    )

    conn = _app_connection()
    acquirer = build_bound_acquirer_for_connection(conn, **_factory_kwargs())

    identity = RepositoryIdentity.model_validate(
        {
            "endpoint": "https://github.com",
            "providerRepoId": "repo-id-1",
            "displayName": "acme/repo",
        }
    )
    assignment = RepositoryAssignment.model_validate(
        {
            "connectionId": conn.id,
            "identity": identity.model_dump(by_alias=True, mode="json"),
            "operations": ["read"],
            "revision": 1,
            "verified": True,
        }
    )
    snapshot = select_repository_authority(
        access_mode=AccessMode.EXPLICIT,
        principal_ref="principal:alice",
        principal_scope=("system", None),
        identity=identity,
        role="reader",
        requested_operations=["read"],
        policy_revision=1,
        explicit_connection=conn,
        explicit_assignment=assignment,
    )
    acquired = asyncio.run(
        acquirer.acquire(
            AcquisitionRequest(snapshot=snapshot, execution_owner="exec:mount")
        )
    )
    assert acquired.binding.adapter_kind == "github_app"
    seen: list[bytes] = []
    acquired.credential.use_now(seen.append)
    assert seen[0] == b"ghs_opaque_mount_xyz"


def test_factory_acquires_pat_connection_through_same_site() -> None:
    """R2: PAT connections issue through the same construction site."""

    from moonmind.auth.bound_acquisition import (
        AccessMode,
        AcquisitionRequest,
        select_repository_authority,
    )
    from moonmind.auth.github_app_wiring import build_bound_acquirer_for_connection
    from moonmind.workflows.executions.repository_contract import (
        RepositoryAssignment,
        RepositoryIdentity,
    )

    conn = _pat_connection()
    acquirer = build_bound_acquirer_for_connection(
        conn, resolve_secret=lambda _ref: "pat-mount-token"
    )
    identity = RepositoryIdentity.model_validate(
        {
            "endpoint": "https://github.com",
            "providerRepoId": "repo-id-1",
            "displayName": "acme/repo",
        }
    )
    assignment = RepositoryAssignment.model_validate(
        {
            "connectionId": conn.id,
            "identity": identity.model_dump(by_alias=True, mode="json"),
            "operations": ["read"],
            "revision": 1,
            "verified": True,
        }
    )
    snapshot = select_repository_authority(
        access_mode=AccessMode.EXPLICIT,
        principal_ref="principal:alice",
        principal_scope=("system", None),
        identity=identity,
        role="reader",
        requested_operations=["read"],
        policy_revision=1,
        explicit_connection=conn,
        explicit_assignment=assignment,
    )
    acquired = asyncio.run(
        acquirer.acquire(
            AcquisitionRequest(snapshot=snapshot, execution_owner="exec:mount")
        )
    )
    assert acquired.binding.adapter_kind == "pat"
    seen: list[bytes] = []
    acquired.credential.use_now(seen.append)
    assert seen[0] == b"pat-mount-token"


def test_acquire_bound_headers_uses_factory_end_to_end() -> None:
    """R2/R6: headers helper runs the full factory+acquire+wire path."""

    from moonmind.auth.github_app_wiring import acquire_bound_headers_for_connection

    conn = _app_connection()
    headers, redact = asyncio.run(
        acquire_bound_headers_for_connection(
            conn,
            operations=("read",),
            principal_ref="principal:alice",
            principal_scope=("system", None),
            execution_owner="exec:mount",
            repository_display="acme/repo",
            **_factory_kwargs(token="ghs_opaque_headers_xyz"),
        )
    )
    assert headers["Authorization"] == "Bearer ghs_opaque_headers_xyz"
    assert headers["Accept"] == "application/vnd.github+json"
    assert headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert "ghs_opaque_headers_xyz" in redact


@asynccontextmanager
async def _connection_db(tmp_path: Path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/repository-app-mount.db", future=True
    )
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(
                sync,
                tables=[
                    RepositoryConnectionRecord.__table__,
                    RepositoryConnectionAssignment.__table__,
                    RepositoryRouteDefault.__table__,
                    RepositoryConnectionAuditEvent.__table__,
                ],
            )
        )
    try:
        yield sessions
    finally:
        await engine.dispose()


def _verified_installation():
    return {
        "app_ref": APP_REF,
        "installation_ref": INSTALLATION_REF,
        "account": ACCOUNT,
        "repositories": list(REPOS),
        "suspended": False,
    }


def test_enrollment_mount_persists_and_converges_through_writer(tmp_path: Path) -> None:
    """R1/R5: the service entry point enrolls + converges ambiguous saves."""

    from moonmind.auth.github_app_setup import GitHubAppSetupService

    async def _run() -> None:
        async with _connection_db(tmp_path) as sessions:
            async with sessions() as session:
                service = RepositoryConnectionService(session)
                setup = GitHubAppSetupService(server_secret="mount-server-secret")
                saved = await service.create_github_app_connection(
                    setup_service=setup,
                    provider_installation=_verified_installation(),
                    expected_app_ref=APP_REF,
                    request_id="req:mount-1",
                    connection_id="repository-connection:app",
                    expected_account=ACCOUNT,
                    permitted_repositories=list(REPOS),
                    operator_import=True,
                    principal_ref="principal:alice",
                    principal_scope=("system", None),
                    owner_ref="owner:team-a",
                    actor_ref="principal:alice",
                )
                assert saved.id == "repository-connection:app"
                assert saved.credential.source == "github_app"

                # Ambiguous retry (same request identity, other connection id)
                # converges on the first connection instead of a second row.
                retry = await service.create_github_app_connection(
                    setup_service=setup,
                    provider_installation=_verified_installation(),
                    expected_app_ref=APP_REF,
                    request_id="req:mount-1",
                    connection_id="repository-connection:app-retry",
                    expected_account=ACCOUNT,
                    permitted_repositories=list(REPOS),
                    existing_by_request={"req:mount-1": "repository-connection:app"},
                    operator_import=True,
                    principal_ref="principal:alice",
                    principal_scope=("system", None),
                    owner_ref="owner:team-a",
                    actor_ref="principal:alice",
                )
                assert retry.id == "repository-connection:app"

                exported = await service.export_snapshot_connections(
                    principal_ref="principal:alice",
                    principal_scope=("system", None),
                )
                ids = sorted(conn.id for conn in exported)
                assert ids == ["repository-connection:app"]

    asyncio.run(_run())


def test_enrollment_mount_rejects_forged_setup_state(tmp_path: Path) -> None:
    """R1/R5: the mounted path rejects forged browser setup callbacks."""

    import pytest

    from moonmind.auth.github_app_setup import GitHubAppSetupService
    from moonmind.workflows.executions.repository_contract import RepositoryRouteError

    async def _run() -> None:
        async with _connection_db(tmp_path) as sessions:
            async with sessions() as session:
                service = RepositoryConnectionService(session)
                setup = GitHubAppSetupService(server_secret="mount-server-secret")
                with pytest.raises(RepositoryRouteError):
                    await service.create_github_app_connection(
                        setup_service=setup,
                        provider_installation=_verified_installation(),
                        expected_app_ref=APP_REF,
                        request_id="req:mount-forged",
                        connection_id="repository-connection:app",
                        expected_account=ACCOUNT,
                        permitted_repositories=list(REPOS),
                        state="forged-state",
                        installation_ref=INSTALLATION_REF,
                        caller_principal="principal:alice",
                        caller_scope=("system", None),
                        destination_connection_id="repository-connection:app",
                        principal_ref="principal:alice",
                        principal_scope=("system", None),
                        owner_ref="owner:team-a",
                        actor_ref="principal:alice",
                    )

    asyncio.run(_run())


def _github_http_mock():
    seen: dict[str, str] = {}

    class _MockResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self):
            if seen["path"].endswith("/commits/refs%2Fheads%2Fmain"):
                return {"sha": "abc123" * 10, "commit": {"tree": {"sha": "def456" * 10}}}
            return {"default_branch": "main"}

    class _MockClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None):
            seen["authorization"] = (headers or {}).get("Authorization", "")
            seen["path"] = url
            return _MockResponse()

    return seen, _MockClient


def test_read_path_consumes_app_connection_with_bound_headers() -> None:
    """R3: the existing read path issues via the bound App credential."""

    from moonmind.workflows.adapters.github_service import GitHubService

    conn = _app_connection()
    seen, mock_client = _github_http_mock()
    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client(),
    ):
        # The production read path needs the factory's provider edge; the
        # App adapter seam is injectable, the HTTP assertions below are real.
        import moonmind.auth.github_app_wiring as wiring

        real_factory = wiring.build_bound_acquirer_for_connection

        def _factory_with_edge(connection, **kwargs):
            merged = _factory_kwargs("ghs_opaque_read_xyz")
            for key, value in kwargs.items():
                if value is None:
                    continue
                if isinstance(value, (str, bytes, tuple, list, dict)) and not value:
                    continue
                merged[key] = value
            return real_factory(connection, **merged)

        with patch.object(wiring, "build_bound_acquirer_for_connection", _factory_with_edge):
            result = asyncio.run(
                GitHubService().read_repository_target("acme/repo", connection=conn)
            )
    assert result["ref"] == "refs/heads/main"
    assert result["revision"]
    assert result["contentDigest"].startswith("git-tree:")
    assert seen["authorization"] == "Bearer ghs_opaque_read_xyz"


def _fake_acquired(token: str):
    class _Credential:
        def use_now(self, fn):
            return fn(token.encode())

    class _Binding:
        binding_digest = "binding:mount"
        connection_id = "repository-connection:app"
        operation_id = "op:mount"
        adapter_kind = "github_app"
        route_id = "route:mount"

    return SimpleNamespace(binding=_Binding(), credential=_Credential())


def test_publish_push_consumes_bound_credential(tmp_path: Path, monkeypatch) -> None:
    """R8: the existing push flow consumes the bound App credential."""

    from moonmind.publish.service import PublishService

    for var in ("GITHUB_TOKEN", "GH_TOKEN", "WORKFLOW_GITHUB_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    calls: list[dict] = []

    async def _run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        if command[:3] == ["git", "status", "--porcelain"]:
            return SimpleNamespace(stdout=" M file.py\n")
        return SimpleNamespace(stdout="")

    result = asyncio.run(
        PublishService().publish(
            job_id=uuid4(),
            instruction="make change",
            publish_mode="branch",
            publish_base_branch=None,
            runtime_mode="codex",
            repo_dir=tmp_path,
            run_command=_run,
            repo="acme/repo",
            bound_credential=_fake_acquired("ghs_opaque_push_xyz"),
        )
    )
    assert result is not None and result.status == "published"
    push_call = next(call for call in calls if call["command"][:2] == ["git", "push"])
    assert push_call["env"]["GITHUB_TOKEN"] == "ghs_opaque_push_xyz"
    assert push_call["env"]["GH_TOKEN"] == "ghs_opaque_push_xyz"
    assert "ghs_opaque_push_xyz" in push_call["redaction_values"]


def test_publish_gh_fallback_consumes_bound_credential(tmp_path: Path) -> None:
    """R8: the deferred gh flow consumes the bound App credential."""

    from moonmind.publish.service import PublishService

    calls: list[dict] = []

    async def _run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        if command[:3] == ["git", "status", "--porcelain"]:
            return SimpleNamespace(stdout=" M file.py\n")
        return SimpleNamespace(stdout="")

    with patch(
        "moonmind.publish.service.verify_cli_is_executable", return_value=None
    ):
        result = asyncio.run(
            PublishService().publish(
                job_id=uuid4(),
                instruction="make change",
                publish_mode="pr",
                publish_base_branch="main",
                runtime_mode="codex",
                repo_dir=tmp_path,
                run_command=_run,
                repo=None,
                bound_credential=_fake_acquired("ghs_opaque_gh_xyz"),
            )
        )
    assert result is not None and result.status == "published"
    gh_call = next(call for call in calls if call["command"][:2] == ["gh", "pr"])
    assert gh_call["env"]["GH_TOKEN"] == "ghs_opaque_gh_xyz"
    assert gh_call["env"]["GITHUB_TOKEN"] == "ghs_opaque_gh_xyz"
    assert "ghs_opaque_gh_xyz" in gh_call["redaction_values"]
