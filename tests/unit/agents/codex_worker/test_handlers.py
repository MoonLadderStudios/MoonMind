"""Unit tests for codex worker handler logic."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from moonmind.agents.codex_worker import handlers
from moonmind.agents.codex_worker.handlers import (
    CodexExecHandler,
    CodexExecPayload,
    CodexSkillPayload,
    CodexWorkerHandlerError,
    CommandCancelledError,
    CommandResult,
)
from moonmind.rag.context_pack import ContextItem, build_context_pack

pytestmark = [pytest.mark.asyncio, pytest.mark.speckit]


@pytest.fixture(autouse=True)
def _clear_codex_runtime_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep command assertions deterministic regardless of host env defaults."""

    for key in (
        "MOONMIND_CODEX_MODEL",
        "CODEX_MODEL",
        "MOONMIND_CODEX_EFFORT",
        "CODEX_MODEL_REASONING_EFFORT",
        "MODEL_REASONING_EFFORT",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MOONMIND_RAG_AUTO_CONTEXT", "0")


async def test_codex_exec_payload_requires_repository_and_instruction() -> None:
    """Required payload fields should be enforced."""

    with pytest.raises(CodexWorkerHandlerError):
        CodexExecPayload.from_payload({"instruction": "do work"})

    with pytest.raises(CodexWorkerHandlerError):
        CodexExecPayload.from_payload({"repository": "MoonLadderStudios/MoonMind"})


async def test_codex_exec_payload_parses_codex_overrides() -> None:
    """Task-level codex overrides should parse from payload codex object."""

    payload = CodexExecPayload.from_payload(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "instruction": "run",
            "codex": {"model": "gpt-5-codex", "effort": "high"},
        }
    )

    assert payload.codex_model == "gpt-5-codex"
    assert payload.codex_effort == "high"


async def test_codex_skill_payload_defaults_and_validation() -> None:
    """Skill payload should parse defaults and enforce enum fields."""

    payload = CodexSkillPayload.from_payload(
        {
            "skillId": "speckit",
            "inputs": {"repo": "MoonLadderStudios/MoonMind"},
        }
    )
    assert payload.skill_id == "speckit"
    assert payload.repository == "MoonLadderStudios/MoonMind"
    assert payload.workdir_mode == "fresh_clone"
    assert payload.publish_mode == "none"
    assert payload.codex_model is None
    assert payload.codex_effort is None

    with pytest.raises(CodexWorkerHandlerError, match="codex_skill workdirMode"):
        CodexSkillPayload.from_payload(
            {
                "skillId": "speckit",
                "inputs": {"repo": "Moon/Mind", "workdirMode": "bad"},
            }
        )


async def test_codex_skill_payload_parses_codex_overrides() -> None:
    """codex_skill should support top-level codex overrides."""

    payload = CodexSkillPayload.from_payload(
        {
            "skillId": "speckit",
            "codex": {"model": "gpt-5-codex", "effort": "medium"},
            "inputs": {"repo": "MoonLadderStudios/MoonMind"},
        }
    )

    assert payload.codex_model == "gpt-5-codex"
    assert payload.codex_effort == "medium"


async def test_payload_rejects_non_object_codex_overrides() -> None:
    """Malformed codex override payloads should fail validation."""

    with pytest.raises(CodexWorkerHandlerError, match="codex field must be an object"):
        CodexExecPayload.from_payload(
            {
                "repository": "MoonLadderStudios/MoonMind",
                "instruction": "run",
                "codex": "gpt-5-codex",
            }
        )


async def test_to_clone_url_accepts_slug_https_and_ssh(tmp_path: Path) -> None:
    """Clone URL helper should preserve accepted token-free repository formats."""

    handler = CodexExecHandler(workdir_root=tmp_path)

    assert (
        handler._to_clone_url("MoonLadderStudios/MoonMind")
        == "https://github.com/MoonLadderStudios/MoonMind.git"
    )
    assert (
        handler._to_clone_url("https://github.com/MoonLadderStudios/MoonMind.git")
        == "https://github.com/MoonLadderStudios/MoonMind.git"
    )
    assert (
        handler._to_clone_url("git@github.com:MoonLadderStudios/MoonMind.git")
        == "git@github.com:MoonLadderStudios/MoonMind.git"
    )


