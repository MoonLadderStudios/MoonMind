# Provider Profile Model and Effort Tiers

**Related design documents:** [ProviderProfiles.md](./ProviderProfiles.md), [SecretsSystem.md](./SecretsSystem.md), [ManagedAndExternalAgentExecutionModel.md](../Temporal/ManagedAndExternalAgentExecutionModel.md), [SettingsPage.md](../UI/SettingsPage.md), [Provider Profile Tier Settings](../UI/ProviderProfileModelEffortTierSettings.md), [Workflow Model Selection](../UI/WorkflowModelSelection.md)

Status: **Desired-state design. The tier-or-custom authoring changes below require implementation.**
Owners: MoonMind Engineering
Last Updated: 2026-09-30

> [!NOTE]
> A tier is a profile-local policy entry that maps a small integer such as `1`, `2`, or `3` to a runtime-specific model and optional effort level. Presets and workflow steps retain tier references. The backend resolves the final model and effort at submit or launch time.
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

The selected Provider Profile owns what each tier means:

```yaml
profile_id: codex_openai_api
runtime_id: codex_cli
provider_id: openai

model_tiers:
  - label: Plan and verify
    model: gpt-5.5
    effort: medium
  - label: Implementation
    model: gpt-5.5
    effort: xhigh
  - label: Documentation path audit
    model: gpt-5.3-codex-spark
    effort: xhigh

default_model_tier: 1
```

The identifiers in examples are illustrative, not a maintained model catalog.

A preset remains portable because it asks for `modelTier: 2`, not a particular model string with a particular effort. If the profile owner changes Tier 2, future launches use the updated policy without editing every preset.

A new, otherwise unconfigured workflow selects the profile's default tier and shows its model and effort. Selecting another numbered tier replaces both displayed values. Editing either field, or selecting Custom directly, detaches the pair from tier policy. Custom preserves both displayed field values and does not edit the Provider Profile.

---

## 2. Design Goals

The tier system must support:

1. **Profile-local tier definitions.** Numbers refer to the selected Provider Profile. The same number on two profiles need not mean the same model, cost, or effort.
2. **Preset portability.** Preserve numeric tier intent through presets, expansion, saving, scheduling, and launch.
3. **Backend authority.** The frontend displays a preview. One backend resolver determines effective model and effort.
4. **Predictable defaults.** New UI-authored tier requests use the existing clamp behavior without requiring a fallback setting.
5. **Minimum one tier.** Every Provider Profile has at least one tier. A null tier model or effort intentionally uses the runtime default.
6. **Runtime-specific effort.** Effort values and application mechanisms remain provider/runtime-specific.
7. **Auditability.** Record requested and effective tiers, resolved values, sources, fallback, and actual effort application.
8. **Explicit Custom intent.** Switching to Custom preserves the full displayed model/effort pair, including an explicitly blank field, without secretly reactivating a tier.

---

## 3. Non-Goals

This design does not:

- create a global cross-provider quality scale or universal effort enum,
- make tiers independent capacity pools or replace profile slot leasing,
- remove direct model/effort control,
- make the browser authoritative for launch policy,
- freeze a tier-based preset to its current preview,
- introduce a snapshot mode, new policy engine, selector service, compatibility fingerprint, or approval gate,
- expose fallback policy in normal or Advanced UI,
- treat tier fallback as model-failure retry, provider failover, or authorization to change credentials.

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
  model: str | null
  effort: str | null
  parameters: dict[str, object]
  annotations: dict[str, object]
