from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from moonmind.auth.github_credentials import ResolvedGitHubCredential
from moonmind.workflows.executions.repository_contract import (
    DEFAULT_GIT_CONNECTION_REF,
    SCOPED_CONNECTIONS_API_VERSION,
    CapabilityReadinessRegistry,
    RepositoryAssignment,
    RepositoryClientEvidence,
    RepositoryClientPolicy,
    RepositoryConnection,
    RepositoryConnectionStore,
    RepositoryContractError,
    RepositoryIdentity,
    compile_repository_target,
    connection_api_dict,
    decode_legacy_repository_history_v1,
    derive_repository_capabilities,
    ensure_repository_ready,
    github_repository_name_from_value,
    is_repository_admitted,
    load_repository_connection,
    load_snapshot,
    materialize_resolved_repository_target,
    persist_repository_connection,
    reconcile_default_git_connection,
    repository_branch_from_value,
    repository_name_from_value,
    resolve_default_git_credential,
    validate_connection_and_client,
)


def _policy() -> RepositoryClientPolicy:
    return RepositoryClientPolicy(
        pinnedVersion="2.46.0",
        toolBundleRef="tool-bundle:git-2.46",
        executableSha256="sha256:git",
    )


def test_common_git_draft_injects_default_connection_and_keeps_axes_distinct() -> None:
    target = compile_repository_target(
        {
            "provider": "git",
            "repository": {"name": "MoonLadderStudios/MoonMind"},
            "branch": {"name": "main"},
            "revision": {"kind": "git_commit", "commitSha": "abcdef012345"},
        }
    )

    assert target.connection_ref == DEFAULT_GIT_CONNECTION_REF
    assert target.repository.name == "MoonLadderStudios/MoonMind"
    assert target.branch.name == "main"
    assert target.revision is not None
    assert target.revision.commit_sha == "abcdef012345"


def test_repository_target_trims_identity_axes() -> None:
    target = compile_repository_target(
        {
            "provider": "git",
            "repository": {"name": "  MoonLadderStudios/MoonMind  "},
            "branch": {"name": "  main  "},
        }
    )

    assert target.repository.name == "MoonLadderStudios/MoonMind"
    assert target.branch.name == "main"


@pytest.mark.parametrize("field", ["repository", "branch"])
def test_repository_target_rejects_whitespace_only_identity(field: str) -> None:
    payload = {
        "provider": "git",
        "repository": {"name": "MoonLadderStudios/MoonMind"},
        "branch": {"name": "main"},
    }
    payload[field] = {"name": "   "}

    with pytest.raises(RepositoryContractError, match="REPOSITORY_TARGET_INVALID"):
        compile_repository_target(payload)


def test_repository_projection_helpers_support_scalar_and_structured_values() -> None:
    target = {
        "provider": "git",
        "repository": {"name": " MoonLadderStudios/MoonMind "},
        "branch": {"name": " feature/repository-target "},
    }

    assert repository_name_from_value(" owner/repo ") == "owner/repo"
    assert repository_name_from_value(target) == "MoonLadderStudios/MoonMind"
    assert (
        repository_name_from_value(target, provider="git")
        == "MoonLadderStudios/MoonMind"
    )
    assert repository_name_from_value(target, provider="lore") == ""
    assert repository_branch_from_value(target) == "feature/repository-target"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("MoonLadderStudios/MoonMind", "MoonLadderStudios/MoonMind"),
        (
            "https://github.com/MoonLadderStudios/MoonMind",
            "MoonLadderStudios/MoonMind",
        ),
        (
            "https://www.github.com/MoonLadderStudios/MoonMind.git/",
            "MoonLadderStudios/MoonMind",
        ),
        (
            "git@github.com:MoonLadderStudios/MoonMind.git",
            "MoonLadderStudios/MoonMind",
        ),
        ("https://gitlab.com/MoonLadderStudios/MoonMind", ""),
        ("https://github.com/owner/repo/issues", ""),
    ],
)
def test_github_repository_name_projection(value: str, expected: str) -> None:
    assert github_repository_name_from_value(value) == expected


@pytest.mark.parametrize("legacy", ["owner/repo", None, 123])
def test_new_compiler_rejects_legacy_or_missing_repository_shape(legacy: object) -> None:
    with pytest.raises(RepositoryContractError, match="provider-discriminated"):
        compile_repository_target(legacy)


