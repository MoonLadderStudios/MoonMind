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

This document covers placement of configuration pages in the Settings dropdown, routes and redirects, the shared page shell, page-level loading and failure boundaries, descriptor-driven settings rendering, edit/preview/save/reset UX, placement of Provider Profile, Managed Secret, OAuth, Source Control, and Operations surfaces, and permission, redaction, accessibility, and unsaved-change behavior.

The detailed Provider Profile create and edit form is described in [ProviderProfileCreation.md](./ProviderProfileCreation.md), and model/effort tier editing in [ProviderProfileModelEffortTierSettings.md](./ProviderProfileModelEffortTierSettings.md). Backend setting eligibility, resolution, persistence, secret storage, provider execution, operational command semantics, authorization, and server validation are described in the backend documents linked above.

---

## 3. Design principles

**One navigation owner.** The Settings dropdown owns navigation among configuration pages. A page-local control only changes state inside the current page, such as a Harness filter, a category filter, or an operation subview.

**Pathname owns page identity.** The pathname selects the configuration page. Query parameters may select filters or scope within a page but never select among the three pages.

**Backend-owned truth.** The backend owns which settings exist and are exposed, their types and scopes, validation, sensitivity, effective values, sources, and application semantics. The frontend renders descriptors and submits intent. Provider Profile creation follows the same rule: Harness, Provider, authentication, and creation capabilities determine backend-owned presets, and Harness choices keep canonical `runtime_id` values in API payloads, because different providers materialize credentials differently and the browser cannot assume one strategy.

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

`/settings` is an entry point, not a destination. It resolves, with replacement history so it is not a back-button trap, to the first destination the backend authorizes in Configuration order: Providers & Secrets, then Instance, then Operations. It does not assume Providers & Secrets is available.

When no destination is accessible, `/settings` renders the Configuration unavailable state (section 10) instead of redirecting to a forbidden page. This fallback applies only to `/settings`; a direct request for a specific unauthorized destination shows that destination's own unavailable or forbidden state and is never rerouted to a sibling.

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

Redirects keep page-relevant query parameters, drop ones that no longer mean anything on the target, and never land on a destination the user cannot see; an unavailable mapped target falls back to the default entry point.

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

The header has an optional `Settings` or `Configuration` overline, the page title (`Providers & Secrets`, `Instance`, or `Operations`), a short description, optional deployment context, a page-scoped warning when persistence, catalog loading, authorization, or operational state is degraded, and optional authorized diagnostic or audit links.

| Page | Status summary emphasis |
|---|---|
| Providers & Secrets | launch readiness, profile validity, secret and OAuth health, blocked profiles |
| Instance | overrides, pending application, locked settings, validation diagnostics |
| Operations | worker state, drain or pause status, queue and runtime health, pending commands |

Pages do not load all three datasets to build a global health summary on every route; a compact cross-configuration alert would use a dedicated aggregate endpoint.

### 6.1 Unsaved changes

Dropdown navigation is real route navigation. When the current page has a dirty draft, the page offers `Stay` and `Discard and leave`, does not mark the destination active until navigation succeeds, preserves the draft on cancel, and navigates immediately when the page is clean or saved.

### 6.2 Responsive behavior

Configuration pages follow the shared responsive design in [DashboardDesignSystem.md](./DashboardDesignSystem.md): one modest phone gutter, restrained section padding, shrinkable single-column grids, and viewport-bounded overlays. Native `fieldset`/`legend` grouping is kept, but fieldsets opt out of the intrinsic `min-content` minimum so forms fit narrow viewports; inputs are `min-width: 0` and `max-width: 100%`; generated-settings rows pair a `minmax(0, 1fr)` description column with the control column; and long keys, SecretRefs, and digests wrap instead of stretching the grid. Enrollment drawers, confirmations, and tier dialogs are viewport-bounded with a single scroll container and wrapping footer actions.

