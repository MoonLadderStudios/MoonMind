# Remaining work: `docs/UI/TypeScriptSystem.md`

**Source:** [`docs/UI/TypeScriptSystem.md`](../../UI/TypeScriptSystem.md)  
**Last synced:** 2026-03-25

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
| `ui:build` in CI | **Not** in default workflow (see Phase 0) |
| Sample entrypoint `tasks-home` + test route | Present |
| **Settings** entrypoint `settings.tsx` + live route `/tasks/settings` | Present (Phase 1 slice) |
| `openapi-typescript` in devDependencies; `frontend/src/generated/openapi.ts` | devDep installed; **no** generated file or npm script yet |
| `frontend/src/lib/api/` shared API client layer | **Not** present (see Phase 0) |
| `frontend/src/lib/query/` query key conventions | **Not** present (see Phase 0) |
| `frontend/src/features/` feature module directories | **Not** present |
| Legacy `api_service/static/task_dashboard/dashboard.js` + Tailwind CLI `dashboard:css` | Still primary for non-settings pages |

---

## Migration status by page (fill in as you go)

| Page / route area | Owner module | Legacy JS | TS entrypoint | Notes |
|-------------------|--------------|-----------|---------------|-------|
| Tasks home / hub | | | `tasks-home` (demo) | Replace demo with real hub when ready |
| Task list | | | | |
| Task detail | | | | |
| Manifests | | | | |
| Schedules | | | | |
| Proposals | | | | |
| Settings | `task_dashboard.py` | Removed | `settings` ✅ | Live at `/tasks/settings`; API-key forms via TanStack Query |

---

## Phase 0 — Foundation tooling

**Goal:** The toolchain is complete, documented for contributors, and CI proves the **production bundle** builds—not only typecheck/lint/tests.

### Verification checklist

- [x] `npm ci` then `npm run ui:dev` serves Vite against `frontend/vite.config.ts` without extra env hacks.
- [x] `npm run ui:build` succeeds and writes `api_service/static/task_dashboard/dist/` including `.vite/manifest.json`.
- [x] A FastAPI-rendered page can inject assets via `ui_assets("<entrypoint>")` and load JS/CSS in a browser (manifest keys match `entrypoints/<name>.tsx`). — Verified via live `/tasks/settings` route.
- [x] `npm run ui:typecheck`, `ui:lint`, `ui:test` match what CI runs (or CI is updated to match local truth).
- [x] Boot payload: server injects JSON the client expects; `parseBootPayload` (and Zod schema, if used) stays aligned—add/adjust unit tests when the contract changes. — `ui_boot.py` centralized.

### Task list

1. [x] **CI: add `npm run ui:build`** to the same job that runs typecheck/lint/test (or a dedicated frontend job on PRs), so broken Rollup/Vite config cannot reach `main`.
2. [x] **OpenAPI → types path:** add a documented, repeatable command (e.g. npm script calling `openapi-typescript`) that writes `frontend/src/generated/openapi.ts` (or agreed path); gate drift with CI or a scheduled check. — `openapi-typescript` script `api:types` added and hooked into CI.
3. [x] **Shared API client layer:** introduce `frontend/src/lib/api/` (fetch wrapper, error normalization, credentials/same-origin policy) consistent with [`docs/UI/TypeScriptSystem.md`](../../UI/TypeScriptSystem.md) §10–12.
4. [x] **Query key + hook conventions:** add `frontend/src/lib/query/` with documented query-key shapes (see canonical §12.2) and a minimal example hook (even if only used by the Phase 1 slice).
5. [x] **Boot payload helper (backend):** centralized in `api_service/ui_boot.py` (`generate_boot_payload`), used by both `tasks-home` and `settings` routes, with tests in `test_ui_route.py`.
6. [x] **Contributor docs (minimal):** in `README.md` or existing UI dev section, add 5–10 lines: install deps, `ui:dev` vs `ui:build`, where dist goes, and "do not edit `dist/`" (canonical §17.4).
7. [x] **Optional — Vite dev proxy:** (Deferred: keep transitional mode for now) if full "UI dev mode" (canonical §8.3 B) is desired, specify and implement proxy to FastAPI for API calls; otherwise explicitly defer and keep "transitional mode" only.

