# Settings Configuration Pages

**Related design documents:** [SettingsSystem.md](../Security/SettingsSystem.md), [SecretsSystem.md](../Security/SecretsSystem.md), [ProviderProfiles.md](../Security/ProviderProfiles.md), [ProviderProfileModelEffortTierSettings.md](./ProviderProfileModelEffortTierSettings.md), [OAuthTerminal.md](../ManagedAgents/OAuthTerminal.md), [DashboardDesignSystem.md](./DashboardDesignSystem.md), [DashboardSPAArchitecture.md](./DashboardSPAArchitecture.md)

Status: **Desired-State UI Contract**  
Owners: MoonMind Engineering  
Last Updated: 2026-08-28

> [!NOTE]
> This document supersedes the single `/settings` page with a three-way tab, radio, or segmented section switcher. The existing Settings dropdown remains the only cross-page navigation surface. Its **Configuration** group exposes the three former Settings sections as separate pages.
>
> Security, persistence, resolution, validation, and authorization remain owned by [SettingsSystem.md](../Security/SettingsSystem.md) and the related domain documents.

---

## 1. Purpose

MoonMind Settings is the human-facing configuration plane for user, workspace, provider, secret, and operational configuration.

Settings is a family of sibling dashboard pages, not one large page with a second navigation layer.

The central information-architecture decision is:

> **Providers & Secrets**, **User / Workspace**, and **Operations** are separate pages in the Settings dropdown's **Configuration** group. None of those pages repeats the three destinations as tabs, radio buttons, segmented controls, pills, cards, a sidebar, or another local page switcher.

This gives every configuration surface a durable URL, page title, loading boundary, authorization state, and browser-history entry. It also removes redundant navigation and lets each page load only the data it owns.

The existing rendering decision remains:

> Settings is a **data-driven control plane**, not a manually maintained collection of one-off setting forms.

Ordinary user and workspace settings come from backend descriptors. Provider Profiles, Managed Secrets, OAuth state, and Operations remain specialized backend resources or commands.

---

## 2. Scope and Authority

This document owns:

- placement of configuration pages in the Settings dropdown;
- the Configuration group and its ordering;
- canonical routes and legacy redirects;
- active destination and dropdown-trigger behavior;
- shared page-shell rules;
- page-level loading and failure boundaries;
- descriptor-driven settings rendering;
- filtering, editing, preview, save, discard, and reset UX;
- Provider Profile, Managed Secret, OAuth, and Operations placement;
- permissions, secret-safe behavior, accessibility, and unsaved-change navigation; and
- migration and acceptance criteria for removing the old page-local switcher.

This document does not redefine backend setting eligibility, resolution, persistence, secret storage, provider execution semantics, operational command semantics, authorization, or server validation.

This is a desired-state contract. The current implementation may still use one Settings component, `?section=` routing, and a segmented or radio-based switcher.

---

## 3. Design Principles

### 3.1 One navigation owner

The Settings dropdown owns navigation among configuration pages. A page-local control is valid only when it changes state inside the current page, such as user versus workspace scope, a runtime filter, a category filter, or an operation subview.

### 3.2 Pathname owns page identity

The pathname selects the configuration page. Query parameters may select safe filters or scope within that page. Query parameters must not select among the three configuration pages.

### 3.3 Backend-owned truth

The backend owns which settings exist, which are exposed, their types and scopes, validation, sensitivity, effective values, sources, and application semantics. The frontend renders descriptors and submits intent.

### 3.4 Data-driven rows

The User / Workspace page renders ordinary settings from catalog descriptors. The frontend may branch on descriptor shape such as `type`, `ui`, `constraints`, `options`, `read_only`, and `sensitive`. It should not branch on individual setting keys except for documented transitional exceptions.

### 3.5 Explicit specialist surfaces

Provider Profiles, Managed Secrets, OAuth credentials, and Operations controls are not generic setting rows. They may use specialized managers, but options, capabilities, readiness, and policy constraints should still come from backend APIs.

### 3.6 References over secrets

Stored secret plaintext is never rendered. Generic settings do not accept raw credentials. Sensitive relationships use SecretRef pickers, provider-profile secret-role bindings, or one-way Managed Secret creation and replacement flows.

### 3.7 Safe independent failure

Failure on one configuration page must not make the other two unavailable unless the shared dashboard shell itself is unavailable.

---

## 4. Information Architecture

