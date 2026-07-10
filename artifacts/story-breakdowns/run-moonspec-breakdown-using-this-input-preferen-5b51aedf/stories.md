# Story Breakdown: Provider Profile Model and Effort Tiers

- Source: `docs/Security/ProviderProfileModelEffortTiers.md`
- Requested source path: `docs/ManagedAgents/ProviderProfileModelTiers.md` (not readable in this checkout)
- Source document class: `canonical-declarative`
- Output mode: `jira`
- Coverage gate: PASS - every major design point is owned by at least one story.

## Design Summary

The source design makes Provider Profiles the owner of declarative model/effort tiers. Presets and workflow steps request portable numeric tiers, while backend launch or submission logic resolves those tiers against the selected profile, applies clamp or strict fallback, and records auditable diagnostics. The design keeps credentials and capacity leasing outside tier policy, treats frontend preview as advisory, and requires persistence, API, UI, runtime, migration, and acceptance-test coverage.

## Coverage Points

- `DESIGN-REQ-001` Profile-local tier definitions (requirement): Tier numbers are interpreted relative to the selected Provider Profile and every profile has at least one tier. Source: 2. Design Goals.
- `DESIGN-REQ-002` Portable preset tier intent (integration): Presets and workflow steps request semantic modelTier values rather than provider-specific model names. Source: 1. Summary; 7. Preset Contract.
- `DESIGN-REQ-003` Backend authoritative resolution (constraint): Frontend previews are advisory; backend resolves final model and effort at submit or launch time. Source: 2. Design Goals; 8. Responsibilities.
- `DESIGN-REQ-004` Provider Profile tier contract (state-model): Profiles add model_tiers and default_model_tier, and tiers carry label, model, effort, parameters, and annotations. Source: 5. Provider Profile Contract.
- `DESIGN-REQ-005` Tier validation and secret exclusion (security): Profile writes and launch checks enforce valid arrays, default bounds, noncredential parameters, and noncredential annotations. Source: 5.2 Field semantics; 15. Validation Rules.
- `DESIGN-REQ-006` Resolution precedence and fallback (state-model): Explicit overrides, requested tiers, defaults, legacy fields, runtime defaults, clamp fallback, and strict rejection follow defined semantics. Source: 9. Resolution Semantics.
- `DESIGN-REQ-007` Resolved output contract (artifact): Resolution returns model, effort, requested/effective tier, label, sources, fallback reason, and effort application status. Source: 9.5 Output.
- `DESIGN-REQ-008` Runtime strategy application (integration): Runtime strategies consume resolved model/effort through their supported launch shaping and report unsupported effort without pretending it applied. Source: 10. Runtime Strategy Integration.
- `DESIGN-REQ-009` Tier parameters precedence (constraint): Tier parameters may be merged into launch parameters, but explicit step parameters override tier parameters. Source: 10.3 Tier parameters.
- `DESIGN-REQ-010` Provider Profile Manager boundaries (constraint): The manager still leases profile-level slots and cooldowns; tiers are not capacity pools or profile selectors. Source: 11. Provider Profile Manager Interaction.
- `DESIGN-REQ-011` Persistence and migration (migration): Storage adds model_tiers/default_model_tier and migrates legacy default_model/default_effort into one tier while preserving auditability. Source: 12. Persistence Model.
- `DESIGN-REQ-012` API read/write and preview (integration): Provider Profile endpoints accept and return tier policy, and an optional preview endpoint resolves step tier previews. Source: 13. API Contract.
- `DESIGN-REQ-013` Frontend tier UI and submission (requirement): Settings and workflow UI display tiers, warn on fallback, allow explicit overrides, and submit requested tier intent. Source: 8.2 Frontend responsibilities.
- `DESIGN-REQ-014` Run diagnostics and historical audit (observability): Launch metadata records tier resolution and historical workflow details show concrete resolved model/effort even after profile edits. Source: 14. Observability and Audit.
- `DESIGN-REQ-015` Non-goal guardrails (non-goal): The design avoids universal quality scales, tier capacity pools, frontend authority, and removal of expert hard overrides. Source: 3. Non-Goals.
- `DESIGN-REQ-016` Acceptance coverage expectations (constraint): Implementation must cover validation, migration, resolution, frontend/backend contract, and runtime launch behaviors. Source: 17. Acceptance Tests.

## Canonical Claims

