"""MoonLadderStudios/MoonMind#4013 AC3: literal pinned consumer through served adapter.

Close the AC3 residual: execute the literal pinned upstream
``omnigent/web/src/lib/sessionUpdatesSocket.ts`` (at
``PINNED_OMNIGENT_COMMIT``) through the real served native-UI document in
embedded and full-page presentations.

* Upstream shell: the real ``omnigent/web/index.html`` at the pinned commit
  (``<div id="root">`` mount point + module script), not a handwritten stub.
* Adapter: extracted from the document rendered by the real
  :func:`moonmind.omnigent.native_ui.render_native_ui_document`, never a copy.
* Consumer: the real ``SessionUpdatesSocket`` class plus its
  ``buildUpdatesUrl``/``nextReconnectDelay`` helpers, extracted from the
  pinned file by brace counting and mechanically de-typed with explicit
  replacements (any leftover type syntax fails the test). The exact
  ``sendWatch`` guard quoted in the issue brief is asserted present before
  execution.
* No network: recording native transports, host-seam stubs, and a real
  reconnect timer through the shimmed global ``WebSocket``.

Covers: never-connected ``setWatched`` silent no-op (the ``null.send`` crash
path), open-socket watch frame, order-insensitive dedupe, non-open silence,
watch-set update resend, drop → scheduled reconnect → rebuild + resend,
``stop()`` terminal silence, and ``start()`` recovery.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from moonmind.omnigent.host_auth_adapter import PINNED_OMNIGENT_COMMIT
from moonmind.omnigent.native_ui import (
    build_chat_bootstrap,
    render_native_ui_document,
    scoped_api_base,
    scoped_ui_base,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PINNED_TS = (
    _REPO_ROOT / "omnigent" / "web" / "src" / "lib" / "sessionUpdatesSocket.ts"
)
_PINNED_INDEX_HTML = _REPO_ROOT / "omnigent" / "web" / "index.html"

_BINDING = "chatb_render123"

_ADAPTER_SCRIPT_RE = re.compile(
    r"<script>window\.__MOONMIND_OMNIGENT_CHAT__=.*?;\n(.*?)</script>",
    re.DOTALL,
)

# Node harness executing the LITERAL pinned upstream class against the ACTUAL
# extracted adapter. Argv: adapter.js, sessionUpdatesSocket.ts, binding id,
# bootstrap.json. No network.
_NODE_PINNED_SOCKET_HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const adapterSrc = fs.readFileSync(process.argv[2], 'utf8');
const tsSrc = fs.readFileSync(process.argv[3], 'utf8');
const bindingId = process.argv[4];

const constructedWs = [];
const sentFrames = [];
class RecordingNativeWebSocket {
  constructor(url, protocols) {
    this.url = String(url);
    this.protocols = protocols;
    this.readyState = RecordingNativeWebSocket.OPEN;
    this.onopen = null; this.onmessage = null; this.onerror = null; this.onclose = null;
    constructedWs.push(this);
  }
  send(data) { sentFrames.push(String(data)); }
  close() { this.readyState = RecordingNativeWebSocket.CLOSED; }
}
RecordingNativeWebSocket.CONNECTING = 0;
RecordingNativeWebSocket.OPEN = 1;
RecordingNativeWebSocket.CLOSING = 2;
RecordingNativeWebSocket.CLOSED = 3;

class RecordingNativeEventSource {
  constructor(url, config) { this.url = String(url); this.config = config; }
}
RecordingNativeEventSource.CONNECTING = 0;
RecordingNativeEventSource.OPEN = 1;
RecordingNativeEventSource.CLOSED = 2;

globalThis.window = globalThis;
globalThis.__MOONMIND_OMNIGENT_CHAT__ = JSON.parse(fs.readFileSync(process.argv[5], 'utf8'));
globalThis.location = {
  href: `https://moonmind.test/omnigent-ui/workflow-chat/${bindingId}/?embedded=1`,
  origin: 'https://moonmind.test', protocol: 'https:', host: 'moonmind.test',
  search: '?embedded=1', hash: '',
};
globalThis.history = { state: null, replaceState() {} };
globalThis.WebSocket = RecordingNativeWebSocket;
globalThis.EventSource = RecordingNativeEventSource;
globalThis.fetch = () => { throw new Error('fetch must not be called here'); };
globalThis.XMLHttpRequest = class { open() {} };
globalThis.document = { readyState: 'complete', getElementById: () => null, addEventListener: () => {} };
globalThis.addEventListener = () => {};
globalThis.MutationObserver = class { constructor() {} observe() {} disconnect() {} };

eval(adapterSrc);

// Identity of the exact guard quoted in the issue brief.
assert.ok(tsSrc.includes('if (this.ws?.readyState === WebSocket.OPEN) {'),
  'pinned file must carry the exact sendWatch guard');
assert.ok(tsSrc.includes('this.ws.send(JSON.stringify({ type: "watch", session_ids: this.watched }));'),
  'pinned file must carry the exact watch send');

function extractBlock(src, startMarker) {
  const start = src.indexOf(startMarker);
  assert.ok(start !== -1, `missing ${startMarker}`);
  let i = src.indexOf('{', start);
  let depth = 0;
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') { depth--; if (depth === 0) { i++; break; } }
  }
  return src.slice(start, i);
}
let classSrc = tsSrc.match(/export const HEARTBEAT_WATCHDOG_MS = [\d_]+;/)[0].replace('export const', 'const') + '\n'
  + tsSrc.match(/const RECONNECT_BASE_MS = [\d_]+;/)[0] + '\n'
  + tsSrc.match(/const RECONNECT_MAX_MS = [\d_]+;/)[0] + '\n'
  + extractBlock(tsSrc, 'function nextReconnectDelay')
  + '\n' + extractBlock(tsSrc, 'function buildUpdatesUrl')
  + '\n' + extractBlock(tsSrc, 'class SessionUpdatesSocket');

// Mechanical de-typing with explicit replacements; specific field patterns
// first, generic modifier strips after. Any leftover type syntax fails.
const reps = [
  ['private ws: WebSocket | null = null;', 'ws = null;'],
  ['private watched: string[] = [];', 'watched = [];'],
  ['private reconnectTimer: ReturnType<typeof setTimeout> | null = null;', 'reconnectTimer = null;'],
  ['private watchdogTimer: ReturnType<typeof setTimeout> | null = null;', 'watchdogTimer = null;'],
  ['private readonly ', ''], ['private ', ''], ['readonly ', ''],
  ['new Set<FrameListener>()', 'new Set()'],
  ['new Set<() => void>()', 'new Set()'],
  ['(listener: FrameListener): () => void {', '(listener) {'],
  ['(listener: () => void): () => void {', '(listener) {'],
  ['(value: boolean): void {', '(value) {'],
  ['(ids: string[]): void {', '(ids) {'],
  ['(event: MessageEvent): void {', '(event) {'],
  ['(): void {', '() {'],
  ['(): boolean {', '() {'],
  ['(failedAttempts: number): number {', '(failedAttempts) {'],
  ['(): string {', '() {'],
  ['let ws: WebSocket;', 'let ws;'],
  ['let frame: SessionUpdatesFrame;', 'let frame;'],
  [' as SessionUpdatesFrame', ''],
];
for (const [a, b] of reps) classSrc = classSrc.split(a).join(b);
const leftover = classSrc.match(/\bprivate\b|\breadonly\b|string\[\]|:\svoid|:\sboolean|:\sstring\b|ReturnType|FrameListener|SessionListWireItem|MessageEvent/);
assert.ok(!leftover, `TS remnants left in extracted class: ${leftover && leftover[0]}`);

function getOmnigentHostConfig() { return {}; }
function modalHostId() { return null; }
function resolveWebSocketUrl(path) { return String(path); }
eval(classSrc + '\n;globalThis.PinnedSessionUpdatesSocket = SessionUpdatesSocket;');

(async () => {
  const Pinned = globalThis.PinnedSessionUpdatesSocket;
  const sock = new Pinned();

  // #4013 crash path: never-connected setWatched is a silent no-op.
  const builtBefore = constructedWs.length;
  assert.doesNotThrow(() => sock.setWatched([bindingId]));
  assert.equal(constructedWs.length, builtBefore);
  assert.equal(sentFrames.length, 0);

  sock.start();
  assert.ok(sock.ws instanceof RecordingNativeWebSocket);
  const scopedUrl = constructedWs[constructedWs.length - 1].url;
  assert.ok(scopedUrl.startsWith('wss://'), `scoped ws url, got ${scopedUrl}`);
  assert.ok(scopedUrl.includes(`/api/workflow-chat-bindings/${bindingId}/omnigent/v1/sessions/updates`),
    `scoped ws path, got ${scopedUrl}`);

  sock.ws.onopen();
  assert.equal(sentFrames.length, 1);
  assert.deepEqual(JSON.parse(sentFrames[0]), { type: 'watch', session_ids: [bindingId] });

  // Same watch-set is a no-op (order-insensitive dedupe).
  sock.setWatched([bindingId]);
  assert.equal(sentFrames.length, 1);

  // Non-open sockets never send and never throw.
  for (const state of [WebSocket.CONNECTING, WebSocket.CLOSING, WebSocket.CLOSED]) {
    sock.ws.readyState = state;
    assert.doesNotThrow(() => sock.sendWatch());
  }
  assert.equal(sentFrames.length, 1);

  // Watch-set update while open re-sends.
  sock.ws.readyState = WebSocket.OPEN;
  sock.setWatched(['chatb_other', bindingId]);
  assert.equal(sentFrames.length, 2);
  assert.deepEqual(JSON.parse(sentFrames[1]).session_ids.sort(), [bindingId, 'chatb_other'].sort());

  // Drop schedules a real reconnect which rebuilds through the shim and re-sends.
  sock.ws.onclose();
  assert.equal(sock.ws, null);
  assert.ok(sock.reconnectTimer !== null);
  const countBefore = constructedWs.length;
  const deadline = Date.now() + 5000;
  while (constructedWs.length === countBefore && Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 50));
  }
  assert.equal(constructedWs.length, countBefore + 1, 'reconnect must construct a new socket');
  sock.ws.onopen();
  assert.deepEqual(JSON.parse(sentFrames[sentFrames.length - 1]).session_ids.sort(),
    [bindingId, 'chatb_other'].sort());

  // stop() is terminal silence: no throw, no send, no new socket.
  sock.stop();
  assert.equal(sock.ws, null);
  const sentBefore = sentFrames.length;
  const builtAtStop = constructedWs.length;
  assert.doesNotThrow(() => { sock.setWatched([bindingId]); sock.sendWatch(); });
  assert.equal(sentFrames.length, sentBefore);
  assert.equal(constructedWs.length, builtAtStop);

  // start() re-opens and watches again.
  sock.start();
  sock.ws.onopen();
  assert.ok(sentFrames.length > sentBefore);
  sock.stop();

  console.log('pinned upstream socket passed');
})().then(() => {}, (err) => { console.error(err); process.exit(1); });
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


def _pinned_head() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(_REPO_ROOT / "omnigent"), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None


def test_pinned_upstream_shell_renders_before_app_script() -> None:
    """The real pinned shell keeps its mount point with bootstrap first."""
    import pytest

    if not _PINNED_INDEX_HTML.is_file():
        pytest.skip("omnigent submodule is not checked out")
    if _pinned_head() != PINNED_OMNIGENT_COMMIT:
        pytest.skip(
            f"omnigent submodule is not at the pinned commit {PINNED_OMNIGENT_COMMIT}"
        )

    upstream_html = _PINNED_INDEX_HTML.read_text(encoding="utf-8")
    assert '<div id="root">' in upstream_html
    base = scoped_ui_base(_BINDING)
    bootstrap = build_chat_bootstrap(
        chat_binding_id=_BINDING,
        mode="embedded",
        read_only=False,
        capabilities=_capabilities(),
        state="available",
    )
    document = render_native_ui_document(
        upstream_html, bootstrap=bootstrap, scoped_base=base
    )
    assert f'<base href="{base}/">' in document
    assert '<div id="root">' in document
    # Bootstrap + adapter run before the app's own module script.
    assert document.index("__MOONMIND_OMNIGENT_CHAT__") < document.index(
        "/src/main.tsx"
    )
    # Root-absolute boot assets resolve through the scoped route.
    assert f'href="{base}/favicon.svg"' in document
    assert f'src="{base}/src/main.tsx"' in document
    assert scoped_api_base(_BINDING) in document


def test_pinned_socket_lifecycle_through_served_adapter(tmp_path) -> None:
    """Run the literal pinned class through the served adapter, both modes."""
    import pytest

    node = shutil.which("node")
    if node is None:  # pragma: no cover - CI provides node; local may not.
        pytest.skip("node is required to execute the served adapter")
    if not _PINNED_TS.is_file():
        pytest.skip("omnigent submodule is not checked out")
    if _pinned_head() != PINNED_OMNIGENT_COMMIT:
        pytest.skip(
            f"omnigent submodule is not at the pinned commit {PINNED_OMNIGENT_COMMIT}"
        )

    ts_text = _PINNED_TS.read_text(encoding="utf-8")
    assert "if (this.ws?.readyState === WebSocket.OPEN) {" in ts_text
    assert (
        'this.ws.send(JSON.stringify({ type: "watch", session_ids: this.watched }));'
        in ts_text
    )

    upstream_html = _PINNED_INDEX_HTML.read_text(encoding="utf-8")
    harness_path = tmp_path / "pinned_socket_harness.js"
    harness_path.write_text(_NODE_PINNED_SOCKET_HARNESS, encoding="utf-8")
    for mode in ("embedded", "full_page"):
        bootstrap = build_chat_bootstrap(
            chat_binding_id=_BINDING,
            mode=mode,  # type: ignore[arg-type]
            read_only=False,
            capabilities=_capabilities(),
            state="available",
        )
        document = render_native_ui_document(
            upstream_html, bootstrap=bootstrap, scoped_base=scoped_ui_base(_BINDING)
        )
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
            [node, str(harness_path), str(adapter_path), str(_PINNED_TS),
             _BINDING, str(bootstrap_path)],
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert completed.returncode == 0, (
            f"pinned socket harness failed ({mode}):\n"
            f"stdout: {completed.stdout}\n"
            f"stderr: {completed.stderr}"
        )
