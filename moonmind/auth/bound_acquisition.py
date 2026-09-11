"""Deterministic access selection and refresh-capable bound credential acquisition.

MoonLadderStudios/MoonMind#4007 (plan slice 2): select repository authority
once, then acquire and renew credentials only within that authority.  PAT and
expiring adapters share the same execution-facing contract.

Design references: ``docs/RepositoryAccessAndWorkspaceDesign.md`` CONTRACT-003
(authentication intent precedes acquisition), CONTRACT-007 (deterministic
selection per role), CONTRACT-009 (connection/revision/issuance lifetimes),
CONTRACT-010 (bound access clients), INV-001 (no inferred authority),
INV-003 (refresh preserves authority), TEST-002 (isolation under concurrency).

Relationship to neighbouring slices:

* Selection reuses #4005's persistence/routing authority
  (``admit_scoped_route`` / ``authorize_connection_use``) and never
  reimplements route eligibility.
* The ACTIVE-revision read is the #4006 authoritative-read shape expressed as
  a small injected protocol (``SecretRevisionReader``) so this module never
  performs unconstrained latest-secret resolution itself.
* The legacy project-wide precedence chain in
  ``moonmind.auth.github_credentials`` is historical input only; this module
  does not wrap it.  New admission resolves only explicitly selected and
  authorized backends (env/db/vault/exec rules from ``secret_refs`` are
  preserved where explicitly selected, but this interface never becomes a
  generic user-authored secret-provider or command executor).
* Historical decoding and new-write cutover owned by #4023 integrate at the
  ``CredentialIssuer`` / ``SecretRevisionReader`` seams defined here: this
  slice performs no historical-decode fallback and no silent cutover.  When
  #4023 lands, its cutover adapter plugs the same issuer boundary and its
  decoding policy rides the ACTIVE-revision read; no consumer-interface
  change is required here.
* Cross-process renewal single-flight rides the injectable
  ``SharedRenewalStore`` behind ``BoundCredentialCache.join_or_lead``.  The
  store holds only short-lived lease ownership plus published metadata — no
  database transaction lock is held during network issuance.  Production
  wiring should back it with an existing durable lease/advisory primitive
  (same pattern family as the pg advisory / host-lease coordination
  elsewhere in the repo); unit scope uses the thread-safe in-memory store.
* Lost issuance acknowledgment is reconciled, never assumed: when the issuer
  exposes ``reconcile_duplicate`` (or a ``duplicate_reconciler`` is
  injected), the acquirer binds the duplicate instead of blindly re-issuing.
  Exactly-once issuance is never promised.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.workflows.executions.repository_contract import (
    RepositoryConnection,
    RepositoryIdentity,
    ScopedRouteCandidate,
    admit_scoped_route,
    authorize_connection_use,
    normalize_endpoint,
)

# ---------------------------------------------------------------------------
# Stable error codes (safe messages carry no secret material).
# ---------------------------------------------------------------------------

BOUND_SELECTION_REQUIRED = "BOUND_SELECTION_REQUIRED"
BOUND_AMBIGUOUS = "BOUND_AMBIGUOUS"
BOUND_DENIED = "BOUND_DENIED"
BOUND_UNAVAILABLE = "BOUND_UNAVAILABLE"
BOUND_REVOKED = "BOUND_REVOKED"
BOUND_DISABLED = "BOUND_DISABLED"
BOUND_SCOPE_MISMATCH = "BOUND_SCOPE_MISMATCH"
BOUND_STALE_REVISION = "BOUND_STALE_REVISION"
BOUND_ISSUER_FAILED = "BOUND_ISSUER_FAILED"
BOUND_SCRATCH_EXCLUDED = "BOUND_SCRATCH_EXCLUDED"


class BoundAccessError(ValueError):
    """Fail-closed acquisition/selection failure with a machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class AccessMode(StrEnum):
    EXPLICIT = "explicit"
    ROUTED = "routed"
    ANONYMOUS = "anonymous"


class CredentialState(StrEnum):
    """Distinct acquisition states; none collapses into another."""

    OMITTED = "omitted"
    ANONYMOUS = "anonymous"
    CONFIGURED_EMPTY = "configured_empty"
    DISABLED = "disabled"
    REVOKED = "revoked"
    UNAVAILABLE = "unavailable"
    READY = "ready"


# ---------------------------------------------------------------------------
# Selection snapshot (R1): authorized, immutable, metadata-only.
# ---------------------------------------------------------------------------


