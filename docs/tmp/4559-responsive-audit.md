# MoonMind#4559 responsive route audit (implementation evidence, not a registry)

Revision audited: working tree on top of `d293430` (see commit). Registry source:
`frontend/src/lib/dashboardRoutes.ts`. Status values: fixed | verified-unaffected |
remaining-defect (with owner). Geometry proof: `mobileOverflow.browser.test.tsx`
(CI: Chromium/Firefox + targeted WebKit leg); structure proof: `DataTable.test.tsx`,
`providerMobileLayout.test.tsx`.

## Fixed in this change

| Route | Component / seam | Defect repaired |
| --- | --- | --- |
| `/omnigent/agents` | `omnigent-inventory.tsx` + `dashboard.css` | Inventory keeps a compact desktop table; at ≤720px rows become stacked records with caption-above-value cells, wrapped toolbar, 0.75rem gutter, wrapped policy JSON/diffs. `data-label` attributes added to both inventory tables. |
| `/omnigent/policies` | same inventory component | Same repair (shared component); editor/version/history/diff sections get the shrinkable-owner + fieldset-minimum fixes and wrapped `pre` handling. |
| `/settings/providers-secrets` (saved profiles) | `ProviderProfilesManager.tsx` table + `dashboard.css` | Mobile cells stack label-above-value (no wide label column); actions own a full-width wrapping area with ≥10rem flexible buttons at 44px+ touch height. |
| `/settings/providers-secrets` (create/edit form) | same manager form | Fieldset intrinsic-minimum reset + `min-width: 0` chain on form/grid/labels; controls bounded to `max-width: 100%`; 16px mobile form-text baseline. |
| `/settings/providers-secrets` (model/effort tiers) | tier editor | Legend names the group only; Duplicate tier / Remove tier moved to a separate wrapping `.tier-card__actions` area (visible wording shortened to `Duplicate tier`, accessible names preserved); flattened nesting (unstyled inner fieldset, list reset). |
| `/settings/providers-secrets` (overlays) | enrollment drawers, confirmations, tier-remove dialog | Viewport-bounded panels (`max-height: calc(100dvh - 2rem)` + internal scroll) via the `.provider-profiles [role="dialog"]` contract; focus/keyboard ownership unchanged. |
| `/settings/instance`, `/settings/operations` | `GeneratedSettingsSection`, `OperationsSettingsSection`, shared settings CSS | Modest phone gutter + restrained card padding; shrinkable single-column grid children; fieldset-minimum reset for operations fieldsets. Grids already use `minmax(0, …)` desktop tracks. |
| Shared `DataTable` seam | `DataTable.tsx` + `dashboard.css` | Loading/error/empty states mirrored into the mobile card fallback (exactly one representation exposed per breakpoint via CSS); card rows stack label-above-value with wrapping values. Directly benefits the Remediation list consumer. |

## Verified-unaffected (concrete evidence, not assumption)

| Route / surface | Evidence |
| --- | --- |
| Workflows list/create/detail/chat, Recurring, Skills, Artifacts/observability | No shared selector in this change targets workflow composition: changed selectors are scoped to `.omnigent-inventory*`, `.provider-profiles*`, `.provider-profile*`, `.provider-tier*`, `.tier-card__actions`, `.settings-page`, and `.data-table-card*` (mobile-only). `.dashboard-root fieldset` only removes the `min-content` floor (strictly prevents overflow). Existing suites `remediationResponsive`, `workflowListResponsiveToolbar`, `skillsTableParity`, `collectionSidebarRail` run in CI on this revision. |
| Operations per-worker health table | Genuinely tabular 5-column technical content; already in a bounded `overflow-x-auto` local scroll region that does not expand the page. Loading/error text states render inline. |
| `CollectionWorkspaceLayout` contract | Unchanged; no doc update needed. |
| Desktop workflow experience | No desktop composition, sidebar, list-display mode, or action-semantics change in this diff. |

## Remaining defects / explicit non-coverage

- Physical-device confirmation (iOS Safari virtual keyboard, real 400% browser zoom): not run here; WebKit automation in CI is layout coverage, not a physical-device claim. No human-gate added — automation-completable.
- Browser geometry evidence for this exact revision is pending CI (`frontend-browser` job: Chromium/Firefox/WebKit) because Playwright binaries are not installable in this sandbox (proxy-blocked CDN). Unit, typecheck, and lint evidence below were collected locally.
- Zoom/reflow at 200% text enlargement and short-height landscape overlays are covered by harness viewports in CI; before/after captures belong to the PR step, which this step does not perform.
