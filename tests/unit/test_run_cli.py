"""Public-contract first-run CLI tests (MoonLadderStudios/MoonMind#3926).

The CLI must use the same public admission/status/artifact contracts as the
dashboard UI, resolve omitted/documented-default choices to the server default
(credentialless route), preserve authorization and idempotency, and fail with
actionable errors instead of silently switching credentials or runtimes.
"""

from __future__ import annotations

import json

import httpx
import pytest

from moonmind.run_cli import (
    BOOTSTRAP_READINESS_PATH,
    CAPTURED_EVIDENCE_PATH_TEMPLATE,
    EXECUTIONS_PATH,
    RunApiClient,
    RunCliError,
    build_workflow_submit_payload,
    is_terminal_state,
    normalize_profile_ref,
    resolve_api_base_url,
    resolve_bearer_token,
    summarize_execution,
    summarize_readiness,
    wait_for_terminal,
)


def test_normalize_profile_ref_treats_omitted_and_documented_defaults_as_default() -> None:
    assert normalize_profile_ref(None) is None
    assert normalize_profile_ref("") is None
    assert normalize_profile_ref("  ") is None
    assert normalize_profile_ref("auto") is None
    assert normalize_profile_ref("AUTO") is None
    assert normalize_profile_ref("default") is None
    assert normalize_profile_ref("Default") is None


def test_normalize_profile_ref_passes_explicit_refs_through() -> None:
    assert normalize_profile_ref("opencode-zen-free") == "opencode-zen-free"
    assert normalize_profile_ref("  custom-profile  ") == "custom-profile"


def test_build_payload_omits_profile_ref_for_server_default() -> None:
    payload, key = build_workflow_submit_payload(
        instructions="Summarize the repo status",
        provider_profile_ref="auto",
        idempotency_key="req-1",
    )

    assert payload["type"] == "workflow"
    workflow = payload["payload"]["workflow"]
    assert workflow["instructions"] == "Summarize the repo status"
    assert "providerProfileRef" not in workflow
    assert "agentProfile" not in workflow
    assert workflow["idempotencyKey"] == "req-1"
    assert key == "req-1"


def test_build_payload_keeps_explicit_profile_and_title() -> None:
    payload, key = build_workflow_submit_payload(
        instructions="Do bounded work",
        title=" First run ",
        provider_profile_ref="my-paid-profile",
        idempotency_key="req-2",
    )

    workflow = payload["payload"]["workflow"]
    assert workflow["title"] == "First run"
    assert workflow["providerProfileRef"] == "my-paid-profile"
    assert key == "req-2"


def test_build_payload_rejects_empty_instructions() -> None:
    with pytest.raises(RunCliError, match="must not be empty"):
        build_workflow_submit_payload(instructions="   ")


def test_build_payload_generates_idempotency_key_when_omitted() -> None:
    payload_a, key_a = build_workflow_submit_payload(instructions="one")
    payload_b, key_b = build_workflow_submit_payload(instructions="one")

    assert key_a and key_b and key_a != key_b
    assert payload_a["payload"]["workflow"]["idempotencyKey"] == key_a
    assert payload_b["payload"]["workflow"]["idempotencyKey"] == key_b


def test_resolve_api_base_url_prefers_explicit_then_env_then_default() -> None:
    assert resolve_api_base_url("http://api:9000/", env={}) == "http://api:9000"
    assert (
        resolve_api_base_url(None, env={"MOONMIND_URL": "http://api:8000/"})
        == "http://api:8000"
    )
    assert (
        resolve_api_base_url(
            None,
            env={
                "MOONMIND_URL": "http://ignored:1",
                "MOONMIND_API_BASE_URL": "http://preferred:2/",
            },
        )
        == "http://preferred:2"
    )
    assert resolve_api_base_url(None, env={}) == "http://localhost:7000"


def test_resolve_bearer_token_prefers_explicit_then_env() -> None:
    assert resolve_bearer_token(" tok ", env={}) == "tok"
    assert (
        resolve_bearer_token(
            None, env={"MOONMIND_API_TOKEN": "t1", "MOONMIND_CONTAINER_JOBS_BEARER_TOKEN": "t2"}
        )
        == "t1"
    )
    assert (
        resolve_bearer_token(None, env={"MOONMIND_CONTAINER_JOBS_BEARER_TOKEN": "t2"})
        == "t2"
    )
    assert resolve_bearer_token(None, env={}) is None


