"""Unit tests for the low-level Jira REST client."""

from __future__ import annotations

import base64
import logging
from collections.abc import Callable

import httpx
import pytest

from moonmind.integrations.jira.auth import ResolvedJiraConnection
from moonmind.integrations.jira.client import JiraClient
from moonmind.integrations.jira.errors import JiraToolError

pytestmark = [pytest.mark.asyncio]

def _build_connection(*, retry_attempts: int = 3) -> ResolvedJiraConnection:
    basic_pair = "bot@example.com:secret-token"
    basic_token = base64.b64encode(basic_pair.encode("utf-8")).decode("ascii")
    authorization = f"Basic {basic_token}"
    return ResolvedJiraConnection(
        auth_mode="basic",
        base_url="https://jira.example/rest/api/3",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": authorization,
        },
        connect_timeout_seconds=10.0,
        read_timeout_seconds=30.0,
        retry_attempts=retry_attempts,
        redaction_values=(
            "secret-token",
            basic_pair,
            authorization,
            f"Authorization: {authorization}",
        ),
    )

def _build_cloud_connection() -> ResolvedJiraConnection:
    return ResolvedJiraConnection(
        auth_mode="service_account_scoped",
        base_url="https://api.atlassian.com/ex/jira/cloud-abc/rest/api/3",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": "Bearer secret-token",
        },
        connect_timeout_seconds=10.0,
        read_timeout_seconds=30.0,
        retry_attempts=1,
        redaction_values=("secret-token", "Bearer secret-token"),
    )

def _build_injected_client(
    connection: ResolvedJiraConnection,
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=connection.base_url,
        headers=connection.headers,
        transport=httpx.MockTransport(handler),
    )

async def test_request_json_sends_headers_and_decodes_json() -> None:
    connection = _build_connection()

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/3/issue/ENG-1"
        assert request.headers["Authorization"] == connection.headers["Authorization"]
        return httpx.Response(
            200,
            json={"key": "ENG-1", "ok": True},
            headers={"content-type": "application/json"},
        )

    injected = _build_injected_client(connection, _handler)
    client = JiraClient(connection=connection, client=injected)
    try:
        payload = await client.request_json(
            method="GET",
            path="/issue/ENG-1",
            action="get_issue",
            context={"issueKey": "ENG-1"},
        )
    finally:
        await injected.aclose()

    assert payload == {"key": "ENG-1", "ok": True}

async def test_request_json_maps_agile_paths_for_site_base_url() -> None:
    connection = _build_connection()

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/agile/1.0/board/42/configuration"
        return httpx.Response(
            200,
            json={"ok": True},
            headers={"content-type": "application/json"},
        )

    injected = _build_injected_client(connection, _handler)
    client = JiraClient(connection=connection, client=injected)
    try:
        payload = await client.request_json(
            method="GET",
            path="agile:/board/42/configuration",
            action="jira_browser.list_columns",
        )
    finally:
        await injected.aclose()

    assert payload == {"ok": True}

async def test_request_json_maps_agile_paths_for_cloud_api_base_url() -> None:
    connection = _build_cloud_connection()

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ex/jira/cloud-abc/rest/agile/1.0/board"
        return httpx.Response(
            200,
            json={"values": []},
            headers={"content-type": "application/json"},
        )

    injected = _build_injected_client(connection, _handler)
    client = JiraClient(connection=connection, client=injected)
    try:
        payload = await client.request_json(
            method="GET",
            path="agile:/board",
            action="jira_browser.list_boards",
        )
    finally:
        await injected.aclose()

    assert payload == {"values": []}

async def test_request_json_maps_auth_failures_to_sanitized_error() -> None:
    connection = _build_connection()

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            text="Authorization: Basic leaked secret-token",
            headers={"content-type": "text/plain"},
        )

    injected = _build_injected_client(connection, _handler)
    client = JiraClient(connection=connection, client=injected)
    try:
        with pytest.raises(JiraToolError) as excinfo:
            await client.request_json(
                method="GET",
                path="/myself",
                action="verify_connection",
            )
    finally:
        await injected.aclose()

    assert excinfo.value.code == "jira_auth_failed"
    assert "secret-token" not in str(excinfo.value)

