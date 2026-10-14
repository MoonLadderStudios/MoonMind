# Provider Profile Model and Effort Tier Settings

This document describes the Settings experience for inspecting and editing a
Provider Profile's `model_tiers` and `default_model_tier`.

**Related design documents:** [Provider Profile Model and Effort Tiers](../Security/ProviderProfileModelEffortTiers.md), [Provider Profiles](../Security/ProviderProfiles.md), [Provider Profile Creation](./ProviderProfileCreation.md), [Settings Page](./SettingsPage.md), [Dashboard Design System](./DashboardDesignSystem.md)

The browser authors and previews tier policy. The backend validates it,
normalizes compatibility, and resolves model and effort at launch; that
resolution contract lives in
[Provider Profile Model and Effort Tiers](../Security/ProviderProfileModelEffortTiers.md).
Profile activation, credentials, and selection are described in
[Provider Profiles](../Security/ProviderProfiles.md), and the surrounding form in
[Provider Profile Creation](./ProviderProfileCreation.md).

Implementation: `frontend/src/components/settings/ProviderProfilesManager.tsx`
(editor) and `frontend/src/utils/providerProfileTiers.ts` (normalization, payload,
renumbering, and error-mapping helpers).

---

## 1. Purpose

Settings makes tier policy easy to understand and hard to change by accident. At
a glance a user can see how many tiers a profile defines, which is the default,
which model and effort each selects, whether a tier inherits a runtime default,
and what adding or removing a tier will change.

A user with write permission can add or remove a tier with one obvious action,
choose model and effort from backend-provided options, keep a custom value where
the runtime permits it, choose exactly one default, and save ordered tier policy
without editing JSON.

The central design choice:

> Tiers are an ordered, numbered vertical stack of cards. The number is
> structural, the default is explicit, and add or remove actions never hide their
> effect on future tier-number requests.

---

## 2. Scope and Authority

The editor covers the tier summary in the Provider Profiles collection, tier
editing in the profile form, model and effort selectors, add/duplicate/remove and
default selection, structural change warnings, draft state and save payloads,
capability loading and degradation, layout, accessibility, and error placement.

It does not change resolution order, launch-time fallback, command construction,
profile selection or leasing, credentials, run diagnostics, or historical audit.

The browser submits ordered tier intent. The backend validates the full tier
array and `default_model_tier`, applies provider and runtime constraints, rejects
secret-like data in tier `parameters` or `annotations`, persists one coherent
policy, and returns the normalized profile. The editor replaces its draft with
that response rather than trusting its local copy.

---

## 3. Product Principles

### 3.1 Order is visible

Tier numbers are array positions, not labels or database identities. Tiers
render in ascending order with an ordered-list treatment, and the number is
never an editable input.

### 3.2 The default is unmistakable

Exactly one tier is the default. It has a selected native radio, a visible
`Default` badge, stronger border or surface emphasis, and a section summary such
as `Default: Tier 2`. Color is never the only signal.

### 3.3 Common actions stay light

Appending a tier, and removing a last tier that renumbers nothing, are
lightweight. Changes that alter existing tier numbers explain their impact first.

### 3.4 Runtime defaults are explicit

A null model or effort appears as an explicit `Runtime default` option, not a
blank. Supporting text may show the inherited value when known (`Runtime default:
gpt-5.5`); the stored value stays null.

`Runtime default` is honest only if a null tier value actually resolves to the
runtime default, so the inherited value shown in supporting text and in the
effective preview comes from backend-reported resolution for that tier, not a
frontend assumption. When the backend reports an inherited value from a source
other than the runtime, the card names that source.

### 3.5 Choices come from the backend

Model names and effort values change, so React holds no product-wide list. The
backend supplies choices, constraints, support status, and custom-value policy
for the profile, and existing unknown values stay visible and round-trippable.

### 3.6 No casual reordering

