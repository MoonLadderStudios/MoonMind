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


def test_download_help_lists_ref_and_out() -> None:
    result = CliRunner().invoke(app, ["workflow", "download", "--help"])
    assert result.exit_code == 0
    body = unstyle(result.output)
    assert "--ref" in body and "--out" in body


def test_download_returns_bytes_and_filename() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.path == "/api/executions/wf-1/captured-evidence/download"
        assert dict(request.url.params)["ref"] == "artifact://omnigent/corr-1/final.json"
        return httpx.Response(
            200,
            content=b'{"ok": true}',
            headers={
                "content-disposition": 'attachment; filename="final.json"',
                "content-type": "application/json",
            },
        )

    downloaded = _client(handler).download_captured_evidence(
        "wf-1", "artifact://omnigent/corr-1/final.json"
    )
    assert downloaded.content == b'{"ok": true}'
    assert downloaded.filename == "final.json"
    assert seen and seen[0].headers.get("authorization") == "Bearer tok-1"


def test_download_derives_filename_from_ref_without_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"bytes")

    downloaded = _client(handler).download_captured_evidence("wf-1", "art_final")
    assert downloaded.content == b"bytes"
    assert downloaded.filename == "art_final"


def test_download_unknown_ref_is_actionable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "detail": {
                    "code": "captured_evidence_not_found",
                    "message": "No such captured evidence for this workflow.",
                }
            },
        )

    with pytest.raises(WorkflowCliError, match="no such captured evidence"):
        _client(handler).download_captured_evidence("wf-1", "art_missing")


def test_download_rejects_empty_ref_before_network() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"x")

    with pytest.raises(WorkflowCliError, match="artifact ref is required"):
        _client(handler).download_captured_evidence("wf-1", "   ")
    assert seen == []


def test_download_redirect_never_forwards_credentials() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"location": "https://evil.invalid/x"})

    with pytest.raises(WorkflowCliError, match="redirected"):
        _client(handler).download_captured_evidence("wf-1", "art_final")
    assert len(seen) == 1


def test_download_save_refuses_overwrite_without_flag(tmp_path) -> None:
    from moonmind.workflow_cli import save_evidence_download

    target = tmp_path / "final.json"
    target.write_bytes(b"existing")
    with pytest.raises(WorkflowCliError, match="overwrite"):
        save_evidence_download(target, b"new", overwrite=False)
    assert target.read_bytes() == b"existing"
    saved = save_evidence_download(target, b"new", overwrite=True)
    assert saved == target
    assert target.read_bytes() == b"new"


def test_download_command_saves_bytes_to_out(tmp_path, monkeypatch) -> None:
    from moonmind import workflow_cli as workflow_cli_module

    saved_path = tmp_path / "evidence.json"

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def download_captured_evidence(self, workflow_id, ref):
            assert workflow_id == "wf-1"
            assert ref == "art_final"
            return workflow_cli_module.EvidenceDownload(
                filename="evidence.json",
                content=b'{"ok": true}',
                content_type="application/json",
            )

        def close(self) -> None:
            pass

    monkeypatch.setattr(workflow_cli_module, "WorkflowApiClient", _FakeClient)
    result = CliRunner().invoke(
        app,
        ["workflow", "download", "wf-1", "--ref", "art_final", "--out", str(saved_path)],
    )
    assert result.exit_code == 0, unstyle(result.output)
    assert saved_path.read_bytes() == b'{"ok": true}'
    assert "wf-1" in unstyle(result.output)


def test_hermetic_submit_to_download_journey_saves_bytes(tmp_path) -> None:
    """MoonLadderStudios/MoonMind#3926 R1/R4/R7: hermetic submit-to-download.

    Exercises the default installation-to-session-to-download contracts in
    one journey through the same public ``/api/executions`` routes the
    dashboard Workflow Detail page uses, with the hermetic
    ``httpx.MockTransport`` external-provider substitute (no live network,
    no credentials, no seeding): submit (admission) -> describe (status) ->
    captured-evidence (result refs) -> download (saved bytes) -> local save
    with overwrite safety and readable terminal text.

    This proves the shared UI/CLI download contract hermetically. The live
    installation-to-session-to-download run against a real stack (compose
    up, dashboard/CLI download bytes, owned cleanup) remains separately
    owed and is NOT claimed by this test.
    """
    from moonmind.workflow_cli import save_evidence_download

    artifact_ref = "artifact://omnigent/corr-1/final.json"
    saved_bytes = b'{"ok": true, "result": "useful bounded work"}'

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/executions":
            body = json.loads(request.content.decode())
            assert body["idempotencyKey"] == "req-journey-3926"
            return httpx.Response(
                201,
                json={"workflowId": "wf-1", "status": "queued", "state": "scheduled"},
            )
        if request.url.path == "/api/executions/wf-1":
            return httpx.Response(
                200, json={"workflowId": "wf-1", "status": "completed"}
            )
        if request.url.path == "/api/executions/wf-1/captured-evidence":
            return httpx.Response(
                200,
                json={
                    "workflowId": "wf-1",
                    "available": True,
                    "summary": "done",
                    "items": [{"label": "report", "artifactRef": artifact_ref}],
                },
            )
        if request.url.path == "/api/executions/wf-1/captured-evidence/download":
            assert dict(request.url.params)["ref"] == artifact_ref
            return httpx.Response(
                200,
                content=saved_bytes,
                headers={
                    "content-disposition": 'attachment; filename="final.json"',
                    "content-type": "application/json",
                },
            )
        return httpx.Response(404, json={"detail": "nope"})

    client = _client(handler)
    try:
        admitted = client.submit_execution(
            build_execution_payload(
                instructions="demo", skill="pr-resolver", idempotency_key="req-journey-3926"
            )
        )
        assert admitted["workflowId"] == "wf-1"
        assert client.describe_execution("wf-1")["status"] == "completed"
        evidence = client.captured_evidence("wf-1")
        assert evidence is not None and evidence["available"] is True
        refs = [i["artifactRef"] for i in evidence["items"]]
        assert refs == [artifact_ref]
        downloaded = client.download_captured_evidence("wf-1", refs[0])
        assert downloaded.content == saved_bytes
        assert downloaded.filename == "final.json"
        terminal = sanitize_terminal_text(evidence["summary"])
        assert "done" in terminal
        out = tmp_path / downloaded.filename
        saved = save_evidence_download(out, downloaded.content, overwrite=False)
        assert saved.read_bytes() == saved_bytes
        with pytest.raises(WorkflowCliError, match="overwrite"):
            save_evidence_download(out, b"other", overwrite=False)
        assert saved.read_bytes() == saved_bytes
    finally:
        client.close()
