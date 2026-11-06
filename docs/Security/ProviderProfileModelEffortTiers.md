# Provider Profile Model and Effort Tiers

**Related design documents:** [ProviderProfiles.md](./ProviderProfiles.md), [SecretsSystem.md](./SecretsSystem.md), [ManagedAndExternalAgentExecutionModel.md](../Temporal/ManagedAndExternalAgentExecutionModel.md), [SettingsPage.md](../UI/SettingsPage.md), [Provider Profile Tier Settings](../UI/ProviderProfileModelEffortTierSettings.md), [Workflow Model Selection](../UI/WorkflowModelSelection.md)

Status: **Implemented Tier/Custom authoring and resolution contract; profile scalar-data retirement remains transition work.**
Owners: MoonMind Engineering
Last Updated: 2026-11-06

> [!NOTE]
> A tier is a profile-local policy entry that maps a small integer such as `1`, `2`, or `3` to a runtime-specific model and optional effort level. Presets and workflow steps retain tier references. The backend resolves the final model and effort at submit or launch time.
>
> A tier can also follow a model family. **Defaults follow the latest eligible release in their model family until the operator chooses a specific version; that explicit choice remains pinned until the operator restores automatic selection.**
>
> Workflow authoring uses one Tier dropdown containing configured tier numbers and Custom, plus editable Model and Effort fields. Tier fallback is not a user-facing setting, including in Advanced mode. Existing explicitly strict requests retain their meaning during the bounded compatibility transition in §9.4.

---

## 1. Summary

MoonMind presets express model intent declaratively without hardcoding concrete model names in every step.

Example preset intent:

```text
Step 1 - Tier 1 - Generate a plan
Step 2 - Tier 2 - Implement the plan in the codebase
Step 3 - Tier 1 - Verify the implementation against the plan
Step 4 - Tier 3 - Make sure documents reference the correct code paths
```

The selected Provider Profile owns what each tier means. The built-in first-party profiles ship four family tiers, default to Tier 2, and use explicit `medium` effort:

| Tier | OpenAI via Codex | Anthropic via Claude Code |
| --- | --- | --- |
| 1 | Astra | Fable |
| **2 (default)** | **Sol** | **Opus** |
| 3 | Terra | Sonnet |
| 4 | Luna | Haiku |

Other profiles start with one Runtime default tier and can define any number of tiers. Model identifiers in examples are illustrative, not a maintained catalog.

A preset remains portable because it asks for `modelTier: 2`, not a particular model string with a particular effort. If the profile owner changes Tier 2, future launches use the updated policy without editing every preset.

A new unconfigured workflow previews the profile's default tier while leaving authored selection omitted, so an untouched save keeps following the profile default. Selecting a numbered tier authors its reference. Editing either field, or selecting Custom, detaches the pair from tier policy without editing the Provider Profile.

---

## 2. Design Goals

The tier system must support:

1. **Profile-local tier definitions.** Numbers refer to the selected Provider Profile. The same number on two profiles need not mean the same model, cost, or effort.
2. **Preset portability.** Preserve numeric tier intent through presets, expansion, saving, scheduling, and launch.
3. **Backend authority.** The frontend previews; one backend resolver determines model and effort.
4. **Predictable defaults.** New tier requests use the existing clamp without a fallback setting.
5. **Minimum one tier.** A null tier model or effort intentionally uses the runtime default.
6. **Runtime-specific effort.** Effort values and application mechanisms stay provider/runtime-specific.
7. **Auditability.** Record requested and effective tiers, resolved values, sources, fallback, and actual effort application.
8. **Explicit Custom intent.** Custom preserves the full displayed pair, including a blank field, without reactivating a tier.
9. **Family tracking with durable pins.** Untouched family tiers follow new eligible releases; explicit pins stay pinned.

---

## 3. Non-Goals

This design does not:

- create a global cross-provider quality scale or universal effort enum,
- make tiers independent capacity pools or replace profile slot leasing,
- remove direct model/effort control,
- make the browser authoritative for launch policy,
- freeze a tier-based preset to its current preview,
- introduce a snapshot mode, policy engine, selector service, discovery scheduler, compatibility fingerprint, or approval gate,
- expose fallback policy in normal or Advanced UI,
- treat tier fallback or family resolution as model-failure retry, provider failover, or authorization to change credentials,
- pin CLI versions, images, or Omnigent revisions through a model pin.

