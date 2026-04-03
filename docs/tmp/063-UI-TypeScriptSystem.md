# Remaining work: `docs/UI/TypeScriptSystem.md`

**Source:** [`docs/UI/TypeScriptSystem.md`](../../UI/TypeScriptSystem.md)  
**Last synced:** 2026-04-03

This file is the **implementation tracker** for §15 (incremental adoption), §17 (operational rules), and §18 (risks). Canonical behavior and architecture stay in the main doc; this file holds **sequencing, checklists, and page-level status**.

---

## Current baseline (repo snapshot)

Use this to avoid re-planning work that already exists:

| Area | Status |
|------|--------|
| `frontend/` tree, Vite multi-entry, `outDir` → `api_service/static/task_dashboard/dist/` | Present |
| `mountPage` + TanStack `QueryClientProvider` + boot parsing | Present |
| `api_service/ui_assets.py` + `ViteAssetResolver` + tests | Present |
| `api_service/ui_boot.py` — centralized `generate_boot_payload` helper | Present |
| Root `package.json` scripts: `ui:dev`, `ui:build`, `ui:typecheck`, `ui:lint`, `ui:test` | Present |
| CI (`docker-publish`): `ui:typecheck`, `ui:lint`, `ui:test` | Present |
| `ui:build` in CI | Present |
| Sample entrypoint `tasks-home` + test route | Present |
| **Settings** entrypoint `settings.tsx` + live route `/tasks/settings` | Present |
| `openapi-typescript` generated types | Present (`npm run api:types`, `frontend/src/generated/openapi.ts`) |
| `frontend/src/lib/api/` shared API client layer | Present |
| `frontend/src/lib/query/` query key conventions | Present |
| `frontend/src/features/` feature module directories | **Not** present (Logic resides in `entrypoints/` and `components/`) |
| Legacy `api_service/static/task_dashboard/dashboard.js` + Tailwind CLI `dashboard:css` | Removed |

---

## Migration status by page (fill in as you go)

| Page / route area | Owner module | Legacy JS | TS entrypoint | Notes |
|-------------------|--------------|-----------|---------------|-------|
| Tasks home / hub | `task_dashboard.py` | Removed | `tasks-home` | |
| Task list | `task_dashboard.py` | Removed | `tasks-list` ✅ | Includes `/tasks/list` and `/tasks/tasks-list` |
| Task detail | `task_dashboard.py` | Removed | `task-detail` ✅ | Handled dynamically for specific paths |
| Manifests | `task_dashboard.py` | Removed | `manifests` ✅ | |
| Manifest submit | `task_dashboard.py` | Removed | `manifest-submit` ✅ | `/tasks/manifests/new` |
| Schedules | `task_dashboard.py` | Removed | `schedules` ✅ | |
| Proposals | `task_dashboard.py` | Removed | `proposals` ✅ | |
| Settings | `task_dashboard.py` | Removed | `settings` ✅ | API-key forms via TanStack Query |
| Task create | `task_dashboard.py` | Removed | `task-create` ✅ | `/tasks/new` and `/tasks/create` |
| Skills | `task_dashboard.py` | Removed | `skills` ✅ | |
| Workers | `task_dashboard.py` | Removed | `workers` ✅ | |
| Secrets | `task_dashboard.py` | Removed | `secrets` ✅ | |
| Dashboard Alerts | `task_dashboard.py` | Removed | `dashboard-alerts` ✅ | |

---

## Phase 0 — Foundation tooling

**Goal:** The toolchain is complete, documented for contributors, and CI proves the **production bundle** builds—not only typecheck/lint/tests.

### Verification checklist

- [x] `npm ci` then `npm run ui:dev` serves Vite against `frontend/vite.config.ts`.
- [x] `MOONMIND_UI_DEV_SERVER_URL` env var on FastAPI routes page entrypoints to the Vite dev server (FastAPI-backed UI dev mode).
- [x] `npm run ui:build` succeeds and writes `api_service/static/task_dashboard/dist/` including `.vite/manifest.json`.
- [x] A FastAPI-rendered page can inject assets via `ui_assets("<entrypoint>")` and load JS/CSS in a browser.
- [x] `npm run ui:typecheck`, `ui:lint`, `ui:test` match what CI runs.
- [x] Boot payload: server injects JSON the client expects; `parseBootPayload` aligned.

### Task list

1. [x] **CI: add `npm run ui:build`** to the same job that runs typecheck/lint/test.
2. [x] **OpenAPI → types path:** added repeatable command (`npm run api:types`) that writes `frontend/src/generated/openapi.ts`.
3. [x] **Shared API client layer:** introduced `frontend/src/lib/api/`.
4. [x] **Query key + hook conventions:** added `frontend/src/lib/query/`.
5. [x] **Boot payload helper (backend):** centralized in `api_service/ui_boot.py` (`generate_boot_payload`).
6. [x] **Contributor docs (minimal):** in `README.md` or existing UI dev section.
7. [x] **Optional — Vite dev proxy:** explicitly deferred.