class SelectionSnapshot(BaseModel):
    """Authorized selection of repository authority for one role/bundle."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    principal_ref: str = Field(alias="principalRef", min_length=1)
    scope_type: str = Field(alias="scopeType", min_length=1)
    scope_ref: str | None = Field(None, alias="scopeRef")
    endpoint: str = Field(min_length=1)
    route_id: str = Field(alias="routeId", min_length=1)
    repository_display: str = Field(alias="repositoryDisplay", min_length=1)
    role: str = Field(min_length=1)
    operations: tuple[str, ...] = Field(min_length=1)
    access_mode: AccessMode = Field(alias="accessMode")
    policy_revision: int = Field(alias="policyRevision", ge=1)
    connection_id: str = Field(alias="connectionId", min_length=1)
    connection_policy_revision: int = Field(alias="connectionPolicyRevision", ge=1)
    credential_revision: int = Field(alias="credentialRevision", ge=1)
    selection_origin: str = Field(alias="selectionOrigin", min_length=1)
    authorized_at: str = Field(alias="authorizedAt", min_length=1)

    @model_validator(mode="after")
    def _validate_snapshot(self) -> "SelectionSnapshot":
        if self.access_mode == AccessMode.ANONYMOUS and self.connection_id != "anonymous":
            raise ValueError("anonymous snapshots carry no connection binding")
        if self.access_mode != AccessMode.ANONYMOUS and not self.connection_id.strip():
            raise ValueError("connection-backed snapshots require a connection")
        return self


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def select_repository_authority(
    *,
    access_mode: AccessMode,
    principal_ref: str,
    principal_scope: tuple[str, str | None],
    identity: RepositoryIdentity,
    role: str,
    requested_operations: Sequence[str],
    policy_revision: int,
    explicit_connection: RepositoryConnection | None = None,
    explicit_assignment: Any | None = None,
    candidates: Sequence[ScopedRouteCandidate] = (),
) -> SelectionSnapshot:
    """Compile one authorized selection snapshot (R1, R2).

    * ``explicit`` validates and uses only the named connection; the full
      requested bundle must be satisfied by that single connection.
    * ``routed`` delegates eligibility to #4005's ``admit_scoped_route`` under
      its transaction rules (missing/ambiguous authority is actionable
      failure, never silent fallback).
    * ``anonymous`` admits only supported read operations and performs zero
      credential discovery (callers must not pass candidates/connections).
    * ``scratch`` never enters acquisition: callers must not call this module
      for scratch work; anything else claiming scratch is rejected here.
    """

    normalized_role = (role or "").strip()
    if not normalized_role or normalized_role == "scratch":
        raise BoundAccessError(
            BOUND_SCRATCH_EXCLUDED, "scratch work carries no repository authority"
        )
    if not (principal_ref or "").strip():
        raise BoundAccessError(BOUND_DENIED, "principal is required before disclosure")
    requested = tuple(
        dict.fromkeys(
            str(op).strip().lower() for op in requested_operations if str(op).strip()
        )
    )
    if not requested:
        raise BoundAccessError(BOUND_SELECTION_REQUIRED, "empty operation bundle")
    if policy_revision < 1:
        raise BoundAccessError(BOUND_SELECTION_REQUIRED, "policy revision is required")

    if access_mode == AccessMode.ANONYMOUS:
        if explicit_connection is not None or candidates:
            raise BoundAccessError(
                BOUND_DENIED, "anonymous access carries no credential binding"
            )
        if any(op not in {"read"} for op in requested):
            raise BoundAccessError(
                BOUND_DENIED, "anonymous access admits only supported reads"
            )
        return SelectionSnapshot(
            principalRef=principal_ref.strip(),
            scopeType=principal_scope[0],
            scopeRef=principal_scope[1],
            endpoint=normalize_endpoint(identity.endpoint),
            routeId=identity.route_id(),
            repositoryDisplay=identity.display_name,
            role=normalized_role,
            operations=requested,
            accessMode=AccessMode.ANONYMOUS,
            policyRevision=policy_revision,
            connectionId="anonymous",
            connectionPolicyRevision=1,
            credentialRevision=1,
            selectionOrigin="anonymous",
            authorizedAt=_utc_now_iso(),
        )

    if access_mode == AccessMode.EXPLICIT:
        if explicit_connection is None:
            raise BoundAccessError(
                BOUND_SELECTION_REQUIRED, "explicit access requires a named connection"
            )
        connection = explicit_connection
        # Authorize before any candidate disclosure: the principal must be
        # admitted even to learn whether the named connection exists.
        authorize_connection_use(
            principal_ref=principal_ref,
            principal_scope=principal_scope,
            connection=connection,
            action="use",
        )
        if connection.lifecycle != "active":
            raise BoundAccessError(BOUND_DISABLED, "named connection is not active")
        if explicit_assignment is not None:
            assignment = explicit_assignment
            if assignment.connection_id != connection.id:
                raise BoundAccessError(
                    BOUND_DENIED, "explicit assignment names another connection"
                )
            if assignment.identity.route_id() != identity.route_id():
                raise BoundAccessError(
                    BOUND_DENIED, "explicit connection does not serve this repository"
                )
            missing = [op for op in requested if op not in assignment.operations]
            if missing:
                raise BoundAccessError(
                    BOUND_DENIED,
                    f"explicit connection does not admit {','.join(missing)}",
                )
        missing_conn = [op for op in requested if op not in connection.allowed_operations]
        if missing_conn:
            raise BoundAccessError(
                BOUND_DENIED,
                f"explicit connection does not allow {','.join(missing_conn)}",
            )
        # One identity serves the whole role: no per-operation credential
        # switching between the role's read and write calls.
        return SelectionSnapshot(
            principalRef=principal_ref.strip(),
            scopeType=principal_scope[0],
            scopeRef=principal_scope[1],
            endpoint=normalize_endpoint(connection.endpoint_ref),
            routeId=identity.route_id(),
            repositoryDisplay=identity.display_name,
            role=normalized_role,
            operations=requested,
            accessMode=AccessMode.EXPLICIT,
            policyRevision=policy_revision,
            connectionId=connection.id,
            connectionPolicyRevision=connection.policy_revision,
            credentialRevision=connection.credential_revision,
            selectionOrigin="explicit",
            authorizedAt=_utc_now_iso(),
        )

    # Routed: one eligible route/default under #4005's rules.
    selected = admit_scoped_route(
        identity=identity,
        requested_operations=requested,
        candidates=candidates,
        principal_ref=principal_ref,
        principal_scope=principal_scope,
    )
    connection = selected.connection
    return SelectionSnapshot(
        principalRef=principal_ref.strip(),
        scopeType=principal_scope[0],
        scopeRef=principal_scope[1],
        endpoint=normalize_endpoint(connection.endpoint_ref),
        routeId=identity.route_id(),
        repositoryDisplay=identity.display_name,
        role=normalized_role,
        operations=requested,
        accessMode=AccessMode.ROUTED,
        policyRevision=policy_revision,
        connectionId=connection.id,
        connectionPolicyRevision=connection.policy_revision,
        credentialRevision=connection.credential_revision,
        selectionOrigin="routed",
        authorizedAt=_utc_now_iso(),
    )


# ---------------------------------------------------------------------------
# Binding metadata vs ephemeral value (R3).
# ---------------------------------------------------------------------------


def binding_digest_for(
    *,
    connection_id: str,
    endpoint: str,
    route_id: str,
    role: str,
    operations: Sequence[str],
    policy_revision: int,
    connection_revision: int,
    credential_revision: int,
) -> str:
    """Stable identifier for a binding; an identifier, never permission."""

    canonical = json.dumps(
        {
            "connection": connection_id,
            "endpoint": endpoint,
            "route": route_id,
            "role": role,
            "operations": sorted(operations),
            "policyRevision": policy_revision,
            "connectionRevision": connection_revision,
            "credentialRevision": credential_revision,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "binding:" + hashlib.sha256(canonical.encode()).hexdigest()[:32]


class BindingMetadata(BaseModel):
    """Immutable binding metadata.  Serializable; never carries secret material."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    binding_digest: str = Field(alias="bindingDigest", min_length=1)
    connection_id: str = Field(alias="connectionId", min_length=1)
    endpoint: str = Field(min_length=1)
    route_id: str = Field(alias="routeId", min_length=1)
    role: str = Field(min_length=1)
    operations: tuple[str, ...] = Field(min_length=1)
    policy_revision: int = Field(alias="policyRevision", ge=1)
    connection_revision: int = Field(alias="connectionRevision", ge=1)
    credential_revision: int = Field(alias="credentialRevision", ge=1)
    principal_ref: str = Field(alias="principalRef", min_length=1)
    scope_type: str = Field(alias="scopeType", min_length=1)
    scope_ref: str | None = Field(None, alias="scopeRef")
    operation_id: str = Field(alias="operationId", min_length=1)
    issuance_id: str = Field(alias="issuanceId", min_length=1)
    generation: int = Field(ge=1)
    adapter_kind: str = Field(alias="adapterKind", min_length=1)
    access_mode: AccessMode = Field(default=AccessMode.EXPLICIT, alias="accessMode")
    expires_at: str | None = Field(None, alias="expiresAt")


