# Settings Configuration Pages

**Related design documents:** [SettingsSystem.md](../Security/SettingsSystem.md), [SecretsSystem.md](../Security/SecretsSystem.md), [ProviderProfiles.md](../Security/ProviderProfiles.md), [ProviderProfileCreation.md](./ProviderProfileCreation.md), [ProviderProfileModelEffortTierSettings.md](./ProviderProfileModelEffortTierSettings.md), [OAuthTerminal.md](../ManagedAgents/OAuthTerminal.md), [DashboardDesignSystem.md](./DashboardDesignSystem.md), [DashboardSPAArchitecture.md](./DashboardSPAArchitecture.md)

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
- canonical routes, the default entry point, and legacy path redirects;
- active destination and dropdown-trigger behavior;
- shared page-shell rules;
- page-level loading and failure boundaries;
- descriptor-driven settings rendering;
- filtering, editing, preview, save, discard, and reset UX;
- Provider Profile, Managed Secret, OAuth, and Operations placement;
- permissions, secret-safe behavior, accessibility, and unsaved-change navigation; and
- migration and acceptance criteria for removing the old page-local switcher.

The detailed Provider Profile create and edit form is owned by [ProviderProfileCreation.md](./ProviderProfileCreation.md). Model and effort tier editing is owned by [ProviderProfileModelEffortTierSettings.md](./ProviderProfileModelEffortTierSettings.md).

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

Provider Profile creation follows the same rule. Runtime, provider, authentication, and creation capabilities determine backend-owned presets. The browser must not assume one materialization strategy for every provider.

### 3.4 Data-driven rows

The User / Workspace page renders ordinary settings from catalog descriptors. The frontend may branch on descriptor shape such as `type`, `ui`, `constraints`, `options`, `read_only`, and `sensitive`. It should not branch on individual setting keys except for documented transitional exceptions.

### 3.5 Explicit specialist surfaces

Provider Profiles, Managed Secrets, OAuth credentials, and Operations controls are not generic setting rows. They may use specialized managers, but options, capabilities, readiness, and policy constraints should still come from backend APIs.

### 3.6 Progressive disclosure

Forms should lead with understandable choices and hide low-level implementation details until requested.

For Provider Profile creation, credential connection remains visible, while raw SecretRefs, volume metadata, materialization details, secondary rate-limit controls, routing metadata, and launch shaping belong behind `Show advanced options`. Max parallel runs remains visible.

### 3.7 References over secrets

Stored secret plaintext is never rendered. Generic settings do not accept raw credentials. Sensitive relationships use SecretRef pickers, provider-profile secret-role bindings, or one-way Managed Secret creation and replacement flows.

### 3.8 Safe independent failure

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

The dropdown is the configuration index. MoonMind does not need another page that repeats the same three choices as cards. The bare `/settings` route resolves to the first configuration page the current user is authorized to see, as defined in section 5.2.

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

### 5.2 Default entry point

`/settings` is an entry point, not a destination. It resolves to the first
destination the backend authorizes for the current user, evaluated in the
canonical Configuration order:

1. `/settings/providers-secrets`;
2. `/settings/user-workspace`; then
3. `/settings/operations`.

Resolution uses replacement history so `/settings` does not become a back-button
trap. The default must never assume Providers & Secrets is available: when that
destination is `hidden` or `unavailable` and another destination is `shown`,
`/settings` resolves to that accessible destination instead.

When no Configuration destination is accessible, `/settings` renders the
Configuration unavailable state described in section 10 rather than redirecting
to a forbidden page. This resolution applies only to `/settings` itself; a direct
request for a specific unauthorized destination still shows that destination's
own unavailable or forbidden state and is never silently rerouted to a sibling.

### 5.3 Retired `?section=` routing

`?section=` is a superseded internal routing contract, not a supported legacy
route. MoonMind is pre-release, so the rename is completed rather than aliased:
the change that introduces the canonical paths also updates every internal
caller, test, and document that still builds a `?section=` Settings link, and
deletes the old section-routing code. No redirect table preserves `?section=`,
because a redirect would let a partial migration keep working and hide the
remaining callers.

After the rename, `section` carries no page identity. A `section` query
parameter arriving on a canonical route is ignored and does not select, restore,
or override the page.

Section 8 covers the separate, still-valid backend use of `section=` as catalog
classification on the settings API. That server-side parameter is unrelated to
client routing and is not affected by this rule.

### 5.4 Legacy path redirects

Standalone paths that users may have bookmarked keep a redirect, because they
are user-visible URLs rather than an internal routing contract:

| Legacy path | Canonical target |
|---|---|
| `/secrets` | `/settings/providers-secrets` |
| `/workers` | `/settings/operations` |
| an unknown older Settings alias | the default entry point in section 5.2 unless a safe specific mapping exists |