The Settings dropdown retains its existing grouped structure. The configuration portion is:

```text
Settings dropdown
  Configuration
    Providers & Secrets
    User / Workspace
    Operations
```

The three entries are sibling dashboard destinations. They are not sections rendered by a parent Settings page.

### 4.1 Configuration group contract

The dropdown must:

1. render the label `Configuration` once;
2. place the entries in this order: Providers & Secrets, User / Workspace, Operations;
3. render every entry as a route link;
4. expose one active entry with `aria-current="page"` or equivalent route semantics;
5. close after selection;
6. support the dashboard's keyboard menu behavior;
7. render the same group and order in the mobile drawer; and
8. omit the group label when none of its destinations is visible.

Group membership must be explicit or deterministically derived. The implementation must not attach `Configuration` only to one destination key and accidentally repeat or omit the label when multiple children exist.

### 4.2 Dropdown trigger

When any Configuration page is active, the masthead trigger remains:

```text
Settings
```

with the Settings icon.

It does not expand to `Providers & Secrets` or `User / Workspace`. The active page is communicated by the active menu item, URL, document title, and page header.

### 4.3 No Settings landing page

The dropdown is the configuration index. MoonMind does not need another page that repeats the same three choices as cards. The bare `/settings` route redirects to the default configuration page.

---

## 5. Routes

### 5.1 Canonical routes

| Destination | Canonical route | Suggested destination key |
|---|---|---|
| Providers & Secrets | `/settings/providers-secrets` | `settings-providers-secrets` |
| User / Workspace | `/settings/user-workspace` | `settings-user-workspace` |
| Operations | `/settings/operations` | `settings-operations` |

All three destinations belong to the Settings dropdown's Configuration group and use the dashboard's utility-page classification.

The route registry may map them to three page modules or one shared bundle with three route-owned components. The user-visible contract is three distinct pages.

### 5.2 Default redirect

`/settings` redirects with replacement history to `/settings/providers-secrets`.

### 5.3 Legacy redirects

| Legacy route | Canonical target |
|---|---|
| `/settings?section=providers-secrets` | `/settings/providers-secrets` |
| `/settings?section=user-workspace` | `/settings/user-workspace` |
| `/settings?section=operations` | `/settings/operations` |
| `/secrets` | `/settings/providers-secrets` |
| `/workers` | `/settings/operations` |
| an unknown older Settings alias | `/settings/providers-secrets` unless a safe mapping exists |

Redirects preserve safe page-relevant query parameters and remove `section`.

### 5.4 Page-local URL state

Page-local filters may use query parameters:

```text
/settings/providers-secrets?runtime=codex
/settings/user-workspace?scope=workspace
/settings/user-workspace?scope=user&q=workflow
/settings/operations?status=paused
```

Sensitive values never enter paths, query parameters, browser history, page titles, or navigation telemetry.

### 5.5 Page titles

Recommended document titles are:

```text
Providers & Secrets | MoonMind
User / Workspace | MoonMind
Operations | MoonMind
```

---

## 6. Shared Page Shell

Recommended structure:

```text
ConfigurationPage
  PageHeader
  PageStatusSummary
  PageContent
  PageDiagnosticsOrAudit
```

There is no `SectionSwitcher`, tab list, segmented destination control, destination radio group, or duplicate Settings sidebar.

### 6.1 Header

Each page header includes:

- an optional `Settings` or `Configuration` overline;
- the page-specific title;
- a short page-specific description;
- optional deployment or workspace context;
- a page-scoped warning when persistence, catalog loading, authorization, or operational state is degraded; and
- optional diagnostic or audit links where authorized.

Required titles are `Providers & Secrets`, `User / Workspace`, and `Operations`.

### 6.2 Page-specific status summaries

| Page | Summary emphasis |
|---|---|
| Providers & Secrets | launch readiness, profile validity, secret and OAuth health, blocked profiles |
| User / Workspace | overrides, pending application, locked settings, validation diagnostics |
| Operations | worker state, drain or pause status, queue and runtime health, pending commands |

Do not load all three pages' detailed datasets to reproduce one global health summary on every route. A compact cross-configuration alert may use a dedicated aggregate endpoint.

### 6.3 Unsaved changes

Dropdown navigation is real route navigation. When the current page has unsaved changes:

1. detect the dirty draft;
2. offer `Stay` and `Discard and leave`;
3. do not mark the destination active until navigation succeeds;
4. preserve the draft when navigation is canceled; and
5. navigate immediately when the page is clean or changes were saved.