```

### 5.2 Field semantics

#### `model_tiers`

An ordered non-empty JSON array of tier objects. Reordering is a policy change because it changes future numeric references. Tier numbers are not independent database identities.

#### `default_model_tier`

The one-based tier used when there is no explicit model selection after workflow/step inheritance:

```text
default_model_tier >= 1
default_model_tier <= len(model_tiers)
```

An explicit Custom selection is not an omitted selection.

#### `label`

An optional human-readable description such as `Plan and verify`, `Implementation`, or `Docs audit`. Labels are not routing keys.

#### `model`

A runtime/provider-specific model string interpreted by the runtime adapter. A null value means runtime default after tier selection, not `default_model` compatibility fallback.

#### `effort`

A runtime/provider-specific effort string. A null value means runtime default effort if one exists, otherwise omit effort. It does not read `default_effort` compatibility state.

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

Annotations are not required launch behavior. Parameters and annotations must not contain raw credentials or credential references.

---

## 6. Declarative Examples

### 6.1 Codex CLI OpenAI profile

```yaml
profile_id: codex_openai_api
runtime_id: codex_cli
provider_id: openai
provider_label: OpenAI
model_tiers:
  - label: Plan and verify
    model: gpt-5.5
    effort: medium
    parameters: {}
    annotations:
      recommendedFor: [planning, verification]
  - label: Implementation
    model: gpt-5.5
    effort: xhigh
    parameters: {}
    annotations:
      recommendedFor: [implementation, refactor]
  - label: Documentation path audit
    model: gpt-5.3-codex-spark
    effort: xhigh
    parameters: {}
    annotations:
      recommendedFor: [documentation, path_audit]
default_model_tier: 1
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

### 6.3 Cost-biased custom profile

```yaml
model_tiers:
  - label: Cheap planning
    model: provider-small-coding
    effort: medium
  - label: Standard implementation
    model: provider-coding
    effort: high
  - label: Expensive escalation
    model: provider-frontier-coding
    effort: xhigh
default_model_tier: 2
```

---

## 7. Preset Contract

### 7.1 Preserve tier references

Presets request tiers through step runtime metadata:

```yaml
steps:
  - title: Generate a plan
    instructions: Generate a concise implementation plan.
    skill:
      id: auto
      runtime:
        modelTier: 1
  - title: Implement the plan
    instructions: Implement the approved plan in the codebase.
    skill:
      id: auto
      runtime:
        modelTier: 2
  - title: Verify implementation
    instructions: Verify the implementation against the plan.
    skill:
      id: auto
      runtime:
        modelTier: 1
  - title: Audit documentation paths
    instructions: Make sure documentation references the correct code paths.
    skill:
      id: auto
      runtime:
        modelTier: 3
```

Profile selection remains independent of tier selection:

```yaml
runtime:
  providerProfileRef: codex_openai_api
  modelTier: 2
```

or:

```yaml
profileSelector:
  providerId: openai
  tagsAll: [default]
runtime:
  modelTier: 2
```

### 7.2 Mutually exclusive authored selections

New workflow and step authoring has three semantic shapes:

| Intent | Authored model-selection fields |
| --- | --- |
| Numbered tier | Positive integer `modelTier`; no explicit `model` or `effort` overrides |
| Custom | No active `modelTier`; both `model` and `effort` are explicitly authored as strings or null |
| Inherit/no explicit selection | Model-selection fields are omitted; existing workflow inheritance and then profile default apply |

Example Custom using the runtime model and an explicit effort:

```yaml
runtime:
  model: null
  effort: high
```

Example Custom using runtime defaults for both fields:

```yaml
runtime:
  model: null
  effort: null
```

Explicit nulls and omitted fields have different meanings for these newly authored selections. Do not strip the full Custom pair into an empty object, infer Custom only from a non-empty model, or allow generic inheritance merging to refill its null fields. Preserve authored presence through the existing runtime-intent and execution contracts rather than adding another persistent policy system.

