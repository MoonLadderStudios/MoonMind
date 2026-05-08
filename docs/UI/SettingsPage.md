# Settings Page

**Related design documents:** [SettingsSystem.md](../Security/SettingsSystem.md), [SecretsSystem.md](../Security/SecretsSystem.md), [ProviderProfiles.md](../Security/ProviderProfiles.md), [OAuthTerminal.md](../ManagedAgents/OAuthTerminal.md), [ManagedAndExternalAgentExecutionModel.md](../Temporal/ManagedAndExternalAgentExecutionModel.md)

Status: **Desired-State UI Contract**
Owners: MoonMind Engineering
Last Updated: 2026-05-08

> [!NOTE]
> This document supersedes `docs/UI/SettingsTab.md` and should live at `docs/UI/SettingsPage.md`.
> It defines the Mission Control UI contract for the Settings page.
> The security, persistence, resolution, validation, and authorization contract remains owned by [SettingsSystem.md](../Security/SettingsSystem.md).

---

## 1. Purpose

The Mission Control **Settings** page is the human-facing configuration plane for user, workspace, provider, secret, and operational configuration.

Settings keeps the main product surface focused on tasks, task details, proposals, schedules, runs, and other task-adjacent workflows. Configuration that shapes those workflows belongs under one coherent Settings page rather than being split across unrelated top-level destinations.

The Settings page replaces the older split where **Settings**, **Secrets**, and **Workers** could behave like separate product areas.

The central UI decision is:

> The Settings page must be a **data-driven control plane**, not a manually maintained collection of one-off setting forms.

This means:

- section navigation is stable and product-owned;
- ordinary user/workspace setting rows are rendered from backend-owned descriptors;
- effective values, defaults, override state, validation rules, source explanations, and reload semantics are loaded from Settings APIs;
- provider profiles, managed secrets, OAuth state, and operations are loaded as first-class backend resources;
- the frontend maintains a small renderer palette for known descriptor control types, not bespoke UI code for every setting key; and
- adding a new eligible setting should normally require backend catalog metadata and validation, not a new hard-coded React panel.

---

## 2. Scope and Authority

### 2.1 What this UI document owns

This document owns the Settings page user experience contract:

- page route and section navigation;
- page-level layout;
- data-loading behavior;
- descriptor-driven rendering rules;
- supported generic controls;
- scope switching, filtering, editing, preview, save, and reset UX;
- source, inheritance, lock, diagnostic, and reload indicators;
- provider profile, managed secret, OAuth, and operations placement inside Settings;
- secret-safe display behavior;
- permission and empty-state UX; and
- acceptance criteria for adding settings without hard-coding each setting in the UI.

### 2.2 What this UI document does not own

This document does not redefine:

- setting eligibility rules;
- setting resolution order;
- scoped override persistence;
- secret storage, encryption, decryption, or resolution;
- provider profile execution semantics;
- operational command semantics;
- backend authorization; or
- server-side validation.

Those contracts remain owned by the Settings System, Secrets System, Provider Profiles, OAuth, Operations, and runtime strategy documents.

### 2.3 Implementation-status note

This is a desired-state UI contract. Current implementation may be partially aligned. Rollout sequencing, migrations, and tactical implementation handoffs belong in MoonSpec artifacts or implementation plans, not in this durable UI contract.

---

## 3. Design Principles

### 3.1 Backend-owned truth

The backend owns which settings exist, which are exposed, how they are typed, which scopes can edit them, how values are validated, whether values are sensitive, and how effective values are resolved.

The frontend renders descriptors and submits user intent. It must not decide that a backend field is safe to expose merely because that field exists.

### 3.2 Data-driven rows, not key-specific forms

The User / Workspace section must render ordinary settings from catalog descriptors. A setting key is an identity, not a frontend branching mechanism.

The frontend may switch on descriptor shape such as `type`, `ui`, `constraints`, `options`, `read_only`, and `sensitive`. It should not switch on specific keys such as `workflow.default_task_runtime` except for intentionally documented transitional exceptions.