There is no drag and drop. Reordering looks harmless but changes the meaning of
every affected future `modelTier` request. Add appends; remove is always visible.
Any future insertion or reordering would need the same renumbering preview as
middle-tier removal.

### 3.7 Future launches change, history does not

The editor states that saved policy affects future resolution. Existing runs keep
the concrete model and effort captured at launch.

---

## 4. Placement

The editor lives on `/settings/providers-secrets`, inside the Provider Profile
form, ordered as:

```text
Identity
Harness and Provider
Model and effort tiers
Credentials and volumes
Capacity
Advanced profile options
Save actions
```

`Model and effort tiers` is a full-width section, not part of the compact grid
used for scalar fields. The form has no separate `Default model` or `Default
effort` input or read-only mirror; the selected default tier is the only place a
profile's default model and effort are authored or shown. A profile row may offer
`Edit tiers`, which opens the normal editor focused on this section rather than
a second persistence path.

---

## 5. Collection Summary

The collection shows tier policy without entering edit mode. On desktop a
dedicated `Model policy` column shows:

```text
3 tiers
Default: Tier 2
T1  Plan and verify    gpt-5.5 / medium
T2  Implementation     gpt-5.5 / xhigh
+1 more
```

The collapsed row shows the count, default tier, up to two mappings, and `+N
more`; a row expansion or native `details` reveals every mapping. Null values read
`Runtime default`, the default has an accessible text badge, exact values use
monospace, and read-only users see the same mapping without edit controls.

On narrow screens rows become cards (`Profile name`, `Harness / Provider`, `3
model tiers · Default Tier 2`, `[Show tier mapping]`). The disclosure state is
local and not persisted.

`model_tiers` is always populated by the profile contract, including rows
converted from pre-tier fields, and the frontend never reconstructs tiers from
another field. A missing or empty array is a backend contract violation, so the
summary reads `Tier policy unavailable · needs repair` and keeps `Edit tiers` so
a writer can reach the repair state (section 15.3).

---

## 6. Tier Editor

### 6.1 Anatomy

```text
┌ Model & effort tiers ───────────────────────────────────────────────┐
│ Map workflow tier requests to a model and effort for this profile. │
│ Future launches use the saved policy. Historical runs keep theirs. │
│ 3 tiers · Default: Tier 2                                          │
│                                                                    │
│  ●  Tier 1  Plan and verify                                        │
│  │  [○ Use as default]                 [Duplicate] [Remove tier]   │
│  │  Model [gpt-5.5            ▾]  Effort level [medium       ▾]    │
│  │  Resolves to gpt-5.5 · medium                                   │
│  ●  Tier 2  Implementation                        [Default]        │
│  │  [● Default tier]                   [Duplicate] [Remove tier]   │
│  │  Model [gpt-5.5            ▾]  Effort level [max          ▾]    │
│  ●  Tier 3  Documentation audit                                    │
│     ...                                                            │
│ [+ Add tier]                                                       │
└────────────────────────────────────────────────────────────────────┘
```

An ordered list with equally clear numbering is an acceptable alternative to the
rail.

### 6.2 Surfaces and header

Following the Dashboard Design System, the section body is a matte data slab,
each tier card a grounded satin form surface with standard satin inputs; the
default uses restrained violet emphasis, warnings amber, destructive confirmation
rose, and no decorative glass appears in the stack.

The header holds the title `Model & effort tiers`, a one-sentence explanation,
the tier count, the default summary, and any capability loading or warning
state. It has no actions. The single `Add tier` action is a full-width button
after the last card, where the new tier appears.

---

## 7. Tier Card

Each tier is one list item and one fieldset. The legend names the tier only; the
default choice and the `Duplicate tier` / `Remove tier` actions share a separate
wrapping action row below the heading, so long labels never compete with actions.

### 7.1 Number

The number is visually dominant and derived from the array index (index 0 is
Tier 1). It is not an input, updates immediately after a confirmed structural
change, and renumbering is announced. The React key is a stable draft-only client
ID, never the index.