The implementation must normalize legacy partial/mixed payloads at the owning boundary using their existing provenance and resolution rules. Do not reinterpret every historical null as newly authored Custom. An editable legacy override is shown as Custom with its effective pair preserved. Historical records and active attempts remain unchanged. A safe canonical save must not guess an unresolved companion value.

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
3. Initialize a new unconfigured workflow from `default_model_tier`, which need not be 1. Preserve presets, drafts, reruns, and inherited step selections.
4. Populate both fields on numbered-tier selection without submitting them as overrides.
5. Enter Custom on a user edit of either field and preserve the other field. Selecting Custom directly preserves both fields.
6. Keep Custom selected until the user explicitly selects a tier, even if its values match a tier.
7. Refresh tier previews on profile changes without overwriting Custom input or applying stale responses.
8. Distinguish unavailable profile data from a profile with no valid tiers. Never invent available tier numbers.
9. Show concise fallback or legacy-strict diagnostics only when relevant.

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
3. For a numbered tier, resolve the requested number against the selected profile, then resolve that tier's model and effort.
4. Without an explicit or inherited selection, use the profile's `default_model_tier`.
5. A null field in the selected tier uses the compatible runtime default, otherwise no value.

`default_model` and `default_effort` are not a second desired-state source. Their retirement and data migration must agree with the tier-only persistence contract in the Settings design. Do not claim that a blank means Runtime default while allowing an old compatibility field to supply it instead.

The current implementation at the time of this design still distinguishes model-only and effort-only overrides and can read legacy scalar defaults. These are implementation gaps, not permission to retain conflicting behavior in the new selector.

### 9.3 Tier fallback

New UI-authored numbered selections omit `tierFallback` and use the existing backend clamp:

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

Until its consumers are migrated, retain the existing rejection when that requested tier is unavailable, including the actionable `requested_model_tier_unavailable` error. Do not silently turn that request into clamp behavior by hiding a control.

The new form does not generate strict policy and does not expose a fallback selector in Advanced mode. Loading a saved strict request shows a concise explanation of its retained requirement. Editing unrelated fields preserves that requirement. Explicitly selecting a numbered tier or Custom replaces the old model selection under the new rules and makes that consequence clear.

The implementation must identify legacy producers and saved consumers across presets, workflow drafts, schedules, and API/runtime paths. Reuse existing compatibility handling rather than creating a new registry or migration framework. Remove backend strict support only after those consumers no longer require it or a separately authorized migration preserves their intent. Removing strict globally is not a prerequisite for this UI change and is not authorized by hiding the selector.

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

Effort application remains one of applied, unsupported, metadata-only, emulated, or unknown according to the runtime's actual behavior.

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

Tiers do not own credentials, credential references, OAuth volumes, or secret materialization rules. Custom selection does not authorize another provider/account or change credential selection. Existing Provider Profile and Secrets responsibilities remain intact.

---

## 11. Provider Profile Manager Interaction

The Provider Profile Manager remains a profile-level slot and cooldown manager. Tiers are not capacity pools.

```text
1. Workflow step supplies runtime, profile selector, and model selection.
2. Provider Profile Manager selects and reserves a profile slot.
3. Backend resolves tier or Custom intent in that profile/runtime context.
4. Runtime adapter launches with the resolved model and effort.
```

Do not create separate capacities for Tier 1, Tier 2, and Tier 3, or treat tier choice as profile choice. The same profile can run different tiers concurrently within its existing capacity.

---

## 12. Persistence Model

### 12.1 Canonical profile fields

The canonical profile policy is a non-empty JSONB `model_tiers` array and a valid one-based `default_model_tier`. Enforce array shape, minimum length, and default bounds in existing database/application validation as appropriate for supported databases.

Do not re-add already implemented schema or migrations from this desired-state document. Inspect current code before implementation.

### 12.2 Migration from legacy defaults

Convert a genuinely pre-tier profile's legacy default model/effort into one canonical tier, or create a runtime-default tier when neither exists. Set the default to 1 only for that backfill. Never replace an existing authored tier array or reset its default during a repeated migration.