async def test_to_clone_url_rejects_embedded_credentials(tmp_path: Path) -> None:
    """Tokenized repository URLs must be rejected before clone execution."""

    handler = CodexExecHandler(workdir_root=tmp_path)

    with pytest.raises(CodexWorkerHandlerError, match="embedded credentials"):
        handler._to_clone_url("https://ghp-secret@github.com/moon/repo.git")


async def test_run_command_redacts_sensitive_log_output(
    tmp_path: Path, monkeypatch
) -> None:
    """Command logs should redact configured sensitive values."""

    token = "ghp-sensitive"
    log_path = tmp_path / "log.txt"
    handler = CodexExecHandler(workdir_root=tmp_path, redaction_values=(token,))

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return (
                f"stdout {token}".encode("utf-8"),
                f"stderr {token}".encode("utf-8"),
            )

    async def fake_exec(*args, **kwargs):
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    await handler._run_command(
        ["echo", token],
        cwd=tmp_path,
        log_path=log_path,
        check=False,
    )

    text = log_path.read_text(encoding="utf-8")
    assert token not in text
    assert "[REDACTED]" in text


async def test_run_command_streaming_redacts_tokens_split_across_chunks(
    tmp_path: Path, monkeypatch
) -> None:
    """Streaming logs should redact secrets even when token text spans chunk boundaries."""

    token = "ghp-split-secret"
    log_path = tmp_path / "stream-redaction.log"
    handler = CodexExecHandler(workdir_root=tmp_path, redaction_values=(token,))

    class FakeReader:
        def __init__(self, chunks: list[str]) -> None:
            self._chunks = [chunk.encode("utf-8") for chunk in chunks]

        async def read(self, _size: int) -> bytes:
            if not self._chunks:
                return b""
            return self._chunks.pop(0)

    class FakeProcess:
        def __init__(self) -> None:
            self.returncode = 0
            self.stdout = FakeReader([f"token:{token[:7]}", f"{token[7:]}\n"])
            self.stderr = FakeReader([])

        async def wait(self) -> int:
            return self.returncode

        def terminate(self) -> None:
            return None

        def kill(self) -> None:
            return None

    async def fake_exec(*args, **kwargs):
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    await handler._run_command(
        ["echo", "stream"],
        cwd=tmp_path,
        log_path=log_path,
        check=False,
    )

    text = log_path.read_text(encoding="utf-8")
    assert token not in text
    assert "[REDACTED]" in text


