"""Qualified host-image drift recovery using observed deployment provenance.

Plans pin an exact digest-pinned host image as immutable launch authority.
Rebuilt images change versions and SHA digests. Requiring the exact SHA to be
present locally fails every app update even when a locally available image
from the same repository is functionally identical.

This module names the deployment's current trusted image for the same
repository so callers can reuse a compatible local image instead of pulling a
stale 7GB digest or failing. A fallback is returned only when it is qualified:

- digest-pinned and not a placeholder (mutable tags never participate, so an
  unadmitted tag cannot become a credential writer or bearer launcher);
- deployment authority: observed in bootstrap resolved-state provenance (the
  trusted boundary probed its Omnigent binary) or an explicit operator pin;
- a parseable Omnigent version when the caller supplies an expected version.

Same-repository is an additional bound, never the whole qualification:
repository equality alone does not prove compatibility. Downstream gates
(deployment identity, host selection, exact-host attestation with live probes)
re-verify the selected image and required behavior before any session starts.
"""

from __future__ import annotations

import copy
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
    operator-pinned), from the same repository, and carrying parseable
    version evidence when ``expected_omnigent_version`` is supplied. Returns
    None when nothing qualifies, when only the requested ref matches, or when
    that ref is not digest-pinned (mutable tags keep their existing
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
    Image and capability authority is enforced downstream by deployment
    identity, host selection, and attestation; this predicate only excuses the
    SHA-level mismatch so those gates can judge versions instead of failing on
    digests.
    """

    return is_same_image_repository(planned_ref, observed_ref)


def installed_server_image_ref() -> str:
    """Return the digest-pinned Omnigent server the deployment has installed."""

    from moonmind.omnigent.bootstrap.store import with_installed_image_refs

    value = str(with_installed_image_refs().get("OMNIGENT_IMAGE_REF") or "").strip()
    if not _is_digest_pinned(value) or _is_placeholder(value):
        return ""
    return value


def _rebindable(planned: str, selected: str) -> bool:
    # Mutable tags and synthetic placeholders never participate in drift
    # recovery: an unadmitted tag or placeholder must fail closed rather than
    # silently becoming launch authority.
    return (
        _is_digest_pinned(planned)
        and _is_digest_pinned(selected)
        and not _is_placeholder(planned)
        and not _is_placeholder(selected)
        and is_compatible_image_drift(planned, selected)
    )


def reconcile_effective_launch_to_selected_host(
    effective_launch: object,
    selected_host_image_ref: str,
    *,
    selected_server_image_ref: str | None = None,
) -> dict[str, Any] | None:
    """Bind a fresh launch to the installed host and server images.

    Fresh planning compiles ``effective_launch`` from the persisted policy
    snapshot, whose image fields record the images current when that policy
    version was written. The Host Class and the installed server come from
    current deployment evidence (bootstrap resolved state). After an update
    the digests differ while the repositories are identical; the policy names
    the image family and the attempt binds the installed artifacts, so a
    routine update needs no new policy version.

    Returns a copy pinning ``selected_host_image_ref`` (and
    ``selected_server_image_ref`` when given and from the policy server's
    repository) when the planned host is same-repository drift, ``None`` when
    the host repositories differ (fail closed, different image family). A
    server from another repository is left as the policy recorded it for
    launch-time attestation to judge. A launch that already matches returns
    an equal copy without re-hashing. The copy updates the top-level refs and
    ``boundaries.host`` so the persisted launch artifact stays internally
    consistent, then recomputes ``snapshotRef`` with the same canonical JSON
    as ``_compile_persisted_effective_launch``. ``policyAuthority`` is left as
    the original policy evidence.
    """

    if not isinstance(effective_launch, Mapping):
        return None
    planned = str(effective_launch.get("hostImageRef") or "").strip()
    selected = str(selected_host_image_ref or "").strip()
    if not planned or not selected:
        return None
    bound: dict[str, str] = {}
    if planned != selected:
        if not _rebindable(planned, selected):
            return None
        bound["hostImageRef"] = selected
    planned_server = str(effective_launch.get("serverImageRef") or "").strip()
    server = str(selected_server_image_ref or "").strip()
    if server and planned_server != server and _rebindable(planned_server, server):
        bound["serverImageRef"] = server
    if not bound:
        return dict(effective_launch)
    reconciled = copy.deepcopy(dict(effective_launch))
    reconciled.update(bound)
    boundaries = reconciled.get("boundaries")
    if isinstance(boundaries, dict):
        host = boundaries.get("host")
        if isinstance(host, dict):
            host = dict(host)
            host.update({key: value for key, value in bound.items() if key in host})
            boundaries = dict(boundaries)
            boundaries["host"] = host
            reconciled["boundaries"] = boundaries
    from moonmind.omnigent.execution_profiles import seal_effective_launch_snapshot

    return seal_effective_launch_snapshot(reconciled)


def bind_installed_shared_host_launch(
    effective_launch: Mapping[str, Any]
) -> dict[str, Any]:
    """Bind a shared-host (Codex/Claude) launch to the installed images.

    For launches compiled outside admission, where no Host Class selection
    runs (for example OAuth credential maintenance). A launch naming another
    host image family is returned unchanged.
    """

    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
    from moonmind.omnigent.harness_platform.host_classes import (
        resolve_shared_host_image_ref,
    )

    planned = str(effective_launch.get("hostImageRef") or "").strip()
    try:
        installed = resolve_shared_host_image_ref()
    except HarnessPlatformError:
        installed = ""
    host = (
        installed
        if installed and is_compatible_image_drift(planned, installed)
        else planned
    )
    return reconcile_effective_launch_to_selected_host(
        effective_launch,
        host,
        selected_server_image_ref=installed_server_image_ref(),
    ) or dict(effective_launch)


__all__ = [
    "bind_installed_shared_host_launch",
    "compatible_deployed_fallback",
    "installed_server_image_ref",
    "is_compatible_image_drift",
    "reconcile_effective_launch_to_selected_host",
]
