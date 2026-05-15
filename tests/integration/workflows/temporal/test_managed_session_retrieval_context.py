from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from moonmind.rag.context_pack import ContextItem, ContextPack
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, ManagedRuntimeProfile
from moonmind.schemas.agent_runtime_models import RuntimeCommandInvocation
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
async def test_claude_launcher_uses_shared_context_injection_for_prompt(
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

    # The injected retrieval context lands in request.instruction_ref and is
    # passed to the Claude CLI via -p; CLAUDE.md remains project context and
    # is never written by the launcher.
    assert any(arg == "Injected retrieval context" for arg in captured_args)
    assert not (workspace / "CLAUDE.md").exists()


@patch("moonmind.rag.context_injection.ContextInjectionService")
async def test_codex_runtime_command_rendering_keeps_command_before_context(
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
        request.instruction_ref = "Check this branch.\n\nRetrieved context."

    mock_service.inject_context = AsyncMock(side_effect=_inject_context)

    async def _fake_resolve(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.resolve_github_token_for_launch",
        _fake_resolve,
    )

    class _FakeProcess:
        def __init__(self, pid: int = 892) -> None:
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

    profile = _make_profile(
        runtime_id="codex_cli",
        command_template=["codex", "exec"],
        default_model="gpt-5.4",
    )
    request = _make_request(
        instruction_ref="/review\nCheck this branch.",
        runtimeCommand=RuntimeCommandInvocation(
            kind="slash_command",
            source="leading_slash",
            sourcePath="objective.instructions",
            command="review",
            rawCommand="/review",
            args="",
            instructionBody="Check this branch.",
            targetRuntime="codex_cli",
            detectionStatus="detected",
            hintStatus="hinted",
            recognitionMode="hinted_runtime_passthrough",
            requiresRuntimeRecognition=True,
            runtimeCapabilityVersion="2026-05-13",
            hintCatalogVersion="2026-05-13",
            detectionPhase="submit",
        ),
    )

    _record, process, _cleanup, _deferred_cleanup = await launcher.launch(
        run_id="run-codex-runtime-command-render",
        request=request,
        profile=profile,
        workspace_path=workspace,
    )
    await process.wait()

    prompts = [arg for arg in captured_args if isinstance(arg, str)]
    rendered = next(arg for arg in prompts if arg.startswith("/review"))
    assert rendered.index("/review") < rendered.index("Check this branch.")
    assert rendered.index("Check this branch.") < rendered.index("Retrieved context.")


@patch("moonmind.rag.context_injection.ContextInjectionService")
async def test_claude_runtime_command_rendering_keeps_command_before_context(
    mock_service_class,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(tmp_path))
    monkeypatch.setattr("os.geteuid", lambda: 1000)

    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    mock_service = mock_service_class.return_value

    async def _inject_context(*, request, workspace_path):
        assert workspace_path == workspace
        request.instruction_ref = "Check this branch.\n\nRetrieved context."

    mock_service.inject_context = AsyncMock(side_effect=_inject_context)

    async def _fake_resolve(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.resolve_github_token_for_launch",
        _fake_resolve,
    )

    class _FakeProcess:
        def __init__(self, pid: int = 893) -> None:
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

    profile = _make_profile(
        runtime_id="claude_code",
        command_template=["claude"],
        default_model="claude-sonnet-4-6",
    )
    request = _make_request(
        instruction_ref="/review\nCheck this branch.",
        runtimeCommand=RuntimeCommandInvocation(
            kind="slash_command",
            source="leading_slash",
            sourcePath="objective.instructions",
            command="review",
            rawCommand="/review",
            args="",
            instructionBody="Check this branch.",
            targetRuntime="claude_code",
            detectionStatus="detected",
            hintStatus="hinted",
            recognitionMode="hinted_runtime_passthrough",
            requiresRuntimeRecognition=True,
            runtimeCapabilityVersion="2026-05-13",
            hintCatalogVersion="2026-05-13",
            detectionPhase="submit",
        ),
    )

    _record, process, _cleanup, _deferred_cleanup = await launcher.launch(
        run_id="run-claude-runtime-command-render",
        request=request,
        profile=profile,
        workspace_path=workspace,
    )
    await process.wait()

    prompts = [arg for arg in captured_args if isinstance(arg, str)]
    rendered = next(arg for arg in prompts if arg.startswith("/review"))
    assert rendered.index("/review") < rendered.index("Check this branch.")
    assert rendered.index("Check this branch.") < rendered.index("Retrieved context.")


async def test_unknown_runtime_command_remains_prompt_prefix_and_unmaterialized(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(tmp_path))

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

    class _FakeProcess:
        def __init__(self, pid: int = 894) -> None:
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

    profile = _make_profile(
        runtime_id="codex_cli",
        command_template=["codex", "exec"],
        default_model="gpt-5.4",
    )
    request = _make_request(
        instruction_ref="/future-command now\nUse provider command.",
        runtimeCommand=RuntimeCommandInvocation(
            kind="slash_command",
            source="leading_slash",
            sourcePath="objective.instructions",
            command="future-command",
            rawCommand="/future-command now",
            args="now",
            instructionBody="Use provider command.",
            targetRuntime="codex_cli",
            detectionStatus="detected",
            hintStatus="opaque",
            recognitionMode="runtime_passthrough",
            requiresRuntimeRecognition=True,
            runtimeCapabilityVersion="2026-05-13",
            hintCatalogVersion="2026-05-13",
            detectionPhase="submit",
        ),
    )

    _record, process, _cleanup, _deferred_cleanup = await launcher.launch(
        run_id="run-codex-unknown-runtime-command",
        request=request,
        profile=profile,
        workspace_path=workspace,
    )
    await process.wait()

    prompts = [arg for arg in captured_args if isinstance(arg, str)]
    rendered = next(arg for arg in prompts if arg.startswith("/future-command now"))
    render_metadata = request.parameters["metadata"]["moonmind"]["runtimeCommandRender"]
    assert rendered.startswith("/future-command now")
    assert render_metadata["materializedTargets"] == []


async def test_runtime_command_render_failure_prevents_launch(tmp_path) -> None:
    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)
    request = _make_request(instruction_ref="/review\nCheck this.")

    class FailingStrategy:
        runtime_id = "codex_cli"

        def render_runtime_command(self, request):
            from moonmind.schemas.agent_runtime_models import RuntimeCommandRenderResult

            return RuntimeCommandRenderResult(
                status="failed",
                failureReason="runtime_command_render_failed",
                diagnostics={"message": "redacted"},
            )

    with pytest.raises(RuntimeError, match="runtime_command_render_failed"):
        launcher._apply_runtime_command_rendering(
            request=request,
            strategy=FailingStrategy(),
        )


@pytest.mark.parametrize("transport", ["direct", "gateway"])
async def test_claude_launcher_publishes_context_artifact_reference_for_runtime_boundary(
    tmp_path,
    monkeypatch,
    transport: str,
) -> None:
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(tmp_path))
    monkeypatch.setenv("MOONMIND_RAG_AUTO_CONTEXT", "true")

    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def _fake_retrieve(self, request):
        return (
            ContextPack(
                items=[ContextItem(score=0.9, source="docs/spec.md", text="retrieved text")],
                filters={"repo": "moonmind"},
                budgets={},
                usage={"tokens": 8, "latency_ms": 4},
                transport=transport,
                context_text="Retrieved context snippet",
                retrieved_at="2026-04-24T00:00:00Z",
                telemetry_id=f"tid-{transport}",
                initiation_mode="automatic",
                truncated=False,
            ),
            None,
        )

    monkeypatch.setattr(
        "moonmind.rag.context_injection.ContextInjectionService._retrieve_context_pack",
        _fake_retrieve,
    )

    async def _fake_resolve(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.resolve_github_token_for_launch",
        _fake_resolve,
    )

    class _FakeProcess:
        def __init__(self, pid: int = 892) -> None:
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
    request = _make_request(instruction_ref="Original instruction", parameters={"publishMode": "none"})

    _record, process, _cleanup, _deferred_cleanup = await launcher.launch(
        run_id=f"run-claude-rag-{transport}",
        request=request,
        profile=profile,
        workspace_path=workspace,
    )
    await process.wait()

    moonmind_meta = request.parameters["metadata"]["moonmind"]
    artifact_ref = moonmind_meta["retrievedContextArtifactPath"]
    artifact_path = workspace / artifact_ref

    assert artifact_ref.startswith("artifacts/context/rag-context-")
    assert moonmind_meta["retrievedContextTransport"] == transport
    assert moonmind_meta["retrievedContextItemCount"] == 1
    assert moonmind_meta["retrievalInitiationMode"] == "automatic"
    assert moonmind_meta["retrievalContextTruncated"] is False
    assert artifact_path.exists()
    artifact_text = artifact_path.read_text(encoding="utf-8")
    assert f'"transport": "{transport}"' in artifact_text
    assert '"initiation_mode": "automatic"' in artifact_text
    assert '"truncated": false' in artifact_text
    # The retrieval prompt (BEGIN_RETRIEVED_CONTEXT, artifact ref, and original
    # instruction) flows through request.instruction_ref to the Claude CLI via
    # -p — not CLAUDE.md, which is project context and must not be overwritten.
    assert not (workspace / "CLAUDE.md").exists()
    prompt_args = [arg for arg in captured_args if isinstance(arg, str)]
    assert any("BEGIN_RETRIEVED_CONTEXT" in arg for arg in prompt_args)
    assert any(
        "Retrieved context artifact: artifacts/context/" in arg for arg in prompt_args
    )
    assert any("Original instruction" in arg for arg in prompt_args)


