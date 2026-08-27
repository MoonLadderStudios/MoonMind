# Provider Profile Model and Effort Tier Settings

Status: **Desired-state UI and implementation contract**

Owners: MoonMind Engineering

Last updated: 2026-08-27

Canonical for: the Settings experience used to inspect and edit `model_tiers` and `default_model_tier` on a Provider Profile

**Related design documents:** [Provider Profile Model and Effort Tiers](../Security/ProviderProfileModelEffortTiers.md), [Provider Profiles](../Security/ProviderProfiles.md), [Settings Page](./SettingsPage.md), [Dashboard Design System](./DashboardDesignSystem.md), [Secrets System](../Security/SecretsSystem.md)

**Related implementation issue:** [#3348 Model tier system should have a clear UI representation in settings](https://github.com/MoonLadderStudios/MoonMind/issues/3348)

**Implementation tracking:** phased delivery sequencing lives in [`docs/tmp/ProviderProfileTierSettingsRollout.md`](../tmp/ProviderProfileTierSettingsRollout.md). This document defines the durable desired state and remains readable without it.

> [!NOTE]
> This document defines the durable desired-state UI contract. It does not replace the backend tier-resolution contract in `ProviderProfileModelEffortTiers.md`.
>
> The frontend authors and previews tier policy. The backend remains authoritative for validation, compatibility normalization, and launch-time model and effort resolution.

---

## 1. Purpose

Provider Profiles already define an ordered model and effort policy through `model_tiers` and `default_model_tier`. Settings must make that policy easy to understand and safe to change.

A user should be able to answer these questions at a glance:

1. How many tiers does this profile define?
2. Which tier is the default?
3. Which model and effort level does each tier select?
4. Does a tier inherit a runtime default?
5. What will change if a tier is added or removed?

A user with write permission should also be able to:

1. add a tier with one obvious action,
2. remove a tier with one obvious action,
3. select a model from backend-provided options,
4. select an effort level from backend-provided options,
5. keep a custom model or effort value when the runtime permits it,
6. choose exactly one default tier, and
7. save canonical ordered tier policy without editing raw JSON.

The primary product decision is:

> Model and effort tiers are shown as an ordered, numbered vertical stack of tier cards. The number is structural, the default is explicit, and add or remove actions never hide the effect that array changes have on future tier-number requests.

---

## 2. Current State and Gap

The Provider Profile API already accepts and validates:

```yaml
model_tiers:
  - label: str | null
    model: str | null
    effort: str | null
    parameters: object
    annotations: object

default_model_tier: int
```

The current Settings table can display tier mappings. The current edit form still centers legacy `default_model` and `default_effort` fields and does not author `model_tiers` or `default_model_tier`.

That produces four gaps:

1. The tier mapping is visible but not directly editable.
2. The default tier cannot be changed through the form.
3. Adding or removing tiers requires API or database-level work.
4. A frontend-only hard-coded model list would become stale and would duplicate provider policy.

This design closes those gaps without moving launch-time authority into the browser.

---

## 3. Scope and Authority

### 3.1 This document owns

This document owns the Settings contract for:

- tier summary display in the Provider Profiles collection,
- tier editing within the Provider Profile form,
- model and effort selectors,
- add, duplicate, remove, and default-selection behavior,
- structural change warnings,
- draft state and canonical save payloads,
- capability loading and safe degradation,
- responsive layout,
- accessibility,
- frontend validation,
- error presentation, and
- UI acceptance criteria.

### 3.2 This document does not own

This document does not redefine:

- model and effort resolution order,
- tier fallback behavior at launch,
- runtime command construction,
- Provider Profile selection or leasing,
- credential storage or materialization,
- secret validation,
- run diagnostics, or
- historical execution audit semantics.

Those contracts remain owned by the Provider Profile, model tier, runtime, and Secrets designs.

### 3.3 Backend authority

The browser submits ordered tier intent. The backend must:

1. validate the full tier array,
2. validate `default_model_tier`,
3. validate provider and runtime capability constraints,
4. reject secret-like data in tier parameters or annotations,
5. resolve tier policy without consulting a second persisted representation of model or effort,
6. persist one coherent profile policy, and
7. return the normalized Provider Profile.

The frontend must replace its saved draft with the normalized response rather than assuming its local representation is authoritative.

---

## 4. Product Principles

### 4.1 Order must be visible

Tier numbers are array positions. They are not labels and they are not independent database identities.

The UI must always render tiers in ascending order with a persistent visual rail or equivalent ordered-list treatment. A tier number cannot be typed or edited directly.

### 4.2 The default must be unmistakable

Exactly one tier is the profile default. It must have all of the following:

- a selected native radio control,
- a visible `Default` badge,
- a stronger border or surface emphasis, and
- a section-level summary such as `Default: Tier 2`.

Color alone must not communicate default state.

### 4.3 Common actions stay easy

Appending a tier and removing a safe last tier should be lightweight. Structural changes that alter existing tier numbers must explain their impact before applying.

### 4.4 Runtime defaults are explicit values

A null model or null effort is not shown as an unexplained blank. The selector exposes an explicit `Runtime default` option.

When the runtime default is known, supporting text may show the current inherited value:

```text
Runtime default: gpt-5.5
```

The stored value remains null.

`Runtime default` is only honest when a null tier value actually resolves to the runtime default. This contract therefore requires that no profile-level compatibility field sits between tier selection and the runtime default: a null tier `model` or `effort` must never resolve through `default_model` or `default_effort`. That resolution order is owned by [Provider Profile Model and Effort Tiers](../Security/ProviderProfileModelEffortTiers.md); removing the compatibility step is a prerequisite of this UI contract and lands with the tier-only persistence contract in §15.2.

Two rules keep the editor honest regardless of resolver state:

1. The inherited value shown in supporting text and in the §8.6 effective preview comes from backend-reported resolution for that tier, never from a frontend assumption about which source supplies it.
2. When the backend reports an inherited value whose source is not the runtime, the card names that source instead of showing `Runtime default`.

### 4.5 Backend-provided choices, not React constants

Model names and effort values change. The frontend must not maintain a product-wide hard-coded list.

The backend supplies choices, constraints, support status, and custom-value policy for the selected runtime and provider. Existing unknown values must remain visible and round-trippable.

### 4.6 Structural safety without unnecessary friction

The initial design does not use drag and drop. Reordering tier cards looks harmless but changes the meaning of every affected future `modelTier` request.

The primary add action appends. The primary remove action is always visible. Advanced insertion or reordering may be added later only with the same explicit renumbering preview required for middle-tier removal.

### 4.7 Future launches change, historical runs do not

The editor must state that saved profile policy affects future resolution. Existing run records retain the concrete model and effort captured at launch.

---

## 5. Placement in Settings

The canonical route remains:

```text
/settings?section=providers-secrets
```

No new top-level Settings section is required.

Within the Provider Profile form, the desired section order is:

```text
Identity
Provider and runtime
Model and effort tiers
Credentials and volumes
Runtime limits
Advanced profile options
Save actions
```

`Model and effort tiers` must be a full-width section. It must not be compressed into the existing three-column grid used for small scalar fields.

The superseded `Default model` and `Default effort` inputs leave the primary form and are not replaced by a read-only mirror. The selected default tier is the only place a profile's default model and effort are authored or displayed.

A profile row may expose an `Edit tiers` action that opens the normal profile editor and focuses this section. It does not create a second tier-specific persistence path.

---

## 6. Provider Profile Collection Summary

The collection view must communicate tier policy without forcing the user to enter edit mode.

### 6.1 Desktop summary

Use a dedicated `Model policy` column when table width permits. Do not bury the full mapping inside an unrelated identity field.

Compact state:

```text
3 tiers
Default: Tier 2
T1  Plan and verify    gpt-5.5 / medium
T2  Implementation     gpt-5.5 / xhigh
+1 more
```

Rules:

1. Show the total tier count.
2. Show the default tier number.
3. Show up to two tier mappings in the collapsed table row.
4. Show `+N more` when additional tiers exist.
5. Let the user expand the row or a native `details` region to inspect all mappings.
6. Show `Runtime default` rather than an empty model or effort.
7. Use a `Default` text badge or star with an accessible label in the expanded mapping.
8. Keep exact model and effort values in monospace where practical.
9. A read-only user can inspect the same mapping without edit controls.

### 6.2 Narrow screens

On narrow screens, the Provider Profile table may become cards. Each card shows:

```text
Profile name
Runtime / Provider
3 model tiers · Default Tier 2
[Show tier mapping]
```

Expanding `Show tier mapping` reveals the ordered tier list. The disclosure state is local UI state and is not persisted.

### 6.3 Profiles without usable tier data

`model_tiers` is always populated by the Provider Profile contract, including for rows the backend migration converted from superseded default fields. The frontend never reconstructs tier policy from another persisted field.

A response with missing or empty `model_tiers` is therefore a backend contract violation rather than a profile variant. The summary says so instead of inventing a tier:

```text
Tier policy unavailable · needs repair
```

The row keeps its `Edit tiers` action so a user with write permission can reach the repair state in §18.3.

---

## 7. Tier Editor Overview

### 7.1 Section anatomy

```text
┌ Model & effort tiers ───────────────────────────────────────────────────────┐
│ Map workflow tier requests to a model and effort for this profile.         │
│ 3 tiers · Default: Tier 2                              [+ Add tier]          │
│                                                                            │
│  ●  Tier 1  Plan and verify                                                │
│  │  [○ Use as default]                                      [Remove tier]  │
│  │  Model [gpt-5.5                         ▾]                               │
│  │  Effort level [medium                   ▾]                               │
│  │  Resolves to gpt-5.5 · medium                                           │
│  │                                                                         │
│  ●  Tier 2  Implementation                              [Default]           │
│  │  [● Default tier]                                       [Remove tier]  │
│  │  Model [gpt-5.5                         ▾]                               │
│  │  Effort level [xhigh                   ▾]                               │
│  │  Resolves to gpt-5.5 · xhigh                                           │
│  │                                                                         │
│  ●  Tier 3  Documentation audit                                           │
│     [○ Use as default]                                      [Remove tier]  │
│     Model [gpt-5.3-codex-spark             ▾]                              │
│     Effort level [xhigh                    ▾]                              │
│                                                                            │
│ [+ Add tier]                                                               │
│ Future launches use the saved policy. Historical runs keep their record.   │
└────────────────────────────────────────────────────────────────────────────┘
```

The circles and connecting line are illustrative. An ordered list with equally clear numbering is acceptable.

### 7.2 Surface hierarchy

The section follows the Dashboard Design System:

- the section body is a matte data slab,
- each tier card is a grounded satin form surface,
- inputs use the standard satin input treatment,
- the default tier uses restrained violet emphasis,
- warning states use amber,
- destructive confirmation uses rose,
- `Add tier` uses the normal create or commit action treatment, and
- decorative glass is not used inside the tier stack.

### 7.3 Section header

The header contains:

- title: `Model & effort tiers`,
- a one-sentence explanation,
- tier count,
- default tier summary,
- capability loading or warning state when relevant, and
- a primary `Add tier` button.

A second full-width `Add tier` action appears after the final card. This makes adding easy after the user reviews the current stack.

---

## 8. Tier Card Contract

Each tier is one list item and one fieldset.

### 8.1 Tier number

The tier number is visually dominant and derived from the current array index:

```text
index 0 -> Tier 1
index 1 -> Tier 2
index 2 -> Tier 3
```

Rules:

1. The number is not an input.
2. The number updates immediately in local draft state after a confirmed structural change.
3. The user receives an announcement when removal causes renumbering.
4. The internal React key must not be the array index. Use a stable draft-only client ID.

### 8.2 Default selection

Every card contains a radio control in one group named `Default model tier`.

Labels:

- selected: `Default tier`,
- unselected: `Use Tier N as default`.

Selecting a new default is immediate in draft state and does not reorder cards.

### 8.3 Label

The optional label explains intended use.

Examples:

```text
Plan and verify
Implementation
Escalation
Documentation audit
```

Rules:

1. The label is not used for routing.
2. A blank label displays as `Tier N` in summaries.
3. Leading and trailing whitespace is removed before save.
4. Duplicate labels are allowed because order and number remain authoritative.
5. The UI should recommend a concise label rather than enforce hidden semantic categories.

### 8.4 Model

The model control is a searchable combobox.

It always exposes:

```text
Runtime default
```

It then exposes backend-provided model choices. Custom entry is available only when capability metadata allows it.

Display behavior:

- null value: `Runtime default`,
- known value: backend-provided label plus exact value when they differ,
- deprecated value: keep selectable with a `Deprecated` warning,
- unknown existing value: show `Custom or unavailable` and preserve it,
- incompatible value after runtime or provider change: keep it visible, show an error, and require a valid choice before save unless the backend explicitly permits it.

The combobox must support keyboard filtering and must not require a mouse.

### 8.5 Effort level

The visible label is `Effort level`.

The control shape comes from backend capabilities:

| Capability | UI control |
| --- | --- |
| Supported with a closed option set | Select |
| Supported with custom values | Searchable combobox |
| Metadata only | Select or combobox plus `Metadata only` explanation |
| Unsupported | Disabled field reading `Not supported by this runtime` |
| Capability unavailable | Preserve current value and show catalog warning |

A supported effort control always includes `Runtime default` as the null option.

When effort support depends on the selected model, changing the model refreshes the compatible effort choices. The UI does not silently clear an incompatible effort. It marks the field and asks the user to choose a valid value.

### 8.6 Effective preview

When enough information is available, each card shows a compact preview:

```text
Resolves to gpt-5.5 · medium
```

The preview is advisory. It may use capability metadata or a backend preview endpoint. The UI must label fallback, unsupported effort, or unresolved runtime defaults honestly.

Examples:

```text
Model uses runtime default: gpt-5.5
Effort is stored as xhigh but this runtime reports metadata-only support
Runtime default is not currently known
```

### 8.7 Advanced tier data

`parameters` and `annotations` remain part of the canonical tier object. They are not shown as primary fields.

Each card may expose an `Advanced tier options` disclosure containing safe structured editors for:

- non-secret runtime parameters,
- annotations, and
- server validation diagnostics.

Rules:

1. Collapsed advanced data must be preserved exactly through ordinary edits.
2. Unknown keys must not be dropped.
3. Raw credentials and credential references are not accepted here.
4. JSON parse errors remain local to the tier card.
5. The primary model and effort workflow must not require opening this disclosure.

### 8.8 Card actions

Each card exposes:

- `Remove tier`,
- `Duplicate as new last tier`, in a secondary action menu or button, and
- optional `Reset model and effort to runtime defaults`.

Duplicate behavior copies label, model, effort, parameters, and annotations, appends the copy, and does not make it the default. The copied label may gain `copy` in draft state to reduce ambiguity.

---

## 9. Add Tier Behavior

The primary `Add tier` action appends one tier. Appending preserves every existing tier number.

Default new tier:

```yaml
label: null
model: null
effort: null
parameters: {}
annotations: {}
```

Interaction sequence:

1. Append the tier to local draft state.
2. Assign a stable draft-only client ID.
3. Update the visible count.
4. Scroll the new card into view when needed.
5. Move focus to the new tier label or model control.
6. Announce `Tier N added` through a polite live region.
7. Leave the current default unchanged.

When the backend advertises a maximum tier count, `Add tier` is disabled at that maximum with an explanation.

The UI must never auto-populate a new tier by guessing that higher numbers mean higher quality, higher cost, or higher effort.

---

## 10. Remove Tier and Renumbering Behavior

A profile must always contain at least one tier.

### 10.1 Removal matrix

| Requested removal | Required behavior |
| --- | --- |
| Only remaining tier | Disable removal and explain the minimum-one-tier rule |
| Last tier, not default | Remove immediately in draft state and offer Undo |
| Any default tier | Confirm removal and require or confirm the new default |
| Any non-last tier | Confirm removal and show every tier-number change |
| Tier with unsaved advanced edits | Include that fact in the confirmation |

### 10.2 Middle-tier confirmation

Removing a non-last tier changes the meaning of later tier numbers. The confirmation must show the mapping, not only a generic warning.

Example:

```text
Remove Tier 2?

This changes future tier-number resolution for this profile:

Tier 3: Documentation audit -> becomes Tier 2
Tier 4: Escalation          -> becomes Tier 3

Existing historical runs do not change.

[Cancel] [Remove and renumber]
```

### 10.3 Removing the default tier

When the removed tier is the default, the confirmation includes a required `New default tier` choice among surviving cards.

The UI may preselect the nearest surviving tier:

1. choose the tier that will occupy the removed ordinal after removal,
2. otherwise choose the previous tier.

The user must be able to review or change that choice before confirming.

The UI must not silently assign a new default after a destructive action.

### 10.4 Undo

After a safe last-tier removal, show an Undo notice while the form remains open. Undo restores:

- the tier at its former position,
- its stable draft client ID,
- its field values, and
- the former default selection if applicable.

Undo is local draft behavior. It does not create a second backend write.

---

## 11. Reordering and Insertion Policy

The initial Settings design does not expose drag and drop, editable tier numbers, or a casual `Move up` action.

Rationale:

- tier order is execution policy,
- reordering changes existing numeric references,
- drag and drop is difficult to make safe and accessible, and
- appending satisfies the common add-tier case without changing existing numbers.

A later implementation may add `Insert after`, `Move up`, or `Move down` only when all of these are true:

1. it uses the same renumbering impact preview as middle-tier removal,
2. keyboard operation is complete,
3. the save diff shows old and new mappings, and
4. the user confirms the structural policy change.

Users can still change the model, effort, and label of any existing tier without reordering it.

---

## 12. Backend-Driven Editor Capabilities

### 12.1 Requirement

Settings needs profile-aware selector metadata. The current boot payload default model map is not enough to define model choices, effort support, compatibility, or custom-value policy.

Runtime and provider identity alone cannot answer the question the editor asks. Two profiles can share a runtime and provider while holding different credentials, credential generations, or pinned host images. MoonMind already persists model-catalog evidence per Provider Profile in `ProviderProfile.model_catalog_evidence_json` and treats that evidence as current only when it matches the profile's `credential_generation` and the pinned host image ref. A runtime-and-provider-only answer can therefore offer a model the edited profile cannot launch and defer the failure until launch time.

Capabilities are consequently scoped to the profile being edited:

```http
GET /api/v1/provider-profiles/{profile_id}/capabilities
```

A create form has no profile yet, so it supplies the draft runtime and provider instead:

```http
GET /api/v1/provider-profiles/capabilities?runtime_id={runtime_id}&provider_id={provider_id}
```

The draft response is advisory: it is not bound to any profile's evidence, and the editor refetches profile-scoped capabilities once the profile exists. The static `capabilities` route must be registered before a dynamic `/{profile_id}` route where framework routing requires it.

### 12.2 Suggested response contract

```yaml
ProviderProfileEditorCapabilities:
  version: string
  profile_id: string | null
  runtime_id: string
  provider_id: string

  evidence:
    source: profile_catalog_evidence | runtime_draft
    credential_generation: int | null
    image_ref: string | null
    observed_at: string | null
    stale: bool

  tier_constraints:
    min_count: 1
    max_count: int | null

  model:
    runtime_default: string | null
    allow_custom: bool
    options:
      - value: string
        label: string
        description: string | null
        status: available | deprecated | unavailable
        recommended: bool

  effort:
    supported: bool
    runtime_default: string | null
    allow_custom: bool
    application: native | config | environment | metadata_only | unsupported | unknown
    options:
      - value: string
        label: string
        description: string | null
        status: available | deprecated | unavailable
        compatible_models: [string] | null

  diagnostics:
    - code: string
      level: info | warning | error
      message: string
```

Illustrative response:

```json
{
  "version": "codex_cli:openai:42",
  "profile_id": "codex-cli-openai",
  "runtime_id": "codex_cli",
  "provider_id": "openai",
  "evidence": {
    "source": "profile_catalog_evidence",
    "credential_generation": 3,
    "image_ref": "ghcr.io/example/codex-host@sha256:0f1e2d3c",
    "observed_at": "2026-08-26T18:04:11Z",
    "stale": false
  },
  "tier_constraints": {
    "min_count": 1,
    "max_count": null
  },
  "model": {
    "runtime_default": "gpt-5.5",
    "allow_custom": true,
    "options": [
      {
        "value": "gpt-5.5",
        "label": "GPT-5.5",
        "description": "General coding model",
        "status": "available",
        "recommended": true
      }
    ]
  },
  "effort": {
    "supported": true,
    "runtime_default": "medium",
    "allow_custom": false,
    "application": "native",
    "options": [
      {"value": "low", "label": "Low", "status": "available", "compatible_models": null},
      {"value": "medium", "label": "Medium", "status": "available", "compatible_models": null},
      {"value": "high", "label": "High", "status": "available", "compatible_models": null},
      {"value": "xhigh", "label": "Extra high", "status": "available", "compatible_models": null}
    ]
  },
  "diagnostics": []
}
```

`model.options` and `effort.options` are the catalog observed for the reported `evidence` identity. A response with `evidence.source = profile_catalog_evidence` is valid only for the named `profile_id`, credential generation, and image ref; it is never reused for another profile that happens to share a runtime and provider.

The example values are illustrative. The backend response, not this document, is the current source of truth.

### 12.3 Capability rules

1. The endpoint contains no credentials.
2. Options are advisory for existing values and authoritative for whether new custom values are allowed.
3. An existing value missing from the catalog remains visible.
4. A deprecated existing value remains round-trippable until the user changes it or backend policy rejects it.
5. A removed or unavailable value is shown with an actionable warning.
6. Capability version is submitted as optional advisory metadata or used with a preview request to detect stale choices.
7. The write endpoint still validates everything.
8. The frontend must not infer effort support from provider name.
9. The frontend must not infer model options from runtime name.
10. Capabilities for a saved profile are requested by `profile_id`. A runtime-and-provider draft response must not be substituted for a saved profile.
11. A response whose `profile_id`, `credential_generation`, or `image_ref` no longer matches the loaded profile is stale. The editor refetches, and until that succeeds it treats the option catalog as unavailable for new values rather than authoritative.
12. `evidence.stale = true` means the profile's catalog evidence needs re-validation. Existing values stay visible and new values follow the degradation rules in §12.4.

### 12.4 Safe degradation

If capabilities fail to load:

- current values remain visible,
- `Runtime default` remains available,
- no existing value is erased,
- an inline warning states that model choices could not be refreshed,
- custom entry follows the last successfully loaded capability policy when safely cached,
- otherwise unknown new custom entry is disabled,
- saving unchanged tier values remains possible when server validation permits it, and
- server validation errors are mapped back to the affected tier and field.

The same rules apply when the profile's catalog evidence is missing or stale. The editor states that model choices reflect no current profile evidence, preserves existing values, and does not accept a new value that only unscoped runtime metadata would justify.

A capability failure must not turn the whole Provider Profiles section into an empty state.

---

## 13. Declarative Frontend State

Array indexes are unstable during editing. The frontend draft needs stable client identities.

Suggested model:

```ts
export type ProviderProfileTierDraft = {
  clientId: string;
  label: string;
  model: string | null;
  effort: string | null;
  parameters: Record<string, unknown>;
  annotations: Record<string, unknown>;
};

export type ProviderProfileTierEditorDraft = {
  tiers: ProviderProfileTierDraft[];
  defaultTierClientId: string;
  sourceProfileUpdatedAt: string | null;
  capabilityVersion: string | null;
  structuralChanges: TierStructuralChange[];
};

export type TierStructuralChange =
  | { type: 'append'; clientId: string }
  | { type: 'remove'; clientId: string; previousIndex: number }
  | { type: 'restore'; clientId: string; index: number };
```

Rules:

1. `clientId` exists only in frontend draft state.
2. The canonical payload never persists `clientId`.
3. The selected default follows `clientId`, not an array index, while editing.
4. On save, `default_model_tier` is computed as the selected tier index plus one.
5. Server validation paths such as `model_tiers.2.effort` map back to the matching draft card.
6. Cancel restores the profile response, not a partially normalized local copy.

---

## 14. Load and Normalization

### 14.1 Canonical profile

When `model_tiers` is a non-empty array:

1. create one draft card for each entry,
2. preserve array order,
3. preserve parameters and annotations,
4. select `default_model_tier`, and
5. surface an error if the server response is internally invalid.

The UI must not silently clamp a malformed saved default during editing. It may choose Tier 1 temporarily to keep controls usable, but it must show that the profile requires repair.

### 14.2 One canonical source

The backend migration owned by [Provider Profile Model and Effort Tiers](../Security/ProviderProfileModelEffortTiers.md) converts pre-tier rows into canonical `model_tiers`, so every Provider Profile response carries tier policy. The editor loads that one source and derives tier drafts from nothing else.

Missing or empty `model_tiers` is a contract violation, not a profile shape to normalize. Load the repair state in §18.3, keep the rest of the section usable, and do not synthesize a tier from any other profile field or present a synthesized value as saved policy.

---

## 15. Save Contract

### 15.1 Canonical payload

The editor submits ordered canonical fields:

```json
{
  "model_tiers": [
    {
      "label": "Plan and verify",
      "model": "gpt-5.5",
      "effort": "medium",
      "parameters": {},
      "annotations": {}
    },
    {
      "label": "Implementation",
      "model": "gpt-5.5",
      "effort": "xhigh",
      "parameters": {},
      "annotations": {}
    }
  ],
  "default_model_tier": 2
}
```

For a focused tier save, use a partial PATCH containing only tier policy fields and optional concurrency metadata. This avoids overwriting credentials, activation state, or limits that changed elsewhere.

The whole-profile form may submit the same fields with the rest of the profile payload.

### 15.2 Tier-only persistence

`model_tiers` and `default_model_tier` are the only persisted representation of a profile's model and effort policy:

1. the frontend submits `model_tiers` and `default_model_tier`, and nothing else that expresses a model or effort default,
2. the backend validates and persists that policy, and
3. the backend returns the normalized profile without a derived default model or default effort mirror.

There is no client-side derivation of `default_model` or `default_effort`, and no compatibility period in which the editor writes both representations. A second persisted representation is what lets canonical and mirrored values disagree at all, and MoonMind's pre-release policy removes a superseded contract together with its callers instead of extending it with an alias.

That cohesive change removes:

- `default_model` and `default_effort` from the Provider Profile read and write contracts,
- the compatibility step between tier selection and runtime defaults in launch-time resolution (§4.4), and
- the remaining callers, fixtures, and tests that assert the mirrored behavior.

No persisted-history cutover is required. Runs persist the concrete model and effort resolved at launch, so historical audit never reads current profile policy (§4.7). Tier policy for existing rows comes from the backend migration described in §14.2, not from a retained mirror.

### 15.3 Structural change summary

When a draft contains structural changes or a new default, show a compact summary above the final Save action:

```text
Tier policy changes

Added Tier 4
Removed former Tier 2
Former Tier 3 becomes Tier 2
Default changes from Tier 1 to Tier 2

These changes affect future launches that resolve this Provider Profile.
```

Ordinary model, effort, or label edits may appear in the same diff without requiring a second confirmation modal.

### 15.4 Concurrency

Where the Provider Profile API exposes an ETag, profile version, or expected update timestamp, the save must include it.

A stale write returns a conflict response rather than silently overwriting newer policy. The UI then offers:

- `Reload latest profile`, and
- `Review my draft against latest` when a merge view exists.

The initial implementation may begin with reload-on-conflict. It must not claim a save succeeded when the profile changed concurrently.

### 15.5 Save completion

On success:

1. replace local state with the server response,
2. clear the structural change log,
3. update the Provider Profiles query cache,
4. announce `Model tier policy saved`, and
5. keep the user near the tier section.

Do not optimistically replace the collection summary before the server accepts and normalizes the policy.

---

## 16. Validation and Error Placement

### 16.1 Frontend validation

Before submit, enforce:

```text
tiers.length >= 1
exactly one default tier is selected
default tier exists in the draft array
label is trimmed
model is null or a permitted string
effort is null or a permitted string
parameters is an object
annotations is an object
capability-blocked values are resolved when required
```

Frontend validation improves usability. It does not replace backend validation.

### 16.2 Backend validation mapping

Map errors to the smallest relevant surface:

| Error path | UI location |
| --- | --- |
| `model_tiers` | Section-level error |
| `model_tiers.1.model` | Tier 2 model control |
| `model_tiers.1.effort` | Tier 2 effort control |
| `model_tiers.1.parameters` | Tier 2 advanced disclosure |
| `default_model_tier` | Default radio group and section summary |
| capability or stale version | Section warning or conflict panel |

The first invalid control receives focus after a failed save. A section-level summary links to every invalid tier.

### 16.3 Unsupported effort

The UI must distinguish these states:

- `Applied natively`,
- `Applied through config`,
- `Applied through environment`,
- `Metadata only`,
- `Unsupported`, and
- `Unknown`.

It must not label a stored effort as applied when the runtime reports otherwise.

---

## 17. Runtime or Provider Changes

Runtime and provider selection determine tier capabilities.

When either field changes in a create form:

1. fetch new capabilities,
2. preserve tier drafts temporarily,
3. revalidate every model and effort,
4. mark incompatible values in place,
5. never silently replace a model or effort,
6. offer `Reset incompatible values to runtime defaults`, and
7. block save only for values the new backend policy cannot accept.

When editing an existing profile, immutable runtime rules remain authoritative. If runtime is not editable, capability changes come only from provider edits or backend catalog changes.

A reset action must list the affected tiers before applying:

```text
Tier 1 model -> Runtime default
Tier 2 effort -> Runtime default
```

---

## 18. Loading, Empty, Read-Only, and Failure States

### 18.1 Loading profile data

Show the section header and tier-card skeletons. Do not render a superseded default model or default effort input while canonical tier data loads.

### 18.2 Loading capabilities

Show current model and effort values immediately. Disable only the choice menu while options load. Preserve text and layout so cards do not jump.

### 18.3 Empty tier data

A truly empty tier array is invalid. The UI renders a repair state rather than a blank section:

```text
This profile has no model tiers and cannot be saved in this state.
[Create runtime-default Tier 1]
```

The repair action creates one local runtime-default tier and selects it as default.

### 18.4 Read-only permission

Read-only users see:

- the full ordered stack,
- default state,
- model and effort values,
- runtime-default explanations,
- capability diagnostics, and
- advanced metadata where authorized.

They do not see enabled add, duplicate, remove, reset, or save controls. Inputs render as values rather than disabled low-contrast form fields.

### 18.5 Capability error

Show a scoped warning:

```text
Model choices could not be refreshed. Existing values are preserved. Server validation remains authoritative.
```

The rest of the Provider Profile editor remains usable.

### 18.6 Save error

Keep every draft value. Do not close or reset the editor. Show field errors inline and a concise top summary.

---

## 19. Responsive Layout

### 19.1 Wide desktop

A tier card uses a compact two-column control row:

```text
Model combobox                 Effort select
```

The label, default radio, and actions share the card header.

### 19.2 Tablet

Model and effort may remain side by side when each control retains a usable width. Otherwise they stack.

### 19.3 Mobile

Each tier is a full-width card:

```text
Tier 2  [Default]
Implementation
[Default tier radio]
Model
[combobox]
Effort level
[select]
[Remove tier]
```

Rules:

1. Preserve ascending visual order.
2. Keep the tier number visible at the top of every card.
3. Use full-width controls.
4. Do not use horizontal scrolling for the tier editor.
5. Keep destructive actions labeled with text.
6. The bottom `Add tier` action remains full width.

---

## 20. Accessibility

The tier editor must satisfy these requirements:

1. Render the tier stack as an ordered list.
2. Render each tier as a fieldset with a legend such as `Tier 2`.
3. Use one native radio group for default selection.
4. Give every model and effort control a tier-specific accessible name.
5. Give remove actions labels such as `Remove Tier 2`.
6. Give duplicate actions labels such as `Duplicate Tier 2 as new last tier`.
7. After add, move focus to the new tier and announce its number.
8. After confirmed removal, announce the removed tier and any renumbering.
9. Confirmation dialogs receive focus, trap focus, close with Escape, and return focus to the initiating action.
10. Color is never the sole signal for default, warning, deprecated, or error state.
11. Selector options expose deprecated or unavailable status in accessible text.
12. The editor remains fully usable without drag and drop.
13. Reduced-motion preferences disable animated rail or card movement.

---

## 21. Component Boundaries

Recommended component decomposition:

```text
ProviderProfilesManager
  ProviderProfilesTable
    ProviderProfileModelPolicySummary
  ProviderProfileForm
    ProviderProfileTierSection
      TierPolicyHeader
      TierOrderedList
        ProviderProfileTierCard
          DefaultTierControl
          ModelCombobox
          EffortControl
          TierResolutionPreview
          AdvancedTierOptions
          TierActions
      TierStructuralChangeSummary
      AddTierButton
    RemoveTierDialog
    CapabilityErrorNotice
```

Suggested hooks and helpers:

```text
useProviderProfileTierCapabilities
normalizeProviderProfileTiers
buildProviderProfileTierPayload
validateProviderProfileTierDraft
computeTierRenumberingImpact
mapTierApiErrorsToClientIds
```

Rules:

1. Keep normalization and payload construction outside JSX.
2. Unit test structural helpers without rendering the full Settings page.
3. Do not let ProviderProfilesManager accumulate more provider-specific model constants.
4. Reuse shared combobox, select, dialog, notice, and disclosure primitives.
5. Keep runtime and provider capability policy in backend responses.

---

## 22. Test Contract

### 22.1 Normalization tests

- A canonical three-tier profile preserves order and default Tier 2.
- A response with missing or empty `model_tiers` loads the repair state instead of a synthesized tier.
- A profile with null values shows explicit runtime defaults.
- Parameters and annotations survive an ordinary label edit.
- An invalid default index produces a repair diagnostic.

### 22.2 Add tests

- Add appends one runtime-default tier.
- Add preserves every existing tier number.
- Add does not change the default.
- Add moves focus to the new card.
- Add is disabled at a backend-provided maximum.
- Duplicate appends a copy and does not duplicate default state.

### 22.3 Remove tests

- The only tier cannot be removed.
- The last non-default tier can be removed and restored with Undo.
- Removing a middle tier shows all renumbering effects.
- Cancel leaves the draft unchanged.
- Confirm removes the tier and updates displayed numbers.
- Removing the default requires a new default selection.
- The saved default index matches the selected surviving client ID.

### 22.4 Selector tests

- Model options come from capability data.
- Capabilities for a saved profile are requested by `profile_id`.
- A capability response bound to different profile evidence is refetched rather than applied.
- Stale profile catalog evidence blocks new values without erasing existing ones.
- Runtime default stores null.
- A custom model is accepted only when allowed.
- An unknown existing model remains visible.
- A deprecated model displays a warning.
- Effort options filter by selected model when compatibility metadata exists.
- Unsupported effort is not presented as applied.
- Capability failure preserves existing values.

### 22.5 Payload tests

- Card order becomes `model_tiers` order.
- The selected client ID becomes a one-based `default_model_tier`.
- Draft client IDs do not enter the payload.
- The payload contains no `default_model` or `default_effort` field.
- Advanced objects are preserved.
- A tier-only PATCH does not include credentials or unrelated profile fields.

### 22.6 Permission and accessibility tests

- Read-only users can inspect every tier and the default.
- Read-only users cannot add, remove, duplicate, or save.
- Default controls use radio semantics.
- Tier cards have ordered-list and fieldset semantics.
- Add and remove announcements are exposed to assistive technology.
- Every dialog returns focus correctly.
- Keyboard-only users can select model, effort, default, add, duplicate, remove, cancel, and save.

### 22.7 Responsive tests

- Wide layout keeps model and effort aligned.
- Mobile layout stacks controls without horizontal scrolling.
- Tier number and default remain visible at every breakpoint.
- The trailing Add action remains reachable after a long tier list.

---

## 23. Acceptance Criteria

The design is correctly implemented when all of the following are true:

1. Every Provider Profile summary clearly shows tier count and default tier.
2. A user can expand the summary to inspect every tier in order.
3. The edit form has a dedicated full-width `Model & effort tiers` section.
4. Every tier visibly shows number, label, model, effort, and default state.
5. Model and effort controls use backend-provided capability data bound to the edited profile's catalog evidence.
6. Null values are shown as explicit runtime defaults.
7. A user can append a tier with one obvious action.
8. A user can remove a tier with one obvious action.
9. The only tier cannot be removed.
10. Removing a middle tier previews every renumbering effect.
11. Removing the default tier requires a reviewed new default.
12. The frontend stores draft identity separately from array index.
13. Save submits ordered `model_tiers` and one-based `default_model_tier`.
14. The save payload carries tier policy only, with no default model or default effort mirror.
15. Existing unknown model or effort values are never silently erased.
16. Read-only users receive a clear non-form representation.
17. Server validation errors appear on the correct tier and field.
18. The editor works with keyboard navigation and narrow screens.
19. Historical run records are not presented as changing when profile policy changes.
20. The implementation adds no hard-coded provider model catalog to React.

---

## 24. Decision Summary

- Provider Profile tiers use an ordered vertical stack of numbered cards.
- The default tier is a native radio selection with redundant visible emphasis.
- Model and effort values use backend-driven selectors with explicit runtime-default options.
- Editor capabilities are scoped to the edited Provider Profile's catalog evidence, not to runtime and provider alone.
- The primary add action appends so existing tier numbers remain stable.
- The primary remove action stays visible on every card.
- Removing the only tier is blocked.
- Removing a middle tier requires a renumbering impact preview.
- Removing the default requires a reviewed replacement default.
- Drag and drop is intentionally excluded from the initial design.
- Draft cards use stable client IDs while persisted tier identity remains array order.
- Canonical saves write `model_tiers` and `default_model_tier`.
- Tier policy is the only persisted representation of profile model and effort defaults; the superseded default fields and their launch-time compatibility step are removed together.
- A null tier value means the runtime default, so nothing resolves through a profile-level compatibility field first.
- The browser previews policy, while the backend validates and resolves it authoritatively.