### 7.2 Default selection

Every card has a radio in one group named `Default model tier`, labelled `Default
tier` when selected and `Use Tier N as default` otherwise. Choosing a default is
immediate in the draft and never reorders cards.

### 7.3 Label

The optional label (`Plan and verify`, `Implementation`, `Escalation`) explains
intended use and is never used for routing. A blank label shows as `Tier N`,
whitespace is trimmed on save, and duplicates are allowed because order and
number are authoritative.

### 7.4 Model

The model control is a keyboard-filterable searchable combobox that always offers
`Runtime default` followed by backend-provided choices. Custom entry is available
only when capabilities allow it.

| Value | Display |
| --- | --- |
| null | `Runtime default` |
| known | backend label, plus exact value when they differ |
| deprecated | selectable with a `Deprecated` warning |
| unknown existing | `Custom or unavailable`, preserved |
| incompatible after a Harness/Provider change | visible with an error; a valid choice is required before save unless the backend permits it |

### 7.5 Effort level

The control shape follows backend capabilities:

| Capability | Control |
| --- | --- |
| Closed option set | Select |
| Custom values allowed | Searchable combobox |
| Metadata only | Select or combobox with a `Metadata only` explanation |
| Unsupported | Disabled field reading `Not supported by this runtime` |
| Capabilities unavailable | Current value preserved with a catalog warning |

A supported control always includes `Runtime default`. Options ascend in effort;
the default catalog is `low`, `medium`, `high`, `xhigh`, `max`, and the same values
serve as a fallback while capabilities are unavailable, with an out-of-catalog
saved value shown once as custom or unavailable. Once capabilities load, an
unavailable option, or one whose `compatible_models` excludes the selected model
(using the backend-reported runtime default for a null model), is disabled.
Changing to an incompatible pair blocks save with a field error but keeps the
draft; an unchanged saved pair stays round-trippable. OpenCode derives
compatibility from the same model support catalog used at launch, so seeded
models that top out at `xhigh` never advertise `max`, and without a compatible
observed model the draft catalog marks `max` unavailable.

When effort support depends on the model, changing the model refreshes effort
choices; an incompatible effort is marked for the user to fix, never cleared.

### 7.6 Effective preview

When enough information is available, a card shows an advisory preview such as
`Resolves to gpt-5.5 · medium`, from capability metadata or a backend preview.
Fallbacks are labelled honestly: `Model uses runtime default: gpt-5.5`, `Effort is
stored as xhigh but this runtime reports metadata-only support`, or `Runtime
default is not currently known`. The editor distinguishes effort applied
natively, through config, through environment, metadata only, unsupported, and
unknown, and never labels a stored effort as applied when the runtime says
otherwise.

### 7.7 Advanced tier data

`parameters` and `annotations` belong to the tier object but are not primary
fields. An `Advanced tier options` disclosure offers constrained structured
editors for non-secret parameters, annotations, and server diagnostics. Collapsed
data is preserved exactly, unknown keys are kept, credentials and credential
references are rejected, JSON parse errors stay local to the card, and the normal
model and effort workflow never requires opening it.

### 7.8 Duplicate

`Duplicate tier` (accessible name `Duplicate Tier N as new last tier`) copies the
label, model, effort, parameters, and annotations, appends the copy, and does not
make it the default. The draft label may gain `copy` to reduce ambiguity. A card
may also offer `Reset model and effort to runtime defaults`.

---

## 8. Adding a Tier

`Add tier` appends a runtime-default tier (`label`, `model`, `effort` null; empty
`parameters` and `annotations`) with a new client ID, preserving every existing
number and the current default. The count updates, the card scrolls into view,
focus moves to its label or model control, and `Tier N added` is announced
politely. At a backend-advertised maximum the action is disabled with an
explanation. New tiers are never pre-filled on the assumption that higher numbers
mean more quality, cost, or effort.

---

