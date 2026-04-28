# Settings System

**Related design documents:** [SecretsSystem.md](./SecretsSystem.md), [ProviderProfiles.md](./ProviderProfiles.md), [OAuthTerminal.md](../ManagedAgents/OAuthTerminal.md), [ManagedAndExternalAgentExecutionModel.md](../Temporal/ManagedAndExternalAgentExecutionModel.md)

Status: **Desired-State Design**
Owners: MoonMind Engineering
Last Updated: 2026-04-27

> [!NOTE]
> This document defines the desired-state MoonMind Settings System.
> It is a declarative contract for how Mission Control exposes, validates, persists, resolves, audits, and safely applies user, workspace, provider, secret, and operational configuration.
>
> This document is not an implementation checklist. Rollout sequencing, migration tasks, and backlog tickets belong in MoonSpec artifacts, gitignored handoffs, or `docs/tmp/` implementation plans.

---

## 1. Summary

MoonMind needs a unified Settings System that gives users and operators a single, predictable place to configure the product without turning the UI into a manually maintained collection of one-off forms.

The Settings System should make Mission Control the human-facing configuration plane for:

- provider profiles,
- managed secrets,
- OAuth-backed credential volumes,
- user preferences,
- workspace defaults,
- runtime and workflow defaults,
- safe operator controls, and
- diagnostic configuration state.

The central design decision is:

> MoonMind settings are exposed through a **declarative settings catalog** and persisted as **scoped overrides**, while sensitive values are represented by **SecretRef bindings** rather than raw plaintext.

This means:

- settings are discoverable through a backend-owned registry,
- UI forms are generated from typed descriptors rather than duplicated frontend knowledge,
- every editable setting is validated server-side,
- effective values can be explained by source and scope,
- secrets are managed through the Secrets System and are never retrieved as plaintext by Settings UI,
- provider profiles remain the execution contract for runtimes and providers, and
- operator actions remain explicit, auditable, and authorization-gated.

The Settings System is therefore not merely a page. It is the durable contract that connects configuration metadata, scoped persistence, effective-value resolution, UI rendering, security boundaries, and runtime application behavior.

---

## 2. Document Boundaries

The Settings System depends on, but does not replace, adjacent MoonMind systems.

### 2.1 What the Settings System owns

The Settings System is the semantic owner of:

- Settings page navigation and section taxonomy
- settings catalog descriptors
- setting eligibility rules
- user/workspace/system scope semantics
- scoped override persistence
- effective-value resolution and source explanation
- UI control metadata for editable settings
- settings update APIs
- settings reset APIs
- server-side setting validation
- setting-change audit records
- settings read/write authorization boundaries
- settings change notifications and reload semantics
- integration points to Provider Profiles, Secrets, OAuth, and Operations

### 2.2 What the Secrets System owns

The Secrets System remains the semantic owner of:

- `SecretRef` semantics
- managed secret storage
- encryption-at-rest behavior
- root key custody
- supported secret backend classes
- secret resolution
- secret rotation and revocation
- secret-specific audit events
- plaintext redaction rules

The Settings System may expose secret management workflows and SecretRef pickers, but it must not redefine how secrets are stored, decrypted, resolved, or materialized.

### 2.3 What Provider Profiles own

Provider Profiles remain the semantic owner of:

- runtime selection
- provider selection
- credential source class
- profile-level routing metadata
- default model intent for a runtime/provider combination
- runtime materialization strategy
- launch shaping
- profile-level concurrency and cooldown policy

The Settings System may provide the page and forms used to view or edit Provider Profiles, but the Provider Profile contract remains authoritative for execution semantics.

### 2.4 What Operations own

Operational subsystems remain the semantic owner of:

- worker pause, drain, quiesce, and resume semantics
- runtime health state
- queue state
- scheduler state
- destructive or disruptive operational actions

The Settings System may expose operational controls, but it must treat them as explicit commands with status feedback, not ordinary preferences.

### 2.5 What runtime strategies own

Runtime strategies remain the semantic owner of:

- command construction
- environment shaping
- generated runtime files
- process launch behavior
- runtime-specific capability checks

Settings may influence runtime strategies through typed effective configuration, but they do not replace runtime-specific launch logic.

---

## 3. Goals

The Settings System must support all of the following:

1. **Unified configuration surface**
   - Mission Control exposes Providers & Secrets, User / Workspace settings, and Operations under one stable Settings page.

2. **Declarative catalog**
   - Editable settings are described by backend-owned descriptors with type, scope, category, validation, UI metadata, sensitivity, and reload behavior.

3. **Safe exhaustiveness**
   - Every setting that is intentionally eligible for UI adjustment can be surfaced without manually building a bespoke form.
   - Ineligible, secret, or operator-only settings remain hidden, read-only, or routed to a specialized manager.

4. **Scoped overrides**
   - User and workspace overrides are persisted separately from built-in defaults, config files, and environment variables.

5. **Explainable effective values**
   - The UI and API can explain whether a value came from a default, environment/config source, workspace override, user override, provider profile, or operator lock.

6. **Server-side validation**
   - All writes are validated on the backend using the authoritative type and constraint model.

