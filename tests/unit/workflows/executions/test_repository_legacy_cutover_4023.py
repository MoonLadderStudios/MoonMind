"""Acceptance coverage for MoonLadderStudios/MoonMind#4023.

One recoverable legacy-credential cutover through existing owners:
typed connections, scoped selection, and versioned persistence. No token
probing, no account merging, no wildcard allowlists, no second migration
system.
"""

from __future__ import annotations

import pytest

from moonmind.workflows.executions import repository_legacy_cutover_4023 as cutover
from moonmind.workflows.executions.repository_contract import (
    REPOSITORY_DENIED,
    REPOSITORY_SETUP_REQUIRED,
    RepositoryConnection,
    RepositoryIdentity,
    RepositoryRouteError,
    ScopedRouteCandidate,
)


def _pat_connection(connection_id: str) -> RepositoryConnection:
    return RepositoryConnection.model_validate(
        {
            "schemaVersion": "moonmind.repository-connection.v1",
            "id": connection_id,
            "provider": "git",
            "displayName": connection_id,
            "endpointRef": "https://github.com",
            "allowedOperations": ["read", "write"],
            "clientPolicy": {
                "pinnedVersion": "2.46.0",
                "toolBundleRef": "tool-bundle:git-2.46",
                "executableSha256": "sha256:git",
            },
            "credential": {
                "source": "secret_ref",
                "credentialRef": {"provider": "managed", "key": "TEAM_A_PAT"},
            },
            "lifecycle": "active",
            "policyRevision": 2,
            "credentialRevision": 1,
            "ownership": {
                "ownerRef": "owner:team-a",
                "scopeType": "system",
                "allowedPrincipalRefs": ["owner:team-a"],
            },
            "hostingService": "github",
        }
    )


def _identity() -> RepositoryIdentity:
    return RepositoryIdentity.model_validate(
        {
            "endpoint": "https://github.com",
            "providerRepoId": "12345",
            "displayName": "o/r",
        }
    )


def test_absent_and_unreadable_are_distinct() -> None:
    absent = cutover.determine_effective_legacy_reference(
        explicit_ref=None, historical_ref=None, proven_identity=None
    )
    assert absent.status == "absent"
    unreadable = cutover.determine_effective_legacy_reference(
        explicit_ref="  ",
        historical_ref=cutover.UNREADABLE_SENTINEL,
        proven_identity=None,
    )
    assert unreadable.status == "unreadable"
    assert absent.status != unreadable.status


def test_empty_allowlist_is_never_wildcard() -> None:
    with pytest.raises(RepositoryRouteError) as excinfo:
        cutover.require_explicit_allowlist_match(
            repository_name="o/r", allowed_repository_ids=()
        )
    assert excinfo.value.code == REPOSITORY_SETUP_REQUIRED


def test_git_default_only_for_proven_identity() -> None:
    proven = cutover.determine_effective_legacy_reference(
        explicit_ref=None,
        historical_ref="repository-connection:git-default",
        proven_identity="repository-connection:git-default",
    )
    assert proven.status == "proven_default"
    assert proven.connection_ref == "repository-connection:git-default"

    unproven = cutover.determine_effective_legacy_reference(
        explicit_ref=None,
        historical_ref="repository-connection:git-default",
        proven_identity=None,
    )
    assert unproven.status == "suspended"
    assert "proven" in (unproven.correction or "").lower()


def test_conflicting_choices_suspend_only_affected_operation() -> None:
    outcome = cutover.determine_effective_legacy_reference(
        explicit_ref="repository-connection:conn-a",
        historical_ref="repository-connection:conn-b",
        proven_identity=None,
    )
    assert outcome.status == "suspended"
    assert outcome.affected_operation == "repository.write"
    assert outcome.correction


def test_selected_secretref_failure_has_no_fallback() -> None:
    with pytest.raises(RepositoryRouteError) as excinfo:
        cutover.fail_selected_backend_without_fallback(
            backend="managed:TEAM_A_PAT",
            operation="repository.write",
            cause="secret backend unavailable",
        )
    assert excinfo.value.code == REPOSITORY_DENIED
    assert "managed:TEAM_A_PAT" in str(excinfo.value)


