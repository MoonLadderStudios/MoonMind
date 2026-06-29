# Dashboard SPA Architecture

Status: **Target Architecture**
Owners: MoonMind Engineering
Last Updated: 2026-06-29
Related: `docs/UI/WorkflowConsoleArchitecture.md`, `docs/UI/TypeScriptSystem.md`, `docs/UI/DashboardDesignSystem.md`, `docs/UI/WorkflowWorkspaceSidebar.md`, `docs/ManagedAgents/LiveLogs.md`, `docs/Temporal/VisibilityAndUiQueryModel.md`

**Implementation tracking:** rollout checklists, Jira work breakdowns, and tactical migration notes belong in Jira, `docs/tmp/`, or gitignored handoffs. This document defines the durable target architecture.

---

## 1. Purpose

Define the target full single-page application (SPA) architecture for the MoonMind dashboard.

MoonMind currently has a transitional React/Vite dashboard: FastAPI serves route-specific HTML shells, injects a page boot payload, and the client has begun intercepting dashboard-internal navigation to preserve the shared React shell. That bridge is useful, but it is not the final architecture.

The long-term target is a **persistent client-routed dashboard application** that keeps the existing FastAPI deployment and same-origin API model while moving dashboard route selection, navigation chrome, shared state, route prefetching, and live update lifecycle into the TypeScript frontend.

Where this document conflicts with older server-rendered frontend guidance in `docs/UI/TypeScriptSystem.md`, this document describes the newer target state. The historical TypeScript-system document remains useful for build tooling, typing, and migration context, but the dashboard is now expected to move beyond page-island routing toward a full SPA shell.

---

## 2. Decision summary

MoonMind should converge on this UI architecture:

- **FastAPI remains the same-origin backend, API owner, auth owner, and static asset host.**
- **The dashboard UI becomes one persistent Vite/React SPA shell.**
- **The client is the routing authority for dashboard UI paths.**
- **FastAPI serves the same SPA shell for recognized dashboard routes and deep links.**
- **API, auth, health, webhook, artifact, presigned, static, and OpenAPI routes never fall back to SPA HTML.**
- **Runtime capabilities and feature gates come from a small UI info endpoint, not from route-specific boot payloads.**
- **Route pages use compact typed API contracts for list and picker surfaces, and detail/paged contracts for evidence-heavy surfaces.**
- **Live updates are owned by the persistent shell through SSE or WebSocket infrastructure with compact polling as a fallback.**
- **MoonMind and Omnigent should share shell, API-client, stream, workspace, and design primitives over time.**

This is a move to a **same-origin SPA**, not a move to a separately deployed frontend service.

---

## 3. Non-goals

The full SPA target does **not** require:

1. A separately deployed frontend service.
2. Next.js, Remix, Node SSR, or a full-stack frontend framework.
3. Browser clients calling Temporal, GitHub, Jira, object storage, model providers, or runtime hosts directly.
4. Removing FastAPI-owned authentication or API authorization.
5. Replacing canonical MoonMind URLs.
6. Rewriting every page visually in the same PR.
7. Collapsing MoonMind workflow concepts into Omnigent session concepts before a product-level unification decision.

---

## 4. Current transitional state

The current dashboard bridge may intercept dashboard-internal links and push browser history state without a document reload. That improves navigation, but the system is still transitional when any of the following remain true:

- the server injects `page` into `moonmind-ui-boot` and the client selects a page module from that value;
- navigation remains a Jinja partial rather than a React navigation component;
- route resolution is implemented with custom event dispatch and custom path matching instead of a normal client router;
- FastAPI has route-specific dashboard handlers for every page instead of a single dashboard shell fallback for UI routes;
- route-specific dashboard config is loaded as a synthetic replacement for server boot payloads;
- list pages still depend on polling instead of shell-managed update streams.

The bridge is acceptable as an intermediate state. It should not be considered the final SPA architecture.

---

## 5. Target runtime model

## 5.1 Server responsibilities

FastAPI owns:

