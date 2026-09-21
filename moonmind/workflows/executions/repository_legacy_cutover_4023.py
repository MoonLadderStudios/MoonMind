"""One recoverable legacy-credential cutover (MoonLadderStudios/MoonMind#4023).

New authenticated work uses its admitted repository connection, never a
search through unrelated available credentials. This module is the single
#4023 coordination seam: it maps proven legacy intent through the existing
connection/secret lifecycle and the existing versioned persistence owners.

Explicitly out of scope (per the issue):

* No new credential resolver, caller registry, upgrade coordinator,
  release fleet, or permanent startup qualification system.
* No token probing against GitHub to choose a winner, no merging of
  distinct accounts because values match, no empty/unknown-allowlist
  wildcard.
* No new migration ledger/lease and no post-migration census of deleted
  sources at ordinary startup.
* No token or token-derived fingerprints in diagnostics.

Reuse:

* Selection reuses ``admit_scoped_route`` (same contract domain, #4005).
* Persistence reuses the ``api_service.services.repository_connections``
  transactional writer contract (expected-revision conflicts); this module
  provides the small idempotent in-memory mapping store used by unit scope
  and the request-identity reconciliation rule production wiring follows.
* Historical decoding reuses the frozen
  ``decode_legacy_repository_history_v1`` shape; saved-target mapping
  preserves identity, typed intent, publication semantics, and recorded
  bytes/digests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from moonmind.workflows.executions.repository_contract import (
    DEFAULT_GIT_CONNECTION_REF,
    REPOSITORY_DENIED,
    REPOSITORY_POLICY_CONFLICT,
    REPOSITORY_ROUTE_CONFLICT,
    REPOSITORY_SETUP_REQUIRED,
    RepositoryIdentity,
    RepositoryRouteError,
    ScopedRouteCandidate,
    admit_scoped_route,
)

LEGACY_CUTOVER_REQUEST_VERSION = "moonmind.repository-legacy-cutover-4023.v1"

#: Narrow historical readers survive only until their recorded histories are
#: migrated or expire; they never grant new-write authority.
LEGACY_HISTORICAL_READER_REMOVAL_CONDITION = (
    "historical readers persist only for recorded histories predating the "
    "#4023 cutover and are removed once those histories are migrated or expire"
)

#: Sentinel marking persisted input that exists but cannot be decoded with
#: trustworthy provenance. Distinct from absent (never configured).
UNREADABLE_SENTINEL = "unreadable"

EffectiveLegacyStatus = Literal[
    "absent", "unreadable", "explicit", "proven_default", "suspended"
]


@dataclass(frozen=True)
class EffectiveLegacyReference:
    """Effective credential reference from trustworthy configuration."""

    status: EffectiveLegacyStatus
    connection_ref: str | None = None
    affected_operation: str | None = None
    correction: str | None = None

    @property
    def safe_summary(self) -> str:
        ref = self.connection_ref or "no connection"
        if self.status == "suspended":
            action = self.affected_operation or "the affected operation"
            return (
                f"Legacy repository reference suspended for {action} "
                f"({ref}); {self.correction or 'declare one connection explicitly.'}"
            )
        return f"Legacy repository reference {self.status} ({ref})."


def _clean_ref(value: str | None) -> str:
    return str(value or "").strip()


def determine_effective_legacy_reference(
    *,
    explicit_ref: str | None,
    historical_ref: str | None,
    proven_identity: str | None,
    affected_operation: str = "repository.write",
) -> EffectiveLegacyReference:
    """Determine the effective legacy connection reference.

    Rules (no network, no token comparison):

    * Blank explicit input with no historical input is ``absent``.
    * The ``UNREADABLE_SENTINEL`` (or whitespace-only explicit input paired
      with it) is ``unreadable`` -- distinct from absent.
    * Two distinct non-empty references conflict: suspend only the affected
      operation with a concrete correction.
    * ``git-default`` survives only as a proven effective legacy identity;
      an unproven historical ``git-default`` suspends instead of acting as
      an always-on resolver alias.
    """

    explicit = _clean_ref(explicit_ref)
    historical = _clean_ref(historical_ref)
    proven = _clean_ref(proven_identity)

    if historical == UNREADABLE_SENTINEL:
        return EffectiveLegacyReference(
            status="unreadable",
            affected_operation=affected_operation,
            correction=(
                "Persisted repository input is present but unreadable; "
                "re-declare the connection explicitly."
            ),
        )
    if not explicit and not historical:
        if explicit_ref is not None and not explicit:
            return EffectiveLegacyReference(
                status="unreadable",
                affected_operation=affected_operation,
                correction=(
                    "Persisted repository input is present but unreadable; "
                    "re-declare the connection explicitly."
                ),
            )
        return EffectiveLegacyReference(status="absent")
    if explicit and historical and explicit != historical:
        return EffectiveLegacyReference(
            status="suspended",
            connection_ref=explicit,
            affected_operation=affected_operation,
            correction=(
                f"Conflicting repository references {explicit!r} and "
                f"{historical!r}; declare one connection explicitly for "
                f"{affected_operation}."
            ),
        )
    winner = explicit or historical
    if winner == DEFAULT_GIT_CONNECTION_REF and proven != DEFAULT_GIT_CONNECTION_REF:
        return EffectiveLegacyReference(
            status="suspended",
            connection_ref=winner,
            affected_operation=affected_operation,
            correction=(
                "git-default is preserved only for a proven effective legacy "
                "identity; declare one connection explicitly."
            ),
        )
    if winner == DEFAULT_GIT_CONNECTION_REF:
        return EffectiveLegacyReference(status="proven_default", connection_ref=winner)
    return EffectiveLegacyReference(status="explicit", connection_ref=winner)


def require_explicit_allowlist_match(
    *, repository_name: str, allowed_repository_ids: Sequence[str]
) -> None:
    """Reject empty/unknown allowlists instead of treating them as wildcard."""

    allowed = [str(item).strip() for item in allowed_repository_ids if str(item).strip()]
    if not allowed:
        raise RepositoryRouteError(
            REPOSITORY_SETUP_REQUIRED,
            "no scoped assignments; scope setup required",
        )
    if str(repository_name or "").strip() not in allowed:
        raise RepositoryRouteError(
            REPOSITORY_DENIED,
            "repository is not allowed by connection",
        )


def fail_selected_backend_without_fallback(
    *, backend: str, operation: str, cause: str
) -> None:
    """Fail a selected SecretRef backend closed; never fall back.

    A selected environment SecretRef remains an explicit backend: its
    failure cannot trigger Settings or another-secret fallback, and it
    suspends only the affected operation.
    """

    raise RepositoryRouteError(
        REPOSITORY_DENIED,
        f"selected credential backend {str(backend).strip()} failed for "
        f"{str(operation).strip()}; no fallback applies: {str(cause).strip()}",
    )


def select_admitted_connection(
    *,
    identity: RepositoryIdentity,
    requested_operations: Sequence[str],
    candidates: Sequence[ScopedRouteCandidate],
    principal_ref: str,
    principal_scope: tuple[str, str | None],
) -> ScopedRouteCandidate:
    """Select one admitted route without ambient fallback.

    Zero assignments authorize nothing (setup-required, never wildcard);
    multiple satisfying connections stay ambiguous (declare explicitly).
    The selected route's failure must surface to the caller; callers must
    never substitute another connection after the selected route fails.
    """

    return admit_scoped_route(
        identity=identity,
        requested_operations=requested_operations,
        candidates=candidates,
        principal_ref=principal_ref,
        principal_scope=principal_scope,
    )


def scratch_or_anonymous_usable(*, mode: str) -> bool:
    """Scratch and explicit anonymous work never require GitHub credentials."""

    return str(mode or "").strip().lower() in {"scratch", "anonymous"}


@dataclass
class CutoverMappingStore:
    """Idempotent in-memory #4023 mapping with expected-revision protection.

    Production wiring applies the same rule through
    ``RepositoryConnectionService`` transactions: one database transaction
    per mapping together with its metadata-only audit record, with
    uniqueness/revision violations surfacing as conflicts. Lost commit
    acknowledgment reconciles by stable ``request_id`` before repeating
    effects. Ordinary post-migration startup consults only the completed
    marker, never a census of deleted sources, and depends on no new
    ledger/lease.
    """

    _mappings: dict[str, dict[str, Any]] = field(default_factory=dict)
    _connection_revisions: dict[str, int] = field(default_factory=dict)
    completed: bool = False

    @property
    def mapping_count(self) -> int:
        return len(self._mappings)

    def apply_mapping(
        self,
        *,
        request_id: str,
        connection_id: str,
        expected_policy_revision: int,
    ) -> dict[str, Any]:
        rid = str(request_id or "").strip()
        cid = str(connection_id or "").strip()
        if not rid or not cid:
            raise RepositoryRouteError(
                REPOSITORY_SETUP_REQUIRED, "stable request identity required"
            )
        if int(expected_policy_revision) < 1:
            raise RepositoryRouteError(
                REPOSITORY_SETUP_REQUIRED, "invalid revision"
            )
        existing = self._mappings.get(rid)
        if existing is not None:
            if existing["connection_id"] != cid:
                raise RepositoryRouteError(
                    REPOSITORY_ROUTE_CONFLICT,
                    "duplicate request identity maps a different connection",
                )
            return dict(existing)
        known_revision = self._connection_revisions.get(cid)
        if known_revision is not None and known_revision != int(expected_policy_revision):
            raise RepositoryRouteError(
                REPOSITORY_POLICY_CONFLICT,
                "concurrent configuration edit detected; refresh and retry",
            )
        record = {
            "request_version": LEGACY_CUTOVER_REQUEST_VERSION,
            "request_id": rid,
            "connection_id": cid,
            "expected_policy_revision": int(expected_policy_revision),
        }
        self._mappings[rid] = record
        self._connection_revisions[cid] = int(expected_policy_revision)
        return dict(record)

    def reconcile_lost_ack(
        self, *, request_id: str, connection_id: str
    ) -> dict[str, Any]:
        """Reconcile a lost commit acknowledgment before repeating effects."""

        rid = str(request_id or "").strip()
        existing = self._mappings.get(rid)
        if existing is not None:
            if existing["connection_id"] != str(connection_id or "").strip():
                raise RepositoryRouteError(
                    REPOSITORY_ROUTE_CONFLICT,
                    "duplicate request identity maps a different connection",
                )
            return dict(existing)
        raise RepositoryRouteError(
            REPOSITORY_SETUP_REQUIRED,
            "no recorded mapping for request identity; declare explicitly",
        )

    def mark_completed(self) -> None:
        self.completed = True

    def startup_requires_no_census(self) -> bool:
        """Ordinary post-migration startup runs no census of deleted sources."""

        return True


def map_saved_target_to_connection(
    *,
    repository_name: str,
    branch_name: str,
    recorded_digest: str,
    connection_ref: str,
) -> dict[str, Any]:
    """Preserve saved draft/schedule identity with an explicit connection.

    Identity, typed intent (git provider), branch, and recorded historical
    bytes/digests pass through unchanged; only the connection binding
    becomes explicit. Recoverable provenance gaps are resolved from existing
    evidence (the recorded name/branch/digest) before requesting a real
    choice -- no mass reapproval or recreation.
    """

    name = str(repository_name or "").strip()
    branch = str(branch_name or "").strip() or "main"
    digest = str(recorded_digest or "").strip()
    ref = str(connection_ref or "").strip()
    if not name or not digest or not ref:
        raise RepositoryRouteError(
            REPOSITORY_SETUP_REQUIRED,
            "saved target needs repository, digest, and explicit connection",
        )
    return {
        "provider": "git",
        "connectionRef": ref,
        "repositoryName": name,
        "branchName": branch,
        "recordedDigest": digest,
    }


def _looks_like_token(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if lowered.startswith(("ghp_", "gho_", "github_pat_")):
        return True
    if "sha256:" in lowered:
        return True
    # Typed backend refs carry a provider/key shape ("managed:KEY",
    # "repository-connection:...") and are safe to render. A long
    # colon-free value is treated as potential secret material.
    if ":" in text:
        return False
    return len(text) >= 20


def cutover_diagnostic(*, action: str, connection_ref: str, backend_ref: str) -> str:
    """Render migration diagnostics with safe references only.

    Exposes the affected action plus safe connection/backend references;
    never tokens or token-derived fingerprints.
    """

    safe_connection = str(connection_ref or "").strip() or "unknown connection"
    backend = str(backend_ref or "").strip()
    if not backend or _looks_like_token(backend):
        safe_backend = "configured credential backend"
    else:
        safe_backend = backend
    return (
        f"cutover {str(action).strip() or 'repository operation'} uses "
        f"{safe_connection} via {safe_backend}."
    )


def is_worker_compatible(
    *,
    observed_sha256: str,
    pinned_sha256: str,
    observed_version: str,
    pinned_version: str,
    observed_bundle: str,
    pinned_bundle: str,
) -> bool:
    """Verify the actual changed interface, not a SHA/patch fingerprint.

    A SHA or patch difference alone is not an incompatible worker: only a
    changed tool bundle (the actual client interface) is incompatible here.
    Version pins beyond the bundle are advisory for this check.
    """

    _ = (observed_sha256, pinned_sha256, observed_version, pinned_version)
    return str(observed_bundle or "").strip() == str(pinned_bundle or "").strip()


__all__ = [
    "LEGACY_CUTOVER_REQUEST_VERSION",
    "LEGACY_HISTORICAL_READER_REMOVAL_CONDITION",
    "UNREADABLE_SENTINEL",
    "CutoverMappingStore",
    "EffectiveLegacyReference",
    "cutover_diagnostic",
    "determine_effective_legacy_reference",
    "fail_selected_backend_without_fallback",
    "is_worker_compatible",
    "map_saved_target_to_connection",
    "require_explicit_allowlist_match",
    "scratch_or_anonymous_usable",
    "select_admitted_connection",
]