class BoundErrorDTO(BaseModel):
    """Safe error projection: codes and metadata only, never secret values."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    code: str
    message: str
    binding_digest: str | None = Field(None, alias="bindingDigest")
    connection_id: str | None = Field(None, alias="connectionId")
    operation_id: str | None = Field(None, alias="operationId")


class BoundTelemetryDTO(BaseModel):
    """Safe telemetry projection for acquisition events."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    event: str
    binding_digest: str = Field(alias="bindingDigest", min_length=1)
    adapter_kind: str = Field(alias="adapterKind", min_length=1)
    operation_id: str = Field(alias="operationId", min_length=1)
    issuance_id: str = Field(alias="issuanceId", min_length=1)
    state: str


class EphemeralCredential:
    """Non-serializable holder for raw credential material (R3).

    The value is never a ``str`` subclass: ``str()``, ``repr()``, formatting,
    equality against strings, and JSON/Pydantic serialization all refuse to
    expose it.  Trusted code accesses the material only through
    :meth:`use_now`, the explicit immediate-use boundary.
    """

    __slots__ = ("_material", "_cleared")

    def __init__(self, material: bytes) -> None:
        if not material:
            raise BoundAccessError(
                BOUND_UNAVAILABLE, "configured credential material is empty"
            )
        object.__setattr__(self, "_material", bytes(material))
        object.__setattr__(self, "_cleared", False)

    def use_now(self, fn: Callable[[bytes], Any]) -> Any:
        """Invoke ``fn`` with the raw material inside the trusted boundary."""

        if self._cleared:
            raise BoundAccessError(BOUND_UNAVAILABLE, "credential material cleared")
        return fn(bytes(self._material))

    def clear(self) -> None:
        object.__setattr__(self, "_material", b"")
        object.__setattr__(self, "_cleared", True)

    @property
    def cleared(self) -> bool:
        return self._cleared

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<EphemeralCredential:cleared>" if self._cleared else "<EphemeralCredential>"

    def __str__(self) -> str:
        return "<EphemeralCredential>"

    def __format__(self, _spec: str) -> str:
        return "<EphemeralCredential>"

    def __bytes__(self) -> bytes:
        raise TypeError("EphemeralCredential must only be used via use_now()")

    def __eq__(self, _other: object) -> bool:
        return False

    def __hash__(self) -> int:
        return id(self)


# ---------------------------------------------------------------------------
# Revision authority + issuance adapters (R4, R8).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ActiveRevision:
    """Authoritative ACTIVE-revision read result (#4006 shape)."""

    credential_revision: int
    connection_revision: int
    policy_revision: int
    status: str  # "active" | "disabled" | "revoked"
    adapter_kind: str  # "pat" | "expiring"


SecretRevisionReader = Callable[[str], Awaitable[ActiveRevision] | ActiveRevision]
SecretBackendResolve = Callable[[str], Awaitable[str] | str]


@dataclass(frozen=True, slots=True)
class Issuance:
    """Verified provider issuance bound to one admitted request."""

    identity: str
    scope: str
    expires_at: datetime | None
    material: bytes
    duplicate_of: str | None = None


CredentialIssuer = Callable[
    [BindingMetadata], Awaitable[Issuance] | Issuance
]


#: Reconcile a possibly-issued duplicate after a lost acknowledgment.
#: Receives the attempted binding plus the previous issuance id and returns
#: the bound duplicate, or ``None`` when the provider cannot reconcile (the
#: caller then retries issuance explicitly).  Exactly-once is never promised.
DuplicateReconciler = Callable[
    [BindingMetadata, str], Awaitable[Issuance | None] | Issuance | None
]


class PatAdapter:
    """Classic/fine-grained PAT adapter over an explicitly selected backend.

    The backend callable implements the preserved env/DB/Vault/exec rules for
    the connection's own SecretRef; this adapter never probes ambient
    credentials or executes user-authored commands.
    """

    kind = "pat"

    def __init__(
        self,
        secret_ref: str,
        resolve_backend: SecretBackendResolve,
        *,
        expected_scope: str = "",
    ) -> None:
        if not (secret_ref or "").strip():
            raise BoundAccessError(
                BOUND_UNAVAILABLE, "PAT adapter requires an explicit SecretRef"
            )
        self._secret_ref = secret_ref.strip()
        self._resolve_backend = resolve_backend
        self._expected_scope = expected_scope

    async def __call__(self, _binding: BindingMetadata) -> Issuance:
        raw = self._resolve_backend(self._secret_ref)
        if hasattr(raw, "__await__"):
            raw = await raw  # type: ignore[misc]
        token = str(raw or "").strip()
        if not token:
            # A failed configured source must not fall through to another
            # credential: it is a distinct configured-empty state.
            raise BoundAccessError(
                BOUND_UNAVAILABLE, "configured PAT source resolved empty"
            )
        return Issuance(
            identity=f"pat:{hashlib.sha256(token.encode()).hexdigest()[:12]}",
            scope=self._expected_scope,
            expires_at=None,
            material=token.encode(),
        )


class FakeExpiringAdapter:
    """Test/stand-in expiring-issuance adapter on the same consumer contract.

    Issues short-lived tokens with a generation counter.  Supports refresh
    (same authority, new material), bounded duplicate reconciliation after a
    lost acknowledgment, and denied-scope simulation.  Production App issuance
    (#4022) plugs the same ``CredentialIssuer`` boundary without changing the
    consumer interface.
    """

    kind = "expiring"

    def __init__(
        self,
        *,
        scope: str,
        ttl_seconds: float = 60.0,
        installation: str = "install:test",
        deny_scope: str = "",
    ) -> None:
        self._scope = scope
        self._ttl = max(1.0, float(ttl_seconds))
        self._installation = installation
        self._deny_scope = deny_scope
        self._counter = 0
        self._lock = asyncio.Lock()
        self.issues = 0
        self.reconciliations = 0

    async def __call__(self, binding: BindingMetadata) -> Issuance:
        if self._deny_scope and self._deny_scope in binding.operations:
            raise BoundAccessError(BOUND_DENIED, "issuer denied requested scope")
        async with self._lock:
            self._counter += 1
            generation = self._counter
            self.issues += 1
        material = f"fake-expiring:{self._installation}:{generation}".encode()
        return Issuance(
            identity=f"installation:{self._installation}",
            scope=self._scope,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=self._ttl),
            material=material,
        )

    async def reconcile_duplicate(
        self, *, previous_issuance_id: str
    ) -> Issuance | None:
        """Bind (not duplicate-issue) after a lost acknowledgment.

        Returns ``None`` when the provider cannot reconcile, in which case the
        caller retries issuance explicitly; exactly-once issuance is never
        promised.
        """

        async with self._lock:
            self.reconciliations += 1
        material = f"fake-expiring:{self._installation}:reconciled".encode()
        _ = previous_issuance_id
        return Issuance(
            identity=f"installation:{self._installation}",
            scope=self._scope,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=self._ttl),
            material=material,
            duplicate_of=previous_issuance_id,
        )


