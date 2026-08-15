"""Unit tests for native Omnigent UI serving primitives.

MoonLadderStudios/MoonMind#3638. Covers the browser-safe bootstrap contract, the
native UI/server version compatibility gate, the embedded vs full-page security
header policy, SPA-document vs hashed-asset classification, and bootstrap
injection / scoped asset-URL rewriting.
"""

from __future__ import annotations

import json

from moonmind.omnigent.host_auth_adapter import PINNED_OMNIGENT_COMMIT
from moonmind.omnigent.native_ui import (
    CODE_NATIVE_CHAT_UNAVAILABLE,
    NATIVE_UI_BOOTSTRAP_SCHEMA_VERSION,
    build_chat_bootstrap,
    evaluate_native_ui_compatibility,
    is_document_request,
    native_ui_security_headers,
    presentation_mode_from_query,
    render_native_ui_document,
    rewrite_asset_urls,
    scoped_api_base,
    scoped_ui_base,
    upstream_path_for,
)

_BINDING = "chatb_opaque123"


# --- presentation mode --------------------------------------------------------


def test_embedded_query_selects_embedded_mode() -> None:
    for value in ("1", "true", "TRUE", "yes", "on"):
        assert presentation_mode_from_query(value) == "embedded"


def test_missing_or_falsey_query_selects_full_page() -> None:
    for value in (None, "", "0", "false", "no", "off", "embedded"):
        assert presentation_mode_from_query(value) == "full_page"


# --- scoped bases -------------------------------------------------------------


def test_scoped_bases_are_binding_scoped_and_server_owned() -> None:
    assert scoped_ui_base(_BINDING) == f"/omnigent-ui/workflow-chat/{_BINDING}"
    assert scoped_api_base(_BINDING) == (
        f"/api/workflow-chat-bindings/{_BINDING}/omnigent"
    )


# --- compatibility gate -------------------------------------------------------


def test_pinned_version_is_compatible_by_default() -> None:
    result = evaluate_native_ui_compatibility(PINNED_OMNIGENT_COMMIT)

    assert result.ready is True
    assert result.reason is None
    assert result.reported_version == PINNED_OMNIGENT_COMMIT


def test_unknown_version_fails_closed() -> None:
    result = evaluate_native_ui_compatibility(None)

    assert result.ready is False
    assert result.reason == "native_ui_version_unknown"


def test_unsupported_version_fails_closed() -> None:
    result = evaluate_native_ui_compatibility("deadbeef-not-supported")

    assert result.ready is False
    assert result.reason == "native_ui_version_unsupported"
    assert result.reported_version == "deadbeef-not-supported"


def test_disabled_bridge_gates_serving() -> None:
    result = evaluate_native_ui_compatibility(PINNED_OMNIGENT_COMMIT, enabled=False)

    assert result.ready is False
    assert result.reason == "omnigent_disabled"


# --- bootstrap contract -------------------------------------------------------


def _capabilities(read_only: bool) -> dict[str, bool]:
    return {
        "viewTranscript": True,
        "readResources": True,
        "sendMessage": not read_only,
        "interruptTurn": not read_only,
        "resolveElicitation": not read_only,
        "createTerminal": False,
    }


def test_bootstrap_is_browser_safe_and_scoped() -> None:
    bootstrap = build_chat_bootstrap(
        chat_binding_id=_BINDING,
        mode="embedded",
        read_only=False,
        capabilities=_capabilities(read_only=False),
        state="available",
    )

    assert bootstrap["schemaVersion"] == NATIVE_UI_BOOTSTRAP_SCHEMA_VERSION
    assert bootstrap["chatBindingId"] == _BINDING
    assert bootstrap["apiBase"] == scoped_api_base(_BINDING)
    assert bootstrap["wsBase"] == scoped_api_base(_BINDING)
    assert bootstrap["mode"] == "embedded"
    assert bootstrap["embedded"] is True
    assert bootstrap["readOnly"] is False
    assert bootstrap["state"] == "available"
    assert bootstrap["capabilities"]["sendMessage"] is True

    # No server-owned identity anywhere in the bootstrap payload.
    serialized = json.dumps(bootstrap).lower()
    for forbidden in (
        "provider_session",
        "providersessionid",
        "endpoint",
        "upstream",
        "host_id",
        "runner",
        "credential",
        "workspace",
        "launch_policy",
        "profile",
        "omnigent_session",
        "bridge_session",
    ):
        assert forbidden not in serialized


