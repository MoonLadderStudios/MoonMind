"""Capability resolution for the browser facade.

Resolves the effective capability set a caller may exercise as the intersection
of session-advertised capabilities and caller-permitted capabilities. Pure.
"""

from __future__ import annotations

from typing import Mapping


def resolve_capabilities(
    session_caps: Mapping[str, bool],
    caller_caps: Mapping[str, bool],
) -> dict[str, bool]:
    """Return capabilities enabled on both the session and the caller."""

    return {
        key: True
        for key, enabled in session_caps.items()
        if enabled and caller_caps.get(key, False)
    }


__all__ = ["resolve_capabilities"]