def test_lore_requires_explicit_connection_and_matching_revision_kind() -> None:
    with pytest.raises(RepositoryContractError, match="REPOSITORY_TARGET_INVALID"):
        compile_repository_target(
            {
                "provider": "lore",
                "repository": {"name": "Tactics"},
                "branch": {"name": "main"},
                "revision": {"kind": "git_commit", "commitSha": "abcdef0"},
            }
        )


def test_provider_publish_skill_tool_capabilities_are_additive() -> None:
    target = compile_repository_target(
        {
            "provider": "lore",
            "connectionRef": "repository-connection:tactics",
            "repository": {"name": "Tactics"},
            "branch": {"name": "main"},
        }
    )
    assert derive_repository_capabilities(
        target,
        publish_mode="pr",
        skill_capabilities=["repo.lock"],
        tool_capabilities=["artifact.read"],
    ) == [
        "lore",
        "repo.read",
        "repo.write",
        "repo.branch.write",
        "repo.review.request",
        "repo.lock",
        "artifact.read",
    ]


def test_policy_and_observed_client_must_match_before_mutation() -> None:
    target = compile_repository_target(
        {
            "provider": "git",
            "repository": {"name": "MoonLadderStudios/MoonMind"},
            "branch": {"name": "main"},
        }
    )
    connection = reconcile_default_git_connection(client_policy=_policy())
    evidence = RepositoryClientEvidence(
        toolBundleRef="tool-bundle:git-2.46",
        clientVersion="wrong",
        executableSha256="sha256:git",
    )
    with pytest.raises(RepositoryContractError, match="REPOSITORY_CLIENT_MISMATCH"):
        validate_connection_and_client(
            target, connection, evidence, operation="write"
        )


@pytest.mark.asyncio
async def test_unknown_capability_fails_closed() -> None:
    registry = CapabilityReadinessRegistry(runtime_owned_tokens=("codex",))
    registry.register("repo.read", lambda _context: True)
    with pytest.raises(RepositoryContractError, match="REPOSITORY_CAPABILITY_UNKNOWN"):
        await registry.check(["codex", "repo.read", "mystery"], {})


@pytest.mark.asyncio
async def test_default_git_connection_invokes_existing_github_resolver() -> None:
    resolver = AsyncMock(return_value=object())
    with patch(
        "moonmind.auth.github_credentials.resolve_github_credential", resolver
    ):
        await resolve_default_git_credential("MoonLadderStudios/MoonMind")
    resolver.assert_awaited_once_with(repo="MoonLadderStudios/MoonMind")


def test_frozen_legacy_decoder_is_explicitly_history_only() -> None:
    target = decode_legacy_repository_history_v1("owner/repo", "release")
    assert target.connection_ref == DEFAULT_GIT_CONNECTION_REF
    assert target.branch.name == "release"


def test_reconciled_connection_is_persisted_and_resolved(tmp_path) -> None:
    path = tmp_path / "connections" / "git-default.json"
    connection = reconcile_default_git_connection(client_policy=_policy())
    persist_repository_connection(connection, path)
    assert load_repository_connection(path, DEFAULT_GIT_CONNECTION_REF) == connection


def test_lore_connection_persists_trust_projection_and_merge_policy(tmp_path) -> None:
    path = tmp_path / "connections" / "lore.json"
    connection = {
        "schemaVersion": "moonmind.repository-connection.v1",
        "id": "repository-connection:tactics",
        "provider": "lore",
        "displayName": "Tactics Lore",
        "endpointRef": "lore-endpoint:tactics",
        "trustBundleRef": "trust-bundle:tactics-ca",
        "allowedRepositoryIds": ["tactics-id"],
        "allowedOperations": ["read", "merge_request"],
        "clientPolicy": {
            "pinnedVersion": "1.2.3",
            "compatibleServerVersions": ["2026.08"],
            "toolBundleRef": "tool-bundle:lore-1.2.3",
            "executableSha256": "sha256:lore",
        },
        "credential": {
            "source": "secret_ref",
            "credentialRef": {"provider": "env", "key": "LORE_TOKEN"},
        },
        "projection": {
            "provider": "github",
            "repository": "owner/tactics-projection",
            "authority": "review_only",
            "statusSourceRef": "projection-status:tactics",
        },
        "mergeCoordinator": {
            "endpointRef": "merge-coordinator:tactics",
            "policyRef": "merge-policy:protected-main",
            "supportedProtocolVersion": "v1",
        },
    }
    modeled = RepositoryConnection.model_validate(connection)
    persist_repository_connection(modeled, path)
    assert load_repository_connection(path, modeled.id) == modeled


