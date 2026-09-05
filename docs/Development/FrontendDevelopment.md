# Frontend Development

This document describes the standard developer workflow for working with the MoonMind frontend.

## Prerequisite

Install frontend dependencies once:

```bash
npm install
```

## Demo and review workflow

Use the production-like workflow when you need another person to review a
frontend change, when you are demoing over the network, or when visual
correctness matters more than Hot Module Replacement (HMR).

Build a fresh frontend bundle and let FastAPI serve the built assets:

```bash
npm run ui:build
docker compose up -d api
```

Then open the dashboard through the normal FastAPI route:

```text
http://localhost:7000/workflows
```

For network demos, replace `localhost` with the host name or IP address that the
reviewer can reach, for example:

```text
http://asus-laptop:8000/workflows
http://192.168.0.20:8000/workflows
```

In this mode, FastAPI serves the built `dist/` bundle through the Vite manifest.
This is the preferred review path because the browser receives one coherent
asset set from the same server that owns the dashboard HTML shell and API
routes.

Do not rebuild `api_service/static/workflow_console/dist/` while a running
FastAPI process is actively serving it. During active frontend development, use
`MOONMIND_UI_DEV_SERVER_URL` instead. After rebuilding the served static bundle,
restart FastAPI so browsers receive a coherent manifest and asset set.

Do not open the Vite dev server root (`http://localhost:5173/`) for demos or
reviews. MoonMind's Vite server serves frontend modules for development; it does
not own the dashboard HTML routes and may return `404` at `/`.

## Live development with Hot Module Replacement (HMR)

Assume the normal MoonMind development stack is already running, for example:

```bash
docker compose up -d
```

HMR uses the **normal MoonMind API**. It does **not** use a secondary API.

### Standard local workflow

Start the Vite dev server:

```bash
npm run ui:dev
```

Start or restart FastAPI with the Vite dev-server URL set:

```bash
MOONMIND_UI_DEV_SERVER_URL=http://127.0.0.1:5173 <your-fastapi-start-command>
```

When `MOONMIND_UI_DEV_SERVER_URL` is set, FastAPI bypasses the built manifest and loads the frontend modules directly from the Vite dev server.

### Important notes

* `npm run ui:dev` by itself is **not enough**
* FastAPI must be started with `MOONMIND_UI_DEV_SERVER_URL` set
* if FastAPI was already running without that env var, restart it in this mode
* frontend changes should then update through Vite HMR without restarting FastAPI again

### If FastAPI is running in Docker

If FastAPI is running inside Docker instead of on the host, `127.0.0.1` usually will not work for `MOONMIND_UI_DEV_SERVER_URL` because that points to the container itself.

Use a host-reachable address instead, for example:

```bash
MOONMIND_UI_DEV_SERVER_URL=http://host.docker.internal:5173 <your-fastapi-start-command>
```

## Frontend verification

Run the standard frontend checks:

```bash
npm run ui:test
npm run ui:typecheck
npm run ui:lint
npm run ui:build:check
```

These commands cover:

* `ui:test` — Vitest unit tests
* `ui:typecheck` — TypeScript type checking
* `ui:lint` — ESLint
* `ui:build:check` — clean rebuild plus manifest validation

Run real-browser regressions locally with `npm run ui:test:browser`; it defaults to Chromium and Firefox. To reproduce one CI matrix leg, set `MOONMIND_BROWSER_ENGINES=chromium` or `MOONMIND_BROWSER_ENGINES=firefox`. CI supplies the matching browsers from its pinned Playwright container.

### Native Workflow Chat browser verification

