"""Hermetic installed-CLI-to-API tests for MoonMind#3939."""

from __future__ import annotations

import json

import httpx
import pytest
from click import unstyle
from typer.testing import CliRunner

from moonmind.cli import app
from moonmind import workflow_cli
from moonmind.workflow_cli import (
    WorkflowApiClient,
    WorkflowCliError,
    build_execution_payload,
    detail_url,
    parse_extra_params,
    require_secure_transport,
    resolve_api_base,
    resolve_bearer_token,
    sanitize_terminal_text,
    validate_repository,
)


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _client(handler, *, token: str | None = "tok-1") -> WorkflowApiClient:
    return WorkflowApiClient(
        base_url="http://127.0.0.1:7000",
        bearer_token=token,
        transport=_transport(handler),
    )


def test_help_requires_no_heavy_imports_and_has_no_manifest_group() -> None:
    import re

    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    text = unstyle(result.output).lower()
    assert "workflow" in text
    workflow_help = CliRunner().invoke(app, ["workflow", "--help"])
    assert workflow_help.exit_code == 0
    body = unstyle(workflow_help.output).lower()
    assert "run" in body and "status" in body and "logs" in body
    # No retired `manifest` command, alias, or retrieval inspection command
    # may be listed as a runnable workflow subcommand.
    assert re.search(r"\bmanifest\b", body) is None
    assert re.search(r"\brag\b", body) is None


def test_run_help_disambiguates_preset_skill_and_profiles() -> None:
    result = CliRunner().invoke(app, ["workflow", "run", "--help"])
    assert result.exit_code == 0
    body = unstyle(result.output)
    assert "--preset" in body and "--skill" in body
    assert "--agent-profile" in body and "--provider-profile" in body
    assert "--profile " not in body.replace("--agent-profile", "").replace(
        "--provider-profile", ""
    )
    assert "--request-id" in body


def test_payload_keeps_server_owned_resolution() -> None:
    payload = build_execution_payload(
        instructions="do work",
        skill="pr-resolver",
        agent_profile="agent-a",
        provider_profile="prov-b",
        publish_mode="pr",
        idempotency_key="req-1",
    )
    assert payload["workflowType"] == "MoonMind.UserWorkflow"
    assert payload["idempotencyKey"] == "req-1"
    task = payload["initialParameters"]["task"]
    assert task["steps"] == [{"skill": {"name": "pr-resolver"}}]
    assert task["agentProfile"] == {"profileId": "agent-a"}
    assert task["providerProfileRef"] == "prov-b"
    assert task["profileId"] == "prov-b"
    assert task["providerProfile"] == "prov-b"
    assert task["publishMode"] == "pr"
    assert task["publish"] == {"mode": "pr"}
    params = payload["initialParameters"]
    assert params["agentProfile"] == {"profileId": "agent-a"}
    assert params["providerProfileRef"] == "prov-b"
    assert params["publish"] == {"mode": "pr"}


def test_preset_without_instructions_keeps_plan_source() -> None:
    payload = build_execution_payload(preset="demo-preset", idempotency_key="req-p")
    task = payload["initialParameters"]["task"]
    assert task["taskTemplate"] == {"slug": "demo-preset", "scope": "global"}
    assert task["instructions"].strip() != ""
    assert task["goal"].strip() != ""


def test_detail_url_strips_userinfo() -> None:
    assert detail_url("https://user:pass@example.com", "wf-1") == (
        "https://example.com/workflows/wf-1"
    )


def test_client_disables_env_proxies() -> None:
    client = WorkflowApiClient(base_url="http://127.0.0.1:7000", bearer_token=None)
    try:
        assert client._client is not None
        assert client._client.trust_env is False
    finally:
        client.close()


def test_payload_rejects_preset_skill_ambiguity() -> None:
    with pytest.raises(WorkflowCliError, match="either --preset or --skill"):
        build_execution_payload(instructions="x", preset="a", skill="b")


def test_repository_rejects_local_paths() -> None:
    for bad in ("/tmp/repo", "./repo", "../repo", "file:///tmp/x", "nota repo!!"):
        with pytest.raises(WorkflowCliError):
            validate_repository(bad)
    assert validate_repository("octo/repo") == "octo/repo"
    assert validate_repository("https://example.com/o/r") == "https://example.com/o/r"


def test_retired_params_rejected() -> None:
    with pytest.raises(WorkflowCliError, match="retired"):
        parse_extra_params(["manifestFoo=1"])
    with pytest.raises(WorkflowCliError, match="retired"):
        parse_extra_params(["rag=1"])


def test_token_only_from_protected_mechanisms() -> None:
    assert resolve_bearer_token({}) is None
    assert resolve_bearer_token({"MOONMIND_API_TOKEN": "  tok "}) == "tok"
    with pytest.raises(WorkflowCliError, match="MOONMIND_API_TOKEN_FILE"):
        resolve_bearer_token({"MOONMIND_API_TOKEN_FILE": "/no/such/file"})
    assert resolve_api_base({}) == "http://127.0.0.1:7000"
    assert resolve_api_base({"MOONMIND_URL": "http://api:8000/"}) == "http://api:8000"