### 3.3 Explicit specialist surfaces

Not every configurable object is an ordinary setting row.

Provider Profiles, Managed Secrets, OAuth credentials, and Operations controls have specialized semantics. They may use specialized managers, but those managers must still load their state and capabilities from backend APIs instead of embedding hidden product policy in the frontend.

### 3.4 References over secrets

The Settings page must never render stored secret plaintext. Generic setting overrides must not accept raw credentials. Settings that need sensitive material use SecretRef pickers, provider-profile secret-role bindings, or one-way managed-secret creation/replacement flows.

### 3.5 Explainability

Every visible effective value should be understandable from the UI. Users should be able to answer:

- what value is active;
- where it came from;
- whether it is inherited or overridden;
- whether it is locked;
- what scope controls it;
- what systems it affects;
- whether a reload, restart, next task, or next launch is required; and
- how to reset it to the inherited value when permitted.

### 3.6 Safe degradation

Unknown descriptor fields must not break the Settings page. Unsupported editable controls should degrade to a read-only row with an actionable diagnostic, or to a backend-provided unsupported-control message. The UI must not silently invent a control for an unknown sensitive field.

---

## 4. Information Architecture

The desired top-level Mission Control navigation model is:

```text
Tasks and task-adjacent product surfaces
Settings
```

Within **Settings**, Mission Control exposes section navigation rather than separate top-level tabs.

```text
Settings
  Providers & Secrets
  User / Workspace
  Operations
```

### 4.1 Providers & Secrets

This section is the primary configuration surface for runtime and provider access.

It contains:

- Provider Profiles as the durable runtime/provider launch contract;
- Managed Secrets and secret-health surfaces;
- SecretRef usage and validation surfaces;
- bindings between secrets and provider-profile roles;
- OAuth-backed provider-profile lifecycle entry points when applicable;
- provider credential health, readiness, and validation feedback; and
- runtime/provider binding diagnostics.

This section should clearly communicate that provider profiles contain references and launch metadata, managed secrets contain encrypted values or external references, OAuth volumes contain runtime-specific credential state, and readiness is a product of profile validity plus secret/OAuth resolvability.

### 4.2 User / Workspace

This section contains schema-driven settings for user and workspace behavior.

It includes:

- user preferences;
- personal task creation defaults;
- personal runtime and provider profile defaults;
- workspace task defaults;
- workspace routing defaults;
- workspace feature flags;
- non-secret integration defaults;
- policy knobs that are safe to expose; and
- SecretRef bindings that are not provider-profile-specific.

This section is the main descriptor-rendered surface. New eligible settings should appear here by adding backend metadata and validation rather than by adding bespoke UI components for each setting.

### 4.3 Operations

This section contains operational controls and status-backed administrative actions.

It includes:

- worker pause/resume controls;
- drain and quiesce controls;
- queue and runtime health summaries;
- maintenance-mode controls;
- deployment or runtime update controls where authorized;
- recent operational audit actions; and
- safe diagnostic switches.

Operations controls are not ordinary preferences. They are explicit commands or statusful controls that must show current state, authorization, expected effect, confirmation requirements, and audit history.

---

## 5. Routing

Canonical route:

```text
/tasks/settings
```

Supported section query model:

```text
/tasks/settings?section=providers-secrets
/tasks/settings?section=user-workspace
/tasks/settings?section=operations
```

The default section should be `providers-secrets` unless product analytics or onboarding requirements justify a different default.

Legacy routes should redirect into the corresponding Settings section:

| Legacy route | Target |
|---|---|
| `/tasks/secrets` | `/tasks/settings?section=providers-secrets` |
| `/tasks/workers` | `/tasks/settings?section=operations` |
| older Settings tab aliases | `/tasks/settings` |

Redirects should preserve relevant query parameters where safe.

---

## 6. Page Shell

The Settings page shell is stable and may be hard-coded because it represents product information architecture rather than individual setting fields.