- `CLAIM-docs-security-providerprofilemodelefforttiers-001` `docs/Security/ProviderProfileModelEffortTiers.md` 1. Summary; 2. Design Goals: Presets express portable model intent with profile-local model tiers while backend resolution remains authoritative.
- `CLAIM-docs-security-providerprofilemodelefforttiers-002` `docs/Security/ProviderProfileModelEffortTiers.md` 3. Non-Goals; 11. Provider Profile Manager Interaction: Tiers are not global quality scales, credential policy, independent capacity pools, or replacements for Provider Profile slot leasing.
- `CLAIM-docs-security-providerprofilemodelefforttiers-003` `docs/Security/ProviderProfileModelEffortTiers.md` 4. Key Concepts; 5. Provider Profile Contract: Provider Profiles own ordered model_tiers and default_model_tier fields with typed tier entries.
- `CLAIM-docs-security-providerprofilemodelefforttiers-004` `docs/Security/ProviderProfileModelEffortTiers.md` 5.2 Field semantics; 15. Validation Rules: Tier lists, default tier values, labels, model, effort, parameters, and annotations have explicit validation and semantics.
- `CLAIM-docs-security-providerprofilemodelefforttiers-005` `docs/Security/ProviderProfileModelEffortTiers.md` 7. Preset Contract: Presets and workflow steps request modelTier and optional tierFallback intent instead of concrete model and effort values.
- `CLAIM-docs-security-providerprofilemodelefforttiers-006` `docs/Security/ProviderProfileModelEffortTiers.md` 8. Frontend and Backend Responsibilities; 13.3 Preview endpoint: The frontend may preview tier mappings and fallback, but must submit tier intent while backend resolves authoritatively.
- `CLAIM-docs-security-providerprofilemodelefforttiers-007` `docs/Security/ProviderProfileModelEffortTiers.md` 9. Resolution Semantics: Model and effort resolution follows a defined precedence order, clamps tiers by default, supports strict fallback, and returns auditable resolution metadata.
- `CLAIM-docs-security-providerprofilemodelefforttiers-008` `docs/Security/ProviderProfileModelEffortTiers.md` 10. Runtime Strategy Integration: Runtime strategies apply resolved model and effort through runtime-specific mechanisms and report unsupported or metadata-only effort honestly.
- `CLAIM-docs-security-providerprofilemodelefforttiers-009` `docs/Security/ProviderProfileModelEffortTiers.md` 12. Persistence Model: Provider profile persistence adds model_tiers and default_model_tier with migration from legacy default_model/default_effort fields.
- `CLAIM-docs-security-providerprofilemodelefforttiers-010` `docs/Security/ProviderProfileModelEffortTiers.md` 13. API Contract: Provider Profile APIs expose tier policy on create, update, response, and optional preview surfaces.
- `CLAIM-docs-security-providerprofilemodelefforttiers-011` `docs/Security/ProviderProfileModelEffortTiers.md` 14. Observability and Audit: Every launched step records requested tier, effective tier, resolved model, resolved effort, source, fallback reason, effort status, and preview mismatch.
- `CLAIM-docs-security-providerprofilemodelefforttiers-012` `docs/Security/ProviderProfileModelEffortTiers.md` 17. Acceptance Tests: Validation, migration, resolution, frontend/backend, and runtime launch behavior require acceptance coverage.
- `CLAIM-docs-security-providerprofilemodelefforttiers-013` `docs/Security/ProviderProfileModelEffortTiers.md` 18. Open Questions; 19. Decision Summary: Open policy choices remain explicit while core decisions establish Provider Profiles as the owner of tier definitions and backend resolution.

## Stories

### STORY-001: Add Provider Profile model tier contract and migration

- Short name: `profile-tier-contract`
- Source reference: `docs/Security/ProviderProfileModelEffortTiers.md`; sections: 4. Key Concepts, 5. Provider Profile Contract, 12. Persistence Model, 15.1 Profile validation
- Canonical claim IDs: `CLAIM-docs-security-providerprofilemodelefforttiers-003`, `CLAIM-docs-security-providerprofilemodelefforttiers-004`, `CLAIM-docs-security-providerprofilemodelefforttiers-009`
- Coverage IDs: `DESIGN-REQ-001`, `DESIGN-REQ-004`, `DESIGN-REQ-005`, `DESIGN-REQ-011`
- Dependencies: None

As an operator managing provider profiles, I need each profile to declare ordered model/effort tiers and a default tier so workflows can request portable tier intent against profile-owned policy.