7. **Secret-safe behavior**
   - Raw secrets are never stored in generic setting overrides and are never rendered back to the browser after submission.
   - Settings that need sensitive values use SecretRef pickers or the Managed Secrets UI.

8. **Provider-aware configuration**
   - Provider Profiles are configurable from Settings while preserving the separation between profile semantics and secret semantics.

9. **Operational safety**
   - Operational controls are discoverable from Settings but remain explicit, authorized, auditable actions.

10. **Local-first baseline**
    - A personal or local deployment can start with minimal external prerequisites, then configure secrets and settings through Mission Control.

11. **Auditable changes**
    - Operators can answer who changed what, when, at which scope, and with what effect, without exposing sensitive values.

12. **Runtime application semantics**
    - Settings clearly indicate whether changes apply immediately, on next run, after worker reload, or after process restart.

13. **Extensible contracts**
    - New settings, sections, scopes, UI controls, backends, and validators can be added without breaking existing settings data.

---

## 4. Non-Goals

This design does **not** attempt to:

- expose every backend field or Pydantic setting automatically,
- allow raw environment-variable editing from the browser,
- replace the Secrets System,
- reveal stored secrets to users or operators,
- replace Provider Profiles with generic settings,
- turn Operations into ordinary key/value preferences,
- guarantee every setting can be hot-reloaded,
- remove the need for runtime-specific strategy code,
- make MoonMind a general-purpose enterprise configuration management product,
- create a generic admin UI for every database table, or
- make the frontend the authority for validation or eligibility.

The Settings System is a typed, product-specific configuration plane, not a generic database editor and not a generic secret manager.

---

## 5. Core Principles

### 5.1 Backend-Owned Truth

The backend owns the settings catalog, setting types, validation rules, sensitivity rules, authorization rules, and effective-value resolution.

The frontend renders descriptors and submits user intent. It does not decide which backend settings are safe to expose.

### 5.2 Explicit Exposure

A setting is editable in the UI only if it is explicitly marked as exposed by the backend registry.

Sensitive or ambiguous fields must default to hidden.

### 5.3 References Over Secrets

Any setting that requires sensitive material must store a reference, not a raw value.

Examples:

```yaml
provider.github.token_ref: db://github-pat-main
provider.anthropic.api_key_ref: db://anthropic-team-api-key
```

Generic setting overrides must not contain:

- API keys,
- access tokens,
- refresh tokens,
- passwords,
- private keys,
- OAuth session state, or
- generated config containing credentials.

### 5.4 Scoped Overrides, Not Mutable Defaults

Defaults remain defaults. User and workspace changes are stored as overrides.

A reset operation removes an override and reverts to the inherited effective value.

### 5.5 Explainability

Every effective setting value must be explainable.

The system should answer:

- What is the effective value?
- Which scope supplied it?
- Is it inherited or overridden?
- Is it locked by operator policy?
- What is the default?
- Does it require reload or restart?
- Which dependent systems are affected?

### 5.6 Least Privilege

Reading settings metadata, editing personal preferences, editing workspace defaults, managing secrets, managing provider profiles, and invoking operations are separate permissions.

### 5.7 Fail Fast

Invalid settings, missing SecretRefs, broken provider profile bindings, locked values, and unsupported scopes must fail explicitly with actionable errors.

The system must not silently fall back to another sensitive source.

### 5.8 UI as a Safe Control Plane

The UI should guide users toward valid configuration, but backend validation and authorization remain authoritative.

### 5.9 Durable Contracts Over Ad Hoc Forms

The catalog contract should outlive individual UI components.

A setting descriptor should be usable by:

- the Settings page,
- API clients,
- CLI tooling,
- tests,
- diagnostics,
- onboarding flows, and
- documentation generators.

---

## 6. Settings Page Topology

The desired top-level Settings page contains three primary sections.

```text
Settings
  Providers & Secrets
  User / Workspace
  Operations
```

### 6.1 Providers & Secrets

This section contains configuration that makes runtimes launchable and provider access resolvable.

It includes:

- Provider Profiles
- Managed Secrets
- OAuth credential flows and OAuth volume status
- SecretRef usage and validation
- provider-profile readiness state
- runtime/provider binding diagnostics

This section should clearly communicate:

- provider profiles hold references and launch metadata,
- managed secrets hold encrypted values or external references,
- OAuth volumes hold runtime-specific credential state,
- raw secrets are not re-rendered after creation, and
- readiness is a product of profile validity plus secret/OAuth resolvability.

### 6.2 User / Workspace

This section contains schema-driven settings for user and workspace behavior.

It includes:

- user preferences,
- personal runtime defaults,
- workspace task defaults,
- workspace routing defaults,
- workspace feature flags,
- non-secret integration defaults,
- configurable policy knobs, and
- SecretRef bindings that are not provider-profile-specific.

This section is catalog-driven.

New eligible settings should land here by adding backend metadata and validation rather than writing an entirely new bespoke UI panel.

### 6.3 Operations

This section contains operational controls and status-backed administrative settings.

It includes:

- worker pause/resume controls,
- drain/quiesce controls,
- operational mode toggles,
- queue and runtime health summaries,
- maintenance-window controls, and
- safe diagnostic switches.