---

## 4. Key Concepts

### 4.1 Provider Profile tier policy

A Provider Profile owns `model_tiers` and `default_model_tier`. Array order defines one-based tier numbers:

```text
model_tiers[0] -> Tier 1
model_tiers[1] -> Tier 2
model_tiers[2] -> Tier 3
```

### 4.2 Requested tier

The authored numeric reference is the requested tier:

```yaml
runtime:
  modelTier: 2
```

Populating Model and Effort from this tier does not author model/effort overrides.

### 4.3 Effective tier

The effective tier is the tier actually used after backend resolution:

```text
requested tier: 3
configured tiers: 2
effective tier: 2
fallback reason: requested_tier_above_configured_range
```

Do not overwrite a saved requested tier merely because the currently selected profile resolves it to another effective tier.

### 4.4 Concrete model and effort

These are values resolved for a particular preview or launch:

```yaml
effective_model: gpt-5.5
effective_effort: xhigh
```

Historical launch values are not current authoring policy and must not be mistaken for Custom intent when reconstructing a draft.

### 4.6 Model family and update mode

A family tier names a machine-readable `family` (for example `opus`) and a `model_mode`:

- `automatic` follows the newest eligible release of that family for the selected profile. New admissions pick up a new release once the profile's catalog reports it.
- `pinned` keeps the saved `model` until the operator chooses "Use latest automatically". Pinning the version that is already current still records a pin.

Example: a profile's Opus tier currently resolves to `claude-opus-5-5`. The operator selects `claude-opus-5`, which pins the tier. When Opus 6 ships, the tier still runs `claude-opus-5`. An untouched automatic Opus tier on another profile moves to Opus 6 for new admissions.

Changing only effort, the label, the default tier, or unrelated settings never changes the model mode. Labels never route. A catalog failure does not change intent, and a pin is durable intent, not a guarantee that the provider keeps offering that version.

### 4.5 Custom selection

Custom is the user-facing replacement for separate hard-override controls. It bypasses tier selection and preserves the complete displayed pair:

```yaml
runtime:
  model: provider-model-b
  effort: high
```

There is no active `modelTier` in this payload. `custom` is a UI selection value, never a string accepted in the integer `modelTier` field.

Selecting Custom directly leaves Model and Effort unchanged. Editing only Model preserves Effort, and editing only Effort preserves Model. Matching a tier's current values does not automatically switch Custom back to a numbered tier.

A blank Custom field means runtime default, not profile default tier or workflow inheritance. Both fields are authored, including explicit nulls when blank. This distinction must survive normalization and persistence as described in §7.2.

---

## 5. Provider Profile Contract

### 5.1 Canonical fields

```yaml
ManagedAgentProviderProfile:
  model_tiers: [ProviderModelEffortTier]
  default_model_tier: int

ProviderModelEffortTier:
  label: str | null
  family: str | null          # omitted when absent
  model_mode: automatic | pinned | null   # omitted when absent
  model: str | null
  effort: str | null
  parameters: dict[str, object]
  annotations: dict[str, object]
```

### 5.2 Field semantics

#### `model_tiers`

An ordered non-empty JSON array of tier objects. Reordering changes future numeric references; tier numbers are not database identities.

#### `default_model_tier`

The one-based tier used when there is no explicit model selection after workflow/step inheritance:

```text
default_model_tier >= 1
default_model_tier <= len(model_tiers)
```

An explicit Custom selection is not an omitted selection.

#### `label`

An optional human-readable description such as `Plan and verify`, `Implementation`, or `Docs audit`. Labels are not routing keys.

#### `family` and `model_mode`

Both are present together or not at all. `automatic` requires a null `model`; `pinned` requires a concrete `model`. Tiers without a family keep the established shape and meaning, so existing exact-model and Runtime default tiers stay valid. Observed resolutions are not stored in the tier: they come from the profile's catalog evidence, so catalog writes can never replace authored policy.

#### `model`

A runtime/provider-specific model string interpreted by the runtime adapter. On a tier without a family, a null value means runtime default after tier selection, not `default_model` compatibility fallback.

