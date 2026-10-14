# Provider Profile Creation

This document describes the create and edit experience for Provider Profiles on
the **Providers & Secrets** Settings page.

**Related design documents:** [Harness, Provider Profile, and Backend Selection](./HarnessProfileBackendSelection.md), [Settings Configuration Pages](./SettingsPage.md), [Provider Profiles](../Security/ProviderProfiles.md), [Provider Profile Model and Effort Tier Settings](./ProviderProfileModelEffortTierSettings.md), [Secrets System](../Security/SecretsSystem.md), [OAuth Terminal](../ManagedAgents/OAuthTerminal.md), [Dashboard Design System](./DashboardDesignSystem.md)

The form simplifies Provider Profile creation through progressive disclosure
without weakening the profile execution contract. The backend owns creation
presets, validation, credential activation, materialization, and launch
readiness. Profile fields, activation states, and credential flows are described
in [Provider Profiles](../Security/ProviderProfiles.md); the tier section is
described in
[Provider Profile Model and Effort Tier Settings](./ProviderProfileModelEffortTierSettings.md).
The form is implemented in
`frontend/src/components/settings/ProviderProfilesManager.tsx`.

---

## Profile identity and advanced behavior

Settings and workflow authoring use **Provider Profile** for the existing
connection and capacity identity; workflow creation, schedules, step overrides,
and replacement selections all use the same persisted profile ID. Settings
scopes profile setup to **Harness**, while **Backend** and host details are
progressively disclosed during workflow authoring, as described in the
[selection design](./HarnessProfileBackendSelection.md). Advanced settings may
pin an immutable execution configuration, which the shared selection owner
reconciles with the chosen Harness and Backend. Omission normally resolves
Omnigent and does not authorize a direct-launch fallback when compatibility or
setup is missing.

Model discovery is an advisory cache: cached choices and explicit custom entries
remain usable during refresh, and the execution host validates the selected model
before work starts.

---

## 1. Purpose

Creating a profile asks for decisions users understand and are likely to change,
not a review of every launch-contract field. Credential bindings, volume
metadata, rate-limit behavior, routing metadata, and launch shaping stay out of
the way of identity and model choices because most supported runtime/provider
combinations do not need them.

> Creation uses one focused form with a collapsed **Show advanced options**
> checkbox. Credential implementation details, volumes, secondary rate-limit
> controls, routing, and launch shaping sit behind it. **Max parallel runs**
> stays visible.

Editing still makes non-default or invalid advanced configuration easy to find.
The single form is the product shape, not a step toward a separate wizard: there
is no second route, creation flow, profile type, or replacement for credential
enrollment or backend policy.

The form aims to:

- make the common path understandable without implementation knowledge while
  keeping every supported expert control reachable;
- take defaults from the backend or omit optional values rather than copying
  policy into React;
- keep credential setup visible while hiding raw credential plumbing;
- never present a profile as enabled while credential setup is incomplete;
- treat model and effort tiers as core policy;
- protect existing advanced values through edit, collapse, validation failure,
  and save;
- fail closed when no validated preset exists, instead of guessing
  `secret_ref` plus `api_key_env`;
- let users pick Harnesses and Providers without memorizing internal IDs; and
- distinguish recommended policy, unsaved overrides, credential connection, and
  launch readiness in what users read.

The browser does not invent materialization policy, store raw credentials, make
`clear_env_keys` a casual preference, or remove advanced fields from editing and
inspection.

---

## 2. Standard Creation Flow

```text
Create Provider Profile

Identity
  Harness                 Select a supported Harness
  Provider                Select a Provider for that Harness
  Account label           Optional
  Profile ID              Suggested, editable before creation

Authentication
  OAuth | API key | No credentials
  Connection or setup action

Model & effort tiers
  One runtime-default tier initially

Capacity and selection
  Max parallel runs
  Use as Harness default

[ ] Show advanced options
    Credential bindings, volumes, rate limits, routing, and launch shaping

Create profile / Create and connect
```

### 2.1 Profile ID

`profile_id` is durable and immutable after creation. The form suggests one from
the canonical runtime and provider IDs plus the optional account label, following
the backend's format and the length limit reported by the choices endpoint. The
field stays visible and editable before creation, with help text saying it
cannot be renamed later, so the user reviews the permanent identity rather than
having it silently hidden. A suggestion is a convenience, not a reservation or a
uniqueness guarantee.

