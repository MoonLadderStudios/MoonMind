"""Integration tests: OpenClaw external agent adapter.

Validates the OpenClaw adapter registration, message construction, and
capability descriptor without requiring a running OpenClaw gateway. When
gateway credentials are present, additional connectivity tests run.

Run::

    ./tools/test_unit.sh tests/integration/agents/test_openclaw_integration.py -v
"""

from __future__ import annotations

import uuid

import pytest

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.adapters.openclaw_agent_adapter import (
    OpenClawExternalAdapter,
    build_openclaw_chat_messages,
    openclaw_success_result,
)

from tests.integration.agents.conftest import requires_openclaw

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Capability descriptor (no credentials needed)
# ---------------------------------------------------------------------------


class TestOpenClawCapability:
    """Verify OpenClaw adapter capability descriptor is correct."""

    def test_execution_style_is_streaming(self) -> None:
        adapter = OpenClawExternalAdapter()
        assert adapter.provider_capability.execution_style == "streaming_gateway"

    def test_provider_name(self) -> None:
        adapter = OpenClawExternalAdapter()
        assert adapter.provider_capability.provider_name == "openclaw"

    def test_does_not_support_cancel(self) -> None:
        adapter = OpenClawExternalAdapter()
        assert adapter.provider_capability.supports_cancel is False

    def test_does_not_support_result_fetch(self) -> None:
        adapter = OpenClawExternalAdapter()
        assert adapter.provider_capability.supports_result_fetch is False


# ---------------------------------------------------------------------------
# Message construction (no credentials needed)
# ---------------------------------------------------------------------------


class TestOpenClawMessageConstruction:
    """Verify request → chat messages mapping."""

    def _build_request(self, **overrides) -> AgentExecutionRequest:
        correlation_id = f"openclaw-test-{uuid.uuid4().hex[:8]}"
        defaults = dict(
            agentKind="external",
            agentId="openclaw",
            executionProfileRef="profile:openclaw-test",
            correlationId=correlation_id,
            idempotencyKey=f"idem-{correlation_id}",
            parameters={
                "title": "Test Task",
                "description": "Do something simple",
            },
        )
        defaults.update(overrides)
        return AgentExecutionRequest(**defaults)

    def test_messages_have_system_and_user(self) -> None:
        request = self._build_request()
        messages = build_openclaw_chat_messages(request)

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    def test_user_message_contains_title_and_description(self) -> None:
        request = self._build_request(
            parameters={
                "title": "Integration Test Title",
                "description": "Integration test description goes here",
            }
        )
        messages = build_openclaw_chat_messages(request)
        user_content = messages[1]["content"]

        assert "Integration Test Title" in user_content
        assert "Integration test description goes here" in user_content

    def test_user_message_contains_workspace_spec(self) -> None:
        request = self._build_request(
            workspaceSpec={"repository": "test/repo", "branch": "main"}
        )
        messages = build_openclaw_chat_messages(request)
        user_content = messages[1]["content"]

        assert "test/repo" in user_content

    def test_system_prompt_mentions_openclaw(self) -> None:
        request = self._build_request()
        messages = build_openclaw_chat_messages(request)
        assert "OpenClaw" in messages[0]["content"]


# ---------------------------------------------------------------------------
# Success result builder (no credentials needed)
# ---------------------------------------------------------------------------


class TestOpenClawResultBuilder:
    def test_success_result_shape(self) -> None:
        request = AgentExecutionRequest(
            agentKind="external",
            agentId="openclaw",
            executionProfileRef="profile:test",
            correlationId="test-123",
            idempotencyKey="idem-test-123",
        )
        result = openclaw_success_result(
            full_text="Task completed successfully.",
            request=request,
        )

        assert result.summary == "Task completed successfully."
        assert result.metadata["normalizedStatus"] == "completed"
        assert result.metadata["providerName"] == "openclaw"
        assert result.failure_class is None

    def test_long_text_is_truncated(self) -> None:
        request = AgentExecutionRequest(
            agentKind="external",
            agentId="openclaw",
            executionProfileRef="profile:test",
            correlationId="test-456",
            idempotencyKey="idem-test-456",
        )
        long_text = "x" * 5000
        result = openclaw_success_result(full_text=long_text, request=request)

        assert len(result.summary) <= 4096
        assert result.summary.endswith("...")


# ---------------------------------------------------------------------------
# Adapter start / status / cancel raise for streaming provider
# ---------------------------------------------------------------------------


class TestOpenClawAdapterRaisesOnPollOps:
    """OpenClaw adapter should raise on poll-based operations since it uses streaming."""

    async def test_do_start_raises(self) -> None:
        adapter = OpenClawExternalAdapter()
        with pytest.raises(RuntimeError, match="start activity is not registered"):
            await adapter.do_start(None, "", "", {})  # type: ignore[arg-type]

    async def test_do_status_raises(self) -> None:
        adapter = OpenClawExternalAdapter()
        with pytest.raises(RuntimeError, match="streaming"):
            await adapter.do_status("some-run-id")

    async def test_do_fetch_result_raises(self) -> None:
        adapter = OpenClawExternalAdapter()
        with pytest.raises(RuntimeError, match="streaming"):
            await adapter.do_fetch_result("some-run-id")

    async def test_do_cancel_raises(self) -> None:
        adapter = OpenClawExternalAdapter()
        with pytest.raises(RuntimeError, match="activity cancellation"):
            await adapter.do_cancel("some-run-id")


# ---------------------------------------------------------------------------
# Gateway connectivity (requires credentials)
# ---------------------------------------------------------------------------


@requires_openclaw
class TestOpenClawGatewayConnectivity:
    """Tests that require a running OpenClaw gateway.

    These validate that the gateway is reachable and responds.
    """

    async def test_gateway_is_reachable(self) -> None:
        """Basic health check of the OpenClaw gateway endpoint."""
        import aiohttp

        from moonmind.openclaw.settings import (
            resolved_gateway_url,
            resolved_default_model,
        )

        url = resolved_gateway_url()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{url}/health", timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    assert resp.status in {200, 404, 405}, (
                        f"Gateway at {url} returned unexpected status {resp.status}"
                    )
        except (aiohttp.ClientError, OSError) as exc:
            pytest.skip(f"OpenClaw gateway not reachable at {url}: {exc}")