Operations controls are not ordinary preferences. They are explicit commands or statusful controls that must show current state, authorization, expected effect, and audit trail.

---

## 7. Key Concepts

### 7.1 Setting Key

A setting key is a stable dotted identifier.

Examples:

```text
workflow.default_task_runtime
workflow.default_publish_mode
skills.policy_mode
skills.canary_percent
integrations.github.token_ref
live_sessions.default_mode
operations.worker_pause_default_reason
```

Setting keys must be:

- stable across releases,
- unique within the catalog,
- safe for URLs and JSON payloads,
- independent of display labels, and
- never overloaded to mean different things in different scopes.

### 7.2 Setting Descriptor

A setting descriptor is backend-owned metadata describing a setting.

It defines:

- key,
- title,
- description,
- category,
- type,
- UI control,
- allowed scopes,
- default value,
- constraints,
- options,
- sensitivity,
- read-only status,
- source information,
- reload behavior,
- dependencies,
- validation hints, and
- audit behavior.

### 7.3 Scope

A scope defines where an override applies.

Supported desired-state scopes:

- `user`
- `workspace`
- `system`
- `operator`

Only `user` and `workspace` are normal editable scopes for most users.

`system` and `operator` are generally read-only, admin-only, or deployment-owned.

### 7.4 Override

An override is a persisted scoped value that intentionally differs from the inherited value.

Overrides are sparse. Absence of an override means “inherit.”

### 7.5 Effective Value

The effective value is the resolved value after applying defaults, environment/config, workspace overrides, user overrides, and operator locks according to the setting’s resolution policy.

### 7.6 Source

A source explains where a value came from.

Examples:

- `default`
- `config_file`
- `environment`
- `workspace_override`
- `user_override`
- `provider_profile`
- `secret_ref`
- `operator_lock`

### 7.7 Lock

A lock is an operator-enforced value that cannot be changed through the normal UI.

Locks may come from:

- environment variables,
- deployment config,
- system policy,
- administrative policy, or
- runtime safety constraints.

### 7.8 Eligibility

Eligibility determines whether a setting can appear in the catalog and whether it can be edited.

Eligibility is not the same as existence. A backend setting can exist without being eligible for UI editing.

### 7.9 SecretRef Setting

A SecretRef setting is a non-secret pointer to a secret managed elsewhere.

The setting value is a reference such as:

```text
db://github-pat-main
env://GITHUB_TOKEN
exec://onepassword/moonmind/github-token
```

The referenced secret value is not part of the setting override.

---

## 8. Settings Catalog Contract

### 8.1 Descriptor Shape

The desired-state descriptor shape is:

```yaml
SettingDescriptor:
  key: string
  title: string
  description: string | null
  category: string
  section: string                    # providers-secrets | user-workspace | operations
  type: string                       # boolean | string | integer | number | enum | string_list | object | secret_ref
  ui: string                         # toggle | input | number | select | tag_editor | key_value | secret_ref_picker | readonly
  scopes: [string]                   # user | workspace | system | operator
  default_value: any
  effective_value: any
  override_value: any | null
  source: string
  options: [SettingOption] | null
  constraints: SettingConstraints | null
  sensitive: boolean
  secret_role: string | null
  read_only: boolean
  read_only_reason: string | null
  requires_reload: boolean
  requires_worker_restart: boolean
  requires_process_restart: boolean
  applies_to: [string]
  depends_on: [SettingDependency]
  order: integer
  audit: SettingAuditPolicy
```

Example:

```yaml
key: workflow.default_task_runtime
title: Default Task Runtime
description: Runtime used when a task does not explicitly request one.
category: Workflow
section: user-workspace
type: enum
ui: select
scopes: [workspace]
default_value: codex_cli
effective_value: codex_cli
override_value: null
source: default
options:
  - value: codex_cli
    label: Codex CLI
  - value: claude_code
    label: Claude Code
sensitive: false
read_only: false
requires_reload: false
requires_worker_restart: false
requires_process_restart: false
applies_to: [task_creation, workflow_runtime]
order: 10
audit:
  store_old_value: true
  store_new_value: true
  redact: false
```

Example SecretRef setting:

```yaml
key: integrations.github.token_ref
title: GitHub Token
description: Secret reference used for GitHub API access.
category: Integrations
section: user-workspace
type: secret_ref
ui: secret_ref_picker
scopes: [user, workspace]
default_value: null
effective_value: db://github-pat-main
override_value: db://github-pat-main
source: workspace_override
secret_role: github_token
sensitive: false
read_only: false
requires_reload: false
requires_worker_restart: false
requires_process_restart: false
```

The SecretRef is not sensitive by itself in the same way as plaintext, but it is still security-relevant metadata and must be access controlled.

### 8.2 Descriptor Generation

Descriptors may be generated from typed backend configuration models, hand-authored registry entries, or a combination of both.

The desired-state registry should support:

- extracting type information from backend models,
- preserving descriptions,
- preserving enum values,
- preserving numeric bounds,
- preserving list and object constraints,
- adding MoonMind-specific UI metadata,
- adding security metadata,
- adding scope metadata,
- adding reload metadata, and
- adding authorization metadata.

