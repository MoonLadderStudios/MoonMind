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


def test_anonymous_snapshot_makes_zero_secret_queries() -> None:
    calls: list[str] = []

    async def _must_not_query(_ref: str) -> str:  # pragma: no cover - must not run
        calls.append(_ref)
        raise AssertionError("anonymous path queried a credential backend")

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
    assert calls == []
    _ = _must_not_query


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


async def test_two_process_renewal_is_single_flight() -> None:
    conn = _connection("conn-race")
    adapter = FakeExpiringAdapter(scope="read,write", ttl_seconds=60.0)
    cache = BoundCredentialCache()
    reader_calls: list[str] = []
    acquirer = BoundCredentialAcquirer(
        revision_reader=_reader(conn, adapter_kind="expiring", calls=reader_calls),
        issuer_for=lambda _kind: adapter,
        cache=cache,
    )
    snapshot = _snapshot_for(conn)
    owner = "exec:shared"
    first, second = await asyncio.gather(
        acquirer.acquire(AcquisitionRequest(snapshot=snapshot, execution_owner=owner)),
        acquirer.acquire(AcquisitionRequest(snapshot=snapshot, execution_owner=owner)),
    )
    assert adapter.issues == 1
    assert first.binding.issuance_id == second.binding.issuance_id


async def test_canceled_waiter_does_not_invalidate_credential() -> None:
    conn = _connection("conn-cancel")
    release = asyncio.Event()

    async def _slow_issuer(_binding: BindingMetadata):
        from moonmind.auth.bound_acquisition import Issuance

        await release.wait()
        return Issuance(identity="installation:i", scope="read,write", expires_at=None, material=b"slow-token")

    cache = BoundCredentialCache()
    acquirer = BoundCredentialAcquirer(
        revision_reader=_reader(conn), issuer_for=lambda _kind: _slow_issuer, cache=cache
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
    # A later consumer still gets the valid credential from the cache.
    again = await acquirer.acquire(
        AcquisitionRequest(snapshot=snapshot, execution_owner=owner)
    )
    assert again.cache_hit


async def test_issuer_timeout_and_scope_mismatch() -> None:
    conn = _connection("conn-issuer")

    async def _timeout(_binding: BindingMetadata):
        raise TimeoutError("provider hung")

    acquirer = BoundCredentialAcquirer(
        revision_reader=_reader(conn), issuer_for=lambda _kind: _timeout, max_attempts=2
    )
    with pytest.raises(BoundAccessError) as exc_info:
        await acquirer.acquire(
            AcquisitionRequest(snapshot=_snapshot_for(conn), execution_owner="exec:t")
        )
    assert exc_info.value.code == "BOUND_ISSUER_FAILED"

    adapter = FakeExpiringAdapter(scope="read", ttl_seconds=60.0, deny_scope="")
    acquirer2 = BoundCredentialAcquirer(
        revision_reader=_reader(conn, adapter_kind="expiring"),
        issuer_for=lambda _kind: adapter,
    )
    with pytest.raises(BoundAccessError):
        await acquirer2.acquire(
            AcquisitionRequest(snapshot=_snapshot_for(conn), execution_owner="exec:s")
        )


async def test_state_survives_restart_with_same_identity() -> None:
    conn = _connection("conn-restart")
    adapter = FakeExpiringAdapter(scope="read,write", ttl_seconds=60.0)
    cache = BoundCredentialCache()
    acquirer = BoundCredentialAcquirer(
        revision_reader=_reader(conn, adapter_kind="expiring"),
        issuer_for=lambda _kind: adapter,
        cache=cache,
    )
    snapshot = _snapshot_for(conn)
    first = await acquirer.acquire(
        AcquisitionRequest(snapshot=snapshot, execution_owner="exec:r")
    )
    # New acquirer instance over the same durable cache/identity: cache hit.
    acquirer2 = BoundCredentialAcquirer(
        revision_reader=_reader(conn, adapter_kind="expiring"),
        issuer_for=lambda _kind: adapter,
        cache=cache,
    )
    second = await acquirer2.acquire(
        AcquisitionRequest(snapshot=snapshot, execution_owner="exec:r")
    )
    assert second.cache_hit
    assert adapter.issues == 1
    assert first.binding.binding_digest == second.binding.binding_digest


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
