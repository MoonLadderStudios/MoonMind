from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from moonmind.workflows.executions.repository_contract import (
    CapabilityReadinessRegistry,
    DEFAULT_GIT_CONNECTION_REF,
    RepositoryClientEvidence,
    RepositoryClientPolicy,
    RepositoryConnection,
    RepositoryContractError,
    compile_repository_target,
    decode_legacy_repository_history_v1,
    derive_repository_capabilities,
    ensure_repository_ready,
    load_repository_connection,
    materialize_resolved_repository_target,
    persist_repository_connection,
    reconcile_default_git_connection,
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
        "credentialSource": "secret_ref",
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
    credential = AsyncMock(return_value=object())
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
