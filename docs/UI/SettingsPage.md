# Settings Configuration Pages

**Related design documents:** [Harness, Provider Profile, and Backend Selection](./HarnessProfileBackendSelection.md), [SettingsSystem.md](../Security/SettingsSystem.md), [SecretsSystem.md](../Security/SecretsSystem.md), [ProviderProfiles.md](../Security/ProviderProfiles.md), [ProviderProfileCreation.md](./ProviderProfileCreation.md), [ProviderProfileModelEffortTierSettings.md](./ProviderProfileModelEffortTierSettings.md), [OAuthTerminal.md](../ManagedAgents/OAuthTerminal.md), [DashboardDesignSystem.md](./DashboardDesignSystem.md), [DashboardSPAArchitecture.md](./DashboardSPAArchitecture.md)

Owners: MoonMind Engineering

> [!NOTE]
> The Settings dropdown is the only cross-page navigation surface for configuration. Its **Configuration** group exposes three separate pages rather than one `/settings` page with a tab, radio, or segmented section switcher.
>
> Security, persistence, resolution, validation, and authorization are described in [SettingsSystem.md](../Security/SettingsSystem.md) and the related domain documents.

---

## 1. Purpose

MoonMind Settings is the human-facing configuration plane for instance, provider, secret, and operational configuration.

Settings is a family of sibling dashboard pages, not one large page with a second navigation layer:

> **Providers & Secrets**, **Instance**, and **Operations** are separate pages in the Settings dropdown's **Configuration** group. None of them repeats the three destinations as tabs, radio buttons, segmented controls, pills, cards, a sidebar, or another local page switcher.

This gives every configuration surface a durable URL, page title, loading boundary, authorization state, and browser-history entry, removes redundant navigation, and lets each page load only the data it owns.

Settings is also a **data-driven control plane**, not a hand-maintained collection of one-off forms. Ordinary instance settings come from backend descriptors. Provider Profiles, Managed Secrets, OAuth state, Source Control connections, and Operations are specialized backend resources or commands.

---

## 2. Scope

This document covers the configuration pages' placement, routes, shell, and failure boundaries; descriptor-driven settings and their save UX; the specialist surfaces; and permission, redaction, accessibility, and unsaved-change behavior.

The detailed Provider Profile create and edit form is described in [ProviderProfileCreation.md](./ProviderProfileCreation.md), and model/effort tier editing in [ProviderProfileModelEffortTierSettings.md](./ProviderProfileModelEffortTierSettings.md). Backend setting eligibility, resolution, persistence, secret storage, provider execution, operational command semantics, authorization, and server validation are described in the backend documents linked above.

---

## 3. Design principles

**One navigation owner.** The Settings dropdown owns navigation among configuration pages. A page-local control only changes state inside the current page, such as a Harness filter, a category filter, or an operation subview.

**Pathname owns page identity.** The pathname selects the configuration page. Query parameters may select filters or scope within a page but never select among the three pages.

**Backend-owned truth.** The backend owns which settings exist and are exposed, their types, validation, sensitivity, effective values, sources, and application semantics. The frontend renders descriptors and submits intent. Provider Profile creation follows the same rule: Harness, Provider, authentication, and creation capabilities determine backend-owned presets, and Harness choices keep canonical `runtime_id` values in API payloads, because different providers materialize credentials differently and the browser cannot assume one strategy.

**Data-driven rows.** The Instance page renders ordinary settings from catalog descriptors. The frontend branches on descriptor shape (`type`, `ui`, `constraints`, `options`, `read_only`, `sensitive`), not on individual setting keys, so adding a setting does not require frontend work.

**Specialist surfaces.** Provider Profiles, Managed Secrets, OAuth credentials, Source Control connections, and Operations controls are not generic setting rows. They use specialized managers, but their options, capabilities, readiness, and policy constraints still come from backend APIs.

**Progressive disclosure.** Forms lead with understandable choices and reveal low-level details on request. In Provider Profile creation, credential connection and max parallel runs stay visible, while raw SecretRefs, volume metadata, materialization details, secondary rate-limit controls, routing metadata, and launch shaping sit behind `Show advanced options`. Refinements stay within this single form rather than adding a wizard, route, or parallel credential flow.

**References over secrets.** Stored secret plaintext is never rendered, and generic settings do not accept raw credentials. Sensitive relationships use SecretRef pickers, Provider Profile secret-role bindings, or one-way Managed Secret creation and replacement.