## 9. Removing a Tier

A profile always has at least one tier.

| Removal | Behavior |
| --- | --- |
| Only remaining tier | Disabled, with the minimum-one-tier explanation |
| Last tier, not default | Removed immediately in the draft, with Undo |
| Default tier | Confirmation requiring a reviewed new default |
| Non-last tier | Confirmation listing every number change |
| Tier with unsaved advanced edits | The confirmation says so |

Removing a non-last tier changes what later numbers mean, so the confirmation
shows the mapping:

```text
Remove Tier 2?

This changes future tier-number resolution for this profile:
Tier 3: Documentation audit -> becomes Tier 2
Tier 4: Escalation          -> becomes Tier 3

Existing historical runs do not change.
[Cancel] [Remove and renumber]
```

Removing the default adds a required `New default tier` choice. The editor may
preselect the tier that will occupy the removed position, else the previous one,
but the user reviews it; a new default is never assigned silently.

Undo after a non-renumbering last-tier removal restores the tier at its position
with its client ID, values, and former default status. Undo is local and makes no
backend write.

---

## 10. Editor Capabilities

### 10.1 Why capabilities are profile-scoped

Runtime and provider alone cannot say what the edited profile can launch. Two
profiles may share both but differ in credentials, credential generation, or
pinned host image. MoonMind stores model-catalog evidence per profile
(`model_catalog_evidence_json`) and treats it as current only when it matches the
profile's `credential_generation` and pinned image. A runtime/provider answer
could offer a model the profile cannot launch and defer the failure to launch.

```http
GET /api/v1/provider-profiles/{profile_id}/capabilities
GET /api/v1/provider-profiles/capabilities?runtime_id={runtime_id}&provider_id={provider_id}
```

The second form serves the create form, which has no profile yet. Its result is
advisory and not bound to any evidence; the editor refetches profile-scoped
capabilities once the profile exists. Both are built by
`api_service/services/provider_profile_tier_capabilities.py`.

### 10.2 Response shape

The response carries an opaque `version`; the `profile_id`, `runtime_id`, and
`provider_id`; an `evidence` block (`source` of `profile_catalog_evidence` or
`runtime_draft`, `credential_generation`, `image_ref`, `observed_at`, `stale`);
`tier_constraints` (`min_count`, `max_count`); a `model` block
(`runtime_default`, `allow_custom`, and `options` with value, label, description,
status `available | deprecated | unavailable`, and `recommended`); an `effort`
block (`supported`, `runtime_default`, `allow_custom`, `application` of `native |
config | environment | metadata_only | unsupported | unknown`, and options with
`compatible_models`); and `diagnostics`. The backend response, not this
document, is the source of truth for values.

`version` changes whenever the option sets or evidence identity change, so a
client can detect stale choices without parsing it. Options with
`profile_catalog_evidence` are valid only for the named profile, generation, and
image, and never reused for another profile sharing runtime and provider.

### 10.3 Rules

- The endpoint contains no credentials.
- Options are advisory for existing values and authoritative for whether new
  custom values are allowed. Existing values missing from the catalog stay
  visible; deprecated values stay round-trippable until changed or rejected;
  removed values show an actionable warning.
- The write endpoint still validates everything. `version` and evidence may be
  sent as advisory metadata to detect stale choices.
- The frontend never infers model options from runtime name or effort support
  from provider name.
- A saved profile's capabilities are fetched by `profile_id`, never substituted
  by a draft response. A response whose profile, generation, or image no longer
  matches the loaded profile is stale: the editor refetches and meanwhile treats
  observed options as unavailable for new selections.
- `evidence.stale = true` means the catalog needs re-validation: existing values
  stay, observed choices are disabled, and an explicit `model.allow_custom =
  true` still permits manual entry.

### 10.4 Degradation