@pytest.mark.parametrize(
    ("provider", "credential"),
    [
        ("git", {"source": "github_resolver"}),
        (
            "git",
            {
                "source": "secret_ref",
                "credentialRef": {"provider": "env", "key": "GIT_TOKEN"},
            },
        ),
        (
            "lore",
            {
                "source": "secret_ref",
                "credentialRef": {"provider": "env", "key": "LORE_TOKEN"},
            },
        ),
        ("lore", {"source": "trusted_network_development"}),
    ],
)
def test_repository_connection_credential_variants_round_trip(
    provider, credential
) -> None:
    payload = {
        "schemaVersion": "moonmind.repository-connection.v1",
        "id": f"repository-connection:{provider}",
        "provider": provider,
        "displayName": f"{provider} connection",
        "endpointRef": f"{provider}-endpoint:default",
        "allowedOperations": ["read"],
        "clientPolicy": _policy().model_dump(by_alias=True),
        "credential": credential,
    }
    modeled = RepositoryConnection.model_validate(payload)
    expected = credential
    if credential["source"] == "secret_ref":
        expected = {
            **credential,
            "credentialRef": {**credential["credentialRef"], "extra": {}},
        }
    assert modeled.model_dump(by_alias=True, mode="json")["credential"] == expected


@pytest.mark.parametrize(
    "credential",
    [
        {"source": "secret_ref"},
        {
            "source": "github_resolver",
            "credentialRef": {"provider": "env", "key": "TOKEN"},
        },
        {
            "source": "trusted_network_development",
            "credentialRef": {"provider": "env", "key": "TOKEN"},
        },
    ],
)
def test_repository_connection_rejects_invalid_credential_reference_rules(
    credential,
) -> None:
    payload = {
        "schemaVersion": "moonmind.repository-connection.v1",
        "id": "repository-connection:test",
        "provider": "lore",
        "displayName": "Lore connection",
        "endpointRef": "lore-endpoint:test",
        "allowedOperations": ["read"],
        "clientPolicy": _policy().model_dump(by_alias=True),
        "credential": credential,
    }
    with pytest.raises(ValueError):
        RepositoryConnection.model_validate(payload)


def test_resolved_target_freezes_remote_tip_and_client_evidence() -> None:
    target = compile_repository_target(
        {
            "provider": "git",
            "repository": {"name": "owner/repo"},
            "branch": {"name": "main"},
        }
    )
    evidence = RepositoryClientEvidence(
        toolBundleRef="tool-bundle:git-2.46",
        clientVersion="2.46.0",
        executableSha256="sha256:git",
    )
    policy = RepositoryClientPolicy(
        pinnedVersion="2.46.0",
        compatibleServerVersions=("2.46",),
        toolBundleRef="tool-bundle:git-2.46",
        executableSha256="sha256:git",
    )
    resolved = materialize_resolved_repository_target(
        target,
        observed_revision="abcdef0123456789",
        evidence=evidence,
        client_policy=policy,
        publish_mode="branch",
    )
    assert resolved.prepared_revision.commit_sha == "abcdef0123456789"
    assert resolved.remote_tip_expectation["revision"]["commitSha"] == "abcdef0123456789"
    assert resolved.client_evidence == evidence
    assert resolved.compatible_server_versions == ("2.46",)
    assert resolved.base_branch.id == "refs/heads/main"
    assert resolved.work_branch is not None
    assert resolved.work_branch.origin == "selected"


def test_resolved_exact_revision_is_read_only_without_work_branch() -> None:
    target = compile_repository_target(
        {
            "provider": "lore",
            "connectionRef": "repository-connection:tactics",
            "repository": {"name": "tactics-id"},
            "branch": {"name": "Main"},
            "revision": {
                "kind": "lore_revision",
                "revisionSignature": "lore-revision-123",
            },
        }
    )
    evidence = RepositoryClientEvidence(
        toolBundleRef="tool-bundle:lore",
        clientVersion="1.2.3",
        executableSha256="sha256:lore",
    )
    resolved = materialize_resolved_repository_target(
        target,
        observed_revision="lore-revision-123",
        evidence=evidence,
        branch_id="branch-id-main",
    )
    assert resolved.remote_tip_expectation == {"kind": "read_only"}
    assert resolved.work_branch is None
    assert resolved.repository.id == "tactics-id"
    assert resolved.base_branch.id == "branch-id-main"
    assert resolved.compatible_server_versions == ()