async def test_run_command_cancellation_reaps_subprocess(
    tmp_path: Path, monkeypatch
) -> None:
    """Cancellation should terminate and reap child processes."""

    handler = CodexExecHandler(workdir_root=tmp_path)
    log_path = tmp_path / "cancel.log"
    started = asyncio.Event()

    class FakeProcess:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self.terminated = False
            self.killed = False
            self.waited = False

        async def communicate(self):
            started.set()
            await asyncio.sleep(3600)
            return (b"", b"")

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

        async def wait(self) -> int:
            self.waited = True
            self.returncode = -15 if self.terminated else -9
            return self.returncode

    process = FakeProcess()

    async def fake_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    task = asyncio.create_task(
        handler._run_command(
            ["sleep", "60"],
            cwd=tmp_path,
            log_path=log_path,
            check=False,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await task

    assert process.terminated is True
    assert process.waited is True


async def test_run_command_cancel_event_joins_stream_tasks(
    tmp_path: Path, monkeypatch
) -> None:
    """Streaming cancellation should cancel and join stdout/stderr reader tasks."""

    handler = CodexExecHandler(workdir_root=tmp_path)
    log_path = tmp_path / "cancel-stream.log"
    cancel_event = asyncio.Event()

    class BlockingReader:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cancelled = False

        async def read(self, _size: int) -> bytes:
            self.started.set()
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            return b""

    class FakeProcess:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self.stdout = BlockingReader()
            self.stderr = BlockingReader()
            self._wait_gate = asyncio.Event()
            self.terminated = False

        async def wait(self) -> int:
            await self._wait_gate.wait()
            return int(self.returncode or -15)

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = -15
            self._wait_gate.set()

        def kill(self) -> None:
            self.returncode = -9
            self._wait_gate.set()

    process = FakeProcess()

    async def fake_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    task = asyncio.create_task(
        handler._run_command(
            ["sleep", "60"],
            cwd=tmp_path,
            log_path=log_path,
            check=False,
            cancel_event=cancel_event,
        )
    )
    await asyncio.wait_for(process.stdout.started.wait(), timeout=1)
    await asyncio.wait_for(process.stderr.started.wait(), timeout=1)
    cancel_event.set()

    with pytest.raises(CommandCancelledError):
        _ = await task

    assert process.terminated is True
    assert process.stdout.cancelled is True
    assert process.stderr.cancelled is True


async def test_run_command_cancel_event_interrupts_subprocess(
    tmp_path: Path, monkeypatch
) -> None:
    """cancel_event should stop subprocess and raise CommandCancelledError."""

    handler = CodexExecHandler(workdir_root=tmp_path)
    log_path = tmp_path / "cancel-event.log"
    started = asyncio.Event()

    class FakeProcess:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self.terminated = False
            self.killed = False
            self.waited = False

        async def communicate(self):
            started.set()
            await asyncio.sleep(3600)
            return (b"", b"")

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

        async def wait(self) -> int:
            self.waited = True
            self.returncode = -15 if self.terminated else -9
            return self.returncode

    process = FakeProcess()

    async def fake_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    cancel_event = asyncio.Event()
    task = asyncio.create_task(
        handler._run_command(
            ["sleep", "60"],
            cwd=tmp_path,
            log_path=log_path,
            check=False,
            cancel_event=cancel_event,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    cancel_event.set()
    with pytest.raises(CommandCancelledError):
        _ = await task

    assert process.terminated is True
    assert process.waited is True


async def test_run_command_streaming_forwards_output_chunks(
    tmp_path: Path, monkeypatch
) -> None:
    """Streaming subprocess reads should forward chunk callbacks per stream."""

    handler = CodexExecHandler(workdir_root=tmp_path)
    log_path = tmp_path / "stream.log"
    callback_events: list[tuple[str, str | None]] = []

    class FakeReader:
        def __init__(self, chunks: list[str]) -> None:
            self._chunks = [chunk.encode("utf-8") for chunk in chunks]

        async def read(self, _size: int) -> bytes:
            if not self._chunks:
                return b""
            return self._chunks.pop(0)

    class FakeProcess:
        def __init__(self) -> None:
            self.returncode = 0
            self.stdout = FakeReader(["line 1\n", "line 2"])
            self.stderr = FakeReader(["warn 1\n"])

        async def wait(self) -> int:
            return self.returncode

        def terminate(self) -> None:
            return None

        def kill(self) -> None:
            return None

    async def fake_exec(*args, **kwargs):
        return FakeProcess()

    async def output_callback(stream: str, text: str | None) -> None:
        callback_events.append((stream, text))

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    result = await handler._run_command(
        ["echo", "stream"],
        cwd=tmp_path,
        log_path=log_path,
        check=False,
        output_chunk_callback=output_callback,
    )

    assert result.returncode == 0
    assert result.stdout == "line 1\nline 2"
    assert result.stderr == "warn 1\n"
    assert ("stdout", "line 1\n") in callback_events
    assert ("stdout", "line 2") in callback_events
    assert ("stderr", "warn 1\n") in callback_events
    assert ("stdout", None) in callback_events
    assert ("stderr", None) in callback_events


async def test_handler_runs_clone_exec_and_diff(tmp_path: Path) -> None:
    """Handler should run core codex_exec command sequence."""

    handler = CodexExecHandler(workdir_root=tmp_path)
    calls: list[list[str]] = []

    async def fake_run_command(
        command,
        *,
        cwd,
        log_path,
        check=True,
        env=None,
        redaction_values=(),
        cancel_event=None,
        output_chunk_callback=None,
    ):
        calls.append(list(command))
        if command[:2] == ["git", "diff"]:
            return CommandResult(tuple(command), 0, "diff --git a/file b/file\n", "")
        return CommandResult(tuple(command), 0, "", "")

    handler._run_command = fake_run_command  # type: ignore[method-assign]

    result = await handler.handle(
        job_id=uuid4(),
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "instruction": "Implement task",
            "ref": "main",
            "workdirMode": "fresh_clone",
            "publish": {"mode": "none"},
        },
    )

    assert result.succeeded is True
    assert any(cmd[:2] == ["git", "clone"] for cmd in calls)
    assert ["codex", "exec", "--sandbox", "workspace-write", "Implement task"] in calls
    assert any(cmd[:2] == ["git", "diff"] for cmd in calls)
    assert any(item.name == "logs/codex_exec.log" for item in result.artifacts)
    assert any(item.name == "patches/changes.patch" for item in result.artifacts)


async def test_handler_injects_retrieved_context_when_available(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Retrieved context should be prepended to codex instructions."""

    handler = CodexExecHandler(workdir_root=tmp_path)
    calls: list[list[str]] = []

    async def fake_run_command(
        command,
        *,
        cwd,
        log_path,
        check=True,
        env=None,
        redaction_values=(),
        cancel_event=None,
        output_chunk_callback=None,
    ):
        calls.append(list(command))
        if command[:2] == ["git", "diff"]:
            return CommandResult(tuple(command), 0, "diff --git a/file b/file\n", "")
        return CommandResult(tuple(command), 0, "", "")

    pack = build_context_pack(
        items=[ContextItem(score=0.8, source="src/rag.py", text="retrieved snippet")],
        filters={"repo": "MoonLadderStudios/MoonMind"},
        budgets={},
        usage={"tokens": 128, "latency_ms": 13},
        transport="direct",
        telemetry_id="ctx-test",
        max_chars=1200,
    )

    handler._run_command = fake_run_command  # type: ignore[method-assign]
    monkeypatch.setenv("MOONMIND_RAG_AUTO_CONTEXT", "1")
    monkeypatch.setattr(
        handler,
        "_retrieve_context_pack",
        lambda *, job_id, payload: pack,
    )

    result = await handler.handle(
        job_id=uuid4(),
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "instruction": "Implement task",
            "publish": {"mode": "none"},
        },
    )

    codex_cmd = next(cmd for cmd in calls if cmd[:2] == ["codex", "exec"])
    assert "RETRIEVED CONTEXT (MoonMind RAG):" in codex_cmd[-1]
    assert "Implement task" in codex_cmd[-1]
    assert any(item.name.startswith("context/rag-context-") for item in result.artifacts)
    assert "rag_context_items=1" in (result.summary or "")


async def test_handler_falls_back_when_retrieval_raises(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Retrieval failures should not block codex execution."""

    handler = CodexExecHandler(workdir_root=tmp_path)
    calls: list[list[str]] = []

    async def fake_run_command(
        command,
        *,
        cwd,
        log_path,
        check=True,
        env=None,
        redaction_values=(),
        cancel_event=None,
        output_chunk_callback=None,
    ):
        calls.append(list(command))
        if command[:2] == ["git", "diff"]:
            return CommandResult(tuple(command), 0, "diff --git a/file b/file\n", "")
        return CommandResult(tuple(command), 0, "", "")

    def raise_context(*, job_id, payload):
        _ = job_id, payload
        raise RuntimeError("qdrant unavailable")

    handler._run_command = fake_run_command  # type: ignore[method-assign]
    monkeypatch.setenv("MOONMIND_RAG_AUTO_CONTEXT", "1")
    monkeypatch.setattr(handler, "_retrieve_context_pack", raise_context)

    result = await handler.handle(
        job_id=uuid4(),
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "instruction": "Implement task",
            "publish": {"mode": "none"},
        },
    )

    codex_cmd = next(cmd for cmd in calls if cmd[:2] == ["codex", "exec"])
    assert codex_cmd[-1] == "Implement task"
    assert result.succeeded is True
    assert "rag_context_items=" not in (result.summary or "")

async def test_handler_applies_task_level_codex_overrides(tmp_path: Path) -> None:
    """Task payload overrides should map to codex CLI model and effort flags."""

    handler = CodexExecHandler(workdir_root=tmp_path)
    calls: list[list[str]] = []

    async def fake_run_command(
        command,
        *,
        cwd,
        log_path,
        check=True,
        env=None,
        redaction_values=(),
        cancel_event=None,
        output_chunk_callback=None,
    ):
        calls.append(list(command))
        if command[:2] == ["git", "diff"]:
            return CommandResult(tuple(command), 0, "diff --git a/file b/file\n", "")
        return CommandResult(tuple(command), 0, "", "")

    handler._run_command = fake_run_command  # type: ignore[method-assign]

    result = await handler.handle(
        job_id=uuid4(),
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "instruction": "Implement task",
            "codex": {"model": "gpt-5-codex", "effort": "high"},
            "publish": {"mode": "none"},
        },
    )

    assert result.succeeded is True
    codex_cmd = next(cmd for cmd in calls if cmd[:2] == ["codex", "exec"])
    assert "--model" in codex_cmd
    assert codex_cmd[codex_cmd.index("--model") + 1] == "gpt-5-codex"
    assert "--config" in codex_cmd
    assert codex_cmd[codex_cmd.index("--config") + 1] == 'model_reasoning_effort="high"'


async def test_handler_normalizes_codex_override_aliases(tmp_path: Path) -> None:
    """Known model/effort aliases should normalize before codex execution."""

    handler = CodexExecHandler(workdir_root=tmp_path)
    calls: list[list[str]] = []

    async def fake_run_command(
        command,
        *,
        cwd,
        log_path,
        check=True,
        env=None,
        redaction_values=(),
        cancel_event=None,
        output_chunk_callback=None,
    ):
        calls.append(list(command))
        if command[:2] == ["git", "diff"]:
            return CommandResult(tuple(command), 0, "diff --git a/file b/file\n", "")
        return CommandResult(tuple(command), 0, "", "")

    handler._run_command = fake_run_command  # type: ignore[method-assign]

    result = await handler.handle(
        job_id=uuid4(),
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "instruction": "Implement task",
            "codex": {"model": "gpt-5.3-codex-spark", "effort": "xhigh"},
            "publish": {"mode": "none"},
        },
    )

    assert result.succeeded is True
    codex_cmd = next(cmd for cmd in calls if cmd[:2] == ["codex", "exec"])
    assert "--model" in codex_cmd
    assert codex_cmd[codex_cmd.index("--model") + 1] == "gpt-5-codex"
    assert "--config" in codex_cmd
    assert codex_cmd[codex_cmd.index("--config") + 1] == 'model_reasoning_effort="high"'


async def test_handler_falls_back_to_worker_default_codex_settings(
    tmp_path: Path,
) -> None:
    """Missing task overrides should fall back to handler-level worker defaults."""

    handler = CodexExecHandler(
        workdir_root=tmp_path,
        default_codex_model="gpt-5-codex",
        default_codex_effort="medium",
    )
    calls: list[list[str]] = []

    async def fake_run_command(
        command,
        *,
        cwd,
        log_path,
        check=True,
        env=None,
        redaction_values=(),
        cancel_event=None,
        output_chunk_callback=None,
    ):
        calls.append(list(command))
        if command[:2] == ["git", "diff"]:
            return CommandResult(tuple(command), 0, "diff --git a/file b/file\n", "")
        return CommandResult(tuple(command), 0, "", "")

    handler._run_command = fake_run_command  # type: ignore[method-assign]

    result = await handler.handle(
        job_id=uuid4(),
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "instruction": "Implement task",
            "publish": {"mode": "none"},
        },
    )

    assert result.succeeded is True
    codex_cmd = next(cmd for cmd in calls if cmd[:2] == ["codex", "exec"])
    assert "--model" in codex_cmd
    assert codex_cmd[codex_cmd.index("--model") + 1] == "gpt-5-codex"
    assert "--config" in codex_cmd
    assert (
        codex_cmd[codex_cmd.index("--config") + 1] == 'model_reasoning_effort="medium"'
    )


