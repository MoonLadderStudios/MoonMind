"""Router tests for serving the native Omnigent UI through MoonMind-scoped routes.

MoonLadderStudios/MoonMind#3638. The browser loads the native UI document and its
assets exclusively through ``/omnigent-ui/workflow-chat/{chatBindingId}[...]``.
Every request authorizes the durable binding, gates on a compatible native UI
version, injects a browser-safe bootstrap into the served document, scopes asset
URLs, and applies the embedded vs full-page security-header policy — never
leaking a provider session id, upstream URL, or credential to the browser.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api_service.api.routers.omnigent_native_ui as native_ui_mod
from api_service.api.routers.omnigent_bridge import (
    _get_bridge_store,
    _get_execution_service,
    _require_bridge_enabled,
)
from api_service.api.routers.omnigent_native_ui import (
    NATIVE_UI_MOUNT_PATH,
    NativeUiUpstream,
    NativeUiUpstreamError,
    NativeUiUpstreamResponse,
    _rewrite_upstream_location,
    get_native_ui_upstream,
    native_ui_router,
)
from api_service.auth_providers import get_current_user
from moonmind.omnigent.native_ui import scoped_ui_base

_USER_ID = uuid4()
_CHAT_BINDING_ID = "chatb_opaque123"
_PROVIDER_SESSION_ID = "prov-sess-secret-1"

_INDEX_HTML = (
    "<!doctype html><html><head><meta charset=\"utf-8\">"
    '<script type="module" src="/assets/index-abc.js"></script>'
    "</head><body><div id=\"root\"></div></body></html>"
).encode("utf-8")


def _mock_user():
    return SimpleNamespace(id=_USER_ID, email="chat@example.com", is_superuser=False)


class _FakeService:
    def __init__(self, owner_id: Any) -> None:
        self._owner_id = owner_id

    async def describe_execution(self, workflow_id: str):
        return SimpleNamespace(owner_id=self._owner_id)


def _row(**overrides: Any) -> SimpleNamespace:
    values = dict(
        bridge_session_id="brs-internal-1",
        moonmind_workflow_id="mm:w1",
        moonmind_run_id="run-1",
        moonmind_agent_run_id="ar-1",
        status="active",
        omnigent_session_id=_PROVIDER_SESSION_ID,
        metadata_={},
    )
    values.update(overrides)
    return SimpleNamespace(**values)


_UNSET = object()


class _FakeStore:
    def __init__(self, *, row: Any = _UNSET) -> None:
        self._row = _row() if row is _UNSET else row

    async def get_session_by_chat_binding_id(self, chat_binding_id: str):
        return self._row


class _FakeUpstream:
    def __init__(self, responses: dict[str, NativeUiUpstreamResponse]) -> None:
        self._responses = responses
        self.paths: list[str] = []

    async def fetch(self, path: str) -> NativeUiUpstreamResponse:
        self.paths.append(path)
        return self._responses[path]


def _index_response() -> NativeUiUpstreamResponse:
    return NativeUiUpstreamResponse(
        status_code=200, content=_INDEX_HTML, media_type="text/html"
    )


def _asset_response() -> NativeUiUpstreamResponse:
    return NativeUiUpstreamResponse(
        status_code=200,
        content=b"console.log('native-app');",
        media_type="application/javascript",
    )


def _build(
    *,
    owner_id: Any = _USER_ID,
    store: _FakeStore | None = None,
    upstream: _FakeUpstream | None = None,
    enabled: bool = True,
) -> tuple[TestClient, _FakeUpstream]:
    app = FastAPI()
    app.include_router(native_ui_router, prefix=NATIVE_UI_MOUNT_PATH)
    store = store or _FakeStore()
    upstream = upstream or _FakeUpstream(
        {
            "/": _index_response(),
            "/assets/index-abc.js": _asset_response(),
        }
    )
    config = SimpleNamespace(enabled=enabled, host_protocol_mode="upstream")
    app.dependency_overrides[get_current_user()] = _mock_user
    app.dependency_overrides[_get_execution_service] = lambda: _FakeService(owner_id)
    app.dependency_overrides[_get_bridge_store] = lambda: store
    app.dependency_overrides[_require_bridge_enabled] = lambda: config
    app.dependency_overrides[get_native_ui_upstream] = lambda: upstream
    return TestClient(app), upstream


def _url(suffix: str = "", *, binding: str = _CHAT_BINDING_ID) -> str:
    base = f"{NATIVE_UI_MOUNT_PATH}/{binding}"
    return f"{base}/{suffix}" if suffix else base


# --- Embedded document -------------------------------------------------------


def test_embedded_document_serves_native_app_with_bootstrap() -> None:
    client, upstream = _build()

    response = client.get(_url(), params={"embedded": "1"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    # The served document is the native app shell (not a copied MoonMind UI).
    assert 'id="root"' in body
    # Browser-safe bootstrap is injected and marks embedded mode.
    assert "window.__MOONMIND_OMNIGENT_CHAT__=" in body
    assert '"mode":"embedded"' in body
    assert '"embedded":true' in body
    assert f'"chatBindingId":"{_CHAT_BINDING_ID}"' in body
    assert (
        f'"apiBase":"/api/workflow-chat-bindings/{_CHAT_BINDING_ID}/omnigent"' in body
    )
    # Asset URLs are scoped back through the MoonMind route.
    scoped = f"{NATIVE_UI_MOUNT_PATH}/{_CHAT_BINDING_ID}"
    assert f'src="{scoped}/assets/index-abc.js"' in body
    assert f'<base href="{scoped}/">' in body
    # The upstream SPA shell is fetched from the server index.
    assert upstream.paths == ["/"]


def test_embedded_document_never_leaks_provider_identity() -> None:
    client, _upstream = _build()

    response = client.get(_url(), params={"embedded": "1"})

    assert response.status_code == 200
    assert _PROVIDER_SESSION_ID not in response.text
    assert "prov-sess" not in response.text


def test_embedded_document_security_headers() -> None:
    client, _upstream = _build()

    response = client.get(_url(), params={"embedded": "1"})

    assert "frame-ancestors 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "same-origin"


# --- Full-page (Open in Omnigent) --------------------------------------------


def test_full_page_document_refuses_framing() -> None:
    client, _upstream = _build()

    response = client.get(_url())  # no embedded=1 -> full page

    assert response.status_code == 200
    assert '"mode":"full_page"' in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"


def test_deep_link_refresh_serves_spa_document() -> None:
    client, upstream = _build()

    response = client.get(_url("workflow/deep/link"), params={"embedded": "1"})

    assert response.status_code == 200
    assert "window.__MOONMIND_OMNIGENT_CHAT__=" in response.text
    # A deep-link/refresh fetches the upstream index for client-side routing.
    assert upstream.paths == ["/"]


# --- Assets ------------------------------------------------------------------


def test_asset_is_reverse_proxied_without_bootstrap() -> None:
    client, upstream = _build()

    response = client.get(_url("assets/index-abc.js"), params={"embedded": "1"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert response.text == "console.log('native-app');"
    assert "__MOONMIND_OMNIGENT_CHAT__" not in response.text
    assert response.headers["cache-control"] == "private, max-age=300"
    assert upstream.paths == ["/assets/index-abc.js"]


# --- Authorization -----------------------------------------------------------


def test_unauthorized_caller_is_non_enumerating() -> None:
    client, _upstream = _build(owner_id=uuid4())

    response = client.get(_url(), params={"embedded": "1"})

    assert response.status_code == 404
    assert "unavailable" in response.text.lower()
    # Never reveals whether a binding/provider session exists.
    assert _PROVIDER_SESSION_ID not in response.text


def test_unknown_binding_is_non_enumerating() -> None:
    client, _upstream = _build(store=_FakeStore(row=None))

    response = client.get(_url("assets/index-abc.js"))

    assert response.status_code == 404
    body = response.json()
    assert body["detail"]["code"] == "omnigent_native_chat_unavailable"


# --- Version compatibility gate ----------------------------------------------


def test_unsupported_native_ui_version_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNIGENT_NATIVE_UI_VERSION", "not-a-supported-build")
    client, _upstream = _build()

    response = client.get(_url(), params={"embedded": "1"})

    assert response.status_code == 503
    assert 'data-reason="native_ui_version_unsupported"' in response.text


def test_serving_disabled_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNIGENT_NATIVE_UI_ENABLED", "false")
    client, _upstream = _build()

    response = client.get(_url("assets/index-abc.js"))

    assert response.status_code == 503
    assert response.json()["detail"]["reason"] == "native_ui_serving_disabled"


# --- Upstream redirect scoping ------------------------------------------------


def test_upstream_redirect_is_kept_in_scope() -> None:
    upstream = _FakeUpstream(
        {
            "/": NativeUiUpstreamResponse(
                status_code=302,
                content=b"",
                media_type="text/html",
                location="https://omnigent.internal:8000/login",
            )
        }
    )
    client, _upstream = _build(upstream=upstream)

    response = client.get(_url(), params={"embedded": "1"}, follow_redirects=False)

    assert response.status_code == 302
    location = response.headers["location"]
    assert location == f"{NATIVE_UI_MOUNT_PATH}/{_CHAT_BINDING_ID}/login"
    assert "omnigent.internal" not in location


def test_upstream_error_serves_unavailable_document() -> None:
    upstream = _FakeUpstream(
        {
            "/": NativeUiUpstreamResponse(
                status_code=502, content=b"", media_type="text/html"
            )
        }
    )
    client, _upstream = _build(upstream=upstream)

    response = client.get(_url(), params={"embedded": "1"})

    assert response.status_code == 503
    assert 'data-reason="native_ui_upstream_unavailable"' in response.text


def test_relative_redirect_traversal_fails_closed_to_scope() -> None:
    base = scoped_ui_base(_CHAT_BINDING_ID)
    # A relative redirect that walks above the binding mount must not be emitted
    # verbatim (the browser would normalize it to an out-of-scope same-origin
    # path); it collapses to the scoped root instead.
    assert (
        _rewrite_upstream_location(
            "../../../../api/executions", scoped_base=base
        )
        == base + "/"
    )
    # A benign relative redirect stays under the scoped base.
    assert (
        _rewrite_upstream_location("dashboard/panel", scoped_base=base)
        == f"{base}/dashboard/panel"
    )
    # Query/fragment are preserved when the target stays in scope.
    assert (
        _rewrite_upstream_location("/login?next=%2Fhome", scoped_base=base)
        == f"{base}/login?next=%2Fhome"
    )


def test_relative_redirect_traversal_kept_in_scope_via_router() -> None:
    upstream = _FakeUpstream(
        {
            "/": NativeUiUpstreamResponse(
                status_code=302,
                content=b"",
                media_type="text/html",
                location="../../../../api/executions",
            )
        }
    )
    client, _upstream = _build(upstream=upstream)

    response = client.get(_url(), params={"embedded": "1"}, follow_redirects=False)

    assert response.status_code == 302
    location = response.headers["location"]
    assert location == f"{NATIVE_UI_MOUNT_PATH}/{_CHAT_BINDING_ID}/"
    assert "/api/executions" not in location


@pytest.mark.asyncio
async def test_fetch_aborts_when_asset_exceeds_limit(monkeypatch) -> None:
    # The limit is enforced while streaming, so a large/compressed upstream
    # response cannot buffer unbounded bytes into API-service memory.
    monkeypatch.setattr(native_ui_mod, "_NATIVE_UI_MAX_ASSET_BYTES", 8)

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x" * 4096,
            headers={"content-type": "application/javascript"},
        )

    upstream = NativeUiUpstream(
        base_url="https://omnigent.internal:8000",
        transport=httpx.MockTransport(_handler),
    )

    with pytest.raises(NativeUiUpstreamError):
        await upstream.fetch("/assets/too-big.js")


@pytest.mark.asyncio
async def test_fetch_returns_asset_under_limit() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"console.log('ok');",
            headers={"content-type": "application/javascript"},
        )

    upstream = NativeUiUpstream(
        base_url="https://omnigent.internal:8000",
        transport=httpx.MockTransport(_handler),
    )

    result = await upstream.fetch("/assets/index-abc.js")

    assert result.status_code == 200
    assert result.content == b"console.log('ok');"
    assert result.media_type == "application/javascript"