def test_resolved_generated_branch_must_not_exist() -> None:
    target = compile_repository_target(
        {
            "provider": "git",
            "repository": {"name": "owner/repo"},
            "branch": {"name": "main"},
        }
    )
    evidence = RepositoryClientEvidence(
        toolBundleRef="tool-bundle:git",
        clientVersion="2.46.0",
        executableSha256="sha256:git",
    )
    resolved = materialize_resolved_repository_target(
        target,
        observed_revision="abcdef0123456789",
        evidence=evidence,
        publish_mode="pr",
        work_branch="feature/mm-1219",
        work_branch_id="refs/heads/feature/mm-1219",
        work_branch_origin="generated",
    )
    assert resolved.remote_tip_expectation == {"kind": "must_not_exist"}
    assert resolved.work_branch is not None
    assert resolved.work_branch.id == "refs/heads/feature/mm-1219"
    assert resolved.work_branch.origin == "generated"


@pytest.mark.asyncio
async def test_coherent_readiness_boundary_completes_before_mutation() -> None:
    target = compile_repository_target(
        {
            "provider": "git",
            "repository": {"name": "MoonLadderStudios/MoonMind"},
            "branch": {"name": "main"},
        }
    )
    connection = reconcile_default_git_connection(client_policy=_policy())
    evidence = RepositoryClientEvidence(
        toolBundleRef="tool-bundle:git-2.46",
        clientVersion="2.46.0",
        executableSha256="sha256:git",
    )
    registry = CapabilityReadinessRegistry()
    for token in ("git", "repo.read", "repo.write", "repo.branch.write", "gh"):
        registry.register(token, lambda _context: True)
    credential = AsyncMock(return_value=ResolvedGitHubCredential(token="test-token"))
    remote_tip = AsyncMock(return_value=True)

    resolved = await ensure_repository_ready(
        target,
        publish_mode="pr",
        operation="write",
        connection_resolver=lambda _target: connection,
        evidence_resolver=lambda _connection: evidence,
        readiness_registry=registry,
        credential_resolver=credential,
        remote_tip_verifier=remote_tip,
    )

    assert resolved == connection
    credential.assert_awaited_once_with("MoonLadderStudios/MoonMind")
    remote_tip.assert_awaited_once_with(target)


@pytest.mark.asyncio
async def test_readiness_boundary_fails_before_resolver_for_unknown_token() -> None:
    target = compile_repository_target(
        {
            "provider": "git",
            "repository": {"name": "owner/repo"},
            "branch": {"name": "main"},
        }
    )
    connection = reconcile_default_git_connection(client_policy=_policy())
    evidence = RepositoryClientEvidence(
        toolBundleRef="tool-bundle:git-2.46",
        clientVersion="2.46.0",
        executableSha256="sha256:git",
    )
    registry = CapabilityReadinessRegistry()
    registry.register("git", lambda _context: True)
    credential = AsyncMock()

    with pytest.raises(RepositoryContractError, match="REPOSITORY_CAPABILITY_UNKNOWN"):
        await ensure_repository_ready(
            target,
            publish_mode="none",
            operation="read",
            tool_capabilities=("unknown.tool",),
            connection_resolver=lambda _target: connection,
            evidence_resolver=lambda _connection: evidence,
            readiness_registry=registry,
            credential_resolver=credential,
        )
    credential.assert_not_awaited()


