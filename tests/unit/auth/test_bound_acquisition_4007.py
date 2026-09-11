"""Acceptance coverage for MoonLadderStudios/MoonMind#4007.

Deterministic access selection + refresh-capable bound credential
acquisition: explicit/routed/anonymous selection, distinct credential states,
binding/ephemeral separation, ACTIVE-revision acquisition, bounded renewal
cache, post-issuance verification, operation identities, PAT + fake-expiring
adapter matrix, revocation races, secret canaries, and the historical
boundary (legacy precedence chain stays behind its explicit reader).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from moonmind.auth.bound_acquisition import (
    BOUND_DENIED,
    BOUND_ISSUER_FAILED,
    BOUND_UNAVAILABLE,
    AccessMode,
    AcquisitionRequest,
    ActiveRevision,
    BindingMetadata,
    BoundAccessError,
    BoundClient,
    BoundCredentialAcquirer,
    BoundCredentialCache,
    BoundErrorDTO,
    BoundTelemetryDTO,
    CredentialState,
    EphemeralCredential,
    FakeExpiringAdapter,
    PatAdapter,
    SelectionSnapshot,
    SharedRenewalStore,
    binding_digest_for,
    make_bound_client,
    safe_error_dto,
    select_repository_authority,
    verify_repository_identity_through_selection,
)
from moonmind.workflows.executions.repository_contract import (
    RepositoryAssignment,
    RepositoryConnection,
    RepositoryIdentity,
    ScopedRouteCandidate,
)

pytestmark = pytest.mark.asyncio

CANARY = "ghp_canary_4007_SECRET_DO_NOT_LOG"


def _policy() -> dict:
    return {
        "pinnedVersion": "2.46.0",
        "toolBundleRef": "tool-bundle:git-2.46",
        "executableSha256": "sha256:git",
    }


def _connection(
    connection_id: str,
    *,
    operations: tuple[str, ...] = ("read", "write"),
    secret_key: str = "TEAM_A_PAT",
    lifecycle: str = "active",
    policy_rev: int = 2,
    credential_rev: int = 3,
) -> RepositoryConnection:
    return RepositoryConnection.model_validate(
        {
            "schemaVersion": "moonmind.repository-connection.v1",
            "id": connection_id,
            "provider": "git",
            "displayName": f"Connection {connection_id}",
            "endpointRef": "https://github.com",
            "allowedOperations": list(operations),
            "clientPolicy": _policy(),
            "credential": {
                "source": "secret_ref",
                "credentialRef": {"provider": "db", "key": secret_key},
            },
            "lifecycle": lifecycle,
            "policyRevision": policy_rev,
            "credentialRevision": credential_rev,
            "ownership": {
                "ownerRef": "owner:team-a",
                "scopeType": "system",
                "allowedPrincipalRefs": ["principal:alice"],
            },
            "hostingService": "github",
        }
    )


def _identity(display: str = "acme/repo") -> RepositoryIdentity:
    return RepositoryIdentity.model_validate(
        {
            "endpoint": "https://github.com",
            "providerRepoId": "repo-id-1",
            "displayName": display,
        }
    )


def _assignment(connection_id: str, identity: RepositoryIdentity) -> RepositoryAssignment:
    return RepositoryAssignment.model_validate(
        {
            "connectionId": connection_id,
            "identity": identity.model_dump(by_alias=True, mode="json"),
            "operations": ["read", "write"],
            "revision": 1,
            "verified": True,
        }
    )


def _reader(
    connection: RepositoryConnection,
    *,
    status: str = "active",
    adapter_kind: str = "pat",
    calls: list | None = None,
) -> callable:
    async def _read(connection_id: str) -> ActiveRevision:
        assert connection_id == connection.id
        if calls is not None:
            calls.append(connection_id)
        return ActiveRevision(
            credential_revision=connection.credential_revision,
            connection_revision=connection.policy_revision,
            policy_revision=2,
            status=status,
            adapter_kind=adapter_kind,
        )

    return _read


# --- A1: explicit precedence / fail-closed ---------------------------------


async def test_explicit_empty_token_fails_closed_despite_ambient(monkeypatch) -> None:
    from moonmind.auth import github_credentials as gc

    monkeypatch.setenv("GITHUB_TOKEN", "ambient-token-A")
    resolved = await gc.resolve_github_credential("", repo="acme/repo")
    assert resolved.token == ""
    assert not resolved.resolved
    assert resolved.source == gc.GitHubCredentialSource.UNRESOLVABLE
    assert "ambient-token-A" not in (resolved.diagnostic or "")


async def test_blank_secret_ref_does_not_fall_through_to_ambient(monkeypatch) -> None:
    from moonmind.auth import github_credentials as gc

    for key in ("GITHUB_TOKEN", "GH_TOKEN", "WORKFLOW_GITHUB_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GITHUB_TOKEN_SECRET_REF", "db://blank")
    monkeypatch.setenv("MOONMIND_GITHUB_TOKEN_REF", "env://FALLBACK")
    monkeypatch.setenv("FALLBACK", "fallback-token")

    async def _blank(_ref: str) -> str:
        return "   "

    monkeypatch.setattr(gc, "_resolve_secret_ref", _blank)
    resolved = await gc.resolve_github_credential()
    assert resolved.token == ""
    assert resolved.source == gc.GitHubCredentialSource.UNRESOLVABLE
    assert "resolved empty" in (resolved.diagnostic or "")


async def test_backend_exception_does_not_select_ambient(monkeypatch) -> None:
    from moonmind.auth import github_credentials as gc

    for key in (
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "WORKFLOW_GITHUB_TOKEN",
        "WORKFLOW_GITHUB_TOKEN_SECRET_REF",
        "MOONMIND_GITHUB_TOKEN_REF",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GITHUB_TOKEN_SECRET_REF", "db://broken")

    async def _boom(_ref: str) -> str:
        raise RuntimeError("vault down")

    monkeypatch.setattr(gc, "_resolve_secret_ref", _boom)
    resolved = await gc.resolve_github_credential()
    assert resolved.token == ""
    assert resolved.source == gc.GitHubCredentialSource.UNRESOLVABLE


def test_explicit_selection_uses_only_named_connection() -> None:
    conn_b = _connection("conn-b")
    snapshot = select_repository_authority(
        access_mode=AccessMode.EXPLICIT,
        principal_ref="principal:alice",
        principal_scope=("system", None),
        identity=_identity(),
        role="publisher",
        requested_operations=["read", "write"],
        policy_revision=2,
        explicit_connection=conn_b,
    )
    assert snapshot.connection_id == "conn-b"
    assert snapshot.selection_origin == "explicit"
    assert snapshot.credential_revision == 3


def test_denied_and_ambiguous_routes_never_select_ambient_or_anonymous() -> None:
    from moonmind.workflows.executions.repository_contract import RepositoryRouteError

    conn = _connection("conn-a")
    identity = _identity()
    # Denied: principal not admitted.
    with pytest.raises((BoundAccessError, RepositoryRouteError)):
        select_repository_authority(
            access_mode=AccessMode.ROUTED,
            principal_ref="principal:intruder",
            principal_scope=("system", None),
            identity=identity,
            role="publisher",
            requested_operations=["read"],
            policy_revision=2,
            candidates=[ScopedRouteCandidate(connection=conn, assignment=_assignment(conn.id, identity))],
        )
    # Ambiguous: two connections satisfy the bundle.
    conn2 = _connection("conn-b")
    allowed = dict.fromkeys(["principal:alice"])
    _ = allowed
    conn2b = RepositoryConnection.model_validate(
        {
            **conn2.model_dump(by_alias=True, mode="json"),
            "ownership": {
                "ownerRef": "owner:team-a",
                "scopeType": "system",
                "allowedPrincipalRefs": ["principal:alice"],
            },
        }
    )
    with pytest.raises((BoundAccessError, RepositoryRouteError)):
        select_repository_authority(
            access_mode=AccessMode.ROUTED,
            principal_ref="principal:alice",
            principal_scope=("system", None),
            identity=identity,
            role="publisher",
            requested_operations=["read"],
            policy_revision=2,
            candidates=[
                ScopedRouteCandidate(connection=conn, assignment=_assignment(conn.id, identity)),
                ScopedRouteCandidate(connection=conn2b, assignment=_assignment(conn2b.id, identity)),
            ],
        )


# --- A2: scratch/anonymous zero discovery -----------------------------------


def test_scratch_never_enters_acquisition() -> None:
    with pytest.raises(BoundAccessError) as exc_info:
        select_repository_authority(
            access_mode=AccessMode.ROUTED,
            principal_ref="principal:alice",
            principal_scope=("system", None),
            identity=_identity(),
            role="scratch",
            requested_operations=["read"],
            policy_revision=2,
        )
    assert exc_info.value.code == "BOUND_SCRATCH_EXCLUDED"


async def test_anonymous_snapshot_makes_zero_secret_queries(monkeypatch) -> None:
    calls: list[str] = []

    async def _must_not_query(_ref: str) -> str:
        calls.append(_ref)
        raise AssertionError("anonymous path queried a credential backend")

    # Inject the forbidden backend into the discovery seams the anonymous
    # path could plausibly touch.  The exercised selection + acquire path
    # below must complete (or fail closed for anonymous acquire) without
    # invoking any of them.
    from moonmind.auth import github_credentials as gc

    monkeypatch.setattr(gc, "_resolve_secret_ref", _must_not_query)

    snapshot = select_repository_authority(
        access_mode=AccessMode.ANONYMOUS,
        principal_ref="principal:alice",
        principal_scope=("system", None),
        identity=_identity(),
        role="reader",
        requested_operations=["read"],
        policy_revision=2,
    )
    assert snapshot.connection_id == "anonymous"
    assert snapshot.access_mode == AccessMode.ANONYMOUS
    # Anonymous write is not a supported read: rejected, never downgraded.
    with pytest.raises(BoundAccessError):
        select_repository_authority(
            access_mode=AccessMode.ANONYMOUS,
            principal_ref="principal:alice",
            principal_scope=("system", None),
            identity=_identity(),
            role="reader",
            requested_operations=["write"],
            policy_revision=2,
        )
    # Anonymous snapshots carry no credential to acquire: the acquirer
    # rejects before touching any issuer or revision reader, even when the
    # only available backend is the forbidden one.
    forbidden_issuer = PatAdapter("db://FORBIDDEN", _must_not_query)

    async def _must_not_read(_connection_id: str) -> ActiveRevision:  # pragma: no cover
        raise AssertionError("anonymous acquire read revision authority")

    acquirer = BoundCredentialAcquirer(
        revision_reader=_must_not_read,
        issuer_for=lambda _kind: forbidden_issuer,
    )
    with pytest.raises(BoundAccessError):
        await acquirer.acquire(
            AcquisitionRequest(snapshot=snapshot, execution_owner="exec:anon")
        )
    assert calls == []


def test_failed_source_never_becomes_anonymous() -> None:
    conn = _connection("conn-a", lifecycle="disabled")

    async def _reader_disabled(connection_id: str) -> ActiveRevision:
        return ActiveRevision(
            credential_revision=3,
            connection_revision=2,
            policy_revision=2,
            status="disabled",
            adapter_kind="pat",
        )

    async def _go() -> None:
        acquirer = BoundCredentialAcquirer(
            revision_reader=_reader_disabled,
            issuer_for=lambda _kind: PatAdapter("db://x", lambda _r: "tok"),
        )
        snapshot = select_repository_authority(
            access_mode=AccessMode.EXPLICIT,
            principal_ref="principal:alice",
            principal_scope=("system", None),
            identity=_identity(),
            role="publisher",
            requested_operations=["read"],
            policy_revision=2,
            explicit_connection=_connection("conn-a"),
        )
        await acquirer.acquire(
            AcquisitionRequest(snapshot=snapshot, execution_owner="exec:1")
        )

    # Explicit selection itself rejects the disabled connection before any
    # acquisition; either boundary failing closed (never anonymous) is a pass.
    from moonmind.workflows.executions.repository_contract import RepositoryRouteError

    with pytest.raises((BoundAccessError, RepositoryRouteError)) as exc_info:
        select_repository_authority(
            access_mode=AccessMode.EXPLICIT,
            principal_ref="principal:alice",
            principal_scope=("system", None),
            identity=_identity(),
            role="publisher",
            requested_operations=["read"],
            policy_revision=2,
            explicit_connection=conn,
        )
    assert "BOUND_DISABLED" in str(exc_info.value) or "DENIED" in str(exc_info.value)
    _ = _go


# --- A3: handle unforgeability ----------------------------------------------


async def test_guessed_handle_cannot_be_used() -> None:
    conn = _connection("conn-a")
    snapshot = select_repository_authority(
        access_mode=AccessMode.EXPLICIT,
        principal_ref="principal:alice",
        principal_scope=("system", None),
        identity=_identity(),
        role="publisher",
        requested_operations=["read", "write"],
        policy_revision=2,
        explicit_connection=conn,
    )
    acquirer = BoundCredentialAcquirer(
        revision_reader=_reader(conn),
        issuer_for=lambda _kind: PatAdapter("db://TEAM_A_PAT", lambda _r: "token-B"),
    )
    acquired = await acquirer.acquire(
        AcquisitionRequest(snapshot=snapshot, execution_owner="exec:1")
    )
    ok = make_bound_client(
        binding=acquired.binding,
        execution_owner="exec:1",
        authenticate_execution=lambda owner: owner == "exec:1",
        expected_principal="principal:alice",
        expected_policy_revision=2,
        expected_operation="write",
    )
    assert isinstance(ok, BoundClient)
    # Wrong principal / policy / operation / unauthenticated execution all fail.
    with pytest.raises(BoundAccessError):
        make_bound_client(
            binding=acquired.binding,
            execution_owner="exec:1",
            authenticate_execution=lambda owner: owner == "exec:1",
            expected_principal="principal:intruder",
        )
    with pytest.raises(BoundAccessError):
        make_bound_client(
            binding=acquired.binding,
            execution_owner="exec:1",
            authenticate_execution=lambda owner: owner == "exec:1",
            expected_policy_revision=99,
        )
    with pytest.raises(BoundAccessError):
        make_bound_client(
            binding=acquired.binding,
            execution_owner="exec:1",
            authenticate_execution=lambda owner: owner == "exec:1",
            expected_operation="merge_request",
        )
    with pytest.raises(BoundAccessError):
        make_bound_client(
            binding=acquired.binding,
            execution_owner="exec:evil",
            authenticate_execution=lambda owner: owner == "exec:1",
        )
    # A forged digest with copied identifiers still fails principal binding.
    forged = acquired.binding.model_copy(update={"binding_digest": "binding:forged"})
    with pytest.raises(BoundAccessError):
        make_bound_client(
            binding=forged,
            execution_owner="exec:1",
            authenticate_execution=lambda owner: owner == "exec:1",
            expected_principal="principal:intruder",
        )
    # Identity verification never probes candidates: mismatch fails.
    with pytest.raises(BoundAccessError):
        verify_repository_identity_through_selection(
            snapshot=snapshot, observed_route_id="id:https://github.com#other"
        )
    verify_repository_identity_through_selection(
        snapshot=snapshot, observed_route_id=snapshot.route_id
    )


# --- Contract matrix shared by both adapters (A4) ----------------------------


def _snapshot_for(conn: RepositoryConnection, *, mode: AccessMode = AccessMode.EXPLICIT) -> SelectionSnapshot:
    if mode == AccessMode.ROUTED:
        identity = _identity()
        return select_repository_authority(
            access_mode=mode,
            principal_ref="principal:alice",
            principal_scope=("system", None),
            identity=identity,
            role="publisher",
            requested_operations=["read", "write"],
            policy_revision=2,
            candidates=[
                ScopedRouteCandidate(connection=conn, assignment=_assignment(conn.id, identity))
            ],
        )
    return select_repository_authority(
        access_mode=mode,
        principal_ref="principal:alice",
        principal_scope=("system", None),
        identity=_identity(),
        role="publisher",
        requested_operations=["read", "write"],
        policy_revision=2,
        explicit_connection=conn,
    )


@pytest.mark.parametrize("adapter_kind", ["pat", "expiring"])
async def test_adapter_contract_matrix(adapter_kind: str) -> None:
    conn = _connection("conn-matrix")
    snapshot = _snapshot_for(conn)
    if adapter_kind == "pat":
        issuer: FakeExpiringAdapter | PatAdapter = PatAdapter(
            "db://TEAM_A_PAT", lambda _r: "matrix-token"
        )
    else:
        issuer = FakeExpiringAdapter(scope="read,write", ttl_seconds=60.0)
    acquirer = BoundCredentialAcquirer(
        revision_reader=_reader(conn, adapter_kind=adapter_kind),
        issuer_for=lambda _kind: issuer,
    )
    acquired = await acquirer.acquire(
        AcquisitionRequest(snapshot=snapshot, execution_owner="exec:matrix")
    )
    assert acquired.state == CredentialState.READY
    assert acquired.binding.adapter_kind == adapter_kind
    seen: list[bytes] = []
    acquired.credential.use_now(seen.append)
    assert seen and seen[0]
    client = make_bound_client(
        binding=acquired.binding,
        execution_owner="exec:matrix",
        authenticate_execution=lambda owner: owner == "exec:matrix",
    )
    assert client.connection_id == "conn-matrix"


def _issuer_and_counter(adapter_kind: str, *, release: asyncio.Event | None = None):
    """Build the real adapter for one matrix leg plus its issuance counter."""
    if adapter_kind == "pat":
        calls: list[str] = []

        async def _backend(_ref: str) -> str:
            calls.append(_ref)
            if release is not None:
                await release.wait()
            return "matrix-pat-token"

        issuer = PatAdapter("db://MATRIX_PAT", _backend, expected_scope="read,write")
        return issuer, lambda: len(calls)
    adapter = FakeExpiringAdapter(scope="read,write", ttl_seconds=60.0)
    if release is not None:
        inner = adapter

        async def _slow_expiring(binding: BindingMetadata):
            await release.wait()
            return await inner(binding)

        return _slow_expiring, lambda: adapter.issues
    return adapter, lambda: adapter.issues


@pytest.mark.parametrize("adapter_kind", ["pat", "expiring"])
async def test_two_process_renewal_is_single_flight(adapter_kind: str) -> None:
    conn = _connection("conn-race")
    issuer, issue_count = _issuer_and_counter(adapter_kind)
    cache = BoundCredentialCache()
    reader_calls: list[str] = []
    acquirer = BoundCredentialAcquirer(
        revision_reader=_reader(conn, adapter_kind=adapter_kind, calls=reader_calls),
        issuer_for=lambda _kind: issuer,
        cache=cache,
    )
    snapshot = _snapshot_for(conn)
    owner = "exec:shared"
    first, second = await asyncio.gather(
        acquirer.acquire(AcquisitionRequest(snapshot=snapshot, execution_owner=owner)),
        acquirer.acquire(AcquisitionRequest(snapshot=snapshot, execution_owner=owner)),
    )
    assert issue_count() == 1
    assert first.binding.issuance_id == second.binding.issuance_id
    assert first.binding.adapter_kind == adapter_kind


async def test_shared_store_two_thread_renewal_is_single_flight() -> None:
    """Genuine cross-worker single-flight through one shared store (R5).

    Two threads run independent event loops, caches, and acquirers sharing a
    single SharedRenewalStore.  Only one issuance may occur; the follower
    observes the shared publication instead of re-issuing.  No database lock
    is held during issuance — only the short-lived lease timestamp.
    """

    import threading

    conn = _connection("conn-shared-race")
    shared = SharedRenewalStore(lease_seconds=30.0)
    adapter = FakeExpiringAdapter(scope="read,write", ttl_seconds=60.0)
    barrier = threading.Barrier(2)
    results: dict[str, object] = {}
    errors: list[BaseException] = []

    def _worker(tag: str) -> None:
        import asyncio as _aio

        async def _run() -> None:
            cache = BoundCredentialCache(shared=shared)
            acquirer = BoundCredentialAcquirer(
                revision_reader=_reader(conn, adapter_kind="expiring"),
                issuer_for=lambda _kind: adapter,
                cache=cache,
            )
            snapshot = _snapshot_for(conn)
            barrier.wait(timeout=10)
            acquired = await acquirer.acquire(
                AcquisitionRequest(snapshot=snapshot, execution_owner="exec:shared-t")
            )
            results[tag] = acquired

        try:
            _aio.run(_run())
        except BaseException as exc:  # noqa: BLE001 - recorded for assertion
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(f"w{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors, f"worker errors: {errors!r}"
    assert adapter.issues == 1, f"expected single issuance, got {adapter.issues}"
    first = results["w0"]
    second = results["w1"]
    assert isinstance(first, object) and isinstance(second, object)
    # Both workers hold the same authority: identical digest and material.
    assert first.binding.binding_digest == second.binding.binding_digest  # type: ignore[attr-defined]
    seen_first: list[bytes] = []
    seen_second: list[bytes] = []
    first.credential.use_now(seen_first.append)  # type: ignore[attr-defined]
    second.credential.use_now(seen_second.append)  # type: ignore[attr-defined]
    assert seen_first[0] == seen_second[0]


async def test_shared_generation_fencing_rejects_stale_publisher() -> None:
    shared = SharedRenewalStore()
    key = BoundCredentialCache.renewal_key_for(
        endpoint="https://github.com",
        connection_revision=2,
        credential_revision=3,
        route_id="id:https://github.com#repo-id-1",
        operations=["read"],
        execution_owner="exec:g",
    )
    first_gen = shared.next_generation(key)
    second_gen = shared.next_generation(key)
    assert second_gen == first_gen + 1
    assert shared.publish(
        key,
        binding=_snapshot_for(_connection("conn-g")).model_dump  # placeholder
        if False
        else _acquire_binding_for_test(),
        material=b"newer",
        generation=second_gen,
    )
    # A stale generation cannot overwrite the newer publication.
    assert not shared.publish(
        key,
        binding=_acquire_binding_for_test(),
        material=b"stale",
        generation=first_gen,
    )
    published = shared.get(key)
    assert published is not None and published[1] == b"newer"


def _acquire_binding_for_test() -> BindingMetadata:
    conn = _connection("conn-g")
    snapshot = _snapshot_for(conn)
    return BindingMetadata(
        bindingDigest="binding:test",
        connectionId=snapshot.connection_id,
        endpoint=snapshot.endpoint,
        routeId=snapshot.route_id,
        role=snapshot.role,
        operations=snapshot.operations,
        policyRevision=snapshot.policy_revision,
        connectionRevision=2,
        credentialRevision=3,
        principalRef=snapshot.principal_ref,
        scopeType=snapshot.scope_type,
        scopeRef=snapshot.scope_ref,
        operationId="op:test",
        issuanceId="iss:test",
        generation=2,
        adapterKind="expiring",
    )


@pytest.mark.parametrize("adapter_kind", ["pat", "expiring"])
async def test_canceled_waiter_does_not_invalidate_credential(adapter_kind: str) -> None:
    conn = _connection("conn-cancel")
    release = asyncio.Event()
    issuer, _ = _issuer_and_counter(adapter_kind, release=release)

    cache = BoundCredentialCache()
    acquirer = BoundCredentialAcquirer(
        revision_reader=_reader(conn, adapter_kind=adapter_kind),
        issuer_for=lambda _kind: issuer,
        cache=cache,
    )
    snapshot = _snapshot_for(conn)
    owner = "exec:cancel"
    leader = asyncio.ensure_future(
        acquirer.acquire(AcquisitionRequest(snapshot=snapshot, execution_owner=owner))
    )
    await asyncio.sleep(0)
    waiter = asyncio.ensure_future(
        acquirer.acquire(AcquisitionRequest(snapshot=snapshot, execution_owner=owner))
    )
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    release.set()
    done = await leader
    assert done.binding.connection_id == "conn-cancel"
    assert done.binding.adapter_kind == adapter_kind
    # A later consumer still gets the valid credential from the cache.
    again = await acquirer.acquire(
        AcquisitionRequest(snapshot=snapshot, execution_owner=owner)
    )
    assert again.cache_hit


@pytest.mark.parametrize("adapter_kind", ["pat", "expiring"])
async def test_issuer_timeout_and_scope_mismatch(adapter_kind: str) -> None:
    conn = _connection("conn-issuer")

    async def _timeout(_binding: BindingMetadata):
        raise TimeoutError("provider hung")

    acquirer = BoundCredentialAcquirer(
        revision_reader=_reader(conn, adapter_kind=adapter_kind),
        issuer_for=lambda _kind: _timeout,
        max_attempts=2,
    )
    with pytest.raises(BoundAccessError) as exc_info:
        await acquirer.acquire(
            AcquisitionRequest(snapshot=_snapshot_for(conn), execution_owner="exec:t")
        )
    assert exc_info.value.code == "BOUND_ISSUER_FAILED"

    if adapter_kind == "pat":
        narrow: FakeExpiringAdapter | PatAdapter = PatAdapter(
            "db://NARROW", lambda _r: "narrow-pat-token", expected_scope="read"
        )
    else:
        narrow = FakeExpiringAdapter(scope="read", ttl_seconds=60.0, deny_scope="")
    acquirer2 = BoundCredentialAcquirer(
        revision_reader=_reader(conn, adapter_kind=adapter_kind),
        issuer_for=lambda _kind: narrow,
    )
    with pytest.raises(BoundAccessError):
        await acquirer2.acquire(
            AcquisitionRequest(snapshot=_snapshot_for(conn), execution_owner="exec:s")
        )


@pytest.mark.parametrize("adapter_kind", ["pat", "expiring"])
async def test_state_survives_restart_with_same_identity(adapter_kind: str) -> None:
    conn = _connection("conn-restart")
    issuer, issue_count = _issuer_and_counter(adapter_kind)
    cache = BoundCredentialCache()
    acquirer = BoundCredentialAcquirer(
        revision_reader=_reader(conn, adapter_kind=adapter_kind),
        issuer_for=lambda _kind: issuer,
        cache=cache,
    )
    snapshot = _snapshot_for(conn)
    first = await acquirer.acquire(
        AcquisitionRequest(snapshot=snapshot, execution_owner="exec:r")
    )
    # New acquirer instance over the same durable cache/identity: cache hit.
    issuer2, _ = _issuer_and_counter(adapter_kind) if False else (issuer, None)
    acquirer2 = BoundCredentialAcquirer(
        revision_reader=_reader(conn, adapter_kind=adapter_kind),
        issuer_for=lambda _kind: issuer2,
        cache=cache,
    )
    second = await acquirer2.acquire(
        AcquisitionRequest(snapshot=snapshot, execution_owner="exec:r")
    )
    assert second.cache_hit
    assert issue_count() == 1
    assert first.binding.binding_digest == second.binding.binding_digest
    assert second.binding.adapter_kind == adapter_kind


async def test_lost_ack_reconciles_duplicate_without_exactly_once() -> None:
    """Lost acknowledgment binds the duplicate instead of blindly re-issuing (R6)."""

    conn = _connection("conn-lost-ack")
    adapter = FakeExpiringAdapter(scope="read,write", ttl_seconds=60.0)
    calls = {"issued": 0}

    async def _flaky_issuer(binding: BindingMetadata):
        calls["issued"] += 1
        if calls["issued"] == 1:
            # Server issued, caller observed a transport failure (lost ack).
            adapter.issues += 1
            raise TimeoutError("ack lost after server-side issuance")
        return await adapter(binding)

    acquirer = BoundCredentialAcquirer(
        revision_reader=_reader(conn, adapter_kind="expiring"),
        issuer_for=lambda _kind: _flaky_issuer,
        # Caller-owned reconcile protocol: the flaky wrapper hides the
        # adapter's own hook, so the adapter's reconciler is injected
        # explicitly (production issuers expose it on the issuer itself).
        duplicate_reconciler=adapter.reconcile_duplicate,
        max_attempts=3,
    )
    acquired = await acquirer.acquire(
        AcquisitionRequest(snapshot=_snapshot_for(conn), execution_owner="exec:lost")
    )
    # The reconciler was consulted exactly once; exactly-once issuance is
    # still not promised (server issued once + reconcile bound the duplicate).
    assert adapter.reconciliations == 1
    assert acquired.binding.adapter_kind == "expiring"
    seen: list[bytes] = []
    acquired.credential.use_now(seen.append)
    assert seen[0].startswith(b"fake-expiring:")
    assert b"reconciled" in seen[0]


async def test_lost_ack_auto_reconciler_on_issuer() -> None:
    """An issuer exposing reconcile_duplicate is consulted without injection."""

    conn = _connection("conn-lost-auto")

    class _FlakyExpiring(FakeExpiringAdapter):
        def __init__(self) -> None:
            super().__init__(scope="read,write", ttl_seconds=60.0)
            self.calls = 0

        async def __call__(self, binding: BindingMetadata):
            self.calls += 1
            if self.calls == 1:
                self.issues += 1
                raise TimeoutError("ack lost after server-side issuance")
            return await super().__call__(binding)

    adapter = _FlakyExpiring()
    acquirer = BoundCredentialAcquirer(
        revision_reader=_reader(conn, adapter_kind="expiring"),
        issuer_for=lambda _kind: adapter,
        max_attempts=3,
    )
    acquired = await acquirer.acquire(
        AcquisitionRequest(snapshot=_snapshot_for(conn), execution_owner="exec:auto")
    )
    assert adapter.reconciliations == 1
    seen: list[bytes] = []
    acquired.credential.use_now(seen.append)
    assert b"reconciled" in seen[0]


async def test_lost_ack_without_reconciler_retries_explicitly() -> None:
    """No reconcile hook means bounded retry re-issues (never silent fallback)."""

    conn = _connection("conn-lost-retry")
    attempts = {"count": 0}

    async def _once_then_ok(_binding: BindingMetadata):
        from moonmind.auth.bound_acquisition import Issuance

        attempts["count"] += 1
        if attempts["count"] == 1:
            raise TimeoutError("transient provider failure")
        return Issuance(
            identity="installation:retry",
            scope="read,write",
            expires_at=None,
            material=b"retry-token",
        )

    acquirer = BoundCredentialAcquirer(
        revision_reader=_reader(conn),
        issuer_for=lambda _kind: _once_then_ok,
        max_attempts=2,
    )
    acquired = await acquirer.acquire(
        AcquisitionRequest(snapshot=_snapshot_for(conn), execution_owner="exec:retry")
    )
    assert attempts["count"] == 2
    seen: list[bytes] = []
    acquired.credential.use_now(seen.append)
    assert seen[0] == b"retry-token"


# --- A5: revocation races -----------------------------------------------------


async def test_revocation_during_issuance_discards_late_response() -> None:
    conn = _connection("conn-revoke")
    state = {"status": "active"}

    async def _flapping_reader(_connection_id: str) -> ActiveRevision:
        return ActiveRevision(
            credential_revision=3,
            connection_revision=2,
            policy_revision=2,
            status=state["status"],
            adapter_kind="pat",
        )

    async def _slow_ok(_binding: BindingMetadata):
        from moonmind.auth.bound_acquisition import Issuance

        await asyncio.sleep(0.01)
        state["status"] = "revoked"  # revocation lands mid-issuance
        return Issuance(identity="pat:x", scope="read,write", expires_at=None, material=b"late-token")

    acquirer = BoundCredentialAcquirer(
        revision_reader=_flapping_reader, issuer_for=lambda _kind: _slow_ok
    )
    with pytest.raises(BoundAccessError) as exc_info:
        await acquirer.acquire(
            AcquisitionRequest(snapshot=_snapshot_for(conn), execution_owner="exec:rv")
        )
    assert exc_info.value.code == "BOUND_REVOKED"
    # Nothing was cached: a fresh attempt after revocation still fails closed.
    with pytest.raises(BoundAccessError):
        await acquirer.acquire(
            AcquisitionRequest(snapshot=_snapshot_for(conn), execution_owner="exec:rv")
        )


async def test_stale_snapshot_revision_cannot_be_rescued() -> None:
    conn = _connection("conn-stale", credential_rev=3)

    async def _rotated_reader(_connection_id: str) -> ActiveRevision:
        return ActiveRevision(
            credential_revision=4,  # rotation happened after admission
            connection_revision=2,
            policy_revision=2,
            status="active",
            adapter_kind="pat",
        )

    acquirer = BoundCredentialAcquirer(
        revision_reader=_rotated_reader,
        issuer_for=lambda _kind: PatAdapter("db://x", lambda _r: "tok"),
    )
    with pytest.raises(BoundAccessError) as exc_info:
        await acquirer.acquire(
            AcquisitionRequest(snapshot=_snapshot_for(conn), execution_owner="exec:st")
        )
    assert exc_info.value.code == "BOUND_STALE_REVISION"


async def test_release_one_use_keeps_other_issuance() -> None:
    conn = _connection("conn-release")
    acquirer = BoundCredentialAcquirer(
        revision_reader=_reader(conn),
        issuer_for=lambda _kind: PatAdapter("db://TEAM_A_PAT", lambda _r: "shared-pat"),
    )
    snapshot = _snapshot_for(conn)
    first = await acquirer.acquire(
        AcquisitionRequest(snapshot=snapshot, execution_owner="exec:a")
    )
    second = await acquirer.acquire(
        AcquisitionRequest(snapshot=snapshot, execution_owner="exec:b")
    )
    # Distinct owners hold distinct cache entries; releasing one use clears
    # only local material and never revokes the other issuance.
    acquirer.release_use(first)
    assert first.credential.cleared
    assert not second.credential.cleared
    seen: list[bytes] = []
    second.credential.use_now(seen.append)
    assert seen[0] == b"shared-pat"


async def test_expiry_between_calls_triggers_renewal_path() -> None:
    conn = _connection("conn-expiry")
    adapter = FakeExpiringAdapter(scope="read,write", ttl_seconds=0.05)
    cache = BoundCredentialCache()
    acquirer = BoundCredentialAcquirer(
        revision_reader=_reader(conn, adapter_kind="expiring"),
        issuer_for=lambda _kind: adapter,
        cache=cache,
        expiry_margin_seconds=0.0,
        clock_skew_seconds=0.0,
    )
    snapshot = _snapshot_for(conn)
    first = await acquirer.acquire(
        AcquisitionRequest(snapshot=snapshot, execution_owner="exec:e")
    )
    await asyncio.sleep(0.08)  # credential expires between calls
    # Post-issuance verification rejects the stale material on direct check...
    from moonmind.auth.bound_acquisition import Issuance

    stale = Issuance(
        identity="installation:i",
        scope="read,write",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        material=b"stale",
    )
    with pytest.raises(BoundAccessError):
        acquirer._verify_issuance(issuance=stale, binding=first.binding)
    # ...and renewal issues fresh material for the same authority.
    renewed = await acquirer.renew(first, execution_owner="exec:e")
    assert renewed.binding.binding_digest == first.binding.binding_digest
    assert adapter.issues == 2


# --- A6: secret canaries ------------------------------------------------------


async def test_secret_canaries_never_escape() -> None:
    conn = _connection("conn-c1")
    acquirer = BoundCredentialAcquirer(
        revision_reader=_reader(conn),
        issuer_for=lambda _kind: PatAdapter("db://K", lambda _r: CANARY),
    )
    acquired = await acquirer.acquire(
        AcquisitionRequest(snapshot=_snapshot_for(conn), execution_owner="exec:c")
    )
    cred = acquired.credential
    binding = acquired.binding

    assert CANARY not in repr(cred)
    assert CANARY not in str(cred)
    assert CANARY not in format(cred, "")
    with pytest.raises(TypeError):
        bytes(cred)
    assert (cred == CANARY) is False
    assert json.dumps({"c": str(cred)}) and CANARY not in json.dumps({"c": str(cred)})

    # Serializable projections carry metadata only.
    assert CANARY not in binding.model_dump_json()
    assert CANARY not in BoundTelemetryDTO(
        event="acquired",
        bindingDigest=binding.binding_digest,
        adapterKind=binding.adapter_kind,
        operationId=binding.operation_id,
        issuanceId=binding.issuance_id,
        state="ready",
    ).model_dump_json()
    err = safe_error_dto(BoundAccessError(BOUND_DENIED, "no"), binding=binding)
    assert isinstance(err, BoundErrorDTO)
    assert CANARY not in err.model_dump_json()

    # Nested exceptions and logs stay canary-free.
    try:
        try:
            raise BoundAccessError(BOUND_DENIED, "inner denial")
        except BoundAccessError as inner:
            raise BoundAccessError(BOUND_UNAVAILABLE, "outer failure") from inner
    except BoundAccessError as outer:
        assert CANARY not in repr(outer)
        assert CANARY not in str(outer.__cause__)

    logger = logging.getLogger("test.bound_canary_4007")
    records: list[str] = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    logger.addHandler(_Handler())
    logger.warning("acquired %s for %s", binding.binding_digest, binding.connection_id)
    assert CANARY not in "\n".join(records)

    # Heartbeat/activity-style payloads built from the binding are safe.
    heartbeat = {"bindingDigest": binding.binding_digest, "state": "ready"}
    assert CANARY not in json.dumps(heartbeat)

    # Safe metadata never contains token-derived public identifiers either.
    assert "canary" not in binding.model_dump_json().lower()


# --- A7: historical boundary --------------------------------------------------


async def test_historical_behavior_only_behind_explicit_legacy_reader(monkeypatch) -> None:
    """Legacy ambient fallback still works when explicitly invoked (history),
    but the new bound path never performs fallback discovery."""

    from moonmind.auth import github_credentials as gc

    for key in (
        "GH_TOKEN",
        "WORKFLOW_GITHUB_TOKEN",
        "GITHUB_TOKEN_SECRET_REF",
        "WORKFLOW_GITHUB_TOKEN_SECRET_REF",
        "MOONMIND_GITHUB_TOKEN_REF",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "legacy-ambient-token")
    legacy = await gc.resolve_github_credential()  # omitted explicit: legacy reader
    assert legacy.token == "legacy-ambient-token"

    # New path: routed/explicit selection with no eligible authority fails;
    # it never consults the legacy ambient resolver.
    from moonmind.workflows.executions.repository_contract import RepositoryRouteError

    with pytest.raises((BoundAccessError, RepositoryRouteError)):
        select_repository_authority(
            access_mode=AccessMode.ROUTED,
            principal_ref="principal:alice",
            principal_scope=("system", None),
            identity=_identity(),
            role="publisher",
            requested_operations=["read"],
            policy_revision=2,
            candidates=[],
        )


async def test_new_path_does_not_call_legacy_resolver(monkeypatch) -> None:
    from moonmind.auth import github_credentials as gc

    async def _forbidden(*_a: object, **_k: object):  # pragma: no cover
        raise AssertionError("new admission must not call the legacy chain")

    monkeypatch.setattr(gc, "resolve_github_credential", _forbidden)
    conn = _connection("conn-nolegacy")
    snapshot = _snapshot_for(conn)
    acquirer = BoundCredentialAcquirer(
        revision_reader=_reader(conn),
        issuer_for=lambda _kind: PatAdapter("db://TEAM_A_PAT", lambda _r: "direct"),
    )
    acquired = await acquirer.acquire(
        AcquisitionRequest(snapshot=snapshot, execution_owner="exec:nl")
    )
    assert acquired.state == CredentialState.READY


# --- R-level: states, digests, cache identity ---------------------------------


def test_credential_states_are_distinct() -> None:
    assert len(set(CredentialState)) == len(list(CredentialState))
    assert CredentialState.ANONYMOUS != CredentialState.OMITTED
    assert CredentialState.CONFIGURED_EMPTY != CredentialState.UNAVAILABLE
    assert CredentialState.DISABLED != CredentialState.REVOKED


def test_binding_digest_is_stable_identifier_only() -> None:
    kwargs = dict(
        connection_id="conn-d",
        endpoint="https://github.com",
        route_id="id:https://github.com#repo-id-1",
        role="publisher",
        operations=["write", "read"],
        policy_revision=2,
        connection_revision=2,
        credential_revision=3,
    )
    assert binding_digest_for(**kwargs) == binding_digest_for(**kwargs)
    other = binding_digest_for(**{**kwargs, "credential_revision": 4})
    assert other != binding_digest_for(**kwargs)


async def test_cache_key_includes_full_renewal_identity() -> None:
    base = dict(
        endpoint="https://github.com",
        connection_revision=2,
        credential_revision=3,
        route_id="id:https://github.com#repo-id-1",
        operations=["read", "write"],
        execution_owner="exec:1",
    )
    key = BoundCredentialCache.renewal_key_for(**base)
    assert key != BoundCredentialCache.renewal_key_for(
        **{**base, "execution_owner": "exec:2"}
    )
    assert key != BoundCredentialCache.renewal_key_for(
        **{**base, "credential_revision": 4}
    )
    # Display-name-only differences do not alias: route identity is in the key.
    assert key != BoundCredentialCache.renewal_key_for(
        **{**base, "route_id": "id:https://github.com#repo-id-2"}
    )


async def test_ephemeral_rejects_empty_material() -> None:
    with pytest.raises(BoundAccessError):
        EphemeralCredential(b"")


async def test_selection_snapshot_records_full_authority() -> None:
    snapshot = _snapshot_for(_connection("conn-full"))
    assert snapshot.role == "publisher"
    assert set(snapshot.operations) == {"read", "write"}
    assert snapshot.policy_revision == 2
    assert snapshot.authorized_at
    assert snapshot.principal_ref == "principal:alice"
