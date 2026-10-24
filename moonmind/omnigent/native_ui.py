"""Native Omnigent Workflow Chat UI serving primitives.

MoonLadderStudios/MoonMind#3638. MoonMind serves the provider-maintained native
Omnigent web application as the Workflow Chat UI through MoonMind-controlled,
binding-scoped routes (``docs/UI/WorkflowChatPanel.md`` §4,
``docs/Omnigent/OmnigentBridge.md`` §4.2, §6 ``workflowChat``):

```text
/omnigent-ui/workflow-chat/{chatBindingId}[?embedded=1]
```

The browser loads the native UI assets and every application request exclusively
through MoonMind-scoped routes. It never connects directly to the upstream
Omnigent server and never receives a raw provider session id, upstream URL,
credential, host/runner id, profile ref, or workspace authority.

This module holds the runtime-neutral primitives the FastAPI serving router
composes so they can be unit-tested in isolation and the browser-boundary
semantics stay in one place:

* the browser-safe bootstrap contract (:func:`build_chat_bootstrap`);
* the serving gate (:func:`native_ui_unavailable_reason`);
* the embedded vs full-page security-header policy
  (:func:`native_ui_security_headers`);
* SPA-document vs hashed-asset request classification
  (:func:`is_document_request`, :func:`upstream_path_for`); and
* bootstrap injection and scoped asset-URL rewriting for the served document
  (:func:`render_native_ui_document`).

Keeping the mechanism here (not inline in the router) mirrors
``moonmind/omnigent/workflow_chat_facade.py``, the sibling binding-scoped HTTP/SSE
API facade (MoonLadderStudios/MoonMind#3634).
"""

from __future__ import annotations

import html
import json
import re
from typing import Any, Literal, Mapping

# --- Route / contract constants ----------------------------------------------
# The native UI is served under this fixed, server-owned mount prefix. The
# browser never authors an upstream route; it only ever navigates within this
# binding-scoped prefix (docs/Omnigent/OmnigentBridge.md §6 ``uiMountPath``).
NATIVE_UI_MOUNT_PREFIX = "/omnigent-ui/workflow-chat"

# Stable browser bootstrap schema version. The native application reads the
# injected ``window.__MOONMIND_OMNIGENT_CHAT__`` object; a compatibility change
# to the contract shape bumps this version.
NATIVE_UI_BOOTSTRAP_SCHEMA_VERSION = "moonmind.omnigent_native_ui.bootstrap.v1"

# Versioned scoped route/feature manifest. Bumped when the scoped serving routes
# or the bootstrap features the native UI depends on change in a way that
# requires a matching native UI build.
NATIVE_UI_ROUTE_FEATURE_VERSION = "1"

# Stable, actionable failure code when native chat cannot be served. The
# serving router returns this instead of partially bypassing the scoped facade
# (issue #3638 requirement 6).
CODE_NATIVE_CHAT_UNAVAILABLE = "omnigent_native_chat_unavailable"

PresentationMode = Literal["embedded", "full_page"]

_TRUE_QUERY_VALUES = {"1", "true", "yes", "on"}


# Chat-binding ids are server-generated in two shapes, both validated by this
# one allowlist before serving code reflects them into HTML, redirects, or
# scoped routes:
# - ``chatb_`` + urlsafe-base64 from the bridge store
#   (``bridge_store._generate_chat_binding_id``): the browser-facing binding
#   authority the canonical flow propagates as ``chat_binding_ref``; and
# - ``omc_`` + 40 lowercase hex chars from the canonical control-plane
#   resolver (deterministic session digest), which shares the
#   ``chat_binding_id`` field as an internal dedup key.
# Unifying the generators on a single format is out of scope for a serving
# validator: the digest shape is load-bearing for idempotent session
# establishment while the opaque shape is load-bearing for unguessable browser
# handles. Anything outside both exact shapes still fails closed.
_CHAT_BINDING_ID_PATTERN = re.compile(r"^chatb_[A-Za-z0-9_-]{1,128}$")
_OMC_CHAT_BINDING_ID_PATTERN = re.compile(r"^omc_[0-9a-f]{40}$")


def is_valid_chat_binding_id(value: Any) -> bool:
    """Return whether an incoming binding id matches a server-generated shape."""

    text = str(value or "").strip()
    return bool(
        _CHAT_BINDING_ID_PATTERN.fullmatch(text)
        or _OMC_CHAT_BINDING_ID_PATTERN.fullmatch(text)
    )