While the ID is suggestion-managed, changing its sources may update it. Once the
user edits it, including clearing it, source changes, capability refreshes, and
late responses no longer overwrite it; an explicit `Use suggested ID` resumes
suggestion. This is local draft state only.

A uniqueness or format conflict preserves the whole draft and points at the
Profile ID field. The client never silently suffixes the ID and retries; only the
backend validates and persists it. OAuth account discovery after creation never
renames a saved profile.

### 2.2 Harness and Provider

Harness and Provider are visible and required because they determine available
authentication methods, model capabilities, the creation preset, and the
materialization strategy. They are accessible selectors with backend-provided
labels and canonical IDs as submitted values; Provider choices are scoped to the
selected Harness, and the Harness control submits the existing `runtime_id`.

`GET /api/v1/provider-profiles/creation-choices` projects the existing
runtime/provider registries and creation-capability authority: it lists
Harnesses with their Provider choices, whether a Harness accepts a custom
provider ID, and the Profile ID length limit. It is not a separate catalog, a
browser constant list, or an inference from existing profiles, so creating the
first profile works with no profiles present, and a temporary lack of
launch-ready hosts does not remove a supported choice.

`runtime_id` holds Harness ownership (`codex_cli`, `claude_code`, `opencode`).
Omnigent is a Backend, never submitted as `runtime_id=omnigent`, and creation adds
no Backend selector.

Changing Harness keeps a Provider only when the new choice set confirms
compatibility; otherwise the Provider clears. Either change invalidates dependent
authentication and preset selections until metadata for the new identity
arrives. Requests and caches are keyed by canonical identity, so stale responses
cannot restore a previous selection or authorize submission. User-authored IDs,
labels, and unrelated values stay; incompatible model or advanced overrides stay
visible for review rather than being rewritten. A Settings Harness filter may
seed creation when its Harness is a permitted choice, and no change silently
selects a different credential method or billing route for an explicit choice.

Loading, empty, denied, and failed-catalog states name the affected control and
any retry. Failure preserves the draft and does not open arbitrary text entry as
a fallback; a manual path exists only when the backend advertises it. Unknown
saved values stay inspectable during edit and round-trip when backend policy
permits, and a catalog outage does not block unrelated permitted edits.

### 2.3 Account label

`account_label` is a visible optional identity aid, important when two profiles
share a runtime and provider with different accounts. OAuth may fill it from the
validated identity; API-key setup leaves it empty or takes a friendly name such
as `Team account`.

### 2.4 Authentication

The form offers the high-level methods the backend supports for the chosen
runtime and provider: OAuth, API key, or No credentials (only when explicitly
allowed). The choice drives a guided setup flow; raw `credential_source`,
`runtime_materialization_mode`, `secret_refs`, `volume_ref`, and
`volume_mount_path` are not ordinary fields.

### 2.5 Model and effort tiers

Tier policy sits outside the advanced disclosure because it is core profile
policy. A new profile starts with one tier using backend-reported runtime
defaults, customizable through the
[tier editor](./ProviderProfileModelEffortTierSettings.md). The form has no
separate default model or default effort fields.

### 2.6 Max parallel runs

`max_parallel_runs` stays visible as the capacity value most users can reason
about. Its initial value comes from the backend creation preset; the generic
fallback is `1` only where the contract permits it. An OAuth home backed by an
exclusive mutable identity may lock it to `1` with an explanation.

### 2.7 Harness default selection

`Use as Harness default` (`is_default`) is visible selection intent. It never
makes a disabled or not-launch-ready profile the default, stays scoped to
`runtime_id`, and does not select a Backend. When creation includes credential
setup, the default is assigned only after readiness succeeds; otherwise the
request stays pending or the checkbox is unavailable with an explanation.

### 2.8 Enabled state

`enabled` is not a creation checkbox. Successful user-initiated OAuth or API-key
setup enables the profile when policy permits; a profile created without valid
credentials is saved disabled; failed setup leaves it disabled with diagnostics;
explicit enable/disable is an edit or collection action. The form never submits
`enabled: true` as a default, because hiding a checkbox while still sending
`true` would weaken activation control rather than simplify it.

### 2.9 Save actions and outcomes

The primary action names what the submission will actually do:

| State | Action |
| --- | --- |
| Guided OAuth or API-key setup will continue after save | `Create and connect`; explains that the profile is saved first and connection continues in the credential flow |
| No setup continuation, including approved credential-free paths | `Create profile`; promises no readiness before the backend reports it |
| Explicitly supported manual creation resulting in a disabled profile | `Save disabled profile`; explains the later setup, and is not a way around an unsupported combination |
| Editing | `Update provider profile`; credential lifecycle actions stay separate |
| Required choices, permissions, capabilities, or preset unavailable | Disabled, naming the missing requirement or retry |
| Save in progress | Disabled progress label (`Creating profile...`); repeat clicks send nothing |

The action uses the same capabilities, preset, manual-path permission, and draft
snapshot as the save and its continuation; knowing only that the method is named
OAuth or API key is not enough.

After save, the normalized server response owns identity, activation, and
readiness; the button label is not evidence that enrollment succeeded. If
creation succeeded but connection is pending, cancelled, or failed, the UI names
the saved profile and offers connect/retry rather than another create. Success
notices never claim `Connected` or `Ready` without the backend result. Ordinary
copy describes outcomes (`Recommended settings loaded`, `Connect this account to
finish setup`); preset versions and materialization terms belong in the advanced
diagnostics.

---

## 3. Advanced Options Control

### 3.1 Shape

One native checkbox, `Show advanced options`, with the description "Credential
bindings, volumes, rate limits, routing, and launch shaping", controls one region
via `aria-controls`. Its state is presentation-only and never persisted. It is
disclosure inside one form, not a tab, route, modal flow, or profile type.

### 3.2 Initial state

Create starts collapsed. Edit starts collapsed when every advanced value is
derived or confirmed equal to the active recommendation with no diagnostics, and
expanded when any value is non-default, unknown, incompatible, or flagged. Server
validation targeting a hidden control expands it. Otherwise the user's open or
closed choice holds for the edit session, and background refreshes do not undo
it. Missing comparison metadata counts as unknown, not as proof of recommended
configuration.

### 3.3 Collapsed summary

The collapsed control summarizes the effective advanced draft using the same
typed values, provenance, preset identity, and diagnostics as the form and
payload builder. A supported authentication capability alone does not establish
that the draft uses recommended settings.

| Comparison | Summary |
| --- | --- |
| Validated recommendation available, all values understood, none differ | `Using recommended <runtime> + <provider> settings`, with only confirmed values |
| Known overrides differ | `N advanced overrides` plus bounded labels such as `Custom cooldown and priority` |
| Unknown, incompatible, invalid, or flagged values | `Advanced settings need attention` with a safe diagnostic |
| Recommendation loading, unavailable, or for another identity | `Recommended settings unavailable` or a loading message; existing profiles may add `Preserving existing settings` |

```text
Using recommended Codex + OpenAI settings
API key environment · 300 second backoff · priority 100
```

Each differing top-level control counts once, not each nested key. Equality
follows backend-normalized semantics, including the difference between
inheritance and explicit empty, false, or zero; the browser compares normalized
metadata but implements no independent materialization or security policy, and
reports uncertainty rather than guessing. System-generated values such as an
enrollment-owned OAuth volume, derived environment isolation, or system tags are
not user overrides; user routing tags remain distinct from system tags.

The summary updates after edits, collapse, applying or discarding a reset, save,
and relevant refreshes; opening a reset preview alone changes nothing. Unsaved
differences are labelled as draft changes. Computing the summary never mutates the
draft or payload. It shows only bounded, authorized labels — never command JSON,
SecretRefs, volume names, host paths, or secret-bearing diagnostics. Connection
and readiness are shown separately: recommended configuration does not mean
connected or launch-ready, and valid custom overrides do not mean unhealthy.

### 3.4 Draft preservation

Collapsing never clears, normalizes, or omits edited values. Returning a field
to its recommendation is an explicit field action or a previewed `Reset advanced
options to recommended`.

---

## 4. Field Classification

### 4.1 Standard fields

| Field | Behavior | Default authority |
| --- | --- | --- |
| `profile_id` | Suggested, reviewable, immutable after create | Frontend suggests; backend validates |
| `runtime_id` | Required Harness selector | Backend Harness registry |
| `provider_id` | Required Provider selector scoped to Harness | Backend provider catalog and creation capabilities |
| `account_label` | Optional friendly label | Empty, OAuth identity, or user |
| Authentication method | Guided choice | Backend capabilities |
| `model_tiers`, `default_model_tier` | Core policy, one runtime-default tier initially | Backend tier capabilities |
| `max_parallel_runs` | Capacity input | Backend preset; baseline `1` |
| `is_default` | Optional intent when readiness permits | User choice plus backend normalization |
| Credential readiness | Status and setup action | Backend validation and activation |