async def test_handler_resolves_relative_workdir_for_clone_destination() -> None:
    """Relative workdir roots should be normalized before clone path assembly."""

    handler = CodexExecHandler(workdir_root=Path("var/worker-relative"))
    calls: list[list[str]] = []

    async def fake_run_command(
        command,
        *,
        cwd,
        log_path,
        check=True,
        env=None,
        redaction_values=(),
        cancel_event=None,
        output_chunk_callback=None,
    ):
        calls.append(list(command))
        if command[:2] == ["git", "diff"]:
            return CommandResult(tuple(command), 0, "diff --git a/file b/file\n", "")
        return CommandResult(tuple(command), 0, "", "")

    handler._run_command = fake_run_command  # type: ignore[method-assign]

    result = await handler.handle(
        job_id=uuid4(),
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "instruction": "touch readme",
            "workdirMode": "fresh_clone",
            "publish": {"mode": "none"},
        },
    )

    assert result.succeeded is True
    clone_cmd = next(cmd for cmd in calls if cmd[:2] == ["git", "clone"])
    assert Path(clone_cmd[-1]).is_absolute()


async def test_handler_publish_pr_invokes_gh(tmp_path: Path, monkeypatch) -> None:
    """Publish mode `pr` should invoke gh PR creation command."""

    handler = CodexExecHandler(workdir_root=tmp_path)
    calls: list[list[str]] = []

    async def fake_run_command(
        command,
        *,
        cwd,
        log_path,
        check=True,
        env=None,
        redaction_values=(),
        cancel_event=None,
        output_chunk_callback=None,
    ):
        calls.append(list(command))
        if command[:3] == ["git", "status", "--porcelain"]:
            return CommandResult(tuple(command), 0, " M changed.py\n", "")
        if command[:2] == ["git", "diff"]:
            return CommandResult(tuple(command), 0, "diff --git a/a b/a\n", "")
        return CommandResult(tuple(command), 0, "", "")

    monkeypatch.setattr(handlers, "verify_cli_is_executable", lambda _name: "gh")
    handler._run_command = fake_run_command  # type: ignore[method-assign]

    result = await handler.handle(
        job_id=uuid4(),
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "instruction": "Implement publish test",
            "publish": {"mode": "pr", "baseBranch": "main"},
        },
    )

    assert result.succeeded is True
    assert any(cmd[:3] == ["gh", "pr", "create"] for cmd in calls)


