"""Parse runtime versions and keep image provenance separate from compatibility."""

from __future__ import annotations

import re

_VERSION = re.compile(
    r"^(?:omnigent\s+)?(\d+)\.(\d+)(?:\.\d+)?(?: \(built [^()\r\n]+\))?$"
)


def compatibility_series(version: object) -> tuple[int, int] | None:
    """Parse a release version without treating unknown input as compatible."""
    match = _VERSION.fullmatch(str(version or "").strip())
    return (int(match[1]), int(match[2])) if match else None


def versions_compatible(expected: object, observed: object) -> bool:
    """Accept observed releases without treating version drift as breakage.

    Callers still require immutable image authority, observed build provenance,
    harness identity, and functional readiness. A different release number by
    itself cannot show that those contracts are incompatible.
    """

    return (
        compatibility_series(expected) is not None
        and compatibility_series(observed) is not None
    )


def image_repository(ref: object) -> str:
    """Return the repository portion of a digest-pinned or tagged image ref.

    ``ghcr.io/org/img@sha256:<digest>`` -> ``ghcr.io/org/img``.
    ``ghcr.io/org/img:1.18.11`` -> ``ghcr.io/org/img``.
    Unknown or empty input returns an empty string (never treated as matching).
    """

    text = str(ref or "").strip()
    if not text:
        return ""
    # Strip digest first, then tag (careful: registry may contain a port).
    candidate = text.split("@", 1)[0]
    slash = candidate.rfind("/")
    colon = candidate.rfind(":")
    if colon > slash:
        candidate = candidate[:colon]
    return candidate.strip()


def is_same_image_repository(first: object, second: object) -> bool:
    """Return whether two image refs share the same repository.

    Empty or unparseable refs never match: a missing repository must fail
    closed, never silently substitute another image family.
    """

    first_repo = image_repository(first)
    second_repo = image_repository(second)
    return bool(first_repo) and first_repo == second_repo