These redirects use replacement history, preserve safe page-relevant query
parameters, and drop parameters that no longer have meaning on the target page.
A redirect never lands on a destination the user is not authorized to see; when
the mapped destination is unavailable, resolution falls back to section 5.2.

### 5.5 Page-local URL state

Page-local filters may use query parameters:

```text
/settings/providers-secrets?runtime=codex
/settings/user-workspace?scope=workspace
/settings/user-workspace?scope=user&q=workflow
/settings/operations?status=paused
```

Sensitive values never enter paths, query parameters, browser history, page titles, or navigation telemetry.

### 5.6 Page titles

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

#### 7.1.1 Provider Profile creation

Provider Profile creation uses progressive disclosure as defined by [ProviderProfileCreation.md](./ProviderProfileCreation.md).

The standard form keeps these user-facing decisions visible:

- Profile ID, with an auto-generated suggestion and review before creation;
- runtime;
- provider;
- optional account label;
- high-level authentication method and connection action;
- model and effort tier policy;
- max parallel runs; and
- optional `Use as runtime default` intent when readiness permits.

One unchecked `Show advanced options` checkbox reveals:

- credential source and materialization metadata;
- structured SecretRef bindings;
- volume reference and mount metadata;
- cooldown after provider 429 responses;
- rate-limit policy;
- command behavior;
- routing tags and priority; and
- launch-shaping diagnostics.

Credential setup itself does not disappear behind the checkbox. OAuth and API-key actions remain visible, while low-level credential plumbing stays advanced or system-generated.

`clear_env_keys` is backend-owned launch-safety metadata. The normal Settings UI may display it read-only, but must not present a freeform textarea as an ordinary preference.

Creation must use backend presets or omit untouched advanced values. It must not blindly submit global React defaults. A profile with required missing credentials is saved disabled, while successful guided setup may enable it when policy permits.

#### 7.1.2 Provider Profile edit behavior

On edit, advanced options start expanded when the profile has non-default, unknown, incompatible, or invalid advanced values. Otherwise they may start collapsed with an effective-policy summary.

Collapsing the region preserves every draft value. A validation error targeting a hidden field automatically expands the region and moves focus to the affected control.

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
| Providers & Secrets | Provider Profiles, Managed Secret metadata, OAuth state, readiness diagnostics, profile creation presets |
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

A descriptor carries enough metadata for the frontend to render and explain a setting without key-specific logic. The backend owns this shape; the fields below are the ones the row contract in this document depends on, and a consumer must not drop a field it renders:

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
  secret_role: string | null
  read_only: boolean
  read_only_reason: string | null
  apply_mode: string
  activation_state: string
  active: boolean
  pending_value: any | null
  affected_process_or_worker: string | null
  completion_guidance: string | null
  requires_reload: boolean
  requires_worker_restart: boolean
  requires_process_restart: boolean
  applies_to: [string]
  depends_on: array
  order: integer
  audit: object
  value_version: integer
  diagnostics: array