### 8.3 Explicit Metadata

A setting must include explicit MoonMind metadata before it becomes UI-editable.

Example:

```python
json_schema_extra={
    "moonmind": {
        "expose": True,
        "section": "user-workspace",
        "category": "Workflow",
        "scopes": ["workspace"],
        "ui": "select",
        "requires_reload": False,
    }
}
```

Absence of explicit metadata means the field is not editable through Settings.

### 8.4 Catalog Stability

The catalog is a durable API contract.

Changes to descriptors should preserve:

- stable keys,
- stable option values,
- stable scope semantics,
- backwards-compatible validation where possible,
- clear migration behavior when a setting is renamed or removed, and
- diagnostic visibility for deprecated settings.

---

## 9. Eligibility Rules

### 9.1 Editable Setting Eligibility

A setting may be editable through User / Workspace if all of the following are true:

1. It has explicit `expose: true` metadata.
2. It is not plaintext-sensitive.
3. It has at least one editable scope.
4. It has a supported UI representation.
5. It can be validated server-side.
6. It does not require direct mutation of deployment environment variables.
7. It does not bypass a specialized subsystem such as Secrets or Provider Profiles.
8. Its change semantics are understood and documented.

### 9.2 Supported Generic UI Types

The generic settings renderer may support:

| Backend shape | UI control |
|---|---|
| `bool` | toggle |
| `str` | text input |
| bounded `int` / `float` | number input |
| enum / literal | select |
| `list[str]` | tag editor |
| `dict[str, str]` | key/value editor |
| SecretRef string | secret ref picker |
| read-only computed value | read-only field |

### 9.3 Ineligible Settings

A setting must not be exposed as an ordinary editable field if it is:

- a raw secret,
- a password,
- an access token,
- a refresh token,
- a private key,
- an OAuth state blob,
- a provider credential value,
- a complex deployment-only infrastructure setting,
- a setting that requires manual operator migration,
- a field owned by Provider Profiles,
- a field owned by Operations command semantics,
- a value that cannot be validated safely, or
- a value whose change impact cannot be explained.

### 9.4 Sensitive-Name Heuristics

The registry must reject or require manual review for fields whose key, alias, or description contains terms such as:

- `secret`
- `token`
- `password`
- `api_key`
- `apikey`
- `credential`
- `private_key`
- `refresh`
- `oauth`

A field matching these heuristics may still be represented as a SecretRef picker, but it must not be represented as a plaintext input unless it is inside the Managed Secrets creation or replacement flow.

---

## 10. Resolution Model

### 10.1 Default Resolution Chain

For ordinary user/workspace settings, the default resolution chain is:

```text
built-in default
  < config file / environment default
  < workspace override
  < user override
```

The first item is weakest; the last applicable item wins.

### 10.2 Operator-Locked Resolution Chain

For operator-locked settings, the resolution chain is:

```text
built-in default
  < workspace override
  < user override
  < operator lock
```

Operator locks win and make the field read-only for non-operator editors.

### 10.3 Provider Profile Resolution

Provider Profiles are not generic setting overrides. They are resources managed in the Providers & Secrets section.

A setting may select or reference a provider profile, but it must not inline provider profile semantics into a generic setting value.

Example:

```yaml
workflow.default_provider_profile_ref: claude_anthropic_api_team
```

### 10.4 SecretRef Resolution

SecretRef settings resolve only to references during settings resolution.

The plaintext value is resolved only by the Secrets System at controlled execution boundaries such as:

- provider-profile-backed launch,
- tool execution,
- integration validation,
- proxy-owned outbound calls, or
- explicit validation flows.

### 10.5 Missing Values

The resolver must distinguish:

- no default exists,
- inherited value is null,
- override is intentionally null,
- SecretRef exists but cannot resolve,
- provider profile reference is missing,
- value is blocked by policy, and
- value is invalid after migration.

These states should produce explicit diagnostics rather than silent fallback.

---

## 11. Persistence Model

### 11.1 Settings Overrides Table

Desired-state scoped override storage:

```sql
CREATE TABLE settings_overrides (
    id UUID PRIMARY KEY,
    scope TEXT NOT NULL,
    workspace_id UUID NULL,
    user_id UUID NULL,
    key TEXT NOT NULL,
    value_json JSONB NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    value_version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by UUID NULL,
    updated_by UUID NULL,
    UNIQUE (scope, workspace_id, user_id, key)
);
```

### 11.2 Settings Audit Table

Desired-state audit storage:

```sql
CREATE TABLE settings_audit_events (
    id UUID PRIMARY KEY,
    event_type TEXT NOT NULL,
    key TEXT NOT NULL,
    scope TEXT NOT NULL,
    workspace_id UUID NULL,
    user_id UUID NULL,
    actor_user_id UUID NULL,
    old_value_json JSONB NULL,
    new_value_json JSONB NULL,
    redacted BOOLEAN NOT NULL DEFAULT FALSE,
    reason TEXT NULL,
    request_id TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 11.3 Persistence Rules

Settings override rows may store:

- booleans,
- numbers,
- strings,
- enums,
- lists,
- small structured JSON values,
- SecretRef strings, and
- resource references.

Settings override rows must not store:

- raw secret values,
- OAuth session blobs,
- decrypted credential files,
- runtime-generated config containing secrets,
- large artifacts,
- workflow payloads, or
- operational command history beyond audit metadata.

### 11.4 Deletion and Reset

Resetting a setting deletes the relevant override row.

It does not delete:

- inherited defaults,
- provider profiles,
- managed secrets,
- OAuth volumes, or
- audit history.

---

## 12. API Contract

### 12.1 Catalog APIs

```http
GET /api/v1/settings/catalog?section=user-workspace&scope=workspace
GET /api/v1/settings/catalog?section=user-workspace&scope=user
GET /api/v1/settings/catalog?section=providers-secrets
GET /api/v1/settings/catalog?section=operations
```

The catalog response returns descriptors grouped into sections and categories.

### 12.2 Effective Settings APIs

```http
GET /api/v1/settings/effective?scope=user
GET /api/v1/settings/effective?scope=workspace
GET /api/v1/settings/effective/{key}?scope=workspace
```

These endpoints return resolved values with source explanations.

### 12.3 Update APIs

```http
PATCH /api/v1/settings/user
PATCH /api/v1/settings/workspace
```

Example payload:

```json
{
  "changes": {
    "workflow.default_task_runtime": "codex_cli",
    "skills.canary_percent": 25
  },
  "expected_versions": {
    "workflow.default_task_runtime": 3,
    "skills.canary_percent": 1
  },
  "reason": "Update default workflow behavior for current workspace."
}
```

The response returns the refreshed descriptors for affected settings.

### 12.4 Reset APIs

```http
DELETE /api/v1/settings/user/{key}
DELETE /api/v1/settings/workspace/{key}
```

Reset removes the override and returns the inherited effective value.

### 12.5 Validation APIs

```http
POST /api/v1/settings/validate
POST /api/v1/settings/preview
```

Validation checks proposed changes without committing them.

Preview returns effective-value changes, dependency warnings, and reload requirements.

### 12.6 Audit APIs

```http
GET /api/v1/settings/audit?key=workflow.default_task_runtime
GET /api/v1/settings/audit?scope=workspace
```

Audit APIs must redact sensitive or security-relevant values according to the descriptor’s audit policy.

### 12.7 Error Shape

Settings API errors should be structured.

```json
{
  "error": "invalid_setting_value",
  "message": "skills.canary_percent must be between 0 and 100.",
  "key": "skills.canary_percent",
  "scope": "workspace",
  "details": {
    "minimum": 0,
    "maximum": 100,
    "received": 101
  }
}
```

Common errors:

- `unknown_setting`
- `setting_not_exposed`
- `scope_not_allowed`
- `read_only_setting`
- `operator_locked`
- `invalid_setting_value`
- `secret_ref_not_resolvable`
- `provider_profile_not_found`
- `version_conflict`
- `permission_denied`
- `requires_confirmation`

---

## 13. UI Contract

### 13.1 Common UI Elements

Every settings row should show:

- title,
- description,
- current control or read-only value,
- source badge,
- scope badge,
- reset action when overridden,
- validation errors,
- reload/restart badge when relevant, and
- lock reason when read-only.

### 13.2 Page-Level UI Elements

Settings pages should support:

- search,
- category filtering,
- scope switching,
- modified-only filtering,
- read-only filtering,
- validation before save,
- save/discard controls,
- reset-to-inherited action,
- source explanation, and
- audit or change-history link where authorized.

### 13.3 Source Badges

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
```

### 13.4 Change Preview

Before committing multi-setting updates, the UI should be able to show:

- changed keys,
- old effective values,
- new effective values,
- validation status,
- affected subsystems,
- reload requirements, and
- warnings about missing dependencies.

### 13.5 SecretRef Picker

SecretRef settings use a picker rather than a plaintext input.

The picker should show:

- secret label,
- backend type,
- status,
- presence indicator,
- last updated time,
- validation status,
- usage count, and
- SecretRef value.

It should not show plaintext.

### 13.6 Managed Secret Editor

Managed secret creation and replacement flows may accept plaintext input, but only as a one-way submission.

After saving, the UI must clear the input and show only metadata.

### 13.7 Operations UI

Operational controls should show:

- current state,
- command impact,
- confirmation requirements,
- actor authorization,
- last action and actor,
- pending transitions,
- failure reason, and
- safe rollback or resume action where available.

---

## 14. Secrets Integration

The Settings System exposes secret management but does not own secret storage semantics.

### 14.1 Managed Secrets

The Settings page should let authorized users:

- create managed secrets,
- replace secret values,
- rotate secrets,
- disable secrets,
- re-enable secrets,
- delete or tombstone secrets,
- validate secrets,
- inspect usage, and
- copy SecretRefs.

### 14.2 No Plaintext Readback

Stored secret values are write-only from the Settings UI perspective.

There is no default “reveal secret” action.

### 14.3 Secret Validation

Secret validation should be provider- or integration-aware when possible.

Validation flow:

1. User requests validation.
2. Backend resolves secret in memory.
3. Backend performs a safe provider-specific or resolver-specific check.
4. Backend discards plaintext.
5. Backend stores redacted validation metadata.
6. UI receives status, timestamp, and redacted diagnostics.

### 14.4 Secret Usage

The Settings UI should show where a secret is referenced, including:

- provider profiles,
- setting overrides,
- tool bindings,
- integrations,
- scheduled task definitions, and
- runtime materialization templates.

Usage views must show references and object names, not plaintext.

### 14.5 Broken SecretRefs

If a setting or provider profile references a missing, disabled, or revoked secret, the UI must surface that state clearly and prevent affected launches where appropriate.

---

## 15. Provider Profiles Integration

Provider Profiles are first-class resources inside Providers & Secrets.

The Settings UI may create, update, delete, validate, enable, disable, and select defaults for provider profiles.

### 15.1 Profile Editing

Provider profile forms should be descriptor-assisted but not reduced to generic key/value editing.

Provider profiles contain structured execution semantics and therefore need specialized UI for:

- runtime,
- provider,
- credential source class,
- runtime materialization mode,
- default model,
- model overrides,
- SecretRef role bindings,
- OAuth volume metadata,
- concurrency,
- cooldown,
- tags,
- priority,
- default profile status, and
- readiness.

### 15.2 Secret Role Binding

When a provider profile requires a secret, the UI should present a role-aware SecretRef picker.

Example:

```yaml
secret_refs:
  anthropic_api_key: db://anthropic-team-api-key
```

The role name describes what the profile needs. The SecretRef identifies where the value can be resolved.

### 15.3 Readiness

Provider profile readiness should combine:

- profile schema validity,
- required fields,
- SecretRef resolvability,
- OAuth volume status,
- provider-specific validation status,
- enabled/disabled state,
- concurrency availability, and
- cooldown state where applicable.

### 15.4 Defaults and Routing

User / Workspace settings may select default provider profiles or provider selectors, but Provider Profiles remain the source of truth for launch semantics.

---

## 16. User / Workspace Settings

### 16.1 User Settings

User settings describe personal preferences and defaults.

Examples:

- preferred default runtime,
- personal profile selector,
- UI density,
- notification preferences,
- personal Git author defaults,
- personal integration SecretRef bindings, and
- personal task creation defaults.

User settings must not override workspace safety policies unless the specific setting explicitly allows it.

### 16.2 Workspace Settings

Workspace settings describe shared behavior for a workspace.

Examples:

- default task runtime,
- default publish mode,
- workspace provider routing defaults,
- skill policy mode,
- allowed skill list,
- canary percentages,
- task proposal defaults,
- live session defaults,
- workspace Git defaults,
- integration defaults, and
- policy constraints.

### 16.3 Workspace Policy Constraints

Workspace settings may define constraints on user settings.

Examples:

- allowed runtimes,
- allowed providers,
- maximum canary percentage,
- allowed publication modes,
- allowed SecretRef backends,
- allowed operations during maintenance mode.

### 16.4 Inheritance

User settings inherit workspace defaults unless explicitly overridden and permitted by policy.

The UI must make inheritance visible.

---

## 17. Operations Settings

Operations belong in Settings for discoverability, but they are not ordinary preferences.

### 17.1 Operational Controls

Examples:

- pause workers,
- resume workers,
- drain queue,
- quiesce runtime family,
- enable maintenance mode,
- disable launch scheduling,
- update operational reason text, and
- set temporary operational banners.

### 17.2 Command Semantics

Operational actions should be modeled as commands with:

- actor,
- target,
- requested state,
- reason,
- confirmation state,
- timestamp,
- idempotency key,
- audit event,
- result status, and
- rollback or resume path where possible.

### 17.3 Safety

Operational controls must avoid accidental disruptive changes.

The UI should require confirmation for actions that:

- stop active work,
- prevent new launches,
- affect all workers,
- affect all runtimes,
- delete data,
- revoke credentials, or
- change global routing behavior.

---

## 18. Validation Model

### 18.1 Backend Validation

All setting writes must be validated server-side.

Validation includes:

- key exists,
- key is exposed,
- scope is allowed,
- actor is authorized,
- value type is correct,
- enum value is allowed,
- numeric bounds are satisfied,
- string constraints are satisfied,
- list constraints are satisfied,
- object constraints are satisfied,
- SecretRef is syntactically valid,
- referenced resource exists where required,
- dependencies are satisfied, and
- workspace policy allows the value.

### 18.2 Cross-Setting Validation

Some settings are only valid in combination.

Examples:

- provider profile selector must reference an enabled profile,
- canary percentage must be zero if the feature is disabled,
- default runtime must be in the workspace allowed runtime list,
- SecretRef backend must be allowed by workspace policy,
- operational mode must not conflict with maintenance policy.

### 18.3 Validation Timing

Validation should occur:

- when catalog descriptors are generated,
- when a write request is received,
- before persistence,
- after persistence during effective-value preview,
- before launch or operation execution, and
- during diagnostic readiness checks.

---

## 19. Change Application Semantics

### 19.1 Apply Modes

Each setting must declare how changes apply.

Supported desired-state apply modes:

- `immediate`
- `next_request`
- `next_task`
- `next_launch`
- `worker_reload`
- `process_restart`
- `manual_operation`

### 19.2 Change Events

Committed setting changes should emit structured events.

Example:

```json
{
  "event_type": "setting_changed",
  "key": "workflow.default_task_runtime",
  "scope": "workspace",
  "source": "workspace_override",
  "apply_mode": "next_task",
  "actor_user_id": "...",
  "changed_at": "2026-04-27T00:00:00Z"
}
```

### 19.3 Reload Behavior

Consumers may subscribe to settings change events where appropriate.

Examples:

- Mission Control refreshes catalog state.
- Task creation uses updated defaults.
- Provider profile manager syncs profile-related changes.
- Workers reload non-disruptive settings.
- Operational controls update runtime status.

### 19.4 Restart Visibility

If a setting requires restart, the UI must show:

- current effective value,
- pending value if applicable,
- restart requirement,
- affected process or worker,
- whether the value is already active, and
- how to complete activation.

---

## 20. Authorization Model

### 20.1 Permissions

Desired-state permissions include:

- `settings.catalog.read`
- `settings.effective.read`
- `settings.user.write`
- `settings.workspace.write`
- `settings.system.read`
- `settings.system.write`
- `secrets.metadata.read`
- `secrets.value.write`
- `secrets.rotate`
- `secrets.disable`
- `secrets.delete`
- `provider_profiles.read`
- `provider_profiles.write`
- `operations.read`
- `operations.invoke`
- `settings.audit.read`

### 20.2 Role Examples

| Role | Capabilities |
|---|---|
| User | Read catalog, edit own user settings, view allowed inherited values |
| Workspace Admin | Edit workspace settings, manage workspace provider profiles and secrets |
| Secret Manager | Manage secret metadata and write secret values, without broad operations access |
| Operator | Invoke operational controls and inspect operational status |
| System Admin | Manage system/operator settings and locks |
| Auditor | Read settings and secret metadata audit logs without plaintext access |

### 20.3 Authorization Rules

Authorization must be checked on every write and every sensitive metadata read.

The frontend may hide unavailable controls, but hidden UI is not a security boundary.

---

## 21. Audit and Observability

### 21.1 Audit Events

Settings audit should record:

- setting key,
- scope,
- actor,
- old value where allowed,
- new value where allowed,
- redaction status,
- source IP or request ID where available,
- reason where supplied,
- validation outcome,
- apply mode, and
- affected systems.

### 21.2 Redaction

Audit events must redact:

- raw secret values,
- sensitive generated config,
- OAuth state,
- private keys,
- token-like values,
- provider-returned sensitive diagnostics, and
- any value whose descriptor marks audit redaction.

SecretRef values may be recorded when authorized by policy, but they should be treated as security-relevant metadata.

### 21.3 Diagnostics

The Settings UI should include diagnostics that answer:

- why is this setting read-only?
- why is this value effective?
- where did this value come from?
- what changed recently?
- why did validation fail?
- what needs restart?
- which provider profile or secret is missing?
- which setting is blocking launch readiness?

---

## 22. Security Requirements

The desired-state Settings System must satisfy all of the following:

1. Raw secrets are not stored in generic setting overrides.
2. Stored secrets are not rendered back to the browser after creation or replacement.
3. Secret-like backend fields are hidden unless explicitly represented as SecretRef settings or managed through the Managed Secrets UI.
4. The backend is authoritative for catalog generation, eligibility, validation, and authorization.
5. Unknown setting keys are rejected.
6. Client-supplied descriptor metadata is ignored for authorization and validation.
7. Operator-locked settings cannot be overwritten through ordinary user/workspace APIs.
8. Settings changes are audited with redaction according to descriptor policy.
9. Settings APIs enforce CSRF/session protections appropriate to MoonMind’s auth model.
10. Values are size-limited and schema-validated before persistence.
11. Object settings do not permit arbitrary executable code, templates, or commands unless explicitly owned by a specialized subsystem.
12. Operational commands require explicit authorization and confirmation where disruptive.
13. Secret validation returns redacted diagnostics only.
14. Provider profile materialization never stores resolved plaintext in settings rows.

---

## 23. Backup and Recovery

Settings overrides are ordinary application configuration data and should be included in database backups.

Backups may contain:

- setting keys,
- non-sensitive values,
- SecretRef values,
- resource references,
- audit records, and
- metadata.

Backups must not contain raw managed secret plaintext.

Restoring settings without restoring the corresponding secrets, OAuth volumes, or provider profiles may produce broken references. The UI must surface those broken references clearly.

---

## 24. Migration and Deprecation

### 24.1 Renaming Settings

Renaming a setting requires:

- a new descriptor key,
- a migration from old overrides to new overrides,
- a deprecation descriptor for the old key where helpful,
- audit visibility, and
- tests proving effective values are preserved.

### 24.2 Removing Settings

Removing a setting requires:

- rejecting new writes,
- preserving or migrating existing values,
- explaining deprecated values in diagnostics, and
- avoiding silent loss of operator intent.

### 24.3 Changing Types

Changing a setting type requires an explicit migration.

The resolver must not reinterpret existing JSON values ambiguously.

---