Independent test: Create and update provider profiles through the backend model/service layer, run migration/backfill tests, and assert invalid tier shapes and credential-like tier metadata are rejected.

Acceptance criteria:
- Every Provider Profile persists model_tiers with at least one entry and default_model_tier within the configured range.
- Existing profiles with legacy default_model/default_effort receive one migrated tier preserving those values.
- Profiles without legacy defaults receive a Runtime default tier with null model and effort.
- Tier parameters and annotations reject raw credential-like keys.
- Reordering model_tiers is treated as changing the profile policy because tier numbers follow array order.

Requirements:
- Add typed ProviderModelEffortTier fields label, model, effort, parameters, and annotations.
- Keep model and effort strings opaque to the Provider Profile system.
- Preserve compatibility fields only as migration/read compatibility until callers are tier-aware.

Assumptions:
- The implementation may keep existing legacy default_model/default_effort fields as mirrors during the documented migration period.

### STORY-002: Resolve requested model tiers authoritatively in the backend

- Short name: `tier-resolver`
- Source reference: `docs/Security/ProviderProfileModelEffortTiers.md`; sections: 8. Frontend and Backend Responsibilities, 9. Resolution Semantics, 15.2 Preset and workflow validation, 15.3 Runtime validation
- Canonical claim IDs: `CLAIM-docs-security-providerprofilemodelefforttiers-001`, `CLAIM-docs-security-providerprofilemodelefforttiers-007`
- Coverage IDs: `DESIGN-REQ-003`, `DESIGN-REQ-006`, `DESIGN-REQ-007`, `DESIGN-REQ-015`
- Dependencies: `STORY-001`

As a workflow submitter, I need MoonMind to resolve requested model tiers against the selected Provider Profile on the backend so launch behavior is deterministic, auditable, and independent of stale frontend previews.

Independent test: Run resolver unit tests against profile fixtures with requested tiers, missing tiers, strict fallback, explicit overrides, null model/effort, and legacy defaults.

Acceptance criteria:
- No requested tier uses default_model_tier.
- Requested Tier 2 resolves to model_tiers[1].
- Requested Tier 3 with two configured tiers clamps to Tier 2 by default and records requested_tier_above_configured_range.
- tierFallback strict rejects unavailable tiers before launch.
- Explicit model or effort overrides bypass tier policy and record task_override source.
- The resolver returns model, effort, requested tier, effective tier, tier label, model source, effort source, fallback reason, and effort application status placeholder.

Requirements:
- Validate modelTier as an integer greater than or equal to 1.
- Preserve the documented resolution order from explicit overrides through runtime defaults.
- Fail explicitly for missing or launch-unready selected profiles.

### STORY-003: Preserve modelTier intent through preset and workflow submission

- Short name: `preset-tier-intent`
- Source reference: `docs/Security/ProviderProfileModelEffortTiers.md`; sections: 1. Summary, 7. Preset Contract, 15.2 Preset and workflow validation
- Canonical claim IDs: `CLAIM-docs-security-providerprofilemodelefforttiers-001`, `CLAIM-docs-security-providerprofilemodelefforttiers-005`
- Coverage IDs: `DESIGN-REQ-002`, `DESIGN-REQ-006`, `DESIGN-REQ-015`
- Dependencies: `STORY-002`

As a preset author, I need presets and workflow steps to carry modelTier and tierFallback intent so reusable workflows remain portable across Provider Profiles.

Independent test: Expand a preset containing modelTier and tierFallback, submit a workflow payload, and assert backend-bound step runtime metadata still contains tier intent rather than only concrete model/effort values.

Acceptance criteria:
- Preset runtime metadata accepts modelTier on steps.
- Preset runtime metadata accepts tierFallback values clamp and strict when present.
- Expanded workflows preserve modelTier until backend resolution.
- Frontend or API submissions do not replace tier intent with only concrete model/effort values.
- Hard model/effort overrides remain explicit and auditable.

Requirements:
- Support providerProfileRef and profileSelector based profile selection with modelTier intent.
- Reject invalid modelTier and tierFallback values at write or submit boundaries.

### STORY-004: Expose Provider Profile tier APIs and preview resolution

