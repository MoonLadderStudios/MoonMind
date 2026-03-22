# TypeScript System

Status: **Design Draft**
Owners: MoonMind Engineering
Last Updated: 2026-03-22
Related: `README.md`, `api_service/static/task_dashboard/`, `docs/Tasks/TaskArchitecture.md`

---

## 1. Summary

MoonMind Mission Control has outgrown the “single static page with a little JavaScript” stage. The frontend now needs stronger contracts, safer refactors, clearer module boundaries, and a path to continue growing without forcing a full frontend-platform rewrite.

This document defines the **TypeScript tooling and frontend strategy** for MoonMind while **keeping FastAPI-owned server rendering for now**.

The core decision is:

- **Keep FastAPI as the owner of routes, auth, and HTML page delivery.**
- **Adopt TypeScript for all new frontend code.**
- **Use a modern frontend build pipeline for typed client code and componentized UI.**
- **Migrate page by page instead of rewriting Mission Control in one step.**

This is intentionally **not** a move to a separately deployed SPA. It is a move to a **typed, componentized, server-rendered frontend system**.

---

## 2. Goals

### 2.1 Primary Goals

1. Add strong typing to Mission Control frontend code.
2. Keep the current server-rendered deployment model and FastAPI route ownership.
3. Replace the growing monolithic dashboard JavaScript with a modular frontend architecture.
4. Improve safety when working with API responses, forms, page state, polling, and mutations.
5. Support incremental migration without blocking active feature work.
6. Keep local development straightforward inside the existing MoonMind development workflow.

### 2.2 Secondary Goals

1. Reduce frontend regressions caused by implicit data shapes and untyped DOM interactions.
2. Make it easier to split UI work across features such as tasks, proposals, schedules, settings, and auth profiles.
3. Prepare the frontend for future growth without requiring a second deployment surface today.
4. Preserve deep links, bookmarkable routes, and server-controlled access checks.

---

## 3. Non-Goals

The following are explicitly **out of scope** for this system:

1. Replacing FastAPI/Jinja with Next.js, Remix, or another full-stack frontend framework.
2. Introducing a separately deployed frontend service.
3. Requiring Node-based SSR in production.
4. Converting every existing Mission Control page in one PR.
5. Adding a client-side router as the primary routing authority.
6. Creating a shared cross-language type system that removes all backend/frontend duplication.

---

## 4. Architectural Decision

## 4.1 Decision Statement

MoonMind should adopt the following frontend model:

- **FastAPI remains the server-rendered page host.**
- **TypeScript becomes the required language for all new frontend code.**
- **React is used for interactive page islands and page-level client applications.**
- **Vite is used as the frontend build tool.**
- **Server-owned URLs remain canonical.**
- **Client code enhances and hydrates server-rendered pages rather than taking over the application as a full SPA.**

## 4.2 Why This Approach

This gives MoonMind the main benefits it needs now:

- typed UI contracts
- modular code organization
- reusable components
- safer async data handling
- better testing options
- modern frontend developer ergonomics

while keeping the current operational advantages:

- one deployment surface
- same-origin API access
- backend-controlled auth and authorization
- simple local startup model
- no separate frontend hosting layer

---

## 5. System Boundaries

## 5.1 What the Server Owns

FastAPI owns:

1. Route definitions and canonical URLs.
2. Authentication and authorization decisions.
3. Initial HTML responses.
4. Navigation shell, page frame, and server-injected boot payload.
5. Static asset serving in production.
6. Optional initial page data for first render when useful.

## 5.2 What the TypeScript Frontend Owns

The TypeScript frontend owns:

1. Interactive controls.
2. Polling and live-refresh behavior.
3. Form state and client validation.
4. Async data fetching for dynamic page regions.
5. Rich page-level UI composition.
6. Mutation flows such as create, update, pause, dismiss, promote, enable, disable, and OAuth session actions.
7. Component rendering for migrated pages.

## 5.3 Routing Rule

**The server remains the source of truth for route ownership.**

