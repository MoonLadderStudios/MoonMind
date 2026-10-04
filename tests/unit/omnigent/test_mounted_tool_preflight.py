"""MM-1215 mounted-tool capability boundary tests."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, call

import pytest

from moonmind.omnigent.mounted_tool_preflight import (
    RATE_LIMIT_LOOKUP_COMMAND,
    MountedToolPreflightError,
    _digest_check_command,
    preflight_github_access,
    preflight_mounted_tools,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "repository",
    [
        "https://github.enterprise.test/owner/repo.git",
        "https://github.enterprise.test:443/owner/repo.git",
    ],
)
async def test_enterprise_preflight_uses_only_the_selected_trusted_host(
    monkeypatch, repository
):
    from moonmind.config.settings import settings

    monkeypatch.setattr(
        settings.github, "github_trusted_api_hosts", "github.enterprise.test"
    )
    calls = []

    async def runner(command):
        calls.append(command)
        if command.startswith("gh repo view"):
            return (
                0,
                json.dumps({"nameWithOwner": "owner/repo", "viewerPermission": "READ"}),
                "",
            )
        return 0, "", ""

    result = await preflight_github_access(
        repository=repository,
        github_host="github.enterprise.test",
        boundaries={"host": runner, "runner": runner},
    )
    assert result["status"] == "ready"
    assert sum(command.startswith("gh repo view owner/repo ") for command in calls) == 2
    assert (
        calls.count("gh auth token --hostname github.enterprise.test >/dev/null") == 2
    )
    assert not any("github.com" in command for command in calls)


@pytest.mark.asyncio
async def test_repository_access_does_not_require_unrelated_account_probe():
    calls = []

    async def runner(command):
        calls.append(command)
        if "gh auth status" in command:
            return 1, "", "account endpoint temporarily unavailable"
        if command.startswith("gh repo view"):
            return (
                0,
                json.dumps(
                    {"nameWithOwner": "owner/repo", "viewerPermission": "WRITE"}
                ),
                "",
            )
        return 0, "", ""

    result = await preflight_mounted_tools(
        required_capabilities=("gh",),
        repository="owner/repo",
        mutation_required=True,
        host_runner=runner,
        runner_runner=runner,
    )
    assert result["status"] == "ready"
    assert sum("gh repo view" in command for command in calls) == 2


@pytest.mark.asyncio
async def test_remote_timeout_is_recovered_in_place(monkeypatch):
    attempts = 0

    async def runner(command):
        nonlocal attempts
        if command.startswith("gh repo view"):
            attempts += 1
            if attempts == 1:
                raise TimeoutError()
            return (
                0,
                json.dumps({"nameWithOwner": "owner/repo", "viewerPermission": "READ"}),
                "",
            )
        return 0, "", ""

    monkeypatch.setattr(
        "moonmind.omnigent.mounted_tool_preflight.asyncio.sleep", AsyncMock()
    )
    result = await preflight_mounted_tools(
        required_capabilities=("gh",),
        repository="owner/repo",
        mutation_required=False,
        host_runner=runner,
        runner_runner=runner,
    )
    assert attempts == 3
    assert result["probes"][4]["error"] == "command timed out"


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
            return (
                0,
                json.dumps(
                    {"nameWithOwner": "owner/repo", "viewerPermission": "WRITE"}
                ),
                "",
            )

        return runner

    result = await preflight_mounted_tools(
        required_capabilities=("gh",),
        repository="https://github.com/owner/repo.git",
        mutation_required=True,
        host_runner=make_runner("host"),
        runner_runner=make_runner("runner"),
    )

    assert result["status"] == "ready"
    assert [boundary for boundary, _ in calls] == ["host"] * 5 + ["runner"] * 5
    assert any("command -v gh" in command for _, command in calls)
    assert any(
        "gh auth token --hostname github.com >/dev/null" == command
        for _, command in calls
    )
    assert any("viewerPermission" in command for _, command in calls)
    permission_commands = [command for _, command in calls if "gh repo view" in command]
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
        return (
            0,
            json.dumps({"nameWithOwner": "owner/repo", "viewerPermission": "WRITE"}),
            "",
        )

    async def runner_runner(command: str) -> tuple[int, str, str]:
        if command.startswith("gh auth token"):
            return 1, "", "Authorization: Bearer ghp_abcdefghijklmnopqrstuvwxyz123456"
        return (
            0,
            json.dumps({"nameWithOwner": "owner/repo", "viewerPermission": "WRITE"}),
            "",
        )

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
    assert sleep.await_count == 0
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
        return (
            0,
            json.dumps({"nameWithOwner": "owner/repo", "viewerPermission": "WRITE"}),
            "",
        )

    async def runner_runner(_command: str) -> tuple[int, str, str]:
        return (
            0,
            json.dumps({"nameWithOwner": "owner/repo", "viewerPermission": "WRITE"}),
            "",
        )

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


@pytest.mark.parametrize(
    "payload,mutation",
    [
        ({"nameWithOwner": "other/repo", "viewerPermission": "ADMIN"}, False),
        ({"nameWithOwner": "owner/repo", "viewerPermission": "READ"}, True),
        ({}, False),
    ],
)
@pytest.mark.asyncio
async def test_repository_identity_and_requested_permission_fail_closed(
    payload, mutation
):
    runner = AsyncMock(side_effect=[(0, "", ""), (0, json.dumps(payload), "")])
    with pytest.raises(
        MountedToolPreflightError, match="matching repository|write permission"
    ):
        await preflight_github_access(
            repository="owner/repo",
            boundaries={"runner": runner},
            mutation_required=mutation,
        )
    assert runner.await_count == 2


@pytest.mark.parametrize(
    "error",
    [
        "HTTP 401: Bad credentials",
        "HTTP 403: Forbidden",
        "HTTP 404: Not Found",
    ],
)
@pytest.mark.asyncio
async def test_permanent_rejection_fails_closed_without_retry(error, monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("moonmind.omnigent.mounted_tool_preflight.asyncio.sleep", sleep)
    runner = AsyncMock(side_effect=[(0, "", ""), (1, "", error)])
    with pytest.raises(MountedToolPreflightError, match=error) as raised:
        await preflight_github_access(
            repository="owner/repo", boundaries={"runner": runner}
        )
    sleep.assert_not_awaited()
    assert raised.value.transient is False


def _headers(status: int, resource: str, remaining: int, reset: int, **extra) -> str:
    lines = [
        f"HTTP/2.0 {status} {'OK' if status == 200 else 'Forbidden'}",
        f"X-Ratelimit-Remaining: {remaining}",
        f"X-Ratelimit-Reset: {reset}",
        f"X-Ratelimit-Resource: {resource}",
        *(f"{name.replace('_', '-')}: {value}" for name, value in extra.items()),
    ]
    return "\n".join(lines) + "\n\n"


def _rate_limited_runner(*, limited_responses: int, lookup_headers: str):
    calls: list[str] = []

    async def runner(command: str) -> tuple[int, str, str]:
        calls.append(command)
        if command.startswith("gh auth token"):
            return 0, "", ""
        if command == RATE_LIMIT_LOOKUP_COMMAND:
            return 0, lookup_headers, ""
        if sum(item.startswith("gh repo view") for item in calls) <= limited_responses:
            return 1, "", "GraphQL: API rate limit exceeded for user ID 16808547."
        return (
            0,
            json.dumps({"nameWithOwner": "owner/repo", "viewerPermission": "WRITE"}),
            "",
        )

    return runner, calls


@pytest.mark.asyncio
async def test_rate_limit_waits_for_reported_reset_then_recovers_in_place(
    monkeypatch,
):
    sleep = AsyncMock()
    monkeypatch.setattr("moonmind.omnigent.mounted_tool_preflight.asyncio.sleep", sleep)
    monkeypatch.setattr(
        "moonmind.omnigent.mounted_tool_preflight.time.time", lambda: 1_000.0
    )
    runner, calls = _rate_limited_runner(
        limited_responses=1,
        # /rate_limit can report an untouched budget while requests are
        # rejected, so the reset comes from real response headers.
        lookup_headers=_headers(200, "core", 4100, 4_000)
        + _headers(403, "graphql", 0, 1_090),
    )

    result = await preflight_github_access(
        repository="owner/repo", boundaries={"host": runner}
    )

    assert result["status"] == "ready"
    # Wait for the exhausted resource's reported reset, not a tight loop and
    # not the unrelated core window.
    sleep.assert_awaited_once_with(91.0)
    assert calls.count(RATE_LIMIT_LOOKUP_COMMAND) == 1
    assert not any(call.startswith("gh api rate_limit") for call in calls)
    limited = [item for item in result["probes"] if item["status"] == "rate_limited"]
    assert len(limited) == 1
    assert limited[0]["error"].startswith("GraphQL: API rate limit exceeded")
    assert limited[0]["retryAfterSeconds"] == 91.0


@pytest.mark.asyncio
async def test_rate_limit_without_reported_reset_waits_bounded_default(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("moonmind.omnigent.mounted_tool_preflight.asyncio.sleep", sleep)
    # A secondary rate limit leaves every primary resource with budget left.
    runner, _calls = _rate_limited_runner(
        limited_responses=1,
        lookup_headers=_headers(200, "core", 4100, 4_000)
        + _headers(200, "graphql", 12, 1),
    )

    result = await preflight_github_access(
        repository="owner/repo", boundaries={"host": runner}
    )

    assert result["status"] == "ready"
    sleep.assert_awaited_once_with(60.0)


@pytest.mark.asyncio
async def test_secondary_rate_limit_waits_for_retry_after(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("moonmind.omnigent.mounted_tool_preflight.asyncio.sleep", sleep)
    runner, _calls = _rate_limited_runner(
        limited_responses=1,
        lookup_headers=_headers(403, "core", 4100, 4_000, Retry_After=120),
    )

    result = await preflight_github_access(
        repository="owner/repo", boundaries={"host": runner}
    )

    assert result["status"] == "ready"
    sleep.assert_awaited_once_with(120.0)


@pytest.mark.asyncio
async def test_persistent_rate_limit_is_reported_as_transient_with_original_error(
    monkeypatch,
):
    sleep = AsyncMock()
    monkeypatch.setattr("moonmind.omnigent.mounted_tool_preflight.asyncio.sleep", sleep)
    monkeypatch.setattr(
        "moonmind.omnigent.mounted_tool_preflight.time.time", lambda: 1_000.0
    )
    runner, _calls = _rate_limited_runner(
        limited_responses=100,
        # A nonsensical far-future reset is still bounded by the cap.
        lookup_headers=_headers(403, "core", 0, 99_999),
    )

    with pytest.raises(MountedToolPreflightError) as raised:
        await preflight_github_access(
            repository="owner/repo", boundaries={"host": runner}
        )

    assert raised.value.code == "github_rate_limited"
    assert raised.value.transient is True
    assert "API rate limit exceeded" in str(raised.value)
    # One bounded in-place wait, well inside the launch activity's budget;
    # longer outages are left to the parent's fresh Step Execution.
    sleep.assert_awaited_once_with(900.0)


@pytest.mark.asyncio
async def test_transient_exhaustion_retains_all_redacted_attempts(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("moonmind.omnigent.mounted_tool_preflight.asyncio.sleep", sleep)

    async def runner(command):
        if command.startswith("gh auth token"):
            return 0, "", ""
        return (
            1,
            "",
            "HTTP 503: unavailable Authorization: Bearer ghp_abcdefghijklmnopqrstuvwxyz123456",
        )

    with pytest.raises(MountedToolPreflightError, match="after 4 attempt") as raised:
        await preflight_github_access(
            repository="owner/repo", boundaries={"opencode_shell": runner}
        )
    assert len(raised.value.evidence["probes"]) == 5
    assert sleep.await_count == 3
    assert raised.value.transient is True
    assert "ghp_abcdefghijklmnopqrstuvwxyz123456" not in str(raised.value)
    assert "ghp_abcdefghijklmnopqrstuvwxyz123456" not in str(raised.value.evidence)


@pytest.mark.asyncio
async def test_repository_free_projection_only_checks_local_credential():
    runner = AsyncMock(return_value=(0, "", ""))
    result = await preflight_github_access(repository="", boundaries={"host": runner})
    runner.assert_awaited_once_with("gh auth token --hostname github.com >/dev/null")
    assert all(item["probe"] == "authentication" for item in result["probes"])


@pytest.mark.asyncio
async def test_cancellation_is_not_retried(monkeypatch):
    import asyncio

    sleep = AsyncMock()
    monkeypatch.setattr("moonmind.omnigent.mounted_tool_preflight.asyncio.sleep", sleep)
    runner = AsyncMock(side_effect=[(0, "", ""), asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        await preflight_github_access(
            repository="owner/repo", boundaries={"runner": runner}
        )
    sleep.assert_not_awaited()
