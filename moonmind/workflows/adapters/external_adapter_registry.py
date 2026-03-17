"""Registry mapping external agent IDs to adapter factories."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from moonmind.workflows.adapters.agent_adapter import AgentAdapter

logger = logging.getLogger(__name__)

AdapterFactory = Callable[[], AgentAdapter]


class ExternalAdapterRegistry:
    """Thread-safe registry of external-agent adapter factories.

    Each factory is a zero-argument callable that returns a fully
    constructed ``AgentAdapter`` instance.  Factories are keyed by
    canonical ``agent_id`` (lowercased).
    """

    def __init__(self) -> None:
        self._factories: dict[str, AdapterFactory] = {}

    def register(self, agent_id: str, factory: AdapterFactory) -> None:
        """Register *factory* for *agent_id* (case-insensitive)."""

        key = agent_id.strip().lower()
        if not key:
            raise ValueError("agent_id must not be blank")
        self._factories[key] = factory

    def create(self, agent_id: str) -> AgentAdapter:
        """Instantiate and return an adapter for *agent_id*.

        Raises ``ValueError`` when no factory is registered for the
        requested *agent_id*.
        """

        key = agent_id.strip().lower()
        factory = self._factories.get(key)
        if factory is None:
            registered = sorted(self._factories) or ["(none)"]
            raise ValueError(
                f"No external adapter registered for agent_id={agent_id!r}. "
                f"Registered: {', '.join(registered)}"
            )
        return factory()

    @property
    def registered_ids(self) -> list[str]:
        """Return sorted list of registered agent IDs."""

        return sorted(self._factories)

    def __contains__(self, agent_id: str) -> bool:
        return agent_id.strip().lower() in self._factories

    def __len__(self) -> int:
        return len(self._factories)


def build_default_registry(
    *,
    env: dict[str, Any] | None = None,
) -> ExternalAdapterRegistry:
    """Build the default registry with Jules and Codex Cloud adapters.

    Each adapter is only registered if its runtime gate is satisfied
    (i.e. the provider is enabled and configured).
    """

    registry = ExternalAdapterRegistry()

    # --- Jules ---
    from moonmind.jules.runtime import is_jules_runtime_enabled

    if is_jules_runtime_enabled(env=env):
        import os

        from moonmind.workflows.adapters.jules_agent_adapter import JulesAgentAdapter
        from moonmind.workflows.adapters.jules_client import JulesClient

        source = env if env is not None else dict(os.environ)
        jules_url = str(source.get("JULES_API_URL", "")).strip()
        jules_key = str(source.get("JULES_API_KEY", "")).strip()

        def _jules_factory() -> AgentAdapter:
            client = JulesClient(base_url=jules_url, api_key=jules_key)
            return JulesAgentAdapter(client_factory=lambda: client)  # type: ignore[return-value]

        registry.register("jules", _jules_factory)
        registry.register("jules_api", _jules_factory)
        logger.info("Registered Jules external adapter")

    # --- Codex Cloud ---
    from moonmind.codex_cloud.settings import is_codex_cloud_enabled

    if is_codex_cloud_enabled(env=env):
        import os

        from moonmind.workflows.adapters.codex_cloud_agent_adapter import (
            CodexCloudAgentAdapter,
        )
        from moonmind.workflows.adapters.codex_cloud_client import CodexCloudClient

        source = env if env is not None else dict(os.environ)
        cloud_url = str(source.get("CODEX_CLOUD_API_URL", "")).strip()
        cloud_key = str(source.get("CODEX_CLOUD_API_KEY", "")).strip()

        def _codex_cloud_factory() -> AgentAdapter:
            client = CodexCloudClient(base_url=cloud_url, api_key=cloud_key)
            return CodexCloudAgentAdapter(client_factory=lambda: client)  # type: ignore[return-value]

        registry.register("codex_cloud", _codex_cloud_factory)
        logger.info("Registered Codex Cloud external adapter")

    return registry


__all__ = [
    "AdapterFactory",
    "ExternalAdapterRegistry",
    "build_default_registry",
]
