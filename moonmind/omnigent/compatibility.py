"""Parse runtime versions and keep image provenance separate from compatibility."""

from __future__ import annotations

import re

_VERSION = re.compile(
    r"^(?:omnigent\s+)?(\d+)\.(\d+)(?:\.\d+)?(?: \(built [^()\r\n]+\))?$"
)

# Vendor runtimes (opencode, codex, claude) report plain versions, sometimes
# embedded in longer probe output (e.g. "opencode version 1.18.11 (...)").
# Patch and image SHA may evolve; only major.minor steers compatibility.
_VENDOR_VERSION = re.compile(r"(\d+)\.(\d+)(?:\.\d+)?")


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


def vendor_compatibility_series(version: object) -> tuple[int, int] | None:
    """Parse a vendor runtime version to its major.minor series.

    Accepts plain versions (``1.18.11``) and longer probe output containing a
    version (``opencode version 1.18.11``). Returns None for unknown input so
    callers fail closed instead of treating garbage as compatible.
    """

    text = str(version or "").strip()
    if not text:
        return None
    # Prefer a full-string plain version first (strict), then fall back to
    # searching embedded probe output for the first X.Y[.Z] triple.
    plain = re.fullmatch(r"(\d+)\.(\d+)(?:\.\d+)?", text)
    if plain is not None:
        return (int(plain[1]), int(plain[2]))
    match = _VENDOR_VERSION.search(text)
    if match is None:
        return None
    try:
        return (int(match[1]), int(match[2]))
    except ValueError:
        return None


def vendor_versions_compatible(pinned: object, observed: object) -> bool:
    """Return whether a vendor runtime observes the pinned major.minor series.

    Patch evolution (1.18.11 -> 1.18.12) is compatible; minor or major drift
    (1.18.x -> 1.19.x) is not. Unknown input is incompatible.
    """

    series = vendor_compatibility_series(pinned)
    return series is not None and series == vendor_compatibility_series(observed)
