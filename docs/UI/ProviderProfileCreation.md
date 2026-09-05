# Provider Profile Creation

Status: **Desired-state UI and implementation contract**  
Owners: MoonMind Engineering  
Last updated: 2026-09-05

Canonical for: the create and edit experience for Provider Profiles on the **Providers & Secrets** Settings page

**Related design documents:** [Settings Configuration Pages](./SettingsPage.md), [Provider Profiles](../Security/ProviderProfiles.md), [Provider Profile Model and Effort Tier Settings](./ProviderProfileModelEffortTierSettings.md), [Secrets System](../Security/SecretsSystem.md), [OAuth Terminal](../ManagedAgents/OAuthTerminal.md), [Dashboard Design System](./DashboardDesignSystem.md)

> [!NOTE]
> This document simplifies the Provider Profile form through progressive disclosure. It does not weaken the Provider Profile execution contract. The backend remains authoritative for validated creation presets, validation, credential activation, runtime materialization, and launch readiness.

---

## Profile identity and advanced behavior

Settings and workflow authoring use the label **Profile**. Workflow creation,
schedules, step overrides and replacement selections use the same persisted
Profile id. Runtime and host details are progressively disclosed. Advanced Profile
settings may pin an immutable execution configuration; the ordinary path resolves
compatible deployment behavior automatically. Model discovery is an advisory cache:
cached choices and explicit custom entries remain usable during refresh, while
the execution host validates the actual selected model before work starts.

## 1. Purpose

Creating a Provider Profile should ask users for decisions they understand and are likely to change. It should not require them to review every launch-contract field before they can create a normal profile.

Low-level credential bindings, volume metadata, rate-limit behavior, routing metadata, and launch-shaping data must not dominate the primary identity and model choices. Most users should not need those fields for a supported runtime and provider combination.

The primary product decision is:

> Provider Profile creation uses a focused standard form with a collapsed **Show advanced options** checkbox. Credential implementation details, volume metadata, secondary rate-limit controls, routing metadata, and launch-shaping fields stay behind that control. **Max parallel runs** remains visible in the standard form.

This decision applies most strongly to creation. Editing must still make existing non-default or invalid advanced configuration easy to discover.

The single form is the product foundation, not an interim step toward a separate wizard. Refinements improve identity selection, action wording, and truthful summaries within that form. They do not require another route, a second creation flow, a new profile type, or replacement of credential enrollment and backend policy.

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
8. protect existing advanced values during edit, collapse, validation failure, and save;
9. fail closed when no valid creation preset exists for a runtime, provider, and authentication combination;
10. let users select supported runtimes and providers without memorizing internal IDs; and
11. distinguish recommended policy, unsaved overrides, credential connection, and launch readiness in the language shown to users.

---

## 3. Non-Goals

This design does not:

- introduce a separate multi-page onboarding wizard or parallel profile-creation route;
- require a new persisted catalog, display-name field, or credential lifecycle to improve the form;
- make all Provider Profile fields optional in the backend contract;
- let the browser invent provider-specific materialization policy;
- hide credential connection status or the action needed to connect a provider;
- store raw credentials in the Provider Profile form;
- treat a missing validated preset as permission to guess `secret_ref` plus `api_key_env`;
- move model and effort resolution authority into the browser;
- make `clear_env_keys` a casual user-authored preference; or
- remove advanced fields from the edit or inspection experience.

---

## 4. Standard Creation Flow

The standard form should read as a short sequence of user decisions.

