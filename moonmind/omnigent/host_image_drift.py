"""Qualified host-image drift recovery (major.minor, not SHA).

Plans pin an exact digest-pinned host image as immutable launch authority.
Rebuilt images change patch tags and SHA digests while keeping the same
major.minor release series (and often the same exact versions). Requiring the
exact SHA to be present locally fails every app update even when a locally
available image from the same repository is functionally identical.

This module names the deployment's current trusted image for the same
repository so callers can reuse a compatible local image instead of pulling a
stale 7GB digest or failing. A fallback is returned only when it is qualified:

- digest-pinned and not a placeholder (mutable tags never participate, so an
  unadmitted tag cannot become a credential writer or bearer launcher);
- deployment authority: observed in bootstrap resolved-state provenance (the
  trusted boundary probed its Omnigent binary) or an explicit operator pin;
- series-compatible with the admitted plan when the caller supplies the
  expected Omnigent version (same major.minor; patch and SHA may evolve).

Same-repository is an additional bound, never the whole qualification:
repository equality alone does not prove compatibility. Downstream gates
(deployment identity, host selection, exact-host attestation with live version
probes) re-verify the series before any session starts.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from collections.abc import Mapping
from typing import Any

from moonmind.omnigent.compatibility import (
    is_same_image_repository,
    versions_compatible,
)

_DIGEST_RE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
_PLACEHOLDER_SUFFIXES = ("@" + "sha256:" + "0" * 64, "@" + "sha256:" + "c" * 64)

_HOST_IMAGE_ATTRS = (
    "opencode_host_image_ref",
    "shared_host_image_ref",
    "pi_host_image_ref",
)
_HOST_IMAGE_ENV_KEYS = (
    "OMNIGENT_OPENCODE_HOST_IMAGE_REF",
    "OMNIGENT_SHARED_HOST_IMAGE_REF",
    "OMNIGENT_PI_HOST_IMAGE_REF",
)


def _is_digest_pinned(ref: str) -> bool:
    return bool(_DIGEST_RE.fullmatch(ref.strip()))


def _is_placeholder(ref: str) -> bool:
    text = ref.strip()
    return any(text.endswith(suffix) for suffix in _PLACEHOLDER_SUFFIXES)


def _provenance_map() -> dict[str, dict[str, str | None]]:
    """Return bootstrap-observed provenance by image ref.

    Only images the trusted resolution boundary actually inspected (binary
    version probe plus build-label read) carry an entry. An entry without a
    recorded version is not prequalified.
    """

    try:
        from moonmind.omnigent.bootstrap.store import load_resolved_state

        state = load_resolved_state()
    except Exception:
        return {}
    if state is None:
        return {}
    details = getattr(state, "details", None) or {}
    raw = details.get("hostImageProvenance")
    provenance: dict[str, dict[str, str | None]] = {}
    if isinstance(raw, dict):
        for ref, entry in raw.items():
            if not isinstance(ref, str) or not ref.strip():
                continue
            if isinstance(entry, dict):
                provenance[ref.strip()] = {
                    "version": entry.get("version"),
                    "buildDigest": entry.get("buildDigest"),
                }
    # Reader for persisted discovery from before per-image provenance: only
    # the actually observed image may use it.
    compatibility = details.get("opencodeHostCompatibility")
    if isinstance(compatibility, dict):
        observed = str(compatibility.get("hostImageRef") or "").strip()
        if observed and observed not in provenance:
            provenance[observed] = {
                "version": compatibility.get("hostVersion"),
                "buildDigest": compatibility.get("hostBuildDigest"),
            }
    return provenance


def _operator_pins() -> list[str]:
    """Return explicit operator image pins (digest-pinned, non-placeholder)."""

    pins: list[str] = []
    for key in _HOST_IMAGE_ENV_KEYS:
        try:
            value = str(os.environ.get(key) or "").strip()
        except Exception:
            continue
        if value and _is_digest_pinned(value) and not _is_placeholder(value):
            if value not in pins:
                pins.append(value)
    return pins


def _candidate_deployed_refs() -> list[str]:
    """Return the deployment's current digest-pinned host refs (deduped).

    Sources are bootstrap resolved state and explicit operator pins. Mutable
    tags and placeholders are never candidates.
    """

    refs: list[str] = []
    try:
        from moonmind.omnigent.bootstrap.store import load_resolved_state

        state = load_resolved_state()
    except Exception:
        state = None
    if state is not None:
        for attr in _HOST_IMAGE_ATTRS:
            try:
                value = str(getattr(state, attr, "") or "").strip()
            except Exception:
                continue
            if (
                value
                and _is_digest_pinned(value)
                and not _is_placeholder(value)
                and value not in refs
            ):
                refs.append(value)
    for pin in _operator_pins():
        if pin not in refs:
            refs.append(pin)
    return refs


def compatible_deployed_fallback(
    requested_ref: str,
    *,
    deployed_refs: list[str] | tuple[str, ...] | None = None,
    expected_omnigent_version: str | None = None,
    provenance: dict[str, dict[str, str | None]] | None = None,
) -> str | None:
    """Return a qualified same-repository image to reuse instead of ``requested``.

    The fallback is digest-pinned, deployment-owned (bootstrap-observed or
    operator-pinned), from the same repository, and series-compatible with
    ``expected_omnigent_version`` when both the expectation and the
    candidate's observed version are known. Returns None when nothing
    qualifies, when the only match is the requested ref itself, or when the
    requested ref is not digest-pinned (mutable tags keep their existing
    behavior). Callers must still verify local presence (``docker image
    inspect``) before use.
    """

    requested = str(requested_ref or "").strip()
    if not _is_digest_pinned(requested) or _is_placeholder(requested):
        return None
    candidates = (
        list(deployed_refs) if deployed_refs is not None else _candidate_deployed_refs()
    )
    try:
        observed = provenance if provenance is not None else _provenance_map()
    except Exception:
        observed = {}
    expected = str(expected_omnigent_version or "").strip()
    operator_pins = set(_operator_pins())
    for candidate in candidates:
        candidate_text = str(candidate or "").strip()
        if (
            not candidate_text
            or candidate_text == requested
            or not _is_digest_pinned(candidate_text)
            or _is_placeholder(candidate_text)
            or not is_same_image_repository(requested, candidate_text)
        ):
            continue
        entry = observed.get(candidate_text) or {}
        candidate_version = entry.get("version")
        if expected and isinstance(candidate_version, str) and candidate_version:
            if not versions_compatible(expected, candidate_version):
                continue
        elif not candidate_version and candidate_text not in operator_pins:
            # Not observed by the trusted boundary and not an explicit
            # operator pin: unqualified, never substitute for credentials.
            continue
        return candidate_text
    return None


def is_compatible_image_drift(
    planned_ref: object,
    observed_ref: object,
) -> bool:
    """Return whether ``observed`` is an acceptable SHA drift from ``planned``.

    Same-repository is the bound: patch tags and SHA digests may evolve, but a
    different repository (different image family) is never compatible drift.
    Major.minor release compatibility is enforced downstream by deployment
    identity, host selection, and attestation; this predicate only excuses the
    SHA-level mismatch so those gates can judge versions instead of failing on
    digests.
    """

    return is_same_image_repository(planned_ref, observed_ref)


def reconcile_effective_launch_to_selected_host(
    effective_launch: object,
    selected_host_image_ref: str,
) -> dict[str, Any] | None:
    """Reconcile a stale policy image to the currently selected Host Class.

    Fresh planning compiles ``effective_launch`` from the persisted policy
    snapshot while the Host Class comes from current deployment evidence
    (env + bootstrap resolved state). After an image rebuild the two digests
    differ while the repository is identical. Exact-equality planning fails
    every app update even though launch-time drift recovery (launcher
    fallback + attestation same-repo gates) already accepts this case.

    Returns a reconciled copy pinning ``selected_host_image_ref`` when the
    planned image is same-repository drift, ``None`` when the repositories
    differ (fail closed, different image family). An exact match returns an
    equal copy without re-hashing. The reconciled copy updates the top-level
    ``hostImageRef`` and the effective ``boundaries.host.hostImageRef`` so the
    persisted launch artifact stays internally consistent, then recomputes
    ``snapshotRef`` with the same canonical JSON as
    ``_compile_persisted_effective_launch``. ``policyAuthority`` is left as
    the original policy evidence.
    """

    if not isinstance(effective_launch, Mapping):
        return None
    planned = str(effective_launch.get("hostImageRef") or "").strip()
    selected = str(selected_host_image_ref or "").strip()
    if not planned or not selected:
        return None
    if planned == selected:
        return dict(effective_launch)
    # Mutable tags and synthetic placeholders never participate in drift
    # recovery: an unadmitted tag or placeholder must fail closed rather than
    # silently becoming launch authority.
    if (
        not _is_digest_pinned(planned)
        or not _is_digest_pinned(selected)
        or _is_placeholder(planned)
        or _is_placeholder(selected)
    ):
        return None
    if not is_compatible_image_drift(planned, selected):
        return None
    reconciled = copy.deepcopy(dict(effective_launch))
    reconciled["hostImageRef"] = selected
    boundaries = reconciled.get("boundaries")
    if isinstance(boundaries, dict):
        host = boundaries.get("host")
        if isinstance(host, dict) and "hostImageRef" in host:
            host = dict(host)
            host["hostImageRef"] = selected
            boundaries = dict(boundaries)
            boundaries["host"] = host
            reconciled["boundaries"] = boundaries
    reconciled.pop("snapshotRef", None)
    canonical = json.dumps(reconciled, sort_keys=True, separators=(",", ":"))
    reconciled["snapshotRef"] = "omnigent-launch:sha256:" + hashlib.sha256(
        canonical.encode()
    ).hexdigest()
    return reconciled


__all__ = [
    "compatible_deployed_fallback",
    "is_compatible_image_drift",
    "reconcile_effective_launch_to_selected_host",
]