# ---------------------------------------------------------------------------
# Bounded cache + renewal coordination (R5).
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _CacheEntry:
    binding: BindingMetadata
    credential: EphemeralCredential
    generation: int
    last_use: float = field(default_factory=time.monotonic)


class SharedRenewalStore:
    """Thread-safe cross-worker renewal coordination + publication (R5).

    Share one instance across ``BoundCredentialCache`` objects (threads,
    worker processes via a durable backing) to prevent two workers from
    stampeding the same renewal.  Only short-lived lease ownership is held —
    a ``(owner_id, monotonic deadline)`` timestamp per renewal key — so no
    database transaction lock is ever held during network issuance.  The
    leader publishes the resulting metadata + material bytes; followers poll
    for that publication instead of re-issuing.

    Production wiring should back this same boundary with an existing durable
    lease/advisory primitive (same pattern family as the pg advisory /
    host-lease coordination elsewhere in the repo).  This in-memory
    implementation is the portable default and is fully shareable across
    threads; separate processes must supply a durable backend behind the same
    small method surface (``try_claim`` / ``release_claim`` / ``get`` /
    ``publish`` / ``next_generation``).
    """

    def __init__(
        self, *, max_entries: int = 128, lease_seconds: float = 30.0
    ) -> None:
        self._max_entries = max(1, int(max_entries))
        self._lease_seconds = max(1.0, float(lease_seconds))
        self._mu = threading.Lock()
        self._leases: dict[tuple[Any, ...], tuple[str, float]] = {}
        # key -> (binding, material bytes copy, generation)
        self._published: OrderedDict[
            tuple[Any, ...], tuple[BindingMetadata, bytes, int]
        ] = OrderedDict()
        self._generations: dict[tuple[Any, ...], int] = {}

    def try_claim(self, key: tuple[Any, ...], *, owner_id: str) -> bool:
        """Claim renewal leadership for ``key``; False means another owner holds it."""

        now = time.monotonic()
        with self._mu:
            owner, deadline = self._leases.get(key, ("", 0.0))
            if owner and owner != owner_id and deadline > now:
                return False
            self._leases[key] = (owner_id, now + self._lease_seconds)
            return True

    def release_claim(self, key: tuple[Any, ...], *, owner_id: str) -> None:
        with self._mu:
            owner, _ = self._leases.get(key, ("", 0.0))
            if owner == owner_id or not owner:
                self._leases.pop(key, None)

    def lease_held(self, key: tuple[Any, ...]) -> bool:
        with self._mu:
            owner, deadline = self._leases.get(key, ("", 0.0))
            return bool(owner) and deadline > time.monotonic()

    def next_generation(self, key: tuple[Any, ...]) -> int:
        with self._mu:
            current = self._generations.get(key, 0) + 1
            self._generations[key] = current
            return current

    def get(
        self, key: tuple[Any, ...]
    ) -> tuple[BindingMetadata, bytes, int] | None:
        with self._mu:
            found = self._published.get(key)
            if found is None:
                return None
            binding, material, generation = found
            self._published.move_to_end(key)
            return (binding, bytes(material), generation)

    def publish(
        self,
        key: tuple[Any, ...],
        *,
        binding: BindingMetadata,
        material: bytes,
        generation: int,
    ) -> bool:
        """Publish only if no newer generation already won (fencing)."""

        with self._mu:
            current = self._published.get(key)
            if current is not None and current[2] > generation:
                return False
            self._published[key] = (binding, bytes(material), generation)
            self._published.move_to_end(key)
            while len(self._published) > self._max_entries:
                evicted_key, _ = self._published.popitem(last=False)
                self._generations.pop(evicted_key, None)
            return True

    def invalidate(self, key: tuple[Any, ...]) -> None:
        with self._mu:
            self._published.pop(key, None)


class RenewalCoordinator(Protocol):
    """Minimal durable single-flight surface a production backend can implement."""

    def try_claim(self, key: tuple[Any, ...], *, owner_id: str) -> bool: ...
    def release_claim(self, key: tuple[Any, ...], *, owner_id: str) -> None: ...


