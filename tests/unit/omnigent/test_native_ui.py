"""Unit tests for native Omnigent UI serving primitives.

MoonLadderStudios/MoonMind#3638. Covers the browser-safe bootstrap contract, the
native UI/server version compatibility gate, the embedded vs full-page security
header policy, SPA-document vs hashed-asset classification, and bootstrap
injection / scoped asset-URL rewriting.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

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
    assert bootstrap["uiBase"] == scoped_ui_base(_BINDING)
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


def test_rewrite_scopes_srcset_candidates() -> None:
    # MoonLadderStudios/MoonMind#4013 AC5: srcset candidates ignore <base href>
    # the same way src/href do; unscoped candidates 404 at the origin root.
    base = scoped_ui_base(_BINDING)
    html = (
        '<img src="/assets/a.png" '
        'srcset="/assets/a.png 1x, /assets/b.png 2x" alt="x">'
    )
    rewritten = rewrite_asset_urls(html, scoped_base=base)

    assert f'srcset="{base}/assets/a.png 1x, {base}/assets/b.png 2x"' in rewritten
    assert f'src="{base}/assets/a.png"' in rewritten


def test_rewrite_scopes_css_url_references() -> None:
    # The missing root wordmark (/assets/omnigent-wordmark-*.svg) is this class
    # of miss: an inline CSS url() that never consults <base href>.
    base = scoped_ui_base(_BINDING)
    html = (
        '<div style="background:url(/assets/omnigent-wordmark-x.svg)"></div>'
        "<div style='background: url(\"/assets/b.png\")'></div>"
    )
    rewritten = rewrite_asset_urls(html, scoped_base=base)

    assert f"url({base}/assets/omnigent-wordmark-x.svg)" in rewritten
    assert f'url("{base}/assets/b.png")' in rewritten


def test_rewrite_leaves_absolute_srcset_and_css_urls() -> None:
    html = (
        '<img srcset="https://cdn.example.test/a.png 1x, //cdn.example.test/b.png 2x">'
        '<div style="background:url(https://cdn.example.test/c.png)"></div>'
    )
    rewritten = rewrite_asset_urls(html, scoped_base=scoped_ui_base(_BINDING))

    assert rewritten == html


def test_rendered_document_scopes_wordmark_asset() -> None:
    base = scoped_ui_base(_BINDING)
    bootstrap = build_chat_bootstrap(
        chat_binding_id=_BINDING,
        mode="embedded",
        read_only=False,
        capabilities=_capabilities(read_only=False),
        state="available",
    )
    html = (
        "<!doctype html><html><head></head><body>"
        '<img src="/assets/omnigent-wordmark-x.svg" alt="w">'
        "</body></html>"
    )
    document = render_native_ui_document(html, bootstrap=bootstrap, scoped_base=base)

    assert f'src="{base}/assets/omnigent-wordmark-x.svg"' in document


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


# --- injected transport-adapter regression (MoonLadderStudios/MoonMind#4013) ---

_ADAPTER_SCRIPT_RE = re.compile(
    r"<script>window\.__MOONMIND_OMNIGENT_CHAT__=.*?;\n(.*?)</script>",
    re.DOTALL,
)

# Node harness executing the ACTUAL injected adapter (extracted from the
# rendered document, not a handwritten copy). It installs recording native
# transports (no network), evals the adapter, then asserts:
# * WebSocket CONNECTING/OPEN/CLOSING/CLOSED statics survive the shim;
# * EventSource CONNECTING/OPEN/CLOSED statics survive the shim;
# * the pinned upstream sendWatch guard (`ws?.readyState === WebSocket.OPEN`)
#   no longer crashes on null and sends only on open sockets;
# * the pinned SessionUpdatesSocket lifecycle shape (connect / setWatched /
#   reconnect / stop / start / dispose) behaves with the restored constants:
#   every socket is constructed through the shimmed global WebSocket;
# * construction still delegates to the native constructor with scoped URLs.
_NODE_ADAPTER_HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const adapterSrc = fs.readFileSync(process.argv[2], 'utf8');
assert.ok(
  adapterSrc.includes('Object.setPrototypeOf(window.WebSocket, NativeWebSocket)'),
  'adapter must preserve WebSocket constructor statics',
);
assert.ok(
  adapterSrc.includes('Object.setPrototypeOf(window.EventSource, NativeEventSource)'),
  'adapter must preserve EventSource constructor statics',
);

const constructedWs = [];
class RecordingNativeWebSocket {
  constructor(url, protocols) {
    this.url = String(url);
    this.protocols = protocols;
    this.readyState = RecordingNativeWebSocket.OPEN;
    constructedWs.push({ url: this.url, protocols });
  }
  send() {}
}
RecordingNativeWebSocket.CONNECTING = 0;
RecordingNativeWebSocket.OPEN = 1;
RecordingNativeWebSocket.CLOSING = 2;
RecordingNativeWebSocket.CLOSED = 3;

const constructedEs = [];
class RecordingNativeEventSource {
  constructor(url, config) {
    this.url = String(url);
    this.config = config;
    constructedEs.push({ url: this.url });
  }
}
RecordingNativeEventSource.CONNECTING = 0;
RecordingNativeEventSource.OPEN = 1;
RecordingNativeEventSource.CLOSED = 2;

globalThis.window = globalThis;
globalThis.__MOONMIND_OMNIGENT_CHAT__ = {
  chatBindingId: 'chatb_test123',
  apiBase: '/api/workflow-chat-bindings/chatb_test123/omnigent',
};
globalThis.location = {
  href: 'https://moonmind.test/omnigent-ui/workflow-chat/chatb_test123/?embedded=1',
  origin: 'https://moonmind.test',
  protocol: 'https:',
  host: 'moonmind.test',
  search: '?embedded=1',
  hash: '',
};
globalThis.history = { state: null, replaceState() {} };
globalThis.WebSocket = RecordingNativeWebSocket;
globalThis.EventSource = RecordingNativeEventSource;
globalThis.fetch = () => { throw new Error('fetch must not be called here'); };
globalThis.XMLHttpRequest = class { open() {} };
globalThis.document = {
  readyState: 'complete',
  getElementById: () => null,
  addEventListener: () => {},
};
globalThis.addEventListener = () => {};
globalThis.removeEventListener = () => {};
const addedWindowListeners = [];
const _recordAddEventListener = globalThis.addEventListener;
globalThis.addEventListener = (type, ...rest) => {
  addedWindowListeners.push(String(type));
  return _recordAddEventListener(type, ...rest);
};
globalThis.MutationObserver = class {
  constructor() {}
  observe() {}
  disconnect() {}
};

eval(adapterSrc);

const HostedWebSocket = globalThis.WebSocket;
const HostedEventSource = globalThis.EventSource;
assert.notEqual(HostedWebSocket, RecordingNativeWebSocket);
assert.equal(HostedWebSocket.prototype, RecordingNativeWebSocket.prototype);
assert.equal(Object.getPrototypeOf(HostedWebSocket), RecordingNativeWebSocket);
for (const [name, value] of Object.entries({ CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 })) {
  assert.equal(HostedWebSocket[name], value, `WebSocket.${name}`);
}
assert.equal(Object.getPrototypeOf(HostedEventSource), RecordingNativeEventSource);
for (const [name, value] of Object.entries({ CONNECTING: 0, OPEN: 1, CLOSED: 2 })) {
  assert.equal(HostedEventSource[name], value, `EventSource.${name}`);
}

// Pinned upstream SessionUpdatesSocket.sendWatch shape.
function sendWatch(ws) {
  if (ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'watch', session_ids: ['chatb_test123'] }));
  }
}
assert.doesNotThrow(() => sendWatch(null));
assert.doesNotThrow(() => sendWatch(undefined));
let sends = 0;
for (const readyState of [0, 2, 3]) {
  sendWatch({ readyState, send() { sends++; } });
}
assert.equal(sends, 0);
sendWatch({ readyState: RecordingNativeWebSocket.OPEN, send() { sends++; } });
assert.equal(sends, 1);

// Scoped URL rewriting + constructor delegation still intact.
const ws1 = new HostedWebSocket('/v1/sessions/updates');
assert.ok(ws1 instanceof RecordingNativeWebSocket);
assert.ok(
  constructedWs[constructedWs.length - 1].url.includes(
    '/api/workflow-chat-bindings/chatb_test123/omnigent/v1/sessions/updates',
  ),
  `scoped ws url, got ${constructedWs[constructedWs.length - 1].url}`,
);
assert.ok(constructedWs[constructedWs.length - 1].url.startsWith('wss://'));
const ws2 = new HostedWebSocket('/v1/sessions/updates', 'omnigent.workflow-chat.v1');
assert.equal(constructedWs[constructedWs.length - 1].protocols, 'omnigent.workflow-chat.v1');
const es1 = new HostedEventSource('/v1/sessions/chatb_test123/stream');
assert.ok(
  constructedEs[constructedEs.length - 1].url.includes(
    '/api/workflow-chat-bindings/chatb_test123/omnigent/v1/sessions/chatb_test123/stream',
  ),
  `scoped EventSource url, got ${constructedEs[constructedEs.length - 1].url}`,
);
// Bounded readiness/fatal signaling for the embedding shell (#4013 AC7/PLAN5).
assert.ok(
  adapterSrc.includes('moonmind.omnigent.chat.ready'),
  'adapter must announce readiness to the embedding shell',
);
assert.ok(
  adapterSrc.includes('moonmind.omnigent.chat.fatal'),
  'adapter must announce fatal failure to the embedding shell',
);
// Signals target the exact origin and carry no provider identity.
assert.ok(
  adapterSrc.includes('window.location.origin'),
  'adapter signals must target the exact origin',
);
assert.ok(
  !adapterSrc.includes('providerSession') && !adapterSrc.includes('provider_session'),
  'adapter signals must not carry provider identity',
);
assert.ok(
  addedWindowListeners.includes('error') &&
    addedWindowListeners.includes('unhandledrejection'),
  `adapter must subscribe to crash surfaces, got ${addedWindowListeners}`,
);
// Watch/reconnect/stop/start/disposal lifecycle of the pinned consumer shape
// (MoonLadderStudios/MoonMind#4013 AC4). This mirrors
// omnigent/web/src/lib/sessionUpdatesSocket.ts at the pinned commit: a
// watch-set, one nullable socket, the exact sendWatch guard, and lifecycle
// methods. Every socket is constructed through the shimmed global WebSocket,
// so scoped URL rewriting, native delegation, subprotocol forwarding, and the
// restored ready-state constants are all exercised — never stubbed around.
class PinnedShapeSessionUpdatesSocket {
  constructor(url) {
    this.url = url;
    this.ws = null;
    this.watched = new Set();
    this.disposed = false;
    this.lastSent = undefined;
  }
  connect() {
    if (this.disposed) return null;
    const sock = new WebSocket(this.url, 'omnigent.workflow-chat.v1');
    const self = this;
    const nativeSend = sock.send.bind(sock);
    sock.send = (data) => { self.lastSent = String(data); nativeSend(data); };
    this.ws = sock;
    return sock;
  }
  sendWatch() {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'watch', session_ids: [...this.watched] }));
    }
  }
  setWatched(ids) {
    if (this.disposed) return;
    this.watched = new Set(ids);
    this.sendWatch();
  }
  reconnect() {
    if (this.disposed) return;
    if (this.ws) this.ws.readyState = WebSocket.CLOSED;
    this.connect();
    this.sendWatch();
  }
  stop() {
    if (this.ws) this.ws.readyState = WebSocket.CLOSED;
    this.watched.clear();
  }
  start(ids) {
    if (this.disposed) return;
    this.connect();
    this.setWatched(ids);
  }
  dispose() {
    this.stop();
    this.disposed = true;
    this.ws = null;
  }
}

// Lifecycle on a never-connected socket: the exact #4013 crash path must be a
// silent no-op, never `null.send`.
const lifecycle = new PinnedShapeSessionUpdatesSocket('/v1/sessions/updates');
assert.doesNotThrow(() => lifecycle.setWatched(['chatb_test123']));
assert.equal(lifecycle.lastSent, undefined);

// Open socket: exactly one watch frame carrying the watch-set.
lifecycle.connect();
assert.ok(lifecycle.ws instanceof RecordingNativeWebSocket);
lifecycle.setWatched(['chatb_test123']);
assert.ok(lifecycle.lastSent, 'open socket must send watch');
assert.deepEqual(JSON.parse(lifecycle.lastSent), { type: 'watch', session_ids: ['chatb_test123'] });

// Non-open sockets never send.
for (const state of [WebSocket.CONNECTING, WebSocket.CLOSING, WebSocket.CLOSED]) {
  lifecycle.lastSent = undefined;
  lifecycle.ws.readyState = state;
  assert.doesNotThrow(() => lifecycle.sendWatch());
  assert.equal(lifecycle.lastSent, undefined, `no send in state ${state}`);
}

// Reconnect replaces the socket and re-sends the watch once.
lifecycle.ws.readyState = WebSocket.OPEN;
const socketsBeforeReconnect = constructedWs.length;
lifecycle.lastSent = undefined;
lifecycle.reconnect();
assert.equal(constructedWs.length, socketsBeforeReconnect + 1, 'reconnect must construct a new socket');
assert.ok(lifecycle.lastSent, 'reconnect must re-send watch');
assert.deepEqual(JSON.parse(lifecycle.lastSent).session_ids, ['chatb_test123']);
assert.ok(constructedWs[constructedWs.length - 1].url.startsWith('wss://'));
assert.equal(constructedWs[constructedWs.length - 1].protocols, 'omnigent.workflow-chat.v1');

// stop() closes and clears: further watch attempts send nothing.
lifecycle.stop();
lifecycle.lastSent = undefined;
assert.doesNotThrow(() => lifecycle.sendWatch());
assert.equal(lifecycle.lastSent, undefined);

// start() re-opens and watches again.
lifecycle.lastSent = undefined;
lifecycle.start(['chatb_test123']);
assert.ok(lifecycle.lastSent, 'start must send watch');

// dispose() is terminal: no throw, no send, no new socket.
const socketsBeforeDispose = constructedWs.length;
lifecycle.dispose();
lifecycle.lastSent = undefined;
assert.doesNotThrow(() => lifecycle.setWatched(['chatb_test123']));
assert.doesNotThrow(() => lifecycle.connect());
assert.equal(lifecycle.lastSent, undefined);
assert.equal(constructedWs.length, socketsBeforeDispose, 'dispose must not construct sockets');

console.log('adapter transport regression passed');
"""


