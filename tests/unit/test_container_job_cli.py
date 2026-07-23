from __future__ import annotations

import httpx
import pytest
from typer.testing import CliRunner

from moonmind.cli import app
from moonmind.container_job_cli import (
    ContainerJobMcpClient,
    ContainerJobCliError,
    python_test_submission,
    run_python_tests,
)


_ENV = {
    "MOONMIND_URL": "http://api:8000",
    "MOONMIND_AGENT_RUN_ID": "mm:run-1",
    "MOONMIND_RUNTIME_ID": "codex_cli",
    "MOONMIND_TASK_WORKFLOW_ID": "mm:run-1",
}


def test_python_test_cli_accepts_optional_variadic_targets() -> None:
    result = CliRunner().invoke(app, ["container", "python-tests", "--help"])

    assert result.exit_code == 0
    assert "[targets]..." in result.stdout.lower()


class _FakeClient:
    def __init__(self, states: list[str]) -> None:
        self.states = states
        self.calls: list[tuple[str, dict]] = []

    def call(self, tool: str, arguments: dict) -> dict:
        self.calls.append((tool, arguments))
        if tool == "container.submit":
            return {"jobId": "container-job:" + "1" * 32, "state": "queued"}
        if tool == "container.logs":
            return {
                "jobId": "container-job:" + "1" * 32,
                "entries": [
                    {"sequence": 1, "stream": "stdout", "text": "3 passed"}
                ],
                "nextCursor": None,
            }
        state = self.states.pop(0)
        return {
            "jobId": "container-job:" + "1" * 32,
            "state": state,
            "terminal": {"exitCode": 0} if state == "succeeded" else None,
            "logsRef": "artifact:logs" if state == "succeeded" else None,
            "artifactsRef": "artifact:outputs" if state == "succeeded" else None,
        }

    def close(self) -> None:
        pass


def test_python_test_submission_uses_canonical_managed_workspace_and_safe_argv(
) -> None:
    payload = python_test_submission(
        ["tests/unit/test_one.py", "node; touch /tmp/not-run"], env=_ENV
    )

    assert payload["source"]["source"] == "managed_session"
    assert payload["spec"]["workspaceRef"] == {
        "kind": "managed_runtime",
        "runtimeId": "codex_cli",
        "agentRunId": "mm:run-1",
        "relativePath": "repo",
    }
    assert payload["spec"]["imageSourceRef"] == "moonmind-python-tests"
    assert "image" not in payload["spec"]
    assert "pullPolicy" not in payload["spec"]
    assert payload["spec"]["environment"] == [
        {"name": "MOONMIND_FORCE_LOCAL_TESTS", "value": "1"},
        {
            "name": "MOONMIND_PYTEST_JUNITXML",
            "value": "artifacts/pytest-unit.xml",
        },
        {"name": "PYTHONPATH", "value": "/workspace"},
    ]
    assert payload["spec"]["command"][-2:] == [
        "tests/unit/test_one.py",
        "node; touch /tmp/not-run",
    ]
    assert '"$@"' in payload["spec"]["command"][2]


def test_python_test_submission_requires_managed_runtime_identity() -> None:
    with pytest.raises(ContainerJobCliError, match="MOONMIND_AGENT_RUN_ID"):
        python_test_submission([], env={"MOONMIND_RUNTIME_ID": "codex_cli"})


def test_python_test_submission_does_not_fall_back_from_explicit_empty_env() -> None:
    with pytest.raises(ContainerJobCliError, match="MOONMIND_AGENT_RUN_ID"):
        python_test_submission([], env={})


def test_mcp_client_retries_ambiguous_transport_failure_with_same_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    monkeypatch.setattr("moonmind.container_job_cli.time.sleep", lambda _seconds: None)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) < 3:
            raise httpx.ReadTimeout("ambiguous timeout", request=request)
        return httpx.Response(200, json={"result": {"jobId": "job-1"}})

    client = ContainerJobMcpClient(
        endpoint="http://api:8000/mcp",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = client.call("container.submit", {"idempotencyKey": "stable-key"})
    finally:
        client.close()

    assert result == {"jobId": "job-1"}
    assert len(requests) == 3
    assert len({request.content for request in requests}) == 1


def test_mcp_client_retries_transient_server_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    monkeypatch.setattr("moonmind.container_job_cli.time.sleep", lambda _seconds: None)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) < 3:
            return httpx.Response(503, json={"detail": "temporarily unavailable"})
        return httpx.Response(200, json={"result": {"jobId": "job-1"}})

    client = ContainerJobMcpClient(
        endpoint="http://api:8000/mcp",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = client.call("container.submit", {"idempotencyKey": "stable-key"})
    finally:
        client.close()

    assert result == {"jobId": "job-1"}
    assert len(requests) == 3
    assert len({request.content for request in requests}) == 1


def test_mcp_client_sends_configured_bearer_token() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"result": {"jobId": "job-1"}})

    client = ContainerJobMcpClient(
        endpoint="http://api:8000/mcp",
        bearer_token="scoped-session-token",
        transport=httpx.MockTransport(handler),
    )
    try:
        client.call("container.submit", {"idempotencyKey": "stable-key"})
    finally:
        client.close()

    assert requests[0].headers["authorization"] == "Bearer scoped-session-token"


def test_run_python_tests_passes_scoped_bearer_token_to_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str | None] = {}
    fake_client = _FakeClient(["succeeded"])

    def client_factory(*, endpoint: str, bearer_token: str | None):
        captured["endpoint"] = endpoint
        captured["bearer_token"] = bearer_token
        return fake_client

    monkeypatch.setattr(
        "moonmind.container_job_cli.ContainerJobMcpClient", client_factory
    )

    result = run_python_tests(
        [],
        env={**_ENV, "MOONMIND_CONTAINER_JOBS_BEARER_TOKEN": "scoped-token"},
        poll_seconds=0.001,
    )

    assert result.state == "succeeded"
    assert captured == {
        "endpoint": "http://api:8000/mcp",
        "bearer_token": "scoped-token",
    }


def test_run_python_tests_polls_to_authoritative_terminal_evidence() -> None:
    client = _FakeClient(["queued", "running", "succeeded"])

    result = run_python_tests(
        [], env=_ENV, poll_seconds=0.001, client=client  # type: ignore[arg-type]
    )

    assert result.state == "succeeded"
    assert result.exit_code == 0
    assert result.logs_ref == "artifact:logs"
    assert result.artifacts_ref == "artifact:outputs"
    assert result.log_tail == ("[stdout] 3 passed",)
    assert [tool for tool, _arguments in client.calls] == [
        "container.submit",
        "container.status",
        "container.status",
        "container.status",
        "container.logs",
    ]