def test_resolve_bearer_token_file(tmp_path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("file-token\n", encoding="utf-8")

    assert (
        resolve_bearer_token(None, env={"MOONMIND_API_TOKEN_FILE": str(token_file)})
        == "file-token"
    )
    with pytest.raises(RunCliError, match="unavailable"):
        resolve_bearer_token(
            None, env={"MOONMIND_API_TOKEN_FILE": str(tmp_path / "missing")}
        )


def _client_with_handler(handler) -> RunApiClient:
    return RunApiClient(
        base_url="http://api:7000",
        bearer_token="tok",
        transport=httpx.MockTransport(handler),
    )


def test_submit_uses_ui_equivalent_executions_contract() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        body = json.loads(request.content.decode("utf-8"))
        seen["body"] = body
        return httpx.Response(201, json={"workflowId": "mm:w1", "state": "running"})

    client = _client_with_handler(handler)
    try:
        payload, _ = build_workflow_submit_payload(
            instructions="hello", idempotency_key="req-9"
        )
        result = client.submit_workflow(payload)
    finally:
        client.close()

    assert seen["method"] == "POST"
    assert seen["path"] == EXECUTIONS_PATH
    assert seen["auth"] == "Bearer tok"
    assert seen["body"]["type"] == "workflow"
    assert seen["body"]["payload"]["workflow"]["idempotencyKey"] == "req-9"
    assert result["workflowId"] == "mm:w1"


def test_status_uses_ui_equivalent_source_temporal_contract() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["query"] = str(request.url.query)
        return httpx.Response(200, json={"workflowId": "mm:w1", "state": "running"})

    client = _client_with_handler(handler)
    try:
        result = client.describe_execution("mm:w1")
    finally:
        client.close()

    assert seen["path"] == f"{EXECUTIONS_PATH}/mm:w1"
    assert "source=temporal" in seen["query"]
    assert result["state"] == "running"


def test_readiness_and_evidence_use_ui_equivalent_paths() -> None:
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == BOOTSTRAP_READINESS_PATH:
            return httpx.Response(
                200, json={"readiness": "ready", "state": "ready", "enabled": True}
            )
        expected = CAPTURED_EVIDENCE_PATH_TEMPLATE.format(workflow_id="mm:w1")
        assert request.url.path == expected
        return httpx.Response(200, json={"available": True, "items": []})

    client = _client_with_handler(handler)
    try:
        readiness = client.get_readiness()
        evidence = client.get_captured_evidence("mm:w1")
    finally:
        client.close()

    assert ("GET", BOOTSTRAP_READINESS_PATH) in seen
    assert readiness["readiness"] == "ready"
    assert evidence["available"] is True


def test_unauthorized_failure_is_actionable_and_preserves_auth() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "missing credentials"})

    client = _client_with_handler(handler)
    try:
        with pytest.raises(RunCliError, match="not authorized.*MOONMIND_API_TOKEN"):
            client.describe_execution("mm:w1")
    finally:
        client.close()


def test_conflict_never_suggests_silent_credential_switch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "detail": {
                    "code": "provider_profile_runtime_mismatch",
                    "message": "profile does not support runtime",
                }
            },
        )

    client = _client_with_handler(handler)
    try:
        with pytest.raises(RunCliError, match="does not substitute another"):
            client.submit_workflow({"type": "workflow", "payload": {}})
    finally:
        client.close()


def test_admission_rejection_points_at_readiness_not_paid_fallback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "detail": {
                    "code": "credentialless_route_unavailable",
                    "message": "credentialless route is unavailable",
                }
            },
        )

    client = _client_with_handler(handler)
    try:
        with pytest.raises(RunCliError, match="readiness.*instead of switching"):
            client.submit_workflow({"type": "workflow", "payload": {}})
    finally:
        client.close()


def test_connection_failure_is_actionable_about_compose() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = _client_with_handler(handler)
    try:
        with pytest.raises(RunCliError, match="docker compose up"):
            client.get_readiness()
    finally:
        client.close()


def test_is_terminal_state_matches_ui_terminal_set() -> None:
    for state in ("completed", "failed", "canceled", "terminated", "timed_out"):
        assert is_terminal_state(state) is True
    for state in ("running", "queued", "preparing", "", None):
        assert is_terminal_state(state) is False


def test_wait_for_terminal_returns_terminal_document() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        state = "running" if calls["count"] < 3 else "completed"
        return httpx.Response(200, json={"workflowId": "mm:w1", "state": state})

    client = _client_with_handler(handler)
    try:
        result = wait_for_terminal(
            client, "mm:w1", timeout_seconds=60.0, poll_seconds=0.01, sleep=lambda _: None
        )
    finally:
        client.close()

    assert result["state"] == "completed"
    assert calls["count"] == 3


def test_wait_for_terminal_times_out_without_cleanup_side_effects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"workflowId": "mm:w1", "state": "running"})

    client = _client_with_handler(handler)
    try:
        with pytest.raises(RunCliError, match="timed out.*re-check"):
            wait_for_terminal(
                client,
                "mm:w1",
                timeout_seconds=0.01,
                poll_seconds=0.01,
                sleep=lambda _: None,
            )
    finally:
        client.close()


def test_summaries_are_stable_and_redacted() -> None:
    assert (
        summarize_execution({"workflowId": "mm:w1", "state": "completed", "title": "t"})
        == "workflow mm:w1: completed t"
    )
    assert (
        summarize_readiness({"readiness": "ready", "state": "ready", "enabled": True})
        == "readiness=ready state=ready enabled=True"
    )