@pytest.mark.asyncio
async def test_readiness_boundary_rejects_unresolved_github_credential() -> None:
    target = compile_repository_target(
        {
            "provider": "git",
            "repository": {"name": "owner/repo"},
            "branch": {"name": "main"},
        }
    )
    connection = reconcile_default_git_connection(client_policy=_policy())
    evidence = RepositoryClientEvidence(
        toolBundleRef="tool-bundle:git-2.46",
        clientVersion="2.46.0",
        executableSha256="sha256:git",
    )
    registry = CapabilityReadinessRegistry()
    registry.register("git", lambda _context: True)
    registry.register("repo.read", lambda _context: True)
    registry.register("gh", lambda _context: True)
    remote_tip = AsyncMock(return_value=True)

    with pytest.raises(
        RepositoryContractError, match="REPOSITORY_CREDENTIAL_UNAVAILABLE"
    ):
        await ensure_repository_ready(
            target,
            publish_mode="none",
            operation="read",
            skill_capabilities=("gh",),
            connection_resolver=lambda _target: connection,
            evidence_resolver=lambda _connection: evidence,
            readiness_registry=registry,
            credential_resolver=AsyncMock(
                return_value=ResolvedGitHubCredential(
                    diagnostic="GitHub auth is unavailable for the repository."
                )
            ),
            remote_tip_verifier=remote_tip,
        )

    remote_tip.assert_not_awaited()


# --- MoonMind#4005: scoped RepositoryConnections and transactional routes ---


def _pat_connection(connection_id: str, operations=("read", "write")) -> RepositoryConnection:
    return RepositoryConnection(
        schemaVersion="moonmind.repository-connection.v1",
        id=connection_id,
        provider="git",
        displayName=connection_id,
        endpointRef="https://github.com",
        hostingService="github",
        allowedOperations=operations,
        clientPolicy=_policy(),
        credential={
            "source": "pat_secret_ref",
            "credentialRef": {"provider": "managed", "key": f"token-{connection_id}"},
            "patSubtype": "fine_grained",
        },
    )


def _identity(repository_id: str) -> RepositoryIdentity:
    return RepositoryIdentity(
        endpoint="https://github.com", repositoryId=repository_id
    )


def test_two_pat_connections_with_assignments_survive_restart(tmp_path) -> None:
    db = tmp_path / "connections.db"
    store = RepositoryConnectionStore(db)
    team_a = store.create_connection(
        _pat_connection("repository-connection:team-a"),
        actor="admin",
        request_id="create-a",
        principal="admin",
    )
    team_b = store.create_connection(
        _pat_connection("repository-connection:team-b"),
        actor="admin",
        request_id="create-b",
        principal="admin",
    )
    store.assign_repository(
        RepositoryAssignment(
            connectionId=team_a.id,
            endpoint="https://github.com",
            repositoryId="1001",
            operations=("read", "write"),
        ),
        actor="admin",
        request_id="assign-a1",
        principal="admin",
    )
    store.assign_repository(
        RepositoryAssignment(
            connectionId=team_b.id,
            endpoint="https://github.com",
            repositoryId="1001",
            operations=("read",),
        ),
        actor="admin",
        request_id="assign-b1",
        principal="admin",
    )
    store.close()

    reopened = RepositoryConnectionStore(db)
    try:
        resolved = reopened.resolve(
            scope="system",
            workspace_id=None,
            identity=_identity("1001"),
            capabilities=("repo.read", "git"),
            principal="admin",
            use_granted=True,
            connection_ref=team_b.id,
        )
        assert resolved.id == team_b.id
        # Metadata-only persistence: the typed SecretRef survives restart so
        # the Secrets System can resolve it, while secret bodies are never
        # stored here and the versioned API projection redacts the ref key.
        reloaded = reopened._load_connection(team_a.id)
        assert reloaded.credential.credential_ref.key == f"token-{team_a.id}"
        assert "token-value-" not in db.read_bytes().decode("utf-8", "replace")
        api = connection_api_dict(reloaded)
        assert api["credential"]["credentialRef"]["key"] == "***"
        assert api["apiVersion"] == SCOPED_CONNECTIONS_API_VERSION
    finally:
        reopened.close()


