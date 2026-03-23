"""Integration tests: external adapter registry (build_default_registry).

Validates that the registry correctly gates adapter registration based on
environment variables.  No real API calls are made.

Run::

    ./tools/test_unit.sh tests/integration/agents/test_adapter_registry_integration.py -v
"""

from __future__ import annotations

import pytest

from moonmind.workflows.adapters.external_adapter_registry import (
    ExternalAdapterRegistry,
    build_default_registry,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


class TestRegistryWithNoCredentials:
    """With no provider env vars, no external adapters should be registered."""

    def test_empty_env_yields_empty_registry(self) -> None:
        """An empty env dict should produce no registered adapters."""
        registry = build_default_registry(env={})
        assert len(registry) == 0
        assert registry.registered_ids == []

    def test_missing_all_keys(self) -> None:
        """Env with irrelevant keys should produce no registrations."""
        registry = build_default_registry(env={"HOME": "/home/test", "PATH": "/usr/bin"})
        assert len(registry) == 0


class TestRegistryWithJulesCredentials:
    """Jules adapter should register when JULES_API_KEY is set."""

    def test_jules_registered_with_api_key(self) -> None:
        env = {"JULES_API_KEY": "test-key-not-real"}
        registry = build_default_registry(env=env)

        assert "jules" in registry
        assert "jules_api" in registry
        assert len(registry) >= 2

    def test_jules_adapter_creates_correctly(self) -> None:
        from moonmind.workflows.adapters.jules_agent_adapter import JulesAgentAdapter

        env = {"JULES_API_KEY": "test-key-not-real"}
        registry = build_default_registry(env=env)

        adapter = registry.create("jules")
        assert isinstance(adapter, JulesAgentAdapter)

    def test_jules_disabled_explicitly(self) -> None:
        """JULES_ENABLED=false should prevent registration even with key."""
        env = {"JULES_API_KEY": "test-key-not-real", "JULES_ENABLED": "false"}
        registry = build_default_registry(env=env)

        assert "jules" not in registry


class TestRegistryWithCodexCloudCredentials:
    """Codex Cloud adapter should register when all 3 env vars are set."""

    def test_codex_cloud_registered(self) -> None:
        env = {
            "CODEX_CLOUD_ENABLED": "true",
            "CODEX_CLOUD_API_URL": "https://api.codex.example.com",
            "CODEX_CLOUD_API_KEY": "test-key-not-real",
        }
        registry = build_default_registry(env=env)

        assert "codex_cloud" in registry

    def test_codex_cloud_adapter_creates_correctly(self) -> None:
        from moonmind.workflows.adapters.codex_cloud_agent_adapter import (
            CodexCloudAgentAdapter,
        )

        env = {
            "CODEX_CLOUD_ENABLED": "true",
            "CODEX_CLOUD_API_URL": "https://api.codex.example.com",
            "CODEX_CLOUD_API_KEY": "test-key-not-real",
        }
        registry = build_default_registry(env=env)

        adapter = registry.create("codex_cloud")
        assert isinstance(adapter, CodexCloudAgentAdapter)

    def test_codex_cloud_not_enabled(self) -> None:
        """Missing CODEX_CLOUD_ENABLED should prevent registration."""
        env = {
            "CODEX_CLOUD_API_URL": "https://api.codex.example.com",
            "CODEX_CLOUD_API_KEY": "test-key-not-real",
        }
        registry = build_default_registry(env=env)

        assert "codex_cloud" not in registry

    def test_codex_cloud_missing_key(self) -> None:
        env = {"CODEX_CLOUD_ENABLED": "true", "CODEX_CLOUD_API_URL": "https://example.com"}
        registry = build_default_registry(env=env)
        assert "codex_cloud" not in registry


class TestRegistryWithOpenClawCredentials:
    """OpenClaw adapter should register when enabled + token set."""

    def test_openclaw_registered(self) -> None:
        env = {
            "OPENCLAW_ENABLED": "true",
            "OPENCLAW_GATEWAY_TOKEN": "test-token-not-real",
        }
        registry = build_default_registry(env=env)

        assert "openclaw" in registry

    def test_openclaw_adapter_creates_correctly(self) -> None:
        from moonmind.workflows.adapters.openclaw_agent_adapter import (
            OpenClawExternalAdapter,
        )

        env = {
            "OPENCLAW_ENABLED": "true",
            "OPENCLAW_GATEWAY_TOKEN": "test-token-not-real",
        }
        registry = build_default_registry(env=env)

        adapter = registry.create("openclaw")
        assert isinstance(adapter, OpenClawExternalAdapter)

    def test_openclaw_not_enabled(self) -> None:
        env = {"OPENCLAW_GATEWAY_TOKEN": "test-token-not-real"}
        registry = build_default_registry(env=env)
        assert "openclaw" not in registry


class TestRegistryWithMultipleProviders:
    """Multiple providers registered simultaneously."""

    def test_all_providers_coexist(self) -> None:
        env = {
            "JULES_API_KEY": "test-key-not-real",
            "CODEX_CLOUD_ENABLED": "true",
            "CODEX_CLOUD_API_URL": "https://api.codex.example.com",
            "CODEX_CLOUD_API_KEY": "test-key-not-real",
            "OPENCLAW_ENABLED": "true",
            "OPENCLAW_GATEWAY_TOKEN": "test-token-not-real",
        }
        registry = build_default_registry(env=env)

        assert "jules" in registry
        assert "jules_api" in registry
        assert "codex_cloud" in registry
        assert "openclaw" in registry
        # jules + jules_api + codex_cloud + openclaw = 4
        assert len(registry) == 4


class TestRegistryUnknownAgent:
    """Requesting an unregistered agent should fail-fast."""

    def test_unknown_agent_raises(self) -> None:
        registry = build_default_registry(env={})
        with pytest.raises(ValueError, match="No external adapter registered"):
            registry.create("nonexistent_agent")