def scoped_ui_base(chat_binding_id: str) -> str:
    """Return the binding-scoped native UI base path (no trailing slash)."""

    return f"{NATIVE_UI_MOUNT_PREFIX}/{chat_binding_id}"


def scoped_api_base(chat_binding_id: str) -> str:
    """Return the binding-scoped API/SSE facade base path (no trailing slash).

    Mirrors the value constructed by ``resolve_workflow_chat_binding`` so the
    served document and the ``chat-binding`` resolution API agree on one
    server-owned scoped base (MoonLadderStudios/MoonMind#3634).
    """

    return f"/api/workflow-chat-bindings/{chat_binding_id}/omnigent"


def presentation_mode_from_query(value: Any) -> PresentationMode:
    """Map the ``embedded`` query parameter to a presentation mode.

    ``embedded=1`` (or any truthy token) selects embedded (iframe/microfrontend)
    presentation; anything else — including a missing parameter — selects the
    full-page **Open in Omnigent** presentation.
    """

    token = str(value if value is not None else "").strip().lower()
    return "embedded" if token in _TRUE_QUERY_VALUES else "full_page"


# --- Serving gate -------------------------------------------------------------


def native_ui_unavailable_reason(
    *, omnigent_enabled: bool, serving_enabled: bool
) -> str | None:
    """Return why the native UI cannot be served, or ``None`` when it can.

    Only the explicit enable controls gate serving. The server image or build
    identity is not a compatibility input: compatibility is the scoped contract
    itself. Every native request is classified against the versioned route map
    (``native_ui_compat``) and an unsupported operation fails closed there, and
    the injected adapter validates the bound session and transcript reads
    before announcing readiness, so an incompatible build falls back to the
    read-only surface instead of being trusted. A compatible rebuild therefore
    serves without any manual version pin.
    """

    if not omnigent_enabled:
        return "omnigent_disabled"
    if not serving_enabled:
        return "native_ui_serving_disabled"
    return None


# --- Browser-safe bootstrap contract -----------------------------------------