Recommended structure:

```text
SettingsPage
  PageHeader
  SectionSwitcher
  SectionStatusSummary
  SectionContent
```

### 6.1 Page header

The header should include:

- title: `Settings`;
- short description of the selected section;
- optional deployment/workspace context;
- optional global warning when settings persistence, catalog loading, or authorization is degraded; and
- optional link to diagnostics or audit where authorized.

### 6.2 Section switcher

The switcher should expose exactly the primary Settings sections unless the backend or product configuration intentionally adds another section:

- Providers & Secrets;
- User / Workspace;
- Operations.

The switcher may show badges for section-level warnings, such as unresolved SecretRefs, blocked provider profiles, pending reloads, or paused workers.

### 6.3 Section content

Each section should follow the same broad pattern:

1. overview copy;
2. status/readiness summary;
3. data-driven tables, generated forms, or command cards;
4. diagnostics and audit affordances; and
5. high-risk actions grouped into clearly labeled areas.

---

## 7. Data Loading Model

The Settings page loads configuration through backend APIs. It must not directly read data stores from the browser.

### 7.1 Data sources by section

| Section | Primary data loaded by UI | Backend-owned data stores or resolvers |
|---|---|---|
| Providers & Secrets | provider profiles, managed secret metadata, OAuth volume state, readiness diagnostics | provider profile records, managed secrets, OAuth credential volume state, secret resolvers |
| User / Workspace | settings catalog descriptors, effective values, overrides, diagnostics, audit entries | settings registry/catalog, settings override rows, environment/config defaults, settings audit rows, managed secret metadata for SecretRefs |
| Operations | worker state, queue health, runtime health, operation capabilities, command history | operations state stores, queue/workflow state, deployment state, operational audit/event stores |

### 7.2 Initial page load

The page may use boot payload data for route context, user identity, or deployment feature flags, but boot payload data must not be the authoritative source for the settings catalog.

When the page loads:

1. determine the selected section from the query string;
2. load the minimum section data needed to render the first screen;
3. defer expensive diagnostics, audit timelines, and resource usage graphs until the section or row needs them;
4. show section-level skeletons while data loads; and
5. preserve section and scope selection across browser navigation.

### 7.3 User / Workspace catalog load

For descriptor-rendered settings, the UI loads catalog data by section and scope.

Desired APIs:

```http
GET /api/v1/settings/catalog?section=user-workspace&scope=workspace
GET /api/v1/settings/catalog?section=user-workspace&scope=user
GET /api/v1/settings/effective?scope=workspace
GET /api/v1/settings/effective?scope=user
GET /api/v1/settings/diagnostics?scope=workspace
GET /api/v1/settings/audit?key=workflow.default_task_runtime
```

The catalog response should contain enough information for the UI to render controls, source explanations, read-only state, diagnostics, and reset affordances without hard-coded per-setting knowledge.

### 7.4 Provider and operations loads

Provider Profiles, Managed Secrets, OAuth flows, and Operations may use specialized endpoints because they are resources and commands, not generic setting rows.

Even specialized managers should load capabilities and constraints from backend responses where possible. For example:

- provider profile forms should load runtime/provider options, required secret roles, allowed materialization modes, model defaults, readiness checks, and validation outcomes;
- managed secret lists should load metadata, status, validation state, and usage references;
- OAuth panels should load connection state and permitted actions;
- operations panels should load current state, permitted commands, confirmation requirements, and recent audit entries.

---

## 8. Descriptor-Driven User / Workspace UI

The User / Workspace section is the canonical generated-settings surface.

### 8.1 Catalog response shape consumed by the UI

The UI expects descriptors grouped by category.

```yaml
SettingsCatalogResponse:
  section: user-workspace
  scope: user | workspace
  categories:
    Workflow:
      - SettingDescriptor
    Skills:
      - SettingDescriptor
```

Each descriptor supplies the information needed to render and edit a row.