#### `effort`

A runtime/provider-specific effort string. A null value means runtime default effort if one exists, otherwise omit effort. It does not read `default_effort` compatibility state.

The tier editor's capability catalog lists effort levels in ascending order: `low`, `medium`, `high`, `xhigh`, `max`. A runtime or model that records its own supported values still decides: the seeded OpenCode models top out at `xhigh`, so they reject `max` instead of silently forwarding it.

#### `parameters`

Optional non-secret runtime parameters, for example:

```yaml
parameters:
  temperature: 0
  output_format: strict_json
```

#### `annotations`

Optional metadata, for example:

```yaml
annotations:
  costClass: premium
  recommendedFor: [implementation, complex_refactor]
```

Annotations do not affect launch. Parameters and annotations must not contain credentials or credential references.

---

## 6. Declarative Examples

### 6.1 Built-in Anthropic profile with one pin

```yaml
profile_id: claude_anthropic_oauth
runtime_id: claude_code
provider_id: anthropic
model_tiers:
  - {label: Fable, family: fable, model_mode: automatic, model: null, effort: medium}
  - {label: Opus, family: opus, model_mode: pinned, model: claude-opus-5, effort: medium}
  - {label: Sonnet, family: sonnet, model_mode: automatic, model: null, effort: medium}
  - {label: Haiku, family: haiku, model_mode: automatic, model: null, effort: medium}
default_model_tier: 2
```

### 6.2 Runtime-default-only profile

A minimal profile satisfies the minimum-one-tier rule without hardcoding a model:

```yaml
model_tiers:
  - label: Runtime default
    model: null
    effort: null
    parameters: {}
    annotations: {}
default_model_tier: 1
```

### 6.3 Exact-model custom profile

Tiers without a family keep exact models, such as `provider-small-coding` at `medium` and `provider-frontier-coding` at `xhigh`, with any default tier.

---

## 7. Preset Contract

### 7.1 Preserve tier references

Presets request tiers through step runtime metadata:

```yaml
steps:
  - title: Generate a plan
    skill: {id: auto, runtime: {modelTier: 1}}
  - title: Implement the plan
    skill: {id: auto, runtime: {modelTier: 2}}
```

Profile selection (`providerProfileRef` or `profileSelector`) is independent of tier selection.

### 7.2 Mutually exclusive authored selections

New workflow and step authoring has three semantic shapes:

| Intent | Authored model-selection fields |
| --- | --- |
| Numbered tier | Positive integer `modelTier`; no explicit `model` or `effort` overrides |
| Custom | No active `modelTier`; both `model` and `effort` are explicitly authored as strings or null |
| Inherit/no explicit selection | Model-selection fields are omitted; existing workflow inheritance and then profile default apply |

For example, `{model: null, effort: high}` uses the runtime model with explicit effort, and `{model: null, effort: null}` uses runtime defaults for both. Explicit nulls and omitted fields have different meanings for these newly authored selections. Do not strip the full Custom pair into an empty object, infer Custom only from a non-empty model, or allow generic inheritance merging to refill its null fields. Preserve authored presence through the existing runtime-intent and execution contracts rather than adding another persistent policy system.

Saved legacy partial/mixed payloads retain their original model-selection fields and field presence on hydration and unrelated saves, using their existing provenance and resolution rules. For example, `modelTier: 2` with only `effort: high` keeps the model dynamic under Tier 2; displaying its effective pair does not authorize replacing that intent with concrete Custom strings. Do not reinterpret historical nulls or resolved launch values as newly authored Custom.

Show a concise saved-source explanation and, where no dropdown value represents the payload, a nonselectable saved-selection placeholder. Normalize to a numbered tier or full-pair Custom only after an explicit selector edit; a Custom save must not guess an unresolved companion value.

Changing back to a numbered tier removes Custom overrides. Changing to Custom removes active tier intent and tier-only preview/fallback fields. Legacy strict policy is handled explicitly under §9.4. Explicit non-model runtime parameters, credential references, and unrelated settings are preserved.

### 7.3 Inheritance and schedules

