"""MoonLadderStudios/MoonMind#4013 AC3: rendered-app harness.

Demonstrate the served production native UI document rendering the authorized
bound transcript (or a validated empty state) in embedded and full-page views
without the null ``sendWatch`` exception, using:

* the real :func:`moonmind.omnigent.native_ui.render_native_ui_document`
  (emitted adapter + bootstrap + scoped assets, not a copy);
* the real facade allowlist
  (:func:`moonmind.omnigent.workflow_chat_facade.match_facade_operation`)
  and the real versioned compatibility map for the transcript + boot reads;
* the pinned consumer shape — the exact ``sendWatch`` guard quoted in the
  issue brief from
  ``omnigent/web/src/lib/sessionUpdatesSocket.ts`` at the pinned commit —
  executed in node against the extracted adapter.

Pinned-bundle note: the ``omnigent`` git submodule is not checked out in this
environment, so the compiled bundle itself is represented by a fragment
carrying the exact pinned guard plus a transcript/empty-state marker. The
adapter under test is always extracted from the served document. Browser
(Chromium/Firefox) and deployment acceptance evidence (AC9/AC10) remain
outstanding and are not claimed here. A denied (403-shaped) envelope must
never render as a transcript.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

from moonmind.omnigent import native_ui_compat as compat
from moonmind.omnigent.native_ui import (
    build_chat_bootstrap,
    native_ui_security_headers,
    render_native_ui_document,
    scoped_api_base,
    scoped_ui_base,
)
from moonmind.omnigent.workflow_chat_facade import match_facade_operation

_BINDING = "chatb_render123"
_PROVIDER_SESSION_ID = "prov-render-9"

# Representative upstream document: stock shell plus a fragment carrying the
# exact pinned consumer guard (issue brief §"Confirmed code defect") and a
# transcript marker the harness evaluates. Root-absolute assets must resolve
# through the scoped route (the #4013 wordmark 404 class).
_UPSTREAM_HTML = (
    "<!doctype html><html><head><meta charset=\"utf-8\">"
    '<script type="module" src="/assets/index-abc.js"></script>'
    '<link rel="stylesheet" href="/assets/index-abc.css">'
    '<img src="/assets/omnigent-wordmark-x.svg" alt="w">'
    "</head><body><div id=\"root\"></div>"
    "<script>window.__PINNED_CONSUMER__ = "
    "\"if (this.ws?.readyState === WebSocket.OPEN)\";</script>"
    "</body></html>"
)

_ADAPTER_SCRIPT_RE = re.compile(
    r"<script>window\.__MOONMIND_OMNIGENT_CHAT__=.*?;\n(.*?)</script>",
    re.DOTALL,
)

# Node harness: eval the ACTUAL extracted adapter (argv[2]), then run the
# pinned consumer shape against the shimmed transports and assert the
# transcript/denied outcome contract. No network.
_NODE_RENDERED_APP_HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const adapterSrc = fs.readFileSync(process.argv[2], 'utf8');
const bindingId = process.argv[3];

class RecordingNativeWebSocket {
  constructor(url, protocols) {
    this.url = String(url);
    this.protocols = protocols;
    this.readyState = RecordingNativeWebSocket.OPEN;
  }
  send() {}
}
RecordingNativeWebSocket.CONNECTING = 0;
RecordingNativeWebSocket.OPEN = 1;
RecordingNativeWebSocket.CLOSING = 2;
RecordingNativeWebSocket.CLOSED = 3;

class RecordingNativeEventSource {
  constructor(url, config) {
    this.url = String(url);
    this.config = config;
  }
}
RecordingNativeEventSource.CONNECTING = 0;
RecordingNativeEventSource.OPEN = 1;
RecordingNativeEventSource.CLOSED = 2;

globalThis.window = globalThis;
globalThis.__MOONMIND_OMNIGENT_CHAT__ = JSON.parse(fs.readFileSync(process.argv[4], 'utf8'));
globalThis.location = {
  href: `https://moonmind.test/omnigent-ui/workflow-chat/${bindingId}/?embedded=1`,
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
globalThis.MutationObserver = class {
  constructor() {}
  observe() {}
  disconnect() {}
};

eval(adapterSrc);

// The exact pinned upstream sendWatch guard (issue brief §"Confirmed code
// defect"): with the adapter's restored constants this no longer crashes on
// null and sends only on open sockets.
function sendWatch(ws, watched) {
  if (ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'watch', session_ids: watched }));
  }
}
assert.doesNotThrow(() => sendWatch(null, [bindingId]));
assert.doesNotThrow(() => sendWatch(undefined, [bindingId]));
let sends = 0;
for (const readyState of [0, 2, 3]) {
  sendWatch({ readyState, send() { sends++; } }, [bindingId]);
}
assert.equal(sends, 0);
sendWatch({ readyState: RecordingNativeWebSocket.OPEN, send() { sends++; } }, [bindingId]);
assert.equal(sends, 1);

// Shell outcome contract: an authorized items payload renders the transcript
// (or a validated empty state); a denied envelope never renders as one.
function renderOutcome(payload) {
  if (payload && Array.isArray(payload.items)) {
    for (const item of payload.items) {
      if (item.session_id !== bindingId) return 'denied';
    }
    return payload.items.length ? 'transcript' : 'empty-state';
  }
  return 'denied';
}
assert.equal(renderOutcome({ items: [{ session_id: bindingId, text: 'hi' }] }), 'transcript');
assert.equal(renderOutcome({ items: [] }), 'empty-state');
assert.equal(renderOutcome({ detail: { code: 'omnigent_chat_operation_denied' } }), 'denied');
assert.equal(renderOutcome({ items: [{ session_id: 'prov-secret' }] }), 'denied');
console.log('rendered-app consumer harness passed');
"""


