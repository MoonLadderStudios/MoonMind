from __future__ import annotations

import asyncio

import pytest

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, ManagedRuntimeProfile
from moonmind.workflows.temporal.runtime.launcher import ManagedRuntimeLauncher
from moonmind.workflows.temporal.runtime.store import ManagedRunStore

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _make_profile(**overrides) -> ManagedRuntimeProfile:
    defaults = dict(
        runtime_id="codex_cli",
        command_template=["codex", "exec"],
        default_model="gpt-5.3-codex",
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
        instruction_ref="Implement MM-506.",
    )
    defaults.update(overrides)
    return AgentExecutionRequest(**defaults)


@pytest.mark.parametrize(
    ("rag_enabled", "expected_fragment"),
    [
        ("1", "moonmind rag search"),
        ("0", "rag_disabled"),
    ],
)
async def test_codex_launcher_includes_followup_retrieval_capability_note(
    tmp_path,
    monkeypatch,
    rag_enabled: str,
    expected_fragment: str,
) -> None:
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(tmp_path))
    monkeypatch.setenv("MOONMIND_RAG_AUTO_CONTEXT", "1")
    monkeypatch.setenv("RAG_ENABLED", rag_enabled)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    async def _fake_resolve(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.resolve_github_token_for_launch",
        _fake_resolve,
    )

    class _FakeContextInjectionService:
        async def inject_context(self, *, request, workspace_path):
            assert workspace_path == workspace
            request.instruction_ref = "Implement MM-506."

    monkeypatch.setattr(
        "moonmind.rag.context_injection.ContextInjectionService",
        _FakeContextInjectionService,
    )

    class _FakeProcess:
        def __init__(self, pid: int = 901) -> None:
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
    request = _make_request()

    _record, process, _cleanup, _deferred_cleanup = await launcher.launch(
        run_id=f"run-followup-{rag_enabled}",
        request=request,
        profile=profile,
        workspace_path=workspace,
    )
    await process.wait()

    prompt_arg = next(arg for arg in captured_args if isinstance(arg, str) and "Managed Codex CLI note:" in arg)
    assert "MoonMind retrieval capability:" in prompt_arg
    assert expected_fragment in prompt_arg
