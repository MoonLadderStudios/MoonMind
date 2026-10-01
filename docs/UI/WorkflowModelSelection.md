# Workflow Model Selection

Status: **Desired-state UI and implementation contract. Not implemented by the documentation PR.**

Owners: MoonMind Engineering

Last updated: 2026-09-30

Canonical for: model selection while creating or editing workflows and their steps, including preset, rerun, and schedule round trips.

**Related designs:** [Provider Profile Model and Effort Tiers](../Security/ProviderProfileModelEffortTiers.md), [Provider Profile Tier Settings](./ProviderProfileModelEffortTierSettings.md), [Dashboard Design System](./DashboardDesignSystem.md)

## 1. Decision

Use three plainly labeled fields:

```text
Tier       [2       v]
Model      [provider-model-a]
Effort     [high]
```

The Tier dropdown contains the selected Provider Profile's configured tier numbers in ascending order, followed by `Custom`. For a three-tier profile, the selectable values are `1`, `2`, `3`, and `Custom`.

Remove `Tier fallback`, the free-form numeric `Model tier intent` input, and the separate `Hard override model` / `Hard override effort` presentation. Do not move fallback into Advanced mode, introduce another Default dropdown option, or require a separate override switch. Model and Effort remain editable where the selected runtime supports them.

Tier selection chooses reusable profile policy. Custom selects explicit model/effort values. Neither action edits the Provider Profile. Profile-tier creation, removal, ordering, and default configuration remain in the existing Settings editor and are not redesigned here.

## 2. Current Implementation Boundary

At the inspected `main` revision `e2efc49a0ce8fc6a137a8bcf88698975289e1a24`, `frontend/src/entrypoints/workflow-start.tsx` contains separate workflow and advanced-step tier/fallback/override controls. The canonical backend model resolver is `moonmind/workflows/executions/model_resolver.py`, and authored tier validation is in `moonmind/runtime_intent.py`.

The current backend treats concrete-model overrides differently from effort-only overrides and can fall through legacy profile scalar defaults. Replacing labels alone is therefore insufficient. The new interaction needs an end-to-end authored-selection contract, including Custom with blank fields.

Recheck current code and existing implementation work before changing these owners. This document defines required behavior, not proof that it is already implemented.

## 3. Interaction Contract

| Event | Tier state | Model and Effort behavior | Authored intent |
| --- | --- | --- | --- |
| Open a new otherwise unconfigured workflow after its profile loads | Profile's actual default tier | Populate both from that tier | Tier reference, not concrete preview overrides |
| Load a preset, saved draft, rerun, or schedule | Restore its authored selection | Display corresponding values | Preserve saved selection and existing inheritance |
| Select a configured numbered tier | Selected number | Replace both fields from that tier | Remove prior Custom overrides |
| User edits Model | Custom | Keep edited Model and unchanged Effort | Author the complete pair |
| User edits Effort | Custom | Keep edited Effort and unchanged Model | Author the complete pair |
| User selects Custom directly | Custom | Leave both fields exactly as displayed | Detach the pair from tier policy |
| User enters values matching a configured tier | Remain Custom | Leave values unchanged | Do not infer a return to tier-following behavior |
| User selects a numbered tier from Custom | Selected number | Replace both fields, including null/default semantics | Remove Custom overrides and use the tier |
| Programmatic preview or profile data arrives | Preserve authored state | Refresh only the applicable unedited tier preview | Never simulate a user edit or overwrite Custom |

Opening the dropdown, focusing a field, or selecting text is not an edit. A real user change, including clearing a field, enters Custom. Tier population must not trigger Custom through a generic change effect. Keep keyboard focus and text selection stable when the label changes to Custom.

Example:

```text
Before: Tier 2, Model A, high
User changes only Model to Model B
After:  Custom, Model B, high
```

Do not replace `high` with the profile default effort. The same preservation applies to an effort-only edit.

## 4. What Is Displayed Versus What Is Saved

The backend tier design owns the wire semantics. The frontend must use its mutually exclusive authored shapes rather than treating every visible value as an override.

### Numbered tier

```yaml
runtime:
  modelTier: 2
```

The populated Model and Effort fields are previews. Do not add their concrete values to the request as overrides. Future launches continue to follow Tier 2's policy.

### Custom

```yaml
runtime:
  model: provider-model-b
  effort: high
```

Both fields are authored. Remove the active `modelTier`. The string `custom` belongs to frontend selection state only and must never be submitted as the integer tier field.

Selecting Custom directly copies the displayed pair into explicit Custom intent without changing either field. A concrete displayed value becomes explicit. A field displayed as Runtime default remains an explicit null, not a guessed or frozen concrete value from helper text.

