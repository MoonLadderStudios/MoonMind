"""Integration tests: ManagedAgentAdapter lifecycle across all 4 runtimes.

Parametric tests that exercise the full adapter pipeline (profile resolution →
env shaping → slot request → launch delegation) with stub callables instead
of a real Temporal + worker stack.

No API keys or Docker workers required — these tests run locally with mocks.

Run::

    ./tools/test_unit.sh tests/integration/agents/test_managed_adapter_lifecycle.py -v
"""

from __future__ import annotations

import uuid

import pytest

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunHandle
from moonmind.workflows.adapters.managed_agent_adapter import (
    ManagedAgentAdapter,
    ProfileResolutionError,
    _shape_environment_for_api_key,
    _shape_environment_for_oauth,
    _OAUTH_CLEARED_VARS,
)

from tests.integration.agents.conftest import (
    build_execution_request,
    build_fake_profile,
    build_fake_profile_fetcher,
    build_stub_callables,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

_ALL_RUNTIME_IDS = ["gemini_cli", "codex_cli", "cursor_cli", "claude_code"]


# ---------------------------------------------------------------------------
# Profile resolution
# ---------------------------------------------------------------------------


class TestProfileResolution:
    """Verify adapter can resolve profiles by ID and via 'auto'."""

    @pytest.mark.parametrize("runtime_id", _ALL_RUNTIME_IDS)
    async def test_auto_selects_first_profile(self, runtime_id: str) -> None:
        """When execution_profile_ref='auto', adapter picks the first profile."""
        profile = build_fake_profile(runtime_id, profile_id="first-profile")
        stubs = build_stub_callables()
        adapter = ManagedAgentAdapter(
            profile_fetcher=build_fake_profile_fetcher(runtime_id, profiles=[profile]),
            workflow_id=f"test-wf-{uuid.uuid4().hex[:8]}",
            runtime_id=runtime_id,
            **stubs,
        )

        request = build_execution_request(
            agent_kind="managed",
            agent_id=runtime_id,
            profile_ref="auto",
        )
        handle: AgentRunHandle = await adapter.start(request)

        assert handle.agent_kind == "managed"
        assert handle.agent_id == runtime_id
        assert handle.metadata["profile_id"] == "first-profile"
        assert handle.status in {"launching", "running", "queued"}

    @pytest.mark.parametrize("runtime_id", _ALL_RUNTIME_IDS)
    async def test_specific_profile_id(self, runtime_id: str) -> None:
        """When execution_profile_ref is a specific ID, adapter finds it."""
        target_id = f"target-{runtime_id}"
        profiles = [
            build_fake_profile(runtime_id, profile_id="other-profile"),
            build_fake_profile(runtime_id, profile_id=target_id),
        ]
        stubs = build_stub_callables()
        adapter = ManagedAgentAdapter(
            profile_fetcher=build_fake_profile_fetcher(runtime_id, profiles=profiles),
            workflow_id=f"test-wf-{uuid.uuid4().hex[:8]}",
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

    @pytest.mark.parametrize("runtime_id", _ALL_RUNTIME_IDS)
    async def test_missing_profile_raises(self, runtime_id: str) -> None:
        """When no profiles exist, adapter raises ProfileResolutionError."""
        stubs = build_stub_callables()
        adapter = ManagedAgentAdapter(
            profile_fetcher=build_fake_profile_fetcher(runtime_id, profiles=[]),
            workflow_id=f"test-wf-{uuid.uuid4().hex[:8]}",
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


# ---------------------------------------------------------------------------
# Environment shaping
# ---------------------------------------------------------------------------


class TestEnvShaping:
    """Verify environment shaping clears sensitive vars correctly."""

    def test_oauth_mode_clears_api_keys(self) -> None:
        """OAuth mode must remove all sensitive API key vars."""
        base_env = {
            "HOME": "/home/test",
            "GOOGLE_API_KEY": "secret-google",
            "ANTHROPIC_API_KEY": "secret-anthropic",
            "OPENAI_API_KEY": "secret-openai",
            "PATH": "/usr/bin",
        }
        shaped = _shape_environment_for_oauth(
            base_env, volume_mount_path="/auth/gemini"
        )

        for sensitive_key in _OAUTH_CLEARED_VARS:
            assert sensitive_key not in shaped, (
                f"Sensitive key {sensitive_key} should be cleared in OAuth mode"
            )
        assert shaped["MANAGED_AUTH_VOLUME_PATH"] == "/auth/gemini"
        assert shaped["HOME"] == "/home/test"
        assert shaped["PATH"] == "/usr/bin"

    def test_api_key_mode_clears_and_sets_ref(self) -> None:
        """API key mode must clear sensitive vars, set ref and label."""
        base_env = {
            "HOME": "/home/test",
            "GOOGLE_API_KEY": "secret-google",
            "GH_TOKEN": "secret-gh",  # Also in _OAUTH_CLEARED_VARS
        }
        shaped = _shape_environment_for_api_key(
            base_env,
            api_key_ref="ref:gemini-key-slot-1",
            account_label="gemini-slot-1",
        )

        for sensitive_key in _OAUTH_CLEARED_VARS:
            assert sensitive_key not in shaped
        assert shaped["MANAGED_API_KEY_REF"] == "ref:gemini-key-slot-1"
        assert shaped["MANAGED_ACCOUNT_LABEL"] == "gemini-slot-1"

    @pytest.mark.parametrize("runtime_id", _ALL_RUNTIME_IDS)
    async def test_no_raw_credentials_in_handle(self, runtime_id: str) -> None:
        """Handle metadata must contain profile_id, auth_mode — not raw secrets."""
        profile = build_fake_profile(runtime_id)
        stubs = build_stub_callables()
        adapter = ManagedAgentAdapter(
            profile_fetcher=build_fake_profile_fetcher(runtime_id, profiles=[profile]),
            workflow_id=f"test-wf-{uuid.uuid4().hex[:8]}",
            runtime_id=runtime_id,
            **stubs,
        )

        request = build_execution_request(
            agent_kind="managed", agent_id=runtime_id, profile_ref="auto"
        )
        handle = await adapter.start(request)

        assert "profile_id" in handle.metadata
        assert "auth_mode" in handle.metadata
        # No raw key values should be present
        for key in ("api_key", "raw_key", "secret", "credential"):
            assert key not in handle.metadata, (
                f"Handle metadata should not contain '{key}'"
            )


# ---------------------------------------------------------------------------
# Slot management
# ---------------------------------------------------------------------------


class TestSlotManagement:
    """Verify slot request/release/cooldown signals fire."""

    @pytest.mark.parametrize("runtime_id", _ALL_RUNTIME_IDS)
    async def test_start_fires_slot_request(self, runtime_id: str) -> None:
        """adapter.start() must call slot_requester."""
        profile = build_fake_profile(runtime_id)
        stubs = build_stub_callables()
        adapter = ManagedAgentAdapter(
            profile_fetcher=build_fake_profile_fetcher(runtime_id, profiles=[profile]),
            workflow_id="test-wf-slot",
            runtime_id=runtime_id,
            **stubs,
        )

        request = build_execution_request(
            agent_kind="managed", agent_id=runtime_id, profile_ref="auto"
        )
        await adapter.start(request)

        stubs["slot_requester"].assert_awaited_once()
        call_kwargs = stubs["slot_requester"].call_args.kwargs
        assert call_kwargs["requester_workflow_id"] == "test-wf-slot"
        assert call_kwargs["runtime_id"] == runtime_id

    @pytest.mark.parametrize("runtime_id", _ALL_RUNTIME_IDS)
    async def test_release_fires_slot_release(self, runtime_id: str) -> None:
        """adapter.release_slot() must call slot_releaser."""
        profile = build_fake_profile(runtime_id, profile_id="release-test-profile")
        stubs = build_stub_callables()
        adapter = ManagedAgentAdapter(
            profile_fetcher=build_fake_profile_fetcher(runtime_id, profiles=[profile]),
            workflow_id="test-wf-release",
            runtime_id=runtime_id,
            **stubs,
        )

        request = build_execution_request(
            agent_kind="managed", agent_id=runtime_id, profile_ref="auto"
        )
        await adapter.start(request)
        await adapter.release_slot()

        stubs["slot_releaser"].assert_awaited_once()
        call_kwargs = stubs["slot_releaser"].call_args.kwargs
        assert call_kwargs["profile_id"] == "release-test-profile"

    @pytest.mark.parametrize("runtime_id", _ALL_RUNTIME_IDS)
    async def test_cooldown_report(self, runtime_id: str) -> None:
        """adapter.report_429_cooldown() forwards to cooldown_reporter."""
        profile = build_fake_profile(runtime_id, profile_id="cooldown-test")
        stubs = build_stub_callables()
        adapter = ManagedAgentAdapter(
            profile_fetcher=build_fake_profile_fetcher(runtime_id, profiles=[profile]),
            workflow_id="test-wf-cooldown",
            runtime_id=runtime_id,
            **stubs,
        )

        request = build_execution_request(
            agent_kind="managed", agent_id=runtime_id, profile_ref="auto"
        )
        await adapter.start(request)
        await adapter.report_429_cooldown(cooldown_seconds=120)

        stubs["cooldown_reporter"].assert_awaited_once()
        call_kwargs = stubs["cooldown_reporter"].call_args.kwargs
        assert call_kwargs["profile_id"] == "cooldown-test"
        assert call_kwargs["cooldown_seconds"] == 120


# ---------------------------------------------------------------------------
# Run launcher delegation
# ---------------------------------------------------------------------------


class TestRunLauncherDelegation:
    """Verify adapter correctly delegates to run_launcher with proper payload."""

    @pytest.mark.parametrize("runtime_id", _ALL_RUNTIME_IDS)
    async def test_launcher_receives_correct_payload(self, runtime_id: str) -> None:
        """run_launcher must receive profile, request, and run_id."""
        profile = build_fake_profile(runtime_id, profile_id="launch-test")
        stubs = build_stub_callables()
        adapter = ManagedAgentAdapter(
            profile_fetcher=build_fake_profile_fetcher(runtime_id, profiles=[profile]),
            workflow_id="test-wf-launch",
            runtime_id=runtime_id,
            **stubs,
        )

        request = build_execution_request(
            agent_kind="managed",
            agent_id=runtime_id,
            profile_ref="auto",
            instruction="Integration test instruction",
        )
        handle = await adapter.start(request)

        stubs["run_launcher"].assert_awaited_once()
        launch_payload = stubs["run_launcher"].call_args.kwargs["payload"]

        assert "run_id" in launch_payload
        assert "profile" in launch_payload
        assert "request" in launch_payload
        assert launch_payload["profile"]["runtimeId"] == runtime_id
        assert launch_payload["profile"]["profileId"] == "launch-test"


# ---------------------------------------------------------------------------
# Wrong agent_kind rejection
# ---------------------------------------------------------------------------


class TestAgentKindValidation:
    """ManagedAgentAdapter must reject non-managed requests."""

    async def test_external_kind_rejected(self) -> None:
        stubs = build_stub_callables()
        adapter = ManagedAgentAdapter(
            profile_fetcher=build_fake_profile_fetcher("gemini_cli"),
            workflow_id="test-wf-reject",
            runtime_id="gemini_cli",
            **stubs,
        )

        # Build request with external kind
        request = build_execution_request(
            agent_kind="external",
            agent_id="jules",
            profile_ref="auto",
        )

        with pytest.raises(ValueError, match="agent_kind='managed'"):
            await adapter.start(request)
