"""MM-3926 #3938: hermetic first-run default journey (required, integration_ci).

Proves the documented credentialless first-result path through the real
production boundaries without credentials, Docker, or network:

- CLI envelope builders in :mod:`moonmind.run_cli` (the same
  ``POST /api/executions`` shape the dashboard UI posts; omitted and
  documented-default profile refs resolve to the server default);
- CLI HTTP paths against the registered server routes in
  :mod:`api_service.api.routers.executions` (submit / describe with
  ``?source=temporal`` / captured-evidence) and
  :mod:`api_service.api.routers.omnigent_bootstrap` (readiness);
- terminal polling in :func:`moonmind.run_cli.wait_for_terminal` through to
  captured evidence, with idempotency preserved and no silent credential or
  runtime substitution on admission/availability failures.

Hermetic by design: the HTTP layer uses ``httpx.MockTransport`` against the
real :class:`~moonmind.run_cli.RunApiClient`, so no Compose networking,
provider credentials, or live provider execution are required. Live
credentialless qualification with cold/warm timing stays separate and is
explicitly out of scope for this suite (see #3926 AC2/AC3).
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
    build_workflow_submit_payload,
    normalize_profile_ref,
    wait_for_terminal,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def _route_paths(router) -> set[tuple[str, str]]:
    """Return ``(method, full_path)`` pairs for every registered route."""

    found: set[tuple[str, str]] = set()
    prefix = str(getattr(router, "prefix", "") or "")
    for route in getattr(router, "routes", []):
        methods = getattr(route, "methods", None) or set()
        path = str(getattr(route, "path", "") or "")
        for method in methods:
            if method.upper() in {"HEAD", "OPTIONS"}:
                continue
            found.add((method.upper(), f"{prefix}{path}"))
    return found


def test_cli_paths_match_registered_server_routes() -> None:
    """CLI/server contract parity at the real router boundary (#3926 AC6)."""

    from api_service.api.routers import executions, omnigent_bootstrap

    execution_routes = _route_paths(executions.router)
    bootstrap_routes = _route_paths(omnigent_bootstrap.router)

    assert ("POST", "/api/executions") in execution_routes
    assert ("GET", "/api/executions/{workflow_id}") in execution_routes
    assert (
        "GET",
        "/api/executions/{workflow_id}/captured-evidence",
    ) in execution_routes
    assert ("GET", "/api/omnigent/bootstrap/readiness") in bootstrap_routes

    assert EXECUTIONS_PATH == "/api/executions"
    assert BOOTSTRAP_READINESS_PATH == "/api/omnigent/bootstrap/readiness"
    assert (
        CAPTURED_EVIDENCE_PATH_TEMPLATE.format(workflow_id="mm:probe")
        == "/api/executions/mm:probe/captured-evidence"
    )


def test_credentialless_submit_journey_reaches_terminal_evidence() -> None:
    """Submit -> terminal state -> captured evidence without credentials (#3926 AC1)."""

    payload, effective_key = build_workflow_submit_payload(
        instructions="Summarize repository status in one sentence",
        provider_profile_ref="auto",
        idempotency_key="first-run-req-1",
    )
    assert "providerProfileRef" not in payload["payload"]["workflow"]
    assert effective_key == "first-run-req-1"

    seen: dict = {"posts": 0, "describes": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == EXECUTIONS_PATH:
            seen["posts"] += 1
            body = json.loads(request.content.decode("utf-8"))
            assert body["type"] == "workflow"
            workflow = body["payload"]["workflow"]
            assert "providerProfileRef" not in workflow
            assert workflow["idempotencyKey"] == "first-run-req-1"
            return httpx.Response(
                201, json={"workflowId": "mm:first-run-1", "state": "running"}
            )
        if request.method == "GET" and request.url.path.endswith("/captured-evidence"):
            return httpx.Response(
                200,
                json={
                    "workflowId": "mm:first-run-1",
                    "available": True,
                    "items": [
                        {
                            "ref": "artifact://mm/first-run-1/summary",
                            "label": "finish_summary",
                        }
                    ],
                },
            )
        assert request.method == "GET"
        seen["describes"] += 1
        assert "source=temporal" in str(request.url.query)
        state = "completed" if seen["describes"] >= 2 else "running"
        return httpx.Response(
            200, json={"workflowId": "mm:first-run-1", "state": state}
        )

    client = RunApiClient(
        base_url="http://api:7000",
        bearer_token=None,
        transport=httpx.MockTransport(handler),
    )
    try:
        execution = client.submit_workflow(payload)
        terminal = wait_for_terminal(
            client, "mm:first-run-1", timeout_seconds=60.0, poll_seconds=0.1
        )
        evidence = client.get_captured_evidence("mm:first-run-1")
    finally:
        client.close()

    assert execution["workflowId"] == "mm:first-run-1"
    assert terminal["state"] == "completed"
    assert seen["describes"] >= 2
    assert evidence["available"] is True
    assert evidence["items"][0]["ref"] == "artifact://mm/first-run-1/summary"


def test_omitted_and_documented_defaults_resolve_to_server_default() -> None:
    """Omitted/auto/default profile choices share one default route (#3926 AC6)."""

    for choice in (None, "", "auto", "AUTO", "default", "Default"):
        assert normalize_profile_ref(choice) is None
        payload, _ = build_workflow_submit_payload(
            instructions="bounded task", provider_profile_ref=choice
        )
        assert "providerProfileRef" not in payload["payload"]["workflow"]

    payload, _ = build_workflow_submit_payload(
        instructions="bounded task", provider_profile_ref="explicit-profile"
    )
    assert (
        payload["payload"]["workflow"]["providerProfileRef"] == "explicit-profile"
    )


def test_admission_rejection_is_actionable_without_silent_substitution() -> None:
    """Credentialless-route-unavailable fails at admission, never switches (#3926 AC4)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "detail": {
                    "code": "admission_blocked",
                    "message": "credentialless route unavailable: bootstrap not ready",
                }
            },
        )

    client = RunApiClient(
        base_url="http://api:7000",
        transport=httpx.MockTransport(handler),
    )
    try:
        payload, _ = build_workflow_submit_payload(instructions="bounded task")
        with pytest.raises(Exception, match="readiness"):
            client.submit_workflow(payload)
        try:
            client.submit_workflow(payload)
        except Exception as exc:  # noqa: BLE001 - asserting on message text
            message = str(exc).lower()
            assert "readiness" in message
            assert "paid" not in message or "instead of switching to paid" in message
            assert "silently" in message or "instead of switching" in message
        else:
            raise AssertionError("expected admission rejection")
    finally:
        client.close()


def test_retry_preserves_idempotency_and_timeout_avoids_cleanup() -> None:
    """Idempotent retry creates no duplicates; timeout changes nothing (#3926 AC5)."""

    _, key = build_workflow_submit_payload(
        instructions="bounded task", idempotency_key="stable-req-7"
    )
    assert key == "stable-req-7"

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json={"workflowId": "mm:w9", "state": "running"})

    client = RunApiClient(
        base_url="http://api:7000",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(Exception, match="no cleanup or credential change"):
            wait_for_terminal(
                client,
                "mm:w9",
                timeout_seconds=1.0,
                poll_seconds=0.1,
                sleep=lambda _: None,
            )
    finally:
        client.close()

    assert calls, "expected at least one status poll before timeout"
    assert all(entry == "GET /api/executions/mm:w9" for entry in calls)