An unconfigured step continues to inherit workflow selection. Viewing, expanding, or saving unrelated fields must not materialize inherited previews into step overrides. An explicit step selection overrides the model/effort selection as a unit rather than leaving stale parent model fields to defeat a child tier or refill Custom nulls.

Scheduled tier requests follow the selected profile's current tier mapping at launch. Explicit Custom strings stay explicit. Explicit null Custom fields follow runtime defaults, not a former tier. Existing omitted/default selections retain their inherited semantics. This change does not introduce schedule snapshots.

---

## 8. Frontend and Backend Responsibilities

### 8.1 Principle

The frontend previews tier resolution. The backend resolves policy authoritatively. The interaction contract is owned by [Workflow Model Selection](../UI/WorkflowModelSelection.md), while profile-tier editing remains owned by [Provider Profile Tier Settings](../UI/ProviderProfileModelEffortTierSettings.md).

### 8.2 Frontend responsibilities

1. Show Tier, Model, and Effort using one shared workflow/step selector behavior.
2. Populate the Tier dropdown from the selected profile's configured numbers plus Custom. Do not offer arbitrary numbers, a fallback selector, or another Default mode.
3. Preview a new unconfigured workflow from `default_model_tier`, which need not be 1, keeping authored selection omitted until a user change. Preserve presets, drafts, reruns, legacy payloads, and inherited step selections.
4. Populate both fields on numbered-tier selection without submitting them as overrides. An automatic family tier shows its current resolution as text, never as an authored model.
5. Enter Custom on a user edit of either field, preserving the other; selecting Custom directly preserves both. Keep Custom until the user selects a tier, even if values match.
6. Refresh previews on profile changes without overwriting Custom input or applying stale responses.
7. Distinguish unavailable profile data from a profile with no valid tiers. Never invent tier numbers.
8. Show fallback, unavailable-family, or legacy-strict diagnostics only when relevant.

### 8.3 Backend responsibilities

1. Validate canonical profile tier definitions and authored runtime intent.
2. Resolve the selected Provider Profile using the existing selection owner.
3. Resolve inherited, tier, or Custom intent without competing precedence paths.
4. Resolve runtime defaults in the selected provider/runtime context.
5. Apply the existing default clamp and retain explicitly strict legacy semantics while required.
6. Preserve explicit null Custom semantics through persistence and launch.
7. Record requested and actual resolution without rewriting historical execution data.
8. Retain necessary credential, provider, and launch validation. Do not add preview or catalog freshness gates to implement this selector.

### 8.4 Why the frontend is not authoritative

Profile data can change between preview and launch. Workflows can originate from APIs, schedules, preset expansion, or remediation without a browser. Saving a concrete preview instead of a tier reference would remove portability and prevent future tier policy updates from taking effect.

### 8.5 Advisory preview snapshots

Existing advisory preview metadata may remain useful:

```yaml
runtime:
  modelTier: 3
  tierPreview:
    profileId: codex_openai_api
    profileVersion: 42
    model: gpt-5.3-codex-spark
    effort: xhigh
```

A mismatch causes authoritative re-resolution and an observable diagnostic, not a new rejection gate. Do not implement the superseded strict-preview proposal or require matching profile versions to launch. Profile versions may still protect actual concurrent profile writes.

A preview is derived data, not evidence of a user edit. It must not switch a numbered selection to Custom, pin runtime defaults, or overwrite a Custom draft after an asynchronous response.

---

## 9. Resolution Semantics

### 9.1 Inputs

Use the existing canonical resolver and runtime-intent contracts. Inputs include selected profile, runtime, authored selection and field presence, workflow inheritance, runtime defaults, and optional advisory preview. The existing `tier_fallback` input remains only where required for legacy strict consumers.

`requested_model: null` alone is not enough to distinguish an omitted selection from an explicit runtime-default Custom field. The boundary must preserve the authored selection semantics rather than infer them from truthiness.

### 9.2 Resolution order

Resolve selection before resolving individual values:

1. Use an explicitly authored step selection, otherwise inherit the workflow selection.
2. For Custom, resolve the full explicit pair. A string is a task override. Null uses the compatible runtime default or remains absent when no such default exists. Skip requested tier, profile default tier, and legacy scalar profile defaults.
3. For a numbered tier, resolve the requested number against the selected profile, then resolve that tier's model and effort. An automatic family tier resolves its family under §9.6.
4. Without an explicit or inherited selection, use the profile's `default_model_tier`.
5. A null field in the selected tier uses the compatible runtime default, otherwise no value.