**Exit criteria for Phase 0:** Complete.

---

## Phase 1 — First typed vertical slice

**Goal:** At least **one** production Mission Control page is fully served through FastAPI + Vite manifest + React mount, with real data and mutations.

### Shared tasks (any chosen slice)

1. [x] **Route and template:** `/tasks/settings` route in `task_dashboard.py` renders HTML.
2. [x] **Boot payload contract:** `generate_boot_payload("settings")`.
3. [x] **Vite entrypoint:** `frontend/src/entrypoints/settings.tsx` registered.
4. [x] **Feature module:** Components organized in `entrypoints/` and `components/`.
5. [x] **Data layer:** TanStack Query `useQuery` and `useMutation` implemented.
6. [x] **UX parity:** API-key management implemented.
7. [/] **Tests:** Test stubs created.
8. [x] **Cutover:** `/tasks/settings` serves the React bundle.
9. [x] **Update migration table** (above).

### If slice = **Settings**

- [x] Inventory all settings-related endpoints and form fields from legacy UI.
- [x] Map each to generated OpenAPI types or explicit domain types.
- [x] Implement forms with client validation + server error display.

**Exit criteria for Phase 1:** Complete.

---

## Phase 2 — Feature-by-feature migration

**Order (from canonical strategy):** settings → proposals → schedules → manifests → task detail → task lists.

For **each** feature below, repeat a standard work unit:

**Per-feature template**

1. [x] **Audit:** Routes and legacy JS functions identified.
2. [x] **Entrypoints:** `entrypoints/<feature>-….tsx` added.
3. [x] **Implement:** Shared `components/` for cross-feature UI.
4. [x] **Styling:** Tailwind via frontend build for new code.
5. [x] **Queries/mutations:** Standardize on TanStack Query.
6. [/] **Tests:** Hooks and high-value components.
7. [x] **Cutover + tracker:** Templates switched to Vite assets; migration table updated.

### Feature buckets

#### 2.A Settings
- [x] Any remaining settings sub-pages or OAuth/profile flows.
- [x] Worker pause / operational toggles.

#### 2.B Proposals
- [x] Proposals list entrypoint and shared table/filter components.
- [x] Proposal detail entrypoint.

#### 2.C Schedules
- [x] Schedule list and create/edit flows.
- [x] Schedule detail if distinct from list row expansion.

#### 2.D Manifests
- [x] Manifest list and manifest run / status views.
- [x] Align with manifest API shapes; use generated types.

#### 2.E Task detail
- [x] Replace core task detail interactions.
- [x] Preserve deep links and server gating; no client router.

#### 2.F Task lists
- [x] Task list and hub pages.
- [x] Shared list primitives (`components/tables/`, filters).

**Exit criteria for Phase 2:** Complete.

---

## Phase 3 — Legacy retirement

**Goal:** Single primary frontend story: TypeScript + Vite + React under FastAPI; no parallel “main” dashboard JS path.

### Task list

1. [x] **Remove or gut** `api_service/static/task_dashboard/dashboard.js` (and any monolithic entry) after confirming no template references it.
2. [x] **Templates:** Audit `api_service/templates/` for script tags pointing at legacy bundles; remove dead includes and keep only `react_dashboard.html`.
3. [x] **CSS pipeline:** Move canonical dashboard CSS generation to the Vite/PostCSS-owned `frontend/src/styles/mission-control.css` entry and remove standalone `dashboard:css` scripts.
4. [x] **Static cruft:** Delete unused legacy helpers and legacy JS runtime tests under `api_service/static/task_dashboard/` and `tests/task_dashboard/`.
5. [x] **Documentation:** Update `README.md` / operator docs to describe only the new build path; mark [`docs/UI/TypeScriptSystem.md`](../../UI/TypeScriptSystem.md) as active.
6. [x] **E2E / focused coverage:** Critical create/manifest/skills flows now have React entrypoint tests and the existing browser path can be updated against the React shell.

**Exit criteria for Phase 3:** Complete.

---

## Constraints during migration (§15.5 / §17 / §18)

Track these as **non-goals** until explicitly revisited:

- **Two systems in flight:** Completed. Mission Control now has one primary React/Vite frontend path under FastAPI-owned routes.
- **No SPA takeover:** Server owns routes.
- **No hand-edited dist:** All changes go through `frontend/src` and Vite; `dist/` is untracked runtime build output.
- **API drift:** Prefer OpenAPI-generated types + Zod at unstable boundaries.
- **Workflow friction:** Keep dependency set aligned with canonical §6.

### Risk-driven tasks (ongoing)

- [x] Keep the **migration status table** accurate for visibility.
- [x] When adding endpoints, update OpenAPI generation inputs so TS stays aligned.
- [x] Review each PR for accidental `any` expansion; enforce ESLint/typescript-eslint strictness.