Remove obsolete profile default read/write fields and callers with the tier-only persistence work owned jointly with [Provider Profile Tier Settings §15.2](../UI/ProviderProfileModelEffortTierSettings.md#152-tier-only-persistence). Do not maintain indefinite denormalized mirrors or teach the new frontend to reconstruct missing tiers from them.

This profile-data conversion is distinct from preserving legacy strict workflow requests under §9.4. Historical execution values and active work must survive both transitions.

### 12.3 Historical data and authored presence

Runs retain their actual launch model, effort, and sources independently of future profile edits. Preserve authored tier/Custom/inherit intent separately from resolved diagnostics through existing execution payloads. Explicit Custom nulls must survive serialization, normalization, artifact storage, and draft reconstruction.

Reuse existing profile update timestamps or version metadata where present. Do not add a policy digest or version registry for this selector.

---

## 13. API Contract

### 13.1 Provider Profile create/update

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
  "default_model_tier": 1
}
```

### 13.2 Provider Profile response

The desired-state response contains canonical tier policy, not a second model/effort default mirror:

```yaml
profile_id: codex_openai_api
model_tiers: [tier_one, tier_two]
default_model_tier: 1
```

### 13.3 Preview endpoint

Reuse the existing profile-tier preview route and resolver rather than adding a competing endpoint or client resolver. Preview inputs and results retain authored selection, requested/effective tiers, resolved values, and sources where available. Custom preview must use the same explicit-null semantics as launch.

A tier preview can report:

```json
{
  "profileId": "codex_openai_api",
  "requestedTier": 3,
  "effectiveTier": 2,
  "model": "gpt-5.5",
  "effort": "xhigh",
  "fallbackReason": "requested_tier_above_configured_range"
}
```

This is illustrative resolution metadata, not a replacement route or exact existing response envelope. Preview never replaces launch-time resolution or becomes a new admission gate.

### 13.4 Runtime intent

Use §7.2's mutually exclusive authored shapes in workflow and step payloads. Preserve omitted versus explicitly null Custom fields through validation, scheduling, preset expansion, edit/rerun, and runtime parameter construction. New UI requests omit fallback settings. Existing saved strict policy remains readable and honored until its documented removal boundary.

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
legacy strict requests remain valid and retain their behavior while supported
```

Existing unknown model/effort strings remain visible and round-trippable where backend policy permits. Missing advisory catalog evidence alone must not erase input or create a new validation gate. Genuine provider/runtime constraints remain authoritative.

### 15.3 Runtime validation

Recheck selected profile existence, required launch readiness, valid tier policy, compatible model/effort application, and credential boundaries. Resolve an unavailable numbered tier with the default clamp or the retained explicit legacy strict policy. Custom does not re-enter tier resolution because a field is null.

---

## 16. Implementation and Transition

This section describes remaining outcomes, not a new multi-phase rollout framework. The documentation PR does not implement them.

1. Extend the existing selector state and payload owners to represent tier, full-pair Custom, and existing inheritance unambiguously. Start with failing executable behavior tests.
2. Replace workflow and step numeric/fallback/hard-override controls with one shared Tier/Model/Effort interaction. Preserve keyboard focus, mobile layout, explicit choices, and late-response safety.
3. Complete the runtime-intent path across preset expansion, workflow submit, edit/rerun, schedules, inheritance, preview, and launch. Reuse the canonical backend resolver.
4. Make runtime-default labels truthful by removing conflicting legacy-default resolution on the new path and coordinating with existing tier-only persistence work. Do not rebuild completed Settings work.
5. Preserve saved strict requests with the visible explanation and replacement behavior in §9.4. Identify their consumers and removal condition without making global strict retirement a prerequisite.
6. Remove obsolete UI state, payload generation, tests, and guidance together. Keep coverage for surviving behavior and migration. Do not move removed controls to Advanced mode.