`default_model` and `default_effort` are not a second desired-state source. Their retirement and data migration must agree with the tier-only persistence contract in the Settings design. Do not claim that a blank means Runtime default while allowing an old compatibility field to supply it instead.

The resolver distinguishes canonical full-pair Custom from saved partial/mixed inputs using authored field presence. Canonical Custom and null tier fields bypass legacy scalar defaults. Saved model-only or pre-tier profile inputs retain their legacy resolution where still supported; unrelated saves preserve their shape under §7.2. Retiring those data sources remains separate from truthful runtime defaults on the canonical path.

### 9.3 Tier fallback

Newly authored numbered selections omit `tierFallback` and use the existing backend clamp:

```python
def effective_tier(requested_tier: int | None, *, default_tier: int, tier_count: int) -> int:
    raw = requested_tier or default_tier or 1
    return max(1, min(raw, tier_count))
```

| Requested tier | Configured count | Effective tier | Reason |
| --- | ---: | ---: | --- |
| Omitted, with no Custom or inherited selection | 3 | `default_model_tier` | `profile_default_tier` |
| 1 | 3 | 1 | None |
| 2 | 3 | 2 | None |
| 3 | 2 | 2 | `requested_tier_above_configured_range` |

The API rejects non-integers and numbers below 1. Defensive clamping does not make malformed input valid. An absent or empty tier array is not a license to invent a profile policy.

Restricting the dropdown prevents ordinary out-of-range authoring, but saved requests, profile changes, APIs, and scheduled launches still need this backend behavior. Record the original requested tier and actual effective tier.

This is ordinal resolution only. It is not automatic model-failure retry or cross-provider failover.

### 9.4 Legacy strict requests and removal boundary

An existing request may explicitly contain:

```yaml
runtime:
  modelTier: 3
  tierFallback: strict
```

Until its saved consumers drain, retain the existing `requested_model_tier_unavailable` rejection for such a request instead of silently clamping it.

The form never generates strict policy or exposes a fallback selector. A loaded strict request explains its retained requirement, unrelated edits preserve it, and an explicit tier or Custom choice replaces it. Public API submission and preset/draft/schedule creation reject newly authored `tierFallback: strict` with an actionable error; a raw copy or caller-supplied flag is not saved provenance. Unchanged saved records, recurring launches, reruns, recovery, and replay obtain strict intent from the server-loaded record or accepted durable payload. Retire backend strict handling only after those sources drain or a separately authorized migration preserves their intent.

### 9.5 Output

Keep existing resolution metadata:

```yaml
ResolvedModelEffort:
  model: str | null
  effort: str | null
  requested_model_tier: int | null
  effective_model_tier: int | null
  tier_label: str | null
  model_source: str
  effort_source: str
  fallback_reason: str | null
  effort_application_status: str | null
```

Numbered/default selection uses requested or profile-default tier sources as appropriate. Custom has no active requested/effective tier or tier fallback. Explicit strings use the existing task-override source; nulls report runtime-default or no-value sources honestly. Retain old source values in historical records instead of renaming them during this change.

Keep the exact serialized effort-application tokens: `applied`, `not_supported`, `metadata_only`, `emulated`, or `unknown`, according to the runtime's actual behavior. Human-readable labels such as Unsupported or Metadata only do not rename wire values or persisted diagnostics.

### 9.6 Family resolution

"Latest automatically" means the newest generally available release in that family that the selected profile's account, authentication route, endpoint, and installed harness report. Only that profile's catalog evidence counts, never an ambient host preview. Strict family ID shapes and major/minor ordering keep specialized lines (`gpt-6.1-sol-codex`), previews, `[1m]` variants, hidden or retired rows, and other families or providers out. Declared family metadata such as Anthropic's `line` wins. Releases that do not list the tier's effort are skipped.