On narrow viewports, saved Provider Profile records stack each label above its value with actions in a full-width wrapping area below. This is CSS on the same table, not a second list, so records keep one data and action owner. Tier rows keep the group name in the legend with Duplicate/Remove in a separate wrapping action area. A browser test under `frontend/src/browser/` checks these pages for mobile overflow.

---

## 7. Page responsibilities

### 7.1 Providers & Secrets

This page contains:

- Provider Profiles, the durable Harness/Provider connection and launch configuration, with model and effort tier policy editing inside the normal profile editor;
- Managed Secrets, secret health, and SecretRef role bindings and validation;
- OAuth-backed profile lifecycle entry points;
- provider credential health and readiness, and Harness/Provider binding diagnostics (Backend provenance lives in execution details); and
- **Source Control** connections for repository access, kept separate from Provider Profiles.

The page explains that profiles contain references and launch metadata, Managed Secrets contain encrypted values or external references, OAuth volumes contain runtime-specific credential state, and readiness combines profile validity with secret or OAuth resolvability.

A page-local Harness filter offers **All harnesses** plus backend-supported Harness choices. It narrows the visible profiles, while readiness counts use the full collection unless labeled as filtered. The `runtime` URL parameter and `runtime_id` API values remain the inputs for Harness ownership; they do not select a Backend. [Harness, Provider Profile, and Backend Selection](./HarnessProfileBackendSelection.md) describes this vocabulary and authoring defaults.

#### 7.1.1 Source Control connections

The Source Control section lists the repository connections the operator can manage, including disabled ones, with each connection's name, credential kind (personal access token, GitHub App installation, or deployment credential), validated App account and installation, lifecycle, and explicitly assigned repositories. A connection with no assigned repositories grants no repository access, and the section says so.

- **Add token connection** asks for a name, a GitHub personal access token, and whether publishing is allowed. Publishing grants the read, write, branch-write, review-request, and issues operations on the connection and its publishing assignments; issue collaboration (labels, comments, issue reads) uses the default connection's `issues` operation. A connection recorded with the first four operations still shows as publishing, and turning publishing off removes every publishing operation except read. The server stores the token as a Managed Secret in the same transaction as the connection (`POST /api/v1/repository-connections/pat`). The connection ID is derived from the name and never suffixed; a clash is a conflict the operator resolves by renaming.
- **Connect GitHub App** asks for a name, the App name from its GitHub URL, the App ID, and explicit `owner/repo` names, then sends the operator through the existing enrollment begin/callback. The App identity and signing-key reference are derived server-side unless supplied on the advanced path. On return, the page completes the callback once and strips `installation_id` and `state` from the URL. The server verifies each repository against the same App, installation, and account before persisting it. An empty scope, mismatched installation, unavailable repository, or redirect never becomes wildcard authority.
- **Edit connection** renames, toggles publishing, or replaces a token with compare-and-set on the loaded policy revision. A conflict keeps the non-sensitive draft and reloads the connection. After an ambiguous update, the form checks the audit receipt for its exact request identity, because another editor's revision increase is not proof that this update committed.
- **Disable connection** stops new use without deleting records, secrets, or shared App installations.
- **Assign repository** verifies `owner/repo` through the connection's own credential before saving the provider repository ID. If GitHub is unavailable, existing assignments and the draft are unchanged.
- **Test connection** (`POST /api/v1/settings/github/token-probe` with `connectionId`) reads one repository using only the selected connection. It checks only repositories the connection admits (a token connection's assignments or a GitHub App's permitted repositories, compared case-insensitively); anything else is reported as not checked before any credential is read, because a token can usually see more repositories than its connection admits. Probes stay on the connection's deployment-trusted API host. Results distinguish verified, denied, not found (GitHub hides unshared private repositories), and unavailable, and tell an empty repository from a missing branch. Outages and throttling are unavailable rather than denied; a throttle stops remaining checks and shows a retry delay from GitHub's retry signal or a bounded fallback. The result shows the remote default branch, separates GitHub-reported permission metadata from tested reads, and labels write access as untested because the probe never writes. A request without `connectionId` is rejected rather than falling back to an ambient token.

