# Provider Profile Creation

Status: **Desired-state UI and implementation contract**  
Owners: MoonMind Engineering  
Last updated: 2026-08-28

Canonical for: the create and edit experience for Provider Profiles on the **Providers & Secrets** Settings page

**Related design documents:** [Settings Configuration Pages](./SettingsPage.md), [Provider Profiles](../Security/ProviderProfiles.md), [Provider Profile Model and Effort Tier Settings](./ProviderProfileModelEffortTierSettings.md), [Secrets System](../Security/SecretsSystem.md), [OAuth Terminal](../ManagedAgents/OAuthTerminal.md), [Dashboard Design System](./DashboardDesignSystem.md)

> [!NOTE]
> This document simplifies the Provider Profile form through progressive disclosure. It does not weaken the Provider Profile execution contract. The backend remains authoritative for safe creation presets, validation, credential activation, runtime materialization, and launch readiness.

---

## 1. Purpose

Creating a Provider Profile should ask users for decisions they understand and are likely to change. It should not require them to review every launch-contract field before they can create a normal profile.

The current form exposes low-level credential bindings, volume metadata, rate-limit behavior, routing metadata, and launch-shaping data beside the primary identity and model choices. Most users should not need those fields for a first-party or otherwise supported runtime and provider combination.

The primary product decision is:

> Provider Profile creation uses a focused standard form with a collapsed **Show advanced options** checkbox. Credential implementation details, volume metadata, secondary rate-limit controls, routing metadata, and launch-shaping fields stay behind that control. **Max parallel runs** remains visible in the standard form.

This decision applies most strongly to creation. Editing must still make existing non-default or invalid advanced configuration easy to discover.

---

## 2. Goals

The creation experience must:

1. make the common path understandable without Provider Profile implementation knowledge;
2. preserve access to every supported expert control;
3. use backend-provided defaults or omit optional values rather than copying policy into React;
4. keep credential setup visible while hiding raw credential plumbing;
5. keep `max_parallel_runs` visible because it is an understandable capacity decision;
6. avoid creating a profile that appears enabled when credential setup is incomplete;
7. expose model and effort tier policy as a core profile decision rather than burying it with launch internals;
8. protect existing advanced values during edit, collapse, validation failure, and save; and
9. fail safely when no valid creation preset exists for a runtime, provider, and authentication combination.

---

## 3. Non-Goals

This design does not:

- make all Provider Profile fields optional in the backend contract;
- let the browser invent provider-specific materialization policy;
- hide credential connection status or the action needed to connect a provider;
- store raw credentials in the Provider Profile form;
- treat a missing safe preset as permission to guess `secret_ref` plus `api_key_env`;
- move model and effort resolution authority into the browser;
- make `clear_env_keys` a casual user-authored preference; or
- remove advanced fields from the edit or inspection experience.

---

## 4. Standard Creation Flow

The standard form should read as a short sequence of user decisions.

```text
Create Provider Profile

Identity
  Profile ID
  Runtime
  Provider
  Account label

Authentication
  OAuth | API key | No credentials
  Connection or setup action

Model & effort tiers
  One runtime-default tier initially
  Customize tier policy

Capacity and selection
  Max parallel runs
  Use as runtime default

[ ] Show advanced options
    Credential bindings, volumes, rate limits, routing, and launch shaping

Create profile / Create and connect
```

### 4.1 Profile ID

`profile_id` is durable and normally immutable after creation. The UI should suggest an ID from runtime, provider, and account label, while keeping the field visible before create so the user can review the permanent identity.

A future separate display-name field may allow the technical ID to move behind advanced options. Until that contract exists, the form should not silently hide an immutable generated identifier.

### 4.2 Runtime and provider

Runtime and provider remain visible and required. They determine the available authentication methods, model capabilities, creation preset, and safe materialization strategy.

Use backend-provided choices rather than unrestricted text fields when the relevant catalog exists. Existing unknown values remain inspectable during edit.

### 4.3 Account label

`account_label` should move out of the current Advanced Options group and remain a visible optional field.

It is a user-facing identity aid, not a launch implementation detail. It becomes important when two profiles use the same runtime and provider but different accounts or credentials.

OAuth may populate the label from validated provider identity. API-key setup may leave it empty or let the user enter a friendly name such as `Team account` or `Personal account`.

### 4.4 Authentication

Credential setup remains visible in the standard flow. The UI presents a high-level method supported by the chosen runtime and provider:

- OAuth;
- API key; or
- No credentials, only when the backend explicitly allows it.