Codex and Claude Code profiles have no separate discovery probe: each managed launch reads the exact host's model options, which become the profile's catalog evidence for that credential generation. A new release takes effect after a launch on that profile observes it, once the account and installed CLI offer it. OpenCode keeps its pinned-runtime revalidation. A failed or empty read keeps the last usable observation. Evidence from a replaced credential counts as unobserved. An observation that omits a family refuses that tier only within `OPENCODE_MODEL_CATALOG_MAX_AGE_HOURS` (6 by default); later, its launch re-reads the catalog. Unobserved, a built-in family uses its starting release (for example `claude-opus-5-5`), reported as `unverified`; the exact host still confirms every selected model and fails with `omnigent_model_unavailable` instead of substituting.

Admission stores the concrete resolution. Queued work, retry, recovery, and replay keep it; catalog changes affect only new admissions, including recurring occurrences and new-admission reruns. A selected or default tier without an eligible release fails with `model_tier_family_unavailable` and a reason, never falling back to another tier, family, account, provider, or the runtime default. A pin the catalog reports as deprecated, retired, or absent keeps its saved value and shows that status; the exact host refuses it. Neither Codex `suppress_default_model_flag`, an account alias, a first-party `ANTHROPIC_MODEL`, nor a passed-through model or effort launch argument replaces a tier-resolved model. If Omnigent clears a session model anyway, the result's `modelSelection` reports `replaced`.

---

## 10. Runtime Strategy Integration

### 10.1 Model application

Runtime adapters consume the canonical resolved model through their existing flag, configuration, or environment shaping. Codex, Claude Code, and OpenCode are harness-specific consumers, not reasons to create parallel selector or resolver systems.

### 10.2 Effort application

Effort remains runtime-specific. Report whether it was applied natively, through configuration/environment, as metadata only, or not supported. A stored effort value is not proof it was applied.

```yaml
resolvedEffort: xhigh
effortApplicationStatus: not_supported
```

### 10.3 Tier parameters

Tier parameters may be merged after tier resolution and before command construction:

```text
explicit step parameters override tier parameters
```

Custom detaches from tier-owned parameters as well as tier model/effort. Remove stale tier-derived values and previews, but preserve separately authored non-model runtime parameters. Do not silently retain a former tier as a hidden source.

### 10.4 Credentials

Tiers do not own credentials, OAuth volumes, or secret materialization. Custom and family selection never authorize another provider, account, or credential.

---

## 11. Provider Profile Manager Interaction

The Provider Profile Manager remains a profile-level slot and cooldown manager: it reserves a profile slot, then the backend resolves tier or Custom intent in that profile/runtime context. Tiers are not capacity pools or profile choices; one profile runs different tiers concurrently within its existing capacity.

---

## 12. Persistence Model

### 12.1 Canonical profile fields

The canonical profile policy is a non-empty JSONB `model_tiers` array and a valid one-based `default_model_tier`. Existing database/application validation enforces array shape, minimum length, and default bounds.

### 12.2 Migration from legacy defaults

Convert a genuinely pre-tier profile's legacy default model/effort into one canonical tier, or create a runtime-default tier when neither exists. Set the default to 1 only for that backfill. Never replace an existing authored tier array or reset its default during a repeated migration.

Every creation path (seeding, API, OAuth enrollment) gives new first-party OpenAI/Codex and Anthropic/Claude Code profiles the family template. Startup upgrades a seeded first-party profile only while its policy is exactly the seed's single Runtime default tier with no legacy defaults, so restarts are no-ops. Customized arrays, explicit legacy values, credentials, enablement, accounts, and the default profile are untouched; the editor's `Use automatic family defaults` adopts the template only on request.