async def test_request_json_maps_issue_404_to_auth_failure_when_myself_rejects() -> None:
    connection = _build_connection()
    seen_paths: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path.endswith("/myself"):
            return httpx.Response(
                401,
                text="Client must be authenticated to access this resource.",
                headers={"content-type": "text/plain"},
            )
        return httpx.Response(
            404,
            json={
                "errorMessages": [
                    "Issue does not exist or you do not have permission to see it."
                ],
                "errors": {},
            },
            headers={"content-type": "application/json"},
        )

    injected = _build_injected_client(connection, _handler)
    client = JiraClient(connection=connection, client=injected)
    try:
        with pytest.raises(JiraToolError) as excinfo:
            await client.request_json(
                method="GET",
                path="/issue/KANDY-2558",
                action="get_issue",
                context={"issueKey": "KANDY-2558"},
            )
    finally:
        await injected.aclose()

    assert excinfo.value.code == "jira_auth_failed"
    assert seen_paths == ["/rest/api/3/issue/KANDY-2558", "/rest/api/3/myself"]

async def test_request_json_preserves_issue_404_when_myself_succeeds() -> None:
    connection = _build_connection()

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/myself"):
            return httpx.Response(
                200,
                json={"accountId": "acct-1"},
                headers={"content-type": "application/json"},
            )
        return httpx.Response(
            404,
            json={"errorMessages": ["Issue does not exist."], "errors": {}},
            headers={"content-type": "application/json"},
        )

    injected = _build_injected_client(connection, _handler)
    client = JiraClient(connection=connection, client=injected)
    try:
        with pytest.raises(JiraToolError) as excinfo:
            await client.request_json(
                method="GET",
                path="/issue/KANDY-2558",
                action="get_issue",
                context={"issueKey": "KANDY-2558"},
            )
    finally:
        await injected.aclose()

    assert excinfo.value.code == "jira_not_found"

async def test_request_json_retries_retry_after_and_surfaces_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _build_connection(retry_attempts=3)
    sleep_calls: list[float] = []
    attempts = {"count": 0}

    async def _fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    def _handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(
            429,
            json={"errorMessages": ["slow down"]},
            headers={"Retry-After": "0.25", "content-type": "application/json"},
        )

    monkeypatch.setattr("moonmind.integrations.jira.client.asyncio.sleep", _fake_sleep)

    injected = _build_injected_client(connection, _handler)
    client = JiraClient(connection=connection, client=injected)
    try:
        with pytest.raises(JiraToolError) as excinfo:
            await client.request_json(
                method="POST",
                path="/search",
                action="search_issues",
            )
    finally:
        await injected.aclose()

    assert excinfo.value.code == "jira_rate_limited"
    assert attempts["count"] == 3
    assert sleep_calls == [0.25, 0.25]

async def test_request_json_redacts_failure_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    connection = _build_connection(retry_attempts=1)

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            text=(
                "Authorization: Basic "
                "Ym90QGV4YW1wbGUuY29tOnNlY3JldC10b2tlbg== secret-token"
            ),
            headers={"content-type": "text/plain"},
        )

    injected = _build_injected_client(connection, _handler)
    client = JiraClient(connection=connection, client=injected)
    try:
        with caplog.at_level(logging.WARNING):
            with pytest.raises(JiraToolError):
                await client.request_json(
                    method="POST",
                    path="/issue",
                    action="create_issue",
                )
    finally:
        await injected.aclose()

    assert "secret-token" not in caplog.text
    assert "Ym90QGV4YW1wbGUuY29tOnNlY3JldC10b2tlbg==" not in caplog.text
    assert "***" in caplog.text

async def test_request_json_retries_transient_server_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _build_connection(retry_attempts=2)
    attempts = {"count": 0}
    sleep_calls: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    def _handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(503, text="temporarily unavailable")
        return httpx.Response(
            200,
            json={"ok": True},
            headers={"content-type": "application/json"},
        )

    monkeypatch.setattr("moonmind.integrations.jira.client.asyncio.sleep", _fake_sleep)

    injected = _build_injected_client(connection, _handler)
    client = JiraClient(connection=connection, client=injected)
    try:
        payload = await client.request_json(
            method="GET",
            path="/myself",
            action="verify_connection",
        )
    finally:
        await injected.aclose()

    assert payload == {"ok": True}
    assert attempts["count"] == 2
    assert sleep_calls == [1.0]

async def test_request_json_maps_decode_errors_to_sanitized_error() -> None:
    connection = _build_connection()

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="{not valid json",
            headers={"content-type": "application/json"},
        )

    injected = _build_injected_client(connection, _handler)
    client = JiraClient(connection=connection, client=injected)
    try:
        with pytest.raises(JiraToolError) as excinfo:
            await client.request_json(
                method="GET",
                path="/myself",
                action="verify_connection",
            )
    finally:
        await injected.aclose()

    assert excinfo.value.code == "jira_request_failed"
    assert "decoded" in str(excinfo.value)