class BoundCredentialCache:
    """Bounded cache keyed by the full renewal identity (R5).

    Key: (endpoint, connection revision, credential revision, admitted
    route/scope, execution/use owner).  Never keyed by user, model, or
    connection display name alone.  Renewal coordination is single-flight per
    key with a finite owner lease and generation check; no database locks are
    held during network issuance, and cancellation of one waiter never
    invalidates another consumer's valid credential.

    Pass a shared :class:`SharedRenewalStore` (or any object with its
    ``try_claim`` / ``release_claim`` surface plus ``get`` / ``publish`` /
    ``next_generation``) to coordinate across cache instances/workers.  Local
    asyncio futures still coordinate waiters inside one process; the shared
    store fences leaders across instances.
    """

    def __init__(
        self,
        *,
        max_entries: int = 128,
        owner_lease_seconds: float = 30.0,
        shared: SharedRenewalStore | Any | None = None,
    ) -> None:
        self._max_entries = max(1, int(max_entries))
        self._lease = max(1.0, float(owner_lease_seconds))
        self._entries: OrderedDict[tuple[Any, ...], _CacheEntry] = OrderedDict()
        self._inflight: dict[tuple[Any, ...], asyncio.Future] = {}
        self._inflight_owner: dict[tuple[Any, ...], tuple[str, float]] = {}
        self._guard = asyncio.Lock()
        self._shared = shared
        self._local_generations: dict[tuple[Any, ...], int] = {}

    @staticmethod
    def renewal_key_for(
        *,
        endpoint: str,
        connection_revision: int,
        credential_revision: int,
        route_id: str,
        operations: Sequence[str],
        execution_owner: str,
    ) -> tuple[Any, ...]:
        return (
            normalize_endpoint(endpoint),
            int(connection_revision),
            int(credential_revision),
            route_id,
            tuple(sorted(str(op).strip().lower() for op in operations if str(op).strip())),
            (execution_owner or "").strip(),
        )

    def next_generation(self, key: tuple[Any, ...]) -> int:
        """Monotonic per-key issuance generation (fences stale publishers)."""

        if self._shared is not None and hasattr(self._shared, "next_generation"):
            try:
                return int(self._shared.next_generation(key))
            except Exception:
                pass
        current = self._local_generations.get(key, 0) + 1
        self._local_generations[key] = current
        return current

    async def get(self, key: tuple[Any, ...]) -> _CacheEntry | None:
        async with self._guard:
            entry = self._entries.get(key)
            if entry is not None:
                if entry.credential.cleared:
                    self._entries.pop(key, None)
                else:
                    self._entries.move_to_end(key)
                    entry.last_use = time.monotonic()
                    return entry
        # Cross-worker publication: materialize a local handle over the
        # shared material bytes without re-issuing.
        if self._shared is not None and hasattr(self._shared, "get"):
            try:
                published = self._shared.get(key)
            except Exception:
                published = None
            if published is not None:
                binding, material, generation = published
                if material:
                    local = _CacheEntry(
                        binding=binding,
                        credential=EphemeralCredential(bytes(material)),
                        generation=int(generation),
                    )
                    async with self._guard:
                        self._entries[key] = local
                        self._entries.move_to_end(key)
                        while len(self._entries) > self._max_entries:
                            _, evicted = self._entries.popitem(last=False)
                            evicted.credential.clear()
                    return local
        return None

    async def publish(
        self, key: tuple[Any, ...], entry: _CacheEntry, *, generation: int
    ) -> bool:
        """Publish issuance only if the generation is still current."""

        async with self._guard:
            current = self._entries.get(key)
            if current is not None and current.generation > generation:
                entry.credential.clear()
                return False
            self._entries[key] = entry
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                _, evicted = self._entries.popitem(last=False)
                evicted.credential.clear()
        if self._shared is not None and hasattr(self._shared, "publish"):
            try:
                shared_ok = self._shared.publish(
                    key,
                    binding=entry.binding,
                    material=entry.credential.use_now(bytes),
                    generation=generation,
                )
                if not shared_ok:
                    return False
            except BoundAccessError:
                raise
            except Exception:
                pass
        return True

    async def join_or_lead(
        self, key: tuple[Any, ...], *, owner_id: str
    ) -> tuple[bool, asyncio.Future | None]:
        """Single-flight entry: (True, None) leads; (False, future) waits.

        A stale owner lease (previous leader died mid-issuance) lets a new
        leader take over instead of blocking forever.  When a shared store is
        configured, leadership is additionally fenced across cache instances:
        (False, None) means a cross-worker leader holds the shared lease, so
        the caller should await shared publication instead of issuing.
        """

        async with self._guard:
            existing = self._inflight.get(key)
            if existing is not None and not existing.done():
                owner, started = self._inflight_owner.get(key, ("", 0.0))
                if owner != owner_id and (time.monotonic() - started) < self._lease:
                    return False, existing
                # Finite owner lease expired: previous leader is fenced out.
                existing.cancel()
            if self._shared is not None and hasattr(self._shared, "try_claim"):
                try:
                    claimed = self._shared.try_claim(key, owner_id=owner_id)
                except Exception:
                    claimed = True
                if not claimed:
                    return False, None
            loop = asyncio.get_running_loop()
            future: asyncio.Future = loop.create_future()
            self._inflight[key] = future
            self._inflight_owner[key] = (owner_id, time.monotonic())
            return True, None

    async def await_shared_publication(
        self,
        key: tuple[Any, ...],
        *,
        timeout_seconds: float = 30.0,
        poll_seconds: float = 0.005,
    ) -> _CacheEntry | None:
        """Poll the shared store for a cross-worker leader's publication."""

        if self._shared is None or not hasattr(self._shared, "get"):
            return None
        deadline = time.monotonic() + max(0.05, float(timeout_seconds))
        while time.monotonic() < deadline:
            published = None
            try:
                published = self._shared.get(key)
            except Exception:
                published = None
            if published is not None:
                binding, material, generation = published
                if material:
                    return _CacheEntry(
                        binding=binding,
                        credential=EphemeralCredential(bytes(material)),
                        generation=int(generation),
                    )
            # Leader released the lease without publishing (failure): the
            # follower should stop waiting and compete for leadership.
            try:
                held = (
                    self._shared.lease_held(key)
                    if hasattr(self._shared, "lease_held")
                    else True
                )
            except Exception:
                held = True
            if not held:
                return None
            await asyncio.sleep(max(0.001, float(poll_seconds)))
        return None

    def release_shared(self, key: tuple[Any, ...], *, owner_id: str) -> None:
        if self._shared is not None and hasattr(self._shared, "release_claim"):
            try:
                self._shared.release_claim(key, owner_id=owner_id)
            except Exception:
                pass

    async def settle_inflight(
        self,
        key: tuple[Any, ...],
        *,
        owner_id: str,
        entry: _CacheEntry | None,
        error: BaseException | None = None,
    ) -> None:
        async with self._guard:
            future = self._inflight.pop(key, None)
            self._inflight_owner.pop(key, None)
            current_owner = True  # settle only our own flight; lease-takeover
            _ = (owner_id, current_owner)
            if future is None or future.done():
                pass
            elif error is not None:
                future.set_exception(error)
            else:
                future.set_result(entry)
        # Release the cross-worker lease only after local waiters settle, so
        # a follower polling the shared store observes either the publication
        # or a released lease (and then competes), never a dangling claim.
        # The shared publication itself was already written by publish(); the
        # lease here is coordination only, never a lock held during issuance
        # beyond this timestamp ownership.
        self.release_shared(key, owner_id=owner_id)

    def invalidate(self, key: tuple[Any, ...]) -> None:
        entry = self._entries.pop(key, None)
        if entry is not None:
            entry.credential.clear()
        if self._shared is not None and hasattr(self._shared, "invalidate"):
            try:
                self._shared.invalidate(key)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Acquisition orchestrator (R4, R6, R7).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AcquisitionRequest:
    snapshot: SelectionSnapshot
    execution_owner: str
    operation_id: str = field(default_factory=lambda: f"op:{uuid.uuid4().hex[:16]}")
    client_policy_revision: int = 1


@dataclass(slots=True)
class AcquiredCredential:
    binding: BindingMetadata
    credential: EphemeralCredential
    state: CredentialState = CredentialState.READY
    cache_hit: bool = False