def test_empty_or_unverified_scope_grants_nothing_legacy_confined(tmp_path) -> None:
    store = RepositoryConnectionStore(tmp_path / "connections.db")
    try:
        connection = store.create_connection(
            _pat_connection("repository-connection:solo"),
            actor="admin",
            request_id="create",
            principal="admin",
        )
        # Zero assignments authorize no repositories in the new admission path,
        # even though the historical empty-means-unrestricted check still passes.
        assert not is_repository_admitted(
            connection,
            [],
            endpoint="https://github.com",
            repository_id="1001",
            operation="read",
        )
        target = compile_repository_target(
            {
                "provider": "git",
                "repository": {"name": "anything/goes"},
                "branch": {"name": "main"},
            }
        )
        legacy = reconcile_default_git_connection(client_policy=_policy())
        evidence = RepositoryClientEvidence(
            toolBundleRef="tool-bundle:git-2.46",
            clientVersion="2.46.0",
            executableSha256="sha256:git",
        )
        validate_connection_and_client(target, legacy, evidence, operation="read")

        store.assign_repository(
            RepositoryAssignment(
                connectionId=connection.id,
                endpoint="https://github.com",
                repositoryId="1001",
                operations=("read",),
                verified=False,
            ),
            actor="admin",
            request_id="assign-unverified",
            principal="admin",
        )
        with pytest.raises(
            RepositoryContractError, match="REPOSITORY_SETUP_REQUIRED"
        ):
            store.resolve(
                scope="system",
                workspace_id=None,
                identity=_identity("1001"),
                capabilities=("repo.read",),
                principal="admin",
                use_granted=True,
            )
    finally:
        store.close()