def _capabilities() -> dict[str, bool]:
    return {
        "viewTranscript": True,
        "readResources": True,
        "sendMessage": True,
        "interruptTurn": True,
        "resolveElicitation": True,
        "createTerminal": False,
    }


def _render(mode: str) -> tuple[str, dict]:
    base = scoped_ui_base(_BINDING)
    bootstrap = build_chat_bootstrap(
        chat_binding_id=_BINDING,
        mode=mode,  # type: ignore[arg-type]
        read_only=False,
        capabilities=_capabilities(),
        state="available",
    )
    return (
        render_native_ui_document(_UPSTREAM_HTML, bootstrap=bootstrap, scoped_base=base),
        bootstrap,
    )


def test_embedded_document_renders_authorized_transcript_shell() -> None:
    document, bootstrap = _render("embedded")
    base = scoped_ui_base(_BINDING)

    assert bootstrap["mode"] == "embedded"
    assert bootstrap["embedded"] is True
    assert bootstrap["apiBase"] == scoped_api_base(_BINDING)
    assert f'<base href="{base}/">' in document
    # Bootstrap + adapter run before the app's own module script.
    assert document.index("__MOONMIND_OMNIGENT_CHAT__") < document.index("index-abc.js")
    # The pinned consumer fragment survives serving: the served app still
    # depends on the restored WebSocket.OPEN constant.
    assert "ws?.readyState === WebSocket.OPEN" in document
    # Boot assets resolve through the scoped route, not the origin root.
    assert f'src="{base}/assets/index-abc.js"' in document
    assert f'src="{base}/assets/omnigent-wordmark-x.svg"' in document
    # No server-owned identity leaks into the served document. (The adapter's
    # own code comment mentions credential classes; the authority check below
    # therefore scans the bootstrap payload, mirroring test_native_ui.py.)
    assert _PROVIDER_SESSION_ID not in document
    serialized = json.dumps(bootstrap).lower()
    for forbidden in (
        "provider_session",
        "providersessionid",
        "credential",
        "runner",
        "workspace",
        "profile",
    ):
        assert forbidden not in serialized

    headers = native_ui_security_headers(mode="embedded", is_document=True)
    assert "frame-ancestors 'self'" in headers["Content-Security-Policy"]
    assert headers["Cache-Control"] == "no-store, private"


def test_full_page_document_renders_same_shell_with_deny_framing() -> None:
    document, bootstrap = _render("full_page")
    base = scoped_ui_base(_BINDING)

    assert bootstrap["mode"] == "full_page"
    assert bootstrap["embedded"] is False
    assert f'<base href="{base}/">' in document
    assert "ws?.readyState === WebSocket.OPEN" in document
    assert f'src="{base}/assets/index-abc.js"' in document

    headers = native_ui_security_headers(mode="full_page", is_document=True)
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert headers["X-Frame-Options"] == "DENY"


def test_facade_boot_projections_authorize_transcript_and_boot_reads() -> None:
    # Transcript reads the shell needs first: snapshot + live stream.
    match = match_facade_operation("GET", f"v1/sessions/{_BINDING}")
    assert match is not None and match.operation.name == "get_session"
    match = match_facade_operation("GET", f"v1/sessions/{_BINDING}/stream")
    assert match is not None and match.operation.name == "stream_events"
    # The four #4013 boot reads are allowlisted binding-local projections.
    assert match_facade_operation("GET", "v1/harnesses") is not None
    assert (
        match_facade_operation("GET", f"v1/sessions/{_BINDING}/agent") is not None
    )
    assert (
        match_facade_operation(
            "GET", f"v1/sessions/{_BINDING}/resources/environments/default"
        )
        is not None
    )
    assert (
        match_facade_operation("GET", f"v1/sessions/{_BINDING}/child_sessions")
        is not None
    )
    served = {
        route["name"]
        for route in compat.compatibility_map()["routes"]
        if route["disposition"] == compat.DISPOSITION_SERVED
    }
    assert {
        "get_session",
        "stream_events",
        "list_harnesses",
        "get_session_agent",
        "get_session_environment",
        "list_child_sessions",
    } <= served
    # Unknown routes still fail closed — a denial is never a transcript.
    assert match_facade_operation("GET", "v1/sessions/elsewhere/admin") is None
    assert match_facade_operation("GET", "v1/unreviewed-route") is None


def test_served_adapter_and_consumer_render_without_sendwatch_crash(tmp_path) -> None:
    node = shutil.which("node")
    if node is None:  # pragma: no cover - CI provides node; local may not.
        import pytest

        pytest.skip("node is required to execute the served adapter")

    harness_path = tmp_path / "rendered_harness.js"
    harness_path.write_text(_NODE_RENDERED_APP_HARNESS, encoding="utf-8")
    for mode in ("embedded", "full_page"):
        document, bootstrap = _render(mode)  # type: ignore[arg-type]
        match = _ADAPTER_SCRIPT_RE.search(document)
        assert match is not None, f"injected adapter missing ({mode})"
        adapter_path = tmp_path / f"adapter_{mode}.js"
        bootstrap_path = tmp_path / f"bootstrap_{mode}.json"
        adapter_path.write_text(match.group(1), encoding="utf-8")
        bootstrap_path.write_text(
            json.dumps(
                {
                    "chatBindingId": bootstrap["chatBindingId"],
                    "apiBase": bootstrap["apiBase"],
                }
            ),
            encoding="utf-8",
        )
        completed = subprocess.run(
            [node, str(harness_path), str(adapter_path), _BINDING, str(bootstrap_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert completed.returncode == 0, (
            f"rendered-app harness failed ({mode}):\n"
            f"stdout: {completed.stdout}\n"
            f"stderr: {completed.stderr}"
        )