Each create, save, disable, and assignment carries a stable `requestId`, reused on retry after an uncertain outcome. After a lost acknowledgment the page reads the connection back and shows the committed record before offering another create, instead of blindly resubmitting a POST. Test results are tied to the selected connection, its policy and credential revisions, its permitted repositories and assignments, and the test inputs: responses for a deselected connection or an earlier request are discarded, and changed inputs mark a displayed result as stale. Tokens live only in the password field until submission and are cleared after transfer, cancellation, or session loss; they never appear in URLs, query caches, or responses, and responses never include SecretRefs. Active token, App, edit, and assignment drafts participate in the Stay/Discard guard.

#### 7.1.2 Provider Profile creation

Creation uses one standard form with progressive disclosure, described in [ProviderProfileCreation.md](./ProviderProfileCreation.md). The visible decisions are: Profile ID (auto-suggested, reviewed before creation), Harness and Provider (backend-owned choices, Provider scoped to Harness), optional account label, authentication method and connection action, model and effort tier policy, max parallel runs, and an optional `Use as Harness default` intent when readiness permits.

- Selectors show friendly labels but submit canonical IDs. Choice discovery reuses creation-capability authority, so it works before any profile or launch-ready host exists. The Settings Harness filter can seed a permitted creation Harness but does not redefine saved ownership.
- The Harness control submits `runtime_id` such as `codex_cli`, `claude_code`, or `opencode`. Omnigent is a Backend, so it never becomes `runtime_id=omnigent`, and choosing a profile default does not change the deployment Backend default.
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

This page holds descriptor-driven settings for the single-operator instance: workspace defaults, feature flags, non-secret integration defaults, authorized policy controls, and SecretRef bindings not owned by a Provider Profile. It reads and writes the backend `workspace` scope and has no user-versus-workspace switch, because MoonMind has one operator per instance. Retained user-scope overrides are handled by the backend and are not edited from this page.

This is the generated-settings surface: adding an eligible ordinary setting needs backend catalog metadata and validation, not a new React row.

### 7.3 Operations

This page holds explicit administrative commands and statusful controls for worker pause and resume, drain and quiesce, queue and runtime health, maintenance mode, authorized deployment or runtime updates, recent operational audit actions, and bounded diagnostic switches.

Each command card shows current state, expected impact, permitted actions, disabled reasons, confirmation and reason requirements, pending transitions, last actor and time, failure state, and recovery or resume action. Commands are not modeled as boolean preferences.

The MoonMind update card submits to the standalone deployment controller ([Docker Compose Deployment Update System](../Steps/DockerComposeUpdateSystem.md)). Each update intent carries one request identity, reused when resubmitting after an unanswered or server-error response so the controller reattaches instead of starting a second operation. History shows each operation's identity, requested and installed image, status (`UNKNOWN` when unreadable), original error, attempt log, and verification checks, with Retry for a failed operation. The card keeps polling with bounded reads while an operation is queued or running, so a reload or API restart reconnects to the same operation. When no controller is installed or it does not answer, the card says so, disables dashboard submission, and points to `./tools/update-moonmind.sh`. Updates recorded by the earlier workflow-based updater appear as read-only history with run and log links and no rollback.

The page title `Operations` is distinct from any broader dropdown group with the same label; the group classifies destinations, while this page owns configuration and administration controls.

---

## 8. Data loading boundaries

| Page | Primary data |
|---|---|
| Providers & Secrets | Provider Profiles, Managed Secret metadata, Source Control connections, OAuth state, readiness diagnostics, permitted Harness/Provider choices, profile creation capabilities and presets |
| Instance | catalog descriptors, effective values, overrides, diagnostics, audit metadata |
| Operations | worker state, queue and runtime health, operation capabilities, command history |

On route load, the page is resolved from the pathname and loads only what its first screen needs. Sibling-page collections are not fetched; expensive diagnostics, audit timelines, and graphs are deferred; route-level and region-level loading states are shown; and page-local filters survive browser navigation.

