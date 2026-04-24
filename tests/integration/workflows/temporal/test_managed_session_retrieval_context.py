from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, ManagedRuntimeProfile
from moonmind.workflows.temporal.runtime.launcher import ManagedRuntimeLauncher
from moonmind.workflows.temporal.runtime.store import ManagedRunStore

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _make_profile(**overrides) -> ManagedRuntimeProfile:
    defaults = dict(
        runtime_id="claude_code",
        command_template=["claude"],
        default_model="claude-sonnet-4-6",
        default_effort=None,
        default_timeout_seconds=3600,
        workspace_mode="tempdir",
        env_overrides={},
    )
    defaults.update(overrides)
    return ManagedRuntimeProfile(**defaults)


def _make_request(**overrides) -> AgentExecutionRequest:
    defaults = dict(
        agent_kind="managed",
        agent_id="agent-1",
        execution_profile_ref="default-managed",
        correlation_id="test-corr-1",
        idempotency_key="run-1",
    )
    defaults.update(overrides)
    return AgentExecutionRequest(**defaults)


@patch("moonmind.rag.context_injection.ContextInjectionService")
async def test_claude_launcher_uses_shared_context_injection_before_writing_claude_md(
    mock_service_class,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(tmp_path))

    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    mock_service = mock_service_class.return_value

    async def _inject_context(*, request, workspace_path):
        assert workspace_path == workspace
        request.instruction_ref = "Injected retrieval context"

    mock_service.inject_context = AsyncMock(side_effect=_inject_context)

    async def _fake_resolve(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.resolve_github_token_for_launch",
        _fake_resolve,
    )

    class _FakeProcess:
        def __init__(self, pid: int = 891) -> None:
            self.pid = pid
            self.returncode = 0
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()

        async def wait(self) -> int:
            return 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    captured_args: tuple[object, ...] = ()

    async def _fake_create_subprocess_exec(*args, **_kwargs):
        nonlocal captured_args
        captured_args = args
        return _FakeProcess()

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    profile = _make_profile()
    request = _make_request(instruction_ref="Original instruction")

    _record, process, _cleanup, _deferred_cleanup = await launcher.launch(
        run_id="run-claude-rag-integration",
        request=request,
        profile=profile,
        workspace_path=workspace,
    )
    await process.wait()

    assert any(arg == "Injected retrieval context" for arg in captured_args)
    assert (workspace / "CLAUDE.md").read_text(encoding="utf-8") == "Injected retrieval context"