```yaml
SettingDescriptor:
  key: string
  title: string
  description: string | null
  category: string
  section: providers-secrets | user-workspace | operations
  type: boolean | string | integer | number | enum | string_list | object | secret_ref
  ui: toggle | input | number | select | tag_editor | key_value | secret_ref_picker | provider_profile_picker | readonly
  scopes: [user | workspace | system | operator]
  default_value: any
  effective_value: any
  override_value: any | null
  source: string
  source_explanation: string
  options: [SettingOption] | null
  constraints: SettingConstraints | null
  sensitive: boolean
  secret_role: string | null
  read_only: boolean
  read_only_reason: string | null
  requires_reload: boolean
  requires_worker_restart: boolean
  requires_process_restart: boolean
  apply_mode: string
  activation_state: string
  active: boolean
  pending_value: any | null
  affected_process_or_worker: string | null
  completion_guidance: string | null
  applies_to: [string]
  depends_on: [SettingDependency]
  order: integer
  audit: SettingAuditPolicy
  value_version: integer
  diagnostics: [SettingDiagnostic]
```

The exact backend schema may evolve, but the UI contract is stable: descriptors carry display metadata, control metadata, current/effective value, scope/source metadata, validation metadata, reload/application semantics, and diagnostics.

### 8.2 Renderer selection

The generated renderer chooses controls by descriptor metadata, not by setting key.

| Descriptor shape | UI behavior |
|---|---|
| `type: boolean` or `ui: toggle` | Toggle switch |
| `type: enum` or `ui: select` | Select using descriptor `options` |
| `type: integer`, `type: number`, or `ui: number` | Number input using min/max/step constraints when present |
| `type: string` or `ui: input` | Text input unless sensitivity rules require a specialized control |
| `type: string_list` or `ui: tag_editor` | Tag editor or comma-separated list editor |
| `type: object` or `ui: key_value` | Small structured editor for safe key/value values |
| `type: secret_ref` or `ui: secret_ref_picker` | SecretRef picker backed by managed-secret metadata and supported SecretRef schemes |
| `ui: provider_profile_picker` | Provider profile selector backed by provider-profile list/readiness data |
| `ui: readonly`, `read_only: true`, or unsupported editable control | Read-only display with reason or unsupported-control diagnostic |

The renderer may have specialized components for control types. It should not have specialized components for individual setting keys unless explicitly approved as an exception.

### 8.3 Row layout

Every descriptor row should show:

- title;
- description;
- current control or read-only value;
- stable setting key;
- source badge;
- scope badge;
- active/pending state;
- default or inherited explanation when useful;
- reset action when an override exists and reset is allowed;
- validation errors;
- diagnostics;
- reload/restart/application badge when relevant;
- affected systems;
- lock reason when read-only; and
- audit/change-history link where authorized.

Recommended row structure:

```text
SettingRow
  Title + source/scope/application badges
  Description
  Source explanation
  Diagnostics
  Affected systems chips
  Control
  Key + reset/history actions
```

### 8.4 Scope switching

The User / Workspace section should support scope switching between `workspace` and `user` where authorized.

Scope switching must:

- reload catalog/effective values for the selected scope;
- discard or clearly preserve unsaved draft changes only with explicit user intent;
- update source labels and reset behavior;
- make inheritance visible; and
- avoid presenting user-level controls that are disallowed by workspace policy.

### 8.5 Filtering and search

The generated settings surface should support:

- free-text search over key, title, description, category, and applicable tags;
- category filtering;
- modified-only filtering;
- read-only filtering;
- diagnostics/error filtering;
- source filtering where useful;
- pending reload/restart filtering; and
- secret-related filtering where authorized.

Filtering is a UI convenience. It must not be used as the security boundary for hiding unauthorized settings. Unauthorized settings should not be returned by the backend, or they should be returned only as read-only/limited metadata according to backend policy.

### 8.6 Change preview

Before saving one or more settings, the UI should show a change preview containing:

- changed keys;
- old effective values;
- new proposed values;
- validation status;
- source/scope that will be written;
- expected versions;
- affected systems;
- reload/restart/next-boundary requirements;
- missing dependency warnings; and
- audit redaction behavior where relevant.

Where a dedicated preview endpoint exists, the UI should use the backend preview rather than reconstructing full effective-value semantics locally.

Desired preview API:

```http
POST /api/v1/settings/preview
```

### 8.7 Save and reset

Saving generated settings uses scoped batch updates.

```http
PATCH /api/v1/settings/workspace
PATCH /api/v1/settings/user
```

The payload should include changed keys, expected versions, and an optional reason.

```json
{
  "changes": {
    "workflow.default_publish_mode": "branch",
    "skills.canary_percent": 25
  },
  "expected_versions": {
    "workflow.default_publish_mode": 3,
    "skills.canary_percent": 1
  },
  "reason": "Updated from Mission Control Settings."
}
```

Resetting removes the scoped override and returns to the inherited effective value.

```http
DELETE /api/v1/settings/workspace/{key}
DELETE /api/v1/settings/user/{key}
```

The UI must not treat reset as deleting the underlying default, managed secret, provider profile, OAuth volume, or audit history.

### 8.8 Draft state

The UI may keep unsaved changes in local state. Draft state must be keyed by setting key and scope.

Draft state should be cleared when:

- a successful save returns fresh values;
- the user discards changes;
- the selected scope changes and the user confirms discard; or
- the catalog version changes in a way that invalidates the draft.

Draft state should not be persisted to local storage unless a future design explicitly addresses sensitive metadata risk.

---

## 9. Providers & Secrets UI

Providers & Secrets is specialized, but it should still be data-driven.

### 9.1 Provider profiles

Provider Profiles are first-class resources. They are not generic setting overrides.

The Provider Profiles manager should load and render:

- profile id;
- runtime;
- provider;
- provider label;
- credential source class;
- runtime materialization mode;
- default model and model overrides;
- SecretRef role bindings;
- OAuth volume metadata;
- concurrency and cooldown policy;
- priority;
- default profile status;
- enabled/disabled status;
- readiness summary; and
- readiness checks.

Provider profile forms may be descriptor-assisted, but they should not reduce provider profiles to arbitrary key/value editing. Provider profiles carry execution semantics and require specialized validation and readiness display.

### 9.2 Secret role binding

When a provider profile requires a secret, the UI should present a role-aware SecretRef picker.

The picker should show:

- role label;
- required/optional status;
- compatible secret types or schemes;
- available managed secrets;
- external reference schemes such as `env://` where allowed;
- selected SecretRef;
- readiness result; and
- validation status.

The picker must not show secret plaintext.

### 9.3 Managed secrets

The Managed Secrets UI should let authorized users:

- create managed secrets;
- replace secret values;
- rotate secrets;
- disable secrets;
- re-enable secrets;
- delete or tombstone secrets where allowed;
- validate secrets;
- inspect usage; and
- copy SecretRefs.

Managed secret creation and replacement may accept plaintext only as a one-way submission. After save, the UI must clear plaintext inputs and display only metadata.

### 9.4 OAuth credential state

OAuth-backed profiles and credential volumes should show:

- connection status;
- account label;
- backing volume or reference metadata;
- validation timestamp;
- permitted actions;
- disconnect or reconnect actions where authorized;
- failure reason with sensitive details redacted; and
- launch readiness.

### 9.5 Readiness and diagnostics

Providers & Secrets should include a readiness-oriented summary because users visit this section to make runtimes launchable.

Readiness should combine:

- profile schema validity;
- required fields;
- SecretRef resolvability;
- managed secret status;
- OAuth status;
- provider-specific validation;
- enabled/disabled state;
- concurrency availability; and
- cooldown state.

Broken SecretRefs, missing OAuth volumes, disabled secrets, and blocked profiles should appear as clear actionable diagnostics.

---

## 10. Operations UI