**Exit criteria for Phase 0:** Items above are either done or explicitly deferred with a one-line rationale in this file; CI runs `ui:build`; first real page migration (Phase 1) does not require new tooling decisions.

---

## Phase 1 — First typed vertical slice

**Goal:** At least **one** production Mission Control page is fully served through FastAPI + Vite manifest + React mount, with real data and mutations—not the demo `tasks-home` placeholder. This satisfies canonical §19 item 3.

**Pick one primary slice** (recommended: smallest surface with clear API boundaries—often **settings** or a read-heavy list). The tasks below are the same pattern; duplicate only the feature-specific bullets.

### Shared tasks (any chosen slice)

1. [x] **Route and template:** `/tasks/settings` route in `task_dashboard.py` renders HTML with `ui_assets("settings")` + boot payload injection.
2. [x] **Boot payload contract:** `generate_boot_payload("settings")` via `ui_boot.py`; `parseBootPayload` handles client-side.
3. [x] **Vite entrypoint:** `frontend/src/entrypoints/settings.tsx` registered in `rollupOptions.input` in `vite.config.ts`.
4. [ ] **Feature module:** Logic currently inline in `settings.tsx`; needs extraction to `frontend/src/features/settings/`.
5. [x] **Data layer:** TanStack Query `useQuery` (profile fetch) and `useMutation` (profile update) with `invalidateQueries` on success.
6. [/] **UX parity:** API-key management implemented; remaining settings sub-pages (OAuth, worker toggles) not yet covered.
7. [ ] **Tests:** No Vitest tests for settings components/hooks yet.
8. [x] **Cutover:** `/tasks/settings` serves the React bundle; legacy JS not required.
9. [x] **Update migration table** (above) — updated 2026-03-25.

### If slice = **Settings**

- [/] Inventory all settings-related endpoints and form fields from legacy UI. — API-key fields (`/me`, `/me/profile`) done; other settings endpoints TBD.
- [ ] Map each to generated OpenAPI types or explicit domain types. — Blocked on Phase 0 task 2 (OpenAPI → types).
- [x] Implement forms with client validation + server error display; mutations invalidate relevant queries. — API-key form with `useMutation` + `invalidateQueries` + notice display.

### If slice = **Proposals** (list + detail or list-only for Phase 1)

- [ ] List view: filters, pagination or “load more,” status badges consistent with backend enums.
- [ ] If in scope: detail route as second entrypoint or defer to Phase 2 with explicit note.
- [ ] Mutations (approve/dismiss/etc.): each defines invalidation + optimistic policy per canonical §12.4.

### If slice = **Schedules**

- [ ] Calendar/list UX parity; polling or refresh rules documented per §12.3.
- [ ] Create/edit flows: wizard or modal components colocated under `features/schedules/`.

**Exit criteria for Phase 1:** One real page runs on TS/React; legacy JS for that page is removable or reduced to shim-only; acceptance criteria in canonical §19 remain satisfied for the new path.

---

## Phase 2 — Feature-by-feature migration

**Order (from canonical strategy):** settings → proposals → schedules → manifests → task detail → task lists. Adjust order only if dependencies force it; record changes in the migration table.

For **each** feature below, repeat a standard work unit:

**Per-feature template**

1. [ ] **Audit:** List routes, legacy JS functions, and API calls touched by this feature.
2. [ ] **Entrypoints:** Add `entrypoints/<feature>-….tsx` as needed (list vs detail may be separate entries).
3. [ ] **Implement:** `features/<feature>/` components + hooks; prefer shared `components/` for cross-feature UI.
4. [ ] **Styling:** Prefer Tailwind via frontend build for new code; avoid growing inline string HTML in TS (canonical §13.3).
5. [ ] **Queries/mutations:** Standardize on TanStack Query; remove ad hoc `setInterval` polling where this feature used it.
6. [ ] **Tests:** Hooks and high-value components; regression tests for parsing/formatting edge cases.
7. [ ] **Cutover + tracker:** Switch templates to Vite assets; update migration table; legacy JS only for still-unmigrated pages.