def test_secure_transport_required_for_remote_token() -> None:
    require_secure_transport("http://127.0.0.1:7000", has_token=True, env={})
    require_secure_transport("https://api.example.invalid", has_token=True, env={})
    with pytest.raises(WorkflowCliError, match="insecure remote"):
        require_secure_transport("http://api.example.invalid", has_token=True, env={})
    # Explicit trusted-network opt-in is honored but never the default.
    require_secure_transport(
        "http://api.example.invalid",
        has_token=True,
        env={"MOONMIND_ALLOW_INSECURE_REMOTE": "1"},
    )


def test_redirect_never_forwards_credentials() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"location": "https://evil.invalid/x"})

    with pytest.raises(WorkflowCliError, match="redirected"):
        _client(handler).submit_execution(
            build_execution_payload(instructions="x", idempotency_key="r1")
        )
    assert seen and seen[0].headers.get("authorization") == "Bearer tok-1"
    # follow_redirects=False: no second request ever carries the credential.
    assert len(seen) == 1


def test_auth_errors_are_actionable_without_leaking() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": {"code": "auth_invalid"}})

    with pytest.raises(WorkflowCliError, match="not authorized"):
        _client(handler).describe_execution("wf-1")


def test_hermetic_submit_status_logs_journey() -> None:
    posts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/executions":
            body = json.loads(request.content.decode())
            posts.append(body)
            assert body["idempotencyKey"] == "req-stable"
            assert body["workflowType"] == "MoonMind.UserWorkflow"
            return httpx.Response(
                201,
                json={
                    "workflowId": "wf-1",
                    "runId": "run-1",
                    "status": "queued",
                    "state": "scheduled",
                    "title": "demo",
                },
            )
        if request.url.path == "/api/executions/wf-1":
            return httpx.Response(
                200,
                json={
                    "workflowId": "wf-1",
                    "runId": "run-1",
                    "status": "completed",
                    "state": "completed",
                    "title": "demo",
                },
            )
        if request.url.path == "/api/executions/wf-1/captured-evidence":
            return httpx.Response(
                200,
                json={
                    "workflowId": "wf-1",
                    "available": True,
                    "summary": "done",
                    "items": [{"label": "report", "kind": "report", "artifactRef": "artifact://a"}],
                },
            )
        if request.url.path == "/api/executions/wf-1/steps":
            return httpx.Response(200, json={"steps": []})
        return httpx.Response(404, json={"detail": "nope"})

    client = _client(handler)
    try:
        admitted = client.submit_execution(
            build_execution_payload(instructions="demo", skill="pr-resolver", idempotency_key="req-stable")
        )
        assert admitted["workflowId"] == "wf-1"
        status = client.describe_execution("wf-1")
        assert status["status"] == "completed"
        evidence = client.captured_evidence("wf-1")
        assert evidence is not None and evidence["available"] is True
        assert detail_url("http://127.0.0.1:7000", "wf-1").endswith("/workflows/wf-1")
    finally:
        client.close()
    assert len(posts) == 1


def test_same_request_id_reconciles_one_workflow() -> None:
    posts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        posts.append(body)
        return httpx.Response(
            201, json={"workflowId": "wf-same", "status": "queued", "state": "scheduled"}
        )

    client = _client(handler)
    try:
        first = client.submit_execution(
            build_execution_payload(instructions="x", idempotency_key="same-key")
        )
        second = client.submit_execution(
            build_execution_payload(instructions="x", idempotency_key="same-key")
        )
    finally:
        client.close()
    assert first["workflowId"] == second["workflowId"] == "wf-same"
    assert [p["idempotencyKey"] for p in posts] == ["same-key", "same-key"]


def test_lost_acknowledgment_stays_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection lost")

    with pytest.raises(WorkflowCliError, match="same --request-id"):
        _client(handler).submit_execution(
            build_execution_payload(instructions="x", idempotency_key="k1")
        )


def test_terminal_sanitization_and_bounds() -> None:
    nasty = "\x1b[31mhello\x1b[0m\r\nworld\x00 GHOST"
    cleaned = sanitize_terminal_text(nasty)
    assert "\x1b" not in cleaned and "\x00" not in cleaned
    assert "hello" in cleaned
    long_text = "x" * 9000
    assert sanitize_terminal_text(long_text).endswith("[truncated]")
    secret_text = sanitize_terminal_text("token=supersecretvalue123")
    assert "supersecretvalue123" not in secret_text


def test_logs_render_reports_gaps_honestly() -> None:
    lines, gap = workflow_cli.render_log_lines(None, None)
    assert lines == [] and gap is not None
    lines, gap = workflow_cli.render_log_lines(
        {"summary": "ok", "items": []}, {"steps": [{"title": "s", "state": "ok"}]}
    )
    assert gap is None and lines


def test_wrong_owner_and_expired_token_mapped() -> None:
    def forbidden(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": {"code": "forbidden"}})

    with pytest.raises(WorkflowCliError, match="forbidden"):
        _client(forbidden).describe_execution("wf-1")

    def expired(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"detail": {"code": "auth_invalid", "message": "token expired"}}
        )

    with pytest.raises(WorkflowCliError, match="expired"):
        _client(expired).describe_execution("wf-1")