**Independent failure.** A failure on one configuration page does not make the other two unavailable unless the shared dashboard shell itself is down.

---

## 4. Information architecture

```text
Settings dropdown
  Configuration
    Providers & Secrets
    Instance
    Operations
```

The three entries are sibling dashboard destinations, not sections of a parent Settings page. The dropdown renders the `Configuration` label once, lists the entries in this order as route links, marks one active entry with `aria-current="page"`, closes after selection, supports the dashboard's keyboard menu behavior, and renders the same group and order in the mobile drawer. The group label is omitted when none of its destinations is visible. Group membership is explicit metadata (not inferred from one destination key) so the label is never repeated or dropped.

While any Configuration page is active, the masthead trigger stays `Settings` with the Settings icon; the active page is communicated by the active menu item, URL, document title, and page header.

There is no Settings landing page, because the dropdown already is the configuration index.

---

## 5. Routes

### 5.1 Canonical routes

| Destination | Route | Destination key | Document title |
|---|---|---|---|
| Providers & Secrets | `/settings/providers-secrets` | `settings-providers-secrets` | `Providers & Secrets \| MoonMind` |
| Instance | `/settings/instance` | `settings-instance` | `Instance \| MoonMind` |
| Operations | `/settings/operations` | `settings-operations` | `Operations \| MoonMind` |

All three use the dashboard's utility-page classification. They may share one bundle with three route-owned components; the user-visible result is three distinct pages.

### 5.2 Default entry point

`/settings` resolves to Providers & Secrets with replacement history, keeping approved page-local filters. Protected operator admission is shared by all three Configuration pages; no account permission list selects a destination.

### 5.3 `section` query parameter

`section` carries no page identity in client routing. A `?section=` value on `/settings` or a canonical route is ignored and does not select, restore, or override the page; `/settings?section=operations` simply resolves through the default entry point. No redirect table maps old `?section=` values, because doing so would let stale internal links keep working and hide them.

The settings API separately uses `section=user-workspace` as catalog classification (section 8). That server-side parameter is unrelated to client routing.

### 5.4 Redirected paths

Standalone paths users may have bookmarked redirect with replacement history:

| Path | Target |
|---|---|
| `/secrets` | `/settings/providers-secrets` |
| `/workers` | `/settings/operations` |
| `/settings/user-workspace` | `/settings/instance` |
| an unknown older Settings alias | the default entry point |

Redirects keep page-relevant query parameters and drop ones that no longer mean anything on the target. A backend failure stays on its destination with readable errors.

### 5.5 Page-local URL state

Page-local filters may use query parameters, for example `/settings/providers-secrets?runtime=codex` or `/settings/operations?status=paused`. The Instance page has no URL-owned filters; stale `scope` and `q` parameters are dropped on redirect and navigation.

Sensitive values never enter paths, query parameters, browser history, page titles, or navigation telemetry.

---

## 6. Shared page shell

```text
ConfigurationPage
  PageHeader
  PageStatusSummary
  PageContent
  PageDiagnosticsOrAudit
```

There is no `SectionSwitcher`, tab list, segmented destination control, destination radio group, or duplicate Settings sidebar.

The header shows an optional `Configuration` overline, page title, short description, and deployment context. Page-scoped warnings explain degraded persistence, catalog loading, authorization, or operations; diagnostic and audit links appear when authorized.

| Page | Status summary emphasis |
|---|---|
| Providers & Secrets | only what blocks or degrades a launch: blocked profiles, broken bound secrets, a new-work default that can't launch, and permissions that disable an action; renders nothing when every check passes |
| Instance | overrides, pending application, locked settings, validation diagnostics |
| Operations | worker state, drain or pause status, queue and runtime health, pending commands |

Pages do not load all three datasets to build a global health summary on every route; a compact cross-configuration alert would use a dedicated aggregate endpoint.

### 6.1 Unsaved changes

Dropdown navigation is real route navigation. When the current page has a dirty draft, the page offers `Stay` and `Discard and leave`, does not mark the destination active until navigation succeeds, preserves the draft on cancel, and navigates immediately when the page is clean or saved. Leaving the page discards every draft on it.

Switching an editor in place (editing another Provider Profile, changing the Harness filter, selecting another Source Control connection or form, or editing another Managed Secret) asks only when the draft being replaced is dirty, offers `Stay` and `Discard and continue`, and discards only that draft; other editors on the page keep theirs.

