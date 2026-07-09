# Provider Profile Model and Effort Tiers

**Related design documents:** [ProviderProfiles.md](./ProviderProfiles.md), [SecretsSystem.md](./SecretsSystem.md), [ManagedAndExternalAgentExecutionModel.md](../Temporal/ManagedAndExternalAgentExecutionModel.md), [SettingsPage.md](../UI/SettingsPage.md)

Status: **Desired-State Design**
Owners: MoonMind Engineering
Last Updated: 2026-07-09

> [!NOTE]
> This document extends the Provider Profiles model with declarative model/effort tiers.
>
> A tier is a profile-local policy entry that maps a small integer such as `1`, `2`, or `3` to a runtime-specific model and optional effort level. Presets and workflow steps should request tiers by number. The backend remains authoritative for resolving the final model and effort at submit or launch time.

---

## 1. Summary

MoonMind presets should be able to express model intent declaratively without hardcoding concrete model names in every step.

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

A preset remains portable because it asks for `modelTier: 2`, not `gpt-5.5` with `xhigh`. If the profile owner later changes Tier 2, future launches use the updated profile policy without editing every preset.

---

## 2. Design Goals

The tier system must support:

1. **Profile-local tier definitions**
   - Tier numbers are interpreted relative to the selected Provider Profile.
   - Tier 2 on one profile does not have to mean the same model or cost as Tier 2 on another profile.

2. **Preset portability**
   - Presets should request semantic tiers rather than concrete provider model names.

3. **Backend authority**
   - The frontend may preview tier resolution, but the backend must resolve the final effective model and effort.

4. **Deterministic fallback**
   - If Tier 3 is requested on a profile with only two tiers, MoonMind falls back to Tier 2 by default.

5. **Minimum one tier**
   - Every Provider Profile must have at least one model tier.
   - A tier may intentionally leave `model` or `effort` null to use runtime defaults.

6. **Runtime-specific effort handling**
   - Effort values are runtime/provider-specific strings.
   - Runtime strategies decide whether a resolved effort can be applied as a flag, config entry, environment variable, or metadata-only annotation.

7. **Auditability**
   - Runs must record requested tier, effective tier, resolved model, resolved effort, source, and fallback reason.

---

## 3. Non-Goals

This design does **not** attempt to:

- create a global cross-provider quality scale,
- define universal effort enum values for every provider,
- make tiers independent capacity pools,
- replace Provider Profile slot leasing,
- remove direct model/effort overrides for expert workflows,
- make the frontend authoritative for model policy,
- freeze a preset to one concrete model forever unless the user explicitly requests a hard override or snapshot mode.

---

## 4. Key Concepts

### 4.1 Provider Profile tier policy

A Provider Profile owns a `model_tiers` array and a `default_model_tier` integer.

The array order defines tier numbers:

```text
model_tiers[0] -> Tier 1
model_tiers[1] -> Tier 2
model_tiers[2] -> Tier 3
```

The tier number is intentionally one-based because it is user-facing.

### 4.2 Requested tier

A requested tier is the tier number supplied by a preset step, manual workflow step, or API caller.

```yaml
runtime:
  modelTier: 2
```

### 4.3 Effective tier

The effective tier is the tier actually used after fallback/clamping.

```text
requested tier: 3
configured tiers: 2
effective tier: 2
fallback reason: requested_tier_above_configured_range
```

### 4.4 Concrete model and effort

The concrete model and effort are resolved from the effective tier.

```yaml
effective_model: gpt-5.5
effective_effort: xhigh
```

### 4.5 Hard override

A hard override bypasses tier policy.

```yaml
runtime:
  model: gpt-5.5
  effort: xhigh
  source: manual_override
```

Hard overrides are allowed for advanced users and debugging, but they are no longer the recommended preset format.

---

## 5. Provider Profile Contract

### 5.1 Canonical fields

Add these fields to the Provider Profile contract:

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

Ordered list of model/effort combinations.

Rules:

```text
len(model_tiers) >= 1
model_tiers must be a JSON array
model_tiers entries must be JSON objects
```

The array order is the tier contract. Reordering tiers is a policy change because it changes what future `modelTier` requests resolve to.

#### `default_model_tier`

The one-based tier number used when no step-level tier is requested.

Rules:

```text
default_model_tier >= 1
default_model_tier <= len(model_tiers)
```

#### `label`

Human-readable tier label for Settings and workflow UI.

Examples:

```text
Plan and verify
Implementation
Docs audit
Low cost
High reasoning
```

The label is not used for routing.

#### `model`

Runtime/provider-specific model string. The value is opaque to the Provider Profile system and interpreted by the runtime strategy.

Examples:

```text
gpt-5.5
gpt-5.3-codex-spark
claude-opus-4-8
MiniMax-M2.7
```

A null model means “use runtime default model after tier selection.”

#### `effort`

Runtime/provider-specific effort string.

Examples:

```text
low
medium
high
xhigh
```

A null effort means “use runtime default effort if one exists, otherwise omit effort.”

#### `parameters`

Optional non-secret runtime parameters associated with the tier.

Example:

```yaml
parameters:
  temperature: 0
  output_format: strict_json
```

This field must not contain raw credentials.

#### `annotations`

Optional metadata for UI, billing labels, documentation, or rollout flags.

Example:

```yaml
annotations:
  costClass: premium
  recommendedFor:
    - implementation
    - complex_refactor
```

Annotations are not used for required launch behavior unless a later design explicitly promotes a key into the contract.

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

A setup stub or minimal custom profile can still satisfy the minimum-one-tier rule without hardcoding a model.

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

Presets should request tiers through step runtime metadata.

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

The preset is not required to name a Provider Profile directly. A workflow may select the profile through normal Provider Profile selection:

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

---

## 8. Frontend and Backend Responsibilities

### 8.1 Principle

The frontend may **preview** tier resolution. The backend must **resolve** tier policy authoritatively.

### 8.2 Frontend responsibilities

The frontend should:

1. Fetch Provider Profile tier definitions for display.
2. Show the user the current mapping from tier to model/effort.
3. Warn when a requested tier will fallback because the selected profile has fewer tiers.
4. Allow an explicit hard model/effort override for advanced use cases.
5. Submit requested tier intent instead of replacing it with concrete model/effort values.

Example frontend preview:

```text
Step 1 · Tier 1 · gpt-5.5 · medium
Step 2 · Tier 2 · gpt-5.5 · xhigh
Step 3 · Tier 1 · gpt-5.5 · medium
Step 4 · Tier 3 · gpt-5.3-codex-spark · xhigh
```

### 8.3 Backend responsibilities

The backend should:

1. Validate Provider Profile tier definitions.
2. Validate requested `modelTier` values when presets or workflows are submitted.
3. Resolve the selected Provider Profile.
4. Resolve the final effective tier after profile selection.
5. Resolve model and effort from the effective tier.
6. Apply fallback policy.
7. Record the full resolution in run diagnostics.
8. Re-check launch readiness at the launch boundary.

### 8.4 Why the frontend is not authoritative

The frontend must not collapse tier intent into concrete model/effort as the primary contract because:

- Provider Profiles are backend-owned policy records.
- Profile data can change between frontend preview and backend launch.
- Workflows may be submitted by API clients, recurring schedules, batch systems, or backend remediation flows without a frontend.
- Tier fallback and launch readiness need backend-owned state.
- A preset loses portability if it is saved as concrete model strings instead of tier intent.

### 8.5 Optional advisory preview snapshot

The frontend may submit an advisory preview snapshot for user-experience auditing and stale-data detection.

```yaml
runtime:
  modelTier: 3
  tierPreview:
    profileId: codex_openai_api
    profileVersion: 42
    model: gpt-5.3-codex-spark
    effort: xhigh
```

The backend may compare the advisory preview to its own resolution. If they differ, the default behavior is to re-resolve using backend state and record a preview mismatch.

A future strict mode may reject stale previews:

```text
409 stale_profile_tiers
```

---

## 9. Resolution Semantics

### 9.1 Inputs

The resolver receives:

```yaml
runtime_id: str
profile: ManagedAgentProviderProfile
requested_model_tier: int | null
requested_model: str | null
requested_effort: str | null
tier_fallback: clamp | strict
workflow_settings: object | null
env: mapping | null
```

### 9.2 Resolution order

Model and effort resolution must use this order:

1. Explicit task or step override.
2. Requested Provider Profile tier.
3. Provider Profile default tier.
4. Legacy `default_model` / `default_effort` compatibility fields.
5. Runtime default model / effort.
6. No value.

### 9.3 Tier fallback

Default fallback policy is `clamp`:

```python
def effective_tier(requested_tier: int | None, *, default_tier: int, tier_count: int) -> int:
    raw = requested_tier or default_tier or 1
    return max(1, min(raw, tier_count))
```

Examples:

| Requested tier | Configured tier count | Effective tier | Reason |
| --- | ---: | ---: | --- |
| null | 3 | `default_model_tier` | `profile_default_tier` |
| 1 | 3 | 1 | null |
| 2 | 3 | 2 | null |
| 3 | 2 | 2 | `requested_tier_above_configured_range` |
| 0 | 3 | 1 | `requested_tier_below_configured_range` |

API validation should reject tier values lower than 1. Runtime clamping is still required as a defensive safety net.

### 9.4 Strict fallback mode

Some workflows may require a specific tier to exist.

```yaml
runtime:
  modelTier: 3
  tierFallback: strict
```

If `tierFallback: strict` and the requested tier is outside the configured range, the backend must reject the workflow step before launch.

```text
422 requested_model_tier_unavailable
```

### 9.5 Output

The resolver returns:

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

Recommended source values:

```text
task_override
requested_tier
profile_default_tier
provider_profile_default
runtime_default
none
```

Recommended effort application statuses:

```text
applied
not_supported
metadata_only
emulated
unknown
```

---

## 10. Runtime Strategy Integration

### 10.1 Model application

Runtime strategies consume the resolved model in their existing command/environment/config shaping.

Examples:

- Codex CLI may pass the model through a CLI flag such as `-m` when supported by the current strategy.
- Claude Code may pass the model through a runtime-specific flag, environment variable, or config entry.
- Provider-compatible runtimes may map a profile tier model to a generated config profile.

### 10.2 Effort application

Effort is intentionally runtime-specific.

A runtime strategy must declare how effort is applied:

```text
native flag
config entry
environment variable
metadata only
unsupported
```

If a runtime does not support effort, MoonMind should not pretend the value was applied. The run diagnostics should record:

```yaml
resolvedEffort: xhigh
effortApplicationStatus: not_supported
```

### 10.3 Tier parameters

Tier `parameters` may be merged into launch parameters after tier resolution, but before runtime strategy command construction.

Precedence:

```text
explicit step parameters override tier parameters
```

This prevents a tier from silently overriding a user’s explicit step-level setting.

### 10.4 Tier policy does not own credentials

Tiers must not contain credentials, credential references, OAuth volume refs, or secret materialization rules. Credentials remain Provider Profile and Secrets System responsibilities.

---

## 11. Provider Profile Manager Interaction

The Provider Profile Manager remains a profile-level slot and cooldown manager.

Tiers are **not** capacity pools.

Correct flow:

```text
1. Workflow step requests runtime + profile selector + modelTier.
2. Provider Profile Manager selects and reserves a Provider Profile slot.
3. Backend resolves modelTier against the selected profile.
4. Runtime strategy launches with the resolved model/effort.
```

Incorrect flow:

```text
1. Create separate slot capacity for Tier 1, Tier 2, Tier 3.
2. Treat tier selection as profile selection.
```

The same profile may run Tier 1 and Tier 3 work concurrently if the profile has available `max_parallel_runs` capacity.

---

## 12. Persistence Model

### 12.1 Table fields

Add fields to `managed_agent_provider_profiles`:

```sql
ALTER TABLE managed_agent_provider_profiles
  ADD COLUMN model_tiers JSONB NOT NULL DEFAULT '[{"label":"Runtime default","model":null,"effort":null,"parameters":{},"annotations":{}}]'::jsonb,
  ADD COLUMN default_model_tier INTEGER NOT NULL DEFAULT 1;
```

Recommended PostgreSQL checks when practical:

```sql
ALTER TABLE managed_agent_provider_profiles
  ADD CONSTRAINT ck_provider_profiles_model_tiers_array
  CHECK (jsonb_typeof(model_tiers) = 'array' AND jsonb_array_length(model_tiers) >= 1);

ALTER TABLE managed_agent_provider_profiles
  ADD CONSTRAINT ck_provider_profiles_default_model_tier_positive
  CHECK (default_model_tier >= 1);
```

The upper-bound check against `jsonb_array_length(model_tiers)` may be enforced in application validation to preserve cross-database compatibility.

### 12.2 Migration from legacy defaults

Existing profiles should be migrated as follows:

```python
if profile.default_model or profile.default_effort:
    profile.model_tiers = [
        {
            "label": "Default",
            "model": profile.default_model,
            "effort": profile.default_effort,
            "parameters": {},
            "annotations": {"migratedFrom": "default_model_default_effort"},
        }
    ]
else:
    profile.model_tiers = [
        {
            "label": "Runtime default",
            "model": None,
            "effort": None,
            "parameters": {},
            "annotations": {"migratedFrom": "runtime_default"},
        }
    ]

profile.default_model_tier = 1
```

Keep `default_model` and `default_effort` as compatibility mirrors until all callers move to tier-aware resolution.

### 12.3 Versioning

A future implementation may add a profile `value_version` or policy digest. Until then, runs should persist the resolved model/effort output so historical executions remain auditable even if the profile changes later.

---

## 13. API Contract

### 13.1 Provider Profile create/update

Provider Profile create and update endpoints should accept:

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

Provider Profile responses should include both the declarative tier policy and compatibility fields during migration:

```json
{
  "profile_id": "codex_openai_api",
  "default_model": "gpt-5.5",
  "default_effort": "medium",
  "model_tiers": [...],
  "default_model_tier": 1
}
```

### 13.3 Preview endpoint

A dedicated preview endpoint is optional but recommended for UI clarity.

```http
POST /provider-profiles/{profile_id}/model-tiers:preview
```

Request:

```json
{
  "steps": [
    {"id": "plan", "modelTier": 1},
    {"id": "implement", "modelTier": 2},
    {"id": "docs", "modelTier": 3}
  ]
}
```

Response:

```json
{
  "profileId": "codex_openai_api",
  "items": [
    {
      "stepId": "plan",
      "requestedTier": 1,
      "effectiveTier": 1,
      "model": "gpt-5.5",
      "effort": "medium",
      "fallbackReason": null
    },
    {
      "stepId": "docs",
      "requestedTier": 3,
      "effectiveTier": 2,
      "model": "gpt-5.5",
      "effort": "xhigh",
      "fallbackReason": "requested_tier_above_configured_range"
    }
  ]
}
```

This endpoint is a preview surface. It does not replace backend launch-time resolution.

---

## 14. Observability and Audit

Every launched step should record tier resolution metadata in run diagnostics or launch metadata.

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

The UI should surface fallback in a concise form:

```text
Requested Tier 3, used Tier 2 because the selected profile only defines two tiers.
```

Historical workflow details should show the resolved concrete model/effort used for that launch, even if the Provider Profile is later edited.

---

## 15. Validation Rules

### 15.1 Profile validation

Provider Profile writes must enforce:

```text
model_tiers is present
model_tiers.length >= 1
default_model_tier >= 1
default_model_tier <= model_tiers.length
model_tiers[*].parameters contains no raw credential-like keys
model_tiers[*].annotations contains no raw credential-like keys
```

### 15.2 Preset and workflow validation

