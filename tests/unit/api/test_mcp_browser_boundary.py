"""MCP browser boundary with the production disabled-mode auth dependency."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport, AsyncClient

from api_service.api.routers import mcp_tools
from api_service.auth_providers import get_current_user
from moonmind.security import auth_modes_4120

pytestmark = pytest.mark.asyncio


@pytest.fixture
def browser_app(monkeypatch, disabled_env_keys):
    # Keep production auth resolution: only the backing DB is represented by
    # an already initialized local operator, not an override of current_user.
    monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "disabled")
    for key in (
        "MOONMIND_PUBLIC_BASE_URL",
        "MOONMIND_TRUSTED_INGRESS",
        "MOONMIND_TRUSTED_PROXIES",
        "MOONMIND_API_PUBLISH_HOST",
        "MOONMIND_API_HOST",
    ):
        monkeypatch.delenv(key, raising=False)
    user = SimpleNamespace(id="operator", is_active=True, is_superuser=True)
    session = SimpleNamespace(get=AsyncMock(return_value=user))
    app = FastAPI()
    app.include_router(mcp_tools.router, prefix="/api")
    app.add_middleware(CORSMiddleware, allow_origins=[], allow_credentials=True)
    app.dependency_overrides[mcp_tools.get_async_session] = lambda: session
    assert get_current_user() not in app.dependency_overrides
    dispatch = AsyncMock(return_value={"accepted": True})
    monkeypatch.setattr(mcp_tools, "_dispatch_tool_call", dispatch)
    return app, dispatch


def _invocation(path):
    if path.endswith("tools/call"):
        return {"tool": "jira.get_issue", "arguments": {}}
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "jira.get_issue", "arguments": {}},
    }


@pytest.mark.parametrize("path", ["/api/mcp", "/api/mcp/tools/call"])
@pytest.mark.parametrize("header", ["Origin", "Referer"])
async def test_local_browser_rebinding_cannot_dispatch(browser_app, path, header):
    app, dispatch = browser_app
    # After DNS rebinding, the transport peer is still local while the
    # attacker's browser retains matching attacker-controlled Host + Origin.
    origin = "http://attacker.example:7000"
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 43210)),
        base_url=origin,
    ) as client:
        response = await client.post(
            path,
            headers={"Accept": "application/json", header: origin},
            json=_invocation(path),
        )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "host_forbidden"
    dispatch.assert_not_awaited()


@pytest.mark.parametrize(
    "origin", ["http://localhost:7000", "http://127.0.0.1:7000", "http://[::1]:7000"]
)
@pytest.mark.parametrize("configured_base", [False, True])
async def test_local_browser_default_and_explicit_urls_work(
    browser_app, monkeypatch, origin, configured_base
):
    app, dispatch = browser_app
    if configured_base:
        monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", origin)
    # Compose can present the bridge peer even for a loopback published port;
    # the browser guard must not confuse container transport with publication.
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("172.18.0.1", 43210)),
        base_url=origin,
    ) as client:
        response = await client.post(
            "/api/mcp", headers={"Origin": origin}, json=_invocation("/api/mcp")
        )
    assert response.status_code == 200
    dispatch.assert_awaited_once()


@pytest.mark.parametrize("trusted_ingress", ["1", "true", "yes"])
async def test_audited_ingress_without_public_url_stays_supported(
    browser_app, monkeypatch, trusted_ingress
):
    app, dispatch = browser_app
    monkeypatch.setenv("MOONMIND_TRUSTED_INGRESS", trusted_ingress)
    monkeypatch.setenv("MOONMIND_API_PUBLISH_HOST", "192.168.10.5")
    origin = "https://operator.example"
    async with AsyncClient(transport=ASGITransport(app=app), base_url=origin) as client:
        response = await client.post(
            "/api/mcp", headers={"Origin": origin}, json=_invocation("/api/mcp")
        )
    assert response.status_code == 200
    dispatch.assert_awaited_once()


async def test_public_url_works_behind_tls_terminating_proxy(browser_app, monkeypatch):
    app, dispatch = browser_app
    monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", "https://operator.example")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://api:8000"
    ) as client:
        response = await client.post(
            "/api/mcp",
            headers={"Host": "operator.example", "Origin": "https://operator.example"},
            json=_invocation("/api/mcp"),
        )
    assert response.status_code == 200
    dispatch.assert_awaited_once()


@pytest.mark.parametrize("configured_base", [None, "https://operator.example"])
async def test_originless_compose_client_stays_supported(
    browser_app, monkeypatch, configured_base
):
    app, dispatch = browser_app
    if configured_base:
        monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", configured_base)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://api:8000"
    ) as client:
        response = await client.post("/api/mcp", json=_invocation("/api/mcp"))
    assert response.status_code == 200
    dispatch.assert_awaited_once()


async def test_forged_forwarding_headers_do_not_allow_rebinding(browser_app):
    app, dispatch = browser_app
    origin = "http://attacker.example:7000"
    async with AsyncClient(transport=ASGITransport(app=app), base_url=origin) as client:
        response = await client.post(
            "/api/mcp",
            headers={
                "Origin": origin,
                "X-Forwarded-Host": "localhost:7000",
                "X-Forwarded-For": "127.0.0.1",
                "X-Moonmind-User": "operator",
            },
            json=_invocation("/api/mcp"),
        )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "host_forbidden"
    dispatch.assert_not_awaited()


@pytest.mark.parametrize(
    "origin", ["http://localhost:8123", "https://localhost:7000", "null"]
)
async def test_local_browser_origin_must_match_scheme_and_port(browser_app, origin):
    app, dispatch = browser_app
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://localhost:7000"
    ) as client:
        response = await client.post(
            "/api/mcp", headers={"Origin": origin}, json=_invocation("/api/mcp")
        )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "origin_forbidden"
    dispatch.assert_not_awaited()


@pytest.mark.parametrize("public_base", ["", "https://operator.example"])
async def test_trusted_ingress_does_not_allow_foreign_browser_origin(
    browser_app, monkeypatch, public_base
):
    app, dispatch = browser_app
    monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", public_base)
    monkeypatch.setenv("MOONMIND_TRUSTED_INGRESS", "1")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://operator.example"
    ) as client:
        response = await client.post(
            "/api/mcp",
            headers={"Origin": "https://attacker.example"},
            json=_invocation("/api/mcp"),
        )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "origin_forbidden"
    dispatch.assert_not_awaited()


async def test_configured_public_url_pins_browser_host(browser_app, monkeypatch):
    app, dispatch = browser_app
    monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", "https://operator.example")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://attacker.example"
    ) as client:
        response = await client.post(
            "/api/mcp",
            headers={"Origin": "https://attacker.example"},
            json=_invocation("/api/mcp"),
        )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "host_forbidden"
    dispatch.assert_not_awaited()