That means:

- `/tasks`, `/tasks/list`, `/tasks/manifests`, `/tasks/schedules`, `/tasks/proposals`, `/tasks/settings`, and similar paths continue to be server-routed.
- Client code may update query-string state and in-page filters.
- Client code may enhance navigation behavior where useful.
- Client code does **not** become the canonical source of route resolution in Phase 1.

This avoids paying the complexity cost of a full SPA before it is necessary.

---

## 6. Selected Tooling Standard

## 6.1 Required Tooling

The frontend stack should standardize on:

- **TypeScript** for all new frontend source
- **React** for component composition
- **Vite** for bundling and development server support
- **Tailwind CSS** and **PostCSS** for styling
- **ESLint** for linting
- **Vitest** for unit and component tests
- **Testing Library** for component behavior tests
- **openapi-typescript** for generated API type baselines
- **Zod** for runtime validation at API boundaries when needed
- **TanStack Query** for server-state fetching, caching, polling, and mutation invalidation

## 6.2 Why React

The current pain is not just typing. It is also:

- page complexity
- shared UI behavior
- duplicated control patterns
- repeated mutation and refresh logic
- a growing need for reusable view primitives

React is the most pragmatic choice for modularizing Mission Control without inventing a custom component system around manual DOM updates.

## 6.3 Why Vite

Vite fits MoonMind well because it provides:

- fast local startup
- strong TypeScript support
- straightforward React integration
- clean multi-entry builds
- manifest-based production asset loading
- direct compatibility with a server-rendered backend

## 6.4 Why TanStack Query

Mission Control is primarily a **server-state UI**:

- lists
- details
- polling snapshots
- mutation flows
- status refreshes
- cache invalidation after actions

TanStack Query matches that problem well and should be preferred over ad hoc fetch-plus-repaint logic.

---

## 7. Directory Layout

The frontend source should be moved out of the static asset output directory and into a dedicated source tree.

### 7.1 Proposed Layout

```text
frontend/
  src/
    boot/
      parseBootPayload.ts
      mountPage.tsx
    entrypoints/
      tasks-home.tsx
      tasks-list.tsx
      task-detail.tsx
      manifests-list.tsx
      schedules-list.tsx
      schedule-create.tsx
      proposals-list.tsx
      proposal-detail.tsx
      settings.tsx
    components/
      layout/
      tables/
      forms/
      status/
      feedback/
    features/
      tasks/
      manifests/
      schedules/
      proposals/
      settings/
      authProfiles/
      workerPause/
    lib/
      api/
      query/
      routing/
      format/
      dom/
      errors/
    generated/
      openapi.ts
    styles/
      mission-control.css
    types/
      boot.ts
      ui.ts
      domain.ts
  vite.config.ts
  tsconfig.json
  tsconfig.node.json
  eslint.config.js
```

### 7.2 Production Output

Built assets should be emitted to:

```text
api_service/static/task_dashboard/dist/
```

The output directory is a **build artifact location**, not the source of truth for editable frontend code.

### 7.3 Template Location

Server-rendered templates remain in the backend template area, for example:

```text
api_service/templates/
```

If Mission Control templates are split by page, they should remain server-owned.

---

## 8. Build and Asset Pipeline

## 8.1 Production Model

Production remains server-rendered:

1. FastAPI renders the page.
2. FastAPI injects the correct built JS and CSS assets.
3. The browser loads the page.
4. The TypeScript bundle hydrates or mounts the interactive region for that page.

There is **no separate frontend production host** in this design.

## 8.2 Vite Manifest Integration

Vite should build with a manifest enabled.

FastAPI should use a small asset helper that:

1. reads the Vite manifest in production
2. resolves the correct JS and CSS files for an entrypoint
3. injects those assets into the rendered template

A small backend utility module should own this lookup, for example:

```text
api_service/ui_assets.py
```

## 8.3 Development Model

In development, the backend should support two modes:

### A. Transitional Mode

- existing static assets continue to work
- new TS pages can be developed with production-like builds