### 6.2 Responsive behavior

Configuration pages follow the shared responsive design in [DashboardDesignSystem.md](./DashboardDesignSystem.md): content centered in the 72rem dashboard page measure, one modest phone gutter, restrained section padding, shrinkable single-column grids, and viewport-bounded overlays. Native `fieldset`/`legend` grouping is kept, but fieldsets opt out of the intrinsic `min-content` minimum so forms fit narrow viewports; inputs are `min-width: 0` and `max-width: 100%`; generated-settings rows pair a `minmax(0, 1fr)` description column with the control column; and long keys, SecretRefs, and digests wrap instead of stretching the grid. Enrollment drawers, confirmations, and tier dialogs are viewport-bounded with a single scroll container and wrapping footer actions.

On narrow viewports, saved Provider Profile, Managed Secret, and worker shard health records stack each label above its value with actions below; stacked rows clip overflow, so a profile's actions menu opens in place. This is CSS on the same table, not a second list, so records keep one owner. Tier rows keep the group name in the legend with Duplicate/Remove in a separate wrapping action area. `frontend/src/browser/mobileOverflow.browser.test.tsx` and `settingsMobileGutters.browser.test.tsx` check these pages in the dashboard shell for overflow, reachable actions, gutters, and the centered measure, including the tier edit journey and enrollment drawers.

---

## 7. Page responsibilities

### 7.1 Providers & Secrets

This page contains, in this order:

- a problem alert, rendered only when a launch check fails (section 6);
- Provider Profiles, the durable Harness/Provider connection and launch configuration, with model and effort tier policy editing inside the normal profile editor, OAuth-backed lifecycle entry points, and credential health, readiness, and Harness/Provider binding diagnostics (Backend provenance lives in execution details);
- **Defaults for new work**: a compact card for the Default Harness and Default Provider Profile instance settings, which Instance also lists in full;
- Managed Secrets, secret health, and SecretRef role bindings and validation; and
- **Source Control** connections for repository access, kept separate from Provider Profiles.

New work that selects neither a harness nor a profile uses the Default Provider Profile if set, otherwise the Default Harness (OpenCode, Codex, or Claude Code; stored `omnigent` is OpenCode on the Omnigent Backend). Launch choices take precedence. `Preferred for <Harness>` (stored as `is_default`) only marks the preferred profile within its harness. With no Default Provider Profile set, a launch uses the Default Harness's enabled profiles ordered by preference, then priority, then profile ID.

The Defaults card shows each setting's label, its control, and an ellipsis menu with View history and, while an override is in force, Reset to default. Source, activation state, affected systems, completion guidance, Details, and keys stay on Instance. A Save bar appears only when a value differs from the saved one, lists each change as old → new, and saves every changed key in one `PATCH` with expected versions. Validation issues and version conflicts show under the row they concern; success is a toast.

Each saved profile row shows identity, readiness, active use, capacity problems, and a broken bound secret by default. Credential source, materialization, OAuth volume and mount path, cooldown, and SecretRefs sit in that row's Details disclosure. Row actions sit in one ellipsis menu, the shared actions menu; pending or readiness-blocked actions stay listed under Unavailable with their reason, and Delete asks for confirmation. The create form opens from **Add profile** rather than staying open below the table; it shows directly only when no profile is listed. The problem alert lists a permission only when it disables an action.

Stored secrets is the primary Managed Secrets view; **Add secret**, Edit, and Rotate open a focused dialog that keeps errors with the draft and confirms discarding an entered value. Broken secrets load Usage automatically.

Action results on this page (saved, reset, enabled, deleted) are toasts. A problem that lasts until someone fixes it appears in the problem alert and on the affected row, never as a toast.

A page-local Harness filter offers **All harnesses** plus backend-supported Harness choices. It narrows the visible profiles, while the problem alert always checks the full collection; an alert link to a hidden row clears the filter first. The `runtime` URL parameter and `runtime_id` API values remain the inputs for Harness ownership; they do not select a Backend. [Harness, Provider Profile, and Backend Selection](./HarnessProfileBackendSelection.md) describes this vocabulary and authoring defaults.

#### 7.1.1 Source Control connections

The Source Control section lists the repository connections the operator can manage, including disabled ones, with each connection's name, credential kind (personal access token, GitHub App installation, or deployment credential), validated App account and installation, lifecycle, and explicitly assigned repositories. A connection with no assigned repositories grants no repository access, and the section says so.

