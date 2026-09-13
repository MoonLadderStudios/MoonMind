"""Omnigent interoperability is scoped to major.minor, independently of builds."""

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
    series = compatibility_series(expected)
    return series is not None and series == compatibility_series(observed)