When capabilities fail to load, or evidence is missing or stale, current values
stay visible, `Runtime default` remains available, nothing is erased, and an
inline warning reads "Model choices could not be refreshed. Existing values are
preserved. Server validation remains authoritative." Manual entry follows the
loaded `model.allow_custom` policy and is disabled when no authoritative response
allows it. Unchanged values remain saveable when the server permits, and server
errors map back to the affected tier and field. A capability failure never turns
the Provider Profiles section into an empty state, and the rest of the editor
stays usable.

---

## 11. Draft State

Indexes shift during editing, so the draft gives each tier a stable client ID:

```ts
type ProviderProfileTierDraft = {
  clientId: string;
  label: string;
  model: string | null;
  effort: string | null;
  parameters: Record<string, unknown>;
  annotations: Record<string, unknown>;
};
```

The editor draft also tracks `defaultTierClientId`, the source profile's
`updated_at`, the capability `version` it was authored against, and a log of
structural changes (append, remove, restore). Client IDs never reach the payload.
The default follows the client ID while editing and becomes `index + 1` on save.
Server paths such as `model_tiers.2.effort` map back to the matching card. Cancel
restores the server profile, not a partially normalized copy.

---

## 12. Load and Normalization

A non-empty `model_tiers` array produces one card per entry in order, preserving
parameters and annotations and selecting `default_model_tier`. An internally
invalid response surfaces an error; a malformed default is not silently clamped,
though the editor may select Tier 1 temporarily so controls stay usable while it
shows that the profile needs repair.

The backend migration described in
[Provider Profile Model and Effort Tiers](../Security/ProviderProfileModelEffortTiers.md)
converts pre-tier rows, so every response carries tier policy and the editor
derives drafts from nothing else. Missing or empty `model_tiers` loads the repair
state (section 15.3) rather than a synthesized tier presented as saved policy.

---

## 13. Save Contract

### 13.1 Payload

```json
{
  "model_tiers": [
    {"label": "Plan and verify", "model": "gpt-5.5", "effort": "medium", "parameters": {}, "annotations": {}},
    {"label": "Implementation", "model": "gpt-5.5", "effort": "xhigh", "parameters": {}, "annotations": {}}
  ],
  "default_model_tier": 2
}
```

A focused tier save is a partial PATCH containing only tier fields (and optional
concurrency metadata), so it cannot overwrite credentials, activation, or limits
changed elsewhere. The whole-profile form may submit the same fields with the
rest of the profile.

<a id="152-tier-only-persistence"></a>

### 13.2 Tier-only persistence