- Short name: `tier-api-preview`
- Source reference: `docs/Security/ProviderProfileModelEffortTiers.md`; sections: 8.2 Frontend responsibilities, 8.5 Optional advisory preview snapshot, 13. API Contract
- Canonical claim IDs: `CLAIM-docs-security-providerprofilemodelefforttiers-006`, `CLAIM-docs-security-providerprofilemodelefforttiers-010`
- Coverage IDs: `DESIGN-REQ-003`, `DESIGN-REQ-012`, `DESIGN-REQ-013`
- Dependencies: `STORY-001`, `STORY-002`

As a UI or API client, I need Provider Profile endpoints to read, write, and preview tier policy so users can understand how requested tiers will resolve before submitting a workflow.

Independent test: Call Provider Profile create/update/read APIs and the tier preview API with representative step inputs, then assert response tiers, fallback reasons, and backend resolver parity.

Acceptance criteria:
- Provider Profile create/update accepts model_tiers and default_model_tier.
- Provider Profile responses include tier policy plus compatibility fields during migration.
- The preview endpoint returns requested tier, effective tier, model, effort, and fallback reason for each requested step.
- Preview output is advisory and does not replace launch-time backend resolution.
- Backend can detect and record preview mismatch when submitted preview state differs from current profile policy.

Requirements:
- Use backend resolver logic for preview parity.
- Do not require presets to name concrete models for preview.

Assumptions:
- The preview endpoint is optional in the design, but this story treats it as the smallest complete client contract for Jira-ready implementation.

### STORY-005: Render tier policy and fallback preview in Settings and workflow UI

- Short name: `tier-ui-preview`
- Source reference: `docs/Security/ProviderProfileModelEffortTiers.md`; sections: 8.2 Frontend responsibilities, 8.4 Why the frontend is not authoritative, 14. Observability and Audit
- Canonical claim IDs: `CLAIM-docs-security-providerprofilemodelefforttiers-006`, `CLAIM-docs-security-providerprofilemodelefforttiers-011`
- Coverage IDs: `DESIGN-REQ-003`, `DESIGN-REQ-013`, `DESIGN-REQ-014`
- Dependencies: `STORY-004`

As an operator selecting models for workflows, I need Settings and submission UI to show Provider Profile tier mappings, fallback warnings, and explicit override controls while submitting tier intent to the backend.

Independent test: Run frontend/unit or integration tests that render profiles with tier mappings, preview a workflow with fallback, submit the form, and assert the payload includes modelTier rather than only resolved model/effort.

Acceptance criteria:
- Settings shows the current mapping from tier number to label, model, and effort.
- Workflow submission preview shows effective model/effort for each tiered step.
- Fallback warnings explain when a requested tier exceeds configured tiers.
- Explicit hard override controls remain available and visibly distinct from tier intent.
- Submitted payload preserves modelTier and tierFallback fields.

Requirements:
- The frontend must not be the authoritative tier compiler.
- UI copy must make fallback concise and understandable.

### STORY-006: Apply resolved tiers at runtime launch boundaries

- Short name: `runtime-tier-launch`
- Source reference: `docs/Security/ProviderProfileModelEffortTiers.md`; sections: 10. Runtime Strategy Integration, 11. Provider Profile Manager Interaction, 15.3 Runtime validation
- Canonical claim IDs: `CLAIM-docs-security-providerprofilemodelefforttiers-002`, `CLAIM-docs-security-providerprofilemodelefforttiers-008`
- Coverage IDs: `DESIGN-REQ-008`, `DESIGN-REQ-009`, `DESIGN-REQ-010`, `DESIGN-REQ-015`
- Dependencies: `STORY-002`

As a workflow runner, I need runtime strategies to launch with the backend-resolved model, effort, and tier parameters while reporting when effort cannot be applied.

Independent test: Launch representative runtime strategy tests with resolved tier outputs and assert command/config/env shaping, effort application status, parameter precedence, and unchanged Provider Profile slot leasing behavior.

Acceptance criteria:
- Codex CLI receives the resolved model through its model application path when supported.
- Unsupported effort is recorded as not_supported rather than applied.
- Tier parameters are merged only when not overridden by explicit step parameters.
- Tier definitions never carry credentials or credential refs into runtime launch materialization.
- Provider Profile Manager leases capacity for the selected profile, not for individual tiers.

Requirements:
- Runtime launch defensively re-checks selected profile readiness and tier resolvability.
- Effort application status values include applied, not_supported, metadata_only, emulated, or unknown as appropriate.

### STORY-007: Record tier resolution diagnostics and acceptance coverage

