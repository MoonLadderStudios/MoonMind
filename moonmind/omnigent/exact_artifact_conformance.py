"""Exact deployable-artifact conformance gate (Tier-1, noncredentialed).

Source issue: MoonLadderStudios/MoonMind#3710.

This module contains no provider semantics.  It is the authoritative pass/fail
decision for the required *Tier-1 exact-artifact* gate: given a runtime
capability-probe report gathered from the **exact deployable images** by
immutable digest, it fails closed unless every required runtime capability is
present in the real image, the probed digests match the admitted compatibility
manifest, and the retained evidence passes secret scanning.

The gate requires only capabilities the Tier-1 driver actually exercises against
the image.  Restart and terminal replay *after a fake host is removed* is a
provider-execution authority handoff that the noncredentialed driver does not
perform, so this gate does not assert it; that boundary is owned by the
reliability-journey and embedded-recovery gates, which run on the same commit
under the Omnigent contract gate.  Deriving a claim from an unrelated exit
status would let the gate advertise a boundary it never crossed.

The gate deliberately operates on a *report* rather than performing the Docker
build/probe itself.  Assembling that report from the real entrypoints requires
Docker and is the responsibility of
``tools/run_omnigent_exact_artifact_conformance.py`` (the CI driver) plus the
in-image probe ``tools/omnigent_exact_artifact_probe.py``.  Keeping the decision
logic in a pure, hermetically-tested module means the same gate that CI relies
on is exercised without a container runtime, and a regression such as the
missing Uvicorn WebSocket implementation (#3697) is proven to fail the gate.

Every returned document is validated secret-free through
:func:`moonmind.omnigent.conformance.assert_secret_free`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping

from moonmind.omnigent.conformance import (
    ConformanceContractError,
    assert_secret_free,
)
from moonmind.omnigent.native_ui_route_inventory import INVENTORY_SCHEMA_VERSION

EXACT_ARTIFACT_CONFORMANCE_VERSION = "moonmind.omnigent.exact-artifact-conformance/v1"

# The deployable roles that must be tested by immutable digest.  ``server`` is
# the API image, ``worker`` the Temporal worker image, and ``ui`` the compiled
# native UI bundle image.
REQUIRED_IMAGE_ROLES = ("server", "worker", "ui")

# Required runtime capabilities that must exist *in the exact image*, probed
# through the real entrypoint.  A missing or failed capability fails the gate;
# it is never a hard error, so a stripped dependency (e.g. the #3697 Uvicorn
# WebSocket implementation) surfaces as a clean gate failure with a named
# reason rather than an opaque crash.
REQUIRED_SERVER_CAPABILITIES = (
    "api_entrypoint_start",
    # #3697: Uvicorn must resolve an *installed* WebSocket protocol
    # implementation in the deployed image, not fall back to "none".
    "uvicorn_websocket_impl",
    "http_route_handler",
    "sse_route_handler",
    "websocket_route_handshake",
    "omnigent_adapters_import",
    "opentelemetry_init",
    "temporal_client_init",
    "docker_or_compose_available",
    "artifact_backend_init",
    "database_init",
    "browser_facing_deps",
    "migrations_clean_apply",
    "migrations_prior_schema_upgrade",
    # The deployable process must survive a restart against the schema it just
    # migrated and serve liveness again through its own entrypoint.
    "api_restart_against_existing_schema",
)

REQUIRED_WORKER_CAPABILITIES = (
    "omnigent_adapters_import",
    "opentelemetry_init",
    "temporal_client_init",
    "worker_task_queues_advertised",
    "worker_readiness_capabilities",
    "database_init",
)

REQUIRED_UI_CAPABILITIES = (
    "hosted_bootstrap_consumed",
    "no_root_v1_requests",
)

REQUIRED_CAPABILITIES: Mapping[str, tuple[str, ...]] = {
    "server": REQUIRED_SERVER_CAPABILITIES,
    "worker": REQUIRED_WORKER_CAPABILITIES,
    "ui": REQUIRED_UI_CAPABILITIES,
}

_DIGEST_REF = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_DIGEST = re.compile(r"^git:[0-9a-f]{40}$")
_ROUTE_ARTIFACT_ROLES = (
    "omnigent",
    "ui",
    "server",
    "host",
    "harnessImplementation",
    "moonmindFacade",
)
_ROUTE_POLICY_FIELDS = frozenset(
    {
        "publicRoute",
        "callerPermission",
        "requestBounds",
        "responseBounds",
        "identityVirtualization",
        "reconnect",
        "idempotency",
        "historicalRead",
        "unsupportedBehavior",
        "mutationReceipt",
    }
)


class ExactArtifactConformanceError(ValueError):
    """Raised when an exact-artifact report is structurally unusable."""


@dataclass(frozen=True, slots=True)
class GateFailure:
    """One named, non-secret gate failure."""

    code: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail}


def _digest_of(image_ref: Any) -> str | None:
    if not isinstance(image_ref, str):
        return None
    digest = image_ref.rsplit("@", 1)[-1]
    return digest if _DIGEST.fullmatch(digest) else None


def _require_digest_pinned(images: Mapping[str, Any]) -> None:
    for role in REQUIRED_IMAGE_ROLES:
        ref = images.get(role)
        if not isinstance(ref, str) or not _DIGEST_REF.fullmatch(ref):
            raise ExactArtifactConformanceError(
                f"{role} image must be pinned by an immutable name@sha256 digest"
            )


def _capability_index(
    probed: Any, *, role: str
) -> dict[str, Mapping[str, Any]]:
    if not isinstance(probed, (list, tuple)):
        raise ExactArtifactConformanceError(
            f"capabilities for role {role!r} must be a list"
        )
    index: dict[str, Mapping[str, Any]] = {}
    for entry in probed:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("name"), str):
            raise ExactArtifactConformanceError(
                f"role {role!r} capability entries must be objects with a name"
            )
        index[entry["name"]] = entry
    return index


def evaluate_exact_artifact_conformance(
    report: Mapping[str, Any],
    *,
    required_digests: Mapping[str, str],
    required_route_inventory_digest: str | None = None,
) -> dict[str, Any]:
    """Project a fail-closed verdict over an exact-artifact probe report.

    ``report`` is the evidence gathered from the exact deployable images:
    ``images`` (digest-pinned refs per role), ``capabilities`` (per-role probe
    results from the real entrypoint), and ``secretScan``.

    ``required_digests`` is the admitted compatibility manifest: the digests
    the deployable images must match.  The returned document is validated
    secret-free before it is returned.
    """
    if not isinstance(report, Mapping):
        raise ExactArtifactConformanceError("exact-artifact report must be an object")

    source_commit = report.get("sourceCommit")
    if not isinstance(source_commit, str) or not source_commit.strip():
        raise ExactArtifactConformanceError("sourceCommit is required")

    images = report.get("images")
    if not isinstance(images, Mapping):
        raise ExactArtifactConformanceError("report images are required")
    _require_digest_pinned(images)

    capabilities = report.get("capabilities")
    if not isinstance(capabilities, Mapping):
        raise ExactArtifactConformanceError("report capabilities are required")

    failures: list[GateFailure] = []

    # --- Exact stock route inventory and facade classification ---------------
    route_inventory = report.get("routeInventory")
    route_projection: dict[str, Any] | None = None
    if not isinstance(route_inventory, Mapping):
        failures.append(
            GateFailure(
                "route_inventory_missing",
                "exact stock route inventory evidence is missing",
            )
        )
    else:
        inventory_digest = route_inventory.get("inventoryDigest")
        artifact_digests = route_inventory.get("artifactDigests")
        route_count = route_inventory.get("routeCount")
        classified_count = route_inventory.get("classifiedRouteCount")
        unclassified_count = route_inventory.get("unclassifiedRouteCount")
        routes = route_inventory.get("routes")
        ui_references = route_inventory.get("uiRouteReferences")
        websocket_protocols = route_inventory.get("websocketProtocols")
        sse_protocols = route_inventory.get("sseProtocols")
        if route_inventory.get("schemaVersion") != INVENTORY_SCHEMA_VERSION:
            failures.append(
                GateFailure(
                    "route_inventory_schema_unsupported",
                    "exact stock route inventory schema is missing or unsupported",
                )
            )
        if not isinstance(inventory_digest, str) or not _DIGEST.fullmatch(
            inventory_digest
        ):
            failures.append(
                GateFailure(
                    "route_inventory_digest_invalid",
                    "exact stock route inventory digest is missing or invalid",
                )
            )
        else:
            digest_body = {
                key: value
                for key, value in route_inventory.items()
                if key != "inventoryDigest"
            }
            expected_inventory_digest = "sha256:" + sha256(
                json.dumps(
                    digest_body, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            if inventory_digest != expected_inventory_digest:
                failures.append(
                    GateFailure(
                        "route_inventory_digest_invalid",
                        "exact stock route inventory content does not match its digest",
                    )
                )
        if (
            required_route_inventory_digest is not None
            and inventory_digest != required_route_inventory_digest
        ):
            failures.append(
                GateFailure(
                    "route_inventory_digest_mismatch",
                    "exact stock route inventory changed without an admitted classification",
                )
            )
        if not isinstance(artifact_digests, Mapping) or set(artifact_digests) != set(
            _ROUTE_ARTIFACT_ROLES
        ):
            failures.append(
                GateFailure(
                    "route_inventory_artifacts_invalid",
                    "route inventory is not bound to every required exact artifact",
                )
            )
        elif not _GIT_DIGEST.fullmatch(str(artifact_digests.get("omnigent") or "")) or any(
            not _DIGEST.fullmatch(str(artifact_digests.get(role) or ""))
            for role in _ROUTE_ARTIFACT_ROLES
            if role != "omnigent"
        ):
            failures.append(
                GateFailure(
                    "route_inventory_artifacts_invalid",
                    "route inventory contains an invalid exact artifact digest",
                )
            )
        route_entries_valid = isinstance(routes, list) and bool(routes)
        route_keys: list[str] = []
        websocket_route_keys: set[str] = set()
        sse_route_keys: set[str] = set()
        actual_classified = 0
        if route_entries_valid:
            for route in routes:
                if not isinstance(route, Mapping):
                    route_entries_valid = False
                    continue
                route_key = route.get("routeKey")
                classification = route.get("classification")
                route_keys.append(str(route_key or ""))
                if route.get("transport") == "websocket" and isinstance(
                    route_key, str
                ):
                    websocket_route_keys.add(route_key)
                if route.get("transport") == "sse" and isinstance(route_key, str):
                    sse_route_keys.add(route_key)
                if classification in {"binding_scoped", "fail_closed"}:
                    actual_classified += 1
                else:
                    route_entries_valid = False
                if (
                    not isinstance(route_key, str)
                    or not route_key
                    or not _ROUTE_POLICY_FIELDS <= set(route)
                    or any(
                        not route.get(field)
                        for field in _ROUTE_POLICY_FIELDS - {"mutationReceipt"}
                    )
                ):
                    route_entries_valid = False
            if len(set(route_keys)) != len(route_keys):
                route_entries_valid = False
        observed_websocket_keys = {
            str(protocol.get("routeKey") or "")
            for protocol in websocket_protocols or ()
            if isinstance(protocol, Mapping)
            and isinstance(protocol.get("clientMessageClasses"), list)
            and isinstance(protocol.get("serverMessageClasses"), list)
            and protocol.get("unknownMessageBehavior") == "fail_closed"
        }
        observed_sse_keys = {
            str(protocol.get("routeKey") or "")
            for protocol in sse_protocols or ()
            if isinstance(protocol, Mapping)
            and protocol.get("upstreamCursorBehavior")
            and protocol.get("facadeCursorBehavior")
            and protocol.get("reconnectAuthorization")
        }
        protocol_entries_valid = (
            isinstance(websocket_protocols, list)
            and observed_websocket_keys == websocket_route_keys
            and isinstance(sse_protocols, list)
            and observed_sse_keys == sse_route_keys
            and isinstance(ui_references, list)
            and bool(ui_references)
            and all(
                isinstance(reference, Mapping)
                and str(reference.get("path") or "").startswith("/v1/")
                and reference.get("sourceFile")
                for reference in ui_references
            )
        )
        if (
            not route_entries_valid
            or not protocol_entries_valid
            or not isinstance(route_count, int)
            or route_count != len(routes or ())
            or classified_count != actual_classified
            or unclassified_count != route_count - actual_classified
            or unclassified_count != 0
        ):
            failures.append(
                GateFailure(
                    "route_inventory_unclassified",
                    "one or more exact stock routes lack a facade or fail-closed classification",
                )
            )
        route_projection = {
            "schemaVersion": route_inventory.get("schemaVersion"),
            "inventoryDigest": inventory_digest,
            "artifactDigests": dict(artifact_digests or {}),
            "routeCount": route_count,
            "classifiedRouteCount": classified_count,
            "unclassifiedRouteCount": unclassified_count,
        }

    # --- Required runtime capabilities exist in the exact image --------------
    for role, required in REQUIRED_CAPABILITIES.items():
        index = _capability_index(capabilities.get(role), role=role)
        for name in required:
            entry = index.get(name)
            if entry is None:
                failures.append(
                    GateFailure(
                        f"missing_capability:{role}:{name}",
                        f"{role} image did not report required capability {name!r}",
                    )
                )
                continue
            if entry.get("ok") is not True:
                detail = str(entry.get("detail") or "capability probe reported not ok")
                failures.append(
                    GateFailure(
                        f"failed_capability:{role}:{name}",
                        f"{role} capability {name!r} failed: {detail}",
                    )
                )

    # --- Probed digests match the admitted compatibility manifest ------------
    for role in REQUIRED_IMAGE_ROLES:
        observed = _digest_of(images.get(role))
        required = required_digests.get(role)
        if not isinstance(required, str) or not _DIGEST.fullmatch(required):
            failures.append(
                GateFailure(
                    f"unknown_required_digest:{role}",
                    f"admitted compatibility manifest has no {role} digest",
                )
            )
            continue
        if observed != required:
            failures.append(
                GateFailure(
                    f"digest_mismatch:{role}",
                    f"{role} image digest does not match the admitted manifest",
                )
            )

    # --- Retained evidence passes secret scanning ----------------------------
    secret_scan = report.get("secretScan")
    if not isinstance(secret_scan, Mapping) or secret_scan.get("status") != "passed":
        failures.append(
            GateFailure(
                "secret_scan_not_passed",
                "retained exact-artifact evidence did not pass secret scanning",
            )
        )
    # Defense in depth: independently scan the retained report so a probe that
    # leaked secret-like material into an evidence detail fails the gate even if
    # its own ``secretScan`` claims to have passed.  The failure detail is a
    # fixed string, so it never re-echoes the offending material.
    try:
        assert_secret_free(report)
    except ConformanceContractError:
        failures.append(
            GateFailure(
                "evidence_not_secret_free",
                "retained exact-artifact evidence contained secret-like material",
            )
        )

    verdict = "passed" if not failures else "failed"
    projection = {
        "schemaVersion": EXACT_ARTIFACT_CONFORMANCE_VERSION,
        "sourceCommit": source_commit,
        "verdict": verdict,
        "images": {role: images.get(role) for role in REQUIRED_IMAGE_ROLES},
        "requiredDigests": {
            role: required_digests.get(role) for role in REQUIRED_IMAGE_ROLES
        },
        "requiredCapabilities": {
            role: list(names) for role, names in REQUIRED_CAPABILITIES.items()
        },
        "routeInventory": route_projection,
        "failures": [failure.as_dict() for failure in failures],
    }
    try:
        assert_secret_free(projection)
    except ConformanceContractError as exc:  # pragma: no cover - defensive
        raise ExactArtifactConformanceError(
            "exact-artifact projection contained secret-like material"
        ) from exc
    return projection


def assert_exact_artifact_evidence(
    evidence: Mapping[str, Any],
    *,
    expected_commit: str | None = None,
    required_digests: Mapping[str, str] | None = None,
) -> None:
    """Fail closed unless a published exact-artifact projection proves success.

    Consumed by downstream publication/readiness gates (#3508/#3642) so a
    code-only change cannot advertise deployability until the exact deployable
    images were proven by digest.
    """
    if not isinstance(evidence, Mapping):
        raise ExactArtifactConformanceError("exact-artifact evidence must be an object")
    if evidence.get("schemaVersion") != EXACT_ARTIFACT_CONFORMANCE_VERSION:
        raise ExactArtifactConformanceError(
            "exact-artifact evidence schema is missing or unsupported"
        )
    if evidence.get("verdict") != "passed" or evidence.get("failures"):
        raise ExactArtifactConformanceError(
            "exact-artifact evidence did not pass the Tier-1 gate"
        )
    if expected_commit is not None and evidence.get("sourceCommit") != expected_commit:
        raise ExactArtifactConformanceError(
            "exact-artifact evidence was produced for a different source commit"
        )
    observed_images = evidence.get("images")
    if not isinstance(observed_images, Mapping):
        raise ExactArtifactConformanceError("exact-artifact evidence images are missing")
    route_inventory = evidence.get("routeInventory")
    if (
        not isinstance(route_inventory, Mapping)
        or route_inventory.get("schemaVersion") != INVENTORY_SCHEMA_VERSION
        or not _DIGEST.fullmatch(str(route_inventory.get("inventoryDigest") or ""))
        or route_inventory.get("routeCount") != route_inventory.get(
            "classifiedRouteCount"
        )
        or route_inventory.get("unclassifiedRouteCount") != 0
    ):
        raise ExactArtifactConformanceError(
            "exact-artifact route inventory evidence is missing or invalid"
        )
    if required_digests is not None:
        for role in REQUIRED_IMAGE_ROLES:
            required = required_digests.get(role)
            if required is None:
                continue
            if _digest_of(observed_images.get(role)) != required:
                raise ExactArtifactConformanceError(
                    f"exact-artifact evidence {role} digest does not match the "
                    "admitted manifest"
                )
    assert_secret_free(evidence)


__all__ = [
    "EXACT_ARTIFACT_CONFORMANCE_VERSION",
    "REQUIRED_IMAGE_ROLES",
    "REQUIRED_CAPABILITIES",
    "REQUIRED_SERVER_CAPABILITIES",
    "REQUIRED_WORKER_CAPABILITIES",
    "REQUIRED_UI_CAPABILITIES",
    "ExactArtifactConformanceError",
    "GateFailure",
    "evaluate_exact_artifact_conformance",
    "assert_exact_artifact_evidence",
]