async def test_handle_skill_maps_to_exec_payload_and_marks_summary(
    tmp_path: Path,
) -> None:
    """Skill handling should map inputs to codex_exec execution payload."""

    handler = CodexExecHandler(workdir_root=tmp_path)

    async def fake_handle(
        *, job_id, payload, cancel_event=None, output_chunk_callback=None
    ):
        assert payload["repository"] == "MoonLadderStudios/MoonMind"
        assert payload["instruction"] == "run unit tests"
        assert payload["codex"]["model"] == "gpt-5-codex"
        assert payload["codex"]["effort"] == "high"
        return handlers.WorkerExecutionResult(
            succeeded=True,
            summary="codex_exec completed",
            error_message=None,
            artifacts=(),
        )

    handler.handle = fake_handle  # type: ignore[method-assign]

    result = await handler.handle_skill(
        job_id=uuid4(),
        payload={
            "skillId": "speckit",
            "inputs": {
                "repo": "MoonLadderStudios/MoonMind",
                "instruction": "run unit tests",
            },
            "codex": {"model": "gpt-5-codex", "effort": "high"},
        },
        selected_skill="speckit",
        fallback=False,
    )

    assert result.succeeded is True
    assert result.summary is not None
    assert "skill=speckit" in result.summary
    assert "executionPath=skill" in result.summary