The high-level choice drives a guided setup flow. It does not expose raw `credential_source`, `runtime_materialization_mode`, `secret_refs`, `volume_ref`, or `volume_mount_path` as ordinary form fields.

### 4.5 Model and effort tiers

Model and effort tier policy remains outside the general advanced disclosure because it is a core Provider Profile policy.

A new profile begins with one tier whose model and effort use backend-reported runtime defaults. The user may leave that policy unchanged or customize the tier stack through the contract in [Provider Profile Model and Effort Tier Settings](./ProviderProfileModelEffortTierSettings.md).

The standard form must not restore the superseded `default_model` and `default_effort` fields.

### 4.6 Max parallel runs

`max_parallel_runs` remains visible.

It is the one Runtime Limits value most users can reason about directly. The initial value comes from a backend creation preset. The generic fallback is `1` only when the backend contract permits that fallback.

An OAuth home backed by an exclusive mutable identity may lock the value to `1` and explain why.

### 4.7 Runtime default selection

`is_default` may remain visible as `Use as runtime default` because it represents understandable selection intent.

The action must not make a disabled or non-launch-ready profile the runtime default. When creation includes credential setup, default assignment occurs only after readiness succeeds. Otherwise the request remains pending or the checkbox is unavailable with an explanation.

### 4.8 Enabled state

`enabled` is not a normal creation checkbox.

Desired behavior is:

- successful user-initiated OAuth or API-key setup creates or updates a connected profile and enables it when policy permits;
- a manually created profile without valid credentials is saved disabled;
- failed setup leaves the profile disabled with diagnostics; and
- explicit enable or disable remains an edit or collection action after creation.

The UI must not use `enabled: true` as an unconditional client-side form default.

---

## 5. Advanced Options Control

### 5.1 Control shape

Use one native checkbox labeled:

```text
Show advanced options
```

Supporting text should say:

```text
Credential bindings, volumes, rate limits, routing, and launch shaping
```

The checkbox controls one expandable region through `aria-controls`. Its checked state is local presentation state and is never persisted as Provider Profile data.

This is progressive disclosure inside one form. It is not a tab, route, modal workflow, or alternate profile type.

### 5.2 Initial state

On create, advanced options are collapsed by default.

On edit:

- start collapsed when every advanced value is empty, derived, or equal to the active creation preset;
- start expanded when any advanced value is non-default, unknown, incompatible, or has a warning or error;
- automatically expand when server validation targets a hidden control; and
- preserve the user's explicit open or closed choice for the rest of the current edit session.

### 5.3 Collapsed summary

The collapsed control should summarize effective advanced policy without requiring expansion.

Example:

```text
Using recommended Codex + OpenAI settings
API key environment · 300 second backoff · priority 100 · no custom volume
```

When advanced overrides exist:

```text
3 advanced overrides
Custom volume · cooldown 600 seconds · tags configured
```

The summary uses normalized backend metadata. It does not infer safety from blank browser fields.

### 5.4 Draft preservation

Collapsing the advanced region must not clear, normalize, or omit edited values from the draft.

Resetting an advanced field to its recommended value should be an explicit field action or an explicit `Reset advanced options to recommended` action with a preview.

---

## 6. Field Classification

### 6.1 Standard fields

| Field or concept | Standard behavior | Default authority |
|---|---|---|
| `profile_id` | Visible, suggested, reviewable, immutable after create | Frontend may suggest. Backend validates uniqueness and format. |
| `runtime_id` | Visible required selector | Backend runtime catalog |
| `provider_id` | Visible required selector | Backend provider catalog |
| `account_label` | Visible optional friendly label | Empty, OAuth identity, or user value |
| Authentication method | Visible guided choice | Backend capabilities for runtime and provider |
| `model_tiers` and `default_model_tier` | Visible core policy, initially one runtime-default tier | Backend tier capabilities and resolution |
| `max_parallel_runs` | Visible capacity input | Backend creation preset. Baseline may be `1`. |
| `is_default` | Visible optional intent when readiness permits | User choice plus backend normalization |
| Credential readiness | Visible status and setup action | Backend validation and activation state |

### 6.2 Advanced fields