### Blank Custom fields

Use an empty input with an explicit `Runtime default` placeholder or equivalent accessible explanation. Normalize a cleared value to null when saving:

```yaml
runtime:
  model: null
  effort: high
```

Both-null Custom is valid intent too:

```yaml
runtime:
  model: null
  effort: null
```

It is not the same as omitting model selection. Do not strip these fields, refill them from workflow/profile tiers, or let a legacy scalar default supply them. A missing runtime default remains unknown/absent and is subject to existing runtime requirements, not an invented model.

### Inheritance

An unconfigured step keeps existing workflow inheritance. Show its effective values with concise `Inherited from workflow` supporting text. Merely opening or saving the form must not persist that preview as a step override.

An explicit tier or Custom edit makes a step-level selection. Reuse the existing inheritance/reset affordance, or a small `Use workflow settings` action outside the Tier dropdown when needed to restore inheritance. Do not add an Inherit value to the configured-numbers-plus-Custom dropdown or invent another runtime mode.

Inherited values must not remain as hidden competing fields. A child tier supersedes inherited Custom model/effort, and child Custom nulls supersede inherited tier values. Preserve separately authored non-model parameters.

## 5. Defaults, Profile Changes, and Unavailable Tiers

### Default initialization

Use the selected profile's `default_model_tier`, not a hard-coded 1. Automatic initialization applies only to an otherwise unconfigured workflow. Saved/preset values and user input take precedence over an asynchronous default response.

Existing omitted/default intent must not be pinned merely by hydration. Distinguish initialization, inherited/saved state, and explicit user changes with minimal local provenance in the existing form owner. Do not create a second persistent preferences or model-policy system.

### Profile or harness change

Rebuild numeric choices from the newly selected profile. For a numbered selection, keep the authored tier number and obtain the new profile's resolution. An unconfigured/default-derived selection follows the new profile's default where it has not become an explicit saved/user choice.

In Custom, preserve both inputs. Validate them against the new provider/runtime, show a known incompatibility beside the field, and never silently substitute a model or effort. Refresh effort suggestions when appropriate, but do not erase the current effort. Custom does not authorize cross-provider credential use.

### A saved request exceeds the current tier count

Do not add an unavailable number to the selectable options. For ordinary clamp behavior, display the effective configured tier and a concise message identifying the original request:

```text
Tier 3 is not configured for this profile. Using Tier 2.
```

Retain requested Tier 3 in saved intent until the user explicitly replaces the model selection. A later profile with three tiers can then honor it. Unrelated edits must not silently convert requested Tier 3 into permanently authored Tier 2.

### Legacy strict requests

The new UI never offers a fallback-policy selector or authors new strict settings. A loaded request that already contains `tierFallback: strict` retains that requirement and displays a concise explanation.

When its tier is unavailable, do not claim a lower tier is effective. Use an unavailable-selection placeholder with a diagnostic and offer only configured numbers plus Custom as replacement choices. A placeholder is not an extra selectable tier. Retain any known draft values and distinguish them from a successful resolution.

Unrelated edits preserve strict intent. Explicit selection of a numbered tier or Custom replaces the old model selection under the new rules. Explain this next to the retained requirement, for example:

```text
This saved request requires Tier 3. Choosing a tier or Custom replaces that requirement.
```

Do not silently discard strict metadata on load/save. Backend strict retirement is a separate compatibility cleanup after identified consumers no longer require it, not a prerequisite for removing the UI selector.

## 6. Loading and Failure Behavior

Tier availability means configured profile tier entries, not a newly invented launch-qualification test or a hard-coded catalog of three numbers. A valid profile may define one tier, many tiers, or a tier using runtime defaults.

While profile tier data loads, preserve the draft. Do not render guessed numbers or reset the form. Distinguish loading, failed fetch, and a malformed empty tier policy. Limit disabled controls to those that genuinely need missing information.

Reuse existing profile data, capabilities, preview queries, and backend validation. Advisory model-catalog or preview failure must not erase input, clear Custom, or create a new whole-form admission gate. Actual profile/provider/runtime constraints remain authoritative.

Ignore delayed responses for an old profile or selection. A tier preview that started before the user typed must not later restore tier mode or overwrite Custom values. Refresh failure leaves current values visible with a scoped warning.

A null tier field must be displayed honestly as Runtime default with any known backend-resolved value in supporting text. Do not call a value Runtime default when the backend reports a legacy profile-default source. Close that resolver gap with the implementation rather than disguising it with UI copy.