Operations belongs under Settings for discoverability, but operational controls are not ordinary setting rows.

### 10.1 Operation cards

Operational controls should be represented as command cards or statusful control panels. Each card should load current state and permitted actions from backend operations APIs.

Each operation card should show:

- operation name;
- current state;
- command impact;
- allowed actions;
- disabled/read-only reason when not allowed;
- required confirmation;
- reason input when audit requires it;
- last action and actor;
- pending transitions;
- failure reason; and
- safe rollback or resume action where available.

### 10.2 Worker controls

Worker pause/resume, drain, and quiesce controls should show:

- whether workers are running, draining, quiesced, or paused;
- queue depth or related metrics where available;
- whether the system is drained;
- pause mode;
- reason;
- actor and timestamp of latest action;
- confirmation text; and
- resume behavior.

### 10.3 Deployment or runtime update controls

Deployment or runtime update controls should show:

- current configured image/version/build where applicable;
- running image evidence;
- target options loaded from backend policy;
- mutable tag warnings;
- affected services;
- update mode;
- confirmation requirements;
- recent actions;
- logs or run detail links where authorized; and
- rollback eligibility.

### 10.4 Operational audit

Operations cards should surface recent operational audit records in context. Sensitive command logs or raw output should be linked only when authorized and redacted according to backend policy.

---

## 11. Source, Inheritance, and Application Semantics

The Settings page should make source and application semantics visible without requiring users to understand the resolver internals.

### 11.1 Source badges

Recommended source labels:

```text
Default
Config
Environment
Workspace override
User override
Provider profile
Secret reference
Operator locked
Migrated override
Deprecated override
```

Source labels should come from backend source metadata, normalized for display. Unknown source values should be displayed safely rather than hidden.

### 11.2 Inheritance display

When a setting is inherited, the UI should explain the inherited source. Examples:

- `Using workspace default.`
- `Using deployment config.`
- `Using built-in default.`
- `Overridden for this user.`
- `Locked by operator policy.`

Reset controls should appear only when an override exists at the selected scope and the current user is authorized to remove it.

### 11.3 Application badges

Settings may apply at different boundaries. The UI should display backend-provided application semantics such as:

- applies immediately;
- applies on next request;
- applies on next task;
- applies on next launch;
- requires worker reload;
- requires process restart; or
- requires manual operation.

Pending values should be shown when a new value has been accepted but is not yet active.

---

## 12. Validation and Error Handling

### 12.1 Client-side validation

The frontend may perform lightweight validation using descriptor metadata:

- required value checks;
- numeric min/max;
- enum option membership;
- string length;
- simple patterns;
- SecretRef shape; and
- obvious type coercion errors.

Client-side validation is a convenience only. It is not authoritative.

### 12.2 Server-side validation

All writes must be validated by the backend using the authoritative setting descriptor, scope, constraints, sensitivity rules, version rules, and authorization policy.

The UI should display structured backend errors without exposing submitted plaintext or sensitive values.

Common settings errors include:

- `unknown_setting`;
- `setting_not_exposed`;
- `scope_not_allowed`;
- `read_only_setting`;
- `operator_locked`;
- `invalid_setting_value`;
- `secret_ref_not_resolvable`;
- `provider_profile_not_found`;
- `version_conflict`;
- `permission_denied`; and
- `requires_confirmation`.

### 12.3 Version conflicts

When a version conflict occurs, the UI should:

1. stop the save;
2. reload affected descriptors;
3. show the user that another change occurred;
4. allow the user to review the latest value; and
5. require explicit resubmission.

### 12.4 Diagnostics

Diagnostics should be shown close to the affected row or resource. Security-relevant diagnostics must avoid exposing raw secret values or sensitive payloads.

Diagnostics may include:

- unresolved SecretRef;
- missing managed secret;
- disabled or revoked secret;
- missing provider profile;
- deprecated setting key;
- migrated override;
- unsupported control;
- pending reload;
- failed validation; and
- permission limitation.