The product requirements are owned by [Workflow Chat Panel](../UI/WorkflowChatPanel.md#41-application-readiness-and-failure-containment). The [Omnigent Bridge](../Omnigent/OmnigentBridge.md#browser-transport-api-preservation) owns browser transport and scoped network compatibility. A binding response or iframe load is not proof that the conversation rendered.

`npm run ui:test:browser` collects only `frontend/src/browser/**/*.browser.test.{ts,tsx}` through Vitest/Vite. Neither that command nor the `frontend-browser` CI job starts FastAPI or an Omnigent server. It covers isolated browser regressions; passing it does not exercise the served native document, compiled Omnigent application, or authorized facade.

The existing served-stack acceptance entrypoint is the protected `workflow_chat` mode in [Provider / Omnigent Live Conformance](../../.github/workflows/omnigent-live-conformance.yml). On its provisioned provider-verification runner, the equivalent command is:

```bash
python3 tools/run_omnigent_live_conformance.py \
  --mode workflow_chat \
  --server-image "${OMNIGENT_CONFORMANCE_SERVER_IMAGE:?qualified server digest required}" \
  --host-image "${OMNIGENT_CONFORMANCE_HOST_IMAGE:?qualified host digest required}" \
  --opencode-host-image "${OMNIGENT_CONFORMANCE_OPENCODE_HOST_IMAGE:?qualified OpenCode host digest required}" \
  --source-commit "$(git rev-parse HEAD)" \
  --output-dir artifacts/omnigent-conformance/live/workflow_chat
```

This requires the protected workflow's dependencies, Chromium, authenticated browser state, dashboard URL, provider configuration, enrolled profile, and provisioned live-action adapter. Each image input is a full immutable digest reference. [Conformance and Live Smoke](../Omnigent/ConformanceAndLiveSmoke.md) owns the complete environment and acceptance requirements. This protected path uses Chromium; the Vitest Chromium/Firefox matrix does not establish Firefox coverage of the served stack.

When changing the native bootstrap, facade, or workflow shell, put the following scenarios in a harness that serves the real FastAPI routes, emitted adapter, and pinned compiled native bundle. Required CI regression fixtures use controlled local dependencies; protected live acceptance uses the provisioned stack above. Extend the controlling harness and its collection rules for any missing scenario. Neither an existing acceptance report nor HTML-string assertions qualify a scenario that was not executed.

Execute the script emitted by `render_native_ui_document` before the pinned native consumer runs. Verify `WebSocket.CONNECTING`, `OPEN`, `CLOSING`, and `CLOSED`, native construction and instance behavior, scoped URLs, subprotocol arguments, and null/connecting/open/closing/closed watch behavior. Verify an open socket actually sends, not merely that a null socket stops throwing. Cover equivalent constructor constants and options for an adapted `EventSource`, plus reconnect, stop/start, and disposal behavior.

Exercise the compiled native application through the normal FastAPI-served Workflow Detail route and authorized facade, in embedded and full-page presentations. Verify the bound transcript or a validated empty state, essential boot reads, dynamic assets, authorized send and streaming, reconnect, terminal read-only access, and historical evidence after host cleanup. Capture console exceptions and network outcomes. Deliberately denied optional controls have different expectations from failed essential reads.

Inject required-asset failure, essential-read denial, a boot exception, a fatal error after readiness, and a startup that never becomes ready. Assert a visible bounded diagnostic with safe recovery. Test stale or wrong-origin frame signals, binding replacement, repeated retries, and listener/timer cleanup. Retain wrong-owner and revoked-access cases so a fallback cannot bypass authorization or display another user's cached transcript.

Bind each result to the actual API/dashboard build, Omnigent source and UI bundle, compatibility profile, test scenario, and relevant image digests. Confirm that the applicable required CI selector collects the escaped-regression tests. Record exact commands and passed, failed, skipped, blocked, or unexecuted scenarios. A successful test command cannot qualify scenarios it did not collect. Hermetic browser tests with controlled dependencies are separate from protected live-provider/deployed-artifact acceptance.

### Troubleshooting a blank or unavailable native chat

Start with the normal Workflow Detail route and record the actual served build and bundle identities. Use `tools/verify_deployed_ui_assets.py` when dashboard asset coherence is in question. The native Omnigent bundle is a separate artifact and also needs verification. Do not hot-patch generated assets or change a version setting merely to bypass compatibility checks.

| Observation | Evidence to inspect | Safe response |
|---|---|---|
| No binding or denied binding | Binding response status and redacted error envelope | Preserve non-enumerating access behavior. Do not guess a provider session. |
| Document loads but the application crashes or stays blank | First application exception, readiness signal/deadline, and failed required asset or read | Reproduce the actual served adapter and consumer. Do not treat iframe `load` as success. |
| Snapshot, transcript items, or stream returns 403 | Authorized facade error code and current capability disabled reason | Distinguish legitimate denial from missing/stale authority at its producer. Never default missing authority to allowed. |
| Native metadata/resource route returns 404 | Exact compiled UI request and reviewed route/method/response contract | Add only the authorized scoped operation when required, or make a genuinely optional unsupported feature nonfatal. No catch-all proxy. |
| Image, stylesheet, or dynamic chunk uses an unscoped root URL | Browser request initiator, compiled asset reference, scoped asset base, and response content type | Verify build/runtime asset-base behavior. Rewriting HTML attributes alone does not prove JavaScript/CSS asset references are scoped. |
| Dashboard workflow-update stream returns 404 | `/api/ui/info`, actual API route registration, and dashboard polling/connection lifecycle | Investigate independently of native chat. Preserve authorized polling and bound reconnect behavior. |

MoonMind's `/api/workflows/updates/stream` dashboard SSE endpoint and Omnigent's binding-scoped `/v1/sessions/updates` WebSocket are different transports with different owners. Repairing one does not establish that the other works. Separate browser-extension `contentscript.js` warnings from application stack traces before attributing a cause.

Keep captured diagnostics minimal and secret-safe. Omit cookies, authorization headers, private message bodies, real binding identifiers, provider/host identities, and signed URLs from issue excerpts. Preserve detailed evidence only through authorized artifact handling. Do not publish an unsanitized HAR or infer a particular capability failure from a status code alone. Incident-specific evidence and implementation checklists belong in issues, not this guide.

## Generated API types

Refresh the generated frontend API types with:

```bash
npm run generate
```

This updates:

```text
frontend/src/generated/openapi.ts
```

## Source of truth and generated output

Frontend source files live under:

```text
frontend/
```

Built frontend output is emitted under:

```text
api_service/static/task_dashboard/dist/
```

Do not edit files in `dist/` directly. Treat `dist/` as generated output, not hand-edited source.