- Short name: `tier-audit-coverage`
- Source reference: `docs/Security/ProviderProfileModelEffortTiers.md`; sections: 14. Observability and Audit, 17. Acceptance Tests, 18. Open Questions, 19. Decision Summary
- Canonical claim IDs: `CLAIM-docs-security-providerprofilemodelefforttiers-011`, `CLAIM-docs-security-providerprofilemodelefforttiers-012`, `CLAIM-docs-security-providerprofilemodelefforttiers-013`
- Coverage IDs: `DESIGN-REQ-014`, `DESIGN-REQ-016`
- Dependencies: `STORY-002`, `STORY-004`, `STORY-006`

As an operator auditing historical workflow behavior, I need every launched step to record tier resolution metadata and tests to prove validation, migration, resolution, preview, and runtime launch behavior.

Independent test: Run end-to-end or boundary tests that launch tiered steps and inspect diagnostics for requested/effective tier, fallback, resolved model/effort, sources, effort status, preview mismatch, and historical stability after profile edits.

Acceptance criteria:
- Run diagnostics include providerProfileId, requestedModelTier, effectiveModelTier, tierLabel, fallbackReason, resolvedModel, resolvedEffort, modelSource, effortSource, effortApplicationStatus, and previewMismatch where applicable.
- Historical workflow details continue to show the concrete resolved model and effort after Provider Profile edits.
- Tests cover profile validation, migration, resolution, frontend/backend contract, and runtime launch acceptance cases from the source design.
- Open policy questions remain visible and are not silently resolved by implementation defaults outside the documented decisions.

Requirements:
- Diagnostics must not include raw credentials.
- Coverage must include degraded or unsupported effort paths.

Assumptions:
- Open questions in the source design can become follow-up product decisions after the core tier model is implemented.

Needs clarification:
- Should recurring workflow schedules use current profile policy at launch or support snapshot mode at schedule creation?
- Should strict fallback be exposed in normal UI or only API/power-user surfaces?

## Coverage Matrix

- `CLAIM-docs-security-providerprofilemodelefforttiers-001` -> `STORY-002`, `STORY-003`
- `CLAIM-docs-security-providerprofilemodelefforttiers-002` -> `STORY-006`
- `CLAIM-docs-security-providerprofilemodelefforttiers-003` -> `STORY-001`
- `CLAIM-docs-security-providerprofilemodelefforttiers-004` -> `STORY-001`
- `CLAIM-docs-security-providerprofilemodelefforttiers-005` -> `STORY-003`
- `CLAIM-docs-security-providerprofilemodelefforttiers-006` -> `STORY-004`, `STORY-005`
- `CLAIM-docs-security-providerprofilemodelefforttiers-007` -> `STORY-002`
- `CLAIM-docs-security-providerprofilemodelefforttiers-008` -> `STORY-006`
- `CLAIM-docs-security-providerprofilemodelefforttiers-009` -> `STORY-001`
- `CLAIM-docs-security-providerprofilemodelefforttiers-010` -> `STORY-004`
- `CLAIM-docs-security-providerprofilemodelefforttiers-011` -> `STORY-005`, `STORY-007`
- `CLAIM-docs-security-providerprofilemodelefforttiers-012` -> `STORY-007`
- `CLAIM-docs-security-providerprofilemodelefforttiers-013` -> `STORY-007`
- `DESIGN-REQ-001` -> `STORY-001`
- `DESIGN-REQ-002` -> `STORY-003`
- `DESIGN-REQ-003` -> `STORY-002`, `STORY-004`, `STORY-005`
- `DESIGN-REQ-004` -> `STORY-001`
- `DESIGN-REQ-005` -> `STORY-001`
- `DESIGN-REQ-006` -> `STORY-002`, `STORY-003`
- `DESIGN-REQ-007` -> `STORY-002`
- `DESIGN-REQ-008` -> `STORY-006`
- `DESIGN-REQ-009` -> `STORY-006`
- `DESIGN-REQ-010` -> `STORY-006`
- `DESIGN-REQ-011` -> `STORY-001`
- `DESIGN-REQ-012` -> `STORY-004`
- `DESIGN-REQ-013` -> `STORY-004`, `STORY-005`
- `DESIGN-REQ-014` -> `STORY-005`, `STORY-007`
- `DESIGN-REQ-015` -> `STORY-002`, `STORY-003`, `STORY-006`
- `DESIGN-REQ-016` -> `STORY-007`