### 4.2 Advanced fields

| Field | Treatment | Untouched value |
| --- | --- | --- |
| `provider_label` | Hidden for catalog providers; optional display override for custom providers | `null`; catalog label rendered |
| `credential_source` | Derived from the authentication method; overridable only on backend-supported expert paths | Backend-derived |
| `runtime_materialization_mode` | Derived from runtime, provider, and method; same override rule | Backend-derived |
| `cooldown_after_429_seconds` | Rate-limit control | Omitted; preset applies (generic `300`) |
| `rate_limit_policy` | Rate-limit control | Omitted; preset applies (generic `backoff`) |
| `secret_refs` | Role-aware SecretRef pickers, not raw JSON | Empty until a credential connects; missing required refs keep the profile disabled |
| `volume_ref` | Imported or external OAuth volume; normally generated by enrollment and read-only | `null` until volume-backed setup succeeds |
| `volume_mount_path` | Runtime-derived, normally read-only | `null` without a volume |
| `command_behavior` | Expert launch shaping | Omitted unless the strategy requires it |
| User routing tags | Routing metadata | Empty; system tags derived separately |
| `priority` | Selector ordering | Omitted; backend `100` |
| `clear_env_keys` | Read-only launch-security metadata | Backend strategy |

Some values are derived rather than merely hidden. Users understand "OAuth" or
"API key", not pairing `oauth_volume` with `oauth_home` or `secret_ref` with
`api_key_env`; the backend preset owns those coherent combinations, and an
unsupported combination is rejected rather than saved to fail at launch.
`clear_env_keys` keeps credentials and provider settings from leaking across
launch paths, so for known strategies the backend generates and validates it and
the UI shows it read-only with its source; a freeform textarea would let a known
profile type weaken isolation.

---

## 5. Defaults and Omission

The browser omits untouched advanced values where the create API allows, and the
backend applies a runtime-, provider-, and method-specific preset before
returning the normalized profile. The form may display a suggested value but
keeps its source and preset identity and never converts a suggestion into a
client-authored override.

`GET /api/v1/provider-profiles/creation-preset?runtime_id=…&provider_id=…&authentication_method=…`
returns the versioned preset: for each relevant field its value, source (for
example `runtime_provider_strategy`, `exclusive_oauth_identity`,
`provider_default`, `system_default`), editability, and lock reason, plus
diagnostics and whether the combination is supported.

When no validated preset exists, the form does not guess. Depending on backend
capability it expands advanced options and identifies required expert fields,
offers an explicitly labelled manual path, or blocks creation as unsupported. A
generic `secret_ref` plus `api_key_env` fallback is not correct for every
provider.

Generic contract baselines — `max_parallel_runs: 1`, cooldown `300`,
`backoff`, priority `100`, empty secrets, null volume fields and
`command_behavior`, no user tags or labels — are backend values, not frontend
constants. Credential source, materialization mode, `clear_env_keys`, model
policy, system tags, and activation state always need contextual backend
ownership.

---

## 6. Credentials and Volumes

Hiding Credentials & Volumes fields never hides whether a profile is connected.
The standard form shows the method, connection state, account label, the setup,
reconnect, or replace action, last validation where useful, and whether the
profile will be enabled after save. The advanced region may show source class,
materialization mode, role-aware SecretRef bindings, volume reference and mount
path, diagnostics, and which values are derived or locked — never secret
plaintext.