---

## 7. Page Responsibilities

### 7.1 Providers & Secrets

This page contains:

- Provider Profiles as the durable runtime and provider launch contract;
- model and effort tier policy editing inside the normal Provider Profile editor;
- Managed Secrets and secret-health surfaces;
- SecretRef role bindings and validation;
- OAuth-backed profile lifecycle entry points;
- provider credential health and readiness; and
- runtime and provider binding diagnostics.

The page explains that profiles contain references and launch metadata, Managed Secrets contain encrypted values or external references, OAuth volumes contain runtime-specific credential state, and readiness combines profile validity with secret or OAuth resolvability.

A page-local runtime filter may narrow the visible Provider Profile collection. It must not narrow global readiness counts unless the summary is explicitly labeled as filtered.

### 7.2 User / Workspace

This page contains descriptor-driven settings for:

- user preferences;
- personal workflow-creation defaults;
- personal runtime and Provider Profile defaults;
- workspace workflow and routing defaults;
- workspace feature flags;
- non-secret integration defaults;
- safe policy controls; and
- SecretRef bindings not owned by a Provider Profile.

This is the canonical generated-settings surface. Adding an eligible ordinary setting should require backend catalog metadata and validation, not a new hard-coded React row.

The user versus workspace scope switch is a page-local control. It may update `?scope=` and must guard dirty drafts before changing scope.

### 7.3 Operations

This page contains explicit administrative commands and statusful controls for:

- worker pause and resume;
- drain and quiesce;
- queue and runtime health;
- maintenance mode;
- deployment or runtime updates where authorized;
- recent operational audit actions; and
- safe diagnostic switches.

Each command card shows current state, expected impact, permitted actions, disabled reason, confirmation requirements, reason input where required, pending transitions, last actor and time, failure state, and recovery or resume action.

The page title `Operations` is distinct from any broader dropdown group also labeled Operations. The group classifies destinations. This page specifically owns configuration and administration controls.

---

## 8. Data Loading Boundaries

| Page | Primary data |
|---|---|
| Providers & Secrets | Provider Profiles, Managed Secret metadata, OAuth state, readiness diagnostics |
| User / Workspace | catalog descriptors, effective values, scoped overrides, diagnostics, audit metadata |
| Operations | worker state, queue and runtime health, operation capabilities, command history |

On route load:

1. resolve the page from the pathname;
2. load only the minimum data needed for that page's first screen;
3. do not fetch sibling-page collections by default;
4. defer expensive diagnostics, audit timelines, and graphs until needed;
5. show route-level and region-level loading states; and
6. preserve page-local scope and filters across browser navigation.

The backend may continue using `section=user-workspace` as catalog classification. It no longer represents a client-side Settings tab.

Desired generated-settings endpoints remain:

```http
GET /api/v1/settings/catalog?section=user-workspace&scope=workspace
GET /api/v1/settings/catalog?section=user-workspace&scope=user
GET /api/v1/settings/effective?scope=workspace
GET /api/v1/settings/effective?scope=user
GET /api/v1/settings/diagnostics?scope=workspace
GET /api/v1/settings/audit?key=workflow.default_runtime
POST /api/v1/settings/preview
PATCH /api/v1/settings/workspace
PATCH /api/v1/settings/user
DELETE /api/v1/settings/workspace/{key}
DELETE /api/v1/settings/user/{key}
```

---

## 9. Descriptor-Driven User / Workspace Contract

A descriptor carries enough metadata for the frontend to render and explain a setting without key-specific logic:

```yaml
SettingDescriptor:
  key: string
  title: string
  description: string | null
  category: string
  section: user-workspace
  type: boolean | string | integer | number | enum | string_list | object | secret_ref
  ui: toggle | input | number | select | tag_editor | key_value | secret_ref_picker | provider_profile_picker | readonly
  scopes: [user | workspace | system | operator]
  default_value: any
  effective_value: any
  override_value: any | null
  source: string
  source_explanation: string
  options: array | null
  constraints: object | null
  sensitive: boolean
  read_only: boolean
  read_only_reason: string | null
  apply_mode: string
  activation_state: string
  pending_value: any | null
  applies_to: [string]
  value_version: integer
  diagnostics: array
```

Control selection follows descriptor shape:

| Descriptor | Control |
|---|---|
| boolean or `toggle` | Toggle |
| enum or `select` | Select from backend options |
| integer, number, or `number` | Constrained number input |
| string or `input` | Text input unless sensitivity requires a specialist control |
| string list or `tag_editor` | Tag or list editor |
| object or `key_value` | Safe structured editor |
| secret ref or `secret_ref_picker` | SecretRef picker |
| `provider_profile_picker` | Provider Profile selector using backend data |
| read-only or unsupported editable type | Read-only value with reason or diagnostic |

Every row should expose title, description, control or value, stable key, scope, source, inherited or override state, active or pending state, validation, diagnostics, reset when allowed, application semantics, affected systems, and audit link where authorized.

Filtering should support text search, category, modified-only, read-only, diagnostics, source, pending application, and secret-related filters where authorized. Filtering is never the authorization boundary.

Before save, show changed keys, old and proposed values, validation, target scope, expected versions, affected systems, application requirements, dependency warnings, and redaction behavior. The backend preview is authoritative where available.

Draft state is keyed by setting key and scope. Clear it after successful save, explicit discard, confirmed scope change, confirmed route departure, or incompatible catalog-version change. Do not persist drafts to local storage without a separate sensitive-metadata design.

---

## 10. Permissions and Failure States

The backend controls authorization. The UI reflects it without treating client checks as a security boundary.

Each destination should have an independent state when permissions differ:

- `shown` for meaningful accessible content;
- `unavailable` when product policy wants a visible explanation; or
- `hidden` when the destination should not be advertised.

The Configuration label appears when at least one child is shown or intentionally unavailable. A direct unauthorized route shows a clear unavailable or forbidden state rather than silently redirecting to a sibling page.

In-page behavior includes:

| Backend state | UI behavior |
|---|---|
| catalog readable, setting not writable | Read-only row with reason |
| secret metadata not readable | Redacted or hidden reference metadata |
| audit not readable | No audit link |
| operation not invokable | Current state visible, action disabled with reason |
| permission error during mutation | Structured error, no automatic destructive retry |

Loading placeholders must not imply values are default, unset, safe, or healthy. Empty states explain the next authorized action. Failures identify the affected page or region and preserve unrelated content where possible.

---

## 11. Secret Safety, Validation, and Explainability

Generic settings never accept raw secret values. Managed Secret creation and replacement may accept plaintext only for one-way submission. After submission, clear the field and display metadata and validation state, not the value.

The UI may display an authorized SecretRef such as `db://github-token` or `env://GITHUB_TOKEN`, but never its plaintext target.

Client validation may use descriptor metadata for required values, numeric bounds, option membership, length, simple patterns, SecretRef shape, and obvious type errors. Backend validation remains authoritative.

Source and application labels come from backend metadata. Recommended source labels include Default, Config, Environment, Workspace override, User override, Provider Profile, Secret reference, and Operator locked.

The UI explains inherited sources and application boundaries such as immediate, next request, next workflow, next launch, worker reload, process restart, or manual operation. Pending accepted values remain visible until active.

---

## 12. Accessibility

The configuration experience must support:

- keyboard access to the dropdown, every route link, page-local controls, and dialogs;
- visible focus and Escape-to-close behavior;
- consistent destination order on desktop and mobile;
- one active route exposed with route semantics;
- focus movement to the new page heading where appropriate;
- associated labels, descriptions, and errors;
- no reliance on color alone;
- confirmation and focus restoration for destructive operations; and
- readable long keys, SecretRefs, and Provider Profile identifiers.

The three destination links must not use `role="tab"` or radio semantics. They navigate to documents and behave as links.

---

## 13. Extensibility Rules

Adding a normal user or workspace setting should require:

1. a stable backend key;
2. explicit exposure metadata;
3. title, description, category, scopes, type, UI control, constraints, options, sensitivity, application semantics, and audit policy;
4. persistence and resolution where needed; and
5. catalog and save/reset tests.

Frontend work is required only for a new reusable control type.

A fourth Configuration page is justified only when it has a distinct user goal, route identity, permission model, loading boundary, and enough durable content to stand alone. A category or small settings group stays within an existing page.

Forbidden patterns include:

- page-local tabs, radio cards, pills, segmented controls, sidebars, or cards that repeat the three destinations;
- `?section=` as page identity;
- eager loading of every configuration dataset on every route;
- one hard-coded React row per setting key;
- frontend copies of backend defaults or authoritative validation;
- plaintext credential inputs in generic settings;
- frontend-only authorization filtering;
- silent fallback for broken SecretRefs; and
- boolean-preference treatment for operational commands.