def test_bootstrap_read_only_records_disabled_reasons() -> None:
    bootstrap = build_chat_bootstrap(
        chat_binding_id=_BINDING,
        mode="full_page",
        read_only=True,
        capabilities=_capabilities(read_only=True),
        state="ended",
        unavailable_reason=None,
    )

    assert bootstrap["embedded"] is False
    assert bootstrap["readOnly"] is True
    assert bootstrap["disabledReasons"]["sendMessage"] == "session_read_only"
    # createTerminal is always denied, not by read-only state.
    assert bootstrap["disabledReasons"]["createTerminal"] == "session_read_only"


def test_bootstrap_policy_denied_reason_when_live() -> None:
    caps = _capabilities(read_only=False)
    caps["sendMessage"] = False  # policy-denied while live
    bootstrap = build_chat_bootstrap(
        chat_binding_id=_BINDING,
        mode="embedded",
        read_only=False,
        capabilities=caps,
        state="available",
    )

    assert bootstrap["disabledReasons"]["sendMessage"] == (
        "policy_or_capability_denied"
    )


def test_bootstrap_projects_versioned_stable_capability_decisions() -> None:
    bootstrap = build_chat_bootstrap(
        chat_binding_id=_BINDING,
        mode="embedded",
        read_only=False,
        capabilities={"sendMessage": False},
        state="available",
        capability_schema_version="moonmind.omnigent.effective-capabilities.v1",
        capability_authority_digest="a" * 64,
        disabled_reasons={"sendMessage": "provider_generation_stale"},
    )
    assert bootstrap["disabledReasons"]["sendMessage"] == "provider_generation_stale"
    assert bootstrap["capabilitySchemaVersion"].endswith("v1")
    assert bootstrap["capabilityAuthorityDigest"] == "a" * 64


# --- security headers ---------------------------------------------------------


def test_embedded_document_headers_allow_self_framing_and_no_store() -> None:
    headers = native_ui_security_headers(mode="embedded", is_document=True)

    assert "frame-ancestors 'self'" in headers["Content-Security-Policy"]
    # connect-src confines fetch/XHR/EventSource/WebSocket to the MoonMind
    # origin so provider JS cannot reach an absolute upstream/external URL.
    assert "connect-src 'self'" in headers["Content-Security-Policy"]
    assert "worker-src 'none'" in headers["Content-Security-Policy"]
    assert headers["X-Frame-Options"] == "SAMEORIGIN"
    assert headers["Cache-Control"] == "no-store, private"
    assert headers["Vary"] == "Cookie"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "same-origin"
    assert headers["Cross-Origin-Resource-Policy"] == "same-origin"


def test_full_page_document_refuses_framing() -> None:
    headers = native_ui_security_headers(mode="full_page", is_document=True)

    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert headers["X-Frame-Options"] == "DENY"


def test_asset_headers_are_privately_cacheable() -> None:
    headers = native_ui_security_headers(mode="embedded", is_document=False)

    assert headers["Cache-Control"] == "private, max-age=300"
    assert "Vary" not in headers


# --- request classification ---------------------------------------------------


def test_document_requests_cover_root_and_deep_links() -> None:
    assert is_document_request(None) is True
    assert is_document_request("") is True
    assert is_document_request("/") is True
    assert is_document_request("workflow/deep/link") is True
    assert is_document_request("index.html") is True


