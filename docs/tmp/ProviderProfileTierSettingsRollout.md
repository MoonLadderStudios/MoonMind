# Provider Profile Tier Settings Rollout

This temporary working note tracks delivery sequencing for the canonical UI contract in `docs/UI/ProviderProfileModelEffortTierSettings.md`.

The canonical document owns durable desired state. This note owns ordered, disposable implementation work and may be deleted or archived once the target is operational.

Related implementation issue: [#3348](https://github.com/MoonLadderStudios/MoonMind/issues/3348).

## Phase 1 — Editable canonical tier stack

- Add tier draft state to `ProviderProfilesManager`.
- Load canonical `model_tiers` and `default_model_tier` from the profile response.
- Add the numbered tier section.
- Save `model_tiers` and `default_model_tier`.
- Cover add, remove, default, and payload behavior with tests.

## Phase 2 — Remove the superseded default fields

This phase is the cohesive removal required by the tier-only persistence contract, and it is a prerequisite for labelling a null tier value `Runtime default`:

- Remove `default_model` and `default_effort` from the Provider Profile read and write contracts, including the API schemas, the ORM columns, and the migration that drops them.
- Remove the compatibility step between tier selection and runtime defaults in `moonmind/workflows/executions/model_resolver.py`, so a null tier `model` or `effort` resolves directly to the runtime default.
- Update the remaining callers, fixtures, and tests that assert the mirrored behavior, including `test_legacy_profile_and_runtime_defaults_remain_after_tiers` in `tests/unit/workflows/executions/test_model_resolver.py`.
- Confirm the backend migration that converts pre-tier rows into canonical `model_tiers` has run before the columns are dropped.

## Phase 3 — Profile-scoped capability descriptors

- Add the profile-scoped capability endpoint and the draft runtime/provider variant.
- Return catalog evidence identity (`credential_generation`, `image_ref`, `observed_at`, `stale`) with the option sets.
- Replace free-text primary controls with capability-backed selectors.
- Preserve custom and unknown values safely.
- Surface effort application status.

## Phase 4 — Collection summary and focused entry

- Add the dedicated `Model policy` summary.
- Add expandable full mappings.
- Add an `Edit tiers` focus action.
- Add read-only and mobile card treatment.

## Phase 5 — Structural diff and stale-write protection

- Add renumbering previews and save summaries.
- Add version or ETag conflict handling.
- Add backend preview integration where useful.

These phases are implementation sequencing only. The complete behavior in the canonical document remains the desired state.
