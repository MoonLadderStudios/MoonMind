"""Trusted realizer registry (Phase 1).

Keys are versioned implementation identities. The registry is populated at
startup and never mutated per-request. The Temporal activity does:

    plan = await plan_service.load_or_compile(request)
    realizer = registry.require(plan.payload.executionRealizerRef)
    return await realizer.execute(request, plan)

It must NOT contain `if harness == "opencode-native": ...`.

`codex-profile-bound@1` delegates to existing coordinator.
`generic-omnigent-host@1` owns generic host realization.
"""

from __future__ import annotations

from moonmind.omnigent.harness_platform.failures import HarnessPlatformError, HarnessPlatformFailure
from moonmind.omnigent.harness_platform.support import validate_realizer

from moonmind.omnigent.realizers.base import OmnigentExecutionRealizer


class OmnigentExecutionRealizerRegistry:
    def __init__(self) -> None:
        self._realizers: dict[str, OmnigentExecutionRealizer] = {}

    def register(self, realizer: OmnigentExecutionRealizer) -> None:
        if not isinstance(realizer.ref, str) or not realizer.ref:
            raise ValueError("realizer.ref must be non-empty string")
        # Validate against known trusted set
        try:
            validate_realizer(realizer.ref)
        except ValueError as exc:
            raise HarnessPlatformError(
                f"realizer {realizer.ref} not in trusted set",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_REALIZER_UNAVAILABLE,
            ) from exc
        existing = self._realizers.get(realizer.ref)
        if existing is not None and existing is not realizer:
            raise ValueError(f"realizer {realizer.ref} already registered")
        self._realizers[realizer.ref] = realizer

    def require(self, ref: str) -> OmnigentExecutionRealizer:
        realizer = self._realizers.get(ref)
        if realizer is None:
            raise HarnessPlatformError(
                f"execution realizer {ref} unavailable",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_REALIZER_UNAVAILABLE,
            )
        return realizer

    def get(self, ref: str) -> OmnigentExecutionRealizer | None:
        return self._realizers.get(ref)

    def list_refs(self) -> list[str]:
        return sorted(self._realizers.keys())


# Global default populated lazily to avoid circular imports at import time
_default_registry: OmnigentExecutionRealizerRegistry | None = None
_default_registry_init_error: Exception | None = None


def get_default_registry() -> OmnigentExecutionRealizerRegistry:
    global _default_registry, _default_registry_init_error
    if _default_registry is not None:
        # If previous init had errors and registry is incomplete, allow retry
        if _default_registry_init_error is not None and len(_default_registry.list_refs()) < 2:
            _default_registry = None
            _default_registry_init_error = None
        else:
            return _default_registry
    registry = OmnigentExecutionRealizerRegistry()
    init_error: Exception | None = None

    # Lazy imports to avoid cycles
    try:
        from moonmind.omnigent.realizers.codex_profile_bound import CodexProfileBoundRealizer

        registry.register(CodexProfileBoundRealizer())
    except Exception as exc:  # pragma: no cover - import/dependency failure is not tested
        init_error = exc

    try:
        from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer

        registry.register(GenericOmnigentHostRealizer())
    except Exception as exc:  # pragma: no cover
        init_error = exc

    if init_error is not None and len(registry.list_refs()) == 0:
        # No realizers at all – surface immediately rather than caching empty
        raise HarnessPlatformError(
            f"realizer registry initialization failed: {init_error}",
            code=HarnessPlatformFailure.OMNIGENT_EXECUTION_REALIZER_UNAVAILABLE,
        ) from init_error

    # Cache only when at least one realizer succeeded; retain init error for diagnostics
    _default_registry_init_error = init_error
    _default_registry = registry
    return registry


def reset_default_registry() -> None:
    global _default_registry, _default_registry_init_error
    _default_registry = None
    _default_registry_init_error = None


# Ensure the global registry is considered used (CodeQL)
__all__ = [
    "OmnigentExecutionRealizerRegistry",
    "get_default_registry",
    "reset_default_registry",
    "_default_registry",
    "_default_registry_init_error",
]
