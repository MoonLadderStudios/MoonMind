import re

with open("tests/unit/agents/codex_worker/test_worker.py", "r") as f:
    content = f.read()

fixture = """
@pytest.fixture
def publish_stage_test_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    queue = FakeQueueClient(jobs=[])
    handler = FakeHandler(
        WorkerExecutionResult(succeeded=True, summary="unused", error_message=None)
    )
    worker = CodexWorker(
        config=CodexWorkerConfig(
            moonmind_url="http://localhost:5000",
            worker_id="worker-1",
            worker_token=None,
            poll_interval_ms=1500,
            lease_seconds=120,
            workdir=tmp_path,
        ),
        queue_client=queue,
        codex_exec_handler=handler,
    )  # type: ignore[arg-type]

    run_calls: list[tuple[str, ...]] = []

    async def _capture_stage_command(
        command,
        *,
        cwd,
        log_path,
        check=True,
        env=None,
        redaction_values=(),
        cancel_event=None,
    ):
        _ = (cwd, log_path, check, env, redaction_values, cancel_event)
        command_tuple = tuple(str(part) for part in command)
        run_calls.append(command_tuple)
        if command_tuple[:2] == ("git", "status"):
            return CommandResult(command_tuple, 0, " M worker.py\\n", "")
        if command_tuple[:3] == ("gh", "pr", "create"):
            return CommandResult(
                command_tuple,
                0,
                "https://github.com/MoonLadderStudios/MoonMind/pull/111\\n",
                "",
            )
        return CommandResult(command_tuple, 0, "", "")

    monkeypatch.setattr(worker, "_run_stage_command", _capture_stage_command)
    return worker, queue, run_calls
"""

pattern = re.compile(
    r"""
    queue = FakeQueueClient\(jobs=\[\]\)
    handler = FakeHandler\(
        WorkerExecutionResult\(succeeded=True, summary="unused", error_message=None\)
    \)
    worker = CodexWorker\(
        config=CodexWorkerConfig\(
            moonmind_url="http://localhost:5000",
            worker_id="worker-1",
            worker_token=None,
            poll_interval_ms=1500,
            lease_seconds=120,
            workdir=tmp_path,
        \),
        queue_client=queue,
        codex_exec_handler=handler,
    \)  \# type: ignore\[arg-type\]

    run_calls: list\[tuple\[str, \.\.\.\]\] = \[\]

    async def _capture_stage_command\(
        command,
        \*,
        cwd,
        log_path,
        check=True,
        env=None,
        redaction_values=\(\),
        cancel_event=None,
    \):
        _ = \(cwd, log_path, check, env, redaction_values, cancel_event\)
        command_tuple = tuple\(str\(part\) for part in command\)
        run_calls.append\(command_tuple\)
        if command_tuple\[:2\] == \("git", "status"\):
            return CommandResult\(command_tuple, 0, " M worker\.py\\n", ""\)
        if command_tuple\[:3\] == \("gh", "pr", "create"\):
            return CommandResult\(
                command_tuple,
                0,
                "https://github.com/MoonLadderStudios/MoonMind/pull/111\\n",
                "",
            \)
        return CommandResult\(command_tuple, 0, "", ""\)

    monkeypatch\.setattr\(worker, "_run_stage_command", _capture_stage_command\)
""",
    re.VERBOSE,
)

# Insert the fixture before the first test that uses it
idx = content.find(
    "async def test_run_publish_stage_pr_mode_resolves_missing_pr_base_branch_with_warning"
)
content = content[:idx] + fixture + "\n\n" + content[idx:]

# Replace the setup in tests with fixture usage
content = re.sub(
    r"async def test_run_publish_stage_pr_mode_resolves_missing_pr_base_branch_with_warning\(\n    tmp_path: Path,\n    monkeypatch: pytest\.MonkeyPatch,\n\) -> None:",
    r"async def test_run_publish_stage_pr_mode_resolves_missing_pr_base_branch_with_warning(\n    tmp_path: Path,\n    publish_stage_test_setup,\n) -> None:",
    content,
)
content = re.sub(
    r"async def test_run_publish_stage_pr_mode_rejects_base_equal_head_and_falls_back\(\n    tmp_path: Path,\n    monkeypatch: pytest\.MonkeyPatch,\n\) -> None:",
    r"async def test_run_publish_stage_pr_mode_rejects_base_equal_head_and_falls_back(\n    tmp_path: Path,\n    publish_stage_test_setup,\n) -> None:",
    content,
)
content = re.sub(
    r"async def test_run_publish_stage_pr_mode_fails_fast_when_no_valid_base_branch\(\n    tmp_path: Path,\n    monkeypatch: pytest\.MonkeyPatch,\n\) -> None:",
    r"async def test_run_publish_stage_pr_mode_fails_fast_when_no_valid_base_branch(\n    tmp_path: Path,\n    publish_stage_test_setup,\n) -> None:",
    content,
)

content = pattern.sub(
    "    worker, queue, run_calls = publish_stage_test_setup\n", content
)

with open("tests/unit/agents/codex_worker/test_worker.py", "w") as f:
    f.write(content)
