from __future__ import annotations

import httpx
import pytest
from click import unstyle
from typer.testing import CliRunner

from moonmind.cli import app
from moonmind.container_job_cli import (
    ContainerJobMcpClient,
    ContainerJobCliError,
    ContainerJobResult,
    container_job_submission,
    load_container_job_spec,
    python_test_submission,
    run_container_job,
    run_python_tests,
)


_ENV = {
    "MOONMIND_URL": "http://api:8000",
    "MOONMIND_AGENT_RUN_ID": "mm:run-1",
    "MOONMIND_RUNTIME_ID": "codex_cli",
    "MOONMIND_TASK_WORKFLOW_ID": "mm:run-1",
    "MOONMIND_CONTAINER_JOBS_SESSION_ID": "sess-1",
}


def test_python_test_cli_accepts_optional_variadic_targets() -> None:
    result = CliRunner().invoke(app, ["container", "python-tests", "--help"])

    assert result.exit_code == 0
    assert "[targets]..." in result.stdout.lower()


def test_generic_container_cli_requires_a_spec_file() -> None:
    result = CliRunner().invoke(app, ["container", "run"])

    assert result.exit_code == 2
    assert "Missing option '--spec'." in unstyle(result.output)


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
            "terminal": (
                {"exitCode": 0}
                if state == "succeeded"
                else {
                    "failureClass": "image_not_found",
                    "message": "deployment image source is absent",
                }
                if state == "failed"
                else None
            ),
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


def test_generic_submission_injects_authoritative_identity_and_workspace() -> None:
    payload = container_job_submission(
        {
            "imageSourceRef": "tactics-unreal",
            "workspaceRef": {
                "kind": "external_state",
                "artifactRef": "attacker-selected",
            },
            "command": ["true"],
            "resources": {"cpuMillis": 1000, "memoryMiB": 512},
        },
        env={**_ENV, "MOONMIND_CONTAINER_JOBS_SESSION_ID": "sess-1"},
        request_id="build-1",
    )

    assert payload["idempotencyKey"] == "container-run:mm:run-1:build-1"
    assert payload["source"]["managedSessionId"] == "sess-1"
    assert payload["spec"]["workspaceRef"] == {
        "kind": "managed_runtime",
        "runtimeId": "codex_cli",
        "agentRunId": "mm:run-1",
        "relativePath": "repo",
    }


def test_python_test_submission_uses_omnigent_sandbox_authority() -> None:
    payload = python_test_submission(
        ["tests/unit/test_one.py"],
        env={
            **_ENV,
            "MOONMIND_CONTAINER_JOBS_SOURCE_KIND": "omnigent",
            "MOONMIND_CONTAINER_JOBS_SESSION_ID": "host-lease-1",
            "MOONMIND_CONTAINER_JOBS_WORKSPACE_KIND": "sandbox",
            "MOONMIND_CONTAINER_JOBS_WORKSPACE_ID": "sandbox-1",
            "MOONMIND_CONTAINER_JOBS_WORKSPACE_RELATIVE_PATH": ".",
        },
    )

    assert payload["source"] == {
        "source": "omnigent",
        "workflowId": "mm:run-1",
        "agentRunId": "mm:run-1",
        "omnigentConversationId": "host-lease-1",
    }
    assert payload["spec"]["workspaceRef"] == {
        "kind": "sandbox",
        "workspaceId": "sandbox-1",
        "relativePath": ".",
    }


def test_generic_submission_hashes_the_full_long_idempotency_identity() -> None:
    long_run_id = "run-" + "a" * 240
    common_prefix = "request-" + "b" * 260

    first = container_job_submission(
        {
            "imageSourceRef": "tactics-unreal",
            "command": ["true"],
            "resources": {"cpuMillis": 1000, "memoryMiB": 512},
        },
        env={
            **_ENV,
            "MOONMIND_AGENT_RUN_ID": long_run_id,
            "MOONMIND_TASK_WORKFLOW_ID": long_run_id,
        },
        request_id=common_prefix + "one",
    )
    second = container_job_submission(
        {
            "imageSourceRef": "tactics-unreal",
            "command": ["true"],
            "resources": {"cpuMillis": 1000, "memoryMiB": 512},
        },
        env={
            **_ENV,
            "MOONMIND_AGENT_RUN_ID": long_run_id,
            "MOONMIND_TASK_WORKFLOW_ID": long_run_id,
        },
        request_id=common_prefix + "two",
    )

    assert len(first["idempotencyKey"]) == 255
    assert len(second["idempotencyKey"]) == 255
    assert first["idempotencyKey"] != second["idempotencyKey"]


def test_load_generic_spec_rejects_submission_envelope(tmp_path) -> None:
    path = tmp_path / "job.json"
    path.write_text('{"spec": {"image": "alpine:3.20"}}', encoding="utf-8")

    with pytest.raises(ContainerJobCliError, match="workload fields only"):
        load_container_job_spec(path)


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


def test_run_generic_job_uses_same_terminal_evidence_path() -> None:
    client = _FakeClient(["running", "succeeded"])

    result = run_container_job(
        {
            "imageSourceRef": "tactics-unreal",
            "command": ["true"],
            "resources": {"cpuMillis": 1000, "memoryMiB": 512},
            "timeoutSeconds": 60,
        },
        env=_ENV,
        request_id="test-1",
        poll_seconds=0.001,
        client=client,  # type: ignore[arg-type]
    )

    assert result.state == "succeeded"
    submitted = client.calls[0][1]
    assert submitted["spec"]["imageSourceRef"] == "tactics-unreal"
    assert submitted["source"]["source"] == "managed_session"


def test_failed_job_preserves_authoritative_terminal_cause() -> None:
    client = _FakeClient(["failed"])

    result = run_container_job(
        {
            "imageSourceRef": "tactics-unreal",
            "command": ["true"],
            "resources": {"cpuMillis": 1000, "memoryMiB": 512},
            "timeoutSeconds": 60,
        },
        env=_ENV,
        poll_seconds=0.001,
        client=client,  # type: ignore[arg-type]
    )

    assert result.state == "failed"
    assert result.failure_class == "image_not_found"
    assert result.message == "deployment image source is absent"


def test_container_cli_prints_authoritative_terminal_cause(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_path = tmp_path / "job.json"
    spec_path.write_text(
        '{"imageSourceRef":"tactics-unreal","command":["true"],'
        '"resources":{"cpuMillis":1000,"memoryMiB":512}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "moonmind.cli.run_container_job",
        lambda *_args, **_kwargs: ContainerJobResult(
            job_id="container-job:" + "1" * 32,
            state="failed",
            exit_code=None,
            failure_class="image_not_found",
            message="deployment image source is absent",
            logs_ref="artifact:logs",
            artifacts_ref=None,
        ),
    )

    result = CliRunner().invoke(app, ["container", "run", "--spec", str(spec_path)])

    assert result.exit_code == 1
    assert "failureClass=image_not_found" in result.output
    assert "message=deployment image source is absent" in result.output