def build_chat_bootstrap(
    *,
    chat_binding_id: str,
    mode: PresentationMode,
    read_only: bool,
    capabilities: Mapping[str, bool],
    state: str,
    compatibility_version: str = NATIVE_UI_ROUTE_FEATURE_VERSION,
    unavailable_reason: str | None = None,
    labels: Mapping[str, Any] | None = None,
    capability_schema_version: str | None = None,
    capability_authority_digest: str | None = None,
    disabled_reasons: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build the browser-safe native application bootstrap object (issue §2).

    The native application receives only browser-safe information: the opaque
    ``chatBindingId``, the scoped API base path, the presentation mode,
    read-only state, the filtered effective capability manifest with disabled
    reasons, safe display labels, and a stable compatibility version.

    It deliberately never carries a raw provider session id, upstream endpoint,
    host, runner, credential, profile, launch policy, or workspace authority —
    those stay server-side and are only reachable through the separately
    authorized scoped facade.
    """

    filtered_caps = {
        str(key): bool(value) for key, value in dict(capabilities or {}).items()
    }
    resolved_disabled_reasons = {
        capability: (
            "session_read_only" if read_only else "policy_or_capability_denied"
        )
        for capability, allowed in filtered_caps.items()
        if not allowed
    }
    for capability, reason in dict(disabled_reasons or {}).items():
        if capability in filtered_caps and not filtered_caps[capability] and reason:
            resolved_disabled_reasons[capability] = str(reason)
    bootstrap: dict[str, Any] = {
        "schemaVersion": NATIVE_UI_BOOTSTRAP_SCHEMA_VERSION,
        "chatBindingId": chat_binding_id,
        "uiBase": scoped_ui_base(chat_binding_id),
        "apiBase": scoped_api_base(chat_binding_id),
        "wsBase": scoped_api_base(chat_binding_id),
        "mode": mode,
        "embedded": mode == "embedded",
        "readOnly": bool(read_only),
        "state": state,
        "capabilities": filtered_caps,
        "disabledReasons": resolved_disabled_reasons,
        "compatibilityVersion": compatibility_version,
        "labels": {str(k): v for k, v in dict(labels or {}).items()},
    }
    if capability_schema_version:
        bootstrap["capabilitySchemaVersion"] = capability_schema_version
    if capability_authority_digest:
        bootstrap["capabilityAuthorityDigest"] = capability_authority_digest
    if unavailable_reason:
        bootstrap["unavailableReason"] = unavailable_reason
    return bootstrap


# --- Security header policy ---------------------------------------------------


def native_ui_security_headers(
    *, mode: PresentationMode, is_document: bool
) -> dict[str, str]:
    """Return the explicit CSP/frame/cache/referrer policy for a served response.

    Embedded mode is framed same-origin inside the MoonMind dashboard, so it
    permits ``frame-ancestors 'self'``; the full-page **Open in Omnigent** mode
    is a top-level navigation and refuses framing entirely
    (``frame-ancestors 'none'`` / ``X-Frame-Options: DENY``).

    A served *document* carries the binding-scoped bootstrap (which embeds the
    caller's effective capabilities and state), so it is never cached
    (``Cache-Control: no-store`` + ``Vary: Cookie``): one binding's bootstrap or
    private session data can never leak to another caller through a shared cache.
    Hashed static assets carry no binding data and are content-addressed, so they
    are privately cacheable behind the per-request authorization boundary.
    """

    if mode == "embedded":
        frame_ancestors = "'self'"
        x_frame_options = "SAMEORIGIN"
    else:
        frame_ancestors = "'none'"
        x_frame_options = "DENY"

    headers: dict[str, str] = {
        # Restrict framing and neutralize base-tag/redirect escapes to unscoped
        # upstream routes; ``connect-src 'self'`` keeps API/SSE traffic on the
        # MoonMind origin (the scoped facade), never the upstream server, so
        # provider JavaScript cannot open a fetch/XHR/EventSource/WebSocket
        # connection to an absolute upstream or external URL.
        "Content-Security-Policy": (
            f"frame-ancestors {frame_ancestors}; base-uri 'self'; "
            "form-action 'self'; connect-src 'self'; worker-src 'none'"
        ),
        "X-Frame-Options": x_frame_options,
        "X-Content-Type-Options": "nosniff",
        # Never leak the scoped route (which contains the opaque binding id) to a
        # cross-origin destination through the Referer header.
        "Referrer-Policy": "same-origin",
        # The native surface is deliberately same-origin. This makes the
        # CORS/resource boundary explicit instead of inheriting permissive
        # upstream policy; CSP worker-src above disables service workers.
        "Cross-Origin-Resource-Policy": "same-origin",
    }
    if is_document:
        headers["Cache-Control"] = "no-store, private"
        headers["Vary"] = "Cookie"
    else:
        headers["Cache-Control"] = "private, max-age=300"
    return headers


# --- SPA document vs hashed asset classification -----------------------------

_DOCUMENT_EXTENSIONS: frozenset[str] = frozenset({".html", ".htm"})


def is_document_request(ui_path: str | None) -> bool:
    """Return whether a scoped request should be served the SPA document.

    The root route, any extension-less deep link, and explicit ``*.html`` all
    resolve to the SPA document so deep links and refreshes work
    (issue #3638 requirement 5). A path whose final segment has any other file
    extension is a hashed static asset to reverse-proxy from upstream.
    """

    path = str(ui_path or "").strip().strip("/")
    if not path:
        return True
    last_segment = path.rsplit("/", 1)[-1]
    if "." not in last_segment:
        return True
    extension = "." + last_segment.rsplit(".", 1)[-1].lower()
    return extension in _DOCUMENT_EXTENSIONS


def upstream_path_for(ui_path: str | None) -> str:
    """Map a scoped UI sub-path to the upstream Omnigent server path to fetch.

    SPA documents (root, deep links, refreshes) always fetch the upstream index
    document at ``/`` so the SPA shell is returned for client-side routing;
    hashed assets fetch their exact upstream path. Traversal segments are
    rejected by returning the safe index path so a caller can never escape the
    upstream asset root through the scoped route.
    """

    if is_document_request(ui_path):
        return "/"
    path = str(ui_path or "").strip().strip("/")
    if not path or ".." in path.split("/"):
        return "/"
    return "/" + path


# --- Bootstrap injection + scoped asset URL rewriting ------------------------

_ROOT_ABSOLUTE_ATTR = re.compile(r"""\b(src|href)=(["'])/(?!/)""")

# Bound the `<head>` scan so a crafted upstream document cannot force
# unbounded work. The opening `<head>` tag is always near the start of a real
# SPA shell; searching only this prefix keeps injection O(1) in document size.
_MAX_HEAD_SEARCH_BYTES = 256 * 1024


def _ascii_lower(char: str) -> str:
    """Length-preserving ASCII lowercase for offset-sensitive scans."""

    code = ord(char)
    if ord("A") <= code <= ord("Z"):
        return chr(code + 32)
    return char


def _head_insert_position(document: str) -> int | None:
    """Return the insert offset just after the opening `<head ...>` tag.

    ASCII case-insensitive scan equivalent to ``<head[^>]*>``. ``str.lower()``
    is not used because some characters (for example ``İ``) expand when
    lowercased, shifting every later offset so the injection can land inside a
    later element instead of right after the head tag. Comparing ASCII letters
    with a length-preserving fold keeps match offsets identical to the input.
    Returns ``None`` when no head tag closes inside the bounded search window,
    in which case the caller prepends the injection.
    """

    window = document[:_MAX_HEAD_SEARCH_BYTES]
    start = 0
    while True:
        idx = window.find("<", start)
        if idx == -1:
            return None
        candidate = window[idx + 1 : idx + 5]
        if len(candidate) == 4 and all(
            _ascii_lower(char) == expected
            for char, expected in zip(candidate, "head")
        ):
            end = window.find(">", idx + 5)
            if end == -1:
                return None
            return end + 1
        start = idx + 1


# Inline CSS url() references (`url(/assets/wordmark.svg)`) ignore <base href>
# the same way src/href do; the wordmark 404 in #4013 is this class of miss.
# Only same-document single-quoted/unquoted root-absolute paths are rewritten;
# absolute (https://) and protocol-relative (//host) URLs are untouched.
_ROOT_ABSOLUTE_CSS_URL = re.compile(r"""url\(\s*(['"]?)/(?!/)""")


def _scope_srcset_candidates(candidates: str, base: str) -> str:
    """Walk srcset URL/descriptor tokens without splitting data-URL commas."""

    output: list[str] = []
    position = 0
    whitespace = " \t\n\r\f"
    while position < len(candidates):
        start = position
        while position < len(candidates) and candidates[position] in whitespace + ",":
            position += 1
        output.append(candidates[start:position])
        start = position
        # Per the srcset tokenizer, a URL ends at whitespace; commas inside a
        # URL (especially data:image/jpeg;base64,/9j/...) belong to that URL.
        while position < len(candidates) and candidates[position] not in whitespace:
            position += 1
        url = candidates[start:position]
        output.append(
            base + url if url.startswith("/") and not url.startswith("//") else url
        )
        if url.endswith(","):
            continue
        start = position
        parentheses = 0
        while position < len(candidates):
            char = candidates[position]
            position += 1
            if char == "(":
                parentheses += 1
            elif char == ")":
                parentheses = max(0, parentheses - 1)
            elif char == "," and parentheses == 0:
                break
        output.append(candidates[start:position])
    return "".join(output)


def rewrite_asset_urls(document: str, *, scoped_base: str) -> str:
    """Rewrite root-absolute asset URLs onto the binding-scoped route.

    A stock SPA build references hashed assets with root-absolute URLs such as
    ``src="/assets/index-abc.js"``. Those ignore ``<base href>``, so they are
    rewritten to ``src="/omnigent-ui/workflow-chat/{id}/assets/index-abc.js"``
    and resolve back through the scoped serving route. Protocol-relative
    (``//host``) and absolute (``https://``) URLs are left untouched so a
    deliberate external resource is not silently rerouted.

    ``srcset`` candidates and inline CSS ``url(/...)`` references are scoped
    the same way: the missing root wordmark (``/assets/omnigent-wordmark-*.svg``,
    MoonLadderStudios/MoonMind#4013) is a decorative nonfatal asset, but it must
    still resolve through the scoped route rather than 404 at the origin root.
    """

    base = html.escape(scoped_base.rstrip("/"), quote=True)

    def _scope_srcset(match: re.Match[str]) -> str:
        tag = match.group(0)
        # Only rewrite the srcset attribute value, leaving src/href (handled
        # above) untouched within the same tag.
        def _rewrite_value(value: re.Match[str]) -> str:
            quote, candidates = value.group(1), value.group(2)
            scoped = _scope_srcset_candidates(candidates, base)
            return f"srcset={quote}{scoped}{quote}"

        return re.sub(
            r"""(?i)\bsrcset=(["'])(.*?)\1""",
            _rewrite_value,
            tag,
            flags=re.DOTALL,
        )

    scoped = _ROOT_ABSOLUTE_ATTR.sub(rf"\1=\g<2>{base}/", document)
    scoped = re.sub(
        r"""<[^<>]*>""",
        _scope_srcset,
        scoped,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return _ROOT_ABSOLUTE_CSS_URL.sub(rf"url(\1{base}/", scoped)


def render_native_ui_document(
    upstream_html: str,
    *,
    bootstrap: Mapping[str, Any],
    scoped_base: str,
) -> str:
    """Return the served SPA document: scoped assets + injected bootstrap.

    Injects ``<base href>`` and the binding-scoped bootstrap object as the first
    children of ``<head>`` (so the native application reads its scoped API/WS
    base, presentation mode, and capabilities before any of its own scripts run)
    and rewrites root-absolute asset URLs onto the scoped route. Bootstrap JSON
    lives in an HTML-escaped data attribute and is parsed before the app runs.
    """

    base = scoped_base.rstrip("/")
    payload = html.escape(
        json.dumps(dict(bootstrap), separators=(",", ":")), quote=True
    )
    # The stock standalone app owns a root BrowserRouter and root-absolute
    # transport paths. Install the thin host adapter before its module executes:
    # route the app to the one virtualized binding id and rebase API/WS traffic
    # onto the binding-scoped facade. This is execution substrate only; the
    # provider-maintained UI still owns rendering and interaction semantics.
    adapter = r"""
(function () {
  "use strict";
  const binding = window.__MOONMIND_OMNIGENT_CHAT__;
  if (!binding || !binding.chatBindingId || !binding.apiBase) return;

  const apiBase = String(binding.apiBase).replace(/\/+$/, "");
  const bindingId = encodeURIComponent(String(binding.chatBindingId));
  const scopedDocumentUrl = window.location.href;
  const uiBase = String(binding.uiBase || new URL(scopedDocumentUrl).pathname).replace(/\/+$/, "");
  // Use the native application's supported basename. Keep the address valid
  // throughout boot, suspense, reload and retry instead of racing its router
  // with a temporary origin-root URL and a first-paint restoration timer.
  window.__OMNIGENT_BASE_PATH__ = uiBase;
  window.history.replaceState(window.history.state, "",
    uiBase + "/c/" + bindingId + window.location.search + window.location.hash);
  const sameOriginApiPath = /^\/(?:v1|api|health)(?:\/|$)/;
  let sessionValidated = false;
  let transcriptValidated = false;
  let fatalAnnounced = false;
  let readinessObserver = null;
  let lastReadFailure = "readiness_timeout";
  const pendingSubmissions = new Map();
  const submissionStorage = "moonmind.chat.pending.v1:" + bindingId;
  function submissionDigest(text) {
    // WebCrypto is unavailable on ordinary HTTP LAN origins. SHA-256 here
    // identifies retry bytes without persisting their plaintext on those URLs.
    const bytes = new TextEncoder().encode(text);
    const padded = new Uint8Array(Math.ceil((bytes.length + 9) / 64) * 64);
    padded.set(bytes); padded[bytes.length] = 128;
    const data = new DataView(padded.buffer);
    data.setUint32(padded.length - 8, Math.floor(bytes.length / 536870912));
    data.setUint32(padded.length - 4, bytes.length * 8);
    const h = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
    const k = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
      0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
      0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
      0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
      0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
      0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
      0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
      0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
    const rotate = (x, n) => (x >>> n) | (x << (32 - n));
    const w = new Uint32Array(64);
    for (let offset = 0; offset < padded.length; offset += 64) {
      for (let i = 0; i < 16; i++) w[i] = data.getUint32(offset + i * 4);
      for (let i = 16; i < 64; i++) {
        const x = w[i - 15], y = w[i - 2];
        w[i] = w[i - 16] + (rotate(x,7)^rotate(x,18)^(x>>>3)) + w[i - 7] + (rotate(y,17)^rotate(y,19)^(y>>>10));
      }
      let [a,b,c,d,e,f,g,j] = h;
      for (let i = 0; i < 64; i++) {
        const t = (j + (rotate(e,6)^rotate(e,11)^rotate(e,25)) + ((e&f)^(~e&g)) + k[i] + w[i]) >>> 0;
        const u = ((rotate(a,2)^rotate(a,13)^rotate(a,22)) + ((a&b)^(a&c)^(b&c))) >>> 0;
        j=g; g=f; f=e; e=(d+t)>>>0; d=c; c=b; b=a; a=(t+u)>>>0;
      }
      [a,b,c,d,e,f,g,j].forEach((value, i) => { h[i] = (h[i] + value) >>> 0; });
    }
    return h.map(value => value.toString(16).padStart(8, "0")).join("");
  }
  // Only digests and opaque retry identities are persisted, never message text.
  try {
    const saved = JSON.parse(window.sessionStorage.getItem(submissionStorage) || "[]");
    if (Array.isArray(saved)) for (const entry of saved.slice(0, 32)) {
      if (Array.isArray(entry) && /^[a-f0-9]{64}$/.test(entry[0]) && /^mm-[a-f0-9-]{32,36}$/.test(entry[1]))
        pendingSubmissions.set(entry[0], entry[1]);
    }
  } catch (error) {}
  function saveSubmissions() {
    try { window.sessionStorage.setItem(submissionStorage,
      JSON.stringify(Array.from(pendingSubmissions).filter(([digest]) => /^[a-f0-9]{64}$/.test(digest)))); }
    catch (error) {}
  }

  function checkConversationRendered() {
    // The pinned ChatPage renders Conversation (role=log) only beyond its
    // loadingConversation/conversationLoadError gates. Require the successful
    // bound reads as well: neither a shell nor an error rendered into #root
    // is evidence of a readable transcript. This also covers empty/read-only
    // conversations without depending on composer authority or message text.
    const root = document.getElementById("root");
    if (!fatalAnnounced && sessionValidated && transcriptValidated &&
        root && root.querySelector('[role="log"]')) {
      announceReady();
      if (readinessObserver) readinessObserver.disconnect();
    }
  }

  function scopedHttpUrl(input) {
    const url = new URL(String(input), window.location.origin);
    const sameHttpOrigin = url.origin === window.location.origin;
    const sameSocketHost =
      (url.protocol === "ws:" || url.protocol === "wss:") &&
      url.host === window.location.host;
    const path = url.pathname.startsWith(uiBase + "/")
      ? url.pathname.slice(uiBase.length) : url.pathname;
    if (
      (!sameHttpOrigin && !sameSocketHost) ||
      url.pathname === apiBase ||
      url.pathname.startsWith(apiBase + "/") ||
      !sameOriginApiPath.test(path)
    ) {
      return null;
    }
    url.pathname = apiBase + path;
    return url;
  }

  const nativeFetch = window.fetch.bind(window);
  window.fetch = async function (input, init) {
    const source = input instanceof Request ? input.url : input;
    const scoped = scopedHttpUrl(source);
    const url = scoped || new URL(String(source), window.location.origin);
    const sessionPath = apiBase + "/v1/sessions/" + bindingId;
    const method = String(init?.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
    // The hosted surface cannot browse other conversations. Do not mount a
    // second server-side catalog query for the native global sidebar. Bound
    // metadata and transcript reads still cross the authorized facade.
    if (method === "GET" && url.origin === window.location.origin &&
        url.pathname === apiBase + "/v1/sessions") {
      return new Response(JSON.stringify({ object: "list", data: [], has_more: false }),
        { headers: { "Content-Type": "application/json" } });
    }
    const essential = method === "GET" && url.origin === window.location.origin &&
      (url.pathname === sessionPath ||
       (url.pathname === sessionPath + "/items" && !url.searchParams.has("after")));
    let submission = null;
    let newSubmission = false;
    if (method === "POST" && url.pathname === sessionPath + "/events") {
      const headers = new Headers(init?.headers || (input instanceof Request ? input.headers : undefined));
      if (!headers.has("Idempotency-Key")) {
        const body = init?.body ?? (input instanceof Request ? await input.clone().text() : null);
        if (typeof body === "string") {
          // An unacknowledged, byte-identical send is a retry. Once its receipt
          // is confirmed, the same text is a new submission with a fresh key.
          let digest;
          if (window.crypto?.subtle) {
            const bytes = await window.crypto.subtle.digest("SHA-256", new TextEncoder().encode(body));
            digest = Array.from(new Uint8Array(bytes), value => value.toString(16).padStart(2, "0")).join("");
          } else digest = submissionDigest(body);
          let key = pendingSubmissions.get(digest);
          if (!key) {
            newSubmission = true;
            if (pendingSubmissions.size >= 32) throw new Error("Too many unconfirmed chat submissions. Retry an earlier send.");
            const nonce = window.crypto.randomUUID?.() || Array.from(
              window.crypto.getRandomValues(new Uint8Array(16)),
              value => value.toString(16).padStart(2, "0")
            ).join("");
            key = "mm-" + nonce;
            pendingSubmissions.set(digest, key);
            saveSubmissions();
          }
          headers.set("Idempotency-Key", key);
          init = { ...init, headers };
          submission = digest;
        }
      }
    }
    const request = scoped
      ? (input instanceof Request ? new Request(scoped.href, input) : scoped.href)
      : input;
    try {
      let response;
      const signal = init?.signal || (input instanceof Request ? input.signal : null);
      for (let attempt = 0; attempt < 3; attempt++) {
        try { response = await nativeFetch(request, init); }
        catch (error) {
          if (!essential || signal?.aborted || attempt === 2) throw error;
        }
        const transient = !response || [408, 429].includes(response.status) || response.status >= 500;
        if (!essential || !transient || attempt === 2) break;
        if (response?.body) await response.body.cancel();
        await new Promise(function (resolve, reject) {
          if (signal?.aborted) { reject(new DOMException("Read aborted", "AbortError")); return; }
          function abort() { window.clearTimeout(timer); reject(new DOMException("Read aborted", "AbortError")); }
          const timer = window.setTimeout(function () {
            signal?.removeEventListener("abort", abort); resolve();
          }, 500 * (attempt + 1));
          signal?.addEventListener("abort", abort, { once: true });
        });
        response = null;
      }
      if (essential) {
        if (!response.ok) {
          lastReadFailure = "essential_read_failed";
          announceFatal([401, 403].includes(response.status)
            ? "transcript_access_denied" : "essential_read_failed");
        } else {
          let body;
          try { body = await response.clone().json(); }
          catch (error) { announceFatal("essential_read_invalid"); return response; }
          if (url.pathname === sessionPath) {
            sessionValidated = body?.id === binding.chatBindingId;
            if (!sessionValidated) announceFatal("essential_read_invalid");
          } else {
            transcriptValidated = Array.isArray(body?.data) && typeof body.has_more === "boolean";
            if (!transcriptValidated) announceFatal("essential_read_invalid");
          }
          checkConversationRendered();
        }
      }
      if (submission !== null && (response.ok ||
          (newSubmission && [400, 401, 403, 404, 413, 415, 422].includes(response.status)))) {
        try {
          // Headers alone are not an acknowledgement: a dropped response body
          // must retain the identity across explicit retry or frame reload.
          // A definitive first-attempt rejection also settles that request;
          // a rejection after uncertain delivery cannot settle the earlier send.
          await response.clone().json();
          pendingSubmissions.delete(submission);
          saveSubmissions();
        } catch (error) {}
      }
      return response;
    } catch (error) {
      if (essential && !(init?.signal || (input instanceof Request ? input.signal : null))?.aborted) {
        lastReadFailure = "essential_read_failed";
        announceFatal(lastReadFailure);
      }
      throw error;
    }
  };

  const nativeXhrOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url) {
    const scoped = scopedHttpUrl(url);
    const args = Array.from(arguments);
    if (scoped) args[1] = scoped.href;
    return nativeXhrOpen.apply(this, args);
  };

  if (window.EventSource) {
    const NativeEventSource = window.EventSource;
    window.EventSource = function (url, config) {
      const scoped = scopedHttpUrl(url);
      return new NativeEventSource(scoped ? scoped.href : url, config);
    };
    window.EventSource.prototype = NativeEventSource.prototype;
    // Preserve constructor statics (CONNECTING/OPEN/CLOSED). Without this the
    // replacement's constants are undefined (MoonLadderStudios/MoonMind#4013).
    Object.setPrototypeOf(window.EventSource, NativeEventSource);
  }

  if (window.WebSocket) {
    const NativeWebSocket = window.WebSocket;
    window.WebSocket = function (url, protocols) {
      const scoped = scopedHttpUrl(url);
      if (scoped) scoped.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      return protocols === undefined
        ? new NativeWebSocket(scoped ? scoped.href : url)
        : new NativeWebSocket(scoped ? scoped.href : url, protocols);
    };
    window.WebSocket.prototype = NativeWebSocket.prototype;
    // Preserve constructor statics (CONNECTING/OPEN/CLOSING/CLOSED). The
    // pinned upstream SessionUpdatesSocket.sendWatch guards with
    // `ws?.readyState === WebSocket.OPEN`; with OPEN undefined the guard
    // passes for null sockets (null.send crash) and fails for open sockets
    // (watch never sent). (MoonLadderStudios/MoonMind#4013).
    Object.setPrototypeOf(window.WebSocket, NativeWebSocket);
  }

  function observeConversation() {
    const root = document.getElementById("root");
    if (!root) return;
    readinessObserver = new MutationObserver(checkConversationRendered);
    readinessObserver.observe(root, { childList: true, subtree: true });
    checkConversationRendered();
    window.addEventListener("pagehide", function () {
      readinessObserver.disconnect();
    }, { once: true });
  }
  // Bounded readiness/fatal signaling for the embedding shell
  // (MoonLadderStudios/MoonMind#4013 AC7/PLAN5). The shell validates exact
  // origin, iframe window, binding id, and mount generation before trusting a
  // signal, so a replaced frame can never mark the current mount. Payloads
  // carry only presentation state and safe reason codes — never provider
  // identity, transcript content, or credentials.
  let readyAnnounced = false;
  function announceReady() {
    if (readyAnnounced) return;
    readyAnnounced = true;
    try {
      window.parent.postMessage(
        { type: "moonmind.omnigent.chat.ready", chatBindingId: binding.chatBindingId },
        window.location.origin
      );
    } catch (err) {}
  }
  function announceFatal(reason) {
    fatalAnnounced = true;
    if (readinessObserver) readinessObserver.disconnect();
    const code = String(reason || "native_crash").slice(0, 128);
    try {
      window.parent.postMessage(
        {
          type: "moonmind.omnigent.chat.fatal",
          chatBindingId: binding.chatBindingId,
          reason: code,
        },
        window.location.origin
      );
    } catch (err) {}
    showFullPageUnavailable(code);
  }
  // Full-page presentation has no embedding shell to receive these signals,
  // so it shows its own unavailable surface and bounds startup with the same
  // deadline the embedded shell uses (docs/UI/WorkflowChatPanel.md §4.1).
  const fullPage = window.parent === window;
  let unavailableShown = false;
  function showFullPageUnavailable(code) {
    if (!fullPage || unavailableShown) return;
    function render() {
      if (unavailableShown || !document.body) return;
      unavailableShown = true;
      const panel = document.createElement("main");
      panel.setAttribute("role", "alert");
      const heading = document.createElement("h1");
      heading.textContent = "Chat unavailable";
      const detail = document.createElement("p");
      detail.textContent =
        "Native chat could not load (" + code + "). The workflow's recorded " +
        "transcript and results remain available in MoonMind.";
      const retry = document.createElement("button");
      retry.setAttribute("type", "button");
      retry.textContent = "Retry";
      retry.addEventListener("click", function () {
        window.location.reload();
      });
      panel.append(heading, detail, retry);
      document.body.replaceChildren(panel);
    }
    if (document.body) {
      render();
    } else {
      document.addEventListener("DOMContentLoaded", render, { once: true });
    }
  }
  if (fullPage) {
    window.setTimeout(function () {
      if (!readyAnnounced && !fatalAnnounced) announceFatal(lastReadFailure);
    }, 60000);
  }
  window.addEventListener("error", function () {
    announceFatal("native_crash");
  });
  window.addEventListener("unhandledrejection", function () {
    announceFatal("unhandled_rejection");
  });
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", observeConversation, { once: true });
  } else {
    observeConversation();
  }
})();
""".strip()
    # Both dynamic values are attributes; no caller value becomes script source.
    escaped_base = html.escape(base, quote=True)
    injected = (
        f'<base href="{escaped_base}/">'
        f'<meta id="moonmind-omnigent-chat-bootstrap" data-bootstrap="{payload}">'
        "<script>window.__MOONMIND_OMNIGENT_CHAT__=JSON.parse("
        'document.getElementById("moonmind-omnigent-chat-bootstrap").getAttribute("data-bootstrap"));'
        f"\n{adapter}</script>"
    )
    rewritten = rewrite_asset_urls(upstream_html, scoped_base=base)
    insert_at = _head_insert_position(rewritten)
    if insert_at is None:
        # No <head>: prepend the injection so the bootstrap still runs first.
        return injected + rewritten
    return rewritten[:insert_at] + injected + rewritten[insert_at:]


__all__ = [
    "CODE_NATIVE_CHAT_UNAVAILABLE",
    "NATIVE_UI_BOOTSTRAP_SCHEMA_VERSION",
    "NATIVE_UI_MOUNT_PREFIX",
    "NATIVE_UI_ROUTE_FEATURE_VERSION",
    "PresentationMode",
    "build_chat_bootstrap",
    "is_document_request",
    "is_valid_chat_binding_id",
    "native_ui_security_headers",
    "native_ui_unavailable_reason",
    "presentation_mode_from_query",
    "render_native_ui_document",
    "rewrite_asset_urls",
    "scoped_api_base",
    "scoped_ui_base",
    "upstream_path_for",
]