- **Add token connection** asks for a name, a GitHub personal access token, and whether publishing is allowed. Publishing grants the read, write, branch-write, review-request, and issues operations on the connection and its publishing assignments; issue collaboration (labels, comments, issue reads) uses the default connection's `issues` operation. A connection recorded with the first four operations still shows as publishing, and turning publishing off removes every publishing operation except read. The server stores the token as a Managed Secret in the same transaction as the connection (`POST /api/v1/repository-connections/pat`). The connection ID is derived from the name and never suffixed; a clash is a conflict the operator resolves by renaming.
- **Connect GitHub App** asks for a name, the App name from its GitHub URL, the App ID, and explicit `owner/repo` names, then sends the operator through the existing enrollment begin/callback. The App identity and signing-key reference are derived server-side unless supplied on the advanced path. On return, the page completes the callback once and strips `installation_id` and `state` from the URL. The server verifies each repository against the same App, installation, and account before persisting it. An empty scope, mismatched installation, unavailable repository, or redirect never becomes wildcard authority.
- **Edit connection** renames, toggles publishing, or replaces a token with compare-and-set on the loaded policy revision. A conflict keeps the non-sensitive draft and reloads the connection. After an ambiguous update, the form checks the audit receipt for its exact request identity, because another editor's revision increase is not proof that this update committed.
- **Disable connection** stops new use without deleting records, secrets, or shared App installations, so it needs no confirmation: **Enable connection** (`POST /api/v1/repository-connections/{id}/enable`) allows new use again with the same credential and assignments.
- **Assign repository** verifies `owner/repo` through the connection's own credential before saving the provider repository ID. If GitHub is unavailable, existing assignments and the draft are unchanged.
- **Test connection** (`POST /api/v1/settings/github/token-probe` with `connectionId`) reads one repository using only the selected connection. It checks only repositories the connection admits (a token connection's assignments or a GitHub App's permitted repositories, compared case-insensitively); anything else is reported as not checked before any credential is read, because a token can usually see more repositories than its connection admits. Probes stay on the connection's deployment-trusted API host. Results distinguish verified, denied, not found (GitHub hides unshared private repositories), and unavailable, and tell an empty repository from a missing branch. Outages and throttling are unavailable rather than denied; a throttle stops remaining checks and shows a retry delay from GitHub's retry signal or a bounded fallback. The result shows the remote default branch, separates GitHub-reported permission metadata from tested reads, and labels write access as untested because the probe never writes. A request without `connectionId` is rejected rather than falling back to an ambient token.

Each create, save, disable, enable, and assignment carries a stable `requestId`, reused on retry after an uncertain outcome. After a lost acknowledgment the page reads the connection back and shows the committed record before offering another create, instead of blindly resubmitting a POST. A retried disable or enable that a later change already reversed is rejected as superseded rather than answered with the other outcome. Test results are tied to the selected connection, its policy and credential revisions, its permitted repositories and assignments, and the test inputs: responses for a deselected connection or an earlier request are discarded, and changed inputs mark a displayed result as stale. Tokens live only in the password field until submission and are cleared after transfer, cancellation, or session loss; they never appear in URLs, query caches, or responses, and responses never include SecretRefs. Active token, App, edit, and assignment drafts participate in the Stay/Discard guard.

#### 7.1.2 Provider Profile creation

Creation uses one standard form with progressive disclosure, described in [ProviderProfileCreation.md](./ProviderProfileCreation.md). The visible decisions are: Profile ID (auto-suggested, reviewed before creation), Harness and Provider (backend-owned choices, Provider scoped to Harness), optional account label, authentication method and connection action, model and effort tier policy, max parallel runs, and an optional `Preferred for <Harness>` intent when readiness permits.

- Selectors show friendly labels but submit canonical IDs. Choice discovery reuses creation-capability authority, so it works before any profile or launch-ready host exists. The Settings Harness filter can seed a permitted creation Harness but does not redefine saved ownership.
- The Harness control submits a harness `runtime_id` (`codex_cli`, `claude_code`, `opencode`), never the Backend `omnigent`.
- Profile ID suggestions follow backend constraints and stop updating once the user edits them; refreshes and account-label changes do not overwrite a user-authored ID. Format or uniqueness conflicts keep the draft and require reviewed resubmission rather than an automatically suffixed retry.
- `Show advanced options` (an unchecked native checkbox) reveals credential source and materialization metadata, structured SecretRef bindings, volume reference and mount metadata, cooldown after provider 429 responses, rate-limit policy, command behavior, routing tags and priority, and launch-shaping diagnostics. OAuth and API-key actions remain visible.
- `clear_env_keys` is backend-owned launch-security metadata, displayed read-only rather than as a freeform preference.
- Creation uses backend presets or omits untouched advanced values instead of submitting frontend defaults. A profile missing required credentials is saved disabled; successful guided setup may enable it when policy permits.

