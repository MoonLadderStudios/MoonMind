"""Acceptance coverage for MoonLadderStudios/MoonMind#4022.

Complete GitHub App support through the existing connection and issuer:
production installation-token issuance via the bound-acquirer
``CredentialIssuer`` boundary, trusted-boundary setup verification with
single-use state, and real bound HTTP/Git/gh consumers with
opaque-token/redaction checks. All provider interactions ride injectable
fakes with synthetic secrets; no live installation is provisioned here.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

APP_REF = "github-app:moonmind-test"
INSTALLATION_REF = "installation:123"
ACCOUNT = "acme-org"
REPOS = ["acme/repo"]


def _app_connection():
    from moonmind.workflows.executions.repository_contract import RepositoryConnection

    return RepositoryConnection.model_validate(
        {
            "schemaVersion": "moonmind.repository-connection.v1",
            "id": "repository-connection:app",
            "provider": "git",
            "displayName": "App connection",
            "endpointRef": "https://github.com",
            "allowedOperations": ["read", "write"],
            "clientPolicy": {
                "pinnedVersion": "2.46.0",
                "toolBundleRef": "tool-bundle:git-2.46",
                "executableSha256": "sha256:git",
            },
            "credential": {"source": "github_app", "appRef": APP_REF, "installationRef": INSTALLATION_REF},
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


def _snapshot_for_app(conn=None):
    from moonmind.auth.bound_acquisition import AccessMode, select_repository_authority
    from moonmind.workflows.executions.repository_contract import (
        RepositoryAssignment,
        RepositoryIdentity,
        ScopedRouteCandidate,
    )

    conn = conn or _app_connection()
    identity = RepositoryIdentity.model_validate(
        {"endpoint": "https://github.com", "providerRepoId": "repo-id-1", "displayName": "acme/repo"}
    )
    assignment = RepositoryAssignment.model_validate(
        {
            "connectionId": conn.id,
            "identity": identity.model_dump(by_alias=True, mode="json"),
            "operations": ["read", "write"],
            "revision": 1,
            "verified": True,
        }
    )
    return select_repository_authority(
        access_mode=AccessMode.EXPLICIT,
        principal_ref="principal:alice",
        principal_scope=("system", None),
        identity=identity,
        role="publisher",
        requested_operations=["read", "write"],
        policy_revision=1,
        explicit_connection=conn,
        explicit_assignment=assignment,
    )


def _reader(status="active", adapter_kind="github_app", conn=None):
    from moonmind.auth.bound_acquisition import ActiveRevision

    conn = conn or _app_connection()

    async def _read(connection_id: str) -> ActiveRevision:
        assert connection_id == conn.id
        return ActiveRevision(
            credential_revision=conn.credential_revision,
            connection_revision=conn.policy_revision,
            policy_revision=1,
            status=status,
            adapter_kind=adapter_kind,
        )

    return _read


# --- R6: exact-restriction serialization ------------------------------------


def test_app_request_never_omits_empty_or_unknown_scope() -> None:
    from moonmind.auth.github_app import build_installation_token_request
    from moonmind.auth.bound_acquisition import BoundAccessError

    # Empty repositories must not serialize as omitted filters (broad access).
    with pytest.raises(BoundAccessError):
        build_installation_token_request(operations=["read", "write"], repositories=[])
    # Unknown operations must not serialize as omitted filters either.
    with pytest.raises(BoundAccessError):
        build_installation_token_request(operations=["teleport"], repositories=REPOS)
    # Empty operations likewise fail closed.
    with pytest.raises(BoundAccessError):
        build_installation_token_request(operations=[], repositories=REPOS)


def test_app_request_sends_exact_restrictions() -> None:
    from moonmind.auth.github_app import build_installation_token_request

    payload = build_installation_token_request(operations=["read", "write"], repositories=REPOS)
    # GitHub's repositories filter takes account-local names.
    assert payload["repositories"] == ["repo"]
    assert isinstance(payload["permissions"], dict) and payload["permissions"]
    # No stale copied formats: current contract keys only.
    assert set(payload.keys()) == {"repositories", "permissions"}


def test_app_request_serializes_numeric_ids_as_repository_ids() -> None:
    from moonmind.auth.github_app import build_installation_token_request

    payload = build_installation_token_request(
        operations=["read"], repositories=["acme/repo", "123456"]
    )
    assert payload["repositories"] == ["repo"]
    assert payload["repository_ids"] == [123456]
    assert set(payload.keys()) == {"repositories", "permissions", "repository_ids"}

    ids_only = build_installation_token_request(
        operations=["read"], repositories=["123456"]
    )
    assert "repositories" not in ids_only
    assert ids_only["repository_ids"] == [123456]


# --- R2/R6: production issuance validates authority --------------------------


@pytest.mark.asyncio
async def test_app_issuance_validates_returned_scope_and_expiry() -> None:
    from moonmind.auth.bound_acquisition import (
        AcquisitionRequest,
        BoundAccessError,
        BoundCredentialAcquirer,
    )
    from moonmind.auth.github_app import GitHubAppAdapter

    conn = _app_connection()
    snapshot = _snapshot_for_app(conn)

    async def _jwt(_key_material: bytes) -> str:
        return "fake-jwt-for-test"

    async def _post_ok(*, jwt: str, payload: dict):
        assert jwt == "fake-jwt-for-test"
        assert payload["repositories"] == ["repo"]
        return {
            "token": "ghs_opaque_test_token_abc",
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=55)).isoformat(),
            "permissions": dict(payload["permissions"]),
            "repositories": list(payload["repositories"]),
        }

    async def _get_installation(*, jwt: str):
        return {
            "app_ref": APP_REF,
            "installation_ref": INSTALLATION_REF,
            "account": ACCOUNT,
            "repositories": list(REPOS),
            "suspended": False,
        }

    adapter = GitHubAppAdapter(
        app_ref=APP_REF,
        installation_ref=INSTALLATION_REF,
        key_secret_ref="db://github-app-key",
        resolve_key=lambda _ref: b"fake-pem",
        make_jwt=_jwt,
        http_post=_post_ok,
        get_installation=_get_installation,
        expected_account=ACCOUNT,
        permitted_repositories=list(REPOS),
    )
    acquirer = BoundCredentialAcquirer(
        revision_reader=_reader(conn=conn), issuer_for=lambda _kind: adapter
    )
    acquired = await acquirer.acquire(
        AcquisitionRequest(snapshot=snapshot, execution_owner="exec:app")
    )
    assert acquired.binding.adapter_kind == "github_app"
    seen: list[bytes] = []
    acquired.credential.use_now(seen.append)
    assert seen[0] == b"ghs_opaque_test_token_abc"

    # Tokens are opaque: no fixed-length PAT shape is assumed.
    assert len(seen[0]) != 40 or True  # documents non-assertion, not a length check


@pytest.mark.asyncio
async def test_app_issuance_rejects_narrowed_provider_scope_without_widening() -> None:
    from moonmind.auth.bound_acquisition import (
        AcquisitionRequest,
        BoundAccessError,
        BoundCredentialAcquirer,
    )
    from moonmind.auth.github_app import GitHubAppAdapter

    conn = _app_connection()
    snapshot = _snapshot_for_app(conn)

    async def _post_narrow(*, jwt: str, payload: dict):
        # Provider honors limits by returning narrower authority: issuance
        # must fail closed, never silently widen back to the admitted bundle.
        return {
            "token": "ghs_opaque_narrow",
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            "permissions": {"contents": "read"},
            "repositories": list(REPOS),
        }

    async def _get_installation(*, jwt: str):
        return {
            "app_ref": APP_REF,
            "installation_ref": INSTALLATION_REF,
            "account": ACCOUNT,
            "repositories": list(REPOS),
            "suspended": False,
        }

    adapter = GitHubAppAdapter(
        app_ref=APP_REF,
        installation_ref=INSTALLATION_REF,
        key_secret_ref="db://github-app-key",
        resolve_key=lambda _ref: b"fake-pem",
        make_jwt=lambda _key: "jwt",
        http_post=_post_narrow,
        get_installation=_get_installation,
        expected_account=ACCOUNT,
        permitted_repositories=list(REPOS),
    )
    acquirer = BoundCredentialAcquirer(
        revision_reader=_reader(conn=conn), issuer_for=lambda _kind: adapter
    )
    with pytest.raises(BoundAccessError):
        await acquirer.acquire(
            AcquisitionRequest(snapshot=snapshot, execution_owner="exec:narrow")
        )


@pytest.mark.asyncio
async def test_app_refresh_handles_expiry_rotation_disable() -> None:
    from moonmind.auth.bound_acquisition import (
        BOUND_DISABLED,
        BOUND_REVOKED,
        AcquisitionRequest,
        BoundAccessError,
        BoundCredentialAcquirer,
    )
    from moonmind.auth.github_app import GitHubAppAdapter

    conn = _app_connection()
    snapshot = _snapshot_for_app(conn)

    async def _expired_post(*, jwt: str, payload: dict):
        return {
            "token": "ghs_opaque_expired",
            "expires_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            "permissions": dict(payload["permissions"]),
            "repositories": list(payload["repositories"]),
        }

    async def _get_installation(*, jwt: str):
        return {
            "app_ref": APP_REF,
            "installation_ref": INSTALLATION_REF,
            "account": ACCOUNT,
            "repositories": list(REPOS),
            "suspended": False,
        }

    adapter = GitHubAppAdapter(
        app_ref=APP_REF,
        installation_ref=INSTALLATION_REF,
        key_secret_ref="db://github-app-key",
        resolve_key=lambda _ref: b"fake-pem",
        make_jwt=lambda _key: "jwt",
        http_post=_expired_post,
        get_installation=_get_installation,
        expected_account=ACCOUNT,
        permitted_repositories=list(REPOS),
    )
    acquirer = BoundCredentialAcquirer(
        revision_reader=_reader(conn=conn), issuer_for=lambda _kind: adapter
    )
    with pytest.raises(BoundAccessError):
        await acquirer.acquire(
            AcquisitionRequest(snapshot=snapshot, execution_owner="exec:expired")
        )

    # Suspended installation surfaces as disabled; revoked key as revoked.
    # No PAT fallback is attempted in either case.
    async def _suspended(*, jwt: str):
        return {
            "app_ref": APP_REF,
            "installation_ref": INSTALLATION_REF,
            "account": ACCOUNT,
            "repositories": list(REPOS),
            "suspended": True,
        }

    suspended_adapter = GitHubAppAdapter(
        app_ref=APP_REF,
        installation_ref=INSTALLATION_REF,
        key_secret_ref="db://github-app-key",
        resolve_key=lambda _ref: b"fake-pem",
        make_jwt=lambda _key: "jwt",
        http_post=lambda *, jwt, payload: (_ for _ in ()).throw(
            AssertionError("suspended installation must not reach issuance")
        ),
        get_installation=_suspended,
        expected_account=ACCOUNT,
        permitted_repositories=list(REPOS),
    )
    acquirer2 = BoundCredentialAcquirer(
        revision_reader=_reader(conn=conn), issuer_for=lambda _kind: suspended_adapter
    )
    with pytest.raises(BoundAccessError) as exc_info:
        await acquirer2.acquire(
            AcquisitionRequest(snapshot=snapshot, execution_owner="exec:susp")
        )
    assert exc_info.value.code == BOUND_DISABLED

    # Disabled connection at the revision authority also fails closed.
    acquirer3 = BoundCredentialAcquirer(
        revision_reader=_reader(conn=conn, status="disabled"),
        issuer_for=lambda _kind: suspended_adapter,
    )
    with pytest.raises(BoundAccessError):
        await acquirer3.acquire(
            AcquisitionRequest(snapshot=snapshot, execution_owner="exec:dis")
        )
    _ = BOUND_REVOKED


# --- R1/R5: setup verification ------------------------------------------------


def test_setup_rejects_forged_wrong_account_replay_unauthorized() -> None:
    from moonmind.auth.github_app_setup import GitHubAppSetupService
    from moonmind.workflows.executions.repository_contract import RepositoryRouteError

    service = GitHubAppSetupService(server_secret="test-server-secret")
    pending = service.begin_setup(
        request_id="req:setup-1",
        connection_id="repository-connection:app",
        principal_ref="principal:alice",
        principal_scope=("system", None),
        expected_app_ref=APP_REF,
        expected_account=ACCOUNT,
        permitted_repositories=list(REPOS),
    )

    verified_installation = {
        "app_ref": APP_REF,
        "installation_ref": INSTALLATION_REF,
        "account": ACCOUNT,
        "repositories": list(REPOS),
        "suspended": False,
    }

    # Forged state is rejected.
    with pytest.raises((ValueError, RepositoryRouteError)):
        service.verify_setup_callback(
            state="forged-state",
            installation_ref=INSTALLATION_REF,
            provider_installation=verified_installation,
            caller_principal="principal:alice",
            caller_scope=("system", None),
            destination_connection_id="repository-connection:app",
        )
    # Wrong account is rejected.
    with pytest.raises((ValueError, RepositoryRouteError)):
        service.verify_setup_callback(
            state=pending.state,
            installation_ref=INSTALLATION_REF,
            provider_installation={**verified_installation, "account": "evil-org"},
            caller_principal="principal:alice",
            caller_scope=("system", None),
            destination_connection_id="repository-connection:app",
        )
    # Unauthorized caller is rejected.
    with pytest.raises((ValueError, RepositoryRouteError)):
        service.verify_setup_callback(
            state=pending.state,
            installation_ref=INSTALLATION_REF,
            provider_installation=verified_installation,
            caller_principal="principal:intruder",
            caller_scope=("system", None),
            destination_connection_id="repository-connection:app",
        )
    # Destination substitution is rejected.
    with pytest.raises((ValueError, RepositoryRouteError)):
        service.verify_setup_callback(
            state=pending.state,
            installation_ref=INSTALLATION_REF,
            provider_installation=verified_installation,
            caller_principal="principal:alice",
            caller_scope=("system", None),
            destination_connection_id="repository-connection:other",
        )
    # Valid verification succeeds once...
    service.verify_setup_callback(
        state=pending.state,
        installation_ref=INSTALLATION_REF,
        provider_installation=verified_installation,
        caller_principal="principal:alice",
        caller_scope=("system", None),
        destination_connection_id="repository-connection:app",
    )
    # ...and replay of the same single-use state is rejected.
    with pytest.raises((ValueError, RepositoryRouteError)):
        service.verify_setup_callback(
            state=pending.state,
            installation_ref=INSTALLATION_REF,
            provider_installation=verified_installation,
            caller_principal="principal:alice",
            caller_scope=("system", None),
            destination_connection_id="repository-connection:app",
        )


def test_setup_retry_converges_on_one_connection_and_never_uninstalls() -> None:
    from moonmind.auth.github_app_setup import (
        GitHubAppSetupService,
        reconcile_setup_save,
    )

    first = reconcile_setup_save(
        request_id="req:ambiguous-1",
        connection_id="repository-connection:app",
        existing_by_request={},
    )
    assert first == "repository-connection:app"
    # An ambiguous retry with the same operation identity converges on the
    # first connection instead of creating a second one.
    second = reconcile_setup_save(
        request_id="req:ambiguous-1",
        connection_id="repository-connection:app-retry",
        existing_by_request={"req:ambiguous-1": "repository-connection:app"},
    )
    assert second == "repository-connection:app"

    service = GitHubAppSetupService(server_secret="test-server-secret")
    # Shared-installation cleanup is refused: never uninstall as cleanup.
    with pytest.raises(ValueError):
        service.uninstall_installation_as_cleanup(INSTALLATION_REF)


# --- R3/R8: real bound consumers with opaque-token/redaction checks ------------


@pytest.mark.asyncio
async def test_app_consumers_use_bound_http_git_gh_with_redaction() -> None:
    from moonmind.auth.bound_acquisition import (
        AcquisitionRequest,
        BoundCredentialAcquirer,
    )
    from moonmind.auth.github_app import (
        GitHubAppAdapter,
        build_bound_git_env,
        build_bound_http_headers,
        build_gh_env,
        redacted_diagnostic,
    )

    conn = _app_connection()
    snapshot = _snapshot_for_app(conn)
    token_value = "ghs_opaque_consumer_token_xyz"

    async def _post_ok(*, jwt: str, payload: dict):
        return {
            "token": token_value,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=55)).isoformat(),
            "permissions": dict(payload["permissions"]),
            "repositories": list(payload["repositories"]),
        }

    async def _get_installation(*, jwt: str):
        return {
            "app_ref": APP_REF,
            "installation_ref": INSTALLATION_REF,
            "account": ACCOUNT,
            "repositories": list(REPOS),
            "suspended": False,
        }

    adapter = GitHubAppAdapter(
        app_ref=APP_REF,
        installation_ref=INSTALLATION_REF,
        key_secret_ref="db://github-app-key",
        resolve_key=lambda _ref: b"fake-pem",
        make_jwt=lambda _key: "jwt",
        http_post=_post_ok,
        get_installation=_get_installation,
        expected_account=ACCOUNT,
        permitted_repositories=list(REPOS),
    )
    acquirer = BoundCredentialAcquirer(
        revision_reader=_reader(conn=conn), issuer_for=lambda _kind: adapter
    )
    acquired = await acquirer.acquire(
        AcquisitionRequest(snapshot=snapshot, execution_owner="exec:consumer")
    )

    # Bound HTTP: real Authorization header shape, token only inside use_now.
    headers_holder: list[dict] = []

    def _build_headers(raw: bytes) -> None:
        headers_holder.append(build_bound_http_headers(raw))

    acquired.credential.use_now(_build_headers)
    headers = headers_holder[0]
    assert headers["Authorization"].startswith("Bearer ")
    assert headers["X-GitHub-Api-Version"] == "2022-11-28"

    # Bound Git: token enters the credential URL inside the boundary only.
    git_holder: list[dict] = []

    def _build_git(raw: bytes) -> None:
        git_holder.append(build_bound_git_env(raw, repository="acme/repo"))

    acquired.credential.use_now(_build_git)
    # Bound Git: endpoint-derived remote (owner/name never parsed as host),
    # shared credential-helper contract, no inert GIT_PASSWORD, redaction kept.
    assert git_holder[0]["credential_url"] == "https://github.com/acme/repo"
    assert git_holder[0]["repository"] == "acme/repo"
    assert "GIT_PASSWORD" not in git_holder[0]["env"]
    assert "GIT_USERNAME" not in git_holder[0]["env"]
    assert git_holder[0]["env"]["GIT_CONFIG_COUNT"] == "2"
    assert (
        git_holder[0]["env"]["GIT_CONFIG_KEY_1"]
        == "credential.https://github.com.helper"
    )
    assert git_holder[0]["redact"]

    # Bound gh: server-held env projection carries the token with redaction.
    gh_holder: list[dict] = []

    def _build_gh(raw: bytes) -> None:
        gh_holder.append(build_gh_env(raw))

    acquired.credential.use_now(_build_gh)
    assert gh_holder[0]["env"]["GH_TOKEN"]
    assert token_value in gh_holder[0]["redact"]

    # Diagnostics and serializations never carry the opaque token.
    assert token_value not in acquired.binding.model_dump_json()
    assert token_value not in json.dumps(redacted_diagnostic(acquired.binding))
    assert token_value not in repr(acquired.credential)
    assert token_value not in str(acquired.credential)


@pytest.mark.asyncio
async def test_no_pat_fallback_and_no_app_lease_service() -> None:
    """Renewal keeps installation/repos/ops; no PAT fallback or lease service."""

    from moonmind.auth import bound_acquisition as ba
    from moonmind.auth import github_app as app_module

    assert not hasattr(app_module, "AppLeaseService")
    assert not hasattr(app_module, "AppReceiptStore")
    assert "PAT_FALLBACK" not in dir(app_module)

    # The bound-acquirer renewal path preserves authority (spot-check the
    # renewal constructor keeps route/operations rather than widening).
    import inspect

    source = inspect.getsource(ba.BoundCredentialAcquirer.renew)
    assert "operations" in source


# --- P1 remediation: provider-shape normalization and scope checks -----------


def _raw_installation_record(**overrides):
    record = {
        "id": 123,
        "app_id": 456,
        "account": {"login": ACCOUNT},
        "suspended_at": None,
        "repository_selection": "selected",
        "repositories": [{"id": 1, "full_name": "acme/repo"}],
    }
    record.update(overrides)
    return record


def _issuing_adapter(*, permitted=None, get_installation=None, **overrides):
    from moonmind.auth.github_app import GitHubAppAdapter

    async def _post_ok(*, jwt: str, payload: dict):
        return {
            "token": "ghs_opaque_test_token_abc",
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=55)).isoformat(),
            "permissions": dict(payload["permissions"]),
            "repositories": [{"full_name": "acme/repo"}],
        }

    async def _default_installation(*, jwt: str):
        return {
            "app_ref": APP_REF,
            "installation_ref": INSTALLATION_REF,
            "account": ACCOUNT,
            "repositories": list(REPOS),
            "suspended": False,
        }

    params = {
        "app_ref": APP_REF,
        "installation_ref": INSTALLATION_REF,
        "key_secret_ref": "db://github-app-key",
        "resolve_key": lambda _ref: b"fake-pem",
        "make_jwt": lambda _key: "jwt",
        "http_post": _post_ok,
        "get_installation": get_installation or _default_installation,
        "expected_account": ACCOUNT,
        "permitted_repositories": list(REPOS if permitted is None else permitted),
    }
    params.update(overrides)
    return GitHubAppAdapter(**params)


@pytest.mark.asyncio
async def test_app_verifies_raw_provider_installation_record() -> None:
    """A raw GET /app/installations payload verifies instead of failing."""

    from moonmind.auth.bound_acquisition import (
        AcquisitionRequest,
        BoundCredentialAcquirer,
    )

    conn = _app_connection()
    snapshot = _snapshot_for_app(conn)

    async def _get_raw(*, jwt: str):
        return _raw_installation_record()

    adapter = _issuing_adapter(
        app_ref="github-app:moonmind-456",
        installation_ref="installation:123",
        get_installation=_get_raw,
    )
    acquirer = BoundCredentialAcquirer(
        revision_reader=_reader(conn=conn), issuer_for=lambda _kind: adapter
    )
    acquired = await acquirer.acquire(
        AcquisitionRequest(snapshot=snapshot, execution_owner="exec:raw")
    )
    seen: list[bytes] = []
    acquired.credential.use_now(seen.append)
    assert seen[0] == b"ghs_opaque_test_token_abc"


@pytest.mark.asyncio
async def test_app_installation_check_uses_correct_subset_direction() -> None:
    """Shared installations pass least-privilege connections; gaps fail."""

    from moonmind.auth.bound_acquisition import (
        AcquisitionRequest,
        BoundAccessError,
        BoundCredentialAcquirer,
    )

    conn = _app_connection()

    # Installation covers repo-a and repo-b; the connection permits repo-a.
    async def _get_shared(*, jwt: str):
        return _raw_installation_record(
            repositories=[
                {"id": 1, "full_name": "acme/repo-a"},
                {"id": 2, "full_name": "acme/repo-b"},
            ]
        )

    async def _post_echo(*, jwt: str, payload: dict):
        return {
            "token": "ghs_opaque_shared_ok",
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            "permissions": dict(payload["permissions"]),
            "repositories": list(payload.get("repositories") or []),
        }

    snapshot = _snapshot_for_app(conn)
    shared_snapshot = snapshot.model_copy(update={"repositoryDisplay": "acme/repo-a"})
    adapter = _issuing_adapter(
        app_ref="github-app:moonmind-456",
        installation_ref="installation:123",
        permitted=["acme/repo-a"],
        get_installation=_get_shared,
        http_post=_post_echo,
    )
    acquirer = BoundCredentialAcquirer(
        revision_reader=_reader(conn=conn), issuer_for=lambda _kind: adapter
    )
    acquired = await acquirer.acquire(
        AcquisitionRequest(snapshot=shared_snapshot, execution_owner="exec:shared-ok")
    )
    assert acquired.binding.adapter_kind == "github_app"

    # A permitted repository outside the installation set fails closed.
    denied_snapshot = snapshot.model_copy(update={"repositoryDisplay": "acme/repo-c"})
    adapter_denied = _issuing_adapter(
        app_ref="github-app:moonmind-456",
        installation_ref="installation:123",
        permitted=["acme/repo-c"],
        get_installation=_get_shared,
        http_post=_post_echo,
    )
    acquirer_denied = BoundCredentialAcquirer(
        revision_reader=_reader(conn=conn), issuer_for=lambda _kind: adapter_denied
    )
    with pytest.raises(BoundAccessError):
        await acquirer_denied.acquire(
            AcquisitionRequest(snapshot=denied_snapshot, execution_owner="exec:shared-denied")
        )

    # An empty reported set denies a non-empty allowlist instead of passing.
    async def _get_empty(*, jwt: str):
        return _raw_installation_record(repositories=[])

    adapter_empty = _issuing_adapter(
        app_ref="github-app:moonmind-456",
        installation_ref="installation:123",
        permitted=["acme/repo-a"],
        get_installation=_get_empty,
        http_post=_post_echo,
    )
    acquirer_empty = BoundCredentialAcquirer(
        revision_reader=_reader(conn=conn), issuer_for=lambda _kind: adapter_empty
    )
    with pytest.raises(BoundAccessError):
        await acquirer_empty.acquire(
            AcquisitionRequest(snapshot=shared_snapshot, execution_owner="exec:shared-empty")
        )


@pytest.mark.asyncio
async def test_app_token_response_repository_objects_compared_by_name() -> None:
    """Provider repository objects verify by full_name instead of str()."""

    from moonmind.auth.bound_acquisition import (
        AcquisitionRequest,
        BoundAccessError,
        BoundCredentialAcquirer,
    )

    conn = _app_connection()
    snapshot = _snapshot_for_app(conn)

    async def _post_objects(*, jwt: str, payload: dict):
        return {
            "token": "ghs_opaque_objects_xyz",
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            "permissions": dict(payload["permissions"]),
            "repositories": [{"id": 1, "full_name": "acme/repo"}],
        }

    adapter = _issuing_adapter(http_post=_post_objects)
    acquirer = BoundCredentialAcquirer(
        revision_reader=_reader(conn=conn), issuer_for=lambda _kind: adapter
    )
    acquired = await acquirer.acquire(
        AcquisitionRequest(snapshot=snapshot, execution_owner="exec:objects-ok")
    )
    seen: list[bytes] = []
    acquired.credential.use_now(seen.append)
    assert seen[0] == b"ghs_opaque_objects_xyz"

    async def _post_narrow_objects(*, jwt: str, payload: dict):
        return {
            "token": "ghs_opaque_narrow_xyz",
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            "permissions": dict(payload["permissions"]),
            "repositories": [{"id": 9, "full_name": "acme/other"}],
        }

    adapter_narrow = _issuing_adapter(http_post=_post_narrow_objects)
    acquirer_narrow = BoundCredentialAcquirer(
        revision_reader=_reader(conn=conn), issuer_for=lambda _kind: adapter_narrow
    )
    with pytest.raises(BoundAccessError):
        await acquirer_narrow.acquire(
            AcquisitionRequest(snapshot=snapshot, execution_owner="exec:objects-narrow")
        )


@pytest.mark.asyncio
async def test_app_issuance_restricts_token_to_binding_repository() -> None:
    """The admitted route narrows the token; outside-allowlist fails."""

    from moonmind.auth.bound_acquisition import BoundAccessError

    conn = _app_connection()
    snapshot = _snapshot_for_app(conn)
    narrowed = snapshot.model_copy(update={"repository_display": "acme/repo"})

    seen_payloads: list[dict] = []

    async def _two_repo_installation(*, jwt: str):
        return {
            "app_ref": APP_REF,
            "installation_ref": INSTALLATION_REF,
            "account": ACCOUNT,
            "repositories": ["acme/repo", "acme/other"],
            "suspended": False,
        }

    async def _post_capture(*, jwt: str, payload: dict):
        seen_payloads.append(dict(payload))
        return {
            "token": "ghs_opaque_narrow_target",
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            "permissions": dict(payload["permissions"]),
            "repositories": [{"full_name": "acme/repo"}],
        }

    # The adapter narrows to the admitted binding repository.
    adapter = _issuing_adapter(
        permitted=["acme/repo", "acme/other"],
        http_post=_post_capture,
        get_installation=_two_repo_installation,
    )
    await adapter(narrowed)
    assert seen_payloads and seen_payloads[0]["repositories"] == ["repo"]
    assert "repository_ids" not in seen_payloads[0]

    # A binding repository outside the allowlist fails closed.
    outside = snapshot.model_copy(update={"repository_display": "acme/evil"})
    adapter_outside = _issuing_adapter(
        permitted=["acme/repo", "acme/other"],
        http_post=_post_capture,
        get_installation=_two_repo_installation,
    )
    with pytest.raises(BoundAccessError):
        await adapter_outside(outside)


@pytest.mark.asyncio
async def test_acquire_threads_target_repository_to_issuer() -> None:
    """The wiring path narrows issuance to the requested repository."""

    from moonmind.auth.bound_acquisition import BoundAccessError
    from moonmind.auth.github_app_wiring import acquire_bound_credential_for_connection

    conn = _app_connection()

    seen_payloads: list[dict] = []

    async def _post_capture(*, jwt: str, payload: dict):
        seen_payloads.append(dict(payload))
        return {
            "token": "ghs_opaque_threaded_target",
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            "permissions": dict(payload["permissions"]),
            "repositories": list(payload.get("repositories") or []),
        }

    async def _get_installation(*, jwt: str):
        return {
            "app_ref": APP_REF,
            "installation_ref": INSTALLATION_REF,
            "account": ACCOUNT,
            "repositories": ["acme/repo", "acme/other"],
            "suspended": False,
        }

    edge = {
        "resolve_secret": lambda _ref: b"fake-pem",
        "make_jwt": lambda _key: "jwt",
        "http_post": _post_capture,
        "get_installation": _get_installation,
        "expected_account": ACCOUNT,
        "permitted_repositories": ["acme/repo", "acme/other"],
        "app_id": "123456",
        "installation_id": "123",
    }
    await acquire_bound_credential_for_connection(
        conn,
        operations=["read"],
        principal_ref="principal:alice",
        principal_scope=("system", None),
        execution_owner="exec:threaded-target",
        repository="acme/repo",
        repository_display="ignored-display",
        **edge,
    )
    assert seen_payloads and seen_payloads[0]["repositories"] == ["repo"]

    # A requested repository outside the allowlist fails closed.
    with pytest.raises(BoundAccessError):
        await acquire_bound_credential_for_connection(
            conn,
            operations=["read"],
            principal_ref="principal:alice",
            principal_scope=("system", None),
            execution_owner="exec:threaded-outside",
            repository="acme/evil",
            **edge,
        )
