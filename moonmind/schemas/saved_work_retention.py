"""Artifact-owned access, dependency retention, and bounded recovery quotas.

Implements MoonLadderStudios/MoonMind#4017 (plan slice 4,
``docs/RepositoryAccessAndWorkspaceDesign.md`` ``QUALITY-005``,
``CONTRACT-012``, ``INV-006``, ``INV-007``, ``DOC-REQ-005``, ``TEST-003``).

Pure, DB-free contracts owned by the existing Temporal artifact lifecycle
owner (``moonmind.workflows.temporal.artifacts``). The service layer binds
these contracts to real API/service/database/object-store state; this module
never touches the database, the object store, or ambient credentials.

Rules enforced here:

- A manifest names a bounded required/optional dependency graph. Required
  references must resolve; cycles are rejected; a parent reference, content
  hash equality, source-connection provenance, or knowledge of a manifest ID
  never grants access on its own.
- Availability is evidence-based (available, incomplete, corrupt, expired,
  deleted, quarantined, locally-retained-but-unsaved), never inferred from a
  bare ``COMPLETE`` row.
- Signed-download TTLs are bounded by artifact/use/security policy and carry
  honest revocation limitations: disabling UI state does not retract an
  already-issued URL unless the serving boundary enforces it.
- Quotas distinguish shared physical bytes from per-owner logical charges so
  concurrent capture/use admission stays bounded without double-billing or
  unlimited logical references.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Mapping, Sequence

SAVED_WORK_RETENTION_CONTRACT_VERSION = "saved-work-retention/v1"

# Bounded graph walk: manifests stay small indexes, not unbounded traversals.
SAVED_WORK_MAX_DEPENDENCIES = 64
SAVED_WORK_MAX_GRAPH_DEPTH = 8

# Signed-download policy: short-lived links or the existing authenticated read
# path, never shared permanent URLs.
SAVED_WORK_MAX_DOWNLOAD_TTL_SECONDS = 15 * 60
SAVED_WORK_MIN_DOWNLOAD_TTL_SECONDS = 60

SAVED_WORK_DOWNLOAD_REVOCATION_LIMITATIONS = (
    "Disabling a button, hiding a link, or deleting a manifest row does not "
    "retract an already-issued signed URL. Revocation is effective only when "
    "the serving boundary re-checks authorization and expiry on every request "
    "(short TTL and/or the authenticated read path). Treat issued URLs as "
    "bearer tokens until they expire; keep them out of logs and history."
)

SavedWorkAvailability = Literal[
    "available",
    "incomplete",
    "corrupt",
    "expired",
    "deleted",
    "quarantined",
    "locally_retained_but_unsaved",
]

SAVED_WORK_OPERATION_KINDS: tuple[str, ...] = (
    "restore",
    "publication",
    "download",
)

# Default per-scope quotas. Scopes are admission-time owner handles
# (user/execution principals), never source credentials.
SAVED_WORK_DEFAULT_QUOTAS: dict[str, int] = {
    "max_logical_bytes_per_scope": 2 * 1024 * 1024 * 1024,
    "max_physical_bytes_per_scope": 4 * 1024 * 1024 * 1024,
    "max_live_use_claims_per_scope": 32,
}


def _artifact_id_text(value: Any) -> str:
    return str(value or "").strip()


def validate_saved_work_dependency_entries(
    dependencies: Sequence[Mapping[str, Any]] | Sequence[str] | None,
) -> list[dict[str, Any]]:
    """Normalize manifest dependency entries to bounded required/optional refs.

    Accepts the legacy flat ``list[str]`` form (all required) and the bounded
    ``{"artifactId": ..., "kind": "required"|"optional"}`` form. Anything else
    is rejected so an untyped manifest cannot smuggle an unbounded graph.
    """
    entries: list[dict[str, Any]] = []
    for item in dependencies or []:
        if isinstance(item, str):
            artifact_id = _artifact_id_text(item)
            if not artifact_id:
                raise ValueError("SAVED_WORK_DEP_BLANK: dependency id is blank")
            entries.append({"artifactId": artifact_id, "kind": "required"})
            continue
        if isinstance(item, Mapping):
            artifact_id = _artifact_id_text(item.get("artifactId") or item.get("id"))
            kind = str(item.get("kind") or "required").strip().lower()
            if not artifact_id:
                raise ValueError("SAVED_WORK_DEP_BLANK: dependency id is blank")
            if kind not in {"required", "optional"}:
                raise ValueError(
                    f"SAVED_WORK_DEP_KIND: unsupported dependency kind {kind!r}"
                )
            entries.append({"artifactId": artifact_id, "kind": kind})
            continue
        raise ValueError(
            "SAVED_WORK_DEP_SHAPE: dependency must be an id string or "
            "{artifactId, kind} mapping"
        )
    if len(entries) > SAVED_WORK_MAX_DEPENDENCIES:
        raise ValueError(
            "SAVED_WORK_DEP_BOUND: manifest names "
            f"{len(entries)} dependencies (max {SAVED_WORK_MAX_DEPENDENCIES})"
        )
    seen: set[str] = set()
    for entry in entries:
        if entry["artifactId"] in seen:
            raise ValueError(
                "SAVED_WORK_DEP_DUPLICATE: "
                f"duplicate dependency {entry['artifactId']!r}"
            )
        seen.add(entry["artifactId"])
    return entries


def validate_saved_work_dependency_graph(
    dependencies: Sequence[Mapping[str, Any]] | Sequence[str] | None,
    *,
    resolve_artifact: Callable[[str], Mapping[str, Any] | None],
    authorize_artifact: Callable[[str], bool],
    child_dependencies: Callable[[str], Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Validate references and authorization; reject cycles; record evidence.

    ``resolve_artifact`` returns immutable artifact evidence for an id (or
    ``None`` when unresolved). ``authorize_artifact`` reports whether the
    publishing principal may retain/read that artifact; provenance, digest
    equality, or manifest knowledge must not count as authorization by the
    caller. Optional dependencies may stay unresolved; required ones must not.

    Returns immutable reachability evidence: the sorted required/optional id
    lists plus a content-bound graph digest. Raises ``ValueError`` with a
    ``SAVED_WORK_DEP_*`` code otherwise.
    """
    entries = validate_saved_work_dependency_entries(dependencies)

    def _children(artifact_id: str) -> Sequence[str]:
        if child_dependencies is not None:
            return list(child_dependencies(artifact_id) or [])
        evidence = resolve_artifact(artifact_id) or {}
        raw = evidence.get("dependencies") or evidence.get("childIds") or []
        return [str(item) for item in list(raw)[:SAVED_WORK_MAX_DEPENDENCIES]]

    resolved: dict[str, dict[str, Any]] = {}
    for entry in entries:
        evidence = resolve_artifact(entry["artifactId"])
        if evidence is None:
            if entry["kind"] == "optional":
                continue
            raise ValueError(
                "SAVED_WORK_DEP_UNRESOLVED: required dependency "
                f"{entry['artifactId']!r} did not resolve"
            )
        if not authorize_artifact(entry["artifactId"]):
            raise ValueError(
                "SAVED_WORK_DEP_UNAUTHORIZED: publisher is not authorized "
                f"for dependency {entry['artifactId']!r}"
            )
        resolved[entry["artifactId"]] = dict(evidence)

    # Bounded DFS for unsafe cycles across the retained dependency closure.
    visiting: list[str] = []
    visited: set[str] = set()

    def _visit(node: str, depth: int) -> None:
        if depth > SAVED_WORK_MAX_GRAPH_DEPTH:
            raise ValueError(
                "SAVED_WORK_DEP_TOO_DEEP: dependency closure exceeds "
                f"{SAVED_WORK_MAX_GRAPH_DEPTH} levels at {node!r}"
            )
        if node in visiting:
            cycle = " -> ".join([*visiting, node])
            raise ValueError(f"SAVED_WORK_DEP_CYCLE: unsafe cycle {cycle}")
        if node in visited:
            return
        visiting.append(node)
        try:
            for child in _children(node):
                child_evidence = resolve_artifact(child)
                if child_evidence is None:
                    continue
                _visit(child, depth + 1)
        finally:
            visiting.pop()
        visited.add(node)

    for artifact_id in resolved:
        _visit(artifact_id, 0)

    required = sorted(
        entry["artifactId"] for entry in entries if entry["kind"] == "required"
    )
    optional = sorted(
        entry["artifactId"] for entry in entries if entry["kind"] == "optional"
    )
    graph_digest = "sha256:" + hashlib.sha256(
        json.dumps(
            {"required": required, "optional": optional},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "contractVersion": SAVED_WORK_RETENTION_CONTRACT_VERSION,
        "graphDigest": graph_digest,
        "required": required,
        "optional": optional,
        "resolvedCount": len(resolved),
    }


def describe_artifact_availability(
    *,
    status: str | None,
    expires_at_expired: bool = False,
    bytes_missing: bool = False,
    digest_mismatch: bool = False,
    quarantined: bool = False,
    deletion_started: bool = False,
    never_verified_complete: bool = False,
) -> SavedWorkAvailability:
    """Report evidence-based availability from lifecycle/upload evidence.

    A bare ``COMPLETE`` row never implies restorability on its own: missing
    bytes, digest mismatches, quarantine, expiry, and deletion evidence each
    take precedence in that order.
    """
    normalized = str(status or "").strip().lower()
    if quarantined:
        return "quarantined"
    if deletion_started or normalized == "deleted":
        return "deleted"
    if digest_mismatch:
        return "corrupt"
    if bytes_missing:
        return "incomplete"
    if never_verified_complete or normalized in {"", "pending_upload", "failed"}:
        if never_verified_complete:
            return "locally_retained_but_unsaved"
        return "incomplete"
    if expires_at_expired:
        return "expired"
    return "available"


def resolve_download_ttl_seconds(
    *,
    configured_ttl_seconds: int,
    artifact_ttl_seconds: int | None = None,
    use_ttl_seconds: int | None = None,
    restricted_content: bool = False,
) -> tuple[int, str]:
    """Bound signed-download validity by artifact/use/security policy.

    Returns ``(ttl_seconds, revocation_statement)``. Restricted content always
    uses the short bound; every caller receives the honest revocation
    limitation so UI state is never advertised as URL retraction.
    """
    candidates = [int(configured_ttl_seconds)]
    if artifact_ttl_seconds is not None:
        candidates.append(int(artifact_ttl_seconds))
    if use_ttl_seconds is not None:
        candidates.append(int(use_ttl_seconds))
    if restricted_content:
        candidates.append(SAVED_WORK_MAX_DOWNLOAD_TTL_SECONDS)
    ttl = max(SAVED_WORK_MIN_DOWNLOAD_TTL_SECONDS, min(candidates))
    ttl = min(ttl, SAVED_WORK_MAX_DOWNLOAD_TTL_SECONDS * 4)
    if restricted_content:
        ttl = min(ttl, SAVED_WORK_MAX_DOWNLOAD_TTL_SECONDS)
    return ttl, SAVED_WORK_DOWNLOAD_REVOCATION_LIMITATIONS


@dataclass(slots=True)
class SavedWorkQuotaLedger:
    """Per-scope quota accounting separating logical and physical bytes.

    Shared physical bytes (deduplicated blobs) and per-owner logical charges
    (each retained reference) are different measures: one physical blob may
    back many logical owners, so admission checks both dimensions and never
    double-bills shared bytes against a single scope.
    """

    quotas: dict[str, int] = field(
        default_factory=lambda: dict(SAVED_WORK_DEFAULT_QUOTAS)
    )
    logical_bytes_used: dict[str, int] = field(default_factory=dict)
    physical_bytes_used: dict[str, int] = field(default_factory=dict)
    live_claims_used: dict[str, int] = field(default_factory=dict)

    def check_and_reserve(
        self,
        *,
        scope: str,
        logical_bytes: int = 0,
        physical_bytes: int = 0,
        claims: int = 0,
    ) -> dict[str, int]:
        """Reserve capacity or raise with an explicit quota verdict."""
        scope = str(scope or "").strip()
        if not scope:
            raise ValueError("SAVED_WORK_QUOTA_SCOPE: quota scope must not be blank")
        if min(logical_bytes, physical_bytes, claims) < 0:
            raise ValueError("SAVED_WORK_QUOTA_SHAPE: quota charges are non-negative")
        logical_after = self.logical_bytes_used.get(scope, 0) + logical_bytes
        physical_after = self.physical_bytes_used.get(scope, 0) + physical_bytes
        claims_after = self.live_claims_used.get(scope, 0) + claims
        if logical_after > self.quotas["max_logical_bytes_per_scope"]:
            raise ValueError(
                "SAVED_WORK_QUOTA_LOGICAL: scope "
                f"{scope!r} exceeds logical quota "
                f"({logical_after} > {self.quotas['max_logical_bytes_per_scope']})"
            )
        if physical_after > self.quotas["max_physical_bytes_per_scope"]:
            raise ValueError(
                "SAVED_WORK_QUOTA_PHYSICAL: scope "
                f"{scope!r} exceeds physical quota "
                f"({physical_after} > {self.quotas['max_physical_bytes_per_scope']})"
            )
        if claims_after > self.quotas["max_live_use_claims_per_scope"]:
            raise ValueError(
                "SAVED_WORK_QUOTA_CLAIMS: scope "
                f"{scope!r} exceeds live-claim quota "
                f"({claims_after} > {self.quotas['max_live_use_claims_per_scope']})"
            )
        self.logical_bytes_used[scope] = logical_after
        self.physical_bytes_used[scope] = physical_after
        self.live_claims_used[scope] = claims_after
        return {
            "scope": scope,
            "logicalBytesUsed": logical_after,
            "physicalBytesUsed": physical_after,
            "liveClaimsUsed": claims_after,
        }

    def release(
        self,
        *,
        scope: str,
        logical_bytes: int = 0,
        physical_bytes: int = 0,
        claims: int = 0,
    ) -> dict[str, int]:
        """Release a previous reservation without going negative."""
        scope = str(scope or "").strip()
        self.logical_bytes_used[scope] = max(
            0, self.logical_bytes_used.get(scope, 0) - max(0, logical_bytes)
        )
        self.physical_bytes_used[scope] = max(
            0, self.physical_bytes_used.get(scope, 0) - max(0, physical_bytes)
        )
        self.live_claims_used[scope] = max(
            0, self.live_claims_used.get(scope, 0) - max(0, claims)
        )
        return {
            "scope": scope,
            "logicalBytesUsed": self.logical_bytes_used[scope],
            "physicalBytesUsed": self.physical_bytes_used[scope],
            "liveClaimsUsed": self.live_claims_used[scope],
        }
