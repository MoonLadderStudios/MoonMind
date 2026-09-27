# Route audit — MoonMind#4559 mobile overflow (per `frontend/src/lib/dashboardRoutes.ts` destinations)

Candidate: `9cfe27f7f8a767bd8165dd6c016e2a8667b56c79` on `moonmind-job-39d1c4f4` (verified 2026-09-25).
Method: code inspection of the complete ancestor sizing chain + targeted unit runs.
Shared-component jsdom regression on this revision (all PASS, re-run fresh 2026-09-25 on 9cfe27f7): workflow-list
89/89, workflow-detail 200/200, skills 27/27, remediations 8/8, schedules 51/51,
artifacts 3/3, settings 8/8, DataTable 9/9, omnigent-inventory 7/7,
ProviderProfilesManager.mobile4559 2/2, providerProfileTiers 14/14.
Browser geometry (320/390/768px) is covered by
`frontend/src/browser/mobileOverflow4559.browser.test.tsx` (6 cases: the prior 5 plus a
production-component ProviderProfilesManager journey) but could
not be executed in this sandbox (no Playwright browsers:
`browserType.launch` fails — missing `chromium_headless_shell-1228` executable;
`npx playwright install chromium` re-attempted 2026-09-25 on 9cfe27f7 and blocked by sandbox proxy ERR_ACCESS_DENIED to
cdn.playwright.dev; CI legs
`frontend-browser` + `frontend-browser-webkit-targeted` never ran for this
revision). Rows below distinguish what code proves from what still needs the
browser/CI run. No CI publication was attempted from this remediation step
(owning workflow controls PR/CI side effects).

| Destination (canonical path) | Status | Evidence / remaining step |
|---|---|---|
| Agents `/omnigent/agents` | Fixed | `omnigent-inventory.tsx`: raw tables replaced by responsive `DataTable` (single owner, loading/error/empty state cards); toolbar wraps; filter `min-width: 0`; identity/action cells wrap with 2.75rem touch targets. Duplicate outer error/empty divs removed so `DataTable` is the single state owner with inline retry. Remaining: browser geometry run. |
| Policies `/omnigent/policies` | Fixed | Same inventory component + `omnigent-policy-detail` responsive rules (`pre` scrolls in place, detail/editor `max-width: 100%`). New browser case covers inspect/version/diff sections and action groups at 320/390px. Remaining: browser geometry run. |
| Providers & Secrets `/settings/providers-secrets` | Fixed | `ProviderProfilesManager.tsx`: form/fieldsets `min-w-0 max-w-full`; tier legend holds group name only; Duplicate/Remove moved to separate wrapping action area; `ol` unindented on mobile. CSS: single-column provider cards, actions full-width, fieldset `min-inline-size: 0`. Tier payload semantics pinned by `providerProfileTiers.test.ts` (order, 1-based default, no `default_model`/`default_effort` mirror, no `clientId` leak). New: production-component journey covered by `ProviderProfilesManager.mobile4559.test.tsx` (2/2 PASS: normalized tier mapping + action-area markup + canonical payload) and a 6th browser case mounting the real manager with long-identity/multi-tier fixtures. Remaining: browser geometry run. |
| Instance `/settings/instance` | Shared-pattern repair, verified by inspection | `GeneratedSettingsSection` grids already use `minmax(0, 1fr)` description + control columns; added `.settings-page` wrap rule (`code`/`dd`/`p` break instead of stretching the grid). No route-specific defect found. Remaining: browser geometry run. |
| Operations `/settings/operations` | Shared-pattern repair, verified by inspection | Metric grids are single-column on mobile; shard table already `overflow-x-auto`; same `.settings-page` wrap rule applies. No route-specific defect found. Remaining: browser geometry run. |
| Workflows `/workflows`, `/workflows/:id[/tab]` | Verified-unaffected (inspection) | #4559 CSS selectors are scoped to inventory/provider/dialog/settings-page; the only global touches are strict relaxations (`fieldset min-width: 0`, inputs `max-width: 100%`). Desktop composition, list-display modes, sidebars, and chat untouched. Remaining: run workflow/collection responsive regression suites + browser matrix in CI. |
| Create `/workflows/new` | Verified-unaffected (inspection) | Same scoping argument as Workflows. Remaining: CI regression. |
| Recurring `/schedules[/:id]` | Verified-unaffected (inspection) | Same scoping argument; `schedules-mobile-card-list` path untouched. Remaining: run remediationResponsive/collection-rails suites + CI. |
| Skills `/skills/*` | Verified-unaffected (inspection) | Same scoping argument. Remaining: run Skills responsive suite + CI. |
| Remediation `/remediations/*` | Verified-unaffected (inspection) | Same scoping argument. `remediations.test.tsx` 8/8 PASS after plural-query repair for the DataTable table+card dual render in jsdom (same pattern as the inventory suite). Remaining: run remediationResponsive suite + CI. |
| Artifacts `/artifacts/*`, `/observability/*` | Verified-unaffected (inspection) | Same scoping argument. Remaining: CI browser matrix. |

Explicitly not claimed: any platform check (Chromium/Firefox/WebKit) for this
revision — no CI run exists for the unpushed head, and the sandbox has no
Playwright browsers. The new `frontend-browser-webkit-targeted` CI leg exists
but never executed.