Provider creation queries are keyed to their runtime/provider/authentication identity, so a stale response cannot restore incompatible selections or authorize a submission. A failed choice query preserves unrelated profile and Managed Secret content and does not invent a manual-entry fallback the backend has not offered.

Generated-settings endpoints (`api_service/api/routers/settings.py`), used by Instance with `scope=workspace`:

```http
GET    /api/v1/settings/catalog?section=user-workspace&scope=workspace
GET    /api/v1/settings/effective?scope=workspace
GET    /api/v1/settings/diagnostics?scope=workspace
GET    /api/v1/settings/audit?key=<key>
POST   /api/v1/settings/validate
POST   /api/v1/settings/preview
PATCH  /api/v1/settings/{scope}
DELETE /api/v1/settings/{scope}/{key}
```

---

## 9. Descriptor-driven Instance rows

A descriptor carries enough metadata for the frontend to render and explain a setting without key-specific logic. The backend owns the shape; the fields the UI depends on are:

```yaml
SettingDescriptor:
  key, title, description, category, section, order
  type: boolean | string | integer | number | enum | string_list | object | secret_ref
  ui: toggle | input | number | select | tag_editor | key_value | secret_ref_picker | provider_profile_picker | readonly
  scopes, default_value, effective_value, override_value, source, source_explanation
  options, constraints, sensitive, secret_role, read_only, read_only_reason
  apply_mode, activation_state, active, pending_value, affected_process_or_worker, completion_guidance
  requires_reload, requires_worker_restart, requires_process_restart, applies_to
  depends_on, audit, value_version, diagnostics
```

Several fields drive specific row behavior: `active` and `pending_value` separate an applied value from an accepted-but-pending one; `affected_process_or_worker` and `completion_guidance` say what a pending change is waiting on and what completes it; the `requires_*` flags produce application warnings before and after save; `order` makes row order deterministic; `secret_role` enables role-aware SecretRef controls; `depends_on` produces dependency warnings; and `audit` decides whether an audit link is offered. The backend can add fields, but removing one the UI renders breaks these behaviors.

Controls follow descriptor shape: toggles for booleans, selects from backend options for enums, constrained number inputs, text inputs (unless sensitivity requires a specialist control), tag editors for string lists, a safe structured editor for objects, a SecretRef picker, a Provider Profile selector backed by backend data, and a read-only value with a reason for read-only or unsupported types.

Each row exposes title, description, control or value, stable key, scope, source, inherited or override state, active or pending state, validation, diagnostics, reset when allowed, application semantics, affected systems, and an audit link where authorized. Filtering covers text search, category, modified-only, read-only, diagnostics, source, pending application, and secret-related filters; filtering is never the authorization boundary.

Before save, the page shows changed keys, old and proposed values, validation, target scope, expected versions, affected systems, application requirements, dependency warnings, and redaction behavior; the backend preview is authoritative. Drafts are keyed by setting key and scope and cleared after a successful save, explicit discard, confirmed route departure, or incompatible catalog-version change. Drafts are not persisted to local storage, since they can contain sensitive metadata.

### 9.1 Version conflicts

Saves submit `expected_versions` with the changed keys, so `PATCH` rejects the write with `version_conflict` when another change landed after the page loaded. This is a normal concurrency outcome, and the page handles it by stopping the save without partial local writes, reloading the affected descriptors, naming the affected keys, showing the latest value beside the proposed value, and requiring explicit resubmission with refreshed versions. It never auto-retries with the stale version or silently overwrites the concurrent change. Unaffected keys keep their drafts.

---

## 10. Permissions and failure states

The backend controls authorization; the UI reflects it without treating client checks as a security boundary.

Each destination has an independent state: `shown` for accessible content, `unavailable` when a visible explanation is wanted, or `hidden` when it should not be advertised. The Configuration label appears when at least one child is shown or intentionally unavailable. A direct unauthorized route shows an unavailable or forbidden state rather than silently redirecting.

