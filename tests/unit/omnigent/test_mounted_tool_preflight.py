"""MM-1215 mounted-tool capability boundary tests."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, call

import pytest

from moonmind.omnigent.mounted_tool_preflight import (
    _digest_check_command,
    MountedToolPreflightError,
    preflight_mounted_tools,
)


def test_digest_probe_executes_against_mounted_executable(tmp_path: Path) -> None:
    executable = tmp_path / "mounted tools/gh"
    executable.parent.mkdir()
    executable.write_bytes(b"pinned gh executable fixture")
    trusted_digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    command = _digest_check_command(str(executable), [trusted_digest])

    assert subprocess.run(["bash", "-lc", command], check=False).returncode == 0

    executable.write_bytes(b"different executable")
    assert subprocess.run(["bash", "-lc", command], check=False).returncode != 0


@pytest.mark.asyncio
async def test_optional_gh_absence_does_not_probe_or_unhealthy_host() -> None:
    calls: list[str] = []

    async def runner(command: str) -> tuple[int, str, str]:
        calls.append(command)
        return 127, "", "missing"

    result = await preflight_mounted_tools(
        required_capabilities=("git",),
        repository="owner/repo",
        mutation_required=False,
        host_runner=runner,
        runner_runner=runner,
    )

    assert result == {"status": "not_required", "boundaries": []}
    assert calls == []


@pytest.mark.asyncio
async def test_gh_probes_host_and_exact_runner_with_mutation_permission() -> None:
    calls: list[tuple[str, str]] = []

    def make_runner(boundary: str):
        async def runner(command: str) -> tuple[int, str, str]:
            calls.append((boundary, command))
            return 0, "ok", ""

        return runner

    result = await preflight_mounted_tools(
        required_capabilities=("gh",),
        repository="https://github.com/owner/repo.git",
        mutation_required=True,
        host_runner=make_runner("host"),
        runner_runner=make_runner("runner"),
    )

    assert result["status"] == "ready"
    assert [boundary for boundary, _ in calls] == ["host"] * 6 + ["runner"] * 6
    assert any("command -v gh" in command for _, command in calls)
    assert any("gh auth status" in command for _, command in calls)
    assert any("viewerPermission" in command for _, command in calls)
    permission_commands = [command for _, command in calls if "--jq .viewerPermission" in command]
    assert len(permission_commands) == 2
    assert all(command.count("gh repo view") == 1 for command in permission_commands)


@pytest.mark.parametrize(
    "repository",
    (
        "https://evil.example/github.com/owner/repo",
        "https://github.com.evil.example/owner/repo",
    ),
)
@pytest.mark.asyncio
async def test_repository_parser_rejects_embedded_github_hostname(repository: str) -> None:
    async def runner(_command: str) -> tuple[int, str, str]:
        return 0, "", ""

    with pytest.raises(MountedToolPreflightError) as raised:
        await preflight_mounted_tools(
            required_capabilities=("gh",),
            repository=repository,
            mutation_required=False,
            host_runner=runner,
            runner_runner=runner,
        )

    assert raised.value.code == "github_repository_unauthorized"


@pytest.mark.asyncio
async def test_runner_auth_failure_is_stable_bounded_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def host_runner(_command: str) -> tuple[int, str, str]:
        return 0, "ok", ""

    async def runner_runner(command: str) -> tuple[int, str, str]:
        if command == "gh auth status":
            return 1, "", "Authorization: Bearer ghp_abcdefghijklmnopqrstuvwxyz123456"
        return 0, "ok", ""

    sleep = AsyncMock()
    monkeypatch.setattr(
        "moonmind.omnigent.mounted_tool_preflight.asyncio.sleep", sleep
    )
    with pytest.raises(MountedToolPreflightError) as raised:
        await preflight_mounted_tools(
            required_capabilities=("gh",),
            repository="owner/repo",
            mutation_required=False,
            host_runner=host_runner,
            runner_runner=runner_runner,
        )

    assert raised.value.code == "github_auth_unavailable"
    assert sleep.await_count == 3
    serialized = str(raised.value.evidence)
    assert "ghp_abcdefghijklmnopqrstuvwxyz123456" not in serialized
    assert len(serialized) < 4096


@pytest.mark.asyncio
async def test_remote_probe_recovers_without_replacing_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_attempts = 0

    async def host_runner(command: str) -> tuple[int, str, str]:
        nonlocal repository_attempts
        if command.startswith("gh repo view"):
            repository_attempts += 1
            if repository_attempts < 3:
                return 1, "", "temporary provider connection failure"
        return 0, "ok", ""

    async def runner_runner(_command: str) -> tuple[int, str, str]:
        return 0, "ok", ""

    sleep = AsyncMock()
    monkeypatch.setattr(
        "moonmind.omnigent.mounted_tool_preflight.asyncio.sleep", sleep
    )

    result = await preflight_mounted_tools(
        required_capabilities=("gh",),
        repository="owner/repo",
        mutation_required=False,
        host_runner=host_runner,
        runner_runner=runner_runner,
    )

    assert result["status"] == "ready"
    assert repository_attempts == 3
    assert sleep.await_args_list == [call(1.0), call(2.0)]
    repository_evidence = [
        item
        for item in result["probes"]
        if item["boundary"] == "host" and item["probe"] == "repository_access"
    ]
    assert [item["status"] for item in repository_evidence] == [
        "failed",
        "failed",
        "ready",
    ]