## 25. Testing Requirements

The desired-state Settings System should include tests for:

1. Catalog generation includes all explicitly exposed eligible settings.
2. Catalog generation excludes sensitive fields by default.
3. Secret-like fields are represented only as SecretRef pickers or specialized secret flows.
4. Unknown keys are rejected.
5. Invalid scopes are rejected.
6. Type validation works for booleans, strings, numbers, enums, lists, objects, and SecretRefs.
7. Numeric and string constraints are enforced.
8. Workspace overrides affect effective values.
9. User overrides inherit from and override workspace values where allowed.
10. Operator locks make settings read-only.
11. Reset removes overrides and restores inherited values.
12. Audit events are written for changes.
13. Audit values are redacted when required.
14. Version conflicts are detected.
15. Provider profile references are validated.
16. SecretRef references are validated without resolving plaintext into durable state.
17. UI renders each generic control type.
18. UI displays source badges and reset actions correctly.
19. Operations controls require proper authorization.
20. Snapshot tests detect accidental catalog drift.

---

## 26. Suggested Internal Components

The desired-state backend architecture includes:

```text
SettingsRegistry
  Owns descriptor registration and eligibility filtering.

SettingsCatalogBuilder
  Builds user/workspace/operations catalog responses.

SettingsOverrideStore
  Persists and retrieves scoped overrides.

SettingsResolver
  Resolves effective values and explains sources.

SettingsValidator
  Validates write payloads, dependencies, and policies.

SettingsAuditWriter
  Writes redacted audit events.

SettingsChangePublisher
  Emits change notifications to interested subsystems.

SettingsAuthorizationService
  Enforces scope and action permissions.
```

Frontend architecture includes:

```text
SettingsPage
  Owns section navigation.

SettingsCatalogSection
  Fetches catalog and groups settings.

SettingControlRenderer
  Renders generic controls by descriptor.ui.

SecretRefPicker
  Selects SecretRefs without exposing plaintext.

ManagedSecretsPanel
  Creates, replaces, rotates, disables, deletes, validates, and shows usage.

ProviderProfilesPanel
  Manages provider profile resources.

OperationsPanel
  Shows status-backed operational controls.
```

---

## 27. Example End-to-End Flows

### 27.1 Change Workspace Default Runtime

1. User opens Settings → User / Workspace → Workspace.
2. UI fetches catalog.
3. UI renders `workflow.default_task_runtime` as a select.
4. User selects `codex_cli`.
5. UI previews change.
6. Backend validates scope, permission, enum value, and policy.
7. Backend stores workspace override.
8. Backend writes audit event.
9. Backend emits change event.
10. UI refreshes descriptor showing source `workspace_override`.
11. Future task creation uses the new effective default.

### 27.2 Add GitHub Token

1. User opens Settings → Providers & Secrets → Managed Secrets.
2. User creates secret `github-pat-main` with plaintext value.
3. Backend encrypts and stores the value through the Secrets System.
4. UI clears plaintext input.
5. UI shows metadata and `db://github-pat-main`.
6. User opens User / Workspace integration setting.
7. User selects `db://github-pat-main` from SecretRef picker.
8. Backend stores only the SecretRef.
9. GitHub integration resolves the secret only when needed.

### 27.3 Reset User Override

1. User opens User / Workspace → User.
2. UI shows setting source `user_override`.
3. User clicks Reset.
4. Backend deletes user override row.
5. Resolver returns inherited workspace or default value.
6. UI shows source `workspace_override` or `default`.

### 27.4 Pause Workers

1. Operator opens Settings → Operations.
2. UI shows current worker state.
3. Operator chooses Pause Workers and provides reason.
4. Backend validates operation permission and confirmation.
5. Backend invokes operation command.
6. Operation subsystem records status.
7. Audit event records actor, reason, target, and result.
8. UI shows paused state and resume action.

---

## 28. Open Integration Points

The Settings System intentionally leaves room for:

- external configuration exporters,
- CLI settings management,
- generated documentation from descriptors,
- workspace templates,
- import/export of non-sensitive settings,
- policy-as-code for operator locks,
- stronger RBAC models,
- settings drift detection,
- multi-workspace inheritance,
- external secret managers,
- real-time settings update subscriptions, and
- richer provider-specific validation.

The durable contract is that all integrations must preserve descriptor-driven exposure, scoped overrides, server-side validation, auditability, and secret-safe behavior.

---

## 29. Desired-State Invariants

The Settings System is correct only if the following invariants hold:

1. A setting cannot be edited unless the backend catalog explicitly exposes it.
2. A setting cannot be written at a scope not declared by its descriptor.
3. A setting cannot bypass backend validation.
4. A setting cannot store raw secret plaintext in a generic override.
5. A stored secret cannot be retrieved as plaintext by the Settings UI.
6. A SecretRef can be selected, validated, and audited without revealing the referenced value.
7. A provider profile can reference secrets without embedding them.
8. An effective value can always explain its source.
9. A reset returns to inheritance rather than mutating defaults.
10. An operator lock cannot be overwritten by ordinary user/workspace writes.
11. Operational commands are authorization-gated and audited.
12. Catalog changes are testable and intentional.