### B. Full UI Dev Mode

- Vite dev server runs with HMR
- FastAPI templates detect a configured dev-server URL
- templates load the Vite client and the page entrypoint from that dev server

This preserves server-rendered pages while still giving fast frontend iteration.

## 8.4 Package Scripts

The root `package.json` may continue to be used initially, but the frontend scripts should become explicit.

Recommended scripts:

```json
{
  "scripts": {
    "ui:dev": "vite --config frontend/vite.config.ts",
    "ui:build": "vite build --config frontend/vite.config.ts",
    "ui:typecheck": "tsc --noEmit -p frontend/tsconfig.json",
    "ui:lint": "eslint frontend/src --ext .ts,.tsx",
    "ui:test": "vitest run --config frontend/vite.config.ts",
    "ui:test:watch": "vitest --config frontend/vite.config.ts"
  }
}
```

## 8.5 Docker and CI

The standard build pipeline should run:

1. `npm ci`
2. `npm run ui:typecheck`
3. `npm run ui:lint`
4. `npm run ui:test`
5. `npm run ui:build`

The production image should contain built assets, not raw TypeScript-only sources.

---

## 9. Server Rendering Contract

## 9.1 Page Shell Contract

Each server-rendered page should provide:

1. a stable root element for the page app
2. a page identifier
3. a JSON boot payload
4. any server-rendered fallback or placeholder markup

Example:

```html
<div id="mission-control-root" data-page="tasks-list"></div>
<script id="moonmind-ui-boot" type="application/json">
  {"page":"tasks-list","apiBase":"/api","features":{"oauth":true}}
</script>
```

## 9.2 Boot Payload Rules

The boot payload should be:

- JSON only
- minimal
- explicit
- versionable
- page-scoped where possible

The boot payload may include:

- page name
- route params
- initial filters
- feature flags
- auth or capability flags safe for the client
- endpoint URLs
- polling defaults
- initial server-fetched data when useful

It should **not** include arbitrary inline executable logic.

## 9.3 Template Responsibility

Templates should remain simple.

Templates should:

- define structure
- inject assets
- provide root nodes
- provide boot payloads

Templates should not become a second place where application logic grows uncontrolled.

---

## 10. Type System Strategy

## 10.1 Strictness Policy

TypeScript should be configured in strict mode.

Recommended baseline:

```json
{
  "compilerOptions": {
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "exactOptionalPropertyTypes": true,
    "useUnknownInCatchVariables": true,
    "noImplicitOverride": true,
    "noEmit": true
  }
}
```

## 10.2 No-`any` Rule

The default rule is:

- do not introduce `any`
- prefer `unknown` at unsafe boundaries
- narrow explicitly
- validate external inputs

If an escape hatch is temporarily required during migration, it must be localized and documented.

## 10.3 API Contract Types

Generated types should be created from MoonMind’s OpenAPI schema.

Recommended flow:

1. export the FastAPI OpenAPI schema during development or CI
2. generate TypeScript types into `frontend/src/generated/openapi.ts`
3. use those generated types as the baseline shape for API requests and responses

This reduces backend/frontend drift for core payloads.

## 10.4 Domain Types vs Generated Types

The codebase should distinguish between:

- **generated transport types** from OpenAPI
- **UI/domain types** used by components and hooks

Generated API types should not automatically become the final shape used everywhere in the UI.

Instead:

- map transport types into cleaner UI types when helpful
- normalize missing fields and enum variants at the boundary
- keep page components insulated from raw transport inconsistencies

## 10.5 Runtime Validation

Static types do not validate runtime responses.

For unstable, external, or especially important boundaries, use **Zod** validators to:

- parse boot payloads
- validate loosely shaped API responses
- protect against partial rollout mismatches
- safely handle legacy endpoint inconsistencies during migration

---

## 11. Component and Page Strategy

## 11.1 Page Model

Mission Control should move to **page-level entrypoints** rather than one permanently growing frontend script.

Each migrated page gets:

- one server route
- one template
- one TS/React entrypoint
- shared feature modules underneath

## 11.2 Shared UI Layers

Shared UI code should be separated into:

1. **layout primitives**
2. **domain feature modules**
3. **generic form and table components**
4. **status, badge, and feedback components**
5. **API/query hooks**

## 11.3 Migration Rule

During migration:

- the legacy dashboard JavaScript may continue serving unmigrated pages
- new feature work should default to the TypeScript system
- legacy JS should receive bug fixes only once a replacement path exists

## 11.4 Client Router Policy

Do **not** introduce React Router as a primary app dependency in Phase 1.

Reason:

- the server already owns canonical routes
- deep links already matter
- access control already belongs on the backend
- the current need is typed modular UI, not client-owned route orchestration

A lightweight helper for reading route params or query strings is fine.

---

## 12. Data Fetching, Polling, and Mutations

## 12.1 Server State Standard

All dynamic backend-backed UI state should move toward a standard query layer using **TanStack Query**.

This should be used for:

- lists
- detail records
- polling snapshots
- action invalidation
- background refresh
- optimistic or semi-optimistic mutation flows where appropriate

## 12.2 Query Key Standard

Query keys should be explicit and stable, for example:

```text
["tasks", "list", filters]
["tasks", "detail", taskId]
["proposals", "list", filters]
["schedules", "detail", scheduleId]
["workerPause", "snapshot"]
```

## 12.3 Polling Policy

Polling should move out of ad hoc `setInterval` usage and into standardized query behavior where possible.

Each polling use case should define:

- refresh interval
- visibility policy
- pause policy
- selection-stability behavior
- mutation-triggered refresh policy

## 12.4 Mutation Standard

Mutations should use a shared API client and shared error handling rules.

Every mutation path should define:

1. request type
2. response type
3. success feedback behavior
4. cache invalidation behavior
5. retry policy if any

---

## 13. Styling Strategy

## 13.1 Tailwind Continues

Tailwind remains the primary styling layer.

There is no need to replace the existing styling direction just to adopt TypeScript.

## 13.2 CSS Build Ownership

Once the Vite frontend is established, the frontend build should become the canonical owner of page CSS output.

That means the long-term direction is:

- CSS imported from frontend entrypoints or a shared style layer
- Vite/PostCSS handles the build
- old standalone CSS build scripts can eventually be removed

## 13.3 Class Strategy

Use a predictable class strategy:

- shared semantic components for repeated patterns
- utility classes for layout and local composition
- avoid large string-built HTML templates in TS code

---

## 14. Backend Integration Points

## 14.1 Asset Resolver

Add a small backend helper to resolve Vite assets from the manifest.

## 14.2 OpenAPI Export

Add a standard script or tool path for exporting the backend OpenAPI schema for type generation.

Possible locations include:

```text
scripts/export_openapi.py
```

or

```text
tools/export_openapi.py
```

## 14.3 Boot Payload Serializer

If multiple pages need boot payloads, add a small backend helper layer so payload creation is:

- explicit
- testable
- consistent across routes

---

## 15. Migration Plan

## 15.1 Phase 0 — Foundation

1. Add `frontend/` source tree.
2. Add Vite, TypeScript, React, ESLint, Vitest, Testing Library, TanStack Query, Zod, and OpenAPI generation tooling.
3. Add a Vite manifest-backed asset resolver in FastAPI.
4. Add a boot payload parser and page mount system.
5. Keep existing dashboard JS fully operational.

## 15.2 Phase 1 — First Typed Vertical Slice

Migrate one page end-to-end to prove the system.

Recommended candidates:

- settings
- proposals list/detail
- recurring schedules list/create

These areas have enough interactivity to validate the new architecture without being the most operationally risky surface.

## 15.3 Phase 2 — Feature-by-Feature Migration

Move the rest of Mission Control feature areas incrementally.

Suggested order:

1. settings and auth profiles
2. proposals
3. schedules
4. manifests
5. task detail
6. task lists and landing views