### Feature buckets

#### 2.A Settings (if not fully done in Phase 1)

- [ ] Any remaining settings sub-pages or OAuth/profile flows.
- [ ] Worker pause / operational toggles if they live under settings in the UI.

#### 2.B Proposals

- [ ] Proposals list entrypoint and shared table/filter components.
- [ ] Proposal detail entrypoint: comments, actions, status timeline if present in legacy UI.

#### 2.C Schedules

- [ ] Schedule list and create/edit flows.
- [ ] Schedule detail if distinct from list row expansion.

#### 2.D Manifests

- [ ] Manifest list and manifest run / status views tied to `tasks/manifests` routes.
- [ ] Align with manifest API shapes; use generated types where stable.

#### 2.E Task detail

- [ ] Replace core task detail interactions (polling, actions, attachments if any) with TS modules.
- [ ] Preserve deep links and server gating; no client router (canonical §11.4).

#### 2.F Task lists

- [ ] Task list and hub pages: last mile of legacy dashboard usage typically lives here—plan for largest DOM/query surface.
- [ ] Shared list primitives (`components/tables/`, filters) to avoid duplication across tasks/proposals/schedules.

**Exit criteria for Phase 2:** All Mission Control pages in scope use TS entrypoints; legacy dashboard script is only loaded where explicitly still required (should shrink to zero before Phase 3 ends).

---

## Phase 3 — Legacy retirement

**Goal:** Single primary frontend story: TypeScript + Vite + React under FastAPI; no parallel “main” dashboard JS path.

### Task list

1. [ ] **Remove or gut** `api_service/static/task_dashboard/dashboard.js` (and any monolithic entry) after confirming no template references it.
2. [ ] **Templates:** Audit `api_service/templates/` for script tags pointing at legacy bundles; remove dead includes.
3. [ ] **CSS pipeline:** Move canonical dashboard CSS generation toward Vite/PostCSS-owned entry (canonical §13.2); deprecate standalone `dashboard:css` scripts once unused.
4. [ ] **Static cruft:** Delete unused legacy helpers under `api_service/static/task_dashboard/` (with grep verification).
5. [ ] **Documentation:** Update `README.md` / operator docs to describe only the new build path; mark [`docs/UI/TypeScriptSystem.md`](../../UI/TypeScriptSystem.md) status from Draft to adopted when appropriate.
6. [ ] **E2E (optional):** Add Playwright (canonical §16.3) for one or two critical flows if regressions remain a concern post-cutover.

**Exit criteria for Phase 3:** No production dependency on the old monolithic dashboard JS; `ui:build` is part of release/deploy expectations; migration table shows complete.

---

## Constraints during migration (§15.5 / §17 / §18)

Track these as **non-goals** until explicitly revisited:

- **Two systems in flight:** Legacy JS and TS coexist until Phase 3; new feature work defaults to TS (§17.1–17.2). Do not expand legacy patterns for new features.
- **No SPA takeover:** Server owns routes; do not introduce React Router as primary orchestration in early phases (§11.4).
- **No hand-edited dist:** All changes go through `frontend/src` and Vite (§17.4).
- **API drift:** Prefer OpenAPI-generated types + Zod at unstable boundaries (§18.3).
- **Workflow friction:** Keep dependency set aligned with canonical §6; document commands contributors actually run (§18.4).

### Risk-driven tasks (ongoing)

- [ ] Keep the **migration status table** accurate for §18.1 (visibility into bridge period).
- [ ] When adding endpoints, update OpenAPI generation inputs so TS stays aligned (§18.3).
- [ ] Review each Phase 2 PR for accidental `any` expansion; enforce ESLint/typescript-eslint strictness per canonical §10.

---

## References

- [`docs/UI/TypeScriptSystem.md`](../../UI/TypeScriptSystem.md) — architecture, tooling standard, acceptance criteria.
- `frontend/vite.config.ts`, `api_service/ui_assets.py`, root `package.json` scripts — concrete integration points.