The primary button names the real operation: `Create and connect` when creation continues into guided enrollment, `Create profile` when no setup continuation is needed, and `Save disabled profile` only for a supported manual outcome; editing uses `Update provider profile`. Missing prerequisites disable the action with an explanation, and an in-flight save cannot be resubmitted.

Persistence, connection, activation, and launch readiness are separate outcomes. If creation succeeds but enrollment fails or is canceled, the page identifies the saved profile and offers its setup recovery action instead of prompting another creation.

#### 7.1.3 Provider Profile editing

On edit, advanced options start expanded when the profile has non-default, unknown, incompatible, or invalid advanced values; otherwise they may start collapsed with an effective-policy summary. Collapsing preserves every draft value, and a validation error on a hidden field expands the region and focuses the control.

The collapsed summary distinguishes a confirmed recommendation, known custom overrides, and unavailable or invalid comparison state. An authentication capability alone is not evidence that every advanced field is recommended, and generated credential-volume and isolation metadata do not count as user overrides. Connection and readiness are shown independently: `Using recommended settings` does not mean connected or launch ready, and custom settings are not automatically unhealthy. Summaries contain bounded safe labels, never credential references, host paths, command JSON, or secret values. Section 5.3 of [ProviderProfileCreation.md](./ProviderProfileCreation.md) covers comparison and uncertainty semantics.

### 7.2 Instance

This page holds descriptor-driven settings for the single-operator instance: workspace defaults, feature flags, non-secret integration defaults, authorized policy controls, and SecretRef bindings not owned by a Provider Profile. It reads and writes the one instance configuration and sends no scope, because MoonMind has one operator per instance. The guarded upgrade reconciles retained per-user overrides; this page never edits them.

This is the generated-settings surface: adding an eligible ordinary setting needs backend catalog metadata and validation, not a new React row.

### 7.3 Operations

This page holds explicit administrative commands and statusful controls for worker pause and resume, drain and quiesce, queue and runtime health, maintenance mode, authorized deployment or runtime updates, recent operational audit actions, and bounded diagnostic switches.

Each command card shows current state, expected impact, permitted actions, disabled reasons, confirmation and reason requirements, pending transitions, last actor and time, failure state, and recovery or resume action. Commands are not modeled as boolean preferences.

The MoonMind update card submits to the standalone deployment controller ([Docker Compose Deployment Update System](../Steps/DockerComposeUpdateSystem.md)). Each update intent carries one request identity, reused when resubmitting after an unanswered or server-error response so the controller reattaches instead of starting a second operation. History shows each operation's identity, requested and installed image, status (`UNKNOWN` when unreadable), original error, attempt log, and verification checks, with Retry for a failed operation. The card keeps polling with bounded reads while an operation is queued or running, so a reload or API restart reconnects to the same operation. When no controller is installed or it does not answer, the card says so, disables dashboard submission, and points to `./tools/update-moonmind.sh`. Updates recorded by the earlier workflow-based updater appear as read-only history with run and log links and no rollback; their shared disclaimer is shown once. History lists the latest few operations and any still running, with older entries behind a disclosure, and timestamps render in local time with the ISO value in the tooltip. The command catalog omits commands that already have their own form and keeps unavailable commands behind a disclosure.

The page title `Operations` is distinct from any broader dropdown group with the same label; the group classifies destinations, while this page owns configuration and administration controls.

---

## 8. Data loading boundaries

| Page | Primary data |
|---|---|
| Providers & Secrets | Provider Profiles, Managed Secrets, Source Control connections, OAuth state, readiness, creation capabilities and presets, and the shared catalog for new-work defaults |
| Instance | catalog descriptors, effective values, overrides, diagnostics, audit metadata |
| Operations | worker state, queue and runtime health, operation capabilities, command history |

Each route loads its own first-screen data, with regional loading states and URL-owned filters. Expensive diagnostics, audit timelines, and graphs load on demand.

