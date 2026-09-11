"""MoonLadderStudios/MoonMind#4226: container CLI has a documented bounded wait."""

import pytest

from moonmind.container_job_cli import (
    ContainerJobCliError,
    run_container_job,
    run_python_tests,
)

_ENV = {
    "MOONMIND_AGENT_RUN_ID": "run-4226",
    "MOONMIND_CONTAINER_JOBS_BEARER_TOKEN": "token-4226",
    "MOONMIND_MANAGED_WORKSPACE": "/workspace",
    "MOONMIND_CONTAINER_JOBS_MCP_URL": "http://localhost:9999/mcp",
}


class _NeverTerminalClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, tool: str, arguments: dict) -> dict:
        self.calls.append((tool, dict(arguments)))
        if tool == "container.submit":
            return {"jobId": "job-never-terminal"}
        return {"state": "running"}

    def close(self) -> None:
        return None


def test_container_job_cli_wait_is_bounded_not_indefinite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A never-terminal job (the #4226 idle-host signature) raises promptly."""

    client = _NeverTerminalClient()
    # Force the deadline to expire immediately without sleeping the suite.
    monkeypatch.setattr("moonmind.container_job_cli.time.monotonic", lambda: 10**9)

    with pytest.raises(ContainerJobCliError, match="did not reach a terminal"):
        run_container_job(
            {
                "imageSourceRef": "moonmind-python-tests",
                "command": ["true"],
                "resources": {"cpuMillis": 1000, "memoryMiB": 512},
                "timeoutSeconds": 60,
            },
            env=_ENV,
            request_id="req-4226",
            poll_seconds=0.001,
            client=client,  # type: ignore[arg-type]
        )
    assert client.calls[0][0] == "container.submit"


def test_python_tests_default_timeout_is_bounded() -> None:
    import inspect

    assert inspect.signature(run_python_tests).parameters[
        "timeout_seconds"
    ].default == 3600