---

## 13. Secret-Safe Behavior

The Settings page must treat secrets as write-only or reference-only metadata.

### 13.1 Generic settings

Generic setting rows must not accept raw secret values. Secret-like settings must use `secret_ref_picker`, provider-profile secret-role binding, or a specialized managed-secret flow.

A SecretRef such as `db://github-token` or `env://GITHUB_TOKEN` may be displayed when authorized, but the referenced plaintext must not be displayed.

### 13.2 Managed secret plaintext

Managed secret creation and replacement flows may accept plaintext input only for submission. After the request completes, the UI must:

- clear the input;
- avoid caching the plaintext in component state longer than necessary;
- avoid placing plaintext in URLs, local storage, or logs;
- show metadata and validation state instead of the value; and
- rely on the backend for redaction and audit behavior.

### 13.3 Audit redaction

Audit views should use backend-redacted values. If the user lacks permission to view security-relevant metadata, the audit view should show redacted placeholders and redaction reasons instead of SecretRefs or values.

---

## 14. Permissions and Authorization UX

The backend is authoritative for authorization. The UI should reflect permission state without relying on frontend checks as the security boundary.

The Settings page should support separate permission states for:

- reading the settings catalog;
- reading effective settings;
- writing user settings;
- writing workspace settings;
- reading system/operator settings;
- reading secret metadata;
- writing or rotating secrets;
- managing provider profiles;
- invoking operations; and
- reading audit history.

When the user lacks permission, the UI should choose the safest available presentation:

| Backend state | UI behavior |
|---|---|
| Section not visible | Hide section or show unavailable shell according to product policy |
| Catalog readable but setting not writable | Render read-only row with reason |
| Secret metadata not readable | Hide SecretRef metadata or show redacted reference state |
| Audit not readable | Hide audit link |
| Operation not invokable | Show current state but disable action with reason |
| Backend returns permission error | Show structured error and do not retry destructive action automatically |

---

## 15. Loading, Empty, and Failure States

### 15.1 Loading states

Use skeletons or compact loading cards for:

- section shell loading;
- catalog loading;
- provider profile list loading;
- managed secret metadata loading;
- operation state loading;
- audit loading; and
- diagnostics loading.

Loading placeholders should not imply that values are default, unset, or safe.

### 15.2 Empty states

Empty states should be actionable:

- no provider profiles: explain how to create or import a profile;
- no managed secrets: explain SecretRefs and create-secret flow;
- no user/workspace settings returned: explain that no settings are exposed for this scope or the user lacks permission;
- no operations available: explain that operations are not configured or not authorized;
- no audit records: explain that no changes have been recorded.

### 15.3 Failure states

Failure states should identify the affected section or row and preserve unrelated content when possible.

Examples:

- settings catalog unavailable;
- settings persistence unavailable;
- provider profile service unavailable;
- managed secret metadata unavailable;
- operations state unavailable;
- audit unavailable; and
- diagnostics unavailable.

The UI should avoid automatic retries for mutation failures that might duplicate operations.

---

## 16. Extensibility Rules

### 16.1 Adding a normal user/workspace setting

A new ordinary setting should be added by changing backend metadata and validation.

Expected path:

1. backend defines stable setting key;
2. backend marks setting explicitly exposed;
3. backend declares title, description, category, section, scopes, type, UI control, constraints, options, sensitivity, application semantics, and audit policy;
4. backend wires persistence/resolution as needed;
5. frontend generic renderer displays it automatically;
6. tests verify that the catalog row appears and save/reset behavior works.

Frontend changes should be unnecessary unless the setting requires a new generic control type.

### 16.2 Adding a new generic control type

A new generic control type may require frontend work, but it should be reusable across many settings.

A new control type must define:

- descriptor `ui` value;
- supported `type` or value shape;
- validation hints;
- accessibility behavior;
- read-only behavior;
- draft serialization;
- display serialization;
- error display; and
- fallback behavior for unsupported browsers or permissions.