Provider creation queries carry their runtime/provider/authentication identity to reject stale responses. Failed discovery preserves unrelated content and offers no unsupported manual-entry fallback.

Settings endpoints (`api_service/api/routers/settings.py`) serve Instance and the curated Defaults card:

```http
GET    /api/v1/settings/catalog?section=user-workspace
GET    /api/v1/settings/effective
GET    /api/v1/settings/diagnostics
GET    /api/v1/settings/audit?key=<key>
POST   /api/v1/settings/validate
POST   /api/v1/settings/preview
PATCH  /api/v1/settings
DELETE /api/v1/settings/{key}
```

---

## 9. Descriptor-driven Instance rows

A descriptor carries enough metadata for the frontend to render and explain a setting without key-specific logic. The backend owns the shape; the fields the UI depends on are:

```yaml
SettingDescriptor:
  key, title, description, category, section, order
  type: boolean | string | integer | number | enum | string_list | object | secret_ref
  ui: toggle | input | number | select | tag_editor | key_value | secret_ref_picker | provider_profile_picker | readonly
  default_value, effective_value, override_value, source, source_explanation
  options, constraints, sensitive, secret_role, read_only, read_only_reason
  apply_mode, activation_state, active, pending_value, affected_process_or_worker, completion_guidance
  requires_reload, requires_worker_restart, requires_process_restart, applies_to
  depends_on, audit, value_version, diagnostics
```

Several fields drive specific row behavior: `active` and `pending_value` separate an applied value from an accepted-but-pending one; `affected_process_or_worker` and `completion_guidance` say what a pending change is waiting on and what completes it; the `requires_*` flags produce application warnings before and after save; `order` makes row order deterministic; `secret_role` enables role-aware SecretRef controls; `depends_on` produces dependency warnings; and `audit` decides whether an audit link is offered. The backend can add fields, but removing one the UI renders breaks these behaviors. Values whose apply mode is `next_request`, `next_workflow`, or `next_launch` are active from the moment they are saved; only `worker_reload`, `process_restart`, and `manual_operation` values report a pending state.

Controls follow descriptors: toggles for booleans, option selects for enums, bounded number inputs, tag editors for lists, structured object editors, and role-aware SecretRef controls. Sensitive values use specialist editors; read-only or unsupported settings show a value and reason. Clearing a number means `null` only if it has no default; otherwise it is invalid, never silently `0` or `null`. The profile picker lists saved profiles and `Not set`, retaining a missing saved ID as not found.

Each Instance row shows its title, description, control, override and activation state, validation, diagnostics, and affected systems. Details contains the key, source, apply mode, and authorized audit link; reset appears when allowed. Filtering by text, category, modification, read-only state, diagnostics, source, activation, or secrets does not confer authorization.

Before save, the Instance page shows changed keys, old and proposed values, validation, expected versions, affected systems, application requirements, dependency warnings, and redaction behavior; the backend preview is authoritative. Curated cards on other pages, such as Defaults for new work, show old and proposed values in their Save bar and validation under each row, and save through the same `PATCH`. Drafts are keyed by setting key and cleared after a successful save, explicit discard, confirmed route departure, or incompatible catalog-version change. Drafts are not persisted to local storage, since they can contain sensitive metadata.

### 9.1 Version conflicts

Saves submit `expected_versions` with the changed keys, so `PATCH` rejects the write with `version_conflict` when another change landed after the page loaded. This is a normal concurrency outcome, and the page handles it by stopping the save without partial local writes, reloading the affected descriptors, naming the affected keys, showing the latest value beside the proposed value, and requiring explicit resubmission with refreshed versions. It never auto-retries with the stale version or silently overwrites the concurrent change. Unaffected keys keep their drafts.

---

## 10. Admission, availability and failure states

Shared server-side operator admission protects Configuration pages and APIs. The dashboard carries no human roles or settings permission list. Destination metadata controls navigation visibility, while each page fetches its instance resources and reports denied access or backend failure in place. Navigation visibility is not authorization.

| Backend state | UI behavior |
|---|---|
| deployment-locked or read-only setting | Read-only row with reason |
| sensitive audit value | Redacted value; safe SecretRef metadata remains readable |
| operation unavailable | Current state visible, action disabled with reason |
| denied request | Structured error, no automatic destructive retry |
| backend unavailable or missing configuration | Region-specific error; unrelated content remains usable |