1. Authentication and authorization.
2. REST and streaming API routes.
3. Health, metrics, OpenAPI, webhooks, callbacks, auth redirects, artifact grants, and presigned upload/download flows.
4. Static asset serving for the built Vite application.
5. Serving the SPA HTML shell for dashboard UI routes.
6. Returning JSON errors for API routes, never accidental HTML fallback.
7. Producing a compact UI info/capability payload for the SPA.

FastAPI does **not** own dashboard page selection after the SPA boots.

## 5.2 Client responsibilities

The TypeScript frontend owns:

1. Dashboard route matching and navigation.
2. Navigation chrome and active route state.
3. Route-level lazy loading and route-level error boundaries.
4. Query cache and mutation invalidation.
5. Persistent dashboard UI state.
6. Active workflow/session runtime state.
7. Live update stream lifecycle.
8. Route prefetching and intent-based data preloading.
9. Workspace layout composition.
10. Browser-only UI preferences.

---

## 6. Serving and fallback rules

## 6.1 Route registration order

FastAPI must register API and non-UI routes before the dashboard fallback. The SPA fallback should be the last UI route layer.

## 6.2 Paths that must never fall back to SPA HTML

The fallback must exclude at least:

- `/api/*`
- `/v1/*`
- `/ws/*`
- `/auth/*`
- `/health`
- `/healthz`
- `/metrics`
- `/openapi`
- `/openapi.json`
- `/docs` if enabled
- `/redoc` if enabled
- `/static/*`
- artifact download/upload/grant routes
- presigned upload/download URLs
- integration callback/webhook routes
- any URL with an explicit static file extension unless served by the static asset handler

Unknown API routes must return API-style 404/JSON responses, not the dashboard shell.

## 6.3 Dashboard UI paths

The SPA shell should be served for recognized dashboard UI paths and deep links, including:

- `/workflows`
- `/workflows/new`
- `/workflows/{workflowId}`
- `/workflows/{workflowId}/steps`
- `/workflows/{workflowId}/artifacts`
- `/workflows/{workflowId}/runs`
- `/workflows/{workflowId}/debug` when enabled
- `/schedules`
- `/schedules/{definitionId}`
- `/skills`
- `/skills/*` extensionless subroutes
- `/settings`
- `/settings/*` extensionless subroutes
- `/manifests`
- `/manifests/{manifestName}`
- `/oauth-terminal`
- `/index-health` if retained as a dashboard surface

The initial response for these routes should be the same shell. The client route table decides which page renders.

## 6.4 Cache policy

The SPA HTML shell should be revalidated, not cached immutably. Hashed Vite assets should be cacheable with immutable cache headers. Non-hashed static assets should use a short or revalidated cache policy.

---

## 7. Client routing model

MoonMind should use a standard client router, preferably React Router, instead of custom event-based route switching.

Representative route tree:

```tsx
const router = createBrowserRouter([
  {
    path: '/',
    element: <DashboardShell />,
    errorElement: <DashboardRouteErrorBoundary />,
    children: [
      { index: true, loader: redirectToWorkflows },
      { path: 'workflows', lazy: () => import('./pages/WorkflowsPage') },
      { path: 'workflows/new', lazy: () => import('./pages/WorkflowStartPage') },
      { path: 'workflows/:workflowId', lazy: () => import('./pages/WorkflowDetailPage') },
      { path: 'workflows/:workflowId/steps', lazy: () => import('./pages/WorkflowDetailPage') },
      { path: 'workflows/:workflowId/artifacts', lazy: () => import('./pages/WorkflowDetailPage') },
      { path: 'workflows/:workflowId/runs', lazy: () => import('./pages/WorkflowDetailPage') },
      { path: 'workflows/:workflowId/debug', lazy: () => import('./pages/WorkflowDetailPage') },
      { path: 'schedules', lazy: () => import('./pages/SchedulesPage') },
      { path: 'schedules/:definitionId', lazy: () => import('./pages/SchedulesPage') },
      { path: 'skills/*', lazy: () => import('./pages/SkillsPage') },
      { path: 'settings/*', lazy: () => import('./pages/SettingsPage') },
      { path: 'manifests', lazy: () => import('./pages/ManifestsPage') },
      { path: 'manifests/:manifestName', lazy: () => import('./pages/ManifestsPage') },
      { path: 'oauth-terminal', lazy: () => import('./pages/OAuthTerminalPage') },
    ],
  },
]);
```