## 15.4 Phase 3 — Legacy Retirement

When all major pages are migrated:

1. remove the monolithic dashboard JS entrypoint
2. remove legacy helper code
3. simplify static asset structure
4. make the TS system the only path for new frontend work

## 15.5 Migration Constraints

During migration:

- avoid simultaneous rewrites of backend routes and frontend architecture unless necessary
- keep URLs stable
- preserve existing workflow operations
- prioritize high-change pages first

---

## 16. Testing Standard

## 16.1 Required Automated Checks

At minimum, new TS frontend code should have:

1. typecheck coverage
2. lint coverage
3. unit tests for critical formatters, parsers, and hooks
4. component tests for important UI flows

## 16.2 Recommended Test Targets

Prioritize tests for:

- boot payload parsing
- API client normalization
- polling pause/resume logic
- task/proposal/schedule mutation hooks
- settings and auth profile forms
- route param parsing

## 16.3 Browser E2E

End-to-end browser coverage is valuable but not required to begin adoption.

Playwright may be added later once the typed component system is established.

---

## 17. Operational Rules

## 17.1 New Frontend Code Rule

All new non-trivial frontend code should be written in TypeScript.

## 17.2 Legacy Rule

Do not expand the legacy dashboard JavaScript indefinitely.

Once the TypeScript system exists:

- use TS for new pages and major feature additions
- use legacy JS only for small bug fixes or until that page is migrated

## 17.3 Page Ownership Rule

Every page should have a clearly identifiable owner at the feature-module level, even if the top-level template remains backend-owned.

## 17.4 Build Rule

Do not hand-edit generated frontend build artifacts.

Do not treat `dist/` output as source code.

---

## 18. Risks and Mitigations

## 18.1 Risk: Two Frontend Systems During Migration

For a period of time, MoonMind will have:

- legacy JS pages
- TS/React pages

### Mitigation

Keep the bridge period explicit and temporary. Track migration status by page.

## 18.2 Risk: Overengineering Too Early

A full client-router rewrite would add complexity that is not yet required.

### Mitigation

Keep server-rendered routes and page-level entrypoints. Solve typing and modularity first.

## 18.3 Risk: API Drift

UI code may still break if backend contracts change unexpectedly.

### Mitigation

Use OpenAPI-generated types plus targeted runtime validation at unstable boundaries.

## 18.4 Risk: Developer Workflow Friction

Adding modern frontend tooling can feel heavier than a single JS file.

### Mitigation

Keep the system small, documented, and convention-driven. Avoid introducing unnecessary libraries beyond the selected standard.

---

## 19. Acceptance Criteria

This design should be considered successfully implemented when all of the following are true:

1. A dedicated `frontend/` TypeScript source tree exists.
2. FastAPI can serve Vite-built assets in production.
3. At least one real Mission Control page is fully migrated to the new TS system.
4. The CI pipeline performs UI typechecking, linting, testing, and building.
5. New frontend feature work defaults to TypeScript.
6. The server still owns routes and page delivery.
7. Mission Control is still deployable as a single backend-served application.

---

## 20. Future Evolution

This design keeps the door open for a later decision to move further toward a richer client application if Mission Control outgrows page-level enhancement.

That future decision should be revisited only if one or more of the following become true:

1. frontend routing complexity becomes a primary product concern
2. multiple engineers are working full-time on the frontend surface
3. the UI needs independent deployment velocity from the backend
4. real-time interaction patterns become dominant across most pages
5. server-rendered page ownership becomes a clear bottleneck

Until then, MoonMind should optimize for the simpler path:

- **typed frontend code**
- **componentized UI**
- **server-owned routing**
- **single-deployment architecture**

---

## 21. Recommendation

MoonMind should adopt **Vite + React + TypeScript** as the new frontend system while **keeping FastAPI-driven server rendering and route ownership**.

This is the highest-leverage next step because it improves maintainability and safety immediately without forcing the project into a heavier frontend deployment model before that model is warranted.