async def test_claude_launcher_marks_local_fallback_as_degraded_retrieval(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(tmp_path))
    monkeypatch.setenv("MOONMIND_RAG_AUTO_CONTEXT", "true")

    store = ManagedRunStore(tmp_path)
    launcher = ManagedRuntimeLauncher(store)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def _fake_retrieve(self, request):
        return (None, "collection_unavailable")

    def _fake_local_fallback(self, *, instruction, workspace_path):
        return ContextPack(
            items=[ContextItem(score=1.0, source="docs/spec.md", text="fallback text")],
            filters={"mode": "local_fallback"},
            budgets={},
            usage={"matches": 1},
            transport="local_fallback",
            context_text="### Retrieved Context\n1. docs/spec.md\n    fallback text",
            retrieved_at="2026-04-24T00:00:00Z",
            telemetry_id="tid-local-fallback",
            initiation_mode="automatic",
            truncated=False,
        )

    monkeypatch.setattr(
        "moonmind.rag.context_injection.ContextInjectionService._retrieve_context_pack",
        _fake_retrieve,
    )
    monkeypatch.setattr(
        "moonmind.rag.context_injection.ContextInjectionService._build_local_fallback_pack",
        _fake_local_fallback,
    )

    async def _fake_resolve(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.resolve_github_token_for_launch",
        _fake_resolve,
    )

    class _FakeProcess:
        def __init__(self, pid: int = 893) -> None:
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
    request = _make_request(instruction_ref="Original instruction", parameters={"publishMode": "none"})

    _record, process, _cleanup, _deferred_cleanup = await launcher.launch(
        run_id="run-claude-rag-local-fallback",
        request=request,
        profile=profile,
        workspace_path=workspace,
    )
    await process.wait()

    moonmind_meta = request.parameters["metadata"]["moonmind"]

    assert moonmind_meta["retrievedContextTransport"] == "local_fallback"
    assert moonmind_meta["retrievalMode"] == "degraded_local_fallback"
    assert moonmind_meta["retrievalDegradedReason"] == "collection_unavailable"
    assert moonmind_meta["retrievalInitiationMode"] == "automatic"
    assert moonmind_meta["retrievalContextTruncated"] is False
    assert not (workspace / "CLAUDE.md").exists()
    assert any(
        isinstance(arg, str) and "Retrieved context mode: degraded local fallback" in arg
        for arg in captured_args
    )