def test_migration_is_idempotent_and_reconciles_lost_ack() -> None:
    store = cutover.CutoverMappingStore()
    first = store.apply_mapping(
        request_id="req-1",
        connection_id="repository-connection:conn-a",
        expected_policy_revision=2,
    )
    rerun = store.apply_mapping(
        request_id="req-1",
        connection_id="repository-connection:conn-a",
        expected_policy_revision=2,
    )
    assert rerun == first
    assert store.mapping_count == 1
    # Lost commit ack: same request_id with same intent reconciles, no repeat.
    reconciled = store.reconcile_lost_ack(
        request_id="req-1",
        connection_id="repository-connection:conn-a",
    )
    assert reconciled == first


def test_concurrent_edit_is_detected_not_merged() -> None:
    store = cutover.CutoverMappingStore()
    store.apply_mapping(
        request_id="req-1",
        connection_id="repository-connection:conn-a",
        expected_policy_revision=2,
    )
    with pytest.raises(RepositoryRouteError):
        store.apply_mapping(
            request_id="req-2",
            connection_id="repository-connection:conn-a",
            expected_policy_revision=1,
        )
    assert store.mapping_count == 1


def test_zero_one_multiple_selection_preserves_selected_identity() -> None:
    conn_a = _pat_connection("repository-connection:conn-a")
    identity = _identity()

    def _candidate(conn: RepositoryConnection) -> ScopedRouteCandidate:
        from moonmind.workflows.executions.repository_contract import (
            RepositoryAssignment,
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
        return ScopedRouteCandidate(connection=conn, assignment=assignment)

    # Zero assignments authorize nothing (setup-required, never wildcard).
    with pytest.raises(RepositoryRouteError) as excinfo:
        cutover.select_admitted_connection(
            identity=identity,
            requested_operations=["read"],
            candidates=[],
            principal_ref="owner:team-a",
            principal_scope=("system", None),
        )
    assert excinfo.value.code == REPOSITORY_SETUP_REQUIRED

    # One eligible route selects exactly that connection.
    selected = cutover.select_admitted_connection(
        identity=identity,
        requested_operations=["read"],
        candidates=[_candidate(conn_a)],
        principal_ref="owner:team-a",
        principal_scope=("system", None),
    )
    assert selected.connection.id == "repository-connection:conn-a"

    # Multiple connections for one bundle stay ambiguous (declare explicitly).
    conn_b = _pat_connection("repository-connection:conn-b")
    with pytest.raises(RepositoryRouteError):
        cutover.select_admitted_connection(
            identity=identity,
            requested_operations=["read"],
            candidates=[_candidate(conn_a), _candidate(conn_b)],
            principal_ref="owner:team-a",
            principal_scope=("system", None),
        )


def test_scratch_and_anonymous_need_no_github() -> None:
    assert cutover.scratch_or_anonymous_usable(mode="scratch") is True
    assert cutover.scratch_or_anonymous_usable(mode="anonymous") is True


def test_diagnostics_expose_safe_refs_only() -> None:
    diagnostic = cutover.cutover_diagnostic(
        action="repository.write",
        connection_ref="repository-connection:conn-a",
        backend_ref="managed:TEAM_A_PAT",
    )
    assert "repository-connection:conn-a" in diagnostic
    assert "ghp_" not in diagnostic
    assert "sha256:" not in diagnostic.lower()
    # A token accidentally passed as backend must never be rendered.
    redacted = cutover.cutover_diagnostic(
        action="repository.write",
        connection_ref="repository-connection:conn-a",
        backend_ref="ghp_supersecretvalue123",
    )
    assert "ghp_supersecretvalue123" not in redacted


def test_sha_or_patch_difference_alone_is_not_incompatible() -> None:
    assert (
        cutover.is_worker_compatible(
            observed_sha256="sha256:other",
            pinned_sha256="sha256:git",
            observed_version="2.46.1",
            pinned_version="2.46.0",
            observed_bundle="tool-bundle:git-2.46",
            pinned_bundle="tool-bundle:git-2.46",
        )
        is True
    )
    assert (
        cutover.is_worker_compatible(
            observed_sha256="sha256:git",
            pinned_sha256="sha256:git",
            observed_version="2.46.0",
            pinned_version="2.46.0",
            observed_bundle="tool-bundle:other",
            pinned_bundle="tool-bundle:git-2.46",
        )
        is False
    )


def test_saved_target_preserves_identity_and_digests() -> None:
    outcome = cutover.map_saved_target_to_connection(
        repository_name="o/r",
        branch_name="main",
        recorded_digest="digest:abc123",
        connection_ref="repository-connection:conn-a",
    )
    assert outcome["connectionRef"] == "repository-connection:conn-a"
    assert outcome["repositoryName"] == "o/r"
    assert outcome["recordedDigest"] == "digest:abc123"