Rules:

- use client route params for `workflowId`, `definitionId`, and `manifestName`;
- use URL search params for shareable filters and view state only;
- do not serialize large or secret-bearing state into URLs;
- use route loaders or query hooks for data, not server boot payloads;
- use hard browser navigation only for external URLs, downloads, auth redirects, and non-dashboard resources.

---

## 8. Shell and navigation

The dashboard masthead, navigation, alerts, layout rails, and global providers should be React-owned.

The shell should provide:

- `QueryClientProvider`;
- router provider;
- UI info/capability provider;
- theme provider;
- global alert/toast provider;
- live update provider;
- route-level loading and error boundaries;
- optional command palette provider;
- shared workspace layout state.

Navigation should use router-native links (`Link`, `NavLink`, or equivalent). Active state should come from the current route, not direct DOM mutation.

Server-rendered navigation partials should be removed after the SPA shell owns the masthead.

---

## 9. Capability and endpoint discovery

Replace route-specific dashboard boot config with a small same-origin UI info endpoint:

```http
GET /api/ui/info
```

Representative response:

```json
{
  "app": "moonmind",
  "buildId": "2026.06.29",
  "apiBase": "/api",
  "features": {
    "workflowList": true,
    "workflowActions": true,
    "workflowEditing": true,
    "workflowLiveUpdates": true,
    "artifacts": true,
    "schedules": true,
    "skills": true,
    "settings": true,
    "oauthTerminal": true
  },
  "limits": {
    "workflowListDefaultPageSize": 50,
    "workflowListMaxPageSize": 200,
    "artifactMaxUploadBytes": 10485760
  },
  "endpoints": {
    "workflows": "/api/executions",
    "workflowDetail": "/api/executions/{workflowId}",
    "workflowSteps": "/api/executions/{workflowId}/steps",
    "workflowUpdatesStream": "/api/workflows/updates/stream",
    "workflowEventsStream": "/api/workflows/{workflowId}/events/stream",
    "artifacts": "/api/artifacts",
    "skills": "/api/workflows/skills"
  }
}
```

Rules:

- this endpoint describes capabilities and endpoint templates only;
- it must not include large page data;
- it should be safe to cache briefly or revalidate;
- feature gates should hide routes or controls without breaking direct deep links;
- page data comes from page-specific APIs, not from the shell document;
- route-specific boot configuration and `/api/dashboard/config` are superseded by this endpoint and should not coexist with it after the SPA target is implemented.

---

## 10. Data contract posture

The SPA must not cache detail-grade payloads for list or picker surfaces.

Required contract hierarchy:

1. **Shell/capability data** — small, app-wide, cacheable configuration.
2. **List rows** — compact scan-first rows, safe for polling or stream patching.
3. **Picker rows** — smaller projections for dropdowns and dependency pickers.
4. **Detail snapshots** — workflow detail evidence and action capabilities.
5. **Paged evidence** — steps, artifacts, logs, timelines, and long histories.
6. **Streams** — patch/update events for active and visible surfaces.

Rules:

- list schemas should forbid or strip unknown detail-only fields;
- list responses should never include `inputParameters`, `taskInstructions`, `memo`, `searchAttributes`, debug fields, full artifact payloads, or long histories;
- detail pages should request evidence in layers so first render does not wait on every optional panel;
- artifact and log views should be paged or tailed;
- presigned/download flows remain normal browser resource flows, not SPA routes.

---

## 11. Server state, client state, and live updates

## 11.1 Server snapshots

Use TanStack Query for:

- list row snapshots;
- detail snapshots;
- paged steps;
- paged artifacts;
- settings and capability data;
- mutation results and invalidation.