Loading placeholders do not imply values are default, unset, permitted, or healthy. Empty states explain the next authorized action. Failures identify the affected page or region and keep unrelated content.

---

## 11. Secrets, validation, and explainability

Generic settings never accept raw secret values. Managed Secret creation and replacement accept plaintext only for one-way submission; afterwards the field is cleared and only metadata and validation state are shown. The UI may display an authorized SecretRef such as `db://github-token` or `env://GITHUB_TOKEN`, never its plaintext target. Provider Profile advanced fields use role-aware SecretRef controls rather than raw JSON, and OAuth volume identifiers and paths come from dedicated backend flows.

Client validation may use descriptor metadata for required values, bounds, option membership, length, simple patterns, SecretRef shape, and obvious type errors; backend validation is authoritative.

Instance shows source and application metadata from the backend. Pending accepted values stay visible until active.

---

## 12. Accessibility

The configuration pages provide keyboard access to the dropdown, route links, page-local controls, and dialogs; visible focus and Escape-to-close; the same destination order on desktop and mobile; one active route exposed with route semantics; focus moved to the new page heading where appropriate; associated labels, descriptions, and errors; no reliance on color alone; confirmation and focus restoration for destructive operations; and readable long keys, SecretRefs, and profile identifiers.

The destination links are links, not `role="tab"` or radio controls, because they navigate to separate documents. The Provider Profile `Show advanced options` control is a native checkbox tied to an expandable form region; it is local form state, not navigation.

---

## 13. Extending Settings

Ordinary instance settings use backend descriptors (section 9), persistence, and catalog/save/reset coverage. Curated cards reuse those descriptors and APIs; new control types need frontend work.

A distinct user goal with independent data loading may warrant another route; a small settings group belongs on an existing page.

Patterns the design avoids, and why:

- page-local tabs, radio cards, pills, segmented controls, sidebars, or cards repeating the three destinations, and `?section=` as page identity — they duplicate the dropdown's navigation;
- eager loading of every configuration dataset — it breaks independent failure and page-scoped loading;
- frontend copies of defaults, validation, or authorization — the backend owns these, including on curated cards;
- a second provider-creation wizard or duplicate Harness/Provider catalog — one form and one choice authority;
- policy summaries inferred only from authentication-method availability, unconditional `enabled: true` for credential-required profiles, or a global fallback credential source — each misreports readiness;
- plaintext credential inputs in generic settings, raw SecretRef JSON as the primary credential UX, freeform `clear_env_keys`, and silent fallback for broken SecretRefs — they weaken secret handling; and
- boolean-preference treatment for operational commands — commands have state, impact, and confirmation.

---

## 14. Ownership structure

**Destination registry.** Section 5.1 lists the three destinations. The client registry (`frontend/src/lib/dashboardRoutes.ts`) and server registry agree on entries, groups and order; the runtime skew check compares keys, paths and capability keys.

**Route and state ownership.** Each route owns its component, loaders and local state: Harness filtering on Providers & Secrets, worker commands on Operations. Bundle sharing does not mount sibling managers or introduce internal page switching.

**Component ownership.** Providers & Secrets owns profile/secret editors and the compact Defaults card. Instance owns full generated rows. Both reuse catalog helpers, the profile picker and audit renderer. Operations owns commands.

### 14.1 Tests

| File | Covers |
|---|---|
| `frontend/src/entrypoints/settingsRedesignConformance.test.tsx` | route resolution and titles, `/settings`, `?section=`, `/secrets`, `/workers`, and alias redirects, dirty-draft departure after successful and failed saves, and the structural guards in sections 12 and 14 |
| `frontend/src/components/settings/providerProfileRedesignConformance.test.tsx` | Provider Profile creation matrix, progressive disclosure, tier integration, existing-profile compatibility |
| `tests/unit/api/routers/test_dashboard_destination_registry_agreement.py` | agreement between the server destination registry and `frontend/src/lib/dashboardRoutes.ts` |
| `frontend/src/entrypoints/settingsRoutes.test.tsx`, `settingsDraftNavigation.test.tsx`, `settingsInstance.test.tsx`, `dashboard.test.tsx` | per-page routing, draft navigation, Instance rows, and shell behavior |

Focused commands:

```bash
npm run ui:test:settings-redesign
./tools/test_unit.sh --python-only tests/unit/api/routers/test_dashboard_destination_registry_agreement.py
```

These run in CI through the frontend and unit suites. Component tests do not replace backend contract tests or live credential verification.