def test_concurrent_default_changes_keep_one_valid_snapshot(tmp_path) -> None:
    import threading

    store = RepositoryConnectionStore(tmp_path / "connections.db")
    try:
        for suffix in ("a", "b"):
            connection = store.create_connection(
                _pat_connection(f"repository-connection:{suffix}"),
                actor="admin",
                request_id=f"create-{suffix}",
                principal="admin",
            )
            store.assign_repository(
                RepositoryAssignment(
                    connectionId=connection.id,
                    endpoint="https://github.com",
                    repositoryId="2001",
                    operations=("read",),
                ),
                actor="admin",
                request_id=f"assign-{suffix}",
                principal="admin",
            )

        errors: list[Exception] = []

        def set_default(connection_id: str, request_id: str) -> None:
            try:
                store.set_default(
                    scope="system",
                    workspace_id=None,
                    repository_id="2001",
                    capabilities=("repo.read",),
                    connection_id=connection_id,
                    actor="admin",
                    request_id=request_id,
                    principal="admin",
                )
            except Exception as exc:  # pragma: no cover - diagnostics only
                errors.append(exc)

        threads = [
            threading.Thread(
                target=set_default,
                args=(f"repository-connection:{suffix}", f"default-{i}"),
            )
            for i, suffix in enumerate(["a", "b"] * 4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert not errors
        assert (
            store._db.execute("SELECT COUNT(*) FROM routes").fetchone()[0] == 1
        )
    finally:
        store.close()


def test_revision_compare_and_endpoint_change_rules(tmp_path) -> None:
    store = RepositoryConnectionStore(tmp_path / "connections.db")
    try:
        connection = store.create_connection(
            _pat_connection("repository-connection:ep"),
            actor="admin",
            request_id="create",
            principal="admin",
        )
        with pytest.raises(RepositoryContractError, match="REPOSITORY_CONFLICT"):
            store.update_connection(
                connection.id,
                actor="admin",
                request_id="stale",
                principal="admin",
                expected_policy_revision=999,
                display_name="stale",
            )
        # Retargeting credentials to another endpoint is never a label edit.
        with pytest.raises(RepositoryContractError, match="REPOSITORY_DENIED"):
            store.update_connection(
                connection.id,
                actor="admin",
                request_id="retarget",
                principal="admin",
                expected_policy_revision=1,
                endpoint_ref="https://unvalidated.example.com",
            )
        updated = store.update_connection(
            connection.id,
            actor="admin",
            request_id="retarget-validated",
            principal="admin",
            expected_policy_revision=1,
            endpoint_ref="https://ghe.example.com",
            validated_endpoint_change=True,
        )
        assert updated.policy_revision == 2
    finally:
        store.close()


def test_transfer_and_rename_lifecycle(tmp_path) -> None:
    store = RepositoryConnectionStore(tmp_path / "connections.db")
    try:
        connection = store.create_connection(
            _pat_connection("repository-connection:life"),
            actor="admin",
            request_id="create",
            principal="admin",
        )
        store.assign_repository(
            RepositoryAssignment(
                connectionId=connection.id,
                endpoint="https://github.com",
                repositoryId="3001",
                operations=("read",),
            ),
            actor="admin",
            request_id="assign",
            principal="admin",
        )
        assert (
            store.transfer_repository_owner(
                endpoint="https://github.com",
                repository_id="3001",
                actor="admin",
                request_id="transfer",
                principal="admin",
            )
            == 1
        )
        with pytest.raises(RepositoryContractError, match="REPOSITORY_DENIED"):
            store.resolve(
                scope="system",
                workspace_id=None,
                identity=_identity("3001"),
                capabilities=("repo.read",),
                principal="admin",
                use_granted=True,
                connection_ref=connection.id,
            )
        store.reauthorize_assignment(
            connection_id=connection.id,
            endpoint="https://github.com",
            repository_id="3001",
            actor="admin",
            request_id="reauthorize",
            principal="admin",
        )
        assert (
            store.reconcile_repository_rename(
                endpoint="https://github.com",
                old_repository_id="3001",
                new_repository_id="3002",
                actor="admin",
                request_id="rename",
                principal="admin",
            )
            == 1
        )
        resolved = store.resolve(
            scope="system",
            workspace_id=None,
            identity=_identity("3002"),
            capabilities=("repo.read",),
            principal="admin",
            use_granted=True,
            connection_ref=connection.id,
        )
        assert resolved.id == connection.id
    finally:
        store.close()


def test_disable_delete_and_snapshot_lifecycle(tmp_path) -> None:
    store = RepositoryConnectionStore(tmp_path / "connections.db")
    try:
        connection = store.create_connection(
            _pat_connection("repository-connection:gone"),
            actor="admin",
            request_id="create",
            principal="admin",
        )
        store.assign_repository(
            RepositoryAssignment(
                connectionId=connection.id,
                endpoint="https://github.com",
                repositoryId="4001",
                operations=("read",),
            ),
            actor="admin",
            request_id="assign",
            principal="admin",
        )
        store.set_default(
            scope="system",
            workspace_id=None,
            repository_id="4001",
            capabilities=("repo.read",),
            connection_id=connection.id,
            actor="admin",
            request_id="default",
            principal="admin",
        )
        disabled = store.disable_connection(
            connection_id=connection.id,
            actor="admin",
            request_id="disable",
            principal="admin",
        )
        assert disabled.lifecycle_status == "disabled"
        # Disabling drops the now-dangling default in the same transaction.
        assert store._db.execute("SELECT COUNT(*) FROM routes").fetchone()[0] == 0
        store.delete_connection(
            connection_id=connection.id,
            actor="admin",
            request_id="delete",
            principal="admin",
        )
        with pytest.raises(RepositoryContractError, match="REPOSITORY_CONFLICT"):
            store.create_connection(
                _pat_connection("repository-connection:gone"),
                actor="admin",
                request_id="recreate",
                principal="admin",
            )
        snapshot_path = tmp_path / "snapshot.json"
        snapshot = store.publish_snapshot(snapshot_path)
        assert load_snapshot(snapshot_path).digest == snapshot.digest
        with pytest.raises(
            RepositoryContractError, match="REPOSITORY_STALE_SNAPSHOT"
        ):
            load_snapshot(snapshot_path, expected_digest="stale")
        assert {record.action for record in store.audit_records()} >= {
            "connection.create",
            "assignment.upsert",
            "route.set_default",
            "connection.disable",
            "connection.delete",
        }
    finally:
        store.close()


def test_use_grant_and_ambiguous_selection_are_denied(tmp_path) -> None:
    store = RepositoryConnectionStore(tmp_path / "connections.db")
    try:
        for suffix in ("a", "b"):
            connection = store.create_connection(
                _pat_connection(f"repository-connection:{suffix}"),
                actor="admin",
                request_id=f"create-{suffix}",
                principal="admin",
            )
            store.assign_repository(
                RepositoryAssignment(
                    connectionId=connection.id,
                    endpoint="https://github.com",
                    repositoryId="5001",
                    operations=("read",),
                ),
                actor="admin",
                request_id=f"assign-{suffix}",
                principal="admin",
            )
        with pytest.raises(RepositoryContractError, match="REPOSITORY_DENIED"):
            store.resolve(
                scope="system",
                workspace_id=None,
                identity=_identity("5001"),
                capabilities=("repo.read",),
                principal="admin",
                use_granted=False,
                connection_ref="repository-connection:a",
            )
        with pytest.raises(
            RepositoryContractError, match="REPOSITORY_ROUTE_AMBIGUOUS"
        ):
            store.resolve(
                scope="system",
                workspace_id=None,
                identity=_identity("5001"),
                capabilities=("repo.read",),
                principal="admin",
                use_granted=True,
            )
    finally:
        store.close()
