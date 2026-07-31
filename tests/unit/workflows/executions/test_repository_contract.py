from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from moonmind.workflows.executions.repository_contract import (
    CapabilityReadinessRegistry,
    DEFAULT_GIT_CONNECTION_REF,
    RepositoryClientEvidence,
    RepositoryClientPolicy,
    RepositoryContractError,
    compile_repository_target,
    decode_recorded_repository_history,
    decode_legacy_repository_history_v1,
    derive_repository_capabilities,
    ensure_repository_ready,
    observe_repository_client_evidence,
    reconcile_default_git_connection,
    resolve_deployment_repository_connection,
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


def test_deployment_policy_and_observed_client_are_independent(monkeypatch) -> None:
    target = compile_repository_target(
        {
            "provider": "git",
            "repository": {"name": "owner/repo"},
            "branch": {"name": "main"},
        }
    )
    monkeypatch.setenv(
        "MOONMIND_DEFAULT_GIT_REPOSITORY_CONNECTION",
        reconcile_default_git_connection(
            client_policy=RepositoryClientPolicy(
                pinnedVersion="github-resolver.v1",
                toolBundleRef="moonmind.auth.github_credentials",
                executableSha256="deployment-pinned-digest",
            )
        ).model_dump_json(by_alias=True),
    )

    connection = resolve_deployment_repository_connection(target)
    observed = observe_repository_client_evidence(connection)

    assert observed.executable_sha256 != connection.client_policy.executable_sha256
    with pytest.raises(RepositoryContractError, match="REPOSITORY_CLIENT_MISMATCH"):
        validate_connection_and_client(target, connection, observed, operation="read")


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


def test_recorded_history_dispatch_requires_exact_frozen_decoder_version() -> None:
    target = decode_recorded_repository_history(
        decoder_version="moonmind.repository-legacy-history.v1",
        repository="owner/repo",
        branch="release",
    )
    assert target.repository.name == "owner/repo"
    with pytest.raises(
        RepositoryContractError, match="REPOSITORY_LEGACY_DECODER_UNSUPPORTED"
    ):
        decode_recorded_repository_history(
            decoder_version="moonmind.repository-legacy-history.v2",
            repository="owner/repo",
        )


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