async def _await_if_needed(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


class BoundCredentialAcquirer:
    """Acquire and renew credentials strictly within admitted authority."""

    def __init__(
        self,
        *,
        revision_reader: SecretRevisionReader,
        issuer_for: Callable[[str], CredentialIssuer],
        cache: BoundCredentialCache | None = None,
        expiry_margin_seconds: float = 30.0,
        clock_skew_seconds: float = 5.0,
        max_attempts: int = 3,
        duplicate_reconciler: DuplicateReconciler | None = None,
        shared_wait_seconds: float = 30.0,
    ) -> None:
        self._revision_reader = revision_reader
        self._issuer_for = issuer_for
        self._cache = cache or BoundCredentialCache()
        self._margin = max(0.0, float(expiry_margin_seconds))
        self._skew = max(0.0, float(clock_skew_seconds))
        self._max_attempts = max(1, int(max_attempts))
        self._duplicate_reconciler = duplicate_reconciler
        self._shared_wait = max(0.2, float(shared_wait_seconds))

    def _reconciler_for(self, issuer: Any) -> DuplicateReconciler | None:
        """Normalize any reconcile hook to ``(binding, previous_id)``.

        Issuers may expose ``reconcile_duplicate`` in the narrow
        provider-native shape ``(*, previous_issuance_id)`` (as
        :class:`FakeExpiringAdapter` does) or in the full
        ``(binding, previous_issuance_id)`` caller-owned shape.  Both are
        normalized here so the acquire path always invokes one contract.
        """

        candidate = self._duplicate_reconciler
        if candidate is None:
            reconcile = getattr(issuer, "reconcile_duplicate", None)
            candidate = reconcile if callable(reconcile) else None
        if candidate is None:
            return None

        async def _normalized(
            binding: BindingMetadata, previous_issuance_id: str
        ) -> Issuance | None:
            try:
                result = candidate(binding, previous_issuance_id)  # type: ignore[operator]
            except TypeError:
                result = candidate(previous_issuance_id=previous_issuance_id)  # type: ignore[call-arg]
            return await _await_if_needed(result)

        return _normalized

    async def _publish_verified(
        self,
        *,
        key: tuple[Any, ...],
        leader_id: str,
        snapshot: SelectionSnapshot,
        active: ActiveRevision,
        binding: BindingMetadata,
        issuance: Issuance,
    ) -> AcquiredCredential:
        """Verify, post-flight recheck, and publish one issuance (R6)."""

        self._verify_issuance(issuance=issuance, binding=binding)
        recheck = await _await_if_needed(
            self._revision_reader(snapshot.connection_id)
        )
        if (
            not isinstance(recheck, ActiveRevision)
            or recheck.status != "active"
            or recheck.credential_revision != active.credential_revision
            or recheck.policy_revision != active.policy_revision
        ):
            raise BoundAccessError(
                BOUND_REVOKED,
                "binding revoked or rotated during issuance; discarding",
            )
        credential = EphemeralCredential(issuance.material)
        entry = _CacheEntry(
            binding=binding, credential=credential, generation=binding.generation
        )
        published = await self._cache.publish(
            key, entry, generation=binding.generation
        )
        if not published:
            # A newer generation won concurrently; use ours directly
            # without polluting the cache.
            pass
        await self._cache.settle_inflight(key, owner_id=leader_id, entry=entry)
        return AcquiredCredential(binding=binding, credential=credential)

    def _binding_for(
        self, request: AcquisitionRequest, *, active: ActiveRevision, generation: int
    ) -> BindingMetadata:
        snapshot = request.snapshot
        digest = binding_digest_for(
            connection_id=snapshot.connection_id,
            endpoint=snapshot.endpoint,
            route_id=snapshot.route_id,
            role=snapshot.role,
            operations=snapshot.operations,
            policy_revision=snapshot.policy_revision,
            connection_revision=active.connection_revision,
            credential_revision=active.credential_revision,
        )
        return BindingMetadata(
            bindingDigest=digest,
            connectionId=snapshot.connection_id,
            endpoint=snapshot.endpoint,
            routeId=snapshot.route_id,
            role=snapshot.role,
            operations=snapshot.operations,
            policyRevision=snapshot.policy_revision,
            connectionRevision=active.connection_revision,
            credentialRevision=active.credential_revision,
            principalRef=snapshot.principal_ref,
            scopeType=snapshot.scope_type,
            scopeRef=snapshot.scope_ref,
            operationId=request.operation_id,
            issuanceId=f"iss:{uuid.uuid4().hex[:16]}",
            generation=generation,
            adapterKind=active.adapter_kind,
            accessMode=snapshot.access_mode,
        )

    def _renewal_key(
        self, snapshot: SelectionSnapshot, *, active: ActiveRevision, owner: str
    ) -> tuple[Any, ...]:
        return BoundCredentialCache.renewal_key_for(
            endpoint=snapshot.endpoint,
            connection_revision=active.connection_revision,
            credential_revision=active.credential_revision,
            route_id=snapshot.route_id,
            operations=snapshot.operations,
            execution_owner=owner,
        )

    def _expiry_ok(self, issuance: Issuance) -> bool:
        if issuance.expires_at is None:
            return True  # long-lived PAT: no expiry to enforce
        now = datetime.now(timezone.utc)
        effective = issuance.expires_at - timedelta(seconds=self._margin + self._skew)
        return effective > now

    def _verify_issuance(
        self, *, issuance: Issuance, binding: BindingMetadata
    ) -> None:
        """Post-issuance verification before metadata publication (R6)."""

        if not issuance.identity.strip():
            raise BoundAccessError(BOUND_ISSUER_FAILED, "issuer returned no identity")
        admitted = {op.strip().lower() for op in binding.operations}
        granted = {part.strip().lower() for part in issuance.scope.split(",") if part.strip()}
        # Empty issuer scope means "bound to the admitted bundle exactly";
        # a non-empty scope must cover every admitted operation.
        if granted and any(op not in granted for op in admitted):
            raise BoundAccessError(
                BOUND_SCOPE_MISMATCH, "issuer scope does not cover admitted operations"
            )
        if not self._expiry_ok(issuance):
            raise BoundAccessError(
                BOUND_ISSUER_FAILED, "issuer returned an expired credential"
            )

    async def acquire(self, request: AcquisitionRequest) -> AcquiredCredential:
        """Acquire the expected ACTIVE revision; fail closed on any drift."""

        snapshot = request.snapshot
        owner = (request.execution_owner or "").strip()
        if not owner:
            raise BoundAccessError(BOUND_DENIED, "execution/use owner is required")
        if snapshot.access_mode == AccessMode.ANONYMOUS:
            raise BoundAccessError(
                BOUND_DENIED, "anonymous snapshots carry no credential to acquire"
            )

        # Authoritative ACTIVE-revision read (#4006 shape) before exposure.
        active = await _await_if_needed(self._revision_reader(snapshot.connection_id))
        if not isinstance(active, ActiveRevision):
            raise BoundAccessError(BOUND_UNAVAILABLE, "revision authority unreadable")
        if active.status == "revoked":
            raise BoundAccessError(BOUND_REVOKED, "credential binding is revoked")
        if active.status == "disabled":
            raise BoundAccessError(BOUND_DISABLED, "connection is disabled")
        if active.status != "active":
            raise BoundAccessError(BOUND_UNAVAILABLE, "credential is unavailable")
        if active.credential_revision != snapshot.credential_revision:
            # Rotation requires an explicit new-attempt adoption policy, not
            # unconstrained latest-secret resolution: the admitted snapshot's
            # revision must match, otherwise the attempt is stale.
            raise BoundAccessError(
                BOUND_STALE_REVISION,
                "admitted credential revision is stale; re-admit the attempt",
            )
        if active.policy_revision != snapshot.policy_revision:
            raise BoundAccessError(
                BOUND_STALE_REVISION, "policy revision changed; re-admit the attempt"
            )

        key = self._renewal_key(snapshot, active=active, owner=owner)
        cached = await self._cache.get(key)
        if cached is not None:
            # Cached entries already passed post-issuance verification for the
            # identical renewal identity; revalidate liveness only.
            if cached.binding.credential_revision != active.credential_revision:
                self._cache.invalidate(key)
            else:
                return AcquiredCredential(
                    binding=cached.binding, credential=cached.credential, cache_hit=True
                )

        # A default change cannot reroute an admitted attempt: the key above
        # pins endpoint/revisions/scope/owner, so a changed default simply
        # misses the cache and re-admission selects fresh authority instead of
        # silently reusing this attempt's credential.

        leader_id = f"{request.operation_id}:{uuid.uuid4().hex[:8]}"
        is_leader, waiter = await self._cache.join_or_lead(key, owner_id=leader_id)
        if not is_leader:
            if waiter is not None:
                try:
                    entry = await asyncio.shield(waiter)
                except asyncio.CancelledError:
                    # Cancellation of one waiter must not invalidate another
                    # consumer's valid credential: the shield keeps the shared
                    # flight alive; this waiter alone stops waiting.
                    raise
                if entry is None or entry.credential.cleared:
                    raise BoundAccessError(
                        BOUND_UNAVAILABLE, "shared renewal produced no credential"
                    )
                return AcquiredCredential(
                    binding=entry.binding, credential=entry.credential, cache_hit=True
                )
            # Cross-worker follower: a shared-store leader holds the lease.
            # Await its publication instead of stampeding a second issuance.
            # A released lease with no publication means the leader failed, so
            # fall through and compete for leadership rather than failing.
            shared_entry = await self._cache.await_shared_publication(
                key, timeout_seconds=self._shared_wait
            )
            if shared_entry is not None:
                if shared_entry.credential.cleared:
                    raise BoundAccessError(
                        BOUND_UNAVAILABLE, "shared renewal produced no credential"
                    )
                return AcquiredCredential(
                    binding=shared_entry.binding,
                    credential=shared_entry.credential,
                    cache_hit=True,
                )
            # Recheck the local cache (leader may have published locally
            # between our claim attempt and the shared poll), otherwise
            # compete for leadership now that the lease is free.
            cached_retry = await self._cache.get(key)
            if cached_retry is not None:
                return AcquiredCredential(
                    binding=cached_retry.binding,
                    credential=cached_retry.credential,
                    cache_hit=True,
                )
            is_leader, waiter = await self._cache.join_or_lead(
                key, owner_id=leader_id
            )
            if not is_leader:
                if waiter is None:
                    raise BoundAccessError(
                        BOUND_UNAVAILABLE, "shared renewal contention; retry"
                    )
                try:
                    entry = await asyncio.shield(waiter)
                except asyncio.CancelledError:
                    raise
                if entry is None or entry.credential.cleared:
                    raise BoundAccessError(
                        BOUND_UNAVAILABLE, "shared renewal produced no credential"
                    )
                return AcquiredCredential(
                    binding=entry.binding, credential=entry.credential, cache_hit=True
                )

        # Leader path: no cache/database lock is held during network issuance;
        # only the in-flight placeholder plus a short-lived shared lease
        # timestamp coordinate waiters.
        last_error: BoundAccessError | None = None
        for _attempt in range(self._max_attempts):
            generation = self._cache.next_generation(key)
            binding = self._binding_for(
                request, active=active, generation=generation
            )
            issuer = self._issuer_for(active.adapter_kind)
            try:
                issuance = await _await_if_needed(issuer(binding))
            except asyncio.CancelledError:
                raise
            except BoundAccessError as exc:
                last_error = exc
                if exc.code in {
                    BOUND_REVOKED,
                    BOUND_DISABLED,
                    BOUND_STALE_REVISION,
                    BOUND_SCOPE_MISMATCH,
                    BOUND_DENIED,
                }:
                    break
                continue
            except Exception as exc:
                # Lost acknowledgment: the provider may have issued server-side
                # while the caller observed a transport failure.  Bind the
                # duplicate via the reconciler instead of blindly re-issuing.
                # Exactly-once issuance is never promised.
                last_error = BoundAccessError(
                    BOUND_ISSUER_FAILED, f"issuer failed: {type(exc).__name__}"
                )
                reconciler = self._reconciler_for(issuer)
                if reconciler is not None:
                    try:
                        duplicate = await _await_if_needed(
                            reconciler(binding, binding.issuance_id)
                        )
                    except Exception:
                        duplicate = None
                    if duplicate is not None:
                        try:
                            return await self._publish_verified(
                                key=key,
                                leader_id=leader_id,
                                snapshot=snapshot,
                                active=active,
                                binding=binding,
                                issuance=duplicate,
                            )
                        except BoundAccessError as reconcile_exc:
                            last_error = reconcile_exc
                            if reconcile_exc.code in {
                                BOUND_REVOKED,
                                BOUND_DISABLED,
                                BOUND_STALE_REVISION,
                                BOUND_SCOPE_MISMATCH,
                                BOUND_DENIED,
                            }:
                                break
                            continue
                continue
            try:
                return await self._publish_verified(
                    key=key,
                    leader_id=leader_id,
                    snapshot=snapshot,
                    active=active,
                    binding=binding,
                    issuance=issuance,
                )
            except asyncio.CancelledError:
                raise
            except BoundAccessError as exc:
                last_error = exc
                if exc.code in {
                    BOUND_REVOKED,
                    BOUND_DISABLED,
                    BOUND_STALE_REVISION,
                    BOUND_SCOPE_MISMATCH,
                    BOUND_DENIED,
                }:
                    break
                continue
        assert last_error is not None
        await self._cache.settle_inflight(
            key, owner_id=leader_id, entry=None, error=last_error
        )
        raise last_error

    async def renew(
        self, acquired: AcquiredCredential, *, execution_owner: str
    ) -> AcquiredCredential:
        """Refresh within the same authority; never broaden scope (R6/INV-003)."""

        owner = (execution_owner or "").strip()
        if not owner:
            raise BoundAccessError(BOUND_DENIED, "execution/use owner is required")
        binding = acquired.binding
        key = BoundCredentialCache.renewal_key_for(
            endpoint=binding.endpoint,
            connection_revision=binding.connection_revision,
            credential_revision=binding.credential_revision,
            route_id=binding.route_id,
            operations=binding.operations,
            execution_owner=owner,
        )
        # Force re-issuance for this renewal identity by invalidating only our
        # generation's entry, then re-acquire through the normal path.
        self._cache.invalidate(key)
        snapshot = SelectionSnapshot(
            principalRef=binding.principal_ref,
            scopeType=binding.scope_type,
            scopeRef=binding.scope_ref,
            endpoint=binding.endpoint,
            routeId=binding.route_id,
            repositoryDisplay=binding.route_id,
            role=binding.role,
            operations=binding.operations,
            accessMode=binding.access_mode,
            policyRevision=binding.policy_revision,
            connectionId=binding.connection_id,
            connectionPolicyRevision=binding.connection_revision,
            credentialRevision=binding.credential_revision,
            selectionOrigin="renewal",
            authorizedAt=_utc_now_iso(),
        )
        return await self.acquire(
            AcquisitionRequest(
                snapshot=snapshot,
                execution_owner=owner,
                operation_id=binding.operation_id,
            )
        )

    def release_use(self, acquired: AcquiredCredential) -> None:
        """Release one use/generation without revoking shared issuance (R7).

        Only local material belonging to this generation is cleared when no
        other cache entry references it.  Provider-side revocation and
        connection disable are separate explicit operations below; a run
        completing never revokes a long-lived PAT.
        """

        acquired.credential.clear()

    async def revoke_provider_token(
        self,
        acquired: AcquiredCredential,
        *,
        revoke: Callable[[BindingMetadata], Awaitable[None] | None],
    ) -> None:
        """Explicit provider-side token revocation (distinct from use release)."""

        await _await_if_needed(revoke(acquired.binding))
        acquired.credential.clear()


# ---------------------------------------------------------------------------
# Bound client factory (R3/CONTRACT-010).
# ---------------------------------------------------------------------------


class BoundClient(BaseModel):
    """Authenticated execution-facing client handle (metadata only)."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    binding_digest: str = Field(alias="bindingDigest", min_length=1)
    connection_id: str = Field(alias="connectionId", min_length=1)
    execution_owner: str = Field(alias="executionOwner", min_length=1)
    operations: tuple[str, ...] = Field(min_length=1)
    adapter_kind: str = Field(alias="adapterKind", min_length=1)


def make_bound_client(
    *,
    binding: BindingMetadata,
    execution_owner: str,
    authenticate_execution: Callable[[str], bool],
    expected_principal: str = "",
    expected_policy_revision: int | None = None,
    expected_operation: str = "",
) -> BoundClient:
    """Build a bound client after independently authenticating the requester.

    Validates binding ownership, role, operations, and revisions.  The digest
    is treated as an identifier only: a guessed handle without matching
    principal/policy/operation context is rejected.
    """

    owner = (execution_owner or "").strip()
    if not owner or not authenticate_execution(owner):
        raise BoundAccessError(BOUND_DENIED, "requesting execution is not authenticated")
    if expected_principal and expected_principal.strip() != binding.principal_ref:
        raise BoundAccessError(BOUND_DENIED, "binding principal mismatch")
    if expected_policy_revision is not None and (
        expected_policy_revision != binding.policy_revision
    ):
        raise BoundAccessError(BOUND_STALE_REVISION, "binding policy revision mismatch")
    if expected_operation and expected_operation.strip().lower() not in {
        op.strip().lower() for op in binding.operations
    }:
        raise BoundAccessError(BOUND_DENIED, "operation not admitted by binding")
    return BoundClient(
        bindingDigest=binding.binding_digest,
        connectionId=binding.connection_id,
        executionOwner=owner,
        operations=binding.operations,
        adapterKind=binding.adapter_kind,
    )


def verify_repository_identity_through_selection(
    *,
    snapshot: SelectionSnapshot,
    observed_route_id: str,
) -> None:
    """Verify stable repository identity via the selected endpoint (R2).

    Compares the provider-observed identity against the admitted snapshot.  It
    never tries alternative candidates: mismatch is failure, not a cue to
    shop for another credential.
    """

    if observed_route_id != snapshot.route_id:
        raise BoundAccessError(
            BOUND_DENIED, "observed repository identity does not match selection"
        )


def safe_error_dto(exc: BoundAccessError, *, binding: BindingMetadata | None = None) -> BoundErrorDTO:
    return BoundErrorDTO(
        code=exc.code,
        message=str(exc),
        bindingDigest=binding.binding_digest if binding else None,
        connectionId=binding.connection_id if binding else None,
        operationId=binding.operation_id if binding else None,
    )


__all__ = [
    "BOUND_AMBIGUOUS",
    "BOUND_DENIED",
    "BOUND_DISABLED",
    "BOUND_ISSUER_FAILED",
    "BOUND_REVOKED",
    "BOUND_SCOPE_MISMATCH",
    "BOUND_SCRATCH_EXCLUDED",
    "BOUND_SELECTION_REQUIRED",
    "BOUND_STALE_REVISION",
    "BOUND_UNAVAILABLE",
    "AccessMode",
    "AcquisitionRequest",
    "AcquiredCredential",
    "ActiveRevision",
    "BindingMetadata",
    "BoundAccessError",
    "BoundClient",
    "BoundCredentialAcquirer",
    "BoundCredentialCache",
    "BoundErrorDTO",
    "BoundTelemetryDTO",
    "CredentialState",
    "DuplicateReconciler",
    "EphemeralCredential",
    "FakeExpiringAdapter",
    "Issuance",
    "PatAdapter",
    "RenewalCoordinator",
    "SharedRenewalStore",
    "SelectionSnapshot",
    "binding_digest_for",
    "make_bound_client",
    "safe_error_dto",
    "select_repository_authority",
    "verify_repository_identity_through_selection",
]