## 7. Shared Implementation Boundaries

Use one shared workflow/step selector component or a comparably small shared behavior owner, together with pure state/payload helpers. Extend or extract from the existing form rather than adding another form framework or backend service.

Keep these responsibilities distinct:

- The selector owns user transitions and editable values.
- Existing profile/capability queries provide available tiers and suggestions.
- Runtime-intent serialization owns tier versus full-pair Custom versus omission.
- The existing backend resolver owns final resolution.
- Runtime adapters own actual model/effort application.

Reuse the interaction in create/edit/rerun and any schedule surface that directly authors the same selection. Verify preset expansion, reconstruction helpers, recurring submission, and remediation draft consumers even when they do not render their own selector. Do not redesign unrelated schedule or remediation controls.

Remove the obsolete numeric tier/fallback controls, separate hard-override state and payload paths where superseded, and tests that freeze the old presentation. Retain necessary legacy readers and active-work compatibility with identified consumers and a removal condition. Do not simply hide stale controls while leaving their values to influence new requests.

## 8. Layout and Accessibility

Use labels `Tier`, `Model`, and `Effort`. Keep the tier number easy to scan. A default badge or concise helper may identify the profile default without another selectable Default mode.

Use the existing select/combobox/input primitives. Suggestions must not turn editable values into a hard-coded model catalog. Preserve supported manual input and existing unknown values according to backend policy. Unsupported effort is shown honestly and must not be described as applied.

On wide screens, place the three fields in one compact row when they fit. On narrow screens, stack them with readable full-width fields. Long model IDs and helper messages must not cause page overflow or squeeze Tier into an unusable control.

Provide workflow- or step-specific accessible names. Tier selection, entry, clearing, and restoring workflow inheritance must work with a keyboard. An automatic switch to Custom can use a concise polite announcement, without moving focus or remounting the edited input. Diagnostics must not rely on color alone.

Do not duplicate a verbose tier-preview panel when Model and Effort already display the same information. Keep only useful source/default, fallback, incompatibility, or unavailable-data messages.

## 9. Automated Acceptance

The implementation issue must supply executable proof, not manual visual signoff or tests of this document's wording.

| Boundary | Required scenarios |
| --- | --- |
| Initialization | Default Tier 2 in a three-tier profile; one-tier and more-than-three-tier profiles; saved preset/draft intent not overwritten |
| User transitions | Every row in §3, both edit directions, direct Custom, clearing values, matching a tier while staying Custom, Custom back to numbered tier |
| Serialization | Tier sends no concrete overrides; Custom sends the full nullable pair without an active tier; omitted step selection remains inherited |
| Resolution | Custom one-null/both-null cases do not use profile tiers, legacy scalars, or inherited values; provider/effort constraints stay authoritative |
| Async/profile changes | Delayed preview/default response after user input; rapid profile switching; unavailable profile data; Custom preserved on provider change |
| Compatibility | Out-of-range saved tier remains authored with honest clamp notice; legacy strict survives unrelated edits and does not silently clamp |
| Round trips | Save/reload, preset expansion, edit/rerun, recurring workflow submission, remediation draft import where applicable, preview and actual launch parameter construction |
| Scheduling/history | A future tier launch uses updated profile mapping; Custom strings remain explicit; Custom nulls use runtime defaults; prior attempt records remain unchanged |
| Presentation | Shared workflow/step controls, no fallback selector in normal or Advanced mode, keyboard/focus continuity, mobile layout with long model IDs |

Extend existing tests such as `frontend/src/entrypoints/workflow-start.test.tsx`, `tests/unit/api/test_presets_service.py`, `tests/integration/api/test_execution_contract_normalization.py`, and runtime-launch tests where their boundaries apply. Add small focused selector tests rather than requiring the entire large page suite for each state transition. Use the existing real-browser infrastructure with production components and styles for focus/layout behavior.

Follow `AGENTS.md`: observe the expected failing executable test, make the smallest correct implementation, and use targeted local checks with broader GitHub Actions verification. No mandatory human review, live paid-provider run, new acceptance registry, or additional scheduler is needed for this selector change. Retain historical/active-work compatibility and test any replay-sensitive change at its existing boundary.

## 10. Delivery Boundary

The documentation PR establishes this desired state and links the backend contract. A separate implementation issue owns the executable change across its actual producers and consumers. Merging documentation does not prove the UI or resolver behavior is implemented.

Profile-tier editing stays in its existing Settings design. Workflow selection has one shared interaction, one canonical authored-intent contract, and one backend resolver. The change removes an operator decision rather than relocating it or adding another policy layer.