| Current field | Advanced treatment | Recommended omitted or initial value |
|---|---|---|
| `provider_label` | Hide for catalog-backed providers. Allow an optional custom display override for unknown or custom providers. | `null`. Render the provider catalog label. |
| `credential_source` | Derived from the visible authentication method. Show as advanced launch-contract metadata. Permit an override only when the backend supports expert manual profiles. | Backend-derived. Never assume one global value. |
| `runtime_materialization_mode` | Derived from runtime, provider, and authentication method. Advanced override only for supported expert paths. | Backend-derived. Never assume one global value. |
| `cooldown_after_429_seconds` | Advanced rate-limit control | Omit and let the backend apply the creation preset. Generic contract default is `300`. |
| `rate_limit_policy` | Advanced rate-limit control | Omit and let the backend apply the creation preset. Generic contract default is `backoff`. |
| `secret_refs` | Advanced structured credential bindings. Use role-aware SecretRef pickers, not raw JSON as the primary UX. | Empty only when no credential has been connected. A profile with required missing refs remains disabled. |
| `volume_ref` | Advanced imported or externally managed OAuth volume binding. Normally generated by OAuth enrollment and read-only afterward. | `null` until a volume-backed setup succeeds. |
| `volume_mount_path` | Advanced runtime-derived volume metadata. Normally read-only when generated. | `null` without a volume. Otherwise backend runtime strategy. |
| `command_behavior` | Expert launch-shaping options | Omit or `null`, unless the backend strategy supplies required behavior. |
| User routing tags | Advanced routing metadata | Empty user tag list. Backend-owned system tags are derived separately. |
| `priority` | Advanced selector ordering | Omit. Backend default is `100`. |
| `clear_env_keys` | Do not expose as a normal editable advanced preference. Show backend-generated values as read-only launch-safety metadata. | Backend runtime and provider strategy. It may not be safely empty for a known profile type. |

### 6.3 Fields that should not merely be hidden

Some values should be derived or system-managed rather than moved unchanged into a collapsed form.

#### Credential source and materialization

A user understands `OAuth` or `API key`. A user should not have to coordinate values such as `oauth_volume` plus `oauth_home`, or `secret_ref` plus `api_key_env`.

The backend creation preset owns those coherent combinations. An unsupported combination must be rejected rather than saved as a profile that fails later at launch.

#### Clear environment keys

`clear_env_keys` prevents credentials and provider settings from leaking across launch paths. It is correctness and security policy.

For known runtime and provider strategies, the backend must generate and validate it. The UI may expose a read-only list and its source. A future expert override requires explicit backend capability, validation, a warning, and audit. A freeform textarea is not the desired normal contract.

#### Enabled state

Enabled state follows credential activation and policy. Hiding a client-side `Enabled` checkbox while still submitting `true` would not simplify the experience safely. The create workflow must stop submitting unconditional enablement and let the backend return activation state.

---

## 7. Default and Omission Rules

### 7.1 Prefer omission over React-owned defaults

Where the create API supports optional fields, the browser should omit untouched advanced values. The backend applies a runtime, provider, and authentication-specific preset and returns the normalized profile.

The frontend may display a suggested value before save, but it must retain the value's source and preset identity. It must not silently convert every visual suggestion into a client-owned explicit override.

### 7.2 Creation preset

The backend should expose enough metadata for the UI to render the standard form and advanced summary safely.

Conceptual response:

```yaml
ProviderProfileCreationPreset:
  version: string
  runtime_id: string
  provider_id: string
  authentication_method: oauth | api_key | none
  supported: bool
  fields:
    credential_source:
      value: oauth_volume
      source: runtime_provider_strategy
      editable: false
    runtime_materialization_mode:
      value: oauth_home
      source: runtime_provider_strategy
      editable: false
    max_parallel_runs:
      value: 1
      source: exclusive_oauth_identity
      editable: false
    cooldown_after_429_seconds:
      value: 300
      source: provider_default
      editable: true
    rate_limit_policy:
      value: backoff
      source: provider_default
      editable: true
    priority:
      value: 100
      source: system_default
      editable: true
    clear_env_keys:
      value: [OPENAI_API_KEY]
      source: runtime_provider_strategy
      editable: false
  diagnostics: []
```

The exact endpoint and schema may share an existing capabilities surface. The durable UI requirement is that the backend supplies coherent values, editability, source, lock reason, and diagnostics.

### 7.3 No safe preset

When no safe preset exists for the selected runtime, provider, and authentication method, the UI must not guess.

It should do one of the following based on backend capability:

1. automatically expand advanced options and identify the required expert fields;
2. offer an explicitly labeled manual profile path; or
3. block creation and explain that the combination is unsupported.

A generic `secret_ref` plus `api_key_env` fallback is not sufficient for every provider.

### 7.4 Expected baseline values

These are generic contract baselines, not universal frontend constants:

```yaml
max_parallel_runs: 1
cooldown_after_429_seconds: 300
rate_limit_policy: backoff
priority: 100
secret_refs: {}
volume_ref: null
volume_mount_path: null
command_behavior: null
user_tags: []
account_label: null
provider_label_override: null
```

`credential_source`, `runtime_materialization_mode`, `clear_env_keys`, model policy, system tags, and activation state require contextual backend ownership.

---

## 8. Credentials and Volumes

### 8.1 Keep connection UX visible

Collapsing Credentials & Volumes does not mean hiding whether a profile is connected.

The standard form shows:

- selected authentication method;
- connection state;
- account label when known;
- setup, reconnect, or replace action;
- last validation state where useful; and
- whether the profile will be enabled after save.

### 8.2 Hide low-level plumbing

The advanced region may show:

- credential source class;
- materialization mode;
- role-aware SecretRef bindings;
- imported or generated volume reference;
- volume mount path;
- validation diagnostics; and
- which values are derived or locked.

It never shows stored secret plaintext.

### 8.3 Guided API-key setup

The API-key flow should:

1. accept the key in a one-way credential dialog;
2. validate it through the provider-specific backend;
3. store it through the Secrets System;
4. bind the resulting SecretRef to the required role;
5. apply backend-owned materialization and environment-clearing policy;
6. return normalized readiness; and
7. clear plaintext input after submission.

### 8.4 Guided OAuth setup

The OAuth flow should own volume creation or registration, mount-path selection, validation, account identity, and activation. The normal form does not ask the user to type a volume name or runtime home path.

An advanced imported-volume workflow may exist where supported, but it must be explicitly distinct from normal OAuth enrollment.

---

## 9. Editing Existing Profiles

### 9.1 Advanced override summary

An existing profile with advanced configuration shows a compact summary beside the checkbox before expansion.

Examples:

- `Custom cooldown and priority`
- `OAuth volume generated by enrollment`
- `3 routing tags and custom command behavior`
- `Launch-safety policy needs attention`

### 9.2 Unknown values

Unknown existing values remain visible and round-trippable. The UI does not erase them merely because the current creation preset no longer advertises them.

Unknown or stale values start the advanced region expanded and receive a diagnostic.

### 9.3 Reset to recommended

`Reset advanced options to recommended` must preview every change before applying it to the draft.

The preview should distinguish:

- fields set to a concrete recommended value;
- fields changed from explicit override to inherited or omitted;
- generated security fields that will be recalculated; and
- changes that affect future launch selection or command construction.

### 9.4 Credential state

Credential replacement, reconnect, rotation, and disconnect remain dedicated actions. Collapsing advanced profile fields must not hide an invalid or disconnected credential state.

---

## 10. Validation and Failure Behavior

1. Validate standard and advanced fields through the same authoritative save path.
2. Automatically check `Show advanced options` when an error targets a hidden field.
3. Move focus to the first invalid hidden control after expansion.
4. Keep every draft value after save failure.
5. Do not mark a profile connected or enabled until backend activation succeeds.
6. Treat a preset-version conflict like any other stale policy conflict. Reload metadata and require review.
7. Show unsupported manual combinations before profile creation when possible.
8. Preserve unrelated Provider Profile and Managed Secret content when one creation request fails.

---

## 11. Accessibility

The form must:

- use a native checkbox for `Show advanced options`;
- connect the checkbox to the advanced region with `aria-controls`;
- expose checked and expanded state correctly;
- keep the supporting description associated with the checkbox;
- announce automatic expansion caused by validation;
- preserve logical focus order when the region opens;
- avoid removing focused content without moving focus safely;
- expose derived, locked, default, and override states in text;
- avoid relying on color alone; and
- keep guided credential flows keyboard accessible and secret safe.

---

## 12. Responsive Layout

On wide screens, standard identity and capacity controls may use compact grids. The advanced region may group related controls into:

```text
Credential implementation
Rate limiting
Routing and selection
Launch shaping and diagnostics
```

On narrow screens:

- every field stacks to one column;
- the checkbox and its summary remain visible before the advanced content;
- opening advanced options does not introduce horizontal scrolling;
- raw identifiers and SecretRefs wrap or scroll inside bounded code fields; and
- create and cancel actions remain reachable after a long advanced region.

---

## 13. Suggested Component Boundaries

