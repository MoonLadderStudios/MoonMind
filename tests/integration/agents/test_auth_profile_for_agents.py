"""Integration tests: auth profile resolution for managed agent adapters.

Tests that ManagedAgentAdapter correctly resolves profiles by ID, via 'auto',
and raises properly when profiles are missing.  No Temporal or Docker workers
required — these use mock profile fetchers simulating the auth_profile.list
activity response shape.

Run::

    ./tools/test_unit.sh tests/integration/agents/test_auth_profile_for_agents.py -v
"""

from __future__ import annotations

import uuid

import pytest

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.adapters.managed_agent_adapter import (
    ManagedAgentAdapter,
    ProfileResolutionError,
)

from tests.integration.agents.conftest import (
    build_execution_request,
    build_fake_profile,
    build_fake_profile_fetcher,
    build_stub_callables,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

_ALL_RUNTIME_IDS = ["gemini_cli", "codex_cli", "cursor_cli", "claude_code"]


class TestAuthProfileDiscovery:
    """Profile fetcher simulates what the real auth_profile.list activity returns."""

    @pytest.mark.parametrize("runtime_id", _ALL_RUNTIME_IDS)
    async def test_auto_selects_first_enabled_profile(self, runtime_id: str) -> None:
        """'auto' profile ref should resolve to the first available profile."""
        disabled = build_fake_profile(runtime_id, profile_id="disabled-1")
        disabled["enabled"] = False
        enabled = build_fake_profile(runtime_id, profile_id="enabled-1")

        # Profile fetcher returns both; adapter should pick the first from the list
        # (the activity pre-filters disabled profiles, so we simulate the
        # filtered result returning only enabled ones).
        stubs = build_stub_callables()
        adapter = ManagedAgentAdapter(
            profile_fetcher=build_fake_profile_fetcher(runtime_id, profiles=[enabled]),
            workflow_id=f"test-discover-{uuid.uuid4().hex[:6]}",
            runtime_id=runtime_id,
            **stubs,
        )

        request = build_execution_request(
            agent_kind="managed",
            agent_id=runtime_id,
            profile_ref="auto",
        )
        handle = await adapter.start(request)
        assert handle.metadata["profile_id"] == "enabled-1"

    @pytest.mark.parametrize("runtime_id", _ALL_RUNTIME_IDS)
    async def test_specific_profile_among_many(self, runtime_id: str) -> None:
        """When multiple profiles exist, a specific profile_id should be found."""
        profiles = [
            build_fake_profile(runtime_id, profile_id=f"slot-{i}")
            for i in range(3)
        ]
        stubs = build_stub_callables()
        adapter = ManagedAgentAdapter(
            profile_fetcher=build_fake_profile_fetcher(runtime_id, profiles=profiles),
            workflow_id=f"test-specific-{uuid.uuid4().hex[:6]}",
            runtime_id=runtime_id,
            **stubs,
        )

        request = build_execution_request(
            agent_kind="managed",
            agent_id=runtime_id,
            profile_ref="slot-2",
        )
        handle = await adapter.start(request)
        assert handle.metadata["profile_id"] == "slot-2"

    @pytest.mark.parametrize("runtime_id", _ALL_RUNTIME_IDS)
    async def test_nonexistent_profile_id_raises(self, runtime_id: str) -> None:
        """Requesting a non-existent profile_id should raise."""
        profiles = [build_fake_profile(runtime_id, profile_id="only-profile")]
        stubs = build_stub_callables()
        adapter = ManagedAgentAdapter(
            profile_fetcher=build_fake_profile_fetcher(runtime_id, profiles=profiles),
            workflow_id=f"test-missing-{uuid.uuid4().hex[:6]}",
            runtime_id=runtime_id,
            **stubs,
        )

        request = build_execution_request(
            agent_kind="managed",
            agent_id=runtime_id,
            profile_ref="does-not-exist",
        )
        with pytest.raises(ProfileResolutionError, match="not found"):
            await adapter.start(request)

    @pytest.mark.parametrize("runtime_id", _ALL_RUNTIME_IDS)
    async def test_empty_profile_list_raises(self, runtime_id: str) -> None:
        """When the fetcher returns zero profiles, resolution should fail."""
        stubs = build_stub_callables()
        adapter = ManagedAgentAdapter(
            profile_fetcher=build_fake_profile_fetcher(runtime_id, profiles=[]),
            workflow_id=f"test-empty-{uuid.uuid4().hex[:6]}",
            runtime_id=runtime_id,
            **stubs,
        )

        request = build_execution_request(
            agent_kind="managed",
            agent_id=runtime_id,
            profile_ref="auto",
        )
        with pytest.raises(ProfileResolutionError, match="No enabled auth profiles"):
            await adapter.start(request)


class TestAuthModeDefaults:
    """Verify each runtime resolves to the correct default auth mode."""

    @pytest.mark.parametrize(
        "runtime_id,expected_auth_mode",
        [
            ("gemini_cli", "api_key"),
            ("codex_cli", "api_key"),
            ("claude_code", "api_key"),
            ("cursor_cli", "oauth"),
        ],
    )
    async def test_default_auth_mode_from_strategy(
        self, runtime_id: str, expected_auth_mode: str
    ) -> None:
        """Adapter should derive auth_mode from strategy when profile doesn't specify."""
        profile = build_fake_profile(runtime_id)
        # Remove auth_mode from profile so adapter falls through to strategy default
        profile.pop("auth_mode", None)

        stubs = build_stub_callables()
        adapter = ManagedAgentAdapter(
            profile_fetcher=build_fake_profile_fetcher(runtime_id, profiles=[profile]),
            workflow_id=f"test-authmode-{uuid.uuid4().hex[:6]}",
            runtime_id=runtime_id,
            **stubs,
        )

        request = build_execution_request(
            agent_kind="managed",
            agent_id=runtime_id,
            profile_ref="auto",
        )
        handle = await adapter.start(request)
        assert handle.metadata["auth_mode"] == expected_auth_mode


class TestMultipleProfilesPerRuntime:
    """Validate adapter behavior with multiple profiles per runtime."""

    async def test_three_gemini_profiles(self) -> None:
        """Multiple profiles for one runtime should all be discoverable."""
        runtime_id = "gemini_cli"
        profiles = [
            build_fake_profile(runtime_id, profile_id="gemini-primary"),
            build_fake_profile(runtime_id, profile_id="gemini-secondary"),
            build_fake_profile(runtime_id, profile_id="gemini-tertiary"),
        ]

        for target_id in ["gemini-primary", "gemini-secondary", "gemini-tertiary"]:
            stubs = build_stub_callables()
            adapter = ManagedAgentAdapter(
                profile_fetcher=build_fake_profile_fetcher(runtime_id, profiles=profiles),
                workflow_id=f"test-multi-{uuid.uuid4().hex[:6]}",
                runtime_id=runtime_id,
                **stubs,
            )
            request = build_execution_request(
                agent_kind="managed",
                agent_id=runtime_id,
                profile_ref=target_id,
            )
            handle = await adapter.start(request)
            assert handle.metadata["profile_id"] == target_id