Preset and workflow writes should enforce:

```text
modelTier is an integer when present
modelTier >= 1
tierFallback is clamp or strict when present
hard model/effort overrides are explicit and auditable
```

### 15.3 Runtime validation

Runtime launch must defensively re-check:

```text
selected profile exists
selected profile is launch ready
selected profile has at least one tier
requested tier can be resolved or rejected according to fallback policy
resolved tier does not introduce raw credentials
```

---

## 16. Rollout Plan

### Phase 1: Provider Profile schema

- Add `model_tiers` and `default_model_tier` to the database model.
- Add migration/backfill from `default_model` and `default_effort`.
- Add API read/write validation.

### Phase 2: Backend resolver

- Add a canonical `resolve_effective_model_effort` helper.
- Route model and effort resolution through the helper.
- Preserve legacy behavior when no tier is supplied.

### Phase 3: Preset expansion

- Allow preset step runtime metadata to include `modelTier` and `tierFallback`.
- Preserve `modelTier` intent through expansion.
- Do not replace tiers with model/effort in the frontend submission path.

### Phase 4: UI preview

- Show tier tables in Provider Profile settings.
- Preview preset step tier resolution before submit.
- Warn about fallback and stale previews.

### Phase 5: Runtime strategy support

- Teach each runtime strategy how to apply or decline resolved effort.
- Record `effortApplicationStatus` for every launch.

### Phase 6: Compatibility cleanup

- Keep `default_model` and `default_effort` as compatibility fields until all callers are tier-aware.
- Later, either remove them or define them as denormalized mirrors of `model_tiers[default_model_tier - 1]`.

---

## 17. Acceptance Tests

### Provider Profile validation

- Reject a profile with zero tiers.
- Reject `default_model_tier` greater than the number of configured tiers.
- Accept a runtime-default-only tier with null model and effort.
- Reject tier parameters that contain raw credential-like keys.

### Migration

- A profile with `default_model` and `default_effort` gets one migrated tier.
- A profile without legacy defaults gets one runtime-default tier.
- `default_model_tier` is set to 1.

### Resolution

- No requested tier uses `default_model_tier`.
- Requested Tier 2 resolves to `model_tiers[1]`.
- Requested Tier 3 with only two tiers clamps to Tier 2 by default.
- `tierFallback: strict` rejects Tier 3 when only two tiers exist.
- Explicit step model/effort overrides tier policy.
- Null tier model falls back to runtime default model.
- Null tier effort falls back to runtime default effort or no effort.

### Frontend/backend contract

- Frontend preview displays the backend preview response.
- Frontend submits `modelTier`, not only concrete model/effort.
- Backend re-resolves and records a preview mismatch when the profile changed after preview.

### Runtime launch

- Codex CLI receives the resolved model through its model application path.
- Unsupported effort is recorded as `not_supported`, not silently marked applied.
- Run diagnostics include requested tier, effective tier, resolved model, resolved effort, source, and fallback reason.

---

## 18. Open Questions

1. Should tier ordering be restricted by Settings UI to prevent accidental semantic reordering?
2. Should MoonMind expose `strict` fallback in the normal UI, or reserve it for API/power-user workflows?
3. Should recurring workflows default to current profile policy at launch, or support a snapshot mode that freezes tier resolution at schedule creation?
4. Should profile `default_model` and `default_effort` remain denormalized compatibility fields indefinitely?
5. Should billing/cost policy eventually define optional tier annotations such as `costClass`, or should those remain UI-only metadata?

---

## 19. Decision Summary

- Provider Profiles own model/effort tier definitions.
- Presets and workflow steps request tiers by number.
- The frontend previews tier resolution but does not authoritatively compile tiers into concrete model/effort values.
- The backend resolves the final model and effort after profile selection.
- Requested tiers fallback by clamping to the nearest configured tier unless strict mode is requested.
- Provider Profile Manager continues to manage profile-level slots, not tier-level capacity.
- Every launched step records the requested tier, effective tier, model, effort, source, and fallback reason for observability.