`model_tiers` and `default_model_tier` are the authored representation of a
profile's model and effort policy. The editor submits them and nothing else that
expresses a default model or effort, and never derives `default_model` or
`default_effort` client-side. A second authored representation is what would let
canonical and mirrored values disagree. The backend still exposes scalar
`default_model` and `default_effort` fields on the profile; they are not authored
by this editor, and a null tier value is meant to resolve to the runtime default
rather than through them (see
[Provider Profiles](../Security/ProviderProfiles.md#55-default-model-ownership)).
Runs persist the concrete model and effort resolved at launch, so history never
depends on current profile policy.

### 13.3 Structural change summary

When the draft has structural changes or a new default, a summary appears above
Save:

```text
Tier policy changes
Added Tier 4
Removed former Tier 2
Former Tier 3 becomes Tier 2
Default changes from Tier 1 to Tier 2
These changes affect future launches that resolve this Provider Profile.
```

Ordinary model, effort, or label edits may appear in the same diff without
another confirmation.

### 13.4 Concurrency

Where the API exposes an ETag, version, or expected update timestamp, the save
sends it. A stale write returns a conflict rather than overwriting newer policy,
and the editor offers `Reload latest profile` (and `Review my draft against
latest` when a merge view exists). A save is never reported as successful when
the profile changed concurrently.

### 13.5 Completion

On success the editor replaces local state with the server response, clears the
structural log, updates the Provider Profiles query cache, announces `Model tier
policy saved`, and keeps the user near the section. The collection summary is not
updated optimistically before the server normalizes the policy.

---

## 14. Validation

Before submit the editor checks: at least one tier; exactly one default that
exists in the draft; trimmed labels; model and effort null or permitted;
`parameters` and `annotations` are objects; capability-blocked values resolved
where required. This aids usability and does not replace backend validation.

| Error path | Shown at |
| --- | --- |
| `model_tiers` | Section error |
| `model_tiers.1.model` / `.effort` | Tier 2 model / effort control |
| `model_tiers.1.parameters` | Tier 2 advanced disclosure |
| `default_model_tier` | Default radio group and summary |
| capability or stale version | Section warning or conflict panel |

After a failed save the first invalid control receives focus, a section summary
links to every invalid tier, and every draft value is kept; the editor never
closes or resets.

---

## 15. Harness/Provider Changes and Other States

### 15.1 Harness or Provider changes

Harness and Provider determine capabilities through canonical `runtime_id` and
`provider_id`; the [selection design](./HarnessProfileBackendSelection.md) owns
their labels and Backend disclosure. When either changes in a create form, the
editor fetches new capabilities, keeps tier drafts, revalidates every value,
marks incompatible ones in place without replacing them, offers `Reset
incompatible values to runtime defaults` (listing affected tiers, such as `Tier 1
model -> Runtime default`, before applying), and blocks save only for values the
new policy rejects. On an existing profile `runtime_id` is immutable, so
capability changes come only from Provider edits or catalog changes.

### 15.2 Loading

While profile data loads, the section header and tier skeletons render; no
default model or effort input appears. While capabilities load, current values
show immediately and only the choice menus are disabled, so cards do not jump.

### 15.3 Empty tier data (repair)

An empty tier array is invalid, so the editor shows "This profile has no model
tiers and cannot be saved in this state." with `[Create runtime-default Tier 1]`,
which creates one local runtime-default tier selected as default.

### 15.4 Read-only users

Read-only users see the full ordered stack, default, values, runtime-default
explanations, capability diagnostics, and authorized advanced metadata, rendered
as values rather than low-contrast disabled inputs, with no add, duplicate,
remove, reset, or save controls.

---

## 16. Layout and Accessibility

On wide screens model and effort share one row, with label, default radio, and
actions in the card header; on tablets they stay side by side only while each
keeps a usable width. On phones each tier is a full-width card with the number at
the top, stacked full-width controls, text-labelled destructive actions, and the
trailing full-width `Add tier`. The ordered list drops its indentation at narrow
widths, tier fieldsets opt out of their intrinsic `min-content` minimum, and
nesting is restrained (one card border, unstyled inner fieldset), so the editor
never scrolls horizontally.

Accessibility:

- the stack is an ordered list and each tier a fieldset with a legend such as
  `Tier 2`;
- default selection is one native radio group;
- model, effort, remove (`Remove Tier 2`), and duplicate controls have
  tier-specific names;
- add moves focus to the new tier and announces it; confirmed removal announces
  the removal and any renumbering;
- dialogs take and trap focus, close on Escape, and return focus to the trigger;
- default, warning, deprecated, and error states are never conveyed by color
  alone, and deprecated or unavailable options say so in accessible text;
- everything works without drag and drop, and reduced-motion preferences disable
  rail or card animation.

---

## 17. Structure and Tests

Normalization, payload construction, renumbering impact, and error mapping live
as pure helpers in `frontend/src/utils/providerProfileTiers.ts`, outside JSX, so
they are unit-tested without rendering Settings
(`frontend/src/utils/providerProfileTiers.test.ts`). The editor reuses shared
combobox, select, dialog, notice, and disclosure primitives and keeps runtime and
provider policy in backend responses rather than accumulating model constants in
`ProviderProfilesManager`. Component behavior is covered by
`frontend/src/components/settings/ProviderProfilesManager.test.tsx` and the
Settings conformance and mobile-layout tests in the same directory.
