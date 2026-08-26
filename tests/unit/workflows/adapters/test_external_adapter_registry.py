"""Unit tests for ExternalAdapterRegistry."""

from __future__ import annotations

from functools import partial

import pytest

from moonmind.workflows.adapters.external_adapter_registry import (
    ExternalAdapterRegistry,
    build_default_registry,
)

class _StubAdapter:
    """Minimal stub satisfying the AgentAdapter protocol shape."""

    def __init__(self, *, agent_id: str = "stub") -> None:
        self.agent_id = agent_id

# ---------------------------------------------------------------------------
# ExternalAdapterRegistry core behaviour
# ---------------------------------------------------------------------------

class TestExternalAdapterRegistry:
    def test_register_and_create_returns_adapter(self):
        registry = ExternalAdapterRegistry()
        registry.register("test", partial(_StubAdapter, agent_id="test"))
        adapter = registry.create("test")
        assert isinstance(adapter, _StubAdapter)
        assert adapter.agent_id == "test"

    def test_create_raises_for_unknown_agent_id(self):
        registry = ExternalAdapterRegistry()
        with pytest.raises(ValueError, match="No external adapter registered"):
            registry.create("nonexistent")

    def test_register_is_case_insensitive(self):
        registry = ExternalAdapterRegistry()
        registry.register("Jules", partial(_StubAdapter, agent_id="jules"))
        adapter = registry.create("JULES")
        assert isinstance(adapter, _StubAdapter)
        assert adapter.agent_id == "jules"

    def test_register_rejects_blank_agent_id(self):
        registry = ExternalAdapterRegistry()
        with pytest.raises(ValueError, match="must not be blank"):
            registry.register("  ", _StubAdapter)

    def test_registered_ids_returns_sorted_list(self):
        registry = ExternalAdapterRegistry()
        registry.register("beta", _StubAdapter)
        registry.register("alpha", _StubAdapter)
        assert registry.registered_ids == ["alpha", "beta"]

    def test_contains_membership(self):
        registry = ExternalAdapterRegistry()
        registry.register("jules", _StubAdapter)
        assert "jules" in registry
        assert "codex_cloud" not in registry

    def test_len(self):
        registry = ExternalAdapterRegistry()
        assert len(registry) == 0
        registry.register("a", _StubAdapter)
        assert len(registry) == 1

# ---------------------------------------------------------------------------
# build_default_registry with runtime gates
# ---------------------------------------------------------------------------

class TestBuildDefaultRegistry:
    def test_empty_env_has_no_adapters(self):
        registry = build_default_registry(env={})
        assert len(registry) == 0

    def test_jules_registered_when_enabled(self):
        env = {
            "JULES_ENABLED": "true",
            "JULES_API_URL": "https://jules.test",
            "JULES_API_KEY": "test-key-123",
        }
        registry = build_default_registry(env=env)
        assert "jules" in registry
        assert "jules_api" in registry

    def test_codex_cloud_registered_when_enabled(self):
        env = {
            "CODEX_CLOUD_ENABLED": "true",
            "CODEX_CLOUD_API_URL": "https://codex.test",
            "CODEX_CLOUD_API_KEY": "test-key-456",
        }
        registry = build_default_registry(env=env)
        assert "codex_cloud" in registry

    def test_omnigent_registered_when_enabled(self):
        env = {
            "OMNIGENT_ENABLED": "true",
            "OMNIGENT_SERVER_URL": "https://omnigent.test",
        }
        registry = build_default_registry(env=env)
        assert "omnigent" in registry
        assert "omnigent_session" not in registry

    def test_omnigent_rollback_disables_only_new_adapter_selection(self):
        enabled = {
            "OMNIGENT_ENABLED": "true",
            "OMNIGENT_SERVER_URL": "https://omnigent.test",
        }
        historical_registry = build_default_registry(env=enabled)
        historical_adapter = historical_registry.create("omnigent")

        rolled_back_registry = build_default_registry(
            env={
                "OMNIGENT_ENABLED": "false",
                "OMNIGENT_SERVER_URL": "https://omnigent.test",
            }
        )

        assert historical_adapter is not None
        assert "omnigent" not in rolled_back_registry
        with pytest.raises(ValueError, match="No external adapter registered"):
            rolled_back_registry.create("omnigent")

    def test_both_registered_when_both_enabled(self):
        env = {
            "JULES_ENABLED": "true",
            "JULES_API_URL": "https://jules.test",
            "JULES_API_KEY": "test-key-jules",
            "CODEX_CLOUD_ENABLED": "true",
            "CODEX_CLOUD_API_URL": "https://codex.test",
            "CODEX_CLOUD_API_KEY": "test-key-codex",
        }
        registry = build_default_registry(env=env)
        assert "jules" in registry
        assert "codex_cloud" in registry

    def test_disabled_provider_not_registered(self):
        env = {
            "JULES_ENABLED": "false",
            "JULES_API_URL": "https://jules.test",
            "JULES_API_KEY": "test-key",
        }
        registry = build_default_registry(env=env)
        assert "jules" not in registry

    def test_omnigent_registered_only_when_runtime_gate_enabled(self):
        disabled_registry = build_default_registry(env={})
        assert "omnigent" not in disabled_registry

        missing_url_registry = build_default_registry(
            env={"OMNIGENT_ENABLED": "1"}
        )
        assert "omnigent" not in missing_url_registry

        enabled_registry = build_default_registry(
            env={
                "OMNIGENT_ENABLED": "1",
                "OMNIGENT_SERVER_URL": "https://omnigent.example.test",
                "OMNIGENT_API_TOKEN": "activity-boundary-only",
            }
        )

        assert enabled_registry.registered_ids == ["omnigent"]

    def test_omnigent_registry_adapter_does_not_expose_api_token(self):
        secret = "activity-boundary-only-secret"
        registry = build_default_registry(
            env={
                "OMNIGENT_ENABLED": "1",
                "OMNIGENT_SERVER_URL": "https://omnigent.example.test",
                "OMNIGENT_API_TOKEN": secret,
            }
        )

        adapter = registry.create("omnigent")
        capability_payload = adapter.provider_capability.model_dump(
            mode="json",
            by_alias=True,
        )

        assert secret not in repr(adapter)
        assert secret not in repr(capability_payload)

    def test_omnigent_alias_agent_ids_are_not_registered(self):
        registry = build_default_registry(
            env={
                "OMNIGENT_ENABLED": "1",
                "OMNIGENT_SERVER_URL": "https://omnigent.example.test",
            }
        )

        assert "omnigent" in registry
        for alias in (
            "omnigent_session",
            "omnigent_claude",
            "omnigent_codex",
            "omnigent_polly",
        ):
            assert alias not in registry
            with pytest.raises(ValueError, match="No external adapter registered"):
                registry.create(alias)
