"""Compatible host-image drift recovery (major.minor, not SHA).

Plans pin an exact digest-pinned host image as immutable launch authority.
Rebuilt images change patch tags and SHA digests while keeping the same
major.minor release series (and often the same exact versions). Requiring the
exact SHA to be present locally fails every app update even when a locally
available image from the same repository is functionally identical.

This module resolves the deployment's current trusted image for the same
repository so callers can reuse a compatible local image instead of pulling a
stale 7GB digest or failing. Same-repository is the safety bound: the fallback
is always the deployment's currently qualified digest (via bootstrap resolved
state or environment), never an arbitrary mutable tag. Downstream
major.minor gates (deployment identity, host selection, attestation) still
enforce release compatibility; this helper only avoids SHA-level brittleness.
"""

from __future__ import annotations

from collections.abc import Mapping

from moonmind.omnigent.compatibility import is_same_image_repository


def _candidate_deployed_refs() -> list[str]:
    """Return the deployment's current digest-pinned host refs (deduped)."""

    refs: list[str] = []
    try:
        from moonmind.omnigent.bootstrap.store import load_resolved_state

        state = load_resolved_state()
    except Exception:
        state = None
    if state is not None:
        for attr in (
            "opencode_host_image_ref",
            "shared_host_image_ref",
            "pi_host_image_ref",
        ):
            try:
                value = str(getattr(state, attr, "") or "").strip()
            except Exception:
                value = ""
            if value and value not in refs:
                refs.append(value)
    # Environment is the second authority (already-resolved digests are
    # exported here by the bootstrap publisher). Operator pins are included:
    # they are still same-repo trusted authority, not mutable tags.
    try:
        import os

        for key in (
            "OMNIGENT_OPENCODE_HOST_IMAGE_REF",
            "OMNIGENT_SHARED_HOST_IMAGE_REF",
            "OMNIGENT_PI_HOST_IMAGE_REF",
        ):
            value = str(os.environ.get(key) or "").strip()
            if value and value not in refs:
                refs.append(value)
    except Exception:
        pass
    return refs


def compatible_deployed_fallback(
    requested_ref: str,
    *,
    deployed_refs: list[str] | tuple[str, ...] | None = None,
) -> str | None:
    """Return a same-repository deployed image to reuse instead of ``requested``.

    Returns None when no same-repository deployed ref exists, when the only
    match is the requested ref itself, or when the requested ref has no
    parseable repository (fail closed). Callers must still verify the fallback
    is present locally (``docker image inspect``) before using it; this helper
    only names the trusted same-repo candidate.
    """

    requested = str(requested_ref or "").strip()
    if not requested or "@sha256:" not in requested:
        # Only digest-pinned writers/launchers participate in drift recovery.
        # Mutable tags keep their existing (non-pulling) behavior.
        return None
    candidates = (
        list(deployed_refs) if deployed_refs is not None else _candidate_deployed_refs()
    )
    for candidate in candidates:
        candidate_text = str(candidate or "").strip()
        if not candidate_text or candidate_text == requested:
            continue
        if is_same_image_repository(requested, candidate_text):
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


__all__ = [
    "compatible_deployed_fallback",
    "is_compatible_image_drift",
]