def test_asset_requests_have_extensions() -> None:
    assert is_document_request("assets/index-abc123.js") is False
    assert is_document_request("assets/style.css") is False
    assert is_document_request("favicon.ico") is False


def test_upstream_path_maps_documents_to_index_and_assets_verbatim() -> None:
    assert upstream_path_for(None) == "/"
    assert upstream_path_for("workflow/deep/link") == "/"
    assert upstream_path_for("assets/index-abc.js") == "/assets/index-abc.js"


def test_upstream_path_rejects_traversal() -> None:
    assert upstream_path_for("../../etc/passwd") == "/"
    assert upstream_path_for("assets/../../secret.js") == "/"


# --- document rendering -------------------------------------------------------


_INDEX_HTML = (
    "<!doctype html><html><head><meta charset=\"utf-8\">"
    '<script type="module" src="/assets/index-abc.js"></script>'
    '<link rel="stylesheet" href="/assets/index-abc.css">'
    "</head><body><div id=\"root\"></div></body></html>"
)


def test_rewrite_asset_urls_scopes_root_absolute_refs() -> None:
    base = scoped_ui_base(_BINDING)
    rewritten = rewrite_asset_urls(_INDEX_HTML, scoped_base=base)

    assert f'src="{base}/assets/index-abc.js"' in rewritten
    assert f'href="{base}/assets/index-abc.css"' in rewritten


def test_rewrite_leaves_absolute_and_protocol_relative_urls() -> None:
    html = (
        '<a href="https://example.test/x">e</a>'
        '<script src="//cdn.example.test/y.js"></script>'
    )
    rewritten = rewrite_asset_urls(html, scoped_base=scoped_ui_base(_BINDING))

    assert rewritten == html


def test_render_document_injects_bootstrap_and_base() -> None:
    base = scoped_ui_base(_BINDING)
    bootstrap = build_chat_bootstrap(
        chat_binding_id=_BINDING,
        mode="embedded",
        read_only=False,
        capabilities=_capabilities(read_only=False),
        state="available",
    )

    document = render_native_ui_document(
        _INDEX_HTML, bootstrap=bootstrap, scoped_base=base
    )

    assert f'<base href="{base}/">' in document
    assert "window.__MOONMIND_OMNIGENT_CHAT__=" in document
    # Bootstrap appears before the app's own module script so it runs first.
    assert document.index("__MOONMIND_OMNIGENT_CHAT__") < document.index(
        "index-abc.js"
    )
    # The host adapter runs before BrowserRouter and sends every stock
    # root-relative transport through the binding-scoped facade.
    assert document.index("window.fetch =") < document.index("index-abc.js")
    assert '"/c/" + bindingId' in document
    assert "apiBase + url.pathname" in document
    assert "sameSocketHost" in document
    assert "window.EventSource =" in document
    assert "window.WebSocket =" in document
    assert "MutationObserver" in document
    assert "restoreScopedDocumentUrl" in document
    assert "beforeunload" not in document
    # Assets are scoped in the rendered document too.
    assert f'src="{base}/assets/index-abc.js"' in document


def test_render_document_escapes_closing_script_tag() -> None:
    base = scoped_ui_base(_BINDING)
    bootstrap = build_chat_bootstrap(
        chat_binding_id=_BINDING,
        mode="embedded",
        read_only=False,
        capabilities=_capabilities(read_only=False),
        state="available",
        labels={"note": "</script><script>alert(1)</script>"},
    )

    document = render_native_ui_document(
        _INDEX_HTML, bootstrap=bootstrap, scoped_base=base
    )

    # The injected bootstrap script is not prematurely terminated.
    assert "</script><script>alert(1)</script>" not in document
    assert "<\\/script>" in document


def test_code_constant_is_stable() -> None:
    assert CODE_NATIVE_CHAT_UNAVAILABLE == "omnigent_native_chat_unavailable"
