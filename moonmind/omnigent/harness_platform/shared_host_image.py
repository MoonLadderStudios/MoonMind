"""Shared Omnigent host image selection.

On-demand hosts, OAuth runner containers, and login preflight checks all run
the one shared ``omnigent-host-moonmind`` image. This module resolves which
image reference those launches use from the deployment environment:

- ``OMNIGENT_SHARED_HOST_IMAGE_REF`` names an exact reference;
- ``OMNIGENT_HOST_IMAGE_REF`` is the bounded legacy alias, read only when the
  shared reference is unset;
- otherwise ``OMNIGENT_SHARED_HOST_IMAGE`` / ``OMNIGENT_SHARED_HOST_IMAGE_TAG``
  select the repository and tag, defaulting to the published ``latest``.

Selection is not qualification: it admits no host or session and does not
weaken artifact attestation. Malformed references fail closed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)

SHARED_HOST_IMAGE_ENV = "OMNIGENT_SHARED_HOST_IMAGE_REF"
LEGACY_HOST_IMAGE_ENV = "OMNIGENT_HOST_IMAGE_REF"
SHARED_HOST_IMAGE_NAME_ENV = "OMNIGENT_SHARED_HOST_IMAGE"
SHARED_HOST_IMAGE_TAG_ENV = "OMNIGENT_SHARED_HOST_IMAGE_TAG"

_DEFAULT_SHARED_HOST_IMAGE = "ghcr.io/moonladderstudios/omnigent-host-moonmind"
_DIGEST_PINNED_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_IMAGE_COORDINATE_RE = re.compile(
    r"(?:[A-Za-z0-9][A-Za-z0-9._-]*(?::[0-9]+)?/)?"
    r"[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*"
    r"(?::[A-Za-z0-9_][A-Za-z0-9._-]*)?"
)


def resolve_shared_host_image_ref(
    environment: Mapping[str, str] | None = None,
) -> str:
    """Resolve the selected shared host image without requiring an operator pin."""

    return resolve_effective_shared_host_image(environment)


def classify_effective_shared_host_image(
    environment: Mapping[str, str] | None = None,
) -> tuple[str | None, bool, str]:
    """Resolve the shared image selection; provenance is recorded at launch.

    Explicit mappings are effective launch inputs and are never rewritten by
    later installed state. Without a mapping, reuse the installed-image owner
    so a long-lived process does not keep selecting its startup environment.
    Tags and the ordinary latest default are valid selection inputs.
    """

    if environment is None:
        from moonmind.omnigent.bootstrap.store import with_installed_image_refs

        source = with_installed_image_refs()
    else:
        source = environment
    shared = str(source.get(SHARED_HOST_IMAGE_ENV) or "").strip()
    legacy = str(source.get(LEGACY_HOST_IMAGE_ENV) or "").strip()
    if shared:
        image, reason = shared, SHARED_HOST_IMAGE_ENV
    elif legacy:
        image, reason = legacy, f"{LEGACY_HOST_IMAGE_ENV} bounded-alias"
    else:
        name = str(source.get(SHARED_HOST_IMAGE_NAME_ENV) or "").strip()
        tag = str(source.get(SHARED_HOST_IMAGE_TAG_ENV) or "").strip()
        image = f"{name or _DEFAULT_SHARED_HOST_IMAGE}:{tag or 'latest'}"
        reason = "shared image/tag or latest default"
    # Check the repository and optional tag before the digest separately:
    # an explicit empty tag must not be normalized into an omitted/latest tag.
    coordinate = image.partition("@")[0]
    if not _IMAGE_COORDINATE_RE.fullmatch(coordinate) or (
        "@" in image and not _DIGEST_PINNED_RE.fullmatch(image)
    ):
        return None, False, f"invalid shared host image reference from {reason}"
    return image, True, reason


def resolve_effective_shared_host_image(
    environment: Mapping[str, str] | None = None,
) -> str:
    """Resolve the effective shared image, rejecting malformed references."""

    image_ref, supported, reason = classify_effective_shared_host_image(environment)
    if supported and image_ref is not None:
        return image_ref
    raise HarnessPlatformError(
        reason,
        code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
    )


__all__ = [
    "LEGACY_HOST_IMAGE_ENV",
    "SHARED_HOST_IMAGE_ENV",
    "SHARED_HOST_IMAGE_NAME_ENV",
    "SHARED_HOST_IMAGE_TAG_ENV",
    "classify_effective_shared_host_image",
    "resolve_effective_shared_host_image",
    "resolve_shared_host_image_ref",
]