| Backend state | UI behavior |
|---|---|
| catalog readable, setting not writable | Read-only row with reason |
| secret metadata not readable | Redacted or hidden reference metadata |
| audit not readable | No audit link |
| operation not invokable | Current state visible, action disabled with reason |
| permission error during mutation | Structured error, no automatic destructive retry |

Loading placeholders do not imply values are default, unset, permitted, or healthy. Empty states explain the next authorized action. Failures identify the affected page or region and keep unrelated content.

---

## 11. Secrets, validation, and explainability

Generic settings never accept raw secret values. Managed Secret creation and replacement accept plaintext only for one-way submission; afterwards the field is cleared and only metadata and validation state are shown. The UI may display an authorized SecretRef such as `db://github-token` or `env://GITHUB_TOKEN`, never its plaintext target. Provider Profile advanced fields use role-aware SecretRef controls rather than raw JSON, and OAuth volume identifiers and paths come from dedicated backend flows.

Client validation may use descriptor metadata for required values, bounds, option membership, length, simple patterns, SecretRef shape, and obvious type errors; backend validation is authoritative.

Source and application labels come from backend metadata (for example Default, Config, Environment, Workspace override, Provider Profile, Secret reference, Operator locked). The UI explains inherited sources and when a change applies (immediately, next request, next workflow, next launch, worker reload, process restart, or manual operation), and pending accepted values stay visible until active.

---

## 12. Accessibility

The configuration pages provide keyboard access to the dropdown, route links, page-local controls, and dialogs; visible focus and Escape-to-close; the same destination order on desktop and mobile; one active route exposed with route semantics; focus moved to the new page heading where appropriate; associated labels, descriptions, and errors; no reliance on color alone; confirmation and focus restoration for destructive operations; and readable long keys, SecretRefs, and profile identifiers.

The destination links are links, not `role="tab"` or radio controls, because they navigate to separate documents. The Provider Profile `Show advanced options` control is a native checkbox tied to an expandable form region; it is local form state, not navigation.

---

## 13. Extending Settings

Adding an ordinary instance setting needs a stable backend key, explicit exposure metadata, the descriptor fields from section 9, persistence and resolution where needed, and catalog and save/reset tests. Frontend work is needed only for a new reusable control type.

A fourth Configuration page makes sense only for a distinct user goal with its own route identity, permission model, loading boundary, and enough durable content; a category or small group of settings stays inside an existing page.

Patterns the design avoids, and why:

- page-local tabs, radio cards, pills, segmented controls, sidebars, or cards repeating the three destinations, and `?section=` as page identity — they duplicate the dropdown's navigation;
- eager loading of every configuration dataset — it breaks independent failure and page-scoped loading;
- hard-coded React rows per setting key, frontend copies of backend defaults or validation, and frontend-only authorization filtering — the backend owns these;
- a second provider-creation wizard or duplicate Harness/Provider catalog — one form and one choice authority;
- policy summaries inferred only from authentication-method availability, unconditional `enabled: true` for credential-required profiles, or a global fallback credential source — each misreports readiness;
- plaintext credential inputs in generic settings, raw SecretRef JSON as the primary credential UX, freeform `clear_env_keys`, and silent fallback for broken SecretRefs — they weaken secret handling; and
- boolean-preference treatment for operational commands — commands have state, impact, and confirmation.

---

## 14. Ownership structure

**Destination registry.** Configuration is exactly three sibling destinations with the keys and routes in section 5.1. The client registry (`frontend/src/lib/dashboardRoutes.ts`) and the server destination registry agree on entries, group membership, and order.

**Route ownership.** Each destination is a route-matched component. Sharing a bundle is fine; sharing page identity is not. A page does not mount managers, panels, or loaders belonging to a sibling, and no parent component chooses among the three by internal state.

**State ownership.** Page-scoped state lives on its page: Harness filtering on Providers & Secrets, worker command state on Operations. There is no cross-page selection state; browser navigation moves between destinations.

**Component ownership.** Provider Profile create/edit belongs to Providers & Secrets, generated settings to Instance, and operational commands to Operations.

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