**API key.** The key is entered in a one-way credential dialog, validated by the
provider-specific backend, stored through the Secrets System, and bound as a
SecretRef to the required role; the backend applies materialization and
environment clearing, returns normalized readiness, and the plaintext input is
cleared. The server side of this flow is described in
[Provider Profiles](../Security/ProviderProfiles.md#7-api-key-backed-profiles).
For `runtime_id=opencode`, guided API-key creation and saved-profile replacement
or retry follow backend-advertised capabilities rather than a frontend provider
allowlist: OpenRouter and other valid providers use the same drawer and the
`opencode_api_key` role, saved actions require capabilities matching the
profile's runtime and provider with `api_key` setup and
`launch_ready_after_setup`, and `command_behavior.auth_actions` does not override
that. The reserved credential-free provider `opencode` offers no API-key setup.

**OAuth.** The OAuth flow owns volume creation or registration, mount path,
validation, account identity, and activation, so the form never asks for a
volume name or home path. An imported-volume workflow, where supported, is
distinct from normal enrollment.

---

## 7. Editing Existing Profiles

An existing profile with advanced configuration shows the section 3.3 summary
beside the checkbox, for example `Custom cooldown and priority`, `OAuth volume
generated by enrollment`, `3 routing tags and custom command behavior`, or
`Launch-safety policy needs attention`. Generated enrollment metadata is
described by source rather than counted as an override, and a recommended-policy
summary never stands in for credential or readiness status.

Unknown values stay visible and round-trippable when backend policy permits;
they are not erased because the current preset no longer lists them, and they
open the advanced region with a diagnostic.

`Reset advanced options to recommended` previews every change first: fields set
to a recommended value, fields moving from explicit to inherited, generated
security fields that will be recalculated, and changes affecting launch
selection or command construction. The preview mutates nothing; applying it
changes only the draft until save. Credential and volume changes that need a
lifecycle action are identified, not performed by reset. Replacement, reconnect,
rotation, and disconnect remain dedicated actions, and an invalid or
disconnected credential is never hidden by collapsing the advanced region.

---

## 8. Validation and Failure

- Standard and advanced fields validate through the same save path.
- An error on a hidden field checks `Show advanced options`, announces the
  expansion, and focuses the first invalid control.
- Every draft value survives a failed save, including a user-authored Profile
  ID; there is no unreviewed ID substitution or automatic retry.
- A profile is not shown as connected or enabled until backend activation
  succeeds.
- A preset-version conflict is handled like any stale-policy conflict: reload
  metadata and require review.
- Unsupported manual combinations are reported before creation when possible.
- A failed creation leaves unrelated profiles and managed secrets untouched.
- Stale selection metadata is discarded as authority without discarding the
  user's unrelated draft work.
- A failed create is distinguished from a confirmed create followed by
  incomplete enrollment; the latter resumes setup for the saved profile.

---

## 9. Accessibility and Layout

The advanced checkbox is native, linked to its region with `aria-controls`,
exposes checked and expanded state, and keeps its description associated.
Opening the region preserves focus order, focused content is not removed without
moving focus safely, and derived, locked, default, and override states are
expressed in text rather than color. Credential flows are keyboard accessible and
secret-safe. Harness and Provider selectors are labelled and expose loading,
unavailable, and invalid states. Action explanations are associated with the
primary button, and save and setup outcomes are announced without unexpected
focus moves.

On wide screens identity and capacity controls may use compact grids, and the
advanced region groups controls into credential implementation, rate limiting,
routing and selection, and launch shaping and diagnostics. On narrow screens the
form is one column: grid and flex containers can shrink, fieldsets reset their
intrinsic minimum (`min-inline-size: 0`), labels sit above full-width fields with
a 16px text baseline, and every field, label, helper, and validation message
stays inside its section down to 320px. The checkbox and summary stay above the
advanced content, raw identifiers and SecretRefs wrap or scroll inside bounded
code fields, tier rows stack, enrollment and confirmation dialogs are bounded by
the viewport, and create/cancel remain reachable after a long advanced region.
Presentation changes never alter draft, tier, authentication, or save semantics.

---

## 10. Structure and Tests

The form's responsibilities — identity fields, authentication setup, tier
section, capacity, default selection, advanced toggle, summary and region, and
save actions — need not each be a separate component. Identity suggestions,
action presentation, and advanced summaries are small pure helpers around the
existing form, sharing the generated API types and normalized draft/preset
semantics instead of a second validation, enrollment, or persistence model. The
create payload builder distinguishes untouched inherited values from explicit
overrides rather than serializing every displayed recommendation.

The contract is exercised by
`frontend/src/components/settings/providerProfileRedesignConformance.test.tsx`
and `frontend/src/components/settings/ProviderProfilesManager.test.tsx`
(run with `npm run ui:test:settings-redesign`), covering standard creation,
progressive disclosure, draft preservation, hidden-field validation focus, tier
integration, existing-profile compatibility, identity selection, and save/setup
presentation. Generated-contract guards reject a second hand-maintained preset
schema in React, and backend tests cover the choices projection and its
agreement with creation-capability and save authorities. Component tests with
mocked responses do not establish live provider enrollment or deployment
readiness.