```text
ProviderProfileForm
  ProviderProfileIdentityFields
  ProviderProfileAuthenticationSetup
  ProviderProfileTierSection
  ProviderProfileCapacityFields
  ProviderProfileDefaultSelection
  ProviderProfileAdvancedToggle
  ProviderProfileAdvancedSummary
  ProviderProfileAdvancedRegion
    CredentialImplementationFields
    RateLimitFields
    RoutingMetadataFields
    LaunchShapingFields
    LaunchSafetySummary
  ProviderProfileSaveActions
```

Suggested helpers:

```text
useProviderProfileCreationPreset
buildProviderProfileCreatePayload
advancedProfileOverrides
summarizeAdvancedProfilePolicy
validateProviderProfileDraft
```

`buildProviderProfileCreatePayload` should distinguish untouched inherited values from explicit overrides. It should not serialize every displayed recommendation as though the user authored it.

---

## 14. Test Contract

### 14.1 Standard creation

- Advanced options are collapsed on create.
- Runtime, provider, account label, authentication, tier policy, max parallel runs, and runtime-default selection remain available.
- A safe preset supplies the advanced summary.
- Untouched advanced values are omitted or submitted according to the backend contract.
- Profile creation never defaults to enabled when required credentials are missing.
- Successful guided credential setup enables the profile when policy permits.

### 14.2 Advanced control

- Checking the box reveals every supported advanced group.
- Unchecking it preserves draft values.
- The checkbox state is not persisted in the profile payload.
- Non-default edit state starts expanded.
- Hidden validation errors expand the region and receive focus.
- The collapsed summary reflects advanced overrides without exposing secrets.

### 14.3 Field defaults

- Max parallel runs uses the backend recommendation and remains visible.
- Exclusive OAuth identity locks max parallel runs to one.
- Cooldown inherits the backend preset when untouched.
- Rate-limit policy inherits the backend preset when untouched.
- Priority inherits `100` when the generic backend default applies.
- Empty volume fields remain null for non-volume profiles.
- Missing credential refs keep a credential-required profile disabled.
- Clear environment keys come from backend strategy rather than a browser constant.

### 14.4 Credential behavior

- OAuth setup does not ask for volume ref or mount path in the normal flow.
- API-key setup stores plaintext only through the one-way credential flow.
- The normal form does not expose raw SecretRef JSON.
- Advanced credential bindings show references and role metadata, never plaintext.
- Connection errors remain visible while advanced options are collapsed.

### 14.5 Editing

- Existing custom advanced values are preserved.
- Unknown values remain round-trippable.
- Reset to recommended previews all affected fields.
- Cancel restores the normalized server profile.
- Read-only users can inspect effective advanced policy without edit controls.

---

## 15. Acceptance Criteria

The design is correctly implemented when:

1. Provider Profile creation starts with advanced options collapsed.
2. One `Show advanced options` checkbox controls the low-level region.
3. The standard form keeps runtime, provider, account label, authentication, model tier policy, max parallel runs, and runtime-default intent visible.
4. Credentials remain easy to connect without exposing raw credential plumbing.
5. Credential source and materialization mode are backend-derived for guided paths.
6. Credentials & Volumes fields live behind advanced options or a dedicated guided credential action.
7. Cooldown and rate-limit policy live behind advanced options.
8. Max parallel runs remains outside advanced options.
9. Command behavior, routing tags, priority, and other current Advanced Options fields remain behind the control.
10. Account label is treated as a user-facing identity field rather than an advanced launch option.
11. Clear environment keys are backend-generated launch-safety metadata, not a normal freeform preference.
12. Untouched advanced values use backend presets or omission instead of duplicated React defaults.
13. A profile without required validated credentials is not silently created as enabled.
14. Existing non-default, invalid, or unknown advanced configuration is conspicuous during edit.
15. Collapsing advanced options never loses draft data.
16. Hidden validation errors automatically reveal the affected controls.
17. No secret plaintext appears in the profile payload, URL, summary, or saved UI state.
18. A missing safe creation preset never falls back to a guessed materialization contract.

---

## 16. Decision Summary

- Provider Profile creation uses progressive disclosure.
- The default form focuses on identity, authentication, model policy, capacity, and default-selection intent.
- `max_parallel_runs` remains visible.
- Cooldown and rate-limit policy move behind advanced options.
- Raw credential bindings and volume metadata move behind advanced options or are owned by guided setup.
- Credential source and materialization mode are derived from the selected setup method.
- Command behavior, tags, and priority remain advanced.
- Account label moves into the standard identity experience.
- Clear environment keys become backend-owned, read-only launch-safety metadata.
- Enabled state follows credential activation and policy instead of a default-true creation checkbox.
- Backend presets and omission replace global frontend guesses.