Do not rewrite retained Temporal histories or active attempts. Test replay-sensitive changes or use the existing controlled compatibility boundary. Broader verification belongs in GitHub Actions under `AGENTS.md`; no mandatory human visual signoff or documentation unit tests are introduced.

---

## 17. Acceptance Tests

### Provider Profile validation and migration

- Reject zero tiers and invalid default indexes.
- Accept null model/effort runtime-default tiers.
- Reject credential-bearing tier data.
- Backfill only genuinely pre-tier profiles and preserve already-authored policy on repeat execution.
- Preserve actual historical run values while removing scalar default mirrors.

### Resolution

- Omitted selection uses workflow inheritance and then the profile default.
- Requested Tier 2 resolves to `model_tiers[1]`.
- Requested Tier 3 on a two-tier profile clamps to Tier 2 with recorded requested/effective values.
- Explicit legacy strict Tier 3 still rejects on a two-tier profile.
- Custom uses the complete authored pair, including effort-only edits with the original model preserved.
- One-null and both-null Custom pairs use runtime defaults without profile-tier, scalar-default, or parent-field leakage.
- Null tier fields use runtime defaults, not obsolete profile defaults.
- A step-level tier cannot be defeated by inherited Custom model fields, and step-level Custom nulls cannot be refilled by an inherited tier.
- Explicit non-model runtime parameters and provider/credential boundaries are preserved.

### Frontend/backend contract

- A new unconfigured workflow initializes from a non-1 default tier when configured.
- The dropdown contains only configured numbers plus Custom.
- Numbered selection fills both fields but submits a tier reference, not overrides.
- Editing either field enters Custom and preserves the companion value.
- Direct Custom selection preserves both values, and coincidental matching never exits Custom.
- Returning to a numbered tier removes Custom overrides.
- Profile changes and delayed responses never overwrite user edits.
- Missing profile data does not invent tiers or erase a draft.
- Saved requested tiers and explicitly strict policy survive unrelated edits with truthful diagnostics.
- No fallback selector exists in normal or Advanced workflow/step controls.

### Persistence and launch

- Save/reload, preset expansion, edit/rerun, recurring submission, and actual launch parameter construction preserve all selection shapes and explicit nulls.
- Tier schedules use current tier policy, while Custom strings stay explicit and Custom nulls use runtime defaults.
- Viewing inherited steps does not create overrides or pin preview values.
- Preview mismatch records a diagnostic rather than adding a launch gate.
- Runtime adapters apply resolved values and report unsupported effort honestly.
- Existing execution history and active work are not rewritten.

Use focused unit/interaction tests plus credential-free integration and browser journeys through production components. Broader suites run in CI. Do not test documentation wording or require manual approval to complete implementation.

---

## 18. Settled Decisions and Scope Boundaries

- Tier fallback is removed from all workflow-authoring UI, not moved to Advanced.
- New tier requests use the existing backend clamp without authoring a policy setting.
- Existing explicit strict requests are retained until their consumers can be migrated without silently weakening intent.
- Tier-based schedules follow current profile policy. No snapshot feature is added.
- Profile scalar default mirrors are not a permanent alternative to tier policy.
- Settings owns ordered tier editing and structural-change warnings. This workflow-selector change does not redesign that editor.
- Cost annotations remain metadata. No quota, billing, provider failover, or capacity subsystem is added.

---

## 19. Decision Summary

Provider Profiles own model/effort tiers. Workflow authoring offers configured tier numbers and Custom in one Tier dropdown. Model and Effort show the selected tier's values and remain editable. A user edit enters Custom while preserving the pair, and direct Custom selection leaves both fields unchanged.

Numbered selections persist tier references, Custom persists explicit nullable values, and unconfigured steps retain inheritance. The backend remains authoritative and uses the existing default clamp for new tier requests. Explicit legacy strict requirements remain observable and honored until safely retired. The implementation adds neither another resolver nor another gate, preserves active work and historical records, and removes obsolete UI and conflicting guidance together.