---

## 14. Implementation Migration

### 14.1 Destination registry

Replace the single Settings destination with:

```text
settings-providers-secrets -> /settings/providers-secrets
settings-user-workspace    -> /settings/user-workspace
settings-operations        -> /settings/operations
```

All three belong to the Configuration group. Local and server-provided destination registries must stay synchronized. Group membership should be explicit metadata or stable grouping logic.

### 14.2 Page boundaries

Recommended decomposition:

```text
ProvidersSecretsSettingsPage
  ProvidersSecretsHeader
  ProvidersSecretsHealthSummary
  ProviderProfilesManager
  SecretManager
  OAuth and token validation surfaces

UserWorkspaceSettingsPage
  UserWorkspaceHeader
  UserWorkspaceStatusSummary
  ScopeSwitcher
  GeneratedSettingsSection
  User and workspace identity/preferences surfaces

OperationsSettingsPage
  OperationsHeader
  OperationsStatusSummary
  OperationsSettingsSection
  Operational audit and diagnostics
```

The pages may initially share one bundle. They must still be route-owned components and must not mount unrelated managers.

### 14.3 Remove old section state

Remove equivalents of:

- `SETTINGS_SECTIONS`;
- `SettingsSectionId`;
- `readSectionFromLocation`;
- `updateSectionInLocation`;
- local cross-page `section` state;
- manual `popstate` handling for section selection;
- the destination radio group or segmented control;
- descriptions selected from a local section array; and
- conditional page rendering selected by `section === ...`.

Normal router matching replaces this state.

Move state to the owning page. Runtime filtering stays on Providers & Secrets. User versus workspace scope stays on User / Workspace. Worker command state stays on Operations.

### 14.4 Required tests

Cover:

- three destination-registry entries;
- one Configuration label and correct ordering;
- stable Settings trigger behavior;
- active state for all three routes;
- desktop and mobile navigation;
- `/settings` and every legacy redirect;
- absence of Settings tab or radio navigation;
- page-specific data loading and no unrelated manager mounting;
- Back and Forward behavior;
- dirty-draft route guards;
- direct-route permissions; and
- deep links with page-local filters.

---

## 15. Acceptance Criteria

The design is satisfied when:

1. The existing Settings dropdown contains one Configuration group.
2. It contains route links for Providers & Secrets, User / Workspace, and Operations in that order.
3. Each destination has its own canonical `/settings/...` pathname.
4. `/settings` redirects to `/settings/providers-secrets`.
5. Legacy `?section=` links redirect to the corresponding canonical page.
6. No configuration page repeats the destinations as tabs, radio buttons, segmented controls, pills, cards, a sidebar, or another local switcher.
7. The masthead trigger remains `Settings` with the Settings icon while any Configuration page is active.
8. Desktop and mobile expose the same group and order.
9. Each page has its own title, header, loading boundary, authorization state, and failure state.
10. Each page loads only its required primary datasets by default.
11. Providers & Secrets keeps Provider Profiles, Managed Secrets, OAuth, tier policy, and readiness as first-class surfaces.
12. User / Workspace renders ordinary settings from backend descriptors and supports authorized scope switching.
13. Operations uses explicit statusful command cards with confirmation and audit context.
14. Secret-like settings use SecretRefs or Managed Secret flows, never generic plaintext inputs.
15. Route navigation protects unsaved drafts.
16. Unsupported descriptors degrade safely.
17. A failure on one page does not break sibling Configuration destinations.
18. Backend authorization, defaults, validation, source resolution, and secret-safety decisions remain authoritative.
19. Navigation tests and telemetry use canonical destination keys instead of the removed `section` state.

---

## 16. Decision Summary

- Settings is a configuration namespace and dropdown group, not one page with three tabs.
- Providers & Secrets, User / Workspace, and Operations are sibling pages.
- Canonical routes are `/settings/providers-secrets`, `/settings/user-workspace`, and `/settings/operations`.
- `/settings` redirects to Providers & Secrets.
- The Settings dropdown is the single cross-page navigation owner.
- The Settings trigger remains stable on all three pages.
- Query parameters represent page-local state, not page identity.
- Every page owns its title, status, authorization, loading, queries, and failures.
- Existing managers can be reused, but the old local section state and switcher are removed.