Obsolete profile default fields and callers go with the tier-only persistence work in [Provider Profile Tier Settings §15.2](../UI/ProviderProfileModelEffortTierSettings.md#152-tier-only-persistence); [Provider Profiles §5.5](./ProviderProfiles.md#55-default-model-ownership) and [Codex CLI via OpenRouter §7](../ManagedAgents/CodexCliOpenRouter.md#7-provider-profile-shape) share this contract. Do not keep denormalized mirrors or rebuild missing tiers from them in the frontend.

This profile-data conversion is distinct from preserving legacy strict workflow requests under §9.4. Historical execution values and active work must survive both transitions.

### 12.3 Historical data and authored presence

Runs retain their actual launch model, effort, and sources independently of future profile edits. Preserve authored tier/Custom/inherit intent separately from resolved diagnostics through existing execution payloads. Explicit Custom nulls must survive serialization, normalization, artifact storage, and draft reconstruction.

Reuse existing profile update timestamps as the profile version; catalog observations do not change it. Do not add a policy digest or version registry.

---

## 13. API Contract

### 13.1 Provider Profile create/update

```json
{
  "model_tiers": [
    {"label": "Sol", "family": "sol", "model_mode": "automatic", "model": null, "effort": "medium", "parameters": {}, "annotations": {}},
    {"label": "Exact", "model": "gpt-custom", "effort": "high", "parameters": {}, "annotations": {}}
  ],
  "default_model_tier": 1
}
```

A full-array PATCH must carry each tier's `family` and `model_mode`; the editor preserves them for unchanged rows.

### 13.2 Provider Profile response

The desired-state response contains canonical tier policy, not a second model/effort default mirror:

```yaml
profile_id: codex_openai_api
model_tiers: [tier_one, tier_two]
default_model_tier: 1
```

### 13.3 Preview endpoint

Reuse the existing profile-tier preview route and resolver rather than adding a competing endpoint or client resolver. Preview inputs and results retain authored selection, requested/effective tiers, resolved values, and sources where available. Custom preview must use the same explicit-null semantics as launch.

Items report requested/effective tiers, model, effort, fallback reason, and sources. Family tiers add `modelFamily`, `modelMode`, and `familyResolutionStatus`; an unavailable family returns its item with a null model and `unavailableReason` instead of failing the whole preview. Preview never replaces launch-time resolution or becomes a new admission gate.

### 13.4 Runtime intent

Use §7.2's mutually exclusive shapes for new workflow and step authoring. Preserve omitted versus explicitly null Custom fields through validation, scheduling, preset expansion, edit/rerun, and runtime parameter construction. Untouched saved legacy mixed fields retain their original meaning. New authoring omits fallback settings, and the submission boundary rejects newly authored strict intent. Existing saved strict policy remains readable and honored through the trusted unchanged-save/replay paths in §9.4 until its documented removal boundary.

---

## 14. Observability and Audit

Every launched step records actual resolution:

```yaml
modelTierResolution:
  providerProfileId: codex_openai_api
  requestedModelTier: 3
  effectiveModelTier: 2
  tierLabel: Implementation
  fallbackReason: requested_tier_above_configured_range
  resolvedModel: gpt-5.5
  resolvedEffort: xhigh
  modelSource: requested_tier
  effortSource: requested_tier
  effortApplicationStatus: applied
  previewMismatch: false
  modelFamily: opus              # family tiers only
  modelMode: automatic
  familyResolutionStatus: resolved   # resolved, unverified, or pinned
  familyResolutionSource: profile_catalog
```

Show fallback concisely when it occurs:

```text
Tier 3 is not configured for this profile. Using Tier 2.
```

Custom records no active tier and retains honest per-field sources. An unavailable runtime default is reported as unavailable, not invented or borrowed from another provider. Historical workflow details show the values used by that attempt even after profile changes.

---

## 15. Validation Rules

### 15.1 Profile validation

```text
model_tiers is a non-empty array of valid tier objects
default_model_tier is within the configured array
parameters and annotations contain no credentials or credential references
```

### 15.2 Preset and workflow validation

```text
modelTier is an integer >= 1 when present
custom is never accepted as the value of modelTier
new numbered selections do not carry hard model/effort overrides
new Custom selections have no active modelTier and retain the complete nullable pair
omission remains distinguishable from explicitly authored runtime-default values
new strict intent is rejected at authoring/submission; trusted saved strict intent retains its behavior under §9.4
```

Existing unknown model/effort strings remain visible and round-trippable where backend policy permits. Missing advisory catalog evidence alone must not erase input or create a new validation gate. Genuine provider/runtime constraints remain authoritative.

### 15.3 Runtime validation

Recheck selected profile existence, required launch readiness, valid tier policy, compatible model/effort application, and credential boundaries. Resolve an unavailable numbered tier with the default clamp or the retained explicit legacy strict policy. Custom does not re-enter tier resolution because a field is null.

---

## 16. Implementation and Transition

Workflow and step forms share `ModelSelectionFields` and the pure transitions in `modelSelection.ts`, and only author a numbered tier or complete nullable pair after an explicit selection edit. Backend normalization, inheritance, preset expansion, recurring targets, preview, Omnigent planning, and launch carry the same field presence. The public submission validator rejects new strict intent; trusted recurrence, recovery, exact reruns, and history readers still honor accepted strict requests until their saved consumers drain.

Family policy lives in `moonmind/provider_profiles/` (`model_tiers.py`, `model_families.py`, `catalog_observations.py`); `model_resolver.py` alone turns automatic tiers into concrete models.

The Run and AgentRun workflows use the `run-model-selection-presence-4636-v1` and `agent-run-model-selection-presence-4636-v1` patches for nullable request and update behavior. Historical attempts are not rewritten.

---

## 17. Acceptance Tests

### Provider Profile validation and migration

- Reject zero tiers and invalid default indexes.
- Accept null model/effort runtime-default tiers.
- Reject credential-bearing tier data.
- Backfill only genuinely pre-tier profiles and preserve already-authored policy on repeat execution.
- Fresh first-party profiles have four automatic family tiers, default Tier 2, and `medium`; untouched seeds upgrade once and customized profiles do not.
- Preserve actual historical run values while removing scalar default mirrors.

### Resolution

- Omitted selection uses workflow inheritance and then the profile default; requested Tier 2 resolves to `model_tiers[1]`; Tier 3 on a two-tier profile clamps to Tier 2 with both values recorded.
- An automatic Opus tier advances from 5.5 to 6 for new admissions; tiers pinned to 5 or to 5.5 do not; admitted work keeps its resolution.
- An unavailable family is refused without substitution while other tiers stay usable.
- Trusted saved strict requests still reject an unavailable tier; new strict authoring, raw legacy copies, and forged provenance are rejected.
- Saved Tier 2 plus an effort-only override follows a later Tier 2 model change until an explicit selection edit replaces it.
- Custom uses the complete authored pair; null Custom and null tier fields use runtime defaults without profile-tier, scalar-default, or parent-field leakage.
- A step-level selection is never defeated or refilled by inherited workflow fields.

### Frontend/backend contract and launch

- Every §8.2 responsibility holds, including previews of a non-1 default tier, Custom transitions, and delayed responses that never overwrite edits.
- Save/reload, preset expansion, edit/rerun, recurring submission, and launch parameter construction preserve all selection shapes and explicit nulls.
- Preview mismatch records a diagnostic rather than adding a launch gate.
- Runtime adapters preserve exact `not_supported` and `metadata_only` effort-application values.
- Existing execution history and active work are not rewritten.

Use focused unit/interaction tests plus credential-free integration and browser journeys through production components. Broader suites run in CI. Do not test documentation wording or require manual approval to complete implementation.

---

## 18. Settled Decisions and Scope Boundaries

- Tier fallback is removed from all workflow-authoring UI, not moved to Advanced.
- New tier requests use the existing backend clamp without authoring a policy setting.
- Newly authored strict intent is rejected at the submission cutoff; existing trusted strict requests are retained until their consumers drain or migrate without silently weakening intent.
- Tier-based schedules follow current profile policy. No snapshot feature is added.
- Profile scalar default mirrors are not a permanent alternative to tier policy.
- Settings owns ordered tier editing and structural-change warnings. This workflow-selector change does not redesign that editor.
- Cost annotations remain metadata. No quota, billing, provider failover, or capacity subsystem is added.

---

## 19. Decision Summary

Provider Profiles own model/effort tiers, including automatic or pinned family tiers. Workflow authoring offers configured tier numbers and Custom in one Tier dropdown. Model and Effort show the selected tier's values and remain editable. A user edit enters Custom while preserving the pair, and direct Custom selection leaves both fields unchanged.

Numbered selections persist tier references, Custom persists explicit nullable values, and unconfigured steps retain inheritance. The backend remains authoritative and uses the existing default clamp for new tier requests. Explicit legacy strict requirements remain observable and honored until safely retired. The implementation adds neither another resolver nor another gate, preserves active work and historical records, and removes obsolete UI and conflicting guidance together.