async def test_handle_skill_requires_repository(tmp_path: Path) -> None:
    """Skills path should fail when repository context is missing."""

    handler = CodexExecHandler(workdir_root=tmp_path)

    with pytest.raises(CodexWorkerHandlerError, match="inputs.repo"):
        await handler.handle_skill(
            job_id=uuid4(),
            payload={"skillId": "custom-skill", "inputs": {"instruction": "do work"}},
            selected_skill="custom-skill",
            fallback=True,
        )


async def test_handler_publish_commit_failure_returns_failed_result(
    tmp_path: Path,
) -> None:
    """Publish flow should fail when git commit returns an error."""

    handler = CodexExecHandler(workdir_root=tmp_path)

    async def fake_run_command(
        command,
        *,
        cwd,
        log_path,
        check=True,
        env=None,
        redaction_values=(),
        cancel_event=None,
        output_chunk_callback=None,
    ):
        if command[:3] == ["git", "status", "--porcelain"]:
            return CommandResult(tuple(command), 0, " M changed.py\n", "")
        if command[:2] == ["git", "commit"]:
            raise CodexWorkerHandlerError("command failed (1): git commit -m message")
        if command[:2] == ["git", "diff"]:
            return CommandResult(tuple(command), 0, "diff --git a/a b/a\n", "")
        return CommandResult(tuple(command), 0, "", "")

    handler._run_command = fake_run_command  # type: ignore[method-assign]

    result = await handler.handle(
        job_id=uuid4(),
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "instruction": "Implement publish failure test",
            "publish": {"mode": "branch"},
        },
    )

    assert result.succeeded is False
    assert result.error_message is not None
    assert "git commit" in result.error_message


