"""Production wiring regression coverage for MoonLadderStudios/MoonMind#4022.

Verifies the three verifier gaps are closed through existing boundaries:

* R2/R6: ``issuer_for_connection`` maps ``github_app`` credentials to
  ``GitHubAppAdapter`` (managed key-secret-ref) and ``secret_ref`` to
  ``PatAdapter`` on the production ``BoundCredentialAcquirer`` seam.
* R1/R5: ``save_verified_app_connection`` persists through the existing
  ``RepositoryConnectionService`` writer with ``ConnectionChangeRequest``
  request identity and never uninstalls shared installations.
* R3/R8: existing ``GitHubService`` HTTP and ``publish`` Git/gh paths
  consume App-issued bound credentials via the shared helpers with
  opaque-token/redaction checks.
"""

from __future__ import annotations

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
    )


def test_production_issuer_maps_github_app_to_adapter() -> None:
    import asyncio
    from datetime import datetime, timedelta, timezone

    from moonmind.auth.github_app import GitHubAppAdapter
    from moonmind.auth.github_app_wiring import issuer_for_connection, revision_reader_for

    conn = _app_connection()

    async def _post_ok(*, jwt: str, payload: dict):
        assert payload["repositories"] == REPOS
        assert set(payload.keys()) == {"repositories", "permissions"}
        return {
            "token": "ghs_opaque_prod_wiring_xyz",
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

    issuer = issuer_for_connection(
        conn,
        resolve_secret=lambda _ref: b"fake-pem",
        make_jwt=lambda _key: "jwt",
        http_post=_post_ok,
        get_installation=_get_installation,
        expected_account=ACCOUNT,
        permitted_repositories=list(REPOS),
    )
    assert isinstance(issuer, GitHubAppAdapter)
    assert issuer.kind == "github_app"

    # Full production seam: revision reader + issuer factory on the acquirer.
    from moonmind.auth.bound_acquisition import (
        AcquisitionRequest,
        BoundCredentialAcquirer,
    )
    from moonmind.workflows.executions.repository_contract import (
        RepositoryAssignment,
        RepositoryIdentity,
    )
    from moonmind.auth.bound_acquisition import AccessMode, select_repository_authority

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

    def _issuer_for(kind: str):
        assert kind == "github_app"
        return issuer

    acquirer = BoundCredentialAcquirer(
        revision_reader=revision_reader_for({conn.id: conn}),
        issuer_for=_issuer_for,
    )
    acquired = asyncio.run(
        acquirer.acquire(AcquisitionRequest(snapshot=snapshot, execution_owner="exec:prod"))
    )
    assert acquired.binding.adapter_kind == "github_app"
    seen: list[bytes] = []
    acquired.credential.use_now(seen.append)
    assert seen[0] == b"ghs_opaque_prod_wiring_xyz"


def test_production_issuer_maps_secret_ref_to_pat() -> None:
    from moonmind.auth.bound_acquisition import PatAdapter
    from moonmind.auth.github_app_wiring import issuer_for_connection
    from moonmind.workflows.executions.repository_contract import RepositoryConnection

    conn = RepositoryConnection.model_validate(
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
    issuer = issuer_for_connection(
        conn,
        resolve_secret=lambda _ref: "tok",
        make_jwt=lambda _key: "jwt",
        http_post=lambda **_kw: {},
        get_installation=lambda **_kw: {},
    )
    assert isinstance(issuer, PatAdapter)


def test_production_revision_reader_maps_app_connection() -> None:
    import asyncio

    from moonmind.auth.github_app_wiring import revision_reader_for

    conn = _app_connection()
    reader = revision_reader_for({"repository-connection:app": conn})
    active = asyncio.run(reader("repository-connection:app"))
    assert active.adapter_kind == "github_app"
    assert active.status == "active"
    assert active.credential_revision == conn.credential_revision


def test_existing_github_service_consumes_bound_app_credential() -> None:
    from moonmind.auth.github_app import build_bound_http_headers
    from moonmind.workflows.adapters.github_service import GitHubService

    token = "ghs_opaque_existing_path_xyz"
    via_existing = GitHubService._github_headers(token)
    via_bound = build_bound_http_headers(token.encode())
    assert via_existing == via_bound
    assert via_existing["Authorization"] == f"Bearer {token}"
    # Existing service exposes the bound-credential entry point.
    assert hasattr(GitHubService, "headers_from_bound_credential")


def test_existing_publish_paths_consume_bound_app_credential() -> None:
    import moonmind.publish.service as publish_service

    assert hasattr(publish_service, "push_env_from_bound_credential")
    assert hasattr(publish_service, "gh_env_from_bound_credential")

    class _FakeCredential:
        def use_now(self, fn):
            return fn(b"ghs_opaque_publish_xyz")

    class _FakeBinding:
        binding_digest = "binding:abc"
        connection_id = "repository-connection:app"
        operation_id = "op:1"
        adapter_kind = "github_app"
        route_id = "route:1"

    class _FakeAcquired:
        binding = _FakeBinding()
        credential = _FakeCredential()

    push_env, redact = publish_service.push_env_from_bound_credential(
        _FakeAcquired(), base_env={"GIT_TERMINAL_PROMPT": "0"}
    )
    assert push_env["GH_TOKEN"] == "ghs_opaque_publish_xyz"
    assert push_env["GITHUB_TOKEN"] == "ghs_opaque_publish_xyz"
    assert "ghs_opaque_publish_xyz" in redact

    gh_env, gh_redact = publish_service.gh_env_from_bound_credential(_FakeAcquired())
    assert gh_env["GH_TOKEN"] == "ghs_opaque_publish_xyz"
    assert "ghs_opaque_publish_xyz" in gh_redact


def test_setup_save_mounts_existing_connection_writer() -> None:
    import asyncio
    import inspect

    from moonmind.auth import github_app_setup as setup_module
    from moonmind.auth.github_app_setup import GitHubAppSetupService

    assert hasattr(setup_module, "save_verified_app_connection")
    source = inspect.getsource(setup_module.save_verified_app_connection)
    # Mounted at the existing writer with operation-identity reconciliation;
    # shared installations are never uninstalled as cleanup.
    assert "RepositoryConnectionService" in source
    assert "request_id" in source
    assert "reconcile_setup_save" in source
    assert "uninstall" not in source.lower() or "never" in source.lower()

    # Ambiguous-save convergence is preserved through the existing helper.
    assert hasattr(setup_module, "reconcile_setup_save")
    first = setup_module.reconcile_setup_save(
        request_id="req:ambiguous-1",
        connection_id="repository-connection:app",
        existing_by_request={},
    )
    second = setup_module.reconcile_setup_save(
        request_id="req:ambiguous-1",
        connection_id="repository-connection:app-retry",
        existing_by_request={"req:ambiguous-1": first},
    )
    assert second == first

    # Exercise the trusted-boundary save through the existing writer seam
    # with a fake service (no new platform, no uninstall).
    class _FakeWriter:
        def __init__(self):
            self.calls: list[dict] = []

        async def create_connection(
            self, connection, *, actor_ref, request_id, principal_ref, principal_scope
        ):
            self.calls.append(
                {
                    "id": connection.id,
                    "request_id": request_id,
                    "credential": connection.credential.model_dump(
                        by_alias=True, mode="json"
                    ),
                }
            )
            return connection

    verified_installation = {
        "app_ref": APP_REF,
        "installation_ref": INSTALLATION_REF,
        "account": ACCOUNT,
        "repositories": list(REPOS),
        "suspended": False,
    }

    async def _run() -> None:
        # Operator-import path persists through the existing writer.
        service = GitHubAppSetupService(server_secret="test-server-secret")
        writer = _FakeWriter()
        saved = await setup_module.save_verified_app_connection(
            setup_service=service,
            connection_service=writer,
            request_id="req:save-1",
            connection_id="repository-connection:app",
            provider_installation=verified_installation,
            expected_app_ref=APP_REF,
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
        assert writer.calls[0]["request_id"] == "req:save-1"

        # Setup-callback path binds single-use state and converges retries.
        service2 = GitHubAppSetupService(server_secret="test-server-secret")
        pending = service2.begin_setup(
            request_id="req:save-2",
            connection_id="repository-connection:app",
            principal_ref="principal:alice",
            principal_scope=("system", None),
            expected_app_ref=APP_REF,
            expected_account=ACCOUNT,
            permitted_repositories=list(REPOS),
        )
        writer2 = _FakeWriter()
        saved2 = await setup_module.save_verified_app_connection(
            setup_service=service2,
            connection_service=writer2,
            request_id="req:save-2",
            connection_id="repository-connection:app",
            provider_installation=verified_installation,
            expected_app_ref=APP_REF,
            expected_account=ACCOUNT,
            permitted_repositories=list(REPOS),
            state=pending.state,
            installation_ref=INSTALLATION_REF,
            caller_principal="principal:alice",
            caller_scope=("system", None),
            destination_connection_id="repository-connection:app",
            principal_ref="principal:alice",
            principal_scope=("system", None),
            owner_ref="owner:team-a",
            actor_ref="principal:alice",
        )
        assert saved2.id == "repository-connection:app"
        # Ambiguous retry converges on the first connection id.
        saved_retry = await setup_module.save_verified_app_connection(
            setup_service=service2,
            connection_service=writer2,
            request_id="req:save-2",
            connection_id="repository-connection:app-retry",
            provider_installation=verified_installation,
            expected_app_ref=APP_REF,
            expected_account=ACCOUNT,
            permitted_repositories=list(REPOS),
            operator_import=True,
            existing_by_request={"req:save-2": "repository-connection:app"},
            principal_ref="principal:alice",
            principal_scope=("system", None),
            owner_ref="owner:team-a",
            actor_ref="principal:alice",
        )
        assert saved_retry.id == "repository-connection:app"

    asyncio.run(_run())