```

These fields are load-bearing for the behavior this document requires:

| Field | Row behavior that depends on it |
|---|---|
| `active` | distinguishing an applied value from an accepted-but-pending one |
| `pending_value` | showing the accepted value awaiting activation |
| `affected_process_or_worker` | naming the system a pending change is waiting on |
| `completion_guidance` | telling the operator what completes the activation |
| `requires_reload`, `requires_worker_restart`, `requires_process_restart` | application-requirement warnings before and after save |
| `order` | deterministic row ordering within a category |
| `secret_role` | role-aware SecretRef controls |
| `depends_on` | dependency warnings in preview and validation |
| `audit` | whether and how an audit link is offered |

This block is the contract the UI consumes, not an exhaustive mirror of the
backend model. The backend may add fields; it may not remove one this document
renders without updating this section in the same change.

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

### 9.1 Version conflicts

Saves submit `expected_versions` alongside the changed keys, so the authoritative
`PATCH` contract can reject the write with `version_conflict` when another
operator changed the same setting after this page loaded. That is a normal
concurrency outcome, not a generic failure, and the UI must make it recoverable:

1. stop the save and apply no partial write locally;
2. reload the affected descriptors;
3. tell the user that another change occurred, identifying the affected keys;
4. show the latest value next to the user's proposed value so the user can
   review the difference; and
5. require explicit resubmission with refreshed `expected_versions`.

Never auto-retry a `version_conflict` with the stale expected version, and never
silently overwrite the concurrent change. Unaffected keys in the same submission
keep their drafts so the user does not lose unrelated edits.

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

Provider Profile advanced fields use role-aware SecretRef controls rather than raw JSON as the primary UX. OAuth volume identifiers and paths are normally generated or imported through dedicated backend-supported flows.

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

The Provider Profile `Show advanced options` control is a native checkbox connected to an expandable form region. It is local form state, not cross-page navigation.

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
- unconditional client-side `enabled: true` for a credential-required Provider Profile;
- a global fallback credential source or materialization mode for every provider;
- plaintext credential inputs in generic settings;
- raw SecretRef JSON as the primary credential setup UX;
- freeform `clear_env_keys` as an ordinary Provider Profile preference;
- frontend-only authorization filtering;
- silent fallback for broken SecretRefs; and
- boolean-preference treatment for operational commands.

---

## 14. Ownership Invariants

These are the durable structural rules the three pages must satisfy. They
describe the target state, not a sequence of migration steps, and they hold for
any future change to the Configuration pages.

**Destination registry.** Configuration is exactly three sibling destinations,
each with its own destination key and canonical route as listed in section 5.1.
The client-side registry and any server-provided registry must agree on those
entries, their Configuration group membership, and their order. Group membership
is explicit metadata or deterministically derived, never inferred from a single
destination key.

**Route ownership.** Each destination is owned by a route-matched component.
Sharing one bundle is allowed; sharing page identity is not. A page must not
mount managers, panels, or data loaders belonging to a sibling destination, and
no parent component may select between the three by internal state.

**State ownership.** Page-scoped state lives on the page that owns it: runtime
filtering on Providers & Secrets, user-versus-workspace scope on User /
Workspace, and worker command state on Operations. No cross-page selection state
exists, and browser navigation — not local state — moves between destinations.

**Component ownership.** Provider Profile creation and edit behavior belongs to
Providers & Secrets and follows `docs/UI/ProviderProfileCreation.md`. The
generated user and workspace settings surface belongs to User / Workspace and
follows section 9. Operational commands belong to Operations and are never
modeled as ordinary boolean preferences.

---

## 15. Acceptance Criteria

The design is satisfied when:

1. The existing Settings dropdown contains one Configuration group.
2. It contains route links for Providers & Secrets, User / Workspace, and Operations in that order.
3. Each destination has its own canonical `/settings/...` pathname.
4. `/settings` resolves to the first Configuration destination the current user is authorized to see, and renders the unavailable state when none is accessible.
5. `?section=` selects nothing: no client route honors it, and no internal caller, test, or document still builds a `?section=` Settings link.
6. No configuration page repeats the destinations as tabs, radio buttons, segmented controls, pills, cards, a sidebar, or another local switcher.
7. The masthead trigger remains `Settings` with the Settings icon while any Configuration page is active.
8. Desktop and mobile expose the same group and order.
9. Each page has its own title, header, loading boundary, authorization state, and failure state.
10. Each page loads only its required primary datasets by default.
11. Providers & Secrets keeps Provider Profiles, Managed Secrets, OAuth, tier policy, and readiness as first-class surfaces.
12. Provider Profile creation starts with one collapsed `Show advanced options` control.
13. Credentials & Volumes, cooldown, rate-limit policy, command behavior, tags, and priority are advanced.
14. Max parallel runs remains visible in the standard Provider Profile form.
15. Account label remains a visible user-facing identity aid.
16. Credential connection remains visible while credential implementation details stay advanced or system-generated.
17. Clear environment keys are backend-owned launch-safety metadata.
18. Provider Profile creation uses contextual backend presets or omission rather than global frontend guesses.
19. A credential-required profile is not silently created enabled without successful setup.
20. User / Workspace renders ordinary settings from backend descriptors and supports authorized scope switching.
21. Operations uses explicit statusful command cards with confirmation and audit context.
22. Secret-like settings use SecretRefs or Managed Secret flows, never generic plaintext inputs.
23. Route navigation protects unsaved drafts.
24. Unsupported descriptors degrade safely.
25. A failure on one page does not break sibling Configuration destinations.
26. Backend authorization, defaults, validation, source resolution, and secret-safety decisions remain authoritative.
27. Navigation tests and telemetry use canonical destination keys instead of the removed `section` state.
28. A concurrent-change `version_conflict` stops the save, refreshes the affected descriptors, shows the conflict, and requires explicit resubmission.
29. Rendered rows keep the descriptor fields section 9 declares load-bearing, including active state, activation guidance, application requirements, and ordering.

---

## 16. Decision Summary

- Settings is a configuration namespace and dropdown group, not one page with three tabs.
- Providers & Secrets, User / Workspace, and Operations are sibling pages.
- Canonical routes are `/settings/providers-secrets`, `/settings/user-workspace`, and `/settings/operations`.
- `/settings` resolves to the first authorized Configuration destination rather than assuming Providers & Secrets.
- The Settings dropdown is the single cross-page navigation owner.
- The Settings trigger remains stable on all three pages.
- Query parameters represent page-local state, not page identity.
- Every page owns its title, status, authorization, loading, queries, and failures.
- Provider Profile creation uses one collapsed advanced-options region.
- Max parallel runs remains visible.
- Credential plumbing, volumes, secondary rate limits, routing metadata, and launch shaping are advanced or backend-derived.
- Account label remains visible.
- Enabled state follows successful credential activation and policy.
- Existing managers can be reused, but the old local section state and switcher are removed.
