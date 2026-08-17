"""Native UI facade seam.

Resolves whether a session can be served by the native Omnigent chat UI. This is
a projection over session-advertised capabilities/profile; it does not decide or
mutate lifecycle. The rich legacy native-UI compatibility surface remains during
the incremental migration and is narrowed behind this seam in later phases.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NativeUiBinding:
    serves_native_chat: bool
    reason: str


def resolve_native_binding(
    *, provider: str, compatibility_profile: str,
    supported_providers: frozenset[str],
    supported_profiles: frozenset[str],
) -> NativeUiBinding:
    if provider not in supported_providers:
        return NativeUiBinding(False, "unsupported_provider")
    if compatibility_profile not in supported_profiles:
        return NativeUiBinding(False, "unsupported_profile")
    return NativeUiBinding(True, "native")


__all__ = ["NativeUiBinding", "resolve_native_binding"]