```text
Create Profile

Identity
  Runtime                 Select a supported runtime
  Provider                Select a provider for that runtime
  Account label           Optional
  Profile ID              Suggested, editable before creation

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

`profile_id` is durable and normally immutable after creation. The UI suggests an ID from the selected canonical runtime and provider IDs plus the optional account label. The field remains visible and editable before create so the user can review the permanent identity. Help text explains that the ID cannot be renamed after creation.

The suggestion follows the backend's existing format and length constraints. It is a convenience, not an identity reservation or a uniqueness guarantee. Empty or unusable account-label text still permits a suggestion from runtime and provider. The browser does not invent a second validation contract.

While the ID remains suggestion-managed, changing its source fields may update it. After the user edits the ID, including clearing it, source changes, capability refreshes, and late responses must not overwrite it. An explicit `Use suggested ID` action may resume suggestion-managed behavior. This distinction is local draft state and is not stored on the profile.

A uniqueness or format conflict preserves the whole draft and identifies the Profile ID field. A revised suggestion requires user review before submission. The client must not silently suffix an ID and retry creation. Only the backend validates and persists the final identity. OAuth account discovery after creation never renames the saved profile.

A separate display-name field is not required for this behavior. Until such a contract exists, the form does not silently hide an immutable generated identifier.

### 4.2 Runtime and provider

Runtime and provider remain visible and required. They determine the available authentication methods, model capabilities, creation preset, and approved materialization strategy.

The standard path uses accessible selectors labeled `Runtime` and `Provider`. Choices have backend-provided display labels and canonical IDs as submitted values. Provider choices are scoped to the selected runtime. Display labels do not become identifiers or compatibility rules.

The choice source reuses the trusted runtime/provider registries and creation-capability authority. If existing read APIs do not enumerate the choices, a small read-only projection of that authority supplies them. It is not a separately maintained catalog, a browser constant list, or an inference from existing profiles. Creating the first profile must work when no profiles exist. A temporary absence of launch-ready hosts must not erase an otherwise supported configuration choice.

Provider Profiles retain their underlying runtime ownership, such as `codex_cli`, `claude_code`, or `opencode`. An Omnigent execution target is not a Provider Profile runtime, and its product display label must not be submitted as `runtime_id=omnigent`.

Changing runtime retains a provider only when the new authoritative choice set confirms compatibility. Otherwise the provider selection is cleared and requires a new selection. Changing runtime or provider invalidates dependent authentication and preset selections until metadata for the new identity is available. Requests and caches include the relevant canonical identity, and stale responses cannot restore a previous selection or authorize submission. User-authored IDs, account labels, and unrelated draft values remain intact. Incompatible model or advanced overrides remain visible for review rather than being silently rewritten into a new launch contract.

A Settings runtime filter may seed creation when its runtime is a permitted creation choice. It does not change the immutable runtime of a saved profile. No runtime/provider change silently selects a different credential method or billing route for an existing explicit choice.

Loading, empty, denied, and failed-catalog states explain the affected control and any permitted retry. Failure preserves the draft and does not enable arbitrary text entry as an implicit fallback. A custom/manual path is available only when the backend explicitly advertises that capability. Unknown saved values remain inspectable during edit and may round-trip only when backend policy permits. A catalog outage alone must not erase them or prevent unrelated edits that the existing save contract permits.

### 4.3 Account label

`account_label` remains a visible optional field in Identity.

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

### 4.9 Save actions and outcomes

The primary action describes the operation that this submission will actually perform:

| State | Primary action and explanation |
|---|---|
| Supported creation with a guided OAuth or API-key setup continuation | `Create and connect`. Explain that the profile is saved first and connection continues in the existing credential flow. |
| Supported creation with no credential setup continuation, including an approved credential-free path | `Create profile`. Do not promise readiness before the backend returns it. |
| Explicitly supported manual creation whose outcome is a disabled profile | `Save disabled profile`. Explain the required later setup. Do not offer this as a way around an unsupported combination. |
| Editing an existing profile | `Update provider profile`. Credential lifecycle actions remain separate. |
| Required choices, permissions, capabilities, or a valid preset are unavailable | A disabled creation action with the specific missing requirement or retry guidance. Do not imply that connection can proceed. |
| A save is in progress | A disabled progress action such as `Creating profile...` or `Saving changes...`. Repeated clicks cannot issue another submission. |

Action selection uses the same current backend capabilities, preset, manual-path permission, and submitted draft snapshot as the save and post-save continuation. Checking only whether the selected authentication method is named OAuth or API key is insufficient. The UI must have a supported continuation for that exact combination.

After save, the normalized server response owns identity, activation, and readiness. The label is not evidence that enrollment succeeded. If creation succeeded but connection is pending, canceled, or failed, the UI identifies the saved profile and offers the existing connect/retry action rather than inviting the user to create it again. A saved-profile success notice must not claim `Connected` or `Ready` without the corresponding backend result. Runtime-default intent remains subject to section 4.7.

Ordinary supporting copy describes user outcomes, for example `Recommended settings loaded` or `Connect this account to finish setup`. Preset versions, omission mechanics, and materialization terminology belong in the advanced diagnostic view rather than the primary success message. Error text remains actionable and does not discard useful diagnostics.

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

- start collapsed when every advanced value is derived or confirmed equal to the active recommendation and no advanced diagnostic requires attention;
- start expanded when any advanced value is non-default, unknown, incompatible, or has a warning or error;
- automatically expand when server validation targets a hidden control; and
- preserve the user's explicit open or closed choice for the rest of the current edit session, except when validation must reveal an affected control.

Missing comparison metadata is unknown, not proof of recommended configuration. A background refresh does not repeatedly undo a user's disclosure choice.

### 5.3 Collapsed summary

The collapsed control summarizes the effective advanced draft policy without requiring expansion. It uses the same typed values, explicit-versus-inherited provenance, backend preset identity, and diagnostics used by the form and payload builder. Merely finding a supported authentication capability does not establish that the draft uses recommended settings.

| Comparison state | Summary behavior |
|---|---|
| A matching validated recommendation is available, all relevant values are understood, and none differ | `Using recommended <runtime> + <provider> settings`. Optional details include only confirmed effective values. |
| Known user overrides differ from the recommendation | `N advanced overrides`, followed by bounded field labels such as `Custom cooldown and priority`. |
| Values are unknown, incompatible, invalid, or associated with an advanced warning/error | `Advanced settings need attention`, followed by a safe diagnostic. A known override count may supplement but never replace the warning. |
| Recommendation metadata is loading, unavailable, or no longer matches the selected identity | `Recommended settings unavailable` or an explicit loading message. Existing profiles may add `Preserving existing settings`; do not assert zero overrides or recommended status. |

Example when comparison is complete:

```text
Using recommended Codex + OpenAI settings
API key environment · 300 second backoff · priority 100
```

Example with custom draft values:

```text
2 advanced overrides
Custom cooldown and priority
Unsaved advanced changes
```

Count each differing top-level advanced control once, not its nested JSON keys. Equality follows backend-normalized field semantics, including the distinction between inheritance and explicit empty, false, or zero values. The browser may compare normalized metadata, but must not implement an independent materialization or security policy. If a field cannot be compared reliably, report that uncertainty rather than guessing.

Expected system-generated values, such as an enrollment-owned OAuth volume, derived environment isolation, or system tags, are not user overrides merely because a create-time field was empty. Their source must be known before classifying them. User routing tags remain distinct from system tags.

The summary updates after draft edits, collapse, reset preview application, discard, normalized save, and relevant preset/profile refresh. Opening a reset preview alone does not change the summary. Unsaved differences are labeled as draft changes, not as already persisted policy. Summary computation and disclosure changes never mutate the draft or save payload.

Show only bounded, authorized labels and policy metadata. Do not dump command JSON, SecretRefs, credential volume names, host paths, or secret-bearing diagnostics into the collapsed summary. Connection and launch-readiness status remain separate and visible. Recommended configuration does not mean connected, enabled, or launch ready, and valid custom overrides do not by themselves mean unhealthy.

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
| `provider_id` | Visible required selector scoped to runtime | Backend provider catalog and creation capabilities |
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
| `clear_env_keys` | Do not expose as a normal editable advanced preference. Show backend-generated values as read-only launch-security metadata. | Backend runtime and provider strategy. It may not be empty without weakening isolation for a known profile type. |

### 6.3 Fields that should not merely be hidden

Some values should be derived or system-managed rather than moved unchanged into a collapsed form.

#### Credential source and materialization

A user understands `OAuth` or `API key`. A user should not have to coordinate values such as `oauth_volume` plus `oauth_home`, or `secret_ref` plus `api_key_env`.

The backend creation preset owns those coherent combinations. An unsupported combination must be rejected rather than saved as a profile that fails later at launch.

#### Clear environment keys

`clear_env_keys` prevents credentials and provider settings from leaking across launch paths. It is correctness and security policy.

For known runtime and provider strategies, the backend must generate and validate it. The UI may expose a read-only list and its source. A future expert override requires explicit backend capability, validation, a warning, and audit. A freeform textarea is not the desired normal contract.

#### Enabled state

Enabled state follows credential activation and policy. Hiding a client-side `Enabled` checkbox while still submitting `true` would not simplify the experience without weakening activation controls. The create workflow does not submit unconditional enablement and lets the backend return activation state.

---

## 7. Default and Omission Rules

### 7.1 Prefer omission over React-owned defaults

Where the create API supports optional fields, the browser should omit untouched advanced values. The backend applies a runtime, provider, and authentication-specific preset and returns the normalized profile.

The frontend may display a suggested value before save, but it must retain the value's source and preset identity. It must not silently convert every visual suggestion into a client-owned explicit override.

### 7.2 Creation preset

The backend should expose enough metadata for the UI to render the standard form and advanced summary securely and predictably.

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

### 7.3 No validated preset

When no validated preset exists for the selected runtime, provider, and authentication method, the UI must not guess.

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

An existing profile with advanced configuration shows a compact summary beside the checkbox before expansion. The comparison and uncertainty rules in section 5.3 apply equally to saved profiles and unsaved edits.

Examples:

- `Custom cooldown and priority`
- `OAuth volume generated by enrollment`
- `3 routing tags and custom command behavior`
- `Launch-safety policy needs attention`

Generated enrollment metadata describes its source without being counted as a custom override. A recommended-policy summary is never a substitute for credential or launch-readiness status.

### 9.2 Unknown values

Unknown existing values remain visible and round-trippable when backend policy permits. The UI does not erase them merely because the current creation preset no longer advertises them.

Unknown or stale values start the advanced region expanded and receive a diagnostic.

### 9.3 Reset to recommended

`Reset advanced options to recommended` must preview every change before applying it to the draft.

The preview should distinguish:

- fields set to a concrete recommended value;
- fields changed from explicit override to inherited or omitted;
- generated security fields that will be recalculated; and
- changes that affect future launch selection or command construction.

A preview performs no mutation. Applying it changes the draft and summary only until save. Credential and volume changes that require a dedicated lifecycle action are identified, not performed implicitly by reset.

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
9. Preserve a user-authored Profile ID on validation conflicts and metadata refresh. No unreviewed ID substitution or automatic create retry is permitted.
10. Discard stale selection metadata as authority, not the user's unrelated draft work.
11. Distinguish a failed create from confirmed creation followed by incomplete enrollment. Resume setup for the confirmed saved profile rather than repeating creation.

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
- avoid relying on color alone;
- keep guided credential flows keyboard accessible and secret safe;
- label runtime/provider selectors and expose loading, unavailable, and invalid-selection states accessibly; and
- associate action explanations with the primary button and announce save/setup outcomes without moving focus unexpectedly.

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

These are responsibilities, not a requirement to split the existing form into every named component. Identity suggestions, action presentation, and advanced summaries can use small pure helpers around the existing form. They share the existing generated API types and normalized draft/preset semantics rather than introducing a second validation, enrollment, or persistence model.

---

## 14. Test Contract

### 14.1 Standard creation

- Advanced options are collapsed on create.
- Runtime, provider, account label, authentication, tier policy, max parallel runs, and runtime-default selection remain available.
- A validated preset supplies the advanced summary.
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
- A custom cooldown or priority remains labeled as an override after collapse and save.
- Matching, custom, warning, unknown, loading, unavailable, and mismatched-preset states have distinct truthful summaries.
- Generated OAuth volumes, isolation policy, and system tags do not become false user overrides.
- Explicit empty, false, zero, inherited values, and structured values follow backend normalization rather than truthiness or raw JSON string equality.
- Reset preview alone changes nothing. Apply, discard, save, and refresh update the summary without changing payload semantics.
- Recommended configuration with disconnected credentials does not render as launch ready. Valid custom configuration does not render as unhealthy solely because it is custom.
- Repeated refreshes preserve the user's disclosure choice unless a hidden validation error requires expansion.

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
- Unknown values remain round-trippable when backend policy permits.
- Reset to recommended previews all affected fields.
- Cancel restores the normalized server profile.
- Read-only users can inspect effective advanced policy without edit controls.

### 14.6 Conformance suite

The form contract is covered by tests in
`frontend/src/components/settings/providerProfileRedesignConformance.test.tsx`
and `frontend/src/components/settings/ProviderProfilesManager.test.tsx`.
Coverage must exercise the standard-creation matrix, progressive disclosure,
draft preservation, hidden-field validation focus, model-tier integration,
existing-profile compatibility, identity selection, and save/setup presentation.
Generated-contract guards reject a second hand-maintained creation-preset schema
in React. Backend tests cover any choice projection and its agreement with the
existing creation-capability and save authorities.

Test-file presence is not proof that every desired-state behavior is implemented
or passing. Component tests with mocked network responses do not establish
live provider enrollment or deployment readiness. Verification reports identify
which boundaries ran and which remain unverified.

Focused local command:

```bash
npm run ui:test:settings-redesign
```

### 14.7 Identity selection

- Supported creation works with an empty profile collection and no launch-ready host.
- Runtime/provider labels display correctly while payloads retain canonical IDs.
- Runtime changes clear incompatible provider/authentication choices and reject delayed old responses as authority.
- A Settings runtime filter seeds only a permitted creation runtime.
- Catalog failure preserves drafts, exposes retry, and never enables an unauthorized manual fallback.
- Unknown saved values remain inspectable and are not silently replaced.
- Generated IDs follow current server constraints and update only while suggestion-managed.
- User edits, explicit clearing, later account-label changes, metadata refresh, and OAuth identity discovery cannot overwrite or rename the user's ID.
- Duplicate-ID and invalid-ID responses preserve the draft and require a reviewed resubmission.
- Forged runtime/provider combinations are rejected at the API boundary regardless of selector filtering.

### 14.8 Save actions and outcomes

- Guided OAuth and API-key creation show `Create and connect` only when an actual supported continuation will run.
- Credential-free creation, explicitly supported disabled/manual creation, edit, blocked, and in-flight states follow section 4.9.
- The button and continuation use the same submitted identity/capability snapshot, including a selection change during a delayed response.
- A repeated click while saving does not duplicate creation or enrollment.
- Confirmed creation followed by canceled or failed enrollment retains the saved identity and offers setup recovery, not another create action for that profile.
- Notices distinguish saved, pending connection, connected, enabled, and launch-ready results from the server.
- Runtime-default intent is not applied before readiness permits it.
- Keyboard, accessible names/descriptions, focus, narrow screens, and secret-safe summaries are verified through the production form.

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
11. Clear environment keys are backend-generated launch-security metadata, not a normal freeform preference.
12. Untouched advanced values use backend presets or omission instead of duplicated React defaults.
13. A profile without required validated credentials is not silently created as enabled.
14. Existing non-default, invalid, or unknown advanced configuration is conspicuous during edit.
15. Collapsing advanced options never loses draft data.
16. Hidden validation errors automatically reveal the affected controls.
17. No secret plaintext appears in the profile payload, URL, summary, or saved UI state.
18. A missing validated creation preset never falls back to a guessed materialization contract.
19. Supported runtime/provider choices come from existing backend authority and remain usable for first-profile setup without memorized internal IDs.
20. A suggested Profile ID remains reviewable, respects user edits, and is never silently changed after a conflict or creation.
21. Primary action wording matches the permitted save/setup operation, and successful persistence is not confused with successful connection or readiness.
22. Collapsed summaries distinguish confirmed recommendations, custom overrides, and unknown or invalid state without changing the underlying draft.
23. These refinements retain one form and the existing credential, capability, validation, and persistence owners.

---

## 16. Decision Summary

- Provider Profile creation uses progressive disclosure within one form, not a separate wizard.
- The default form focuses on identity, authentication, model policy, capacity, and default-selection intent.
- Runtime and provider use backend-owned choices. Their display labels never replace canonical IDs.
- Profile ID suggestions are reviewable and stop changing automatically once the user edits them.
- `max_parallel_runs` remains visible.
- Cooldown and rate-limit policy remain behind advanced options.
- Raw credential bindings and volume metadata remain behind advanced options or are owned by guided setup.
- Credential source and materialization mode are derived from the selected setup method.
- Command behavior, tags, and priority remain advanced.
- Account label remains in the standard identity experience.
- Clear environment keys are backend-owned, read-only launch-security metadata.
- Enabled state follows credential activation and policy instead of a default-true creation checkbox.
- Backend presets and omission replace global frontend guesses.
- Save actions explain whether creation continues into connection. Backend responses alone establish the outcome.
- Advanced summaries distinguish recommended, custom, and uncertain state while keeping readiness separate.