## 11.2 Route-stable client state

Use a small client store for route-stable dashboard runtime state such as:

- active workflow/workspace selection;
- active run or detail tab;
- open side panels and split-pane layout;
- stream connection status;
- optimistic action state;
- user preferences not stored in the URL;
- command palette state.

Client stores must not become a second copy of durable server data. Server snapshots remain the source of truth.

## 11.3 Live updates

The persistent shell should own live update lifecycle. Preferred contracts:

```http
GET /api/workflows/updates/stream
GET /api/workflows/{workflowId}/events/stream
```

or WebSocket equivalents when bidirectional behavior is required.

Rules:

- list-level events patch compact list rows;
- detail events patch or invalidate scoped detail/step/artifact queries;
- background tabs pause or reduce live connections;
- disconnected streams degrade to compact polling;
- ended runs do not keep unnecessary live connections open;
- stream errors never erase existing artifact-backed or query-backed content.

---

## 12. Omnigent alignment and shared code seams

MoonMind should align with Omnigent at the architectural seams that are product-neutral:

- same-origin Vite/React SPA served by FastAPI;
- API routes registered before SPA fallback;
- small UI info/capability endpoint;
- persistent app shell;
- client-side route table;
- route-level lazy loading;
- TanStack Query for server snapshots;
- lightweight client store for active runtime/session/workspace state;
- SSE/WebSocket helpers with reconnect, backoff, and visibility awareness;
- shared API error normalization;
- shared endpoint-template interpolation;
- shared design tokens and shell primitives;
- shared workspace panels for files, artifacts, logs, terminal/OAuth sessions, timelines, and approvals.

MoonMind-specific concepts remain explicit:

- Workflow Execution;
- `workflowId` as product identity;
- Temporal-backed execution state;
- step ledger and artifact evidence;
- MoonMind workflow actions and recovery flows.

Omnigent-specific concepts remain explicit until product convergence is designed:

- sessions;
- chat turns;
- agent runner health;
- sandbox/session file resources;
- inbox/policy/account concepts.

Shared components should accept product-neutral inputs where practical, but should not blur durable backend semantics.

---

## 13. Acceptance criteria for full SPA completion

MoonMind can be considered fully converted when:

1. Dashboard routes are client-routed after initial shell load.
2. The dashboard navigation is React-owned.
3. Internal dashboard route transitions do not reload the document.
4. Direct refresh of supported dashboard deep links returns the SPA shell and renders the correct client route.
5. FastAPI API/auth/health/static/artifact routes are never captured by SPA fallback.
6. The initial HTML shell no longer contains route-specific data payloads beyond safe boot metadata.
7. `/api/ui/info` or an equivalent endpoint drives feature gates and endpoint discovery.
8. QueryClient and shell providers persist across internal route transitions.
9. List and picker surfaces use compact contracts only.
10. Live update lifecycle is owned by the persistent shell with compact polling fallback.
11. Shared code seams for Omnigent alignment are explicit and isolated.
12. Tests cover client routing, direct deep links, API fallback exclusion, navigation, query persistence, and feature-gated routes.

---

## 14. Testing requirements

Frontend tests should cover:

- route rendering under the client router;
- internal navigation without `window.location.assign`;
- active nav state;
- QueryClient persistence across route changes;
- page-level error boundaries;
- capability endpoint loading/failure states;
- feature-gated route visibility;
- stream pause/resume behavior when implemented.

Backend tests should cover:

- dashboard deep links return the SPA shell;
- API, auth, health, static, artifact, webhook, and OpenAPI routes do not return the SPA shell;
- unknown API paths return API errors;
- static assets receive correct cache headers;
- the shell does not embed route-specific secret-bearing query strings;
- direct refresh of supported extensionless dashboard routes works.

---

## 15. Documentation maintenance

When this target is implemented, update or retire older language that says client-side routing is out of scope. Until then, docs should describe the current custom-navigation bridge as an intermediate state and this document as the full SPA destination.