### 16.3 Adding provider/secret/operations capabilities

Provider, secret, OAuth, and operations capabilities may require specialized UI because they represent resources or commands. Even then, the frontend should load options, allowed actions, readiness, validation, and policy constraints from backend APIs rather than hard-coding policy in the UI.

### 16.4 Forbidden patterns

The following patterns are not allowed for ordinary settings:

- hard-coding a new setting row in `SettingsPage` for each setting key;
- duplicating backend defaults in the frontend;
- accepting raw API keys in a generic setting text input;
- using environment-variable editing as a generic browser UI;
- hiding unauthorized settings only through frontend filtering;
- implementing setting validation only in the frontend;
- silently falling back when a SecretRef is broken; or
- treating operations commands as simple boolean preferences.

---

## 17. Accessibility and Usability Requirements

The Settings page should support:

- keyboard navigation through section switcher, filters, rows, controls, and dialogs;
- clear focus management after save, reset, validation, and modal close;
- accessible names for all controls;
- screen-reader-visible descriptions for badges and diagnostics;
- form errors associated with the relevant controls;
- no reliance on color alone for source, status, warning, or error meaning;
- confirmation dialogs for high-risk operations;
- cancel/discard paths for unsaved drafts; and
- compact but readable displays for long keys, SecretRefs, and provider profile identifiers.

---

## 18. Observability and Audit UX

Settings should make change history discoverable without turning the page into an audit console.

Recommended behavior:

- row-level audit link where authorized;
- section-level recent changes summary;
- actor, timestamp, scope, key, reason, and redaction state;
- old/new values only when the backend permits display;
- affected systems and application mode where available;
- no plaintext secret display; and
- diagnostics link for migration or unresolved-reference issues.

Audit and diagnostics should be loaded on demand when they are not needed for initial rendering.

---

## 19. Acceptance Criteria

The Settings page design is satisfied when all of the following are true:

1. The canonical UI document is `docs/UI/SettingsPage.md`; the old `SettingsTab.md` path is removed or replaced by a short redirect note.
2. The page uses `/tasks/settings` with `section` query selection for Providers & Secrets, User / Workspace, and Operations.
3. The User / Workspace section renders ordinary settings from backend catalog descriptors.
4. Adding a new supported setting descriptor does not require a new hard-coded row in the frontend.
5. The frontend chooses generic controls from descriptor metadata such as `type`, `ui`, `options`, and `constraints`.
6. Effective values, defaults, sources, overrides, version information, diagnostics, and application semantics come from backend responses.
7. The UI supports scope switching between user and workspace where authorized.
8. The UI supports search, category filtering, modified/read-only/diagnostic filtering, change preview, save, discard, and reset.
9. Secret-like settings use SecretRef pickers or managed-secret flows, never generic plaintext settings inputs.
10. Provider Profiles and Managed Secrets remain first-class resource managers inside Providers & Secrets.
11. Operations controls remain explicit command cards with confirmation, status, authorization, and audit context.
12. Permissions are reflected in read-only/hidden/disabled states, but backend authorization remains authoritative.
13. Unknown or unsupported descriptor controls degrade safely rather than failing the page or exposing unsafe inputs.
14. Audit and diagnostics are available where authorized and are redacted according to backend policy.
15. The UI does not duplicate backend defaults, validation rules, source resolution, or secret-safety decisions.

---

## 20. Migration from `SettingsTab.md`

The old document name implies a narrower tab-level IA artifact. The updated design should use `SettingsPage.md` because Settings is now a full Mission Control page and configuration plane.

Recommended migration:

```bash
git mv docs/UI/SettingsTab.md docs/UI/SettingsPage.md
```

Then replace the file content with this document.

Any references to `docs/UI/SettingsTab.md` should be updated to `docs/UI/SettingsPage.md`. If external links need a transition period, leave a tiny `SettingsTab.md` stub that points to `SettingsPage.md`, but the canonical document should be `SettingsPage.md`.