def test_injected_adapter_preserves_transport_statics_and_sendwatch(
    tmp_path,
) -> None:
    """Execute the actual injected adapter; the #4013 crash must be gone.

    MoonLadderStudios/MoonMind#4013: the shim replaced ``window.WebSocket``
    with only a prototype assignment, dropping ``WebSocket.OPEN`` (and the
    other ready-state constants). The pinned upstream ``sendWatch`` guard then
    treated a null socket as open (``undefined === undefined``) and crashed on
    ``null.send``, while genuinely open sockets never sent. A string-contains
    assertion cannot catch this; this test runs the real injected script,
    including the pinned connect/setWatched/reconnect/stop/start/dispose
    lifecycle shape (AC4) with every socket constructed through the shim.
    """

    node = shutil.which("node")
    if node is None:  # pragma: no cover - CI provides node; local may not.
        import pytest

        pytest.skip("node is required to execute the injected adapter")

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
    match = _ADAPTER_SCRIPT_RE.search(document)
    assert match is not None, "injected adapter script missing from document"
    adapter_src = match.group(1)
    assert "window.WebSocket =" in adapter_src

    adapter_path = tmp_path / "adapter.js"
    harness_path = tmp_path / "harness.js"
    adapter_path.write_text(adapter_src, encoding="utf-8")
    harness_path.write_text(_NODE_ADAPTER_HARNESS, encoding="utf-8")

    completed = subprocess.run(
        [node, str(harness_path), str(adapter_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, (
        f"adapter transport regression failed:\n"
        f"stdout: {completed.stdout}\n"
        f"stderr: {completed.stderr}"
    )