async def test_run_command_error_includes_stderr_tail(
    tmp_path: Path, monkeypatch
) -> None:
    """Failure exceptions should include stderr context for fast diagnostics."""

    handler = CodexExecHandler(workdir_root=tmp_path)
    log_path = tmp_path / "command.log"

    class FakeProcess:
        returncode = 1

        async def communicate(self):
            return (b"", b"invalid model: gpt-5.3-codex-spark\n")

    async def fake_exec(*args, **kwargs):
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(CodexWorkerHandlerError, match="invalid model"):
        await handler._run_command(
            ["codex", "exec", "--model", "gpt-5.3-codex-spark", "run task"],
            cwd=tmp_path,
            log_path=log_path,
        )


async def test_handler_invalid_payload_returns_failed_result(tmp_path: Path) -> None:
    """Handler should normalize validation failures into failed results."""

    handler = CodexExecHandler(workdir_root=tmp_path)
    result = await handler.handle(job_id=uuid4(), payload={"repository": "repo-only"})

    assert result.succeeded is False
    assert result.error_message is not None


async def test_handler_rejects_tokenized_repository_url(tmp_path: Path) -> None:
    """Credential-bearing repository URLs should fail without exposing token text."""

    token = "ghp-inline-secret"
    handler = CodexExecHandler(workdir_root=tmp_path, redaction_values=(token,))
    result = await handler.handle(
        job_id=uuid4(),
        payload={
            "repository": f"https://{token}@github.com/moon/repo.git",
            "instruction": "run",
        },
    )

    assert result.succeeded is False
    assert result.error_message is not None
    assert "embedded credentials" in result.error_message
    assert token not in result.error_message